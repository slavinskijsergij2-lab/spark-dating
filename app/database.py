import os
from urllib.parse import quote_plus

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


def _get_database_url() -> str:
    url = os.getenv("DATABASE_URL", "").strip()
    if url:
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql://", 1)
        return url

    url = os.getenv("DATABASE_PRIVATE_URL", "").strip()
    if url:
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql://", 1)
        return url

    pg_host = os.getenv("PGHOST", "").strip()
    pg_port = os.getenv("PGPORT", "5432").strip()
    pg_db   = os.getenv("PGDATABASE", "").strip()
    pg_user = os.getenv("PGUSER", "").strip()
    pg_pass = os.getenv("PGPASSWORD", "").strip()
    if pg_host and pg_db and pg_user:
        encoded_user = quote_plus(pg_user)
        encoded_pass = quote_plus(pg_pass)
        return f"postgresql://{encoded_user}:{encoded_pass}@{pg_host}:{pg_port}/{pg_db}"

    return "sqlite:///./dating.db"


def _to_async_url(url: str) -> str:
    if url.startswith("sqlite://"):
        return url.replace("sqlite://", "sqlite+aiosqlite://", 1)
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return url


DATABASE_URL = _get_database_url()
ASYNC_DATABASE_URL = _to_async_url(DATABASE_URL)

_is_sqlite = DATABASE_URL.startswith("sqlite")
_sync_connect_args = {"check_same_thread": False} if _is_sqlite else {}

# Sync engine — used only for startup migrations and test fixtures
if _is_sqlite:
    engine = create_engine(DATABASE_URL, connect_args=_sync_connect_args)
else:
    engine = create_engine(
        DATABASE_URL,
        pool_size=5,
        max_overflow=10,
        pool_timeout=30,
        pool_recycle=1800,
        pool_pre_ping=True,
    )

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Async engine — used for all HTTP request handling
if _is_sqlite:
    async_engine = create_async_engine(ASYNC_DATABASE_URL)
else:
    async_engine = create_async_engine(
        ASYNC_DATABASE_URL,
        pool_size=5,
        max_overflow=10,
        pool_timeout=30,
        pool_recycle=1800,
        pool_pre_ping=True,
    )

AsyncSessionLocal = async_sessionmaker(
    async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

Base = declarative_base()


async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
