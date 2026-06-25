"""Tests for new settings endpoints: change password + GDPR data export."""
import secrets
from tests.conftest import make_auth_client, make_client, get_csrf


def _tag():
    return secrets.token_hex(4)


# ── Change password page ──────────────────────────────────────────────────────

def test_change_password_page_loads():
    c, _, _ = make_auth_client(f"cpwd_{_tag()}")
    r = c.get("/settings/password")
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")


def test_change_password_page_requires_auth():
    c = make_client()
    r = c.get("/settings/password", follow_redirects=False)
    assert r.status_code in (302, 401)


def test_change_password_page_has_form():
    c, _, _ = make_auth_client(f"cpwd_{_tag()}")
    r = c.get("/settings/password")
    assert r.status_code == 200
    assert 'type="password"' in r.text
    assert "current_password" in r.text
    assert "new_password" in r.text


def test_change_password_wrong_current():
    c, _, csrf = make_auth_client(f"cpwd_{_tag()}")
    r = c.post("/settings/password", data={
        "current_password": "wrongpassword_WRONG",
        "new_password": "newpass123",
        "confirm_password": "newpass123",
        "csrftoken": csrf,
    }, follow_redirects=False)
    assert r.status_code == 302
    assert "wrong_current" in r.headers.get("location", "")


def test_change_password_too_short():
    c, _, csrf = make_auth_client(f"cpwd_{_tag()}")
    r = c.post("/settings/password", data={
        "current_password": "TestPass123!",
        "new_password": "short1",
        "confirm_password": "short1",
        "csrftoken": csrf,
    }, follow_redirects=False)
    assert r.status_code == 302
    assert "too_short" in r.headers.get("location", "")


def test_change_password_no_digit():
    c, _, csrf = make_auth_client(f"cpwd_{_tag()}")
    r = c.post("/settings/password", data={
        "current_password": "TestPass123!",
        "new_password": "passwordnodigit",
        "confirm_password": "passwordnodigit",
        "csrftoken": csrf,
    }, follow_redirects=False)
    assert r.status_code == 302
    assert "no_digit" in r.headers.get("location", "")


def test_change_password_mismatch():
    c, _, csrf = make_auth_client(f"cpwd_{_tag()}")
    r = c.post("/settings/password", data={
        "current_password": "TestPass123!",
        "new_password": "newpass123",
        "confirm_password": "differentpass456",
        "csrftoken": csrf,
    }, follow_redirects=False)
    assert r.status_code == 302
    assert "no_match" in r.headers.get("location", "")


def test_change_password_same_as_current():
    c, _, csrf = make_auth_client(f"cpwd_{_tag()}")
    r = c.post("/settings/password", data={
        "current_password": "TestPass123!",
        "new_password": "TestPass123!",  # identical to current
        "confirm_password": "TestPass123!",
        "csrftoken": csrf,
    }, follow_redirects=False)
    assert r.status_code == 302
    assert "same_password" in r.headers.get("location", "")


def test_change_password_success():
    c, email, csrf = make_auth_client(f"cpwd_{_tag()}")
    r = c.post("/settings/password", data={
        "current_password": "TestPass123!",
        "new_password": "newpass9999",
        "confirm_password": "newpass9999",
        "csrftoken": csrf,
    }, follow_redirects=False)
    assert r.status_code == 302
    assert "saved=1" in r.headers.get("location", "")


def test_change_password_success_shows_confirmation():
    c, email, csrf = make_auth_client(f"cpwd_{_tag()}")
    c.post("/settings/password", data={
        "current_password": "TestPass123!",
        "new_password": "changed123!",
        "confirm_password": "changed123!",
        "csrftoken": csrf,
    })
    r = c.get("/settings/password?saved=1")
    assert r.status_code == 200
    # Template should show success message when saved=1
    assert "saved" in r.text.lower() or "success" in r.text.lower() or "✅" in r.text


def test_change_password_updates_db():
    """After a successful change, old password is rejected and new one works."""
    from tests.conftest import SessionLocal, _get_user_id
    from app.auth import verify_password
    from app.models.models import User as UserModel

    c, email, csrf = make_auth_client(f"cpwd_{_tag()}")
    c.post("/settings/password", data={
        "current_password": "TestPass123!",
        "new_password": "updated8888",
        "confirm_password": "updated8888",
        "csrftoken": csrf,
    })
    db = SessionLocal()
    try:
        uid = _get_user_id(db, email)
        u = db.query(UserModel).filter(UserModel.id == uid).first()
        assert verify_password("updated8888", u.hashed_password)
        assert not verify_password("TestPass123!", u.hashed_password)
    finally:
        db.close()


def test_change_password_increments_token_version():
    """token_version is incremented so old JWTs are invalidated."""
    from tests.conftest import SessionLocal, _get_user_id
    from app.models.models import User as UserModel

    c, email, csrf = make_auth_client(f"cpwd_{_tag()}")
    db = SessionLocal()
    try:
        uid = _get_user_id(db, email)
        u = db.query(UserModel).filter(UserModel.id == uid).first()
        old_version = u.token_version or 0
    finally:
        db.close()

    c.post("/settings/password", data={
        "current_password": "TestPass123!",
        "new_password": "newerpass99",
        "confirm_password": "newerpass99",
        "csrftoken": csrf,
    })

    db = SessionLocal()
    try:
        u = db.query(UserModel).filter(UserModel.id == uid).first()
        assert (u.token_version or 0) == old_version + 1
    finally:
        db.close()


def test_change_password_requires_csrf():
    c, _, _ = make_auth_client(f"cpwd_{_tag()}")
    r = c.post("/settings/password", data={
        "current_password": "TestPass123!",
        "new_password": "newpass123",
        "confirm_password": "newpass123",
    }, follow_redirects=False)
    assert r.status_code in (400, 403)


def test_change_password_link_on_page():
    """Page should contain link to forgot-password for those who forgot current."""
    c, _, _ = make_auth_client(f"cpwd_{_tag()}")
    r = c.get("/settings/password")
    assert r.status_code == 200
    assert "forgot-password" in r.text


# ── GDPR data export ──────────────────────────────────────────────────────────

def test_export_requires_auth():
    c = make_client()
    r = c.get("/account/export", follow_redirects=False)
    assert r.status_code in (302, 401)


def test_export_returns_json():
    c, _, _ = make_auth_client(f"exp_{_tag()}")
    r = c.get("/account/export")
    assert r.status_code == 200
    assert "json" in r.headers.get("content-type", "")


def test_export_has_attachment_header():
    c, _, _ = make_auth_client(f"exp_{_tag()}")
    r = c.get("/account/export")
    assert r.status_code == 200
    cd = r.headers.get("content-disposition", "")
    assert "attachment" in cd
    assert ".json" in cd


def test_export_no_cache():
    c, _, _ = make_auth_client(f"exp_{_tag()}")
    r = c.get("/account/export")
    cc = r.headers.get("cache-control", "")
    assert "no-store" in cc


def test_export_contains_user_section():
    c, email, _ = make_auth_client(f"exp_{_tag()}")
    r = c.get("/account/export")
    data = r.json()
    assert "user" in data
    assert data["user"]["email"] == email


def test_export_contains_all_sections():
    c, _, _ = make_auth_client(f"exp_{_tag()}")
    r = c.get("/account/export")
    data = r.json()
    for section in ["user", "profile", "gallery", "quiz_answers",
                    "messages_sent", "matches", "blocks", "stories"]:
        assert section in data, f"Missing section: {section}"


def test_export_has_exported_at_timestamp():
    c, _, _ = make_auth_client(f"exp_{_tag()}")
    r = c.get("/account/export")
    data = r.json()
    assert "exported_at" in data
    assert data["exported_at"].endswith("Z")


def test_export_user_fields():
    c, email, _ = make_auth_client(f"exp_{_tag()}")
    r = c.get("/account/export")
    user = r.json()["user"]
    assert "id" in user
    assert "email" in user
    assert "created_at" in user
    assert "email_verified" in user
    assert "is_premium" in user
    assert "language" in user


def test_export_messages_empty_by_default():
    c, _, _ = make_auth_client(f"exp_{_tag()}")
    r = c.get("/account/export")
    assert r.json()["messages_sent"] == []


def test_export_matches_empty_by_default():
    c, _, _ = make_auth_client(f"exp_{_tag()}")
    r = c.get("/account/export")
    assert r.json()["matches"] == []


def test_export_quiz_answers_appear():
    """After answering a quiz question, it appears in export."""
    c, _, csrf = make_auth_client(f"exp_{_tag()}")
    from app.quiz_questions import QUIZ_QUESTIONS
    qid = QUIZ_QUESTIONS[0]["id"]
    c.post("/quiz/answer", json={"question_id": qid, "answer_index": 1},
           headers={"x-csrf-token": csrf})

    r = c.get("/account/export")
    answers = r.json()["quiz_answers"]
    assert len(answers) >= 1
    assert any(a["question_id"] == qid for a in answers)


def test_export_profile_not_none_after_edit():
    """After editing profile, export returns profile data."""
    c, _, csrf = make_auth_client(f"exp_{_tag()}")
    c.post("/profile/edit", data={
        "name": "ExportUser", "age": "30", "gender": "male",
        "looking_for": "female", "csrftoken": csrf,
    })
    r = c.get("/account/export")
    profile = r.json()["profile"]
    assert profile is not None
    assert profile["name"] == "ExportUser"
    assert profile["age"] == 30


# ── X-RateLimit headers (unit test, bypasses TESTING env var) ────────────────

def test_rate_limit_headers_logic():
    """rate_limit dependency adds X-RateLimit headers to the response object."""
    import asyncio, os, time
    from unittest.mock import MagicMock
    from fastapi import Request, Response

    # Temporarily disable TESTING so the rate limiter actually runs
    os.environ.pop("TESTING", None)
    try:
        from app.rate_limit import rate_limit, _store
        _store.clear()

        # Build a minimal fake request
        req = MagicMock(spec=Request)
        req.headers = {}
        req.client = MagicMock()
        req.client.host = "1.2.3.4"
        req.url = MagicMock()
        req.url.path = "/test-rl"

        resp = MagicMock(spec=Response)
        resp.headers = {}

        dep = rate_limit(10, 60)
        asyncio.run(dep(req, resp))

        assert "X-RateLimit-Limit" in resp.headers
        assert resp.headers["X-RateLimit-Limit"] == "10"
        assert "X-RateLimit-Remaining" in resp.headers
        assert int(resp.headers["X-RateLimit-Remaining"]) == 9
    finally:
        os.environ["TESTING"] = "1"


def test_rate_limit_remaining_decrements():
    """Each call decrements X-RateLimit-Remaining."""
    import asyncio, os
    from unittest.mock import MagicMock
    from fastapi import Request, Response

    os.environ.pop("TESTING", None)
    try:
        from app.rate_limit import rate_limit, _store
        _store.clear()

        req = MagicMock(spec=Request)
        req.headers = {}
        req.client = MagicMock()
        req.client.host = "5.6.7.8"
        req.url = MagicMock()
        req.url.path = "/test-rl-dec"

        dep = rate_limit(5, 60)
        for expected_remaining in [4, 3, 2]:
            resp = MagicMock(spec=Response)
            resp.headers = {}
            asyncio.run(dep(req, resp))
            assert int(resp.headers["X-RateLimit-Remaining"]) == expected_remaining
    finally:
        os.environ["TESTING"] = "1"
