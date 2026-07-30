"""Configuration normalisation and production invariants."""

from __future__ import annotations

import pytest

from app.core.config import Settings, normalise_database_url


class TestNormaliseDatabaseUrl:
    """Managed Postgres providers emit URLs SQLAlchemy 2 cannot use as given."""

    @pytest.mark.parametrize(
        ("given", "expected"),
        [
            # Neon, Render, Supabase and Heroku all hand out this form.
            # SQLAlchemy 2 removed the `postgres` alias and rejects it.
            (
                "postgres://user:pw@host/db",
                "postgresql+psycopg://user:pw@host/db",
            ),
            # Accepted by SQLAlchemy, but resolves to psycopg2, which this
            # project does not install -- `.[postgres]` provides psycopg 3.
            (
                "postgresql://user:pw@host/db",
                "postgresql+psycopg://user:pw@host/db",
            ),
            # Query strings and ports must survive untouched.
            (
                "postgres://u:p@h:5432/db?sslmode=require",
                "postgresql+psycopg://u:p@h:5432/db?sslmode=require",
            ),
        ],
    )
    def test_rewrites_provider_urls_onto_the_installed_driver(
        self, given: str, expected: str
    ) -> None:
        assert normalise_database_url(given) == expected

    @pytest.mark.parametrize(
        "url",
        [
            "postgresql+psycopg://user:pw@host/db",
            "sqlite+pysqlite:///./storage/biointel.db",
            "sqlite:///:memory:",
            # An explicit driver is a deliberate choice. Overriding it would
            # hide a missing dependency rather than surface it.
            "postgresql+psycopg2://user:pw@host/db",
            "",
            "not-a-url",
        ],
    )
    def test_leaves_everything_else_alone(self, url: str) -> None:
        assert normalise_database_url(url) == url

    def test_applied_when_settings_are_constructed(self) -> None:
        settings = Settings(database_url="postgres://user:pw@host/db")
        assert settings.database_url == "postgresql+psycopg://user:pw@host/db"
        assert settings.is_sqlite is False


class TestProductionInvariants:
    """Startup must refuse a production configuration that is not safe."""

    def test_rejects_missing_api_keys(self) -> None:
        with pytest.raises(ValueError, match="API_KEYS"):
            Settings(environment="production", api_keys="", secret_key="generated-secret")

    def test_rejects_the_development_secret_key(self) -> None:
        with pytest.raises(ValueError, match="SECRET_KEY"):
            Settings(
                environment="production",
                api_keys="a-key",
                secret_key="dev-insecure-secret-change-me",
            )

    def test_accepts_a_complete_production_configuration(self) -> None:
        settings = Settings(
            environment="production",
            api_keys="key-one,key-two",
            secret_key="generated-secret",
            database_url="postgres://user:pw@host/db",
        )
        assert settings.auth_enabled is True
        assert settings.api_key_set == {"key-one", "key-two"}
        assert settings.database_url.startswith("postgresql+psycopg://")
