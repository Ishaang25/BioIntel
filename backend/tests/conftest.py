"""Test configuration.

Every test runs against a real SQLite database in a temporary directory and the
deterministic LLM provider.  External HTTP is blocked by default: a test that
reaches the network is a bug, not a feature.  Tests that genuinely exercise the
live APIs are marked ``network`` and skipped unless ``BIOINTEL_TEST_NETWORK=1``.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

# Configure the environment *before* app.core.config is imported anywhere.
_TMP_ROOT = Path(tempfile.mkdtemp(prefix="biointel-tests-"))
os.environ.update(
    {
        "ENVIRONMENT": "test",
        "STORAGE_DIR": str(_TMP_ROOT),
        "DATABASE_URL": f"sqlite+pysqlite:///{(_TMP_ROOT / 'test.db').as_posix()}",
        "LLM_PROVIDER": "stub",
        "OPENAI_API_KEY": "",
        "RETRIEVAL_ENABLED": "false",
        # Runs are enqueued but not executed: no worker is running, so API
        # tests observe a pending run without racing a background pipeline.
        # Pipeline tests drive AnalysisPipeline directly.
        "JOB_EXECUTION_MODE": "worker",
        "API_KEYS": "",
        "LOG_LEVEL": "WARNING",
        "RATE_LIMIT_PER_MINUTE": "10000",
        "UPLOAD_RATE_LIMIT_PER_HOUR": "10000",
    }
)

from app.core.config import get_settings  # noqa: E402
from app.db.base import Base  # noqa: E402
from app.db.session import create_all, get_engine, reset_engine, session_scope  # noqa: E402
from app.llm.client import LLMClient  # noqa: E402
from app.llm.stub_provider import StubProvider  # noqa: E402


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "network: requires outbound network access")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if os.environ.get("BIOINTEL_TEST_NETWORK") == "1":
        return
    skip = pytest.mark.skip(reason="set BIOINTEL_TEST_NETWORK=1 to run live-network tests")
    for item in items:
        if "network" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session", autouse=True)
def _session_setup() -> Iterator[None]:
    get_settings().ensure_directories()
    create_all()
    yield
    reset_engine()
    shutil.rmtree(_TMP_ROOT, ignore_errors=True)


@pytest.fixture(autouse=True)
def clean_database() -> Iterator[None]:
    """Truncate every table between tests so they cannot leak into each other."""
    yield
    engine = get_engine()
    with engine.begin() as connection:
        for table in reversed(Base.metadata.sorted_tables):
            connection.exec_driver_sql(f'DELETE FROM "{table.name}"')


@pytest.fixture(autouse=True)
def _reset_rate_limits() -> Iterator[None]:
    from app.api.deps import reset_rate_limits

    reset_rate_limits()
    yield


@pytest.fixture(autouse=True)
def _block_network(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> None:
    """Fail loudly if a unit test tries to make a real HTTP request."""
    if "network" in request.keywords:
        return

    import httpx

    async def _blocked(*args, **kwargs):  # pragma: no cover - only on failure
        raise RuntimeError(
            "Outbound HTTP is disabled in tests. Use httpx.MockTransport, or mark "
            "the test with @pytest.mark.network."
        )

    # Patch the real transport only: httpx.MockTransport stays usable, so tests
    # can still exercise the full client stack against canned responses.
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", _blocked)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", _blocked)


@pytest.fixture
def settings():
    return get_settings()


@pytest.fixture
def db() -> Iterator:
    with session_scope() as session:
        yield session


@pytest.fixture
def llm() -> LLMClient:
    return LLMClient(StubProvider(), persist_logs=False)


@pytest.fixture
def client() -> Iterator:
    from fastapi.testclient import TestClient

    from app.main import create_app

    with TestClient(create_app()) as test_client:
        yield test_client


@pytest.fixture
def neurogen_pdf() -> bytes:
    from tests.fixtures.sample_deck import neurogen_pdf as build

    return build()


@pytest.fixture
def uploaded_document(db, neurogen_pdf):
    from app.services.documents import store_document

    document, _ = store_document(db, data=neurogen_pdf, filename="neurogen.pdf")
    db.commit()
    return document
