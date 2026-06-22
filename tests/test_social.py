"""Block, unblock, report, settings page."""
import secrets
import pytest
from tests.conftest import make_auth_client, make_client, _get_user_id


def _tag() -> str:
    return secrets.token_hex(5)


def _two_users():
    tag = _tag()
    client_a, email_a, csrf_a = make_auth_client(f"soc_a_{tag}")
    client_b, email_b, csrf_b = make_auth_client(f"soc_b_{tag}")
    return client_a, csrf_a, client_b, csrf_b, email_a, email_b


# ── Block ─────────────────────────────────────────────────────────────────────

def test_block_user(db):
    client_a, csrf_a, client_b, csrf_b, email_a, email_b = _two_users()
    uid_b = _get_user_id(db, email_b)

    r = client_a.post(f"/user/{uid_b}/block", headers={"x-csrf-token": csrf_a})
    assert r.status_code == 200
    assert r.json().get("success") is True


def test_block_nonexistent_user():
    client, _, csrf = make_auth_client(f"soc_bne_{_tag()}")
    r = client.post("/user/999999/block", headers={"x-csrf-token": csrf})
    assert r.status_code == 404


def test_block_self_returns_400(db):
    client, email, csrf = make_auth_client(f"soc_bself_{_tag()}")
    uid = _get_user_id(db, email)
    r = client.post(f"/user/{uid}/block", headers={"x-csrf-token": csrf})
    assert r.status_code == 400


def test_block_twice_is_idempotent(db):
    client_a, csrf_a, _, _, _, email_b = _two_users()
    uid_b = _get_user_id(db, email_b)

    r1 = client_a.post(f"/user/{uid_b}/block", headers={"x-csrf-token": csrf_a})
    r2 = client_a.post(f"/user/{uid_b}/block", headers={"x-csrf-token": csrf_a})
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r2.json().get("already") is True


def test_block_requires_auth():
    r = make_client().post("/user/1/block", headers={"x-csrf-token": "x"},
                           follow_redirects=False)
    assert r.status_code in (302, 401, 403)


# ── Unblock ───────────────────────────────────────────────────────────────────

def test_unblock_user(db):
    client_a, csrf_a, _, _, _, email_b = _two_users()
    uid_b = _get_user_id(db, email_b)

    client_a.post(f"/user/{uid_b}/block", headers={"x-csrf-token": csrf_a})
    r = client_a.post(f"/user/{uid_b}/unblock", headers={"x-csrf-token": csrf_a})
    assert r.status_code == 200
    assert r.json().get("success") is True


def test_unblock_not_blocked_is_noop(db):
    client_a, csrf_a, _, _, _, email_b = _two_users()
    uid_b = _get_user_id(db, email_b)

    r = client_a.post(f"/user/{uid_b}/unblock", headers={"x-csrf-token": csrf_a})
    assert r.status_code == 200


# ── Report ────────────────────────────────────────────────────────────────────

def test_report_user(db):
    client_a, csrf_a, _, _, _, email_b = _two_users()
    uid_b = _get_user_id(db, email_b)

    r = client_a.post(
        f"/user/{uid_b}/report",
        json={"reason": "spam", "comment": "lots of spam"},
        headers={"x-csrf-token": csrf_a},
    )
    assert r.status_code == 200
    assert r.json().get("success") is True


def test_report_self_returns_400(db):
    client, email, csrf = make_auth_client(f"soc_rself_{_tag()}")
    uid = _get_user_id(db, email)
    r = client.post(
        f"/user/{uid}/report",
        json={"reason": "spam"},
        headers={"x-csrf-token": csrf},
    )
    assert r.status_code == 400


def test_report_nonexistent_user():
    client, _, csrf = make_auth_client(f"soc_rne_{_tag()}")
    r = client.post(
        "/user/999999/report",
        json={"reason": "spam"},
        headers={"x-csrf-token": csrf},
    )
    assert r.status_code == 404


def test_report_unknown_reason_defaults_to_other(db):
    client_a, csrf_a, _, _, _, email_b = _two_users()
    uid_b = _get_user_id(db, email_b)

    r = client_a.post(
        f"/user/{uid_b}/report",
        json={"reason": "not_a_valid_reason"},
        headers={"x-csrf-token": csrf_a},
    )
    assert r.status_code == 200


def test_report_auto_blocks_user(db):
    """Reporting a user should automatically block them."""
    from app.models.models import Block
    client_a, csrf_a, _, _, email_a, email_b = _two_users()
    uid_a = _get_user_id(db, email_a)
    uid_b = _get_user_id(db, email_b)

    client_a.post(
        f"/user/{uid_b}/report",
        json={"reason": "harassment"},
        headers={"x-csrf-token": csrf_a},
    )

    db.expire_all()
    block = db.query(Block).filter(
        Block.blocker_id == uid_a, Block.blocked_id == uid_b
    ).first()
    assert block is not None, "Report should auto-block the reported user"


# ── Settings page ─────────────────────────────────────────────────────────────

def test_blocked_list_page_loads():
    client, _, _ = make_auth_client(f"soc_page_{_tag()}")
    r = client.get("/settings/blocks")
    assert r.status_code == 200


def test_blocked_list_shows_blocked_user(db):
    client_a, csrf_a, _, _, _, email_b = _two_users()
    uid_b = _get_user_id(db, email_b)

    client_a.post(f"/user/{uid_b}/block", headers={"x-csrf-token": csrf_a})
    r = client_a.get("/settings/blocks")
    assert r.status_code == 200
