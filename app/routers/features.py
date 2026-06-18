import os
import random
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from app.auth import get_current_user
from app.database import get_db
from app.i18n import get_lang, get_translations, is_rtl
from app.models.models import Match, PolitenessVote, Profile, QuizAnswer, User
from app.quiz_questions import QUIZ_QUESTIONS, TOTAL_QUESTIONS
from app.templates import templates

router = APIRouter()

UPLOAD_DIR = Path("static/uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

VERIFY_GESTURES = [
    "✌️ peace sign",
    "👋 wave",
    "👍 thumbs up",
    "🤙 hang loose",
    "🖐️ open palm",
]


# ─────────────────────────────────────────────────────────────────────────────
# Function 2: AI Icebreakers
# ─────────────────────────────────────────────────────────────────────────────

@router.get("/chat/{match_id}/icebreakers")
def get_icebreakers(
    match_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    match = db.query(Match).filter(Match.id == match_id).first()
    if not match or (match.user1_id != user.id and match.user2_id != user.id):
        return JSONResponse({"error": "Forbidden"}, status_code=403)

    partner = match.user2 if match.user1_id == user.id else match.user1
    profile = partner.profile

    ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

    if not ANTHROPIC_API_KEY:
        # Return stubs when no API key is configured
        name = profile.name if profile else "them"
        city = profile.city or "their city"
        suggestions = [
            f"Hey {name}! What's your favourite thing to do in {city}?",
            f"Hi {name}, your profile caught my eye — what are you passionate about?",
            f"Hello {name}! If you could travel anywhere tomorrow, where would you go?",
        ]
        return JSONResponse({"suggestions": suggestions})

    try:
        import anthropic
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        name = profile.name if profile else "Unknown"
        age = str(profile.age) if profile else "unknown"
        city = profile.city or "unknown"
        bio = profile.bio or "no bio"

        prompt = (
            "You are a dating app assistant. Based on this profile, suggest 3 short, friendly opening messages "
            "(max 20 words each). Be creative, warm, reference something specific from their profile. "
            "Return ONLY a JSON array of 3 strings, no other text.\n"
            f"Profile: Name: {name}, Age: {age}, City: {city}, About: {bio}"
        )

        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        import json
        text = message.content[0].text.strip()
        suggestions = json.loads(text)
        if not isinstance(suggestions, list):
            raise ValueError("Not a list")
        suggestions = [str(s) for s in suggestions[:3]]
    except Exception:
        name = profile.name if profile else "them"
        city = (profile.city or "your city") if profile else "your city"
        suggestions = [
            f"Hey {name}! What do you love most about {city}?",
            f"Hi {name}, your profile stood out — what are you up to these days?",
            f"Hello! If you could do anything this weekend, what would it be?",
        ]

    return JSONResponse({"suggestions": suggestions})


# ─────────────────────────────────────────────────────────────────────────────
# Function 3: Politeness Rating
# ─────────────────────────────────────────────────────────────────────────────

@router.post("/chat/{match_id}/rate")
async def rate_politeness(
    match_id: int,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
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

    # Check for existing vote
    existing = db.query(PolitenessVote).filter(
        PolitenessVote.voter_id == user.id,
        PolitenessVote.target_id == partner.id,
    ).first()
    if existing:
        lang = get_lang(request, user)
        t = get_translations(lang)
        return JSONResponse({"error": t.get("already_rated", "Already rated")}, status_code=409)

    # Save vote
    vote = PolitenessVote(voter_id=user.id, target_id=partner.id, stars=stars)
    db.add(vote)

    # Update partner's rolling average
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
        lang = get_lang(request, user)
        t = get_translations(lang)
        return JSONResponse({"error": t.get("already_rated", "Already rated")}, status_code=409)

    lang = get_lang(request, user)
    t = get_translations(lang)
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


@router.post("/quiz/answer")
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
    if not isinstance(answer_index, int) or answer_index < 0 or answer_index > 3:
        return JSONResponse({"error": "Invalid answer_index"}, status_code=400)

    # Upsert answer
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


@router.post("/verify", response_class=HTMLResponse)
async def verify_submit(
    request: Request,
    photo: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    lang = get_lang(request, user)
    t = get_translations(lang)

    if not photo or not photo.filename:
        return templates.TemplateResponse("verify.html", {
            "request": request,
            "user": user,
            "gesture": user.verify_gesture or random.choice(VERIFY_GESTURES),
            "error": "Please upload a photo",
            "t": t,
            "rtl": is_rtl(lang),
        })

    # Validate that it's a real image using PIL
    try:
        from PIL import Image as PILImage
        import io
        contents = await photo.read()
        img = PILImage.open(io.BytesIO(contents))
        img.verify()  # verify it's a valid image
    except Exception:
        return templates.TemplateResponse("verify.html", {
            "request": request,
            "user": user,
            "gesture": user.verify_gesture or random.choice(VERIFY_GESTURES),
            "error": "Invalid image file. Please upload a valid photo.",
            "t": t,
            "rtl": is_rtl(lang),
        })

    # Save the selfie
    ext = Path(photo.filename).suffix.lower() or ".jpg"
    filename = f"verify_{uuid.uuid4().hex}{ext}"
    path = UPLOAD_DIR / filename
    path.write_bytes(contents)

    # Mark user as verified
    user.is_verified = True
    db.commit()

    return RedirectResponse("/profile/edit?verified=1", status_code=302)
