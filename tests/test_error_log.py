"""Tests for persistent error logging (ErrorLog model + /errors endpoint)."""
import os
import pytest
from tests.conftest import make_client


def _client():
    return make_client()


# ── /errors endpoint auth ─────────────────────────────────────────────────────

def test_errors_no_token_forbidden():
    r = _client().get("/errors")
    assert r.status_code == 403


def test_errors_wrong_token_forbidden():
    r = _client().get("/errors?token=wrong")
    assert r.status_code == 403


def test_errors_valid_token_returns_json(monkeypatch):
    monkeypatch.setenv("METRICS_TOKEN", "test-secret")
    r = _client().get("/errors?token=test-secret")
    assert r.status_code == 200
    body = r.json()
    assert "errors" in body
    assert "source" in body


def test_errors_returns_list(monkeypatch):
    monkeypatch.setenv("METRICS_TOKEN", "test-secret")
    r = _client().get("/errors?token=test-secret")
    assert isinstance(r.json()["errors"], list)


# ── ErrorLog model ────────────────────────────────────────────────────────────

def test_errorlog_model_fields():
    from app.models.models import ErrorLog
    log = ErrorLog(
        method="GET",
        path="/some/path",
        exc_type="ValueError",
        exc_msg="something went wrong",
        traceback="Traceback...",
        user_agent="pytest/1.0",
    )
    assert log.method == "GET"
    assert log.path == "/some/path"
    assert log.exc_type == "ValueError"
    assert log.exc_msg == "something went wrong"
    assert log.traceback == "Traceback..."
    assert log.user_agent == "pytest/1.0"


def test_errorlog_table_name():
    from app.models.models import ErrorLog
    assert ErrorLog.__tablename__ == "error_logs"


# ── /metrics endpoint ─────────────────────────────────────────────────────────

def test_metrics_no_token_forbidden():
    r = _client().get("/metrics")
    assert r.status_code == 403


def test_metrics_valid_token(monkeypatch):
    monkeypatch.setenv("METRICS_TOKEN", "test-secret")
    r = _client().get("/metrics?token=test-secret")
    assert r.status_code == 200
    body = r.json()
    assert "requests_total" in body
    assert "errors_5xx" in body
    assert "uptime_seconds" in body


# ── utils/maintenance.py ──────────────────────────────────────────────────────

def test_maintenance_imports():
    from app.utils.maintenance import fix_broken_photo_urls, do_cleanup
    assert callable(fix_broken_photo_urls)
    assert callable(do_cleanup)


def test_do_cleanup_runs_without_error():
    """do_cleanup should not raise even on empty test DB (SQLite)."""
    from app.utils.maintenance import do_cleanup
    do_cleanup()  # should complete without exception


# ── utils/template_filters.py ────────────────────────────────────────────────

def test_tojson_filter_basic():
    from app.utils.template_filters import tojson_filter
    result = tojson_filter({"key": "value"})
    assert '"key"' in result
    assert '"value"' in result


def test_tojson_filter_escapes_script_tags():
    from app.utils.template_filters import tojson_filter
    result = tojson_filter({"html": "</script>"})
    assert "</script>" not in result
    assert "<\\/" in result


def test_tojson_filter_datetime():
    from datetime import datetime
    from app.utils.template_filters import tojson_filter
    dt = datetime(2026, 1, 15, 12, 0, 0)
    result = tojson_filter({"ts": dt})
    assert "2026-01-15" in result


def test_online_status_none_when_no_last_seen():
    from app.utils.template_filters import online_status
    assert online_status(None) is None


def test_online_status_online_when_recent():
    from datetime import datetime, timedelta
    from app.utils.time import utcnow
    from app.utils.template_filters import online_status
    recent = utcnow() - timedelta(seconds=60)
    result = online_status(recent, lang="ru")
    assert result is not None
    assert result["is_online"] is True


def test_online_status_minutes_ago():
    from datetime import datetime, timedelta
    from app.utils.time import utcnow
    from app.utils.template_filters import online_status
    mins_ago = utcnow() - timedelta(minutes=10)
    result = online_status(mins_ago, lang="en")
    assert result is not None
    assert result["is_online"] is False
    assert "10" in result["label"]


def test_online_status_none_when_old():
    from datetime import timedelta
    from app.utils.time import utcnow
    from app.utils.template_filters import online_status
    old = utcnow() - timedelta(days=2)
    assert online_status(old) is None


# ── utils/photos.py remove_photo_file ────────────────────────────────────────

def test_remove_photo_file_none_does_nothing():
    from app.utils.photos import remove_photo_file
    remove_photo_file(None)  # must not raise


def test_remove_photo_file_non_photo_url_does_nothing():
    from app.utils.photos import remove_photo_file
    remove_photo_file("data:image/jpeg;base64,abc")  # must not raise
    remove_photo_file("https://external.com/img.jpg")  # must not raise


def test_remove_photo_file_missing_file_does_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("PHOTO_DIR", str(tmp_path))
    from app.utils.photos import remove_photo_file
    remove_photo_file("/photos/nonexistent_file.jpg")  # must not raise


def test_remove_photo_file_deletes_existing_file(tmp_path, monkeypatch):
    monkeypatch.setenv("PHOTO_DIR", str(tmp_path))
    fname = "test_photo.jpg"
    (tmp_path / fname).write_bytes(b"fake image data")
    assert (tmp_path / fname).exists()
    from app.utils.photos import remove_photo_file
    remove_photo_file(f"/photos/{fname}")
    assert not (tmp_path / fname).exists()
