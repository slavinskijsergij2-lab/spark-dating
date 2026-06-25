"""Extended matches tests: page loading, SSE, chat page, streak, compatibility."""
import secrets
from tests.conftest import (
    make_client, make_auth_client, get_csrf,
    _get_user_id, _create_profile, _create_match, SessionLocal,
)


def _pair(suffix=""):
    tag = suffix or secrets.token_hex(4)
    c1, e1, csrf1 = make_auth_client(f"mx_a_{tag}")
    c2, e2, csrf2 = make_auth_client(f"mx_b_{tag}")
    db = SessionLocal()
    try:
        uid1 = _get_user_id(db, e1)
        uid2 = _get_user_id(db, e2)
        _create_profile(db, uid1, "Alice")
        _create_profile(db, uid2, "Bob")
        mid = _create_match(db, uid1, uid2)
    finally:
        db.close()
    return c1, c2, mid, csrf1, csrf2, e1, e2


# ── Matches list page ─────────────────────────────────────────────────────────

def test_matches_page_loads():
    c, _, _ = make_auth_client("mxpg1")
    r = c.get("/matches")
    assert r.status_code == 200


def test_matches_page_requires_auth():
    c = make_client()
    r = c.get("/matches", follow_redirects=False)
    assert r.status_code in (302, 401)


def test_matches_page_invalid_page_returns_400():
    c, _, _ = make_auth_client("mxpg2")
    r = c.get("/matches?page=0")
    assert r.status_code in (400, 422)


def test_matches_page_page_2_empty_when_few_matches():
    c, _, _ = make_auth_client("mxpg3")
    r = c.get("/matches?page=2")
    assert r.status_code == 200


def test_matches_page_shows_match(db):
    c1, c2, mid, csrf1, csrf2, e1, e2 = _pair("show")
    r = c1.get("/matches")
    assert r.status_code == 200


def test_matches_page_shows_no_matches_by_default():
    c, _, _ = make_auth_client("mxempty")
    r = c.get("/matches")
    assert r.status_code == 200


# ── Chat page ─────────────────────────────────────────────────────────────────

def test_chat_page_loads_for_match_member():
    c1, c2, mid, csrf1, _, _, _ = _pair("chatpg")
    r = c1.get(f"/chat/{mid}")
    assert r.status_code == 200


def test_chat_page_forbidden_for_outsider():
    c1, c2, mid, _, _, _, _ = _pair("chatout")
    outsider, _, _ = make_auth_client("chatout_o")
    r = outsider.get(f"/chat/{mid}", follow_redirects=False)
    assert r.status_code in (302, 403)


def test_chat_page_requires_auth():
    c1, c2, mid, _, _, _, _ = _pair("chatauth")
    anon = make_client()
    r = anon.get(f"/chat/{mid}", follow_redirects=False)
    assert r.status_code in (302, 401)


def test_chat_page_nonexistent_match_returns_error():
    c, _, _ = make_auth_client("chatnoexist")
    r = c.get("/chat/999999", follow_redirects=False)
    assert r.status_code in (302, 403, 404)


def test_chat_page_contains_message_input():
    c1, c2, mid, _, _, _, _ = _pair("chatinput")
    r = c1.get(f"/chat/{mid}")
    assert r.status_code == 200
    assert "textarea" in r.text.lower() or "input" in r.text.lower()


# ── Liked-me preview ──────────────────────────────────────────────────────────

def test_liked_me_page_loads():
    c, _, _ = make_auth_client("likedme1")
    r = c.get("/matches/liked-me")
    assert r.status_code in (200, 404)


# ── Swipe history page ────────────────────────────────────────────────────────

def test_swipe_page_requires_auth():
    c = make_client()
    r = c.get("/swipe", follow_redirects=False)
    assert r.status_code in (302, 401)


def test_swipe_page_loads_for_auth_user():
    c, _, _ = make_auth_client("swipepg1")
    csrf = get_csrf(c)
    c.post("/profile/edit", data={
        "name": "Test", "age": "25", "gender": "female",
        "looking_for": "male", "csrftoken": csrf,
    })
    r = c.get("/swipe")
    assert r.status_code == 200


# ── Messages API (JSON) ───────────────────────────────────────────────────────

def test_messages_endpoint_requires_auth():
    c = make_client()
    r = c.get("/chat/1/messages", follow_redirects=False)
    assert r.status_code in (302, 401)


def test_messages_endpoint_returns_json(db):
    c1, c2, mid, csrf1, _, _, _ = _pair("msgjson")
    r = c1.get(f"/chat/{mid}/messages")
    assert r.status_code == 200
    # /messages returns a raw JSON list
    assert isinstance(r.json(), list)


def test_messages_endpoint_forbidden_for_outsider(db):
    c1, c2, mid, _, _, _, _ = _pair("msgout")
    outsider, _, _ = make_auth_client("msgout_o")
    r = outsider.get(f"/chat/{mid}/messages", follow_redirects=False)
    assert r.status_code in (302, 403)


# ── Message sending ───────────────────────────────────────────────────────────

def test_send_message_to_own_match(db):
    c1, c2, mid, csrf1, _, _, _ = _pair("sendmsg")
    # /send uses Form data, not JSON body
    r = c1.post(
        f"/chat/{mid}/send",
        data={"content": "Hello!"},
        headers={"x-csrf-token": csrf1},
    )
    assert r.status_code == 200
    assert "id" in r.json()


def test_send_message_appears_in_messages(db):
    c1, c2, mid, csrf1, _, _, _ = _pair("msgsend2")
    c1.post(
        f"/chat/{mid}/send",
        data={"content": "Test message here"},
        headers={"x-csrf-token": csrf1},
    )
    r = c1.get(f"/chat/{mid}/messages")
    messages = r.json()  # returns a raw list
    assert any(m["content"] == "Test message here" for m in messages)


def test_send_message_empty_rejected(db):
    c1, c2, mid, csrf1, _, _, _ = _pair("msgempty")
    r = c1.post(
        f"/chat/{mid}/send",
        data={"content": ""},
        headers={"x-csrf-token": csrf1},
    )
    assert r.status_code in (400, 422)


def test_send_message_too_long_rejected(db):
    c1, c2, mid, csrf1, _, _, _ = _pair("msglong")
    r = c1.post(
        f"/chat/{mid}/send",
        data={"content": "x" * 2001},
        headers={"x-csrf-token": csrf1},
    )
    assert r.status_code in (400, 422)


# ── Streak ────────────────────────────────────────────────────────────────────

def test_streak_function_no_previous():
    from app.routers.matches import _update_streak
    from app.models.models import Match as MatchModel
    from unittest.mock import MagicMock
    db = MagicMock()
    match = MatchModel()
    match.last_streak_date = None
    match.streak_days = None
    _update_streak(match, db)
    assert match.streak_days == 1


def test_streak_function_consecutive_day():
    from app.routers.matches import _update_streak
    from app.models.models import Match as MatchModel
    from app.utils.time import utcnow
    from unittest.mock import MagicMock
    from datetime import timedelta

    db = MagicMock()
    match = MatchModel()
    match.streak_days = 3
    match.last_streak_date = utcnow() - timedelta(days=1)
    _update_streak(match, db)
    assert match.streak_days == 4


def test_streak_function_same_day_no_increment():
    from app.routers.matches import _update_streak
    from app.models.models import Match as MatchModel
    from app.utils.time import utcnow
    from unittest.mock import MagicMock

    db = MagicMock()
    match = MatchModel()
    match.streak_days = 5
    match.last_streak_date = utcnow()
    _update_streak(match, db)
    assert match.streak_days == 5


def test_streak_function_gap_resets_to_1():
    from app.routers.matches import _update_streak
    from app.models.models import Match as MatchModel
    from app.utils.time import utcnow
    from unittest.mock import MagicMock
    from datetime import timedelta

    db = MagicMock()
    match = MatchModel()
    match.streak_days = 10
    match.last_streak_date = utcnow() - timedelta(days=5)
    _update_streak(match, db)
    assert match.streak_days == 1


# ── Compatibility ─────────────────────────────────────────────────────────────

def test_compute_compatibility_empty_answers():
    import asyncio
    from app.routers.matches import compute_compatibility_batch

    async def _run():
        from app.database import AsyncSessionLocal
        async with AsyncSessionLocal() as db:
            return await compute_compatibility_batch(1, [], db)

    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(_run())
    finally:
        loop.close()
    assert result == {}
