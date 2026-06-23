"""Basic performance benchmarks — ensure key endpoints respond under budget."""
import time
from tests.conftest import make_client


def _ms(start: float) -> float:
    return (time.perf_counter() - start) * 1000


def test_health_under_200ms():
    c = make_client()
    t = time.perf_counter()
    r = c.get("/health")
    elapsed = _ms(t)
    assert r.status_code == 200
    assert elapsed < 200, f"/health took {elapsed:.1f}ms (limit 200ms)"


def test_login_page_under_300ms():
    c = make_client()
    t = time.perf_counter()
    r = c.get("/login")
    elapsed = _ms(t)
    assert r.status_code == 200
    assert elapsed < 300, f"/login took {elapsed:.1f}ms (limit 300ms)"


def test_root_under_300ms():
    c = make_client()
    t = time.perf_counter()
    r = c.get("/")
    elapsed = _ms(t)
    assert r.status_code in (200, 302)
    assert elapsed < 300, f"/ took {elapsed:.1f}ms (limit 300ms)"


def test_health_10_sequential_under_1s():
    """10 health checks in a row must complete under 1 second total."""
    c = make_client()
    t = time.perf_counter()
    for _ in range(10):
        r = c.get("/health")
        assert r.status_code == 200
    elapsed = _ms(t)
    assert elapsed < 1000, f"10x /health took {elapsed:.1f}ms (limit 1000ms)"
