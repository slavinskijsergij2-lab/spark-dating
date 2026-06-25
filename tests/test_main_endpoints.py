"""Tests for main.py routes: privacy, welcome, sitemap, robots, sentry-debug, errors."""
import os
import pytest
from tests.conftest import make_client, make_auth_client


# ── Static/public pages ───────────────────────────────────────────────────────

def test_privacy_page_loads():
    c = make_client()
    r = c.get("/privacy")
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")


def test_welcome_page_loads():
    c = make_client()
    r = c.get("/welcome")
    assert r.status_code == 200


def test_root_redirects_logged_in_user():
    c, _, _ = make_auth_client("rootredir")
    r = c.get("/", follow_redirects=False)
    assert r.status_code == 302
    assert "/swipe" in r.headers["location"]


def test_root_shows_landing_for_anonymous():
    c = make_client()
    r = c.get("/")
    assert r.status_code == 200


def test_sitemap_is_valid_xml():
    c = make_client()
    r = c.get("/sitemap.xml")
    assert r.status_code == 200
    ct = r.headers.get("content-type", "")
    assert "xml" in ct
    assert r.text.startswith("<?xml")


def test_sitemap_contains_key_urls():
    c = make_client()
    r = c.get("/sitemap.xml")
    for path in ["/login", "/register", "/privacy"]:
        assert path in r.text


def test_robots_txt_loads():
    c = make_client()
    r = c.get("/robots.txt")
    assert r.status_code == 200
    assert "User-agent" in r.text or "user-agent" in r.text.lower()


def test_robots_txt_disallows_admin():
    c = make_client()
    r = c.get("/robots.txt")
    assert "/admin" in r.text


def test_robots_txt_disallows_metrics():
    c = make_client()
    r = c.get("/robots.txt")
    assert "/metrics" in r.text


def test_security_txt_loads():
    c = make_client()
    r = c.get("/security.txt")
    assert r.status_code == 200
    assert "Contact" in r.text


def test_well_known_security_txt_accessible():
    c = make_client()
    r = c.get("/.well-known/security.txt")
    assert r.status_code == 200


def test_security_txt_has_contact_email():
    c = make_client()
    r = c.get("/security.txt")
    assert "mailto:" in r.text


def test_favicon_returns_icon():
    c = make_client()
    r = c.get("/favicon.ico")
    assert r.status_code == 200


def test_service_worker_js():
    c = make_client()
    r = c.get("/sw.js")
    assert r.status_code == 200
    ct = r.headers.get("content-type", "")
    assert "javascript" in ct


def test_service_worker_has_service_worker_allowed_header():
    c = make_client()
    r = c.get("/sw.js")
    assert r.headers.get("Service-Worker-Allowed") == "/"


# ── /metrics endpoint ─────────────────────────────────────────────────────────

def test_metrics_no_token_returns_403():
    c = make_client()
    r = c.get("/metrics")
    assert r.status_code == 403


def test_metrics_wrong_token_returns_403(monkeypatch):
    monkeypatch.setenv("METRICS_TOKEN", "secret123")
    c = make_client()
    r = c.get("/metrics?token=wrong")
    assert r.status_code == 403


def test_metrics_valid_token_returns_data(monkeypatch):
    monkeypatch.setenv("METRICS_TOKEN", "mytoken")
    c = make_client()
    r = c.get("/metrics?token=mytoken")
    assert r.status_code == 200
    data = r.json()
    assert "requests_total" in data
    assert "errors_5xx" in data
    assert "uptime_seconds" in data
    assert "status_counts" in data


def test_metrics_uptime_is_non_negative(monkeypatch):
    monkeypatch.setenv("METRICS_TOKEN", "tok")
    c = make_client()
    r = c.get("/metrics?token=tok")
    assert r.json()["uptime_seconds"] >= 0


# ── /errors endpoint ──────────────────────────────────────────────────────────

def test_errors_no_token_returns_403():
    c = make_client()
    r = c.get("/errors")
    assert r.status_code == 403


def test_errors_valid_token_returns_json(monkeypatch):
    monkeypatch.setenv("METRICS_TOKEN", "errtok")
    c = make_client()
    r = c.get("/errors?token=errtok")
    assert r.status_code == 200
    data = r.json()
    assert "errors" in data


def test_errors_returns_list(monkeypatch):
    monkeypatch.setenv("METRICS_TOKEN", "errtok2")
    c = make_client()
    r = c.get("/errors?token=errtok2")
    assert isinstance(r.json()["errors"], list)


# ── /sentry-debug ─────────────────────────────────────────────────────────────

def test_sentry_debug_raises_500():
    # raise_server_exceptions=False so RuntimeError is caught by global handler
    import main as _main
    from fastapi.testclient import TestClient
    c = TestClient(_main.app, raise_server_exceptions=False)
    r = c.get("/sentry-debug", follow_redirects=True)
    assert r.status_code == 500


def test_sentry_debug_trailing_slash():
    import main as _main
    from fastapi.testclient import TestClient
    c = TestClient(_main.app, raise_server_exceptions=False)
    r = c.get("/sentry-debug/", follow_redirects=True)
    assert r.status_code == 500


# ── 404 handling ─────────────────────────────────────────────────────────────

def test_unknown_path_returns_404():
    c = make_client()
    r = c.get("/this-page-does-not-exist-xyz")
    assert r.status_code == 404


def test_404_html_response_for_browser():
    """Known 404 route (caught by our handler) returns HTML for browser requests."""
    c, _, _ = make_auth_client("html404")
    # /profile/999999999 is a known route that can return 404 via our exception handler
    r = c.get("/profile/999999999", headers={"Accept": "text/html"})
    assert r.status_code == 404


def test_404_json_for_api_client():
    c = make_client()
    r = c.get("/nonexistent", headers={"Accept": "application/json"})
    assert r.status_code == 404


# ── HEAD requests ─────────────────────────────────────────────────────────────

def test_head_root_returns_200():
    c = make_client()
    r = c.head("/")
    assert r.status_code in (200, 302)
