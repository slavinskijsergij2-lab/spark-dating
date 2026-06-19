import os
from urllib.parse import quote_plus

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from dotenv import load_dotenv

load_dotenv()


def _get_database_url() -> str:
    # 1. Try DATABASE_URL (Railway links Postgres this way)
    url = os.getenv("DATABASE_URL", "").strip()
    if url:
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql://", 1)
        return url

    # 2. Try DATABASE_PRIVATE_URL (Railway private network URL)
    url = os.getenv("DATABASE_PRIVATE_URL", "").strip()
    if url:
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql://", 1)
        return url

    # 3. Build from individual PG* vars — URL-encode credentials to handle special chars
    pg_host = os.getenv("PGHOST", "").strip()
    pg_port = os.getenv("PGPORT", "5432").strip()
    pg_db   = os.getenv("PGDATABASE", "").strip()
    pg_user = os.getenv("PGUSER", "").strip()
    pg_pass = os.getenv("PGPASSWORD", "").strip()
    if pg_host and pg_db and pg_user:
        encoded_user = quote_plus(pg_user)
        encoded_pass = quote_plus(pg_pass)
        return f"postgresql://{encoded_user}:{encoded_pass}@{pg_host}:{pg_port}/{pg_db}"

    # 4. Fallback to local SQLite
    return "sqlite:///./dating.db"


DATABASE_URL = _get_database_url()

_is_sqlite = DATABASE_URL.startswith("sqlite")
connect_args = {"check_same_thread": False} if _is_sqlite else {}

if _is_sqlite:
    engine = create_engine(DATABASE_URL, connect_args=connect_args)
else:
    engine = create_engine(
        DATABASE_URL,
        pool_size=5,
        max_overflow=10,
        pool_timeout=30,
        pool_recycle=1800,   # FIX M1: Railway closes idle connections after ~10min
        pool_pre_ping=True,  # detect stale connections before use
    )

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
