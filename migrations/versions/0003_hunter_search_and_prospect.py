"""hunter search and prospect

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-21

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "hunter_search",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("niches", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("city", sa.String(length=80), nullable=False),
        sa.Column("neighborhood", sa.String(length=80), nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("fetch_emails", sa.Boolean(), nullable=False),
        sa.Column("depth", sa.Integer(), nullable=False),
        sa.Column("max_time_seconds", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("scraper_job_id", sa.String(length=64), nullable=True),
        sa.Column("total_found", sa.Integer(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name="pk_hunter_search"),
    )
    op.create_index("ix_hunter_search_status", "hunter_search", ["status"])
    op.create_index("ix_hunter_search_created_at", "hunter_search", ["created_at"])

    op.create_table(
        "hunter_prospect",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("search_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("maps_link", sa.String(length=2000), nullable=False),
        sa.Column("category", sa.String(length=120), nullable=True),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column("borough", sa.String(length=120), nullable=True),
        sa.Column("city", sa.String(length=120), nullable=True),
        sa.Column("latitude", sa.Numeric(precision=10, scale=7), nullable=True),
        sa.Column("longitude", sa.Numeric(precision=10, scale=7), nullable=True),
        sa.Column("phone", sa.String(length=40), nullable=True),
        sa.Column("website", sa.Text(), nullable=True),
        sa.Column("emails", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("review_rating", sa.Numeric(precision=2, scale=1), nullable=True),
        sa.Column("review_count", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("do_not_contact", sa.Boolean(), nullable=False),
        sa.Column("client_id", sa.Uuid(), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["search_id"],
            ["hunter_search.id"],
            name="fk_hunter_prospect_search_id_hunter_search",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_hunter_prospect"),
    )
    # The dedup key: finding the same place again updates its row.
    op.create_index(
        "ix_hunter_prospect_maps_link", "hunter_prospect", ["maps_link"], unique=True
    )
    op.create_index("ix_hunter_prospect_search_id", "hunter_prospect", ["search_id"])
    op.create_index("ix_hunter_prospect_status", "hunter_prospect", ["status"])


def downgrade() -> None:
    op.drop_index("ix_hunter_prospect_status", table_name="hunter_prospect")
    op.drop_index("ix_hunter_prospect_search_id", table_name="hunter_prospect")
    op.drop_index("ix_hunter_prospect_maps_link", table_name="hunter_prospect")
    op.drop_table("hunter_prospect")
    op.drop_index("ix_hunter_search_created_at", table_name="hunter_search")
    op.drop_index("ix_hunter_search_status", table_name="hunter_search")
    op.drop_table("hunter_search")
