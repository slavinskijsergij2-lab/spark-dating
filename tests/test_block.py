"""Block/unblock, report, unmatch — access control and side effects."""
import secrets
import pytest
from tests.conftest import make_auth_client, make_client, _create_match, _get_user_id


def _tag() -> str:
    return secrets.token_hex(5)


def _setup_match(db):
    tag = _tag()
    client_a, email_a, csrf_a = make_auth_client(f"bl_a_{tag}")
    client_b, email_b, csrf_b = make_auth_client(f"bl_b_{tag}")
    uid_a = _get_user_id(db, email_a)
    uid_b = _get_user_id(db, email_b)
    mid = _create_match(db, uid_a, uid_b)
    return client_a, csrf_a, uid_a, client_b, csrf_b, uid_b, mid


# ── Block ─────────────────────────────────────────────────────────────────────

def test_block_user_success(db):
    client_a, csrf_a, _, _, _, uid_b, _ = _setup_match(db)
    r = client_a.post(f"/user/{uid_b}/block",
                      headers={"x-csrf-token": csrf_a})
    assert r.status_code == 200
    assert r.json().get("success") is True or r.json().get("status") == "blocked"


def test_block_persisted_in_db(db):
    from app.models.models import Block
    client_a, csrf_a, uid_a, _, _, uid_b, _ = _setup_match(db)

    client_a.post(f"/user/{uid_b}/block", headers={"x-csrf-token": csrf_a})

    db.expire_all()
    block = db.query(Block).filter(
        Block.blocker_id == uid_a, Block.blocked_id == uid_b
    ).first()
    assert block is not None


def test_blocked_user_cannot_send_message(db):
    """After A blocks B, B cannot send a message to A in their shared match."""
    client_a, csrf_a, _, client_b, csrf_b, uid_b, mid = _setup_match(db)

    client_a.post(f"/user/{uid_b}/block", headers={"x-csrf-token": csrf_a})

    r = client_b.post(f"/chat/{mid}/send",
                      data={"content": "hello?"},
                      headers={"x-csrf-token": csrf_b})
    assert r.status_code == 403


def test_blocker_also_cannot_send_after_block(db):
    """After A blocks B, A also cannot send messages to B."""
    client_a, csrf_a, _, _, _, uid_b, mid = _setup_match(db)

    client_a.post(f"/user/{uid_b}/block", headers={"x-csrf-token": csrf_a})

    r = client_a.post(f"/chat/{mid}/send",
                      data={"content": "locked out too"},
                      headers={"x-csrf-token": csrf_a})
    assert r.status_code == 403


def test_block_requires_csrf(db):
    client_a, _, _, _, _, uid_b, _ = _setup_match(db)
    r = client_a.post(f"/user/{uid_b}/block")
    assert r.status_code == 403


def test_block_requires_auth():
    r = make_client().post("/user/1/block",
                           headers={"x-csrf-token": "fake"},
                           follow_redirects=False)
    assert r.status_code in (302, 401, 403)


# ── Unblock ───────────────────────────────────────────────────────────────────

def test_unblock_removes_block(db):
    from app.models.models import Block
    client_a, csrf_a, uid_a, _, _, uid_b, _ = _setup_match(db)

    client_a.post(f"/user/{uid_b}/block", headers={"x-csrf-token": csrf_a})
    r = client_a.post(f"/user/{uid_b}/unblock", headers={"x-csrf-token": csrf_a})
    assert r.status_code == 200

    db.expire_all()
    block = db.query(Block).filter(
        Block.blocker_id == uid_a, Block.blocked_id == uid_b
    ).first()
    assert block is None


def test_unblock_restores_messaging(db):
    client_a, csrf_a, _, client_b, csrf_b, uid_b, mid = _setup_match(db)

    client_a.post(f"/user/{uid_b}/block", headers={"x-csrf-token": csrf_a})
    client_a.post(f"/user/{uid_b}/unblock", headers={"x-csrf-token": csrf_a})

    r = client_b.post(f"/chat/{mid}/send",
                      data={"content": "back to normal"},
                      headers={"x-csrf-token": csrf_b})
    assert r.status_code == 200


def test_unblock_nonexistent_block_is_safe(db):
    """Unblocking a user that wasn't blocked should not crash."""
    client_a, csrf_a, _, _, _, uid_b, _ = _setup_match(db)
    r = client_a.post(f"/user/{uid_b}/unblock",
                      headers={"x-csrf-token": csrf_a})
    assert r.status_code in (200, 404)


# ── Report ────────────────────────────────────────────────────────────────────

def test_report_user_success(db):
    client_a, csrf_a, _, _, _, uid_b, _ = _setup_match(db)
    r = client_a.post(
        f"/user/{uid_b}/report",
        json={"reason": "spam"},
        headers={"x-csrf-token": csrf_a, "content-type": "application/json"},
    )
    assert r.status_code == 200


def test_report_invalid_reason_normalized(db):
    """Invalid reason is silently normalized to 'other' (not rejected)."""
    client_a, csrf_a, _, _, _, uid_b, _ = _setup_match(db)
    r = client_a.post(
        f"/user/{uid_b}/report",
        json={"reason": "not_a_real_reason"},
        headers={"x-csrf-token": csrf_a},
    )
    # Endpoint normalizes unknown reason → 200
    assert r.status_code == 200


def test_report_requires_csrf(db):
    client_a, _, _, _, _, uid_b, _ = _setup_match(db)
    r = client_a.post(f"/user/{uid_b}/report",
                      json={"reason": "spam"})
    assert r.status_code == 403


# ── Unmatch ───────────────────────────────────────────────────────────────────

def test_unmatch_success(db):
    client_a, csrf_a, _, _, _, _, mid = _setup_match(db)
    r = client_a.post(f"/match/{mid}/unmatch",
                      headers={"x-csrf-token": csrf_a})
    assert r.status_code == 200
    assert r.json().get("success") is True


def test_unmatch_removes_match_from_db(db):
    from app.models.models import Match
    client_a, csrf_a, _, _, _, _, mid = _setup_match(db)

    client_a.post(f"/match/{mid}/unmatch", headers={"x-csrf-token": csrf_a})

    db.expire_all()
    match = db.query(Match).filter(Match.id == mid).first()
    assert match is None


def test_unmatch_removes_messages(db):
    from app.models.models import Message
    client_a, csrf_a, uid_a, client_b, csrf_b, uid_b, mid = _setup_match(db)

    client_a.post(f"/chat/{mid}/send", data={"content": "hi"},
                  headers={"x-csrf-token": csrf_a})
    client_b.post(f"/chat/{mid}/send", data={"content": "bye"},
                  headers={"x-csrf-token": csrf_b})

    client_a.post(f"/match/{mid}/unmatch", headers={"x-csrf-token": csrf_a})

    db.expire_all()
    msgs = db.query(Message).filter(Message.match_id == mid).all()
    assert len(msgs) == 0


def test_unmatch_forbidden_for_outsider(db):
    _, _, _, _, _, _, mid = _setup_match(db)
    client_c, _, csrf_c = make_auth_client(f"bl_unmatch_c_{_tag()}")

    r = client_c.post(f"/match/{mid}/unmatch",
                      headers={"x-csrf-token": csrf_c})
    assert r.status_code == 403


def test_unmatch_requires_csrf(db):
    client_a, _, _, _, _, _, mid = _setup_match(db)
    r = client_a.post(f"/match/{mid}/unmatch")
    assert r.status_code == 403
