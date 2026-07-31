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
from app.core.logging import get_logger
from app.db.base import Base
from app.db.sanitize import scrub_instance

log = get_logger(__name__)

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


def install_nul_guard(factory: sessionmaker[Session]) -> None:
    """Strip NUL bytes from every object about to be written.

    ``before_flush`` is the one place that sees all of it: every write in this
    application goes through the ORM unit of work, so a single listener covers
    all 19 tables and every column, including the JSON ones -- which matters,
    because a NUL inside a JSON value survives serialisation as ``\\u0000`` and
    PostgreSQL's ``jsonb`` rejects that too.

    It runs here rather than in a type decorator because JSON values must be
    cleaned *before* serialisation, and rather than in the extraction code
    because the guarantee should hold for anything written, not only for the
    paths someone remembered.
    """

    @event.listens_for(factory, "before_flush")
    def _strip_nul_bytes(session: Session, _context: Any, _instances: Any) -> None:
        for instance in (*session.new, *session.dirty):
            changed = scrub_instance(instance)
            if changed:
                log.warning(
                    "db.nul_bytes_stripped",
                    table=type(instance).__tablename__,
                    columns=changed,
                )


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
        install_nul_guard(_SessionFactory)
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
