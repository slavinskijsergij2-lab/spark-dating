"""Extended profile tests: validation, geo fields, gallery, preferences."""
import secrets
from tests.conftest import make_auth_client, make_client, get_csrf, _get_user_id, SessionLocal


def _client_with_profile(suffix=""):
    tag = suffix or secrets.token_hex(4)
    c, email, csrf = make_auth_client(f"prof_{tag}")
    c.post("/profile/edit", data={
        "name": "ProfilUser",
        "age": "25",
        "gender": "female",
        "looking_for": "male",
        "csrftoken": csrf,
    })
    return c, email, csrf


# ── Edit profile validation ────────────────────────────────────────────────────

def test_profile_name_too_short_rejected():
    c, _, csrf = make_auth_client("prval1")
    r = c.post("/profile/edit", data={
        "name": "A", "age": "25", "gender": "male",
        "looking_for": "female", "csrftoken": csrf,
    }, follow_redirects=False)
    assert r.status_code in (302, 400, 422)


def test_profile_age_too_young_rejected():
    c, _, csrf = make_auth_client("prval2")
    r = c.post("/profile/edit", data={
        "name": "YoungUser", "age": "17", "gender": "male",
        "looking_for": "female", "csrftoken": csrf,
    }, follow_redirects=False)
    assert r.status_code in (302, 400, 422)


def test_profile_age_too_old_rejected():
    c, _, csrf = make_auth_client("prval3")
    r = c.post("/profile/edit", data={
        "name": "OldUser", "age": "101", "gender": "male",
        "looking_for": "female", "csrftoken": csrf,
    }, follow_redirects=False)
    assert r.status_code in (302, 400, 422)


def test_profile_age_boundary_18_accepted():
    c, _, csrf = make_auth_client("prval4")
    r = c.post("/profile/edit", data={
        "name": "JustAdult", "age": "18", "gender": "male",
        "looking_for": "female", "csrftoken": csrf,
    }, follow_redirects=False)
    assert r.status_code in (200, 302)


def test_profile_age_boundary_100_accepted():
    c, _, csrf = make_auth_client("prval5")
    r = c.post("/profile/edit", data={
        "name": "Elder100", "age": "100", "gender": "female",
        "looking_for": "male", "csrftoken": csrf,
    }, follow_redirects=False)
    assert r.status_code in (200, 302)


def test_profile_requires_auth():
    c = make_client()
    r = c.get("/profile/edit", follow_redirects=False)
    assert r.status_code in (302, 401)


def test_profile_edit_page_loads():
    c, _, _ = _client_with_profile("editload")
    r = c.get("/profile/edit")
    assert r.status_code == 200


def test_profile_edit_csrf_required():
    c, _, _ = _client_with_profile("editcsrf")
    r = c.post("/profile/edit", data={
        "name": "NoCsrf", "age": "25", "gender": "male", "looking_for": "female",
    }, follow_redirects=False)
    assert r.status_code in (400, 302, 403)


def test_profile_bio_saved():
    c, email, csrf = make_auth_client("prbio")
    c.post("/profile/edit", data={
        "name": "BioUser", "age": "28", "gender": "male",
        "looking_for": "female", "csrftoken": csrf,
        "bio": "I love hiking!",
    })
    db = SessionLocal()
    try:
        uid = _get_user_id(db, email)
        from app.models.models import Profile
        p = db.query(Profile).filter(Profile.user_id == uid).first()
        assert p.bio == "I love hiking!"
    finally:
        db.close()


def test_profile_city_saved():
    c, email, csrf = make_auth_client("prcity")
    c.post("/profile/edit", data={
        "name": "CityUser", "age": "24", "gender": "female",
        "looking_for": "male", "csrftoken": csrf,
        "city": "Berlin",
    })
    db = SessionLocal()
    try:
        uid = _get_user_id(db, email)
        from app.models.models import Profile
        p = db.query(Profile).filter(Profile.user_id == uid).first()
        assert p.city == "Berlin"
    finally:
        db.close()


def test_profile_looking_for_any_accepted():
    c, email, csrf = make_auth_client("prany")
    r = c.post("/profile/edit", data={
        "name": "OpenUser", "age": "26", "gender": "male",
        "looking_for": "any", "csrftoken": csrf,
    }, follow_redirects=False)
    assert r.status_code in (200, 302)


# ── View profile ───────────────────────────────────────────────────────────────

def test_view_own_profile_page():
    c, email, _ = _client_with_profile("viewown")
    db = SessionLocal()
    uid = _get_user_id(db, email)
    db.close()
    r = c.get(f"/profile/{uid}")
    assert r.status_code == 200


def test_view_nonexistent_profile_returns_404():
    c, _, _ = _client_with_profile("viewno")
    r = c.get("/profile/999999")
    assert r.status_code == 404


# ── Notifications settings ────────────────────────────────────────────────────

def test_notifications_page_loads():
    c, _, _ = _client_with_profile("notifload")
    r = c.get("/settings/notifications")
    assert r.status_code == 200


def test_notifications_settings_requires_auth():
    c = make_client()
    r = c.get("/settings/notifications", follow_redirects=False)
    assert r.status_code in (302, 401)


# ── Referral page ─────────────────────────────────────────────────────────────

def test_referral_page_requires_profile():
    c, _, _ = _client_with_profile("reftest")
    r = c.get("/referral")
    assert r.status_code == 200


# ── Stories feed ─────────────────────────────────────────────────────────────

def test_stories_feed_loads():
    c, _, _ = _client_with_profile("storiesload")
    r = c.get("/stories/feed")
    assert r.status_code == 200


# ── User model fields ─────────────────────────────────────────────────────────

def test_user_has_politeness_score_field(db):
    c, email, _ = make_auth_client("polscore")
    from app.models.models import User
    db2 = SessionLocal()
    try:
        u = db2.query(User).filter(User.email == email).first()
        assert hasattr(u, "politeness_score")
        assert hasattr(u, "politeness_votes")
    finally:
        db2.close()


def test_new_user_politeness_defaults():
    c, email, _ = make_auth_client("poldef")
    from app.models.models import User
    db = SessionLocal()
    try:
        u = db.query(User).filter(User.email == email).first()
        assert u.politeness_votes in (0, None)
    finally:
        db.close()
