"""Interfaces the ads service depends on."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from typing import Protocol
from uuid import UUID

from app.ads.domain import (
    AccountStatus,
    AdAccount,
    Campaign,
    CampaignTotals,
    DailyInsight,
    DailyPoint,
    DailyRow,
    SyncKind,
    SyncOutcome,
    SyncStatus,
    Totals,
)


class AdsGatewayError(RuntimeError):
    """Meta could not be reached or answered something unusable."""


class RateLimited(AdsGatewayError):
    """Meta asked us to slow down. ``retry_after`` is in seconds."""

    def __init__(self, retry_after: int, detail: str = "") -> None:
        self.retry_after = max(int(retry_after), 1)
        super().__init__(detail or f"rate limited, retry in {self.retry_after}s")


class AdsPlatformGateway(Protocol):
    """Reads an ad platform. Implemented by ``adapters.meta_graph``.

    Read-only: nothing in this contract changes anything at Meta.
    """

    async def list_campaigns(self, account_id: str) -> list[Campaign]: ...

    async def daily_insights(
        self, account_id: str, since: date, until: date
    ) -> list[DailyInsight]: ...


class ClientProfile(Protocol):
    """The one thing the ads module needs from a client: what counts."""

    @property
    def result_action_type(self) -> str: ...

    @property
    def name(self) -> str: ...


class ClientDirectory(Protocol):
    """Looking a client up.

    Structural on purpose: ``app.clients.ClientsApi`` satisfies it without
    this module importing anything from the clients module.
    """

    async def get_client(self, client_id: UUID) -> ClientProfile | None: ...


class AdsRepository(Protocol):
    """Persistence contract. Implemented by ``app.ads.repository``."""

    async def upsert_account(
        self,
        account_id: str,
        *,
        client_id: UUID,
        name: str,
        status: AccountStatus,
    ) -> AdAccount: ...

    async def get_account(self, account_id: str) -> AdAccount | None: ...

    async def list_accounts(
        self, *, client_id: UUID | None = None, only_active: bool = False
    ) -> list[AdAccount]: ...

    async def delete_account(self, account_id: str) -> bool: ...

    async def touch_account(self, account_id: str, *, moment: datetime) -> None: ...

    async def upsert_campaigns(self, campaigns: list[Campaign]) -> int: ...

    async def upsert_daily_rows(self, rows: list[DailyRow]) -> int: ...

    async def record_sync(
        self,
        *,
        account_id: str,
        kind: SyncKind,
        status: SyncStatus,
        rows_written: int,
        message: str | None,
        started_at: datetime,
    ) -> None: ...

    async def recent_syncs(self, *, limit: int = 50) -> list[SyncOutcome]: ...

    async def totals_for(self, account_ids: Sequence[str], since: date, until: date) -> Totals: ...

    async def daily_series(
        self, account_ids: Sequence[str], since: date, until: date
    ) -> list[DailyPoint]: ...

    async def campaign_totals(
        self, account_ids: Sequence[str], since: date, until: date
    ) -> list[CampaignTotals]: ...

    async def totals_by_account(self, since: date, until: date) -> dict[str, Totals]: ...
