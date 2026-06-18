from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import or_

from app.auth import get_current_user
from app.database import get_db
from app.i18n import get_lang, get_translations, is_rtl
from app.models.models import Match, Message, QuizAnswer, User

router = APIRouter()
templates = Jinja2Templates(directory="templates")


def get_user_matches(user_id: int, db: Session):
    return (
        db.query(Match)
        .filter(or_(Match.user1_id == user_id, Match.user2_id == user_id))
        .order_by(Match.created_at.desc())
        .all()
    )


def get_partner(match: Match, user_id: int) -> User:
    return match.user2 if match.user1_id == user_id else match.user1


def compute_compatibility(user_id: int, partner_id: int, db: Session) -> int | None:
    """Return % compatibility based on shared quiz answers, or None if 0 questions answered by either."""
    user_answers = {
        qa.question_id: qa.answer_index
        for qa in db.query(QuizAnswer).filter(QuizAnswer.user_id == user_id).all()
    }
    partner_answers = {
        qa.question_id: qa.answer_index
        for qa in db.query(QuizAnswer).filter(QuizAnswer.user_id == partner_id).all()
    }

    if not user_answers or not partner_answers:
        return None

    # Compare only questions both have answered
    common_qids = set(user_answers.keys()) & set(partner_answers.keys())
    if not common_qids:
        return None

    matches = sum(1 for qid in common_qids if user_answers[qid] == partner_answers[qid])
    return round(100 * matches / len(common_qids))


@router.get("/matches", response_class=HTMLResponse)
def matches_page(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    matches = get_user_matches(user.id, db)
    partners = []
    for m in matches:
        partner = get_partner(m, user.id)
        compat = compute_compatibility(user.id, partner.id, db)
        partners.append((m, partner, compat))
    lang = get_lang(request, user)
    return templates.TemplateResponse("matches.html", {
        "request": request,
        "user": user,
        "partners": partners,
        "t": get_translations(lang),
        "rtl": is_rtl(lang),
    })


@router.get("/chat/{match_id}", response_class=HTMLResponse)
def chat_page(match_id: int, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    match = db.query(Match).filter(Match.id == match_id).first()
    if not match or (match.user1_id != user.id and match.user2_id != user.id):
        return HTMLResponse("Доступ запрещен", status_code=403)

    db.query(Message).filter(
        Message.match_id == match_id,
        Message.sender_id != user.id,
        Message.is_read == False,
    ).update({"is_read": True})
    db.commit()

    messages = (
        db.query(Message)
        .filter(Message.match_id == match_id)
        .order_by(Message.created_at)
        .all()
    )
    partner = get_partner(match, user.id)
    messages_data = [
        {
            "id": m.id,
            "content": m.content,
            "sender_id": m.sender_id,
            "created_at": m.created_at.isoformat(),
            "is_read": m.is_read,
        }
        for m in messages
    ]
    lang = get_lang(request, user)
    return templates.TemplateResponse("chat.html", {
        "request": request,
        "user": user,
        "match": match,
        "partner": partner,
        "messages": messages_data,
        "t": get_translations(lang),
        "rtl": is_rtl(lang),
    })


@router.post("/chat/{match_id}/send")
def send_message(
    match_id: int,
    content: str = Form(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    content = content.strip()
    if not content:
        return JSONResponse({"error": "Empty message"}, status_code=400)

    match = db.query(Match).filter(Match.id == match_id).first()
    if not match or (match.user1_id != user.id and match.user2_id != user.id):
        return JSONResponse({"error": "Forbidden"}, status_code=403)

    msg = Message(match_id=match_id, sender_id=user.id, content=content)
    db.add(msg)
    db.commit()
    db.refresh(msg)

    return JSONResponse({
        "id": msg.id,
        "content": msg.content,
        "sender_id": msg.sender_id,
        "created_at": msg.created_at.strftime("%H:%M"),
    })


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

    messages = (
        db.query(Message)
        .filter(Message.match_id == match_id, Message.id > after_id)
        .order_by(Message.created_at)
        .all()
    )

    db.query(Message).filter(
        Message.match_id == match_id,
        Message.sender_id != user.id,
        Message.is_read == False,
    ).update({"is_read": True})
    db.commit()

    return JSONResponse([{
        "id": m.id,
        "content": m.content,
        "sender_id": m.sender_id,
        "time": m.created_at.strftime("%H:%M"),
        "is_read": m.is_read,
    } for m in messages])
