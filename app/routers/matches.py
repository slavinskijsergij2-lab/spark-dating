import asyncio
import base64
import io
import json as _json
from datetime import timedelta

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from PIL import Image
from sqlalchemy import and_, func, or_, select, update, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from app.auth import get_current_user
from app.csrf import validate_csrf_header
from app.database import get_db, AsyncSessionLocal
from app.utils.time import utcnow as _utcnow
from app.i18n import get_lang, get_translations, is_rtl
from app.models.models import Block, Like, Match, Message, MessageReaction, QuizAnswer, User
from app.quiz_questions import CATEGORY_ORDER, QID_TO_CATEGORY
from app.push import send_push_to_user
from app.rate_limit import rate_limit
from app.templates import templates

router = APIRouter()

MAX_MESSAGE_LENGTH = 2000
CHAT_PAGE_SIZE = 100
MAX_VOICE_BYTES = 5 * 1024 * 1024
POLL_PAGE_SIZE = 50
MATCHES_PAGE_SIZE = 20
LIKED_ME_PREVIEW = 12
ALLOWED_AUDIO_MIMES = {"audio/webm", "audio/ogg", "audio/mp4", "audio/mpeg", "audio/wav"}


def _update_streak(match: "Match", db):
    today = _utcnow().date()
    if match.last_streak_date:
        last = match.last_streak_date.date()
        if last == today:
            return
        if last == today - timedelta(days=1):
            match.streak_days = (match.streak_days or 0) + 1
        else:
            match.streak_days = 1
    else:
        match.streak_days = 1
    match.last_streak_date = _utcnow()


async def compute_compatibility_batch(user_id: int, partner_ids: list, db: AsyncSession) -> dict:
    if not partner_ids:
        return {}

    all_ids = [user_id] + list(partner_ids)
    result = await db.execute(select(QuizAnswer).where(QuizAnswer.user_id.in_(all_ids)))
    all_answers = result.scalars().all()

    answers_by_user: dict = {}
    for qa in all_answers:
        answers_by_user.setdefault(qa.user_id, {})[qa.question_id] = qa.answer_index

    user_answers = answers_by_user.get(user_id, {})
    out = {}

    for partner_id in partner_ids:
        partner_answers = answers_by_user.get(partner_id, {})
        if not user_answers or not partner_answers:
            out[partner_id] = None
            continue
        common_qids = set(user_answers.keys()) & set(partner_answers.keys())
        if not common_qids:
            out[partner_id] = None
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
        out[partner_id] = {"overall": overall, "categories": categories}

    return out


@router.get("/api/notifications", dependencies=[Depends(rate_limit(120, 60))])
async def notifications(user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(func.count(Match.id)).where(
            or_(
                and_(Match.user1_id == user.id, Match.seen_by_user1 == False),
                and_(Match.user2_id == user.id, Match.seen_by_user2 == False),
            )
        )
    )
    new_matches = result.scalar() or 0

    result = await db.execute(
        select(func.count(Message.id))
        .join(Match, Message.match_id == Match.id)
        .where(
            or_(Match.user1_id == user.id, Match.user2_id == user.id),
            Message.sender_id != user.id,
            Message.is_read == False,
        )
    )
    unread_messages = result.scalar() or 0

    return JSONResponse({"new_matches": new_matches, "unread_messages": unread_messages})


@router.get("/matches", response_class=HTMLResponse, dependencies=[Depends(rate_limit(30, 60))])
async def matches_page(
    request: Request,
    page: int = Query(1, ge=1),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    base_where = or_(Match.user1_id == user.id, Match.user2_id == user.id)

    result = await db.execute(select(func.count(Match.id)).where(base_where))
    total_matches = result.scalar() or 0
    total_pages = max(1, (total_matches + MATCHES_PAGE_SIZE - 1) // MATCHES_PAGE_SIZE)
    page = min(page, total_pages)

    result = await db.execute(
        select(Match)
        .where(base_where)
        .order_by(Match.created_at.desc())
        .offset((page - 1) * MATCHES_PAGE_SIZE)
        .limit(MATCHES_PAGE_SIZE)
    )
    matches = result.scalars().all()

    match_ids = [m.id for m in matches]
    if match_ids:
        from sqlalchemy import case as _case
        await db.execute(
            update(Match)
            .where(
                Match.id.in_(match_ids),
                or_(
                    and_(Match.user1_id == user.id, Match.seen_by_user1 == False),
                    and_(Match.user2_id == user.id, Match.seen_by_user2 == False),
                ),
            )
            .values(
                seen_by_user1=_case(
                    (Match.user1_id == user.id, True), else_=Match.seen_by_user1
                ),
                seen_by_user2=_case(
                    (Match.user2_id == user.id, True), else_=Match.seen_by_user2
                ),
            )
        )
        await db.commit()

    partner_id_by_match = {
        m.id: (m.user2_id if m.user1_id == user.id else m.user1_id)
        for m in matches
    }
    all_partner_ids = list(partner_id_by_match.values())
    if all_partner_ids:
        result = await db.execute(
            select(User).options(joinedload(User.profile)).where(User.id.in_(all_partner_ids))
        )
        partners_map = {u.id: u for u in result.scalars().unique().all()}
    else:
        partners_map = {}

    compat_map = await compute_compatibility_batch(user.id, all_partner_ids, db)

    # Last message per match for preview
    last_message_by_match: dict = {}
    if match_ids:
        last_id_subq = (
            select(func.max(Message.id).label("max_id"))
            .where(Message.match_id.in_(match_ids))
            .group_by(Message.match_id)
            .subquery()
        )
        result = await db.execute(
            select(Message).where(Message.id.in_(select(last_id_subq.c.max_id)))
        )
        for msg in result.scalars().all():
            last_message_by_match[msg.match_id] = msg

    partners = []
    for m in matches:
        pid = partner_id_by_match[m.id]
        partner = partners_map.get(pid)
        if partner:
            partners.append((m, partner, compat_map.get(pid)))

    # Users who liked me — pending (not yet matched, not yet swiped by me)
    # All filtering done in SQL to avoid loading unbounded sets into Python
    matched_subq = select(Match.user1_id, Match.user2_id).where(base_where).subquery()
    already_matched_ids = (
        select(
            func.coalesce(
                matched_subq.c.user2_id,
                matched_subq.c.user1_id,
            )
        ).where(
            or_(
                matched_subq.c.user1_id == user.id,
                matched_subq.c.user2_id == user.id,
            )
        )
    )
    i_swiped_subq = select(Like.liked_id).where(Like.liker_id == user.id).scalar_subquery()

    pending_q = (
        select(Like.liker_id)
        .where(
            Like.liked_id == user.id,
            Like.is_like == True,
            Like.liker_id.not_in(i_swiped_subq),
        )
    )

    result = await db.execute(select(func.count()).select_from(pending_q.subquery()))
    liked_me_total = result.scalar() or 0

    liked_me_users = []
    if liked_me_total > 0:
        result = await db.execute(pending_q.limit(LIKED_ME_PREVIEW))
        preview_ids = [row[0] for row in result.all()]
        if preview_ids:
            result = await db.execute(
                select(User).options(joinedload(User.profile)).where(User.id.in_(preview_ids))
            )
            liked_me_users = [u for u in result.scalars().unique().all() if u.profile]

    lang = get_lang(request, user)
    partner_names = [
        partner.profile.name.lower()
        for _, partner, _ in partners
        if partner.profile
    ]
    return templates.TemplateResponse(request, "matches.html", {
        "user": user,
        "partners": partners,
        "liked_me_users": liked_me_users,
        "liked_me_total": liked_me_total,
        "partner_names": partner_names,
        "last_message_by_match": last_message_by_match,
        "page": page,
        "total_pages": total_pages,
        "total_matches": total_matches,
        "t": get_translations(lang),
        "rtl": is_rtl(lang),
        "lang": lang,
    })


@router.get("/chat/{match_id}", response_class=HTMLResponse, dependencies=[Depends(rate_limit(30, 60))])
async def chat_page(match_id: int, request: Request, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Match)
        .options(selectinload(Match.user1).selectinload(User.profile),
                 selectinload(Match.user2).selectinload(User.profile))
        .where(Match.id == match_id)
    )
    match = result.scalar_one_or_none()
    if not match or (match.user1_id != user.id and match.user2_id != user.id):
        raise HTTPException(status_code=403, detail="Forbidden")

    await db.execute(
        update(Message)
        .where(
            Message.match_id == match_id,
            Message.sender_id != user.id,
            Message.is_read == False,
        )
        .values(is_read=True)
    )
    await db.commit()

    result = await db.execute(
        select(Message)
        .where(Message.match_id == match_id)
        .order_by(Message.created_at.desc())
        .limit(CHAT_PAGE_SIZE)
    )
    messages_raw = list(reversed(result.scalars().all()))

    partner = match.user2 if match.user1_id == user.id else match.user1
    partner_id = partner.id

    # Check if I blocked them (so chat menu can show Unblock instead of Block)
    block_result = await db.execute(
        select(Block).where(Block.blocker_id == user.id, Block.blocked_id == partner_id)
    )
    i_blocked_them = block_result.scalar_one_or_none() is not None

    msg_ids = [m.id for m in messages_raw]
    reactions_by_msg: dict = {}
    if msg_ids:
        result = await db.execute(
            select(MessageReaction).where(MessageReaction.message_id.in_(msg_ids))
        )
        for r in result.scalars().all():
            reactions_by_msg.setdefault(r.message_id, {})[r.emoji] = \
                reactions_by_msg.get(r.message_id, {}).get(r.emoji, 0) + 1

    messages_data = [
        {
            "id": m.id,
            "content": m.content,
            "sender_id": m.sender_id,
            "created_at": m.created_at.isoformat(),
            "is_read": m.is_read,
            "is_voice": m.is_voice,
            "reactions": reactions_by_msg.get(m.id, {}),
        }
        for m in messages_raw
    ]
    lang = get_lang(request, user)
    is_user1 = match.user1_id == user.id
    i_revealed = match.user1_revealed if is_user1 else match.user2_revealed
    partner_revealed = match.user2_revealed if is_user1 else match.user1_revealed
    return templates.TemplateResponse(request, "chat.html", {
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
        "i_blocked_them": i_blocked_them,
    })


@router.post("/chat/{match_id}/send", dependencies=[Depends(validate_csrf_header), Depends(rate_limit(30, 60))])
async def send_message(
    match_id: int,
    background_tasks: BackgroundTasks,
    content: str = Form(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    content = content.strip()
    if not content:
        return JSONResponse({"error": "Empty message"}, status_code=400)
    if len(content) > MAX_MESSAGE_LENGTH:
        return JSONResponse({"error": f"Message too long (max {MAX_MESSAGE_LENGTH} chars)"}, status_code=400)

    result = await db.execute(select(Match).where(Match.id == match_id))
    match = result.scalar_one_or_none()
    if not match or (match.user1_id != user.id and match.user2_id != user.id):
        return JSONResponse({"error": "Forbidden"}, status_code=403)

    partner_id = match.user2_id if match.user1_id == user.id else match.user1_id
    block = await db.execute(
        select(Block).where(
            or_(
                and_(Block.blocker_id == user.id, Block.blocked_id == partner_id),
                and_(Block.blocker_id == partner_id, Block.blocked_id == user.id),
            )
        )
    )
    if block.scalar_one_or_none():
        return JSONResponse({"error": "Forbidden"}, status_code=403)

    msg = Message(match_id=match_id, sender_id=user.id, content=content)
    db.add(msg)
    _update_streak(match, db)
    await db.commit()
    await db.refresh(msg)

    sender_name = user.profile.name if hasattr(user, "profile") and user.profile else "Spark"
    preview = content[:60] + ("…" if len(content) > 60 else "")
    background_tasks.add_task(
        send_push_to_user, partner_id,
        f"💬 {sender_name}", preview, f"/chat/{match_id}"
    )

    return JSONResponse({
        "id": msg.id,
        "content": msg.content,
        "sender_id": msg.sender_id,
        "created_at": msg.created_at.isoformat(),
        "is_voice": False,
    })


@router.post("/chat/{match_id}/typing", dependencies=[Depends(validate_csrf_header), Depends(rate_limit(60, 60))])
async def typing_indicator(
    match_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Match).where(Match.id == match_id))
    match = result.scalar_one_or_none()
    if not match or (match.user1_id != user.id and match.user2_id != user.id):
        return JSONResponse({"error": "Forbidden"}, status_code=403)
    try:
        from app.rate_limit import _get_redis
        redis = await _get_redis()
        if redis:
            await redis.set(f"typing:{match_id}:{user.id}", "1", ex=5)
    except Exception:
        pass
    return JSONResponse({"ok": True})


@router.post("/chat/{match_id}/voice", dependencies=[Depends(validate_csrf_header), Depends(rate_limit(5, 60))])
async def send_voice(
    match_id: int,
    audio: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Match).where(Match.id == match_id))
    match = result.scalar_one_or_none()
    if not match or (match.user1_id != user.id and match.user2_id != user.id):
        return JSONResponse({"error": "Forbidden"}, status_code=403)

    partner_id = match.user2_id if match.user1_id == user.id else match.user1_id
    block = await db.execute(
        select(Block).where(
            or_(
                and_(Block.blocker_id == user.id, Block.blocked_id == partner_id),
                and_(Block.blocker_id == partner_id, Block.blocked_id == user.id),
            )
        )
    )
    if block.scalar_one_or_none():
        return JSONResponse({"error": "Forbidden"}, status_code=403)

    raw = await audio.read(MAX_VOICE_BYTES + 1)
    if len(raw) > MAX_VOICE_BYTES:
        return JSONResponse({"error": "Audio too large (max 5 MB)"}, status_code=400)

    mime = audio.content_type or "audio/webm"
    if mime not in ALLOWED_AUDIO_MIMES:
        mime = "audio/webm"
    b64 = base64.b64encode(raw).decode()
    content = f"data:{mime};base64,{b64}"

    msg = Message(match_id=match_id, sender_id=user.id, content=content, is_voice=True)
    db.add(msg)
    _update_streak(match, db)
    await db.commit()
    await db.refresh(msg)

    return JSONResponse({
        "id": msg.id,
        "content": msg.content,
        "sender_id": msg.sender_id,
        "created_at": msg.created_at.isoformat(),
        "is_voice": True,
    })


@router.post("/chat/{match_id}/reveal", dependencies=[Depends(validate_csrf_header), Depends(rate_limit(5, 60))])
async def reveal_anonymous(
    match_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Match).where(Match.id == match_id))
    match = result.scalar_one_or_none()
    if not match or (match.user1_id != user.id and match.user2_id != user.id):
        return JSONResponse({"error": "Forbidden"}, status_code=403)

    if match.user1_id == user.id:
        match.user1_revealed = True
    else:
        match.user2_revealed = True
    await db.commit()

    both = match.user1_revealed and match.user2_revealed
    return JSONResponse({"success": True, "both_revealed": both})


@router.post("/match/{match_id}/unmatch", dependencies=[Depends(validate_csrf_header), Depends(rate_limit(10, 60))])
async def unmatch(
    match_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Match).where(Match.id == match_id))
    match = result.scalar_one_or_none()
    if not match or (match.user1_id != user.id and match.user2_id != user.id):
        return JSONResponse({"error": "Forbidden"}, status_code=403)
    await db.execute(delete(Message).where(Message.match_id == match_id))
    await db.delete(match)
    await db.commit()
    return JSONResponse({"success": True})


ALLOWED_REACTIONS = {"❤️", "😂", "😮", "😢", "👍", "🔥"}


@router.post("/chat/{match_id}/message/{msg_id}/react", dependencies=[Depends(validate_csrf_header), Depends(rate_limit(30, 60))])
async def react_to_message(
    match_id: int,
    msg_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Match).where(Match.id == match_id))
    match = result.scalar_one_or_none()
    if not match or (match.user1_id != user.id and match.user2_id != user.id):
        return JSONResponse({"error": "Forbidden"}, status_code=403)

    result = await db.execute(
        select(Message).where(Message.id == msg_id, Message.match_id == match_id)
    )
    msg = result.scalar_one_or_none()
    if not msg:
        return JSONResponse({"error": "Not found"}, status_code=404)

    try:
        data = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)
    emoji = data.get("emoji", "")
    if emoji not in ALLOWED_REACTIONS:
        return JSONResponse({"error": "Invalid reaction"}, status_code=400)

    result = await db.execute(
        select(MessageReaction).where(
            MessageReaction.message_id == msg_id,
            MessageReaction.user_id == user.id,
        )
    )
    existing = result.scalar_one_or_none()

    if existing:
        if existing.emoji == emoji:
            await db.delete(existing)
        else:
            existing.emoji = emoji
    else:
        db.add(MessageReaction(message_id=msg_id, user_id=user.id, emoji=emoji))

    from sqlalchemy.exc import IntegrityError
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()

    result = await db.execute(
        select(MessageReaction).where(MessageReaction.message_id == msg_id)
    )
    summary: dict = {}
    for r in result.scalars().all():
        summary[r.emoji] = summary.get(r.emoji, 0) + 1

    return JSONResponse({"reactions": summary})


@router.get("/chat/{match_id}/messages", dependencies=[Depends(rate_limit(120, 60))])
async def get_messages(
    match_id: int,
    after_id: int = 0,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Match).where(Match.id == match_id))
    match = result.scalar_one_or_none()
    if not match or (match.user1_id != user.id and match.user2_id != user.id):
        return JSONResponse({"error": "Forbidden"}, status_code=403)

    result = await db.execute(
        select(Message)
        .where(Message.match_id == match_id, Message.id > after_id)
        .order_by(Message.created_at)
        .limit(POLL_PAGE_SIZE)
    )
    messages = result.scalars().all()

    await db.execute(
        update(Message)
        .where(
            Message.match_id == match_id,
            Message.sender_id != user.id,
            Message.is_read == False,
        )
        .values(is_read=True)
    )
    await db.commit()

    msg_ids = [m.id for m in messages]
    reactions_by_msg: dict = {}
    if msg_ids:
        result = await db.execute(
            select(MessageReaction).where(MessageReaction.message_id.in_(msg_ids))
        )
        for r in result.scalars().all():
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


async def _fetch_new_messages(match_id: int, last_id: int, user_id: int) -> tuple:
    """Returns (msgs_list, new_last_id, partner_read_up_to).
    msgs_list is [] when no new messages; partner_read_up_to is always current."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Message)
            .where(Message.match_id == match_id, Message.id > last_id)
            .order_by(Message.created_at)
            .limit(POLL_PAGE_SIZE)
        )
        msgs = result.scalars().all()
        new_last_id = msgs[-1].id if msgs else last_id

        if msgs:
            await session.execute(
                update(Message)
                .where(
                    Message.match_id == match_id,
                    Message.sender_id != user_id,
                    Message.is_read == False,
                )
                .values(is_read=True)
            )
            await session.commit()

        msg_ids = [m.id for m in msgs]
        reactions_by_msg: dict = {}
        if msg_ids:
            rr = await session.execute(
                select(MessageReaction).where(MessageReaction.message_id.in_(msg_ids))
            )
            for r in rr.scalars().all():
                reactions_by_msg.setdefault(r.message_id, {})[r.emoji] = \
                    reactions_by_msg.get(r.message_id, {}).get(r.emoji, 0) + 1

        # Max ID of current user's own messages that partner has already read
        rr2 = await session.execute(
            select(func.max(Message.id)).where(
                Message.match_id == match_id,
                Message.sender_id == user_id,
                Message.is_read == True,
            )
        )
        partner_read_up_to = rr2.scalar() or 0

        msgs_data = [{
            "id": m.id,
            "content": m.content,
            "sender_id": m.sender_id,
            "created_at": m.created_at.isoformat(),
            "is_read": m.is_read,
            "is_voice": m.is_voice,
            "reactions": reactions_by_msg.get(m.id, {}),
        } for m in msgs]

        return msgs_data, new_last_id, partner_read_up_to


async def _check_partner_typing(match_id: int, partner_id: int) -> bool:
    """Returns True if partner sent a typing ping within the last 5 seconds."""
    try:
        from app.rate_limit import _get_redis
        redis = await _get_redis()
        if redis:
            return bool(await redis.exists(f"typing:{match_id}:{partner_id}"))
    except Exception:
        pass
    return False


_SSE_MAX_SECONDS = 300  # 5 min; EventSource auto-reconnects after this

@router.get("/chat/{match_id}/stream", dependencies=[Depends(rate_limit(20, 60))])
async def chat_stream(
    match_id: int,
    request: Request,
    after_id: int = 0,
    user: User = Depends(get_current_user),
):
    """SSE endpoint — auth check uses a short-lived session, then released so the
    connection pool is not held for the full stream lifetime."""
    async with AsyncSessionLocal() as _auth_db:
        result = await _auth_db.execute(select(Match).where(Match.id == match_id))
        match = result.scalar_one_or_none()
        if not match or (match.user1_id != user.id and match.user2_id != user.id):
            raise HTTPException(403, "Forbidden")

    user_id = user.id
    match_obj = match  # captured from auth check above
    partner_id = match_obj.user2_id if match_obj.user1_id == user_id else match_obj.user1_id

    async def generator():
        import logging as _log
        last_id = after_id
        deadline = _utcnow().timestamp() + _SSE_MAX_SECONDS
        last_read_up_to = -1   # -1 = not yet sent to client
        last_typing = None     # None = not yet sent

        while True:
            if _utcnow().timestamp() > deadline:
                yield "event: reconnect\ndata: {}\n\n"
                break

            try:
                if await request.is_disconnected():
                    break
            except Exception:
                break

            try:
                msgs_data, last_id, partner_read_up_to = await _fetch_new_messages(
                    match_id, last_id, user_id
                )
                is_typing = await _check_partner_typing(match_id, partner_id)
            except Exception as exc:
                _log.error("SSE stream error match=%s: %s", match_id, exc)
                break

            has_msgs    = bool(msgs_data)
            read_change = partner_read_up_to != last_read_up_to
            type_change = is_typing != last_typing

            if has_msgs or read_change or type_change:
                last_read_up_to = partner_read_up_to
                last_typing = is_typing
                payload = _json.dumps({
                    "messages": msgs_data,
                    "partner_read_up_to": partner_read_up_to,
                    "typing": is_typing,
                })
                yield f"data: {payload}\n\n"
            else:
                yield ": heartbeat\n\n"

            await asyncio.sleep(2)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
