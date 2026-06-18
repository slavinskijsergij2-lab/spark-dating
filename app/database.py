import os
from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from dotenv import load_dotenv

load_dotenv()

def _get_database_url() -> str:
    # 1. Try DATABASE_URL (Railway links Postgres this way)
    url = os.getenv("DATABASE_URL", "").strip()
    if url:
        # Railway uses postgres://, SQLAlchemy needs postgresql://
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql://", 1)
        return url

    # 2. Try DATABASE_PRIVATE_URL (Railway private network URL)
    url = os.getenv("DATABASE_PRIVATE_URL", "").strip()
    if url:
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql://", 1)
        return url

    # 3. Try to build from individual PG* vars
    pg_host = os.getenv("PGHOST", "").strip()
    pg_port = os.getenv("PGPORT", "5432").strip()
    pg_db   = os.getenv("PGDATABASE", "").strip()
    pg_user = os.getenv("PGUSER", "").strip()
    pg_pass = os.getenv("PGPASSWORD", "").strip()
    if pg_host and pg_db and pg_user:
        return f"postgresql://{pg_user}:{pg_pass}@{pg_host}:{pg_port}/{pg_db}"

    # 4. Fallback to local SQLite
    return "sqlite:///./dating.db"


DATABASE_URL = _get_database_url()

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
