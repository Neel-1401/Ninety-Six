"""Engine and session factory. LEAD-OWNED.

Rules
- Keep transactions SHORT. Never hold a transaction open across an external API call:
  commit the claim, call the provider, then open a new session to persist the result.
- SQLite runs in WAL mode with a busy timeout so the API and worker can interleave.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from .config import settings


def _make_engine(url: str):  # noqa: ANN202
    connect_args = {"check_same_thread": False, "timeout": 30} if url.startswith("sqlite") else {}
    eng = create_engine(url, connect_args=connect_args, future=True)
    if url.startswith("sqlite"):

        @event.listens_for(eng, "connect")
        def _pragmas(dbapi_conn, _record):  # noqa: ANN001
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA busy_timeout=30000")
            cur.close()

    return eng


engine = _make_engine(settings.database_url)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)


def configure(url: str) -> None:
    """Re-point the engine (used by tests to get an isolated temp database)."""
    global engine
    engine.dispose()
    engine = _make_engine(url)
    SessionLocal.configure(bind=engine)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Unit of work for background code: commits on success, rolls back on error."""
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def get_session() -> Iterator[Session]:
    """FastAPI dependency. Route/service code commits explicitly."""
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


def create_all_for_tests() -> None:
    from .models import Base

    Base.metadata.create_all(engine)
