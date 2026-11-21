"""ads accounts, campaigns, daily metrics and the sync log

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-21

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ads_account",
        # Meta's own id is the key: already unique, already stable.
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("client_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("currency", sa.String(length=8), nullable=False),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name="pk_ads_account"),
    )
    op.create_index("ix_ads_account_client_id", "ads_account", ["client_id"])
    op.create_index("ix_ads_account_status", "ads_account", ["status"])

    op.create_table(
        "ads_campaign",
        sa.Column("id", sa.String(length=40), nullable=False),
        sa.Column("account_id", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("objective", sa.String(length=64), nullable=True),
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
            ["account_id"],
            ["ads_account.id"],
            name="fk_ads_campaign_account_id_ads_account",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_ads_campaign"),
    )
    op.create_index("ix_ads_campaign_account_id", "ads_campaign", ["account_id"])

    op.create_table(
        "ads_campaign_daily",
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("campaign_id", sa.String(length=40), nullable=False),
        sa.Column("account_id", sa.String(length=40), nullable=False),
        sa.Column("spend", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("impressions", sa.BigInteger(), nullable=False),
        sa.Column("reach", sa.BigInteger(), nullable=False),
        sa.Column("link_clicks", sa.BigInteger(), nullable=False),
        sa.Column("results", sa.BigInteger(), nullable=False),
        sa.Column("actions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "action_values", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
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
            ["account_id"],
            ["ads_account.id"],
            name="fk_ads_campaign_daily_account_id_ads_account",
            ondelete="CASCADE",
        ),
        # The pair is the key, so re-syncing a day overwrites it.
        sa.PrimaryKeyConstraint("date", "campaign_id", name="pk_ads_campaign_daily"),
    )
    op.create_index(
        "ix_ads_campaign_daily_account_id", "ads_campaign_daily", ["account_id"]
    )
    op.create_index(
        "ix_ads_campaign_daily_account_date",
        "ads_campaign_daily",
        ["account_id", "date"],
    )

    op.create_table(
        "sync_log",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.String(length=40), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("rows_written", sa.BigInteger(), nullable=False),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_sync_log"),
    )
    op.create_index("ix_sync_log_account_id", "sync_log", ["account_id"])
    op.create_index("ix_sync_log_status", "sync_log", ["status"])
    op.create_index("ix_sync_log_started_at", "sync_log", ["started_at"])


def downgrade() -> None:
    op.drop_index("ix_sync_log_started_at", table_name="sync_log")
    op.drop_index("ix_sync_log_status", table_name="sync_log")
    op.drop_index("ix_sync_log_account_id", table_name="sync_log")
    op.drop_table("sync_log")

    op.drop_index("ix_ads_campaign_daily_account_date", table_name="ads_campaign_daily")
    op.drop_index("ix_ads_campaign_daily_account_id", table_name="ads_campaign_daily")
    op.drop_table("ads_campaign_daily")

    op.drop_index("ix_ads_campaign_account_id", table_name="ads_campaign")
    op.drop_table("ads_campaign")

    op.drop_index("ix_ads_account_status", table_name="ads_account")
    op.drop_index("ix_ads_account_client_id", table_name="ads_account")
    op.drop_table("ads_account")
