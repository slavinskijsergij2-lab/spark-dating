"""Tests for Germany geolocation system: /geo/autocomplete, geo swipe search."""
import secrets

from tests.conftest import SessionLocal, make_client, register, login, get_csrf


# ── /geo/autocomplete ─────────────────────────────────────────────────────────

def test_autocomplete_requires_min_2_chars():
    c = make_client()
    r = c.get("/geo/autocomplete?q=B")
    assert r.status_code == 422  # FastAPI validation


def test_autocomplete_returns_json_list():
    c = make_client()
    r = c.get("/geo/autocomplete?q=Be")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_autocomplete_empty_db_returns_empty_list():
    """german_locations table is empty in tests — endpoint must not crash."""
    c = make_client()
    r = c.get("/geo/autocomplete?q=Stuttgart")
    assert r.status_code == 200
    assert r.json() == []


def test_autocomplete_too_long_query_rejected():
    c = make_client()
    r = c.get("/geo/autocomplete?q=" + "A" * 51)
    assert r.status_code == 422


def test_autocomplete_with_data():
    """Insert a location, verify it appears in autocomplete results."""
    from app.models.geo import GermanLocation
    db = SessionLocal()
    try:
        loc = GermanLocation(
            name="Teststadt",
            name_ascii="Teststadt",
            bundesland="Bayern",
            lat=48.0,
            lon=11.0,
            population=50000,
        )
        db.add(loc)
        db.commit()
        db.refresh(loc)
        loc_id = loc.id
    finally:
        db.close()

    c = make_client()
    r = c.get("/geo/autocomplete?q=Test")
    assert r.status_code == 200
    results = r.json()
    assert any(item["name"] == "Teststadt" for item in results)
    found = next(item for item in results if item["name"] == "Teststadt")
    assert found["bundesland"] == "Bayern"
    assert found["lat"] == 48.0
    assert found["lon"] == 11.0
    assert "id" in found

    # Cleanup
    db = SessionLocal()
    try:
        db.query(GermanLocation).filter(GermanLocation.id == loc_id).delete()
        db.commit()
    finally:
        db.close()


def test_autocomplete_ascii_search():
    """Search 'Munchen' should find 'München' via name_ascii."""
    from app.models.geo import GermanLocation
    db = SessionLocal()
    try:
        loc = GermanLocation(
            name="München",
            name_ascii="Munchen",
            bundesland="Bayern",
            lat=48.1351,
            lon=11.5820,
            population=1500000,
        )
        db.add(loc)
        db.commit()
        db.refresh(loc)
        loc_id = loc.id
    finally:
        db.close()

    c = make_client()
    r = c.get("/geo/autocomplete?q=Munchen")
    assert r.status_code == 200
    assert any(item["name"] == "München" for item in r.json())

    db = SessionLocal()
    try:
        db.query(GermanLocation).filter(GermanLocation.id == loc_id).delete()
        db.commit()
    finally:
        db.close()


# ── /geo/bundeslaender ────────────────────────────────────────────────────────

def test_bundeslaender_returns_list():
    c = make_client()
    r = c.get("/geo/bundeslaender")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


# ── Haversine utility ─────────────────────────────────────────────────────────

def test_haversine_same_point_is_zero():
    from app.utils.geo import haversine_km
    assert haversine_km(52.5, 13.4, 52.5, 13.4) == 0.0


def test_haversine_berlin_to_munich():
    from app.utils.geo import haversine_km
    # Berlin (52.52, 13.41) → Munich (48.14, 11.58): ~504 km
    dist = haversine_km(52.52, 13.41, 48.14, 11.58)
    assert 490 < dist < 520


def test_haversine_symmetric():
    from app.utils.geo import haversine_km
    a = haversine_km(48.0, 11.0, 50.0, 14.0)
    b = haversine_km(50.0, 14.0, 48.0, 11.0)
    assert abs(a - b) < 0.001


def test_bounding_box_structure():
    from app.utils.geo import bounding_box
    lat_min, lat_max, lon_min, lon_max = bounding_box(48.0, 11.0, 50.0)
    assert lat_min < 48.0 < lat_max
    assert lon_min < 11.0 < lon_max


def test_bounding_box_radius_zero():
    from app.utils.geo import bounding_box
    lat_min, lat_max, lon_min, lon_max = bounding_box(48.0, 11.0, 0.0)
    assert lat_min == lat_max == 48.0
    assert lon_min == lon_max == 11.0


# ── Swipe with location_id ────────────────────────────────────────────────────

def _make_auth(suffix=""):
    tag = suffix or secrets.token_hex(4)
    email = f"geo_{tag}@test.com"
    c = make_client()
    register(c, email)
    login(c, email)
    return c, email


def test_swipe_with_unknown_location_id_returns_200():
    """location_id=999999 (not in DB) must not crash — falls back gracefully."""
    c, _ = _make_auth()
    # Need a profile first
    csrf = get_csrf(c)
    c.post("/profile/edit", data={
        "name": "GeoTester", "age": "25", "gender": "female",
        "looking_for": "male", "csrftoken": csrf,
    })
    r = c.get("/swipe?location_id=999999")
    assert r.status_code == 200


def test_swipe_location_id_param_accepted():
    """location_id=0 treated as no filter → 200."""
    c, _ = _make_auth()
    csrf = get_csrf(c)
    c.post("/profile/edit", data={
        "name": "GeoTester2", "age": "26", "gender": "female",
        "looking_for": "male", "csrftoken": csrf,
    })
    r = c.get("/swipe?location_id=0")
    assert r.status_code == 200


def test_swipe_with_real_location_id():
    """Insert a location, assign to profile, verify geo search returns candidate."""
    from app.models.geo import GermanLocation
    from app.models.models import Profile, GenderEnum

    db = SessionLocal()
    try:
        loc = GermanLocation(
            name="GeoTestCity",
            name_ascii="GeoTestCity",
            bundesland="Hessen",
            lat=50.11,
            lon=8.68,
            population=750000,
        )
        db.add(loc)
        db.commit()
        db.refresh(loc)
        loc_id = loc.id
    finally:
        db.close()

    # Register seeker
    seeker, s_email = _make_auth("seek")
    csrf = get_csrf(seeker)
    seeker.post("/profile/edit", data={
        "name": "Seeker", "age": "28", "gender": "female",
        "looking_for": "male", "csrftoken": csrf,
    })

    # Register candidate in that city
    candidate, c_email = _make_auth("cand")
    csrf2 = get_csrf(candidate)
    candidate.post("/profile/edit", data={
        "name": "Candidate", "age": "30", "gender": "male",
        "looking_for": "female", "csrftoken": csrf2,
        "location_id": str(loc_id), "geo_lat": "50.11", "geo_lon": "8.68",
    })

    # Seeker searches by location_id
    r = seeker.get(f"/swipe?location_id={loc_id}")
    assert r.status_code == 200

    # Cleanup
    db = SessionLocal()
    try:
        db.query(GermanLocation).filter(GermanLocation.id == loc_id).delete()
        db.commit()
    finally:
        db.close()


def test_profile_saves_geo_fields():
    """location_id/lat/lon saved to Profile when provided in edit form."""
    from app.models.geo import GermanLocation
    from app.models.models import Profile

    db = SessionLocal()
    try:
        loc = GermanLocation(
            name="SaveGeoCity",
            name_ascii="SaveGeoCity",
            bundesland="Berlin",
            lat=52.52,
            lon=13.41,
            population=3700000,
        )
        db.add(loc)
        db.commit()
        db.refresh(loc)
        loc_id = loc.id
    finally:
        db.close()

    c, email = _make_auth("savegeo")
    csrf = get_csrf(c)
    c.post("/profile/edit", data={
        "name": "BerlinUser", "age": "24", "gender": "male",
        "looking_for": "female", "csrftoken": csrf,
        "location_id": str(loc_id), "geo_lat": "52.52", "geo_lon": "13.41",
        "city": "Berlin",
    })

    db = SessionLocal()
    try:
        from app.models.models import User
        u = db.query(User).filter(User.email == email).first()
        p = db.query(Profile).filter(Profile.user_id == u.id).first()
        assert p is not None
        assert p.location_id == loc_id
        assert abs(p.lat - 52.52) < 0.01
        assert abs(p.lon - 13.41) < 0.01

        # Cleanup
        db.query(GermanLocation).filter(GermanLocation.id == loc_id).delete()
        db.commit()
    finally:
        db.close()


def test_profile_clears_geo_when_city_cleared():
    """Clearing city field also clears location_id/lat/lon."""
    from app.models.geo import GermanLocation
    from app.models.models import Profile, User

    db = SessionLocal()
    try:
        loc = GermanLocation(
            name="ClearCity",
            name_ascii="ClearCity",
            bundesland="Sachsen",
            lat=51.05,
            lon=13.74,
            population=600000,
        )
        db.add(loc)
        db.commit()
        db.refresh(loc)
        loc_id = loc.id
    finally:
        db.close()

    c, email = _make_auth("cleartest")
    csrf = get_csrf(c)

    # Set geo
    c.post("/profile/edit", data={
        "name": "ClearTest", "age": "27", "gender": "female",
        "csrftoken": csrf,
        "location_id": str(loc_id), "geo_lat": "51.05", "geo_lon": "13.74",
        "city": "ClearCity",
    })

    # Now clear city
    csrf2 = get_csrf(c)
    c.post("/profile/edit", data={
        "name": "ClearTest", "age": "27", "gender": "female",
        "csrftoken": csrf2,
        "city": "",  # empty → clears geo
    })

    db = SessionLocal()
    try:
        u = db.query(User).filter(User.email == email).first()
        p = db.query(Profile).filter(Profile.user_id == u.id).first()
        assert p.location_id is None
        assert p.lat is None
        assert p.lon is None

        db.query(GermanLocation).filter(GermanLocation.id == loc_id).delete()
        db.commit()
    finally:
        db.close()
