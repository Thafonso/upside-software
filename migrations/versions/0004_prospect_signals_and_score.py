"""prospect signals and score

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-21

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "hunter_prospect", sa.Column("whatsapp", sa.String(length=15), nullable=True)
    )
    op.add_column(
        "hunter_prospect",
        sa.Column(
            "website_type",
            sa.String(length=10),
            nullable=False,
            server_default="none",
        ),
    )
    op.add_column(
        "hunter_prospect", sa.Column("has_meta_pixel", sa.Boolean(), nullable=True)
    )
    op.add_column(
        "hunter_prospect", sa.Column("has_instagram", sa.Boolean(), nullable=True)
    )
    op.add_column(
        "hunter_prospect", sa.Column("has_whatsapp_link", sa.Boolean(), nullable=True)
    )
    op.add_column(
        "hunter_prospect",
        sa.Column(
            "site_unreachable", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
    )
    op.add_column(
        "hunter_prospect",
        sa.Column("site_checked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "hunter_prospect",
        sa.Column("score", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "hunter_prospect",
        sa.Column(
            "score_details",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="{}",
        ),
    )

    op.create_index("ix_hunter_prospect_score", "hunter_prospect", ["score"])
    op.create_index(
        "ix_hunter_prospect_website_type", "hunter_prospect", ["website_type"]
    )

    # The defaults exist so rows written before this revision stay valid;
    # from here on the application always supplies every value.
    op.alter_column("hunter_prospect", "website_type", server_default=None)
    op.alter_column("hunter_prospect", "site_unreachable", server_default=None)
    op.alter_column("hunter_prospect", "score", server_default=None)
    op.alter_column("hunter_prospect", "score_details", server_default=None)


def downgrade() -> None:
    op.drop_index("ix_hunter_prospect_website_type", table_name="hunter_prospect")
    op.drop_index("ix_hunter_prospect_score", table_name="hunter_prospect")
    for column in (
        "score_details",
        "score",
        "site_checked_at",
        "site_unreachable",
        "has_whatsapp_link",
        "has_instagram",
        "has_meta_pixel",
        "website_type",
        "whatsapp",
    ):
        op.drop_column("hunter_prospect", column)
