"""The only surface other modules may call."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.clients import ClientsApi
from app.reports.adapters.ads_numbers import AdsApiNumbers
from app.reports.domain import ClientReports, MonthPeriod, Report
from app.reports.render import snapshot_to_pdf
from app.reports.repository import SqlAlchemyReportsRepository
from app.reports.service import ReportsService


def build_service(session: AsyncSession) -> ReportsService:
    """Wire the module with the caller's session. Used here and by ``web``."""
    return ReportsService(
        SqlAlchemyReportsRepository(session),
        AdsApiNumbers(session),
        ClientsApi(session),
    )


class ReportsApi:
    """Facade over the reports module, built with the caller's session."""

    def __init__(self, session: AsyncSession) -> None:
        self._service = build_service(session)

    async def generate_monthly_report(
        self,
        *,
        client_id: UUID,
        year: int,
        month: int,
        comment: str = "",
        generated_by: str = "",
    ) -> Report:
        return await self._service.generate_monthly_report(
            client_id=client_id,
            period=MonthPeriod(year, month),
            comment=comment,
            generated_by=generated_by,
        )

    async def get_report(self, report_id: UUID) -> Report | None:
        return await self._service.get_report(report_id)

    async def list_for_client(self, client_id: UUID, *, limit: int = 100) -> list[Report]:
        return await self._service.list_for_client(client_id, limit=limit)

    async def list_recent(self, *, limit: int = 50) -> list[Report]:
        return await self._service.list_recent(limit=limit)

    async def client_rows(self) -> list[ClientReports]:
        return await self._service.client_rows()

    async def render_pdf(self, report_id: UUID) -> tuple[str, bytes] | None:
        """The file name and the bytes, rebuilt from the stored snapshot."""
        report = await self._service.get_report(report_id)
        if report is None:
            return None
        return report.pdf_filename, snapshot_to_pdf(report.snapshot)
