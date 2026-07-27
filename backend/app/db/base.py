"""SQLAlchemy declarative base and portable column types."""

from __future__ import annotations

import datetime as dt
import json
from typing import Any

from sqlalchemy import DateTime, MetaData, String, TypeDecorator
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, mapped_column
from sqlalchemy.types import JSON

# Deterministic constraint naming keeps Alembic autogenerate stable.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


#: JSON column that uses native JSONB on PostgreSQL and JSON elsewhere.
JSONType = JSON().with_variant(JSONB(), "postgresql")


class UTCDateTime(TypeDecorator):
    """Timezone-aware datetime that round-trips correctly on SQLite.

    SQLite drops tzinfo; we normalise to UTC on write and re-attach UTC on
    read so application code never sees a naive datetime.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Any) -> Any:
        if value is None:
            return None
        if not isinstance(value, dt.datetime):
            raise TypeError(f"expected datetime, got {type(value)!r}")
        if value.tzinfo is None:
            value = value.replace(tzinfo=dt.UTC)
        return value.astimezone(dt.UTC)

    def process_result_value(self, value: Any, dialect: Any) -> Any:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=dt.UTC)
        return value.astimezone(dt.UTC)


class EnumType(TypeDecorator):
    """A ``StrEnum`` stored as a plain string and *returned as the enum*.

    SQLAlchemy's native ``Enum`` type creates backend-specific CHECK
    constraints or PostgreSQL enum types, which make adding a vocabulary value
    a schema migration.  A plain ``String`` avoids that but hands application
    code raw strings, so ``row.status is RunStatus.SUCCEEDED`` silently fails
    and ``row.band.value`` raises.  This keeps the flexible storage and
    restores the typed read.
    """

    impl = String
    cache_ok = True

    def __init__(self, enum_class: type, length: int = 48) -> None:
        self.enum_class = enum_class
        super().__init__(length)

    def process_bind_param(self, value: Any, dialect: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, self.enum_class):
            return value.value
        # Accept the raw string too, but validate it against the vocabulary.
        return self.enum_class(value).value

    def process_result_value(self, value: Any, dialect: Any) -> Any:
        if value is None:
            return None
        try:
            return self.enum_class(value)
        except ValueError:
            # A value written by an older schema version: surface it as-is
            # rather than failing the whole query.
            return value


class StringList(TypeDecorator):
    """A ``list[str]`` stored as a JSON array, portable across backends."""

    impl = JSON
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Any) -> Any:
        if value is None:
            return []
        return list(value)

    def process_result_value(self, value: Any, dialect: Any) -> Any:
        if value is None:
            return []
        if isinstance(value, str):  # defensive: legacy rows
            return json.loads(value)
        return list(value)


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


def pk_column(prefix_len: int = 40):
    return mapped_column(String(prefix_len), primary_key=True)


def timestamp_columns() -> dict[str, Any]:
    return {
        "created_at": mapped_column(UTCDateTime, default=utcnow, nullable=False),
        "updated_at": mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow, nullable=False),
    }
