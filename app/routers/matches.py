import base64
import io
from datetime import timedelta

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse
from PIL import Image
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import and_, func, or_

from app.auth import get_current_user
from app.csrf import validate_csrf_header
from app.database import get_db
from app.utils.time import utcnow as _utcnow
from app.i18n import get_lang, get_translations, is_rtl
from app.models.models import Like, Match, Message, MessageReaction, QuizAnswer, User
from app.quiz_questions import CATEGORY_ORDER, QID_TO_CATEGORY
from app.templates import templates

router = APIRouter()

MAX_MESSAGE_LENGTH = 2000
CHAT_PAGE_SIZE = 100
MAX_VOICE_BYTES = 5 * 1024 * 1024  # 5 MB
POLL_PAGE_SIZE = 50   # MEDIUM-19: cap polling response
# HIGH-1: only allow real audio MIME types for voice messages
ALLOWED_AUDIO_MIMES = {"audio/webm", "audio/ogg", "audio/mp4", "audio/mpeg", "audio/wav"}


def _update_streak(match: "Match", db):
    """Update streak_days and last_streak_date on a match when a message is sent."""
    today = _utcnow().date()
    if match.last_streak_date:
        last = match.last_streak_date.date()
        if last == today:
            return  # Already counted today
        if last == today - timedelta(days=1):
            match.streak_days = (match.streak_days or 0) + 1
        else:
            match.streak_days = 1  # Streak broken
    else:
        match.streak_days = 1
    match.last_streak_date = _utcnow()


def get_user_matches(user_id: int, db: Session):
    return (
        db.query(Match)
        .filter(or_(Match.user1_id == user_id, Match.user2_id == user_id))
        .order_by(Match.created_at.desc())
        .all()
    )


def get_partner(match: Match, user_id: int) -> User:
    return match.user2 if match.user1_id == user_id else match.user1


def compute_compatibility_batch(user_id: int, partner_ids: list, db: Session) -> dict:
    """Return {partner_id: {"overall": N, "categories": {cat: N, ...}} | None}. 2 queries total."""
    if not partner_ids:
        return {}

    all_ids = [user_id] + list(partner_ids)
    all_answers = db.query(QuizAnswer).filter(QuizAnswer.user_id.in_(all_ids)).all()

    answers_by_user: dict = {}
    for qa in all_answers:
        answers_by_user.setdefault(qa.user_id, {})[qa.question_id] = qa.answer_index

    user_answers = answers_by_user.get(user_id, {})
    result = {}

    for partner_id in partner_ids:
        partner_answers = answers_by_user.get(partner_id, {})
        if not user_answers or not partner_answers:
            result[partner_id] = None
            continue
        common_qids = set(user_answers.keys()) & set(partner_answers.keys())
        if not common_qids:
            result[partner_id] = None
            continue

        cat_matched: dict = {}
        cat_total: dict = {}
        for qid in common_qids:
            cat = QID_TO_CATEGORY.get(qid, "lifestyle")
            cat_total[cat] = cat_total.get(cat, 0) + 1
            if user_answers[qid] == partner_answers[qid]:
                cat_matched[cat] = cat_matched.get(cat, 0) + 1

        categories = {
            cat: round(100 * cat_matched.get(cat, 0) / cat_total[cat])
            for cat in CATEGORY_ORDER
            if cat_total.get(cat, 0) > 0
        }

        total_matched = sum(cat_matched.values())
        overall = round(100 * total_matched / len(common_qids))

        result[partner_id] = {"overall": overall, "categories": categories}

    return result


@router.get("/api/notifications")
def notifications(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    new_matches = db.query(func.count(Match.id)).filter(
        or_(
            and_(Match.user1_id == user.id, Match.seen_by_user1 == False),
            and_(Match.user2_id == user.id, Match.seen_by_user2 == False),
        )
    ).scalar() or 0

    unread_messages = db.query(func.count(Message.id)).join(Match).filter(
        or_(Match.user1_id == user.id, Match.user2_id == user.id),
        Message.sender_id != user.id,
        Message.is_read == False,
    ).scalar() or 0

    return JSONResponse({"new_matches": new_matches, "unread_messages": unread_messages})


@router.get("/matches", response_class=HTMLResponse)
def matches_page(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    matches = get_user_matches(user.id, db)

    # Mark all as seen
    for m in matches:
        if m.user1_id == user.id and not m.seen_by_user1:
            m.seen_by_user1 = True
        elif m.user2_id == user.id and not m.seen_by_user2:
            m.seen_by_user2 = True
    db.commit()

    # Batch-load all partner User objects in 1 query to avoid N+1 lazy loads
    partner_id_by_match = {
        m.id: (m.user2_id if m.user1_id == user.id else m.user1_id)
        for m in matches
    }
    all_partner_ids = list(partner_id_by_match.values())
    partners_map = {
        u.id: u
        for u in db.query(User).options(joinedload(User.profile)).filter(User.id.in_(all_partner_ids)).all()
    } if all_partner_ids else {}

    compat_map = compute_compatibility_batch(user.id, all_partner_ids, db)

    partners = []
    for m in matches:
        pid = partner_id_by_match[m.id]
        partner = partners_map.get(pid)
        if partner:
            partners.append((m, partner, compat_map.get(pid)))

    # Users who LIKED me (is_like=True), not yet matched, and I haven't swiped on
    liker_ids = {
        like.liker_id
        for like in db.query(Like.liker_id).filter(
            Like.liked_id == user.id, Like.is_like == True
        ).all()
    }
    i_swiped_ids = {like.liked_id for like in db.query(Like.liked_id).filter(Like.liker_id == user.id).all()}
    matched_ids = set(all_partner_ids)
    pending_ids = liker_ids - matched_ids - i_swiped_ids
    liked_me_users = []
    if pending_ids:
        liked_me_users = [
            u for u in db.query(User).options(joinedload(User.profile)).filter(User.id.in_(pending_ids)).all()
            if u.profile
        ]

    lang = get_lang(request, user)
    partner_names = [
        partner.profile.name.lower()
        for _, partner, _ in partners
        if partner.profile
    ]
    return templates.TemplateResponse("matches.html", {
        "request": request,
        "user": user,
        "partners": partners,
        "liked_me_users": liked_me_users,
        "partner_names": partner_names,
        "t": get_translations(lang),
        "rtl": is_rtl(lang),
        "lang": lang,
    })


@router.get("/chat/{match_id}", response_class=HTMLResponse)
def chat_page(match_id: int, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    match = db.query(Match).filter(Match.id == match_id).first()
    if not match or (match.user1_id != user.id and match.user2_id != user.id):
        raise HTTPException(status_code=403, detail="Forbidden")

    db.query(Message).filter(
        Message.match_id == match_id,
        Message.sender_id != user.id,
        Message.is_read == False,
    ).update({"is_read": True})
    db.commit()

    # FIX H4: load only the most recent CHAT_PAGE_SIZE messages to prevent OOM.
    # The polling endpoint (/chat/{id}/messages?after_id=N) delivers new messages incrementally.
    messages_raw = (
        db.query(Message)
        .filter(Message.match_id == match_id)
        .order_by(Message.created_at.desc())
        .limit(CHAT_PAGE_SIZE)
        .all()
    )
    messages_raw = list(reversed(messages_raw))  # back to chronological order

    partner = get_partner(match, user.id)
    msg_ids = [m.id for m in messages_raw]
    all_reactions = db.query(MessageReaction).filter(MessageReaction.message_id.in_(msg_ids)).all() if msg_ids else []
    reactions_by_msg: dict = {}
    for r in all_reactions:
        reactions_by_msg.setdefault(r.message_id, {})[r.emoji] = \
            reactions_by_msg.get(r.message_id, {}).get(r.emoji, 0) + 1

    messages_data = [
        {
            "id": m.id,
            "content": m.content,
            "sender_id": m.sender_id,
            "created_at": m.created_at.isoformat(),
            "is_read": m.is_read,
            "is_voice": m.is_voice,  # C2: was missing — voice messages showed as raw base64
            "reactions": reactions_by_msg.get(m.id, {}),
        }
        for m in messages_raw
    ]
    lang = get_lang(request, user)
    is_user1 = match.user1_id == user.id
    i_revealed = match.user1_revealed if is_user1 else match.user2_revealed
    partner_revealed = match.user2_revealed if is_user1 else match.user1_revealed
    return templates.TemplateResponse("chat.html", {
        "request": request,
        "user": user,
        "match": match,
        "partner": partner,
        "messages": messages_data,
        "t": get_translations(lang),
        "rtl": is_rtl(lang),
        "lang": lang,
        "streak": match.streak_days or 0,
        "i_revealed": i_revealed,
        "partner_revealed": partner_revealed,
    })


@router.post("/chat/{match_id}/send", dependencies=[Depends(validate_csrf_header)])
def send_message(
    match_id: int,
    content: str = Form(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    content = content.strip()
    if not content:
        return JSONResponse({"error": "Empty message"}, status_code=400)
    if len(content) > MAX_MESSAGE_LENGTH:
        return JSONResponse({"error": f"Message too long (max {MAX_MESSAGE_LENGTH} chars)"}, status_code=400)

    match = db.query(Match).filter(Match.id == match_id).first()
    if not match or (match.user1_id != user.id and match.user2_id != user.id):
        return JSONResponse({"error": "Forbidden"}, status_code=403)

    msg = Message(match_id=match_id, sender_id=user.id, content=content)
    db.add(msg)
    _update_streak(match, db)
    db.commit()
    db.refresh(msg)

    return JSONResponse({
        "id": msg.id,
        "content": msg.content,
        "sender_id": msg.sender_id,
        "created_at": msg.created_at.isoformat(),
        "is_voice": False,
    })


@router.post("/chat/{match_id}/voice", dependencies=[Depends(validate_csrf_header)])
async def send_voice(
    match_id: int,
    audio: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    match = db.query(Match).filter(Match.id == match_id).first()
    if not match or (match.user1_id != user.id and match.user2_id != user.id):
        return JSONResponse({"error": "Forbidden"}, status_code=403)

    raw = await audio.read(MAX_VOICE_BYTES + 1)
    if len(raw) > MAX_VOICE_BYTES:
        return JSONResponse({"error": "Audio too large (max 5 MB)"}, status_code=400)

    # HIGH-1: whitelist MIME types — reject anything that could be rendered as HTML/SVG
    mime = audio.content_type or "audio/webm"
    if mime not in ALLOWED_AUDIO_MIMES:
        mime = "audio/webm"
    b64 = base64.b64encode(raw).decode()
    content = f"data:{mime};base64,{b64}"

    msg = Message(match_id=match_id, sender_id=user.id, content=content, is_voice=True)
    db.add(msg)
    _update_streak(match, db)
    db.commit()
    db.refresh(msg)

    return JSONResponse({
        "id": msg.id,
        "content": msg.content,
        "sender_id": msg.sender_id,
        "created_at": msg.created_at.strftime("%H:%M"),
        "is_voice": True,
    })


@router.post("/chat/{match_id}/reveal", dependencies=[Depends(validate_csrf_header)])
def reveal_anonymous(
    match_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    match = db.query(Match).filter(Match.id == match_id).first()
    if not match or (match.user1_id != user.id and match.user2_id != user.id):
        return JSONResponse({"error": "Forbidden"}, status_code=403)

    if match.user1_id == user.id:
        match.user1_revealed = True
    else:
        match.user2_revealed = True
    db.commit()

    both = match.user1_revealed and match.user2_revealed
    return JSONResponse({"success": True, "both_revealed": both})


@router.post("/match/{match_id}/unmatch", dependencies=[Depends(validate_csrf_header)])
def unmatch(
    match_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    match = db.query(Match).filter(Match.id == match_id).first()
    if not match or (match.user1_id != user.id and match.user2_id != user.id):
        return JSONResponse({"error": "Forbidden"}, status_code=403)
    db.query(Message).filter(Message.match_id == match_id).delete()
    db.delete(match)
    db.commit()
    return JSONResponse({"success": True})


ALLOWED_REACTIONS = {"❤️", "😂", "😮", "😢", "👍", "🔥"}


@router.post("/chat/{match_id}/message/{msg_id}/react", dependencies=[Depends(validate_csrf_header)])
async def react_to_message(
    match_id: int,
    msg_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    match = db.query(Match).filter(Match.id == match_id).first()
    if not match or (match.user1_id != user.id and match.user2_id != user.id):
        return JSONResponse({"error": "Forbidden"}, status_code=403)

    msg = db.query(Message).filter(Message.id == msg_id, Message.match_id == match_id).first()
    if not msg:
        return JSONResponse({"error": "Not found"}, status_code=404)

    data = await request.json()
    emoji = data.get("emoji", "")
    if emoji not in ALLOWED_REACTIONS:
        return JSONResponse({"error": "Invalid reaction"}, status_code=400)

    existing = db.query(MessageReaction).filter(
        MessageReaction.message_id == msg_id,
        MessageReaction.user_id == user.id,
    ).first()

    if existing:
        if existing.emoji == emoji:
            db.delete(existing)
        else:
            existing.emoji = emoji
    else:
        db.add(MessageReaction(message_id=msg_id, user_id=user.id, emoji=emoji))

    from sqlalchemy.exc import IntegrityError
    try:
        db.commit()
    except IntegrityError:
        db.rollback()

    reactions = db.query(MessageReaction).filter(MessageReaction.message_id == msg_id).all()
    summary: dict = {}
    for r in reactions:
        summary[r.emoji] = summary.get(r.emoji, 0) + 1

    return JSONResponse({"reactions": summary})


@router.get("/chat/{match_id}/messages")
def get_messages(
    match_id: int,
    after_id: int = 0,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    match = db.query(Match).filter(Match.id == match_id).first()
    if not match or (match.user1_id != user.id and match.user2_id != user.id):
        return JSONResponse({"error": "Forbidden"}, status_code=403)

    # MEDIUM-19: always limit polling results to prevent OOM on very long chats
    messages = (
        db.query(Message)
        .filter(Message.match_id == match_id, Message.id > after_id)
        .order_by(Message.created_at)
        .limit(POLL_PAGE_SIZE)
        .all()
    )

    db.query(Message).filter(
        Message.match_id == match_id,
        Message.sender_id != user.id,
        Message.is_read == False,
    ).update({"is_read": True})
    db.commit()

    msg_ids = [m.id for m in messages]
    reactions_by_msg: dict = {}
    if msg_ids:
        for r in db.query(MessageReaction).filter(MessageReaction.message_id.in_(msg_ids)).all():
            reactions_by_msg.setdefault(r.message_id, {})[r.emoji] = \
                reactions_by_msg.get(r.message_id, {}).get(r.emoji, 0) + 1

    return JSONResponse([{
        "id": m.id,
        "content": m.content,
        "sender_id": m.sender_id,
        "created_at": m.created_at.isoformat(),
        "is_read": m.is_read,
        "is_voice": m.is_voice,
        "reactions": reactions_by_msg.get(m.id, {}),
    } for m in messages])
