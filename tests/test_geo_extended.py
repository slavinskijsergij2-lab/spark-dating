"""Extended geo tests: name_simple column, umlaut-free search, bundeslaender with data."""
import pytest
from tests.conftest import make_client, SessionLocal


def _add_location(**kwargs):
    from app.models.geo import GermanLocation
    defaults = dict(
        bundesland="Bayern", lat=48.0, lon=11.0, population=0,
        name_ascii=None, name_simple=None,
    )
    defaults.update(kwargs)
    db = SessionLocal()
    try:
        loc = GermanLocation(**defaults)
        db.add(loc)
        db.commit()
        db.refresh(loc)
        return loc.id
    finally:
        db.close()


def _del_location(loc_id):
    from app.models.geo import GermanLocation
    db = SessionLocal()
    try:
        db.query(GermanLocation).filter(GermanLocation.id == loc_id).delete()
        db.commit()
    finally:
        db.close()


# ── name_simple column ────────────────────────────────────────────────────────

def test_autocomplete_finds_via_name_simple():
    """'Koln' (simple transliteration) finds Köln which has name_simple='koln'."""
    lid = _add_location(name="Köln", name_ascii="Koeln", name_simple="koln",
                        bundesland="Nordrhein-Westfalen", population=1000000,
                        lat=50.93, lon=6.96)
    try:
        c = make_client()
        r = c.get("/geo/autocomplete?q=Koln")
        assert r.status_code == 200
        results = r.json()
        assert any(item["name"] == "Köln" for item in results), f"Köln not in {results}"
    finally:
        _del_location(lid)


def test_autocomplete_finds_munchen_via_simple():
    """'Munchen' finds München which has name_simple='munchen'."""
    lid = _add_location(name="München", name_ascii="Muenchen", name_simple="munchen",
                        bundesland="Bayern", population=1500000,
                        lat=48.13, lon=11.58)
    try:
        c = make_client()
        r = c.get("/geo/autocomplete?q=Munchen")
        assert r.status_code == 200
        results = r.json()
        assert any(item["name"] == "München" for item in results), f"München not in {results}"
    finally:
        _del_location(lid)


def test_autocomplete_finds_nurnberg_via_simple():
    """'Nurnberg' finds Nürnberg which has name_simple='nurnberg'."""
    lid = _add_location(name="Nürnberg", name_ascii="Nuremberg", name_simple="nurnberg",
                        bundesland="Bayern", population=500000,
                        lat=49.45, lon=11.07)
    try:
        c = make_client()
        r = c.get("/geo/autocomplete?q=Nurnberg")
        assert r.status_code == 200
        results = r.json()
        assert any(item["name"] == "Nürnberg" for item in results), f"Nürnberg not in {results}"
    finally:
        _del_location(lid)


def test_autocomplete_finds_dusseldorf_via_simple():
    """'Dusseldorf' finds Düsseldorf (ü→u)."""
    lid = _add_location(name="Düsseldorf", name_ascii="Duesseldorf", name_simple="dusseldorf",
                        bundesland="Nordrhein-Westfalen", population=600000,
                        lat=51.22, lon=6.77)
    try:
        c = make_client()
        r = c.get("/geo/autocomplete?q=Dusseldorf")
        assert r.status_code == 200
        results = r.json()
        assert any("sseldorf" in item["name"] for item in results), f"Düsseldorf not in {results}"
    finally:
        _del_location(lid)


def test_autocomplete_still_finds_by_name_with_umlaut():
    """Direct umlaut search still works when name_simple is set."""
    lid = _add_location(name="München", name_ascii="Muenchen", name_simple="munchen",
                        bundesland="Bayern", population=1500000,
                        lat=48.13, lon=11.58)
    try:
        c = make_client()
        r = c.get("/geo/autocomplete?q=München")
        assert r.status_code == 200
        assert any(item["name"] == "München" for item in r.json())
    finally:
        _del_location(lid)


def test_autocomplete_still_finds_by_ascii():
    """GeoNames-style ASCII (oe/ue/ae) search still works."""
    lid = _add_location(name="Köln", name_ascii="Koeln", name_simple="koln",
                        bundesland="Nordrhein-Westfalen", population=1000000,
                        lat=50.93, lon=6.96)
    try:
        c = make_client()
        r = c.get("/geo/autocomplete?q=Koeln")
        assert r.status_code == 200
        assert any(item["name"] == "Köln" for item in r.json())
    finally:
        _del_location(lid)


def test_autocomplete_sorted_by_population_desc():
    """Higher population cities appear first."""
    lid_small = _add_location(name="Testdorf", name_simple="testdorf",
                              bundesland="Hessen", population=100, lat=50.0, lon=8.0)
    lid_big = _add_location(name="Teststadt", name_simple="teststadt",
                            bundesland="Hessen", population=500000, lat=50.1, lon=8.1)
    try:
        c = make_client()
        r = c.get("/geo/autocomplete?q=Test")
        results = r.json()
        names = [item["name"] for item in results]
        # Teststadt (500k) should appear before Testdorf (100)
        if "Teststadt" in names and "Testdorf" in names:
            assert names.index("Teststadt") < names.index("Testdorf")
    finally:
        _del_location(lid_small)
        _del_location(lid_big)


def test_autocomplete_returns_at_most_8():
    """Limit of 8 results per query."""
    lids = []
    for i in range(10):
        lids.append(_add_location(
            name=f"Alphastadt{i}", name_simple=f"alphastadt{i}",
            bundesland="Bayern", population=i * 1000, lat=48.0 + i * 0.01, lon=11.0,
        ))
    try:
        c = make_client()
        r = c.get("/geo/autocomplete?q=Alphastadt")
        assert len(r.json()) <= 8
    finally:
        for lid in lids:
            _del_location(lid)


def test_autocomplete_result_has_required_fields():
    """Each result has id, name, bundesland, lat, lon."""
    lid = _add_location(name="Feldkirch", name_simple="feldkirch",
                        bundesland="Bayern", population=10000, lat=47.5, lon=9.7)
    try:
        c = make_client()
        r = c.get("/geo/autocomplete?q=Feldkirch")
        results = r.json()
        assert len(results) > 0
        item = results[0]
        for field in ["id", "name", "bundesland", "lat", "lon"]:
            assert field in item, f"Missing field: {field}"
    finally:
        _del_location(lid)


def test_autocomplete_case_insensitive():
    """Search is case-insensitive."""
    lid = _add_location(name="Hamburg", name_simple="hamburg",
                        bundesland="Hamburg", population=1800000, lat=53.55, lon=10.0)
    try:
        c = make_client()
        r_lower = c.get("/geo/autocomplete?q=hamburg")
        r_upper = c.get("/geo/autocomplete?q=HAMBURG")
        assert len(r_lower.json()) > 0
        assert len(r_upper.json()) > 0
    finally:
        _del_location(lid)


def test_autocomplete_empty_result_for_no_match():
    c = make_client()
    r = c.get("/geo/autocomplete?q=ZZZNOMATCH99")
    assert r.status_code == 200
    assert r.json() == []


# ── bundeslaender endpoint ────────────────────────────────────────────────────

def test_bundeslaender_with_data():
    lid = _add_location(name="Musterstadt", name_simple="musterstadt",
                        bundesland="Sachsen", population=50000, lat=51.0, lon=13.0)
    try:
        c = make_client()
        r = c.get("/geo/bundeslaender")
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, list)
        assert "Sachsen" in data
    finally:
        _del_location(lid)


def test_bundeslaender_sorted_alphabetically():
    lids = []
    for land in ["Thüringen", "Bayern", "Berlin"]:
        lids.append(_add_location(
            name=f"Stadt_{land}", name_simple=f"stadt_{land.lower()}",
            bundesland=land, population=10000, lat=50.0, lon=10.0,
        ))
    try:
        c = make_client()
        r = c.get("/geo/bundeslaender")
        data = r.json()
        # Check sorted order for our inserted Bundesländer
        lands_in_result = [d for d in data if d in ["Thüringen", "Bayern", "Berlin"]]
        assert lands_in_result == sorted(lands_in_result)
    finally:
        for lid in lids:
            _del_location(lid)


def test_bundeslaender_no_duplicates():
    """Even with multiple cities in same Bundesland, each appears once."""
    lids = []
    for i in range(3):
        lids.append(_add_location(
            name=f"UniqueCity{i}", name_simple=f"uniquecity{i}",
            bundesland="Mecklenburg-Vorpommern", population=i * 1000,
            lat=53.0 + i, lon=11.0,
        ))
    try:
        c = make_client()
        r = c.get("/geo/bundeslaender")
        data = r.json()
        assert data.count("Mecklenburg-Vorpommern") == 1
    finally:
        for lid in lids:
            _del_location(lid)


# ── name_simple model field ───────────────────────────────────────────────────

def test_german_location_has_name_simple_field():
    from app.models.geo import GermanLocation
    loc = GermanLocation(
        name="Köln", name_ascii="Koeln", name_simple="koln",
        bundesland="NRW", lat=50.9, lon=6.9,
    )
    assert loc.name_simple == "koln"


def test_name_simple_defaults_to_none_without_value():
    from app.models.geo import GermanLocation
    loc = GermanLocation(name="Test", bundesland="Bayern", lat=48.0, lon=11.0)
    assert loc.name_simple is None


# ── Haversine edge cases ──────────────────────────────────────────────────────

def test_haversine_antipodal_points():
    from app.utils.geo import haversine_km
    # Antipodal points ~20015 km apart (half of Earth's circumference)
    d = haversine_km(0, 0, 0, 180)
    assert 19900 < d < 20200


def test_haversine_across_equator():
    from app.utils.geo import haversine_km
    # 1° latitude ≈ 111 km
    d = haversine_km(0.0, 0.0, 1.0, 0.0)
    assert 100 < d < 120


def test_bounding_box_large_radius():
    from app.utils.geo import bounding_box
    lat_min, lat_max, lon_min, lon_max = bounding_box(52.52, 13.41, 500.0)
    assert lat_min < 52.52 < lat_max
    assert lon_min < 13.41 < lon_max
    # 500 km radius ~ 4.5° latitude delta
    assert lat_max - lat_min > 5


def test_bounding_box_near_poles():
    """Should not crash for high latitudes."""
    from app.utils.geo import bounding_box
    lat_min, lat_max, lon_min, lon_max = bounding_box(89.0, 0.0, 50.0)
    assert lat_min < 89.0 <= lat_max
