"""
Test configuration and shared fixtures.

Env vars must be set before ANY app module is imported — Python caches modules,
so database.py creates its engine exactly once, at first import.
"""
import os
import secrets

os.environ.setdefault("DATABASE_URL", "sqlite:///./tests/test.db")
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-prod")
os.environ["PHOTO_DIR"] = "/tmp/spark_test_photos"
os.environ["TESTING"] = "1"              # disables rate limiting
os.environ["PREMIUM_CODES"] = ""         # disable code-gating in tests
os.environ.pop("RAILWAY_ENVIRONMENT", None)  # prevent production-mode guards

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.database import Base, engine, get_db, SessionLocal, async_engine
import main  # runs inline migrations against the test DB

app = main.app

os.makedirs("/tmp/spark_test_photos", exist_ok=True)


@pytest.fixture(scope="session", autouse=True)
def reset_db():
    """Drop and recreate schema once per test session for a clean slate."""
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield
    # Leave the DB file for inspection after failures; CI can delete it.


@pytest.fixture()
def db() -> Session:
    """Raw DB session for direct fixture setup (bypasses HTTP layer)."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


# ── HTTP helpers ───────────────────────────────────────────────────────────────

def make_client() -> TestClient:
    """Each call returns a fresh TestClient with its own cookie jar."""
    return TestClient(app, raise_server_exceptions=True)


def get_csrf(client: TestClient) -> str:
    """Hit the login page to get (and store) the csrftoken cookie."""
    client.get("/login")
    return client.cookies.get("csrftoken", "")


def register(client: TestClient, email: str, password: str = "TestPass123!") -> None:
    csrf = get_csrf(client)
    client.post("/register", data={"email": email, "password": password, "csrftoken": csrf})


def login(client: TestClient, email: str, password: str = "TestPass123!") -> str:
    """Log in and return the current csrftoken."""
    csrf = get_csrf(client)
    client.post(
        "/login",
        data={"email": email, "password": password, "csrftoken": csrf},
        follow_redirects=True,
    )
    return client.cookies.get("csrftoken", csrf)


def make_auth_client(suffix: str = "", password: str = "TestPass123!"):
    """Create a fresh TestClient with a registered + logged-in user.

    Returns (client, email, csrf_token).
    """
    tag = suffix or secrets.token_hex(4)
    email = f"user_{tag}@test.com"
    client = make_client()
    register(client, email, password)
    csrf = login(client, email, password)
    return client, email, csrf


# ── Direct DB helpers (avoid HTTP round-trips for setup) ──────────────────────

def _create_profile(db: Session, user_id: int, name: str = "Test") -> None:
    from app.models.models import GenderEnum, Profile
    existing = db.query(Profile).filter(Profile.user_id == user_id).first()
    if existing:
        return
    db.add(Profile(
        user_id=user_id,
        name=name,
        age=25,
        gender=GenderEnum.female,
        looking_for=GenderEnum.male,
        bio="test bio",
    ))
    db.commit()


def _get_user_id(db: Session, email: str) -> int:
    from app.models.models import User
    return db.query(User.id).filter(User.email == email).scalar()


def _create_match(db: Session, user1_id: int, user2_id: int) -> int:
    from app.models.models import Match
    assert user1_id is not None, "user1_id is None — registration likely failed"
    assert user2_id is not None, "user2_id is None — registration likely failed"
    m = Match(user1_id=user1_id, user2_id=user2_id)
    db.add(m)
    db.commit()
    db.refresh(m)
    return m.id
