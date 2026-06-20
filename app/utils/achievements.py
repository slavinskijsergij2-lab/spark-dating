"""Compute user achievement badges from existing data."""
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

_LABELS: dict[str, dict[str, str]] = {
    "first_match": {
        "ru": "Первый матч", "uk": "Перший матч",
        "en": "First match", "de": "Erster Match",
        "tr": "İlk eşleşme", "ar": "أول تطابق",
    },
    "matches_10": {
        "ru": "10 матчей", "uk": "10 матчів",
        "en": "10 matches", "de": "10 Matches",
        "tr": "10 eşleşme", "ar": "١٠ تطابقات",
    },
    "matches_50": {
        "ru": "50 матчей", "uk": "50 матчів",
        "en": "50 matches", "de": "50 Matches",
        "tr": "50 eşleşme", "ar": "٥٠ تطابقاً",
    },
    "verified": {
        "ru": "Верифицирован", "uk": "Верифіковано",
        "en": "Verified", "de": "Verifiziert",
        "tr": "Doğrulandı", "ar": "موثق",
    },
    "premium": {
        "ru": "Premium", "uk": "Premium",
        "en": "Premium", "de": "Premium",
        "tr": "Premium", "ar": "Premium",
    },
    "polite": {
        "ru": "Вежливый", "uk": "Ввічливий",
        "en": "Polite", "de": "Höflich",
        "tr": "Kibar", "ar": "لطيف",
    },
    "quiz_done": {
        "ru": "Квиз пройден", "uk": "Квіз пройдено",
        "en": "Quiz done", "de": "Quiz abgeschlossen",
        "tr": "Test tamamlandı", "ar": "اكتمل الاختبار",
    },
    "full_profile": {
        "ru": "Полный профиль", "uk": "Повний профіль",
        "en": "Full profile", "de": "Vollständiges Profil",
        "tr": "Tam profil", "ar": "ملف كامل",
    },
    "phone_verified": {
        "ru": "Номер подтверждён", "uk": "Номер підтверджено",
        "en": "Phone verified", "de": "Telefon bestätigt",
        "tr": "Telefon doğrulandı", "ar": "الهاتف موثق",
    },
}


def _lbl(key: str, lang: str) -> str:
    translations = _LABELS.get(key, {})
    return translations.get(lang) or translations.get("en") or key


def get_achievements(user, db: Session, lang: str = "ru") -> list[dict]:
    from app.models.models import Match, QuizAnswer
    from app.quiz_questions import TOTAL_QUESTIONS

    badges = []

    match_count = db.query(func.count(Match.id)).filter(
        or_(Match.user1_id == user.id, Match.user2_id == user.id)
    ).scalar() or 0

    if match_count >= 1:
        badges.append({"icon": "💘", "label": _lbl("first_match", lang)})
    if match_count >= 10:
        badges.append({"icon": "🏆", "label": _lbl("matches_10", lang)})
    if match_count >= 50:
        badges.append({"icon": "👑", "label": _lbl("matches_50", lang)})

    if user.is_verified:
        badges.append({"icon": "✅", "label": _lbl("verified", lang)})

    if user.is_premium:
        badges.append({"icon": "⭐", "label": _lbl("premium", lang)})

    if (user.politeness_score or 0) >= 4.5 and (user.politeness_votes or 0) >= 3:
        badges.append({"icon": "😊", "label": _lbl("polite", lang)})

    quiz_count = db.query(func.count(QuizAnswer.id)).filter(
        QuizAnswer.user_id == user.id
    ).scalar() or 0
    if quiz_count >= TOTAL_QUESTIONS:
        badges.append({"icon": "🎯", "label": _lbl("quiz_done", lang)})

    if user.profile and user.profile.interests:
        badges.append({"icon": "🌟", "label": _lbl("full_profile", lang)})

    if user.phone_verified:
        badges.append({"icon": "📱", "label": _lbl("phone_verified", lang)})

    return badges
