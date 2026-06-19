QUIZ_CATEGORIES = {
    "lifestyle":     {"icon": "🌿", "color": "green"},
    "values":        {"icon": "💎", "color": "purple"},
    "communication": {"icon": "💬", "color": "blue"},
    "romance":       {"icon": "❤️",  "color": "pink"},
    "personality":   {"icon": "✨", "color": "yellow"},
}

CATEGORY_ORDER = ["lifestyle", "values", "communication", "romance", "personality"]

QUIZ_QUESTIONS = [
    # ── Lifestyle ──
    {"id": 1,  "key": "q1",  "category": "lifestyle",     "options": ["q1_a",  "q1_b",  "q1_c",  "q1_d"]},
    {"id": 4,  "key": "q4",  "category": "lifestyle",     "options": ["q4_a",  "q4_b",  "q4_c",  "q4_d"]},
    {"id": 6,  "key": "q6",  "category": "lifestyle",     "options": ["q6_a",  "q6_b",  "q6_c",  "q6_d"]},
    {"id": 8,  "key": "q8",  "category": "lifestyle",     "options": ["q8_a",  "q8_b",  "q8_c",  "q8_d"]},
    {"id": 11, "key": "q11", "category": "lifestyle",     "options": ["q11_a", "q11_b", "q11_c", "q11_d"]},
    # ── Values ──
    {"id": 3,  "key": "q3",  "category": "values",        "options": ["q3_a",  "q3_b",  "q3_c",  "q3_d"]},
    {"id": 10, "key": "q10", "category": "values",        "options": ["q10_a", "q10_b", "q10_c", "q10_d"]},
    {"id": 12, "key": "q12", "category": "values",        "options": ["q12_a", "q12_b", "q12_c", "q12_d"]},
    {"id": 13, "key": "q13", "category": "values",        "options": ["q13_a", "q13_b", "q13_c", "q13_d"]},
    {"id": 14, "key": "q14", "category": "values",        "options": ["q14_a", "q14_b", "q14_c", "q14_d"]},
    # ── Communication ──
    {"id": 2,  "key": "q2",  "category": "communication", "options": ["q2_a",  "q2_b",  "q2_c",  "q2_d"]},
    {"id": 5,  "key": "q5",  "category": "communication", "options": ["q5_a",  "q5_b",  "q5_c",  "q5_d"]},
    {"id": 15, "key": "q15", "category": "communication", "options": ["q15_a", "q15_b", "q15_c", "q15_d"]},
    {"id": 16, "key": "q16", "category": "communication", "options": ["q16_a", "q16_b", "q16_c", "q16_d"]},
    {"id": 17, "key": "q17", "category": "communication", "options": ["q17_a", "q17_b", "q17_c", "q17_d"]},
    # ── Romance ──
    {"id": 7,  "key": "q7",  "category": "romance",       "options": ["q7_a",  "q7_b",  "q7_c",  "q7_d"]},
    {"id": 18, "key": "q18", "category": "romance",       "options": ["q18_a", "q18_b", "q18_c", "q18_d"]},
    {"id": 19, "key": "q19", "category": "romance",       "options": ["q19_a", "q19_b", "q19_c", "q19_d"]},
    {"id": 20, "key": "q20", "category": "romance",       "options": ["q20_a", "q20_b", "q20_c", "q20_d"]},
    {"id": 21, "key": "q21", "category": "romance",       "options": ["q21_a", "q21_b", "q21_c", "q21_d"]},
    # ── Personality ──
    {"id": 9,  "key": "q9",  "category": "personality",   "options": ["q9_a",  "q9_b",  "q9_c",  "q9_d"]},
    {"id": 22, "key": "q22", "category": "personality",   "options": ["q22_a", "q22_b", "q22_c", "q22_d"]},
    {"id": 23, "key": "q23", "category": "personality",   "options": ["q23_a", "q23_b", "q23_c", "q23_d"]},
    {"id": 24, "key": "q24", "category": "personality",   "options": ["q24_a", "q24_b", "q24_c", "q24_d"]},
    {"id": 25, "key": "q25", "category": "personality",   "options": ["q25_a", "q25_b", "q25_c", "q25_d"]},
]

TOTAL_QUESTIONS = len(QUIZ_QUESTIONS)

QID_TO_CATEGORY = {q["id"]: q["category"] for q in QUIZ_QUESTIONS}
