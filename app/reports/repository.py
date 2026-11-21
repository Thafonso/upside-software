"""SQLAlchemy persistence for the ``report`` table.

The snapshot is the row's point: once written it is never updated, so the
same PDF can be reopened months later and still say what it said.
"""

from __future__ import annotations

import uuid
from typing import Any
from uuid import UUID

from sqlalchemy import CheckConstraint, Index, Integer, String, Text, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.reports.domain import MonthPeriod, Report, ReportTally, Snapshot
from app.shared.db import Base, CreatedAt, UpdatedAt, UuidPk


class ReportRow(Base):
    """One generated report, with its frozen snapshot."""

    __tablename__ = "report"
    __table_args__ = (
        CheckConstraint("month >= 1 AND month <= 12", name="month_range"),
        Index("ix_report_client_period", "client_id", "year", "month"),
    )

    id: Mapped[UuidPk]
    client_id: Mapped[UUID] = mapped_column(index=True)
    year: Mapped[int] = mapped_column(Integer)
    month: Mapped[int] = mapped_column(Integer)
    comment: Mapped[str] = mapped_column(Text, default="")
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    # The name is copied in, not joined: the report is a document, and it
    # should keep saying who signed it even if that account is gone.
    generated_by: Mapped[str] = mapped_column(String(160), default="")
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[UpdatedAt]


def report_to_domain(row: ReportRow) -> Report:
    return Report(
        id=row.id,
        client_id=row.client_id,
        year=row.year,
        month=row.month,
        comment=row.comment,
        snapshot=Snapshot.from_dict(dict(row.snapshot or {})),
        generated_by=row.generated_by,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class SqlAlchemyReportsRepository:
    """Implements :class:`app.reports.ports.ReportsRepository`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(
        self,
        *,
        client_id: UUID,
        year: int,
        month: int,
        comment: str,
        snapshot: Snapshot,
        generated_by: str,
    ) -> Report:
        row = ReportRow(
            id=uuid.uuid4(),
            client_id=client_id,
            year=year,
            month=month,
            comment=comment,
            snapshot=snapshot.to_dict(),
            generated_by=generated_by,
        )
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return report_to_domain(row)

    async def get(self, report_id: UUID) -> Report | None:
        row = await self._session.get(ReportRow, report_id)
        return report_to_domain(row) if row else None

    async def list_for_client(self, client_id: UUID, *, limit: int = 100) -> list[Report]:
        """Newest month first; within a month, the newest run first."""
        rows = (
            await self._session.execute(
                select(ReportRow)
                .where(ReportRow.client_id == client_id)
                .order_by(
                    ReportRow.year.desc(),
                    ReportRow.month.desc(),
                    ReportRow.created_at.desc(),
                )
                .limit(limit)
            )
        ).scalars()
        return [report_to_domain(row) for row in rows]

    async def list_recent(self, *, limit: int = 50) -> list[Report]:
        rows = (
            await self._session.execute(
                select(ReportRow).order_by(ReportRow.created_at.desc()).limit(limit)
            )
        ).scalars()
        return [report_to_domain(row) for row in rows]

    async def count_for_month(self, client_id: UUID, year: int, month: int) -> int:
        return (
            await self._session.execute(
                select(func.count())
                .select_from(ReportRow)
                .where(
                    ReportRow.client_id == client_id,
                    ReportRow.year == year,
                    ReportRow.month == month,
                )
            )
        ).scalar_one()

    async def tally_by_client(self) -> dict[UUID, ReportTally]:
        """How many reports each client has and which month was last."""
        counted = await self._session.execute(
            select(
                ReportRow.client_id,
                func.count().label("total"),
                func.max(ReportRow.created_at),
            ).group_by(ReportRow.client_id)
        )
        totals = {row[0]: (int(row[1]), row[2]) for row in counted}
        if not totals:
            return {}

        # The latest month is not the latest row: a report for October can be
        # generated after one for November. Rank by period, not by timestamp.
        ranked = await self._session.execute(
            select(ReportRow.client_id, ReportRow.year, ReportRow.month)
            .where(ReportRow.client_id.in_(list(totals)))
            .order_by(ReportRow.client_id, ReportRow.year.desc(), ReportRow.month.desc())
        )
        latest: dict[UUID, MonthPeriod] = {}
        for client_id, year, month in ranked:
            latest.setdefault(client_id, MonthPeriod(year, month))

        return {
            client_id: ReportTally(
                total=total,
                latest_period=latest.get(client_id),
                latest_at=latest_at,
            )
            for client_id, (total, latest_at) in totals.items()
        }
