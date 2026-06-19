"""Compute user achievement badges from existing data."""
from sqlalchemy import func, or_
from sqlalchemy.orm import Session


def get_achievements(user, db: Session) -> list[dict]:
    from app.models.models import Match, QuizAnswer
    from app.quiz_questions import TOTAL_QUESTIONS

    badges = []

    # Match count
    match_count = db.query(func.count(Match.id)).filter(
        or_(Match.user1_id == user.id, Match.user2_id == user.id)
    ).scalar() or 0

    if match_count >= 1:
        badges.append({"icon": "💘", "label": "Первый матч"})
    if match_count >= 10:
        badges.append({"icon": "🏆", "label": "10 матчей"})
    if match_count >= 50:
        badges.append({"icon": "👑", "label": "50 матчей"})

    # Verified
    if user.is_verified:
        badges.append({"icon": "✅", "label": "Верифицирован"})

    # Premium
    if user.is_premium:
        badges.append({"icon": "⭐", "label": "Premium"})

    # Good politeness score
    if (user.politeness_score or 0) >= 4.5 and (user.politeness_votes or 0) >= 3:
        badges.append({"icon": "😊", "label": "Вежливый"})

    # Quiz completed
    quiz_count = db.query(func.count(QuizAnswer.id)).filter(
        QuizAnswer.user_id == user.id
    ).scalar() or 0
    if quiz_count >= TOTAL_QUESTIONS:
        badges.append({"icon": "🎯", "label": "Квиз пройден"})

    # Has interests filled
    if user.profile and user.profile.interests:
        badges.append({"icon": "🌟", "label": "Полный профиль"})

    # Phone verified
    if user.phone_verified:
        badges.append({"icon": "📱", "label": "Номер подтверждён"})

    return badges
