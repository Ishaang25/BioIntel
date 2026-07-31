"""Convert StringList columns from json to jsonb

PostgreSQL's ``json`` type stores the document verbatim and has no equality
operator, so any ``SELECT DISTINCT``, ``UNION`` or ``GROUP BY`` touching such a
column fails with::

    could not identify an equality operator for type json

``app.db.base.StringList`` declared plain ``JSON``, which left fourteen columns
across eight tables in that state. ``/runs/{id}/evidence`` was the query that
found it. The query has been corrected to de-duplicate on the id alone, but the
columns are fixed here as well so the next ``DISTINCT`` written against any of
these tables cannot reintroduce it.

The cast is safe in both directions: every value is a JSON array of strings, and
``jsonb`` accepts anything ``json`` holds. Going back loses only key ordering
and insignificant whitespace, neither of which this application depends on.

SQLite has no json/jsonb distinction, so this is a no-op there.

Revision ID: 9c1f4b7a2d38
Revises: 7ae42a9019df
"""

from __future__ import annotations

from alembic import op

revision = "9c1f4b7a2d38"
down_revision = "7ae42a9019df"
branch_labels = None
depends_on = None

#: (table, column) for every StringList column in the schema.
STRING_LIST_COLUMNS: list[tuple[str, str]] = [
    ("evidence_items", "authors"),
    ("evidence_items", "publication_types"),
    ("evidence_items", "mesh_terms"),
    ("evidence_items", "keywords"),
    ("claims", "review_reasons"),
    ("company_profiles", "partnerships"),
    ("diligence_questions", "related_claim_ids"),
    ("diligence_questions", "related_evidence_ids"),
    ("entities", "aliases"),
    ("reports", "limitations"),
    ("claim_assessments", "verification_identifiers"),
    ("claim_assessments", "key_uncertainties"),
    ("claim_evidence_links", "caveats"),
    ("risk_flags", "evidence_ids"),
]


def _convert(target_type: str) -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    for table, column in STRING_LIST_COLUMNS:
        op.execute(
            f'ALTER TABLE "{table}" '
            f'ALTER COLUMN "{column}" TYPE {target_type} '
            f'USING "{column}"::{target_type}'
        )


def upgrade() -> None:
    _convert("jsonb")


def downgrade() -> None:
    _convert("json")
