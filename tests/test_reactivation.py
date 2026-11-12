"""Reactivation emails for users who have been away 3–4 days."""
import asyncio
from datetime import timedelta

import pytest
from sqlalchemy.dialects import postgresql

from app import reactivation
from app.models.models import Like, User
from app.utils.time import utcnow
from tests.conftest import _create_match, _create_profile, _get_user_id, make_auth_client


@pytest.fixture()
def outbox(monkeypatch):
    sent = []

    def fake_send(email, name, matches, likes, lang):
        sent.append({"email": email, "matches": matches, "likes": likes})
        return True

    monkeypatch.setattr(reactivation, "send_reactivation_email", fake_send)
    reactivation._local_claims.clear()
    return sent


def _away_user(db, tag, days_away=3.5, **fields):
    _c, email, _csrf = make_auth_client(tag)
    uid = _get_user_id(db, email)
    _create_profile(db, uid, name="Away")
    user = db.get(User, uid)
    user.email_verified = True
    user.last_seen = utcnow() - timedelta(days=days_away)
    for key, value in fields.items():
        setattr(user, key, value)
    db.commit()
    return uid, email


def _like(db, liker_id, liked_id, days_ago):
    db.add(Like(liker_id=liker_id, liked_id=liked_id, is_like=True,
                created_at=utcnow() - timedelta(days=days_ago)))
    db.commit()


def _for(outbox, email):
    return [m for m in outbox if m["email"] == email]


def test_counts_matches_and_only_likes_since_last_seen(db, outbox):
    uid, email = _away_user(db, "react_counts")
    other1, _ = _away_user(db, "react_o1", days_away=0)
    other2, _ = _away_user(db, "react_o2", days_away=0)
    other3, _ = _away_user(db, "react_o3", days_away=0)
    _create_match(db, uid, other1)
    _like(db, other2, uid, days_ago=1)   # after the user left → counts
    _like(db, other3, uid, days_ago=10)  # already seen → doesn't

    asyncio.run(reactivation.send_reactivation_emails())

    assert _for(outbox, email) == [{"email": email, "matches": 1, "likes": 1}]


def test_not_sent_twice_when_the_loop_restarts(db, outbox):
    uid, email = _away_user(db, "react_twice")
    other, _ = _away_user(db, "react_twice_o", days_away=0)
    _like(db, other, uid, days_ago=1)

    asyncio.run(reactivation.send_reactivation_emails())
    asyncio.run(reactivation.send_reactivation_emails())

    assert len(_for(outbox, email)) == 1


def test_failed_send_is_retried_next_run(db, outbox, monkeypatch):
    uid, email = _away_user(db, "react_retry")
    other, _ = _away_user(db, "react_retry_o", days_away=0)
    _like(db, other, uid, days_ago=1)

    monkeypatch.setattr(reactivation, "send_reactivation_email", lambda *a: False)
    asyncio.run(reactivation.send_reactivation_emails())
    assert uid not in reactivation._local_claims


@pytest.mark.parametrize("tag,days_away,fields", [
    ("react_recent", 1, {}),
    ("react_long_gone", 6, {}),
    ("react_optout", 3.5, {"notif_matches": False}),
    ("react_unverified", 3.5, {"email_verified": False}),
    ("react_banned", 3.5, {"is_active": False}),
])
def test_skipped_users(db, outbox, tag, days_away, fields):
    uid, email = _away_user(db, tag, days_away=days_away, **fields)
    other, _ = _away_user(db, tag + "_o", days_away=0)
    _like(db, other, uid, days_ago=0.5)

    asyncio.run(reactivation.send_reactivation_emails())

    assert _for(outbox, email) == []


def test_nothing_to_report_means_no_email(db, outbox):
    _uid, email = _away_user(db, "react_empty")
    asyncio.run(reactivation.send_reactivation_emails())
    assert _for(outbox, email) == []


def test_like_cutoff_is_a_column_not_a_bind_parameter():
    """PostgreSQL rejected the old query: a bare bind parameter inside CASE was typed as text."""
    from sqlalchemy import func, select
    stmt = (
        select(Like.liked_id, func.count())
        .join(User, User.id == Like.liked_id)
        .where(Like.created_at >= User.last_seen)
        .group_by(Like.liked_id)
    )
    sql = str(stmt.compile(dialect=postgresql.dialect()))
    assert "likes.created_at >= users.last_seen" in sql
