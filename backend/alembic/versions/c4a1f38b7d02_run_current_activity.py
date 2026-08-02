"""Record what a run is currently doing, not only which stage it is in

The report stage takes minutes and reports as a single unit of work, so the UI
could say "Report" and nothing else for the whole of it. Stage identity is too
coarse to distinguish "writing the executive summary" from "hung": both look
identical to someone watching.

``current_activity`` carries a short human-readable description of the sub-step
in flight. It is advisory display state -- nullable, never read by the
pipeline, and cleared implicitly by the next write -- so an older writer that
does not set it degrades to exactly the previous behaviour.

Revision ID: c4a1f38b7d02
Revises: 9c1f4b7a2d38
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c4a1f38b7d02"
down_revision = "9c1f4b7a2d38"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "analysis_runs",
        sa.Column("current_activity", sa.String(length=160), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("analysis_runs", "current_activity")
