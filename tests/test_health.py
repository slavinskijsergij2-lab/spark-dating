"""Tests for /health endpoint and startup readiness."""
from tests.conftest import make_client


def test_health_always_200():
    """Health check must always return HTTP 200 (never 500) so Railway never
    marks the deployment as unhealthy due to a transient DB blip."""
    r = make_client().get("/health")
    assert r.status_code == 200


def test_health_response_shape():
    r = make_client().get("/health")
    data = r.json()
    assert "status" in data
    assert "db" in data
    assert "startup_done" in data
    assert "startup_ok" in data


def test_health_db_field_is_bool():
    r = make_client().get("/health")
    assert isinstance(r.json()["db"], bool)


def test_health_startup_done_true_in_tests():
    """In TESTING mode, startup tasks complete before requests are served."""
    r = make_client().get("/health")
    assert r.json()["startup_done"] is True


def test_health_ok_status_when_db_reachable():
    r = make_client().get("/health")
    data = r.json()
    if data["db"]:
        assert data["status"] == "ok"
    else:
        assert data["status"] == "starting"


def test_readiness_passthrough_for_health():
    """/health must bypass the startup readiness middleware."""
    r = make_client().get("/health")
    assert r.status_code == 200


def test_readiness_passthrough_for_root():
    """Root page bypasses readiness check."""
    r = make_client().get("/")
    assert r.status_code in (200, 302)
