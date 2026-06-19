"""Zodiac sign helpers."""
from datetime import datetime

# (end_month, end_day, name, symbol, key)
_SIGNS = [
    (1,  20, "Козерог",   "♑", "capricorn"),
    (2,  19, "Водолей",   "♒", "aquarius"),
    (3,  20, "Рыбы",      "♓", "pisces"),
    (4,  20, "Овен",      "♈", "aries"),
    (5,  21, "Телец",     "♉", "taurus"),
    (6,  21, "Близнецы",  "♊", "gemini"),
    (7,  23, "Рак",       "♋", "cancer"),
    (8,  23, "Лев",       "♌", "leo"),
    (9,  23, "Дева",      "♍", "virgo"),
    (10, 23, "Весы",      "♎", "libra"),
    (11, 22, "Скорпион",  "♏", "scorpio"),
    (12, 22, "Стрелец",   "♐", "sagittarius"),
    (12, 31, "Козерог",   "♑", "capricorn"),  # Dec 23-31
]

# Explicit mapping key → 0-based row index in _COMPAT matrix (fixed, never changes)
_KEY_TO_INDEX: dict[str, int] = {
    "capricorn": 0, "aquarius": 1, "pisces": 2, "aries": 3,
    "taurus": 4, "gemini": 5, "cancer": 6, "leo": 7,
    "virgo": 8, "libra": 9, "scorpio": 10, "sagittarius": 11,
}

# Compatibility matrix [sign1_index][sign2_index] — values 0-100
_COMPAT: list[list[int]] = [
    [95, 60, 70, 80, 90, 65, 55, 75, 85, 95, 60, 70],  # Козерог
    [60, 95, 90, 65, 55, 85, 80, 60, 70, 60, 95, 75],  # Водолей
    [70, 90, 95, 50, 65, 80, 95, 65, 55, 70, 85, 60],  # Рыбы
    [80, 65, 50, 95, 85, 70, 60, 95, 65, 55, 70, 90],  # Овен
    [90, 55, 65, 85, 95, 60, 70, 80, 95, 65, 55, 70],  # Телец
    [65, 85, 80, 70, 60, 95, 65, 55, 75, 90, 70, 80],  # Близнецы
    [55, 80, 95, 60, 70, 65, 95, 70, 60, 75, 80, 55],  # Рак
    [75, 60, 65, 95, 80, 55, 70, 95, 85, 60, 65, 90],  # Лев
    [85, 70, 55, 65, 95, 75, 60, 85, 95, 70, 60, 65],  # Дева
    [95, 60, 70, 55, 65, 90, 75, 60, 70, 95, 85, 60],  # Весы
    [60, 95, 85, 70, 55, 70, 80, 65, 60, 85, 95, 70],  # Скорпион
    [70, 75, 60, 90, 70, 80, 55, 90, 65, 60, 70, 95],  # Стрелец
]


def get_sign(birth_date: datetime) -> dict:
    """Return zodiac info dict for a given birth_date."""
    if not birth_date:
        return {}
    month, day = birth_date.month, birth_date.day
    for end_month, end_day, name, symbol, key in _SIGNS:
        if month < end_month or (month == end_month and day <= end_day):
            return {
                "name": name,
                "symbol": symbol,
                "key": key,
                "index": _KEY_TO_INDEX[key],  # always correct, no list search
            }
    # Fallback (should never reach here given the Dec 31 catch-all)
    return {"name": "Козерог", "symbol": "♑", "key": "capricorn", "index": 0}


def compatibility(sign1: dict, sign2: dict) -> int:
    """Return compatibility % between two signs (0-100)."""
    if not sign1 or not sign2:
        return 0
    i1 = sign1.get("index", 0) % 12
    i2 = sign2.get("index", 0) % 12
    return _COMPAT[i1][i2]
