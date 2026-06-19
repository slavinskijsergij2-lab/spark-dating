import io
import os
import random

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.auth import get_current_user
from app.csrf import validate_csrf_form, validate_csrf_header
from app.database import get_db
from app.i18n import get_lang, get_translations, is_rtl
from app.models.models import Match, PolitenessVote, Profile, QuizAnswer, User
from app.quiz_questions import QUIZ_QUESTIONS, TOTAL_QUESTIONS
from app.templates import templates

router = APIRouter()

VERIFY_GESTURES = [
    "✌️ peace sign",
    "👋 wave",
    "👍 thumbs up",
    "🤙 hang loose",
    "🖐️ open palm",
]

# Singleton Anthropic client — created once at import time if API key is set
_anthropic_client = None
_ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
if _ANTHROPIC_API_KEY:
    try:
        import anthropic as _anthropic_module
        _anthropic_client = _anthropic_module.Anthropic(api_key=_ANTHROPIC_API_KEY)
    except Exception:
        _anthropic_client = None


def _sanitize_prompt_field(text: str, max_len: int = 200) -> str:
    """FIX H3: strip newlines and limit length to mitigate prompt injection."""
    if not text:
        return ""
    return text.replace("\n", " ").replace("\r", " ").replace("```", "").strip()[:max_len]


# ─────────────────────────────────────────────────────────────────────────────
# Function 2: AI Icebreakers
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/chat/{match_id}/icebreakers")
async def get_icebreakers(
    match_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    match = db.query(Match).filter(Match.id == match_id).first()
    if not match or (match.user1_id != user.id and match.user2_id != user.id):
        return JSONResponse({"error": "Forbidden"}, status_code=403)

    partner = match.user2 if match.user1_id == user.id else match.user1
    profile = partner.profile

    # FIX H2: guard every profile field — profile may be None
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

        # H1: wrap synchronous Anthropic SDK call in a thread so it doesn't block the event loop
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


# ─────────────────────────────────────────────────────────────────────────────
# Function 3: Politeness Rating
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/chat/{match_id}/rate", dependencies=[Depends(validate_csrf_header)])
async def rate_politeness(
    match_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # FIX M4: resolve lang once, not twice
    lang = get_lang(request, user)
    t = get_translations(lang)

    data = await request.json()
    stars = data.get("stars")
    if not isinstance(stars, int) or stars < 1 or stars > 5:
        return JSONResponse({"error": "Invalid stars value"}, status_code=400)

    match = db.query(Match).filter(Match.id == match_id).first()
    if not match or (match.user1_id != user.id and match.user2_id != user.id):
        return JSONResponse({"error": "Forbidden"}, status_code=403)

    partner = match.user2 if match.user1_id == user.id else match.user1
    if partner.id == user.id:
        return JSONResponse({"error": "Cannot rate yourself"}, status_code=400)

    existing = db.query(PolitenessVote).filter(
        PolitenessVote.voter_id == user.id,
        PolitenessVote.target_id == partner.id,
    ).first()
    if existing:
        return JSONResponse({"error": t.get("already_rated", "Already rated")}, status_code=409)

    vote = PolitenessVote(voter_id=user.id, target_id=partner.id, stars=stars)
    db.add(vote)

    old_score = partner.politeness_score or 5.0
    old_votes = partner.politeness_votes or 0
    new_votes = old_votes + 1
    new_score = (old_score * old_votes + stars) / new_votes
    partner.politeness_score = round(new_score, 2)
    partner.politeness_votes = new_votes

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return JSONResponse({"error": t.get("already_rated", "Already rated")}, status_code=409)

    return JSONResponse({"success": True, "message": t.get("rate_success", "Rated!")})


# ─────────────────────────────────────────────────────────────────────────────
# Function 6: Quiz
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/quiz", response_class=HTMLResponse)
def quiz_page(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    answered_ids = {
        qa.question_id
        for qa in db.query(QuizAnswer).filter(QuizAnswer.user_id == user.id).all()
    }
    if len(answered_ids) >= TOTAL_QUESTIONS:
        return RedirectResponse("/matches", status_code=302)

    lang = get_lang(request, user)
    t = get_translations(lang)
    return templates.TemplateResponse("quiz.html", {
        "request": request,
        "user": user,
        "questions": QUIZ_QUESTIONS,
        "answered_ids": list(answered_ids),
        "total": TOTAL_QUESTIONS,
        "t": t,
        "rtl": is_rtl(lang),
    })


@router.post("/quiz/answer", dependencies=[Depends(validate_csrf_header)])
async def quiz_answer(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    data = await request.json()
    question_id = data.get("question_id")
    answer_index = data.get("answer_index")

    if question_id is None or answer_index is None:
        return JSONResponse({"error": "Missing fields"}, status_code=400)
    # MEDIUM-2: validate against actual question IDs (1-based), not 0-based index
    valid_question_ids = {q["id"] for q in QUIZ_QUESTIONS}
    if not isinstance(question_id, int) or question_id not in valid_question_ids:
        return JSONResponse({"error": "Invalid question_id"}, status_code=400)
    if not isinstance(answer_index, int) or answer_index < 0 or answer_index > 3:
        return JSONResponse({"error": "Invalid answer_index"}, status_code=400)

    existing = db.query(QuizAnswer).filter(
        QuizAnswer.user_id == user.id,
        QuizAnswer.question_id == question_id,
    ).first()
    if existing:
        existing.answer_index = answer_index
    else:
        qa = QuizAnswer(user_id=user.id, question_id=question_id, answer_index=answer_index)
        db.add(qa)

    try:
        db.commit()
    except IntegrityError:
        db.rollback()

    answered_count = db.query(QuizAnswer).filter(QuizAnswer.user_id == user.id).count()
    return JSONResponse({"success": True, "answered": answered_count, "total": TOTAL_QUESTIONS})


# ─────────────────────────────────────────────────────────────────────────────
# Function 7: Photo Verification
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/verify", response_class=HTMLResponse)
def verify_page(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not user.verify_gesture:
        user.verify_gesture = random.choice(VERIFY_GESTURES)
        db.commit()
    lang = get_lang(request, user)
    t = get_translations(lang)
    return templates.TemplateResponse("verify.html", {
        "request": request,
        "user": user,
        "gesture": user.verify_gesture,
        "t": t,
        "rtl": is_rtl(lang),
    })


@router.post("/verify", response_class=HTMLResponse, dependencies=[Depends(validate_csrf_form)])
async def verify_submit(
    request: Request,
    photo: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    lang = get_lang(request, user)
    t = get_translations(lang)

    def _err(msg: str):
        return templates.TemplateResponse("verify.html", {
            "request": request,
            "user": user,
            "gesture": user.verify_gesture or random.choice(VERIFY_GESTURES),
            "error": msg,
            "t": t,
            "rtl": is_rtl(lang),
        })

    if not photo or not photo.filename:
        return _err("Please upload a photo")

    # Read up to 10MB and validate it's a real image
    try:
        import base64
        from PIL import Image as PILImage
        contents = await photo.read(10 * 1024 * 1024 + 1)
        if len(contents) > 10 * 1024 * 1024:
            return _err("File too large (max 10 MB)")
        PILImage.open(io.BytesIO(contents)).load()
    except Exception:
        return _err("Invalid image file. Please upload a valid photo.")

    # H3: use Anthropic Vision to confirm the required gesture is present.
    # Falls back to auto-approve when ANTHROPIC_API_KEY is not set.
    if _anthropic_client and user.verify_gesture:
        try:
            import asyncio
            ext = (photo.filename or "photo.jpg").rsplit(".", 1)[-1].lower()
            media_type_map = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png", "webp": "image/webp"}
            img_media_type = media_type_map.get(ext, "image/jpeg")
            b64_data = base64.b64encode(contents).decode()

            gesture_clean = _sanitize_prompt_field(user.verify_gesture, 50)
            vision_prompt = (
                "You are a photo verification assistant for a dating app. "
                f"The user was asked to show this gesture: {gesture_clean}. "
                "Look at the image and answer with ONLY 'yes' or 'no': "
                "is there a human hand or person clearly showing that gesture?"
            )

            # H1: wrap sync Anthropic call in thread so event loop isn't blocked
            def _call_vision():
                return _anthropic_client.messages.create(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=10,
                    messages=[{
                        "role": "user",
                        "content": [
                            {"type": "image", "source": {"type": "base64", "media_type": img_media_type, "data": b64_data}},
                            {"type": "text", "text": vision_prompt},
                        ],
                    }],
                )

            vision_msg = await asyncio.to_thread(_call_vision)
            answer = vision_msg.content[0].text.strip().lower()
            if not answer.startswith("yes"):
                return _err(
                    t.get("verify_gesture_not_found", "Gesture not detected. Please try again with better lighting.")
                )
        except Exception:
            pass  # Vision unavailable — proceed to auto-approve

    user.is_verified = True
    user.verify_gesture = None  # Clear gesture so a new one is assigned next time
    db.commit()

    return RedirectResponse("/profile/edit?verified=1", status_code=302)
