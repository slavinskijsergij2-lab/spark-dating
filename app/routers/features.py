import io
import os
import random

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import func as _func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import get_current_user
from app.csrf import validate_csrf_form, validate_csrf_header
from app.database import get_db
from app.i18n import get_lang, get_translations, is_rtl
from app.models.models import Match, PolitenessVote, Profile, QuizAnswer, User
from app.quiz_questions import QUIZ_QUESTIONS, TOTAL_QUESTIONS
from app.rate_limit import rate_limit
from app.templates import templates

router = APIRouter()

VERIFY_GESTURES = [
    "✌️ peace sign",
    "👋 wave",
    "👍 thumbs up",
    "🤙 hang loose",
    "🖐️ open palm",
]

_anthropic_client = None
_ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
if _ANTHROPIC_API_KEY:
    try:
        import anthropic as _anthropic_module
        _anthropic_client = _anthropic_module.Anthropic(api_key=_ANTHROPIC_API_KEY)
    except Exception:
        _anthropic_client = None


def _sanitize_prompt_field(text: str, max_len: int = 200) -> str:
    if not text:
        return ""
    return text.replace("\n", " ").replace("\r", " ").replace("```", "").strip()[:max_len]


@router.get("/chat/{match_id}/icebreakers", dependencies=[Depends(rate_limit(5, 60))])
async def get_icebreakers(
    match_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Match)
        .options(
            selectinload(Match.user1).selectinload(User.profile),
            selectinload(Match.user2).selectinload(User.profile),
        )
        .where(Match.id == match_id)
    )
    match = result.scalar_one_or_none()
    if not match or (match.user1_id != user.id and match.user2_id != user.id):
        return JSONResponse({"error": "Forbidden"}, status_code=403)

    partner = match.user2 if match.user1_id == user.id else match.user1
    profile = partner.profile

    def _fallback():
        name = profile.name if profile else "them"
        city = (profile.city if profile else None) or "their city"
        return [
            f"Hey {name}! What's your favourite thing to do in {city}?",
            f"Hi {name}, your profile caught my eye — what are you passionate about?",
            f"Hello {name}! If you could travel anywhere tomorrow, where would you go?",
        ]

    if not _anthropic_client:
        return JSONResponse({"suggestions": _fallback()})

    try:
        import asyncio
        import json
        name = _sanitize_prompt_field(profile.name if profile else "Unknown", 50)
        age  = str(profile.age) if profile else "unknown"
        city = _sanitize_prompt_field((profile.city or "") if profile else "", 100)
        bio  = _sanitize_prompt_field((profile.bio or "no bio") if profile else "no bio", 300)

        prompt = (
            "You are a dating app assistant. Based on this profile, suggest 3 short, friendly opening messages "
            "(max 20 words each). Be creative, warm, reference something specific from their profile. "
            "Return ONLY a JSON array of 3 strings, no other text.\n"
            f"Profile: Name: {name}, Age: {age}, City: {city}, About: {bio}"
        )

        def _call_api():
            return _anthropic_client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=300,
                messages=[{"role": "user", "content": prompt}],
            )

        message = await asyncio.to_thread(_call_api)
        text = message.content[0].text.strip()
        if text.startswith("```"):
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        suggestions = json.loads(text)
        if not isinstance(suggestions, list):
            raise ValueError("Not a list")
        suggestions = [str(s) for s in suggestions[:3]]
    except Exception:
        suggestions = _fallback()

    return JSONResponse({"suggestions": suggestions})


@router.post("/chat/{match_id}/rate", dependencies=[Depends(validate_csrf_header), Depends(rate_limit(5, 60))])
async def rate_politeness(
    match_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    lang = get_lang(request, user)
    t = get_translations(lang)

    data = await request.json()
    stars = data.get("stars")
    if not isinstance(stars, int) or stars < 1 or stars > 5:
        return JSONResponse({"error": "Invalid stars value"}, status_code=400)

    result = await db.execute(
        select(Match)
        .options(selectinload(Match.user1), selectinload(Match.user2))
        .where(Match.id == match_id)
    )
    match = result.scalar_one_or_none()
    if not match or (match.user1_id != user.id and match.user2_id != user.id):
        return JSONResponse({"error": "Forbidden"}, status_code=403)

    partner = match.user2 if match.user1_id == user.id else match.user1
    if partner.id == user.id:
        return JSONResponse({"error": "Cannot rate yourself"}, status_code=400)

    result = await db.execute(
        select(PolitenessVote).where(
            PolitenessVote.voter_id == user.id,
            PolitenessVote.target_id == partner.id,
        )
    )
    if result.scalar_one_or_none():
        return JSONResponse({"error": t.get("already_rated", "Already rated")}, status_code=409)

    vote = PolitenessVote(voter_id=user.id, target_id=partner.id, stars=stars)
    db.add(vote)

    result = await db.execute(
        select(User).where(User.id == partner.id).with_for_update()
    )
    locked_partner = result.scalar_one_or_none()
    if locked_partner:
        old_score = locked_partner.politeness_score or 5.0
        old_votes = locked_partner.politeness_votes or 0
        new_votes = old_votes + 1
        locked_partner.politeness_score = round((old_score * old_votes + stars) / new_votes, 2)
        locked_partner.politeness_votes = new_votes

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        return JSONResponse({"error": t.get("already_rated", "Already rated")}, status_code=409)

    return JSONResponse({"success": True, "message": t.get("rate_success", "Rated!")})


@router.get("/quiz", response_class=HTMLResponse)
async def quiz_page(request: Request, user: User = Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(QuizAnswer).where(QuizAnswer.user_id == user.id)
    )
    answered_ids = {qa.question_id for qa in result.scalars().all()}
    if len(answered_ids) >= TOTAL_QUESTIONS:
        return RedirectResponse("/matches", status_code=302)

    lang = get_lang(request, user)
    t = get_translations(lang)
    return templates.TemplateResponse(request, "quiz.html", {
        "user": user,
        "questions": QUIZ_QUESTIONS,
        "answered_ids": list(answered_ids),
        "total": TOTAL_QUESTIONS,
        "t": t,
        "rtl": is_rtl(lang),
    })


@router.post("/quiz/answer", dependencies=[Depends(validate_csrf_header), Depends(rate_limit(60, 60))])
async def quiz_answer(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    data = await request.json()
    question_id = data.get("question_id")
    answer_index = data.get("answer_index")

    if question_id is None or answer_index is None:
        return JSONResponse({"error": "Missing fields"}, status_code=400)
    valid_question_ids = {q["id"] for q in QUIZ_QUESTIONS}
    if not isinstance(question_id, int) or question_id not in valid_question_ids:
        return JSONResponse({"error": "Invalid question_id"}, status_code=400)
    if not isinstance(answer_index, int) or answer_index < 0 or answer_index > 3:
        return JSONResponse({"error": "Invalid answer_index"}, status_code=400)

    result = await db.execute(
        select(QuizAnswer).where(
            QuizAnswer.user_id == user.id,
            QuizAnswer.question_id == question_id,
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        existing.answer_index = answer_index
    else:
        db.add(QuizAnswer(user_id=user.id, question_id=question_id, answer_index=answer_index))

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()

    result = await db.execute(
        select(_func.count(QuizAnswer.id)).where(QuizAnswer.user_id == user.id)
    )
    answered_count = result.scalar() or 0
    return JSONResponse({"success": True, "answered": answered_count, "total": TOTAL_QUESTIONS})


