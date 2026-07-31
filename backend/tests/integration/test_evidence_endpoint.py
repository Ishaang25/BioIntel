"""Regression: `/runs/{id}/evidence` must work on PostgreSQL.

Production failed with::

    sqlalchemy.exc.ProgrammingError
    could not identify an equality operator for type json

`SELECT DISTINCT` requires an equality operator for every selected column.
PostgreSQL's ``json`` has none (``jsonb`` does), and ``StringList`` declared
plain ``JSON``, so ``evidence_items`` carried four such columns.

The suite runs on SQLite, which has no json/jsonb distinction and permits
DISTINCT on anything -- so a functional test alone would pass either way. These
tests therefore also assert against the *PostgreSQL-compiled* SQL and DDL,
which is what actually differs.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from app.db.base import Base, StringList
from app.db.models import AnalysisRun, Claim, ClaimEvidenceLink, EvidenceItem
from app.db.session import session_scope
from app.services import runs as run_service
from app.services.documents import store_document


def _sample_pdf() -> bytes:
    import fitz

    doc = fitz.open()
    doc.new_page().insert_text((72, 72), "A claim about a Phase 3 trial.")
    data: bytes = doc.tobytes()
    doc.close()
    return data


class TestSchemaHasNoPlainJson:
    """The type-level fix: no column may be plain `json` on PostgreSQL."""

    def test_no_table_declares_a_plain_json_column(self) -> None:
        offenders: list[str] = []
        for table in Base.metadata.sorted_tables:
            ddl = str(CreateTable(table).compile(dialect=postgresql.dialect()))
            for line in ddl.splitlines():
                parts = line.strip().rstrip(",").split()
                if len(parts) >= 2 and parts[1] == "JSON":
                    offenders.append(f"{table.name}.{parts[0]}")
        assert offenders == [], (
            "plain json has no equality operator; these columns would break "
            f"any DISTINCT/UNION/GROUP BY: {offenders}"
        )

    def test_string_list_uses_jsonb_on_postgresql(self) -> None:
        rendered = StringList().dialect_impl(postgresql.dialect()).compile(postgresql.dialect())
        assert "JSONB" in str(rendered).upper()


class TestEvidenceQueryShape:
    """The query-level fix: de-duplicate on the id, not on whole rows."""

    def test_query_does_not_select_distinct_over_evidence_rows(self) -> None:
        statement = EvidenceItem.__table__.select().where(
            EvidenceItem.id.in_(run_service.evidence_ids_for_run("run_x"))
        )
        sql = str(statement.compile(dialect=postgresql.dialect()))
        head = sql[: sql.lower().index("from")]
        assert "DISTINCT" not in head.upper(), (
            "DISTINCT over the evidence row is what broke production; "
            "de-duplication belongs on the id sub-select"
        )
        # The de-duplication itself must still be present, on the sub-select.
        assert "DISTINCT" in sql.upper()


@pytest.fixture
def run_with_duplicate_links() -> str:
    """A run whose evidence is linked by several claims -- the DISTINCT case."""
    with session_scope() as session:
        document, _ = store_document(session, data=_sample_pdf(), filename="ev.pdf")
        session.flush()
        run = AnalysisRun(document_id=document.id)
        session.add(run)
        session.flush()

        evidence = EvidenceItem(
            source="pubmed",
            external_id="39608389",
            title="Safety and immunogenicity of mRNA-1345",
            authors=["Wilson E", "Qian L"],
            keywords=["RSV", "vaccine"],
            mesh_terms=["Vaccines"],
            publication_types=["Journal Article"],
            publication_year=2025,
        )
        session.add(evidence)
        session.flush()

        # Two claims linking the same record: without de-duplication the
        # endpoint would return it twice.
        for index in range(2):
            claim = Claim(
                run_id=run.id,
                document_id=document.id,
                statement=f"claim {index}",
                verbatim_quote="",
                page_number=1,
            )
            session.add(claim)
            session.flush()
            session.add(
                ClaimEvidenceLink(
                    run_id=run.id, claim_id=claim.id, evidence_id=evidence.id, stance="supports"
                )
            )
        session.flush()
        return run.id


class TestEvidenceEndpoint:
    def test_returns_each_record_once(
        self, client: TestClient, run_with_duplicate_links: str
    ) -> None:
        response = client.get(f"/api/v1/runs/{run_with_duplicate_links}/evidence")
        assert response.status_code == 200
        body = response.json()
        assert len(body) == 1, "a record linked by two claims must appear once"
        assert body[0]["pmid"] is None or isinstance(body[0]["pmid"], str)
        assert body[0]["title"] == "Safety and immunogenicity of mRNA-1345"
        assert body[0]["authors"] == ["Wilson E", "Qian L"]

    def test_service_helper_agrees_with_the_endpoint(
        self, client: TestClient, run_with_duplicate_links: str
    ) -> None:
        with session_scope() as session:
            rows = run_service.evidence_for_run(session, run_with_duplicate_links)
        assert len(rows) == 1

    def test_run_with_no_evidence_returns_empty(
        self, client: TestClient, run_with_duplicate_links: str
    ) -> None:
        with session_scope() as session:
            document, _ = store_document(session, data=_sample_pdf(), filename="empty.pdf")
            session.flush()
            empty = AnalysisRun(document_id=document.id)
            session.add(empty)
            session.flush()
            empty_id = empty.id

        response = client.get(f"/api/v1/runs/{empty_id}/evidence")
        assert response.status_code == 200
        assert response.json() == []
