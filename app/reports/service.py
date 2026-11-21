"""Report use cases. Repository, ads numbers and client lookup come by injection."""

from __future__ import annotations

import logging
from uuid import UUID

from app.reports.domain import (
    ClientReports,
    MonthPeriod,
    Report,
    ReportTally,
    Snapshot,
    build_snapshot,
)
from app.reports.ports import AdsNumbers, ClientDirectory, ReportsRepository
from app.shared.clock import now_local, today_local
from app.shared.events import ReportGenerated, publish

logger = logging.getLogger("app.reports")


class UnknownClient(LookupError):
    """The report was asked for a client that does not exist."""


class ReportsService:
    def __init__(
        self,
        repository: ReportsRepository,
        ads: AdsNumbers,
        clients: ClientDirectory,
    ) -> None:
        self._repository = repository
        self._ads = ads
        self._clients = clients

    # ── GenerateMonthlyReport ─────────────────────────────────

    async def generate_monthly_report(
        self,
        *,
        client_id: UUID,
        period: MonthPeriod,
        comment: str = "",
        generated_by: str = "",
    ) -> Report:
        """Read the month through the ads API, freeze it, store it.

        Everything the PDF will ever show is decided here. Nothing is read
        from the ads tables again afterwards.
        """
        client = await self._clients.get_client(client_id)
        if client is None:
            raise UnknownClient(str(client_id))

        account_ids = await self._ads.account_ids(client_id)
        previous = period.previous()

        rows = await self._ads.daily_rows(account_ids, period.since, period.until)
        previous_rows = await self._ads.daily_rows(account_ids, previous.since, previous.until)
        campaigns = await self._ads.campaigns(account_ids)

        snapshot = build_snapshot(
            client_name=client.name,
            result_label=client.result_label,
            period=period,
            rows=rows,
            previous_rows=previous_rows,
            campaigns=campaigns,
            account_ids=account_ids,
            comment=comment,
            generated_at=now_local(),
            generated_by=generated_by,
            today=today_local(),
        )

        report = await self._repository.create(
            client_id=client_id,
            year=period.year,
            month=period.month,
            comment=comment,
            snapshot=snapshot,
            generated_by=generated_by,
        )

        publish(ReportGenerated(report_id=report.id))
        logger.info(
            "report %s generated for %s (%s), %s campaign(s)",
            report.id,
            client.name,
            period.short_label,
            len(snapshot.campaigns),
        )
        return report

    # ── leitura ───────────────────────────────────────────────

    async def get_report(self, report_id: UUID) -> Report | None:
        return await self._repository.get(report_id)

    async def list_for_client(self, client_id: UUID, *, limit: int = 100) -> list[Report]:
        return await self._repository.list_for_client(client_id, limit=limit)

    async def list_recent(self, *, limit: int = 50) -> list[Report]:
        return await self._repository.list_recent(limit=limit)

    async def reports_in_month(self, client_id: UUID, period: MonthPeriod) -> int:
        """How many reports this client already has for that month."""
        return await self._repository.count_for_month(client_id, period.year, period.month)

    async def client_rows(self) -> list[ClientReports]:
        """One row per client, the ones with reports first."""
        tallies = await self._repository.tally_by_client()
        clients = await self._clients.list_clients()

        rows = [
            ClientReports(
                client_id=client.id,
                client_name=client.name,
                tally=tallies.get(client.id, ReportTally()),
            )
            for client in clients
        ]
        return sorted(
            rows,
            key=lambda row: (not row.has_reports, row.client_name.lower()),
        )

    async def snapshot_of(self, report_id: UUID) -> Snapshot | None:
        report = await self._repository.get(report_id)
        return report.snapshot if report else None
