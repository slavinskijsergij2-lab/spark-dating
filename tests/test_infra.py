"""Tests for infrastructure: request ID, health, security.txt, sitemap, 404."""
from tests.conftest import make_client


def _c():
    return make_client()


# ── Request ID ────────────────────────────────────────────────────────────────

def test_request_id_header_present():
    r = _c().get("/health")
    assert "x-request-id" in r.headers


def test_request_id_echoed_back():
    rid = "test-request-id-12345"
    r = _c().get("/health", headers={"X-Request-ID": rid})
    assert r.headers.get("x-request-id") == rid


def test_request_id_generated_when_absent():
    r = _c().get("/health")
    rid = r.headers.get("x-request-id", "")
    assert len(rid) > 0


# ── Health check ──────────────────────────────────────────────────────────────

def test_health_returns_200():
    r = _c().get("/health")
    assert r.status_code == 200


def test_health_has_db_field():
    r = _c().get("/health")
    assert "db" in r.json()


def test_health_has_status_field():
    r = _c().get("/health")
    assert "status" in r.json()


def test_health_db_true_in_tests():
    r = _c().get("/health")
    assert r.json()["db"] is True


# ── 404 page ──────────────────────────────────────────────────────────────────

def test_404_json_for_api():
    r = _c().get("/api/nonexistent-endpoint-xyz")
    assert r.status_code == 404


def test_404_returns_404_status():
    r = _c().get("/this-page-does-not-exist-xyz")
    assert r.status_code == 404


# ── security.txt ─────────────────────────────────────────────────────────────

def test_security_txt_accessible():
    r = _c().get("/security.txt")
    assert r.status_code == 200


def test_security_txt_has_contact():
    r = _c().get("/security.txt")
    assert "Contact:" in r.text


def test_well_known_security_txt():
    r = _c().get("/.well-known/security.txt")
    assert r.status_code == 200
    assert "Contact:" in r.text


# ── sitemap.xml ───────────────────────────────────────────────────────────────

def test_sitemap_returns_xml():
    r = _c().get("/sitemap.xml")
    assert r.status_code == 200
    assert "application/xml" in r.headers.get("content-type", "")


def test_sitemap_has_urls():
    r = _c().get("/sitemap.xml")
    assert "<url>" in r.text
    assert "/login" in r.text
    assert "/register" in r.text


# ── robots.txt ────────────────────────────────────────────────────────────────

def test_robots_txt_accessible():
    r = _c().get("/robots.txt")
    assert r.status_code == 200


def test_robots_txt_disallows_admin():
    r = _c().get("/robots.txt")
    assert "Disallow: /admin/" in r.text


def test_robots_txt_disallows_metrics():
    r = _c().get("/robots.txt")
    assert "Disallow: /metrics" in r.text
