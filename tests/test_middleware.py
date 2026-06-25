"""Tests for HTTP middleware: body size, CSRF, request-ID, security headers."""
from tests.conftest import make_client, make_auth_client, get_csrf


# ── Max body size (12 MB) ─────────────────────────────────────────────────────

def test_body_within_limit_accepted():
    c = make_client()
    csrf = get_csrf(c)
    # 1 KB payload — well within 12 MB limit
    r = c.post("/register", data={"email": "a@b.com", "password": "x", "csrftoken": csrf})
    assert r.status_code != 413


def test_body_over_limit_rejected():
    c = make_client()
    # Send Content-Length header claiming 13 MB — middleware rejects before reading body
    r = c.post(
        "/register",
        data=b"x",
        headers={"Content-Length": str(13 * 1024 * 1024)},
    )
    assert r.status_code == 413


def test_body_exactly_at_limit_accepted():
    c = make_client()
    r = c.post(
        "/register",
        data=b"x",
        headers={"Content-Length": str(12 * 1024 * 1024)},
    )
    assert r.status_code != 413


def test_invalid_content_length_rejected():
    c = make_client()
    r = c.post(
        "/register",
        data=b"x",
        headers={"Content-Length": "not-a-number"},
    )
    assert r.status_code == 400


# ── CSRF cookie ───────────────────────────────────────────────────────────────

def test_csrf_cookie_set_on_first_visit():
    c = make_client()
    c.get("/login")
    assert "csrftoken" in c.cookies


def test_csrf_cookie_persists_across_requests():
    c = make_client()
    c.get("/login")
    token1 = c.cookies.get("csrftoken")
    c.get("/register")
    token2 = c.cookies.get("csrftoken")
    assert token1 == token2


def test_missing_csrf_token_on_post_rejected():
    c = make_client()
    r = c.post("/register", data={"email": "x@y.com", "password": "pass"})
    # Should fail (either 400, 302 with error, or 403)
    assert r.status_code in (400, 403, 422) or "error" in r.url


# ── Request-ID ────────────────────────────────────────────────────────────────

def test_request_id_echoed_from_client():
    c = make_client()
    r = c.get("/health", headers={"X-Request-ID": "my-custom-id-123"})
    assert r.headers.get("X-Request-ID") == "my-custom-id-123"


def test_request_id_generated_if_absent():
    c = make_client()
    r = c.get("/health")
    rid = r.headers.get("X-Request-ID")
    assert rid is not None
    assert len(rid) > 0


def test_request_id_different_per_request():
    c = make_client()
    r1 = c.get("/health")
    r2 = c.get("/health")
    assert r1.headers.get("X-Request-ID") != r2.headers.get("X-Request-ID")


# ── Security headers ──────────────────────────────────────────────────────────

def test_x_content_type_options():
    c = make_client()
    r = c.get("/")
    assert r.headers.get("X-Content-Type-Options") == "nosniff"


def test_x_frame_options_deny():
    c = make_client()
    r = c.get("/")
    assert r.headers.get("X-Frame-Options") == "DENY"


def test_referrer_policy():
    c = make_client()
    r = c.get("/")
    assert "strict-origin" in r.headers.get("Referrer-Policy", "")


def test_permissions_policy():
    c = make_client()
    r = c.get("/")
    pp = r.headers.get("Permissions-Policy", "")
    assert "geolocation" in pp
    assert "camera" in pp


def test_csp_header_present():
    c = make_client()
    r = c.get("/")
    assert "Content-Security-Policy" in r.headers


def test_no_hsts_without_railway_env():
    c = make_client()
    r = c.get("/")
    assert "Strict-Transport-Security" not in r.headers


def test_html_pages_not_cached():
    c, _, csrf = make_auth_client("nocache")
    r = c.get("/swipe", follow_redirects=True)
    cc = r.headers.get("Cache-Control", "")
    assert "no-store" in cc or "no-cache" in cc


# ── Startup passthrough ───────────────────────────────────────────────────────

def test_health_passthrough_always_200():
    c = make_client()
    r = c.get("/health")
    assert r.status_code == 200


def test_root_passthrough_returns_response():
    c = make_client()
    r = c.get("/")
    assert r.status_code in (200, 302)
