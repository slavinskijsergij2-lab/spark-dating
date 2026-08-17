"""
E2E test configuration — starts a real uvicorn server, runs Playwright against it.
"""
import os
import secrets
import socket
import subprocess
import sys
import time

import pytest

os.environ.setdefault("DATABASE_URL", "sqlite:///./tests/e2e_test.db")
os.environ.setdefault("SECRET_KEY", "e2e-test-secret-key-not-for-prod")
os.environ["PHOTO_DIR"] = "/tmp/spark_e2e_photos"
os.environ["TESTING"] = "1"
os.environ["PREMIUM_CODES"] = ""
os.environ.pop("RAILWAY_ENVIRONMENT", None)

os.makedirs("/tmp/spark_e2e_photos", exist_ok=True)


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def live_server_url():
    """Start uvicorn in a subprocess and return its base URL."""
    port = _free_port()
    env = {**os.environ, "PORT": str(port)}
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app",
         "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    # Wait until server is up
    base = f"http://127.0.0.1:{port}"
    import httpx
    for _ in range(30):
        try:
            r = httpx.get(f"{base}/health", timeout=1)
            if r.status_code < 500:
                break
        except Exception:
            pass
        time.sleep(0.5)
    yield base
    proc.terminate()
    proc.wait(timeout=5)


@pytest.fixture(scope="session")
def browser_context_args():
    return {"viewport": {"width": 390, "height": 844}}  # iPhone 14 size


# Helper: register + login via HTTP (fast, no browser needed)
def api_register(base_url: str, email: str, password: str = "TestE2E123!") -> dict:
    import httpx
    with httpx.Client(base_url=base_url, follow_redirects=True) as c:
        # get csrf
        r = c.get("/login")
        csrf = r.cookies.get("csrftoken", "")
        # register
        c.post("/register", data={"email": email, "password": password, "csrftoken": csrf})
        # login
        r2 = c.get("/login")
        csrf2 = r2.cookies.get("csrftoken", "")
        c.post("/login", data={"email": email, "password": password, "csrftoken": csrf2})
        return {"cookies": dict(c.cookies), "csrf": csrf2}
