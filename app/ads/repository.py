"""SQLAlchemy persistence for the ads tables."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Date,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    func,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.ads.domain import (
    AccountStatus,
    AdAccount,
    Campaign,
    DailyRow,
    SyncKind,
    SyncOutcome,
    SyncStatus,
)
from app.shared.clock import now_utc
from app.shared.db import Base, CreatedAt, EnumAsString, Timestamptz, UpdatedAt, UuidPk


class AccountRow(Base):
    """Meta ids are the primary key: they are already unique and stable."""

    __tablename__ = "ads_account"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    client_id: Mapped[UUID] = mapped_column(index=True)
    name: Mapped[str] = mapped_column(String(160), default="")
    status: Mapped[AccountStatus] = mapped_column(
        EnumAsString(AccountStatus, 16), default=AccountStatus.ACTIVE, index=True
    )
    currency: Mapped[str] = mapped_column(String(8), default="BRL")
    last_synced_at: Mapped[Timestamptz | None] = mapped_column(default=None)
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[UpdatedAt]


class CampaignRow(Base):
    __tablename__ = "ads_campaign"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    account_id: Mapped[str] = mapped_column(
        ForeignKey("ads_account.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(32), default="UNKNOWN")
    objective: Mapped[str | None] = mapped_column(String(64), default=None)
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[UpdatedAt]


class CampaignDailyRow(Base):
    """One campaign on one day. The key is the pair, so a resync overwrites."""

    __tablename__ = "ads_campaign_daily"
    __table_args__ = (Index("ix_ads_campaign_daily_account_date", "account_id", "date"),)

    date: Mapped[date] = mapped_column(Date, primary_key=True)
    campaign_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    account_id: Mapped[str] = mapped_column(
        ForeignKey("ads_account.id", ondelete="CASCADE"), index=True
    )
    spend: Mapped[Decimal] = mapped_column(Numeric(14, 2), default=Decimal("0.00"))
    impressions: Mapped[int] = mapped_column(BigInteger, default=0)
    reach: Mapped[int] = mapped_column(BigInteger, default=0)
    link_clicks: Mapped[int] = mapped_column(BigInteger, default=0)
    results: Mapped[int] = mapped_column(BigInteger, default=0)
    # The raw numbers stay, so a client can change what counts as a result
    # and the past can be recounted without asking Meta again.
    actions: Mapped[dict[str, int]] = mapped_column(JSONB, default=dict)
    action_values: Mapped[dict[str, str]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[UpdatedAt]


class SyncLogRow(Base):
    __tablename__ = "sync_log"
    __table_args__ = (Index("ix_sync_log_started_at", "started_at"),)

    id: Mapped[UuidPk]
    account_id: Mapped[str] = mapped_column(String(40), index=True)
    kind: Mapped[SyncKind] = mapped_column(EnumAsString(SyncKind, 16))
    status: Mapped[SyncStatus] = mapped_column(EnumAsString(SyncStatus, 16), index=True)
    rows_written: Mapped[int] = mapped_column(BigInteger, default=0)
    message: Mapped[str | None] = mapped_column(Text, default=None)
    started_at: Mapped[Timestamptz]
    finished_at: Mapped[Timestamptz]
    created_at: Mapped[CreatedAt]


def account_to_domain(row: AccountRow) -> AdAccount:
    return AdAccount(
        id=row.id,
        client_id=row.client_id,
        name=row.name,
        status=AccountStatus(row.status),
        currency=row.currency,
        last_synced_at=row.last_synced_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def campaign_to_domain(row: CampaignRow) -> Campaign:
    return Campaign(
        id=row.id,
        account_id=row.account_id,
        name=row.name,
        status=row.status,
        objective=row.objective,
    )


class SqlAlchemyAdsRepository:
    """Implements :class:`app.ads.ports.AdsRepository`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ── contas ────────────────────────────────────────────────

    async def upsert_account(
        self,
        account_id: str,
        *,
        client_id: UUID,
        name: str,
        status: AccountStatus,
    ) -> AdAccount:
        row = await self._session.get(AccountRow, account_id)
        if row is None:
            row = AccountRow(id=account_id)
            self._session.add(row)
        row.client_id = client_id
        row.name = name
        row.status = status
        await self._session.flush()
        await self._session.refresh(row)
        return account_to_domain(row)

    async def get_account(self, account_id: str) -> AdAccount | None:
        row = await self._session.get(AccountRow, account_id)
        return account_to_domain(row) if row else None

    async def list_accounts(
        self, *, client_id: UUID | None = None, only_active: bool = False
    ) -> list[AdAccount]:
        statement = select(AccountRow).order_by(AccountRow.created_at)
        if client_id is not None:
            statement = statement.where(AccountRow.client_id == client_id)
        if only_active:
            statement = statement.where(AccountRow.status == AccountStatus.ACTIVE)
        rows = (await self._session.execute(statement)).scalars()
        return [account_to_domain(row) for row in rows]

    async def delete_account(self, account_id: str) -> bool:
        row = await self._session.get(AccountRow, account_id)
        if row is None:
            return False
        await self._session.delete(row)
        await self._session.flush()
        return True

    async def touch_account(self, account_id: str, *, moment: datetime) -> None:
        row = await self._session.get(AccountRow, account_id)
        if row is None:
            return
        row.last_synced_at = moment
        await self._session.flush()

    # ── campanhas ─────────────────────────────────────────────

    async def upsert_campaigns(self, campaigns: list[Campaign]) -> int:
        if not campaigns:
            return 0

        moment = now_utc()
        values = [
            {
                "id": campaign.id,
                "account_id": campaign.account_id,
                "name": campaign.name,
                "status": campaign.status,
                "objective": campaign.objective,
                "created_at": moment,
                "updated_at": moment,
            }
            for campaign in {campaign.id: campaign for campaign in campaigns}.values()
        ]
        statement = insert(CampaignRow).values(values)
        statement = statement.on_conflict_do_update(
            index_elements=[CampaignRow.id],
            set_={
                "account_id": statement.excluded.account_id,
                "name": statement.excluded.name,
                "status": statement.excluded.status,
                "objective": statement.excluded.objective,
                "updated_at": moment,
            },
        )
        await self._session.execute(statement)
        return len(values)

    async def list_campaigns(self, account_id: str) -> list[Campaign]:
        rows = (
            await self._session.execute(
                select(CampaignRow)
                .where(CampaignRow.account_id == account_id)
                .order_by(CampaignRow.name)
            )
        ).scalars()
        return [campaign_to_domain(row) for row in rows]

    # ── métricas diárias ──────────────────────────────────────

    async def upsert_daily_rows(self, rows: list[DailyRow]) -> int:
        """A day already stored is replaced: Meta keeps adjusting it."""
        if not rows:
            return 0

        moment = now_utc()
        unique = {(row.day, row.campaign_id): row for row in rows}
        values = [
            {
                "date": row.day,
                "campaign_id": row.campaign_id,
                "account_id": row.account_id,
                "spend": row.spend,
                "impressions": row.impressions,
                "reach": row.reach,
                "link_clicks": row.link_clicks,
                "results": row.results,
                "actions": row.actions,
                "action_values": {key: str(value) for key, value in row.action_values.items()},
                "created_at": moment,
                "updated_at": moment,
            }
            for row in unique.values()
        ]
        statement = insert(CampaignDailyRow).values(values)
        statement = statement.on_conflict_do_update(
            index_elements=[CampaignDailyRow.date, CampaignDailyRow.campaign_id],
            set_={
                "account_id": statement.excluded.account_id,
                "spend": statement.excluded.spend,
                "impressions": statement.excluded.impressions,
                "reach": statement.excluded.reach,
                "link_clicks": statement.excluded.link_clicks,
                "results": statement.excluded.results,
                "actions": statement.excluded.actions,
                "action_values": statement.excluded.action_values,
                "updated_at": moment,
            },
        )
        await self._session.execute(statement)
        return len(values)

    async def count_daily_rows(self, account_id: str) -> int:
        return (
            await self._session.execute(
                select(func.count())
                .select_from(CampaignDailyRow)
                .where(CampaignDailyRow.account_id == account_id)
            )
        ).scalar_one()

    async def daily_rows_between(
        self, account_id: str, since: date, until: date
    ) -> list[CampaignDailyRow]:
        rows = (
            await self._session.execute(
                select(CampaignDailyRow)
                .where(
                    CampaignDailyRow.account_id == account_id,
                    CampaignDailyRow.date >= since,
                    CampaignDailyRow.date <= until,
                )
                .order_by(CampaignDailyRow.date)
            )
        ).scalars()
        return list(rows)

    # ── registro das sincronizações ───────────────────────────

    async def record_sync(
        self,
        *,
        account_id: str,
        kind: SyncKind,
        status: SyncStatus,
        rows_written: int,
        message: str | None,
        started_at: datetime,
    ) -> None:
        self._session.add(
            SyncLogRow(
                id=uuid.uuid4(),
                account_id=account_id,
                kind=kind,
                status=status,
                rows_written=rows_written,
                message=(message or None),
                started_at=started_at,
                finished_at=now_utc(),
            )
        )
        await self._session.flush()

    async def recent_syncs(self, *, limit: int = 50) -> list[SyncOutcome]:
        rows = (
            await self._session.execute(
                select(SyncLogRow).order_by(SyncLogRow.started_at.desc()).limit(limit)
            )
        ).scalars()
        return [
            SyncOutcome(
                account_id=row.account_id,
                kind=SyncKind(row.kind),
                status=SyncStatus(row.status),
                rows_written=row.rows_written,
                message=row.message,
            )
            for row in rows
        ]

    async def recent_sync_rows(self, *, limit: int = 50) -> list[SyncLogRow]:
        """The log with its timestamps, for the screen."""
        rows = (
            await self._session.execute(
                select(SyncLogRow).order_by(SyncLogRow.started_at.desc()).limit(limit)
            )
        ).scalars()
        return list(rows)
