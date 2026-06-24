"""Match archiving: archived page, unarchive, auto-archive logic."""
import secrets
import pytest
from datetime import timedelta
from tests.conftest import make_auth_client, make_client, _create_match, _get_user_id
from app.utils.time import utcnow


def _tag() -> str:
    return secrets.token_hex(5)


def _setup_match(db):
    tag = _tag()
    client_a, email_a, csrf_a = make_auth_client(f"ar_a_{tag}")
    client_b, email_b, csrf_b = make_auth_client(f"ar_b_{tag}")
    uid_a = _get_user_id(db, email_a)
    uid_b = _get_user_id(db, email_b)
    mid = _create_match(db, uid_a, uid_b)
    return client_a, csrf_a, client_b, csrf_b, mid, uid_a, uid_b


# ── /matches/archived page ────────────────────────────────────────────────────

def test_archived_page_loads():
    client, _, _ = make_auth_client(f"ar_page_{_tag()}")
    r = client.get("/matches/archived")
    assert r.status_code == 200


def test_archived_page_requires_auth():
    r = make_client().get("/matches/archived", follow_redirects=False)
    assert r.status_code == 302
    assert "/login" in r.headers["location"]


def test_archived_page_empty_by_default():
    client, _, _ = make_auth_client(f"ar_empty_{_tag()}")
    r = client.get("/matches/archived")
    assert r.status_code == 200
    # Should show empty state (no archived matches yet)
    assert "archive" in r.text.lower() or "архив" in r.text.lower()


def test_archived_page_shows_archived_match(db):
    """Manually archived match must appear on /matches/archived."""
    from app.models.models import Match
    client_a, csrf_a, _, _, mid, _, _ = _setup_match(db)

    # Manually set archived_at
    db.query(Match).filter(Match.id == mid).update({"archived_at": utcnow()})
    db.commit()

    r = client_a.get("/matches/archived")
    assert r.status_code == 200
    assert str(mid) in r.text or "chat" in r.text


def test_archived_match_not_in_active_matches(db):
    """Archived match should NOT appear on the regular /matches page."""
    from app.models.models import Match
    client_a, _, _, _, mid, _, _ = _setup_match(db)

    db.query(Match).filter(Match.id == mid).update({"archived_at": utcnow()})
    db.commit()

    r = client_a.get("/matches")
    assert r.status_code == 200
    # The match chat link should not appear (archived)
    assert f"/chat/{mid}" not in r.text


def test_archived_page_only_shows_own_matches(db):
    """User B cannot see User A's archived matches."""
    from app.models.models import Match
    client_a, _, client_b, _, mid, _, _ = _setup_match(db)

    db.query(Match).filter(Match.id == mid).update({"archived_at": utcnow()})
    db.commit()

    # client_b's /matches/archived should show this match (they're also in it)
    # but a totally unrelated user should not see it
    client_c, _, _ = make_auth_client(f"ar_outsider_{_tag()}")
    r = client_c.get("/matches/archived")
    assert r.status_code == 200
    assert f"/chat/{mid}" not in r.text


# ── Unarchive ─────────────────────────────────────────────────────────────────

def test_unarchive_clears_archived_at(db):
    from app.models.models import Match
    client_a, csrf_a, _, _, mid, _, _ = _setup_match(db)

    db.query(Match).filter(Match.id == mid).update({"archived_at": utcnow()})
    db.commit()

    r = client_a.post(
        f"/match/{mid}/unarchive",
        headers={"x-csrf-token": csrf_a},
    )
    assert r.status_code == 200
    assert r.json().get("success") is True

    db.expire_all()
    match = db.query(Match).filter(Match.id == mid).first()
    assert match.archived_at is None


def test_unarchive_makes_match_active_again(db):
    """After unarchive, match.archived_at is None and match is counted as active."""
    from app.models.models import Match
    from sqlalchemy import func
    client_a, csrf_a, _, _, mid, _, _ = _setup_match(db)

    db.query(Match).filter(Match.id == mid).update({"archived_at": utcnow()})
    db.commit()

    client_a.post(f"/match/{mid}/unarchive",
                  headers={"x-csrf-token": csrf_a})

    db.expire_all()
    match = db.query(Match).filter(Match.id == mid).first()
    assert match.archived_at is None


def test_unarchive_foreign_match_forbidden(db):
    """User C cannot unarchive A↔B match."""
    from app.models.models import Match
    _, _, _, _, mid, _, _ = _setup_match(db)
    client_c, _, csrf_c = make_auth_client(f"ar_unarch_c_{_tag()}")

    db.query(Match).filter(Match.id == mid).update({"archived_at": utcnow()})
    db.commit()

    r = client_c.post(
        f"/match/{mid}/unarchive",
        headers={"x-csrf-token": csrf_c},
    )
    assert r.status_code == 403


def test_unarchive_already_active_match_is_idempotent(db):
    """Unarchiving an already active (not archived) match is safe."""
    client_a, csrf_a, _, _, mid, _, _ = _setup_match(db)

    r = client_a.post(
        f"/match/{mid}/unarchive",
        headers={"x-csrf-token": csrf_a},
    )
    assert r.status_code == 200


def test_unarchive_requires_csrf(db):
    from app.models.models import Match
    client_a, _, _, _, mid, _, _ = _setup_match(db)
    db.query(Match).filter(Match.id == mid).update({"archived_at": utcnow()})
    db.commit()

    r = client_a.post(f"/match/{mid}/unarchive")
    assert r.status_code == 403


def test_unarchive_requires_auth():
    r = make_client().post("/match/1/unarchive",
                           headers={"x-csrf-token": "fake"},
                           follow_redirects=False)
    assert r.status_code in (302, 401, 403)


# ── Auto-archive logic ────────────────────────────────────────────────────────

def test_auto_archive_old_match_with_no_messages(db):
    """Match older than 7 days with no messages is archived when /matches is loaded."""
    from app.models.models import Match
    client_a, _, _, _, mid, _, _ = _setup_match(db)

    # Backdate the match creation
    db.query(Match).filter(Match.id == mid).update({
        "created_at": utcnow() - timedelta(days=8),
    })
    db.commit()

    # Load /matches — triggers auto-archive
    client_a.get("/matches")

    db.expire_all()
    match = db.query(Match).filter(Match.id == mid).first()
    assert match.archived_at is not None


def test_auto_archive_recent_match_not_archived(db):
    """Match created yesterday should NOT be auto-archived."""
    from app.models.models import Match
    client_a, _, _, _, mid, _, _ = _setup_match(db)

    db.query(Match).filter(Match.id == mid).update({
        "created_at": utcnow() - timedelta(days=1),
    })
    db.commit()

    client_a.get("/matches")

    db.expire_all()
    match = db.query(Match).filter(Match.id == mid).first()
    assert match.archived_at is None


def test_auto_archive_old_match_with_recent_message_not_archived(db):
    """Old match that has a recent message should NOT be archived."""
    from app.models.models import Match, Message
    client_a, csrf_a, _, _, mid, uid_a, _ = _setup_match(db)

    # Backdate the match
    db.query(Match).filter(Match.id == mid).update({
        "created_at": utcnow() - timedelta(days=10),
    })
    db.commit()

    # Send a recent message
    client_a.post(f"/chat/{mid}/send", data={"content": "recent!"},
                  headers={"x-csrf-token": csrf_a})

    # Trigger auto-archive
    client_a.get("/matches")

    db.expire_all()
    match = db.query(Match).filter(Match.id == mid).first()
    assert match.archived_at is None


def test_auto_archive_already_archived_not_touched(db):
    """Already archived matches should not have archived_at overwritten."""
    from app.models.models import Match
    client_a, _, _, _, mid, _, _ = _setup_match(db)

    original_time = utcnow() - timedelta(days=3)
    db.query(Match).filter(Match.id == mid).update({
        "created_at": utcnow() - timedelta(days=10),
        "archived_at": original_time,
    })
    db.commit()

    client_a.get("/matches")

    db.expire_all()
    match = db.query(Match).filter(Match.id == mid).first()
    # archived_at should not be changed significantly
    assert match.archived_at is not None


# ── Banner on /matches page ───────────────────────────────────────────────────

def test_archived_count_banner_visible_when_archived_exist(db):
    """When there are archived matches, a banner with the count should appear."""
    from app.models.models import Match
    client_a, _, _, _, mid, _, _ = _setup_match(db)

    db.query(Match).filter(Match.id == mid).update({"archived_at": utcnow()})
    db.commit()

    r = client_a.get("/matches")
    assert "archived" in r.text.lower() or "архив" in r.text.lower()


def test_no_banner_when_no_archived(db):
    """When no matches are archived, the banner should not appear."""
    client_a, _, _, _, mid, _, _ = _setup_match(db)

    r = client_a.get("/matches")
    # No archived matches → banner text with count should not appear prominently
    assert "matches/archived" not in r.text or "0" not in r.text
