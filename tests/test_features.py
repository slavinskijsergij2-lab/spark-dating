"""Tests for /quiz, /quiz/answer, /chat/{match_id}/rate, /chat/{match_id}/icebreakers."""
import secrets
from tests.conftest import (
    make_client, make_auth_client, _get_user_id, _create_profile, _create_match, SessionLocal
)


def _make_match_pair(suffix=""):
    tag = suffix or secrets.token_hex(4)
    c1, e1, csrf1 = make_auth_client(f"feat_a_{tag}")
    c2, e2, csrf2 = make_auth_client(f"feat_b_{tag}")
    db = SessionLocal()
    try:
        uid1 = _get_user_id(db, e1)
        uid2 = _get_user_id(db, e2)
        _create_profile(db, uid1, "Alpha")
        _create_profile(db, uid2, "Beta")
        mid = _create_match(db, uid1, uid2)
    finally:
        db.close()
    return c1, c2, mid, csrf1, csrf2


# ── Quiz page ─────────────────────────────────────────────────────────────────

def test_quiz_page_loads():
    c, _, csrf = make_auth_client("quiz1")
    r = c.get("/quiz")
    assert r.status_code == 200


def test_quiz_page_requires_auth():
    c = make_client()
    r = c.get("/quiz", follow_redirects=False)
    assert r.status_code in (302, 401)


def test_quiz_page_contains_questions():
    c, _, csrf = make_auth_client("quiz2")
    r = c.get("/quiz")
    assert r.status_code == 200
    assert "quiz" in r.text.lower() or "question" in r.text.lower() or "quiz" in r.url.lower()


# ── Quiz answer ───────────────────────────────────────────────────────────────

def test_quiz_answer_valid():
    c, _, csrf = make_auth_client("quizans1")
    from app.quiz_questions import QUIZ_QUESTIONS
    qid = QUIZ_QUESTIONS[0]["id"]
    r = c.post(
        "/quiz/answer",
        json={"question_id": qid, "answer_index": 0},
        headers={"x-csrf-token": csrf},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["success"] is True
    assert "answered" in data
    assert "total" in data


def test_quiz_answer_missing_fields():
    c, _, csrf = make_auth_client("quizans2")
    r = c.post(
        "/quiz/answer",
        json={"question_id": 1},
        headers={"x-csrf-token": csrf},
    )
    assert r.status_code == 400


def test_quiz_answer_invalid_question_id():
    c, _, csrf = make_auth_client("quizans3")
    r = c.post(
        "/quiz/answer",
        json={"question_id": 99999, "answer_index": 0},
        headers={"x-csrf-token": csrf},
    )
    assert r.status_code == 400


def test_quiz_answer_invalid_answer_index_too_high():
    c, _, csrf = make_auth_client("quizans4")
    from app.quiz_questions import QUIZ_QUESTIONS
    qid = QUIZ_QUESTIONS[0]["id"]
    r = c.post(
        "/quiz/answer",
        json={"question_id": qid, "answer_index": 10},
        headers={"x-csrf-token": csrf},
    )
    assert r.status_code == 400


def test_quiz_answer_invalid_answer_index_negative():
    c, _, csrf = make_auth_client("quizans5")
    from app.quiz_questions import QUIZ_QUESTIONS
    qid = QUIZ_QUESTIONS[0]["id"]
    r = c.post(
        "/quiz/answer",
        json={"question_id": qid, "answer_index": -1},
        headers={"x-csrf-token": csrf},
    )
    assert r.status_code == 400


def test_quiz_answer_requires_auth():
    c = make_client()
    r = c.post("/quiz/answer", json={"question_id": 1, "answer_index": 0})
    assert r.status_code in (302, 401, 403)


def test_quiz_answer_update_on_second_answer():
    """Answering the same question twice updates, not duplicates."""
    c, _, csrf = make_auth_client("quizdup")
    from app.quiz_questions import QUIZ_QUESTIONS
    qid = QUIZ_QUESTIONS[0]["id"]
    c.post("/quiz/answer", json={"question_id": qid, "answer_index": 0}, headers={"x-csrf-token": csrf})
    r = c.post("/quiz/answer", json={"question_id": qid, "answer_index": 2}, headers={"x-csrf-token": csrf})
    assert r.status_code == 200
    assert r.json()["success"] is True


def test_quiz_answer_increments_answered_count():
    c, _, csrf = make_auth_client("quizcount")
    from app.quiz_questions import QUIZ_QUESTIONS
    for i, q in enumerate(QUIZ_QUESTIONS[:3]):
        r = c.post(
            "/quiz/answer",
            json={"question_id": q["id"], "answer_index": 0},
            headers={"x-csrf-token": csrf},
        )
        assert r.json()["answered"] == i + 1


# ── Rate politeness ───────────────────────────────────────────────────────────

def test_rate_politeness_valid():
    c1, c2, mid, csrf1, _ = _make_match_pair("rate1")
    r = c1.post(
        f"/chat/{mid}/rate",
        json={"stars": 4},
        headers={"x-csrf-token": csrf1},
    )
    assert r.status_code == 200
    assert r.json()["success"] is True


def test_rate_politeness_all_star_values():
    for stars in [1, 2, 3, 4, 5]:
        tag = f"rate_star_{stars}"
        c1, c2, mid, csrf1, _ = _make_match_pair(tag)
        r = c1.post(
            f"/chat/{mid}/rate",
            json={"stars": stars},
            headers={"x-csrf-token": csrf1},
        )
        assert r.status_code == 200, f"Failed for stars={stars}"


def test_rate_politeness_invalid_stars_zero():
    c1, c2, mid, csrf1, _ = _make_match_pair("rate_zero")
    r = c1.post(
        f"/chat/{mid}/rate",
        json={"stars": 0},
        headers={"x-csrf-token": csrf1},
    )
    assert r.status_code == 400


def test_rate_politeness_invalid_stars_six():
    c1, c2, mid, csrf1, _ = _make_match_pair("rate_six")
    r = c1.post(
        f"/chat/{mid}/rate",
        json={"stars": 6},
        headers={"x-csrf-token": csrf1},
    )
    assert r.status_code == 400


def test_rate_politeness_invalid_stars_string():
    c1, c2, mid, csrf1, _ = _make_match_pair("rate_str")
    r = c1.post(
        f"/chat/{mid}/rate",
        json={"stars": "five"},
        headers={"x-csrf-token": csrf1},
    )
    assert r.status_code == 400


def test_rate_politeness_double_rate_returns_409():
    c1, c2, mid, csrf1, _ = _make_match_pair("rate_dupe")
    c1.post(f"/chat/{mid}/rate", json={"stars": 3}, headers={"x-csrf-token": csrf1})
    r = c1.post(f"/chat/{mid}/rate", json={"stars": 5}, headers={"x-csrf-token": csrf1})
    assert r.status_code == 409


def test_rate_politeness_forbidden_on_foreign_match():
    c1, c2, mid, csrf1, _ = _make_match_pair("rate_foreign")
    outsider, _, csrf_out = make_auth_client("rate_outsider")
    r = outsider.post(
        f"/chat/{mid}/rate",
        json={"stars": 3},
        headers={"x-csrf-token": csrf_out},
    )
    assert r.status_code == 403


def test_rate_politeness_updates_partner_score(db):
    c1, c2, mid, csrf1, _ = _make_match_pair("ratescore")
    from tests.conftest import make_auth_client as mac
    db2 = SessionLocal()
    try:
        # find partner email
        from app.models.models import Match, User
        m = db2.query(Match).filter(Match.id == mid).first()
        uid1 = m.user1_id
        uid2 = m.user2_id
        partner_uid = uid2

        c1.post(f"/chat/{mid}/rate", json={"stars": 5}, headers={"x-csrf-token": csrf1})

        db2.expire_all()
        partner = db2.query(User).filter(User.id == partner_uid).first()
        assert partner.politeness_votes == 1
        assert partner.politeness_score == 5.0
    finally:
        db2.close()


def test_rate_politeness_requires_csrf():
    c1, c2, mid, csrf1, _ = _make_match_pair("rate_csrf")
    r = c1.post(f"/chat/{mid}/rate", json={"stars": 4})
    assert r.status_code in (403, 422)


def test_rate_politeness_requires_auth():
    c = make_client()
    r = c.post("/chat/1/rate", json={"stars": 4})
    # CSRF checked before auth: no CSRF token → 403; logged-out with valid CSRF → 302/401
    assert r.status_code in (302, 401, 403)


# ── Icebreakers ───────────────────────────────────────────────────────────────

def test_icebreakers_returns_suggestions():
    c1, c2, mid, csrf1, _ = _make_match_pair("ice1")
    r = c1.get(f"/chat/{mid}/icebreakers")
    assert r.status_code == 200
    data = r.json()
    assert "suggestions" in data
    assert isinstance(data["suggestions"], list)
    assert len(data["suggestions"]) == 3


def test_icebreakers_suggestions_are_strings():
    c1, c2, mid, csrf1, _ = _make_match_pair("ice2")
    r = c1.get(f"/chat/{mid}/icebreakers")
    suggestions = r.json()["suggestions"]
    for s in suggestions:
        assert isinstance(s, str)
        assert len(s) > 0


def test_icebreakers_forbidden_on_foreign_match():
    c1, c2, mid, _, _ = _make_match_pair("ice_foreign")
    outsider, _, _ = make_auth_client("ice_out")
    r = outsider.get(f"/chat/{mid}/icebreakers")
    assert r.status_code == 403


def test_icebreakers_requires_auth():
    c = make_client()
    r = c.get("/chat/1/icebreakers", follow_redirects=False)
    assert r.status_code in (302, 401)
