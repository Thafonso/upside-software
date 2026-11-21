"""Ads use cases. Repository, gateway and client lookup come by injection."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Iterable
from datetime import date
from decimal import Decimal
from uuid import UUID

from app.ads.domain import (
    RESYNC_DAYS,
    AccountStatus,
    AdAccount,
    CampaignTotals,
    ClientPanelRow,
    DailyPoint,
    Period,
    SyncKind,
    SyncOutcome,
    SyncStatus,
    Totals,
    sync_window,
    to_daily_row,
)
from app.ads.ports import (
    AdsGatewayError,
    AdsPlatformGateway,
    AdsRepository,
    ClientDirectory,
    RateLimited,
)
from app.shared.clock import now_utc, today_local

logger = logging.getLogger("app.ads")

# Used only if a client vanished between linking and syncing.
FALLBACK_RESULT_ACTION_TYPE = "lead"


class AccountNotLinked(LookupError):
    """No ad account with that id is linked to a client."""


class UnknownClient(LookupError):
    """The account points at a client that does not exist."""


class AdsService:
    def __init__(self, repository: AdsRepository, clients: ClientDirectory) -> None:
        self._repository = repository
        self._clients = clients

    # ── vínculo conta ↔ cliente ───────────────────────────────

    async def link_account(
        self,
        account_id: str,
        *,
        client_id: UUID,
        name: str = "",
        status: AccountStatus = AccountStatus.ACTIVE,
    ) -> AdAccount:
        """Point an ad account at a client. Linking again just updates it."""
        client = await self._clients.get_client(client_id)
        if client is None:
            raise UnknownClient(str(client_id))

        return await self._repository.upsert_account(
            account_id,
            client_id=client_id,
            name=name or client.name,
            status=status,
        )

    async def get_account(self, account_id: str) -> AdAccount | None:
        return await self._repository.get_account(account_id)

    async def list_accounts(
        self, *, client_id: UUID | None = None, only_active: bool = False
    ) -> list[AdAccount]:
        return await self._repository.list_accounts(client_id=client_id, only_active=only_active)

    async def unlink_account(self, account_id: str) -> bool:
        """Drop the link and everything synced for it."""
        return await self._repository.delete_account(account_id)

    async def recent_syncs(self, *, limit: int = 50) -> list[SyncOutcome]:
        return await self._repository.recent_syncs(limit=limit)

    async def record_failure(self, account_id: str, kind: SyncKind, message: str) -> SyncOutcome:
        """Log a sync that never got off the ground, such as a missing token.

        Without this the admin would see nothing on the screen while the
        queue quietly retried the same misconfiguration.
        """
        await self._repository.record_sync(
            account_id=account_id,
            kind=kind,
            status=SyncStatus.FAILED,
            rows_written=0,
            message=message[:500],
            started_at=now_utc(),
        )
        return SyncOutcome(
            account_id=account_id,
            kind=kind,
            status=SyncStatus.FAILED,
            message=message[:500],
        )

    # ── sincronização ─────────────────────────────────────────

    async def sync_structure(self, account_id: str, gateway: AdsPlatformGateway) -> SyncOutcome:
        """Refresh campaign names and statuses."""

        async def work(account: AdAccount) -> int:
            campaigns = await gateway.list_campaigns(account.id)
            return await self._repository.upsert_campaigns(campaigns)

        return await self._run(account_id, SyncKind.STRUCTURE, work)

    async def sync_day(
        self, account_id: str, gateway: AdsPlatformGateway, *, day: date | None = None
    ) -> SyncOutcome:
        """Today's numbers, which Meta is still adding to."""
        target = day or today_local()
        return await self._sync_range(account_id, gateway, target, target, SyncKind.DAILY)

    async def resync_recent(
        self, account_id: str, gateway: AdsPlatformGateway, *, days: int = RESYNC_DAYS
    ) -> SyncOutcome:
        """The last few days again: Meta keeps adjusting them after the fact."""
        since, until = sync_window(today_local(), days=days)
        return await self._sync_range(account_id, gateway, since, until, SyncKind.RESYNC)

    async def _sync_range(
        self,
        account_id: str,
        gateway: AdsPlatformGateway,
        since: date,
        until: date,
        kind: SyncKind,
    ) -> SyncOutcome:
        async def work(account: AdAccount) -> int:
            client = await self._clients.get_client(account.client_id)
            result_action_type = (
                client.result_action_type if client else FALLBACK_RESULT_ACTION_TYPE
            )

            insights = await gateway.daily_insights(account.id, since, until)
            rows = [
                to_daily_row(
                    insight,
                    account_id=account.id,
                    result_action_type=result_action_type,
                )
                for insight in insights
            ]
            return await self._repository.upsert_daily_rows(rows)

        return await self._run(account_id, kind, work)

    async def _run(
        self,
        account_id: str,
        kind: SyncKind,
        work: Callable[[AdAccount], Awaitable[int]],
    ) -> SyncOutcome:
        """Run one sync, write it to the log, and never swallow a throttle."""
        started_at = now_utc()
        account = await self._repository.get_account(account_id)
        if account is None:
            raise AccountNotLinked(account_id)

        try:
            rows_written = await work(account)
        except RateLimited:
            # Not a failure: the queue will come back. Nothing is logged, or
            # the log would fill with waits instead of outcomes.
            raise
        except AdsGatewayError as exc:
            await self._repository.record_sync(
                account_id=account_id,
                kind=kind,
                status=SyncStatus.FAILED,
                rows_written=0,
                message=str(exc)[:500],
                started_at=started_at,
            )
            logger.warning("sync %s failed for %s: %s", kind.value, account_id, exc)
            return SyncOutcome(
                account_id=account_id,
                kind=kind,
                status=SyncStatus.FAILED,
                message=str(exc)[:500],
            )

        await self._repository.touch_account(account_id, moment=now_utc())
        await self._repository.record_sync(
            account_id=account_id,
            kind=kind,
            status=SyncStatus.OK,
            rows_written=rows_written,
            message=None,
            started_at=started_at,
        )
        logger.info("sync %s wrote %s row(s) for %s", kind.value, rows_written, account_id)
        return SyncOutcome(
            account_id=account_id,
            kind=kind,
            status=SyncStatus.OK,
            rows_written=rows_written,
        )

    # ── painel ────────────────────────────────────────────────

    async def panel_rows(self, period: Period) -> list[ClientPanelRow]:
        """One row per client that has an ad account, biggest spender first."""
        accounts = await self._repository.list_accounts()
        if not accounts:
            return []

        by_account = await self._repository.totals_by_account(period.since, period.until)

        grouped: dict[UUID, list[AdAccount]] = {}
        for account in accounts:
            grouped.setdefault(account.client_id, []).append(account)

        rows: list[ClientPanelRow] = []
        for client_id, owned in grouped.items():
            client = await self._clients.get_client(client_id)
            totals = _add_up(by_account.get(account.id, Totals()) for account in owned)
            rows.append(
                ClientPanelRow(
                    client_id=client_id,
                    client_name=client.name if client else "Cliente removido",
                    account_ids=tuple(account.id for account in owned),
                    totals=totals,
                )
            )

        return sorted(rows, key=lambda row: (-row.totals.spend, row.client_name.lower()))

    async def client_totals(self, client_id: UUID, period: Period) -> Totals:
        return await self._repository.totals_for(
            await self._account_ids(client_id), period.since, period.until
        )

    async def client_series(self, client_id: UUID, period: Period) -> list[DailyPoint]:
        return await self._repository.daily_series(
            await self._account_ids(client_id), period.since, period.until
        )

    async def client_campaigns(self, client_id: UUID, period: Period) -> list[CampaignTotals]:
        return await self._repository.campaign_totals(
            await self._account_ids(client_id), period.since, period.until
        )

    async def _account_ids(self, client_id: UUID) -> tuple[str, ...]:
        accounts = await self._repository.list_accounts(client_id=client_id)
        return tuple(account.id for account in accounts)


def _add_up(totals: Iterable[Totals]) -> Totals:
    """Add several accounts of one client together."""
    spend = Decimal("0.00")
    results = impressions = reach = link_clicks = 0
    for item in totals:
        spend += item.spend
        results += item.results
        impressions += item.impressions
        reach += item.reach
        link_clicks += item.link_clicks
    return Totals(
        spend=spend,
        results=results,
        impressions=impressions,
        reach=reach,
        link_clicks=link_clicks,
    )
