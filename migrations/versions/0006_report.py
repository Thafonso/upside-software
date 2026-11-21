"""monthly reports with a frozen snapshot

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-21

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "report",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("client_id", sa.Uuid(), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("month", sa.Integer(), nullable=False),
        sa.Column("comment", sa.Text(), nullable=False),
        # Everything the PDF shows. Written once, never updated: that is what
        # makes reopening a report give the same document.
        sa.Column("snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("generated_by", sa.String(length=160), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("month >= 1 AND month <= 12", name="ck_report_month_range"),
        sa.PrimaryKeyConstraint("id", name="pk_report"),
    )
    op.create_index("ix_report_client_id", "report", ["client_id"])
    op.create_index("ix_report_client_period", "report", ["client_id", "year", "month"])


def downgrade() -> None:
    op.drop_index("ix_report_client_period", table_name="report")
    op.drop_index("ix_report_client_id", table_name="report")
    op.drop_table("report")
