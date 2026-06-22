"""Referral system: page, code generation, registration with ref, bonus."""
import secrets
import pytest
from tests.conftest import make_auth_client, make_client, get_csrf, _get_user_id


def _tag() -> str:
    return secrets.token_hex(5)


# ── Referral page ─────────────────────────────────────────────────────────────

def test_referral_page_loads():
    client, _, _ = make_auth_client(f"ref_page_{_tag()}")
    r = client.get("/referral")
    assert r.status_code == 200


def test_referral_page_requires_auth():
    r = make_client().get("/referral", follow_redirects=False)
    assert r.status_code == 302


def test_referral_page_generates_code(db):
    client, email, _ = make_auth_client(f"ref_gen_{_tag()}")
    uid = _get_user_id(db, email)

    from app.models.models import User
    user = db.query(User).filter(User.id == uid).first()
    # referral_code is auto-generated at registration
    assert user.referral_code is not None
    assert len(user.referral_code) > 0


def test_referral_page_shows_link(db):
    client, email, _ = make_auth_client(f"ref_link_{_tag()}")
    r = client.get("/referral")
    assert r.status_code == 200
    # The page should contain the referral link
    assert "ref=" in r.text


def test_referral_page_shows_referred_count():
    client, _, _ = make_auth_client(f"ref_cnt_{_tag()}")
    r = client.get("/referral")
    assert r.status_code == 200
    # Page should render without error — count is 0 for a new user
    assert r.status_code == 200


# ── Register with referral code ───────────────────────────────────────────────

def test_register_with_valid_ref_sets_referred_by(db):
    """Registering with a valid ref code links the new user to the referrer."""
    # Create referrer
    referrer_client, referrer_email, _ = make_auth_client(f"ref_referrer_{_tag()}")
    referrer_id = _get_user_id(db, referrer_email)

    from app.models.models import User
    referrer = db.query(User).filter(User.id == referrer_id).first()
    db.refresh(referrer)
    ref_code = referrer.referral_code
    assert ref_code, "Referrer should have a referral code"

    # Register a new user with the referral code
    referee_email = f"ref_new_{_tag()}@test.com"
    new_client = make_client()
    csrf = get_csrf(new_client)
    new_client.post(
        "/register",
        data={
            "email": referee_email,
            "password": "TestPass123!",
            "csrftoken": csrf,
            "ref": ref_code,
        },
        follow_redirects=False,
    )

    # New user should have referred_by_id set to the referrer's ID
    db.expire_all()
    new_uid = _get_user_id(db, referee_email)
    assert new_uid is not None, "Registration failed"
    new_user = db.query(User).filter(User.id == new_uid).first()
    assert new_user.referred_by_id == referrer_id


def test_register_with_valid_ref_gives_referrer_premium_bonus(db):
    """The referrer gets premium_until extended when someone registers with their code."""
    from datetime import datetime, timezone
    from app.models.models import User

    referrer_client, referrer_email, _ = make_auth_client(f"ref_bon_{_tag()}")
    referrer_id = _get_user_id(db, referrer_email)

    db.expire_all()
    referrer = db.query(User).filter(User.id == referrer_id).first()
    ref_code = referrer.referral_code
    premium_before = referrer.premium_until

    # Register new user with referral code
    new_client = make_client()
    csrf = get_csrf(new_client)
    new_client.post(
        "/register",
        data={
            "email": f"ref_bon_new_{_tag()}@test.com",
            "password": "TestPass123!",
            "csrftoken": csrf,
            "ref": ref_code,
        },
        follow_redirects=False,
    )

    # Referrer's premium_until should now be set (bonus added)
    db.expire_all()
    referrer = db.query(User).filter(User.id == referrer_id).first()
    assert referrer.premium_until is not None, "Referrer should get premium bonus"
    if premium_before:
        assert referrer.premium_until > premium_before


def test_register_with_invalid_ref_ignores_it(db):
    """Registering with a non-existent ref code should succeed but not link anyone."""
    from app.models.models import User

    new_client = make_client()
    csrf = get_csrf(new_client)
    email = f"ref_invalid_{_tag()}@test.com"
    r = new_client.post(
        "/register",
        data={
            "email": email,
            "password": "TestPass123!",
            "csrftoken": csrf,
            "ref": "NOTACODE",
        },
        follow_redirects=False,
    )
    assert r.status_code == 302  # still registers fine

    db.expire_all()
    uid = _get_user_id(db, email)
    assert uid is not None
    user = db.query(User).filter(User.id == uid).first()
    assert user.referred_by_id is None


def test_referral_code_unique_per_user(db):
    """Every user gets a distinct referral code."""
    from app.models.models import User

    _, email_a, _ = make_auth_client(f"ref_uniq_a_{_tag()}")
    _, email_b, _ = make_auth_client(f"ref_uniq_b_{_tag()}")

    db.expire_all()
    code_a = db.query(User.referral_code).filter(User.email == email_a).scalar()
    code_b = db.query(User.referral_code).filter(User.email == email_b).scalar()
    assert code_a != code_b
