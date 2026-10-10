"""Verify HTTP security headers are present on every response."""
from tests.conftest import make_client, make_auth_client
import secrets


_REQUIRED_HEADERS = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "strict-origin-when-cross-origin",
}

_REQUIRED_PRESENT = [
    "content-security-policy",
    "permissions-policy",
    "cross-origin-opener-policy",
]


def _check(response):
    headers = {k.lower(): v for k, v in response.headers.items()}
    for header, expected_value in _REQUIRED_HEADERS.items():
        assert header in headers, f"Missing header: {header}"
        assert headers[header] == expected_value, (
            f"{header}: expected '{expected_value}', got '{headers[header]}'"
        )
    for header in _REQUIRED_PRESENT:
        assert header in headers, f"Missing header: {header}"


def test_security_headers_on_homepage():
    r = make_client().get("/")
    _check(r)


def test_security_headers_on_login_page():
    r = make_client().get("/login")
    _check(r)


def test_security_headers_on_authenticated_page():
    client, _, _ = make_auth_client(f"sec_{secrets.token_hex(4)}")
    r = client.get("/swipe")
    _check(r)


def test_csp_blocks_framing():
    r = make_client().get("/")
    csp = r.headers.get("content-security-policy", "")
    assert "frame-ancestors 'none'" in csp


def test_csp_allows_no_third_party_scripts():
    r = make_client().get("/")
    csp = r.headers.get("content-security-policy", "")
    assert "script-src 'self' 'unsafe-inline';" in csp


def test_pages_use_local_stylesheet():
    r = make_client().get("/welcome")
    assert "cdn.tailwindcss.com" not in r.text
    assert "/static/css/app.css?v=" in r.text


def test_permissions_policy_disables_sensors():
    r = make_client().get("/")
    pp = r.headers.get("permissions-policy", "")
    assert "geolocation=(self)" in pp
    assert "microphone=()" in pp
    assert "camera=()" in pp


def test_no_hsts_without_railway_env(monkeypatch):
    """HSTS must not be set in local dev (no RAILWAY_ENVIRONMENT)."""
    import os
    monkeypatch.delenv("RAILWAY_ENVIRONMENT", raising=False)
    r = make_client().get("/")
    assert "strict-transport-security" not in {k.lower() for k in r.headers}


def test_versioned_static_is_cached_long():
    r = make_client().get("/static/css/app.css?v=abc")
    assert r.status_code == 200
    assert "immutable" in r.headers.get("cache-control", "")
