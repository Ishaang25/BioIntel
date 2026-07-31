"""Regression: NUL bytes must never reach the database driver.

The production failure was::

    psycopg.DataError: PostgreSQL text fields cannot contain NUL (0x00) bytes

raised inserting into ``claims``. NUL arrives from PDF text extraction, vision
and OCR readings, and model output -- everything a claim is built from.

These tests run on SQLite, which stores NUL happily, so asserting that the
insert succeeds would prove nothing. They assert the invariant PostgreSQL
actually enforces: no NUL in the parameters handed to the driver, and none in
what comes back out. The driver-level assertion is backend-independent, so it
holds for the Postgres deployment even though the suite runs on SQLite.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import event

from app.core.enums import ClaimType, QuestionPriority, RiskCategory, RiskSeverity
from app.db.models import AnalysisRun, Claim, ClaimAssessment, DiligenceQuestion, RiskFlag
from app.db.session import get_engine, session_scope
from app.services.documents import store_document

#: A NUL inside a JSON value survives serialisation as this escape, which
#: PostgreSQL's jsonb rejects with "unsupported Unicode escape sequence".
JSON_NUL_ESCAPE = "\\u0000"


def _sample_pdf() -> bytes:
    """Smallest thing `store_document` will accept."""
    import fitz

    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Phase 3 trial met its primary endpoint.")
    data: bytes = doc.tobytes()
    doc.close()
    return data


@pytest.fixture
def run_id() -> str:
    with session_scope() as session:
        document, _ = store_document(session, data=_sample_pdf(), filename="nul.pdf")
        session.flush()
        run = AnalysisRun(document_id=document.id)
        session.add(run)
        session.flush()
        return run.id


@pytest.fixture
def captured_parameters() -> Iterator[list[Any]]:
    """Every parameter set handed to the DBAPI cursor during the test."""
    engine = get_engine()
    seen: list[Any] = []

    def _capture(
        _conn: Any, _cursor: Any, _statement: str, parameters: Any, _context: Any, _many: bool
    ) -> None:
        seen.append(parameters)

    event.listen(engine, "before_cursor_execute", _capture)
    try:
        yield seen
    finally:
        event.remove(engine, "before_cursor_execute", _capture)


def _flatten(parameters: Any) -> Iterator[str]:
    """Yield every string in a captured parameter set."""
    if isinstance(parameters, str):
        yield parameters
    elif isinstance(parameters, dict):
        for value in parameters.values():
            yield from _flatten(value)
    elif isinstance(parameters, (list, tuple)):
        for value in parameters:
            yield from _flatten(value)


def assert_no_nul_reached_the_driver(captured: list[Any]) -> None:
    for parameters in captured:
        for value in _flatten(parameters):
            assert "\x00" not in value, f"raw NUL reached the driver: {value!r}"
            assert JSON_NUL_ESCAPE not in value, f"escaped NUL reached the driver: {value!r}"


class TestClaimInsertion:
    """The table the production error named."""

    def test_claim_with_nul_in_every_text_field_is_sanitised(
        self, run_id: str, captured_parameters: list[Any]
    ) -> None:
        with session_scope() as session:
            run = session.get(AnalysisRun, run_id)
            assert run is not None
            claim = Claim(
                run_id=run_id,
                document_id=run.document_id,
                # From model output.
                statement="mRESVIA\x00 received approval\x00",
                # From PDF text extraction -- the original source of the bug.
                verbatim_quote="Approval of\x00 mRESVIA, our 2nd\x00 commercial product",
                page_number=9,
                block_id="blk_00\x001",
                claim_type=ClaimType.OTHER,
                # A JSON column: jsonb rejects the escaped form too.
                quantitative=[{"metric": "ORR\x00", "value": 42, "note": ["a\x00", "b"]}],
                # A StringList column.
                review_reasons=["quote not found\x00", "hedged\x00"],
            )
            session.add(claim)
            session.flush()
            claim_id = claim.id

        assert_no_nul_reached_the_driver(captured_parameters)

        with session_scope() as session:
            stored = session.get(Claim, claim_id)
            assert stored is not None
            assert stored.statement == "mRESVIA received approval"
            assert stored.verbatim_quote == "Approval of mRESVIA, our 2nd commercial product"
            assert stored.block_id == "blk_001"
            assert stored.quantitative == [{"metric": "ORR", "value": 42, "note": ["a", "b"]}]
            assert stored.review_reasons == ["quote not found", "hedged"]

    def test_enum_column_survives_scrubbing(self, run_id: str) -> None:
        """`StrEnum` is a `str`; the guard must not downgrade it."""
        with session_scope() as session:
            run = session.get(AnalysisRun, run_id)
            assert run is not None
            claim = Claim(
                run_id=run_id,
                document_id=run.document_id,
                statement="clean\x00",
                verbatim_quote="",
                page_number=1,
                claim_type=ClaimType.REGULATORY_APPROVAL,
            )
            session.add(claim)
            session.flush()
            claim_id = claim.id

        with session_scope() as session:
            stored = session.get(Claim, claim_id)
            assert stored is not None
            assert stored.claim_type is ClaimType.REGULATORY_APPROVAL

    def test_update_is_sanitised_too(self, run_id: str, captured_parameters: list[Any]) -> None:
        """The guard covers `session.dirty`, not only inserts."""
        with session_scope() as session:
            run = session.get(AnalysisRun, run_id)
            assert run is not None
            claim = Claim(
                run_id=run_id,
                document_id=run.document_id,
                statement="clean on insert",
                verbatim_quote="",
                page_number=1,
            )
            session.add(claim)
            session.flush()
            claim_id = claim.id

        with session_scope() as session:
            stored = session.get(Claim, claim_id)
            assert stored is not None
            stored.statement = "dirtied\x00 later"
            session.flush()

        assert_no_nul_reached_the_driver(captured_parameters)

        with session_scope() as session:
            reloaded = session.get(Claim, claim_id)
            assert reloaded is not None
            assert reloaded.statement == "dirtied later"


class TestOtherWritePaths:
    """The guard is centralised, so it must hold for every table."""

    def test_assessment_rationales_are_sanitised(self, run_id: str) -> None:
        with session_scope() as session:
            run = session.get(AnalysisRun, run_id)
            assert run is not None
            claim = Claim(
                run_id=run_id,
                document_id=run.document_id,
                statement="s",
                verbatim_quote="",
                page_number=1,
            )
            session.add(claim)
            session.flush()
            session.add(
                ClaimAssessment(
                    run_id=run_id,
                    claim_id=claim.id,
                    corroboration_rationale="not in Drugs@FDA\x00",
                    score_explanation="prior of 82\x00/100",
                    verdict="the company asserts\x00",
                    key_uncertainties=["whether FDA granted\x00 a BLA"],
                )
            )
            session.flush()
            claim_id = claim.id

        with session_scope() as session:
            stored = session.execute(
                ClaimAssessment.__table__.select().where(
                    ClaimAssessment.__table__.c.claim_id == claim_id
                )
            ).one()
            assert "\x00" not in stored.corroboration_rationale
            assert "\x00" not in stored.score_explanation
            assert "\x00" not in stored.verdict
            assert all("\x00" not in item for item in stored.key_uncertainties)

    def test_questions_and_risks_are_sanitised(self, run_id: str) -> None:
        with session_scope() as session:
            session.add(
                DiligenceQuestion(
                    run_id=run_id,
                    priority=QuestionPriority.CRITICAL,
                    question="Provide the approval letter\x00",
                    rationale="unverified\x00",
                    what_good_looks_like="a copy of the letter\x00",
                    rank=1,
                )
            )
            session.add(
                RiskFlag(
                    run_id=run_id,
                    category=RiskCategory.SCIENTIFIC,
                    severity=RiskSeverity.HIGH,
                    title="Thesis-critical claim conflicts\x00",
                    description="the retrieved evidence disagrees\x00",
                    basis="rule\x00",
                )
            )
            session.flush()

        with session_scope() as session:
            question = session.execute(DiligenceQuestion.__table__.select()).one()
            risk = session.execute(RiskFlag.__table__.select()).one()

        assert "\x00" not in question.question
        assert "\x00" not in question.rationale
        assert "\x00" not in question.what_good_looks_like
        assert "\x00" not in risk.title
        assert "\x00" not in risk.description
        assert "\x00" not in risk.basis

    def test_clean_writes_are_untouched(self, run_id: str) -> None:
        """The guard must be invisible when there is nothing to strip."""
        statement = "Moderna's CMV vaccine program mRNA-1647 is in Phase 3."
        with session_scope() as session:
            run = session.get(AnalysisRun, run_id)
            assert run is not None
            claim = Claim(
                run_id=run_id,
                document_id=run.document_id,
                statement=statement,
                verbatim_quote="CMV vaccine mRNA-1647 bar extends to Phase 3",
                page_number=23,
                quantitative=[{"metric": "phase", "value": 3}],
                review_reasons=[],
            )
            session.add(claim)
            session.flush()
            claim_id = claim.id

        with session_scope() as session:
            stored = session.get(Claim, claim_id)
            assert stored is not None
            assert stored.statement == statement
            assert stored.quantitative == [{"metric": "phase", "value": 3}]
