"""Engine and session management.

The application is synchronous at the persistence layer (SQLAlchemy 2.0 ORM)
and asynchronous at the I/O layer (HTTP + LLM).  DB work is short and runs in
a threadpool via FastAPI's dependency system, which keeps the ORM code simple
and avoids the async-driver matrix for SQLite/Postgres.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import Engine, create_engine, event, inspect
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import BACKEND_ROOT, settings
from app.db.base import Base

_engine: Engine | None = None
_SessionFactory: sessionmaker[Session] | None = None


def _engine_kwargs() -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "echo": settings.db_echo,
        "future": True,
        "pool_pre_ping": True,
    }
    if settings.is_sqlite:
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 30}
        if ":memory:" in settings.database_url:
            kwargs["poolclass"] = StaticPool
    else:
        kwargs["pool_size"] = settings.db_pool_size
        kwargs["max_overflow"] = settings.db_max_overflow
    return kwargs


def _configure_sqlite(engine: Engine) -> None:
    """Enable WAL + foreign keys so SQLite behaves like a real database."""

    @event.listens_for(engine, "connect")
    def _set_pragmas(dbapi_conn: Any, _record: Any) -> None:  # pragma: no cover
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        settings.ensure_directories()
        _engine = create_engine(settings.database_url, **_engine_kwargs())
        if settings.is_sqlite:
            _configure_sqlite(_engine)
    return _engine


def get_session_factory() -> sessionmaker[Session]:
    global _SessionFactory
    if _SessionFactory is None:
        _SessionFactory = sessionmaker(
            bind=get_engine(),
            autoflush=False,
            autocommit=False,
            expire_on_commit=False,
            class_=Session,
        )
    return _SessionFactory


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope: commit on success, roll back on failure."""
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding a request-scoped session."""
    with session_scope() as session:
        yield session


def create_all() -> None:
    """Create the schema directly.

    Used by tests and by the ``local`` bootstrap path so the app runs with no
    setup.  Deployed environments use Alembic migrations instead.

    The freshly created schema is stamped with the head revision, so a
    developer who starts the API before running ``alembic upgrade`` does not
    then hit "target database is not up to date".
    """
    import app.db.models  # noqa: F401  (register mappers)

    engine = get_engine()
    is_new = not inspect(engine).has_table("alembic_version")
    Base.metadata.create_all(bind=engine)
    if is_new:
        _stamp_head(engine)


def _stamp_head(engine: Engine) -> None:
    """Record the current migration head without running any migrations."""
    try:
        from alembic.config import Config
        from alembic.runtime.migration import MigrationContext
        from alembic.script import ScriptDirectory

        config_path = BACKEND_ROOT / "alembic.ini"
        if not config_path.exists():  # e.g. installed as a wheel without alembic/
            return
        script = ScriptDirectory.from_config(Config(str(config_path)))
        head = script.get_current_head()
        if head is None:
            return
        with engine.begin() as connection:
            MigrationContext.configure(connection).stamp(script, head)
    except Exception:  # stamping is a convenience, never a hard requirement
        logging.getLogger(__name__).debug("could not stamp alembic head", exc_info=True)


def reset_engine() -> None:
    """Dispose cached engine/session factory (test helper)."""
    global _engine, _SessionFactory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionFactory = None
