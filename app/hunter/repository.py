"""SQLAlchemy persistence for ``hunter_search`` and ``hunter_prospect``."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.hunter.domain import (
    FoundCompany,
    Prospect,
    ProspectStatus,
    Search,
    SearchInput,
    SearchStatus,
)
from app.shared.clock import now_utc
from app.shared.db import Base, CreatedAt, EnumAsString, Timestamptz, UpdatedAt, UuidPk


class SearchRow(Base):
    __tablename__ = "hunter_search"
    __table_args__ = (
        # The list screen is newest first.
        Index("ix_hunter_search_created_at", "created_at"),
    )

    id: Mapped[UuidPk]
    name: Mapped[str] = mapped_column(String(200))
    niches: Mapped[list[str]] = mapped_column(JSONB, default=list)
    city: Mapped[str] = mapped_column(String(80))
    neighborhood: Mapped[str | None] = mapped_column(String(80), default=None)
    quantity: Mapped[int] = mapped_column(Integer, default=60)
    fetch_emails: Mapped[bool] = mapped_column(Boolean, default=False)
    depth: Mapped[int] = mapped_column(Integer, default=5)
    max_time_seconds: Mapped[int] = mapped_column(Integer, default=1200)
    status: Mapped[SearchStatus] = mapped_column(
        EnumAsString(SearchStatus, 16), default=SearchStatus.QUEUED, index=True
    )
    scraper_job_id: Mapped[str | None] = mapped_column(String(64), default=None)
    total_found: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, default=None)
    started_at: Mapped[Timestamptz | None] = mapped_column(default=None)
    deadline_at: Mapped[Timestamptz | None] = mapped_column(default=None)
    finished_at: Mapped[Timestamptz | None] = mapped_column(default=None)
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[UpdatedAt]


class ProspectRow(Base):
    __tablename__ = "hunter_prospect"

    id: Mapped[UuidPk]
    search_id: Mapped[UUID] = mapped_column(
        ForeignKey("hunter_search.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(200))
    # The dedup key: the same place found again updates instead of duplicating.
    maps_link: Mapped[str] = mapped_column(String(2000), unique=True, index=True)
    category: Mapped[str | None] = mapped_column(String(120), default=None)
    address: Mapped[str | None] = mapped_column(Text, default=None)
    borough: Mapped[str | None] = mapped_column(String(120), default=None)
    city: Mapped[str | None] = mapped_column(String(120), default=None)
    latitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 7), default=None)
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 7), default=None)
    phone: Mapped[str | None] = mapped_column(String(40), default=None)
    website: Mapped[str | None] = mapped_column(Text, default=None)
    emails: Mapped[list[str]] = mapped_column(JSONB, default=list)
    review_rating: Mapped[Decimal | None] = mapped_column(Numeric(2, 1), default=None)
    review_count: Mapped[int] = mapped_column(BigInteger, default=0)
    status: Mapped[ProspectStatus] = mapped_column(
        EnumAsString(ProspectStatus, 16), default=ProspectStatus.NEW, index=True
    )
    do_not_contact: Mapped[bool] = mapped_column(Boolean, default=False)
    client_id: Mapped[UUID | None] = mapped_column(default=None)
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[UpdatedAt]


def search_to_domain(row: SearchRow) -> Search:
    return Search(
        id=row.id,
        name=row.name,
        niches=tuple(row.niches or ()),
        city=row.city,
        neighborhood=row.neighborhood,
        quantity=row.quantity,
        fetch_emails=row.fetch_emails,
        status=SearchStatus(row.status),
        scraper_job_id=row.scraper_job_id,
        total_found=row.total_found,
        error_message=row.error_message,
        depth=row.depth,
        max_time_seconds=row.max_time_seconds,
        started_at=row.started_at,
        deadline_at=row.deadline_at,
        finished_at=row.finished_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def prospect_to_domain(row: ProspectRow) -> Prospect:
    return Prospect(
        id=row.id,
        search_id=row.search_id,
        name=row.name,
        maps_link=row.maps_link,
        category=row.category,
        address=row.address,
        borough=row.borough,
        city=row.city,
        phone=row.phone,
        website=row.website,
        emails=tuple(row.emails or ()),
        review_rating=row.review_rating,
        review_count=row.review_count,
        status=ProspectStatus(row.status),
        do_not_contact=row.do_not_contact,
        client_id=row.client_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


class SqlAlchemyHunterRepository:
    """Implements :class:`app.hunter.ports.HunterRepository`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ── buscas ────────────────────────────────────────────────

    async def create_search(
        self, data: SearchInput, *, depth: int, max_time_seconds: int
    ) -> Search:
        row = SearchRow(
            name=data.display_name,
            niches=list(data.niches),
            city=data.city,
            neighborhood=data.neighborhood,
            quantity=data.quantity,
            fetch_emails=data.fetch_emails,
            depth=depth,
            max_time_seconds=max_time_seconds,
            status=SearchStatus.QUEUED,
        )
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return search_to_domain(row)

    async def get_search(self, search_id: UUID) -> Search | None:
        row = await self._session.get(SearchRow, search_id)
        return search_to_domain(row) if row else None

    async def list_searches(self, *, limit: int = 50) -> list[Search]:
        rows = (
            await self._session.execute(
                select(SearchRow).order_by(SearchRow.created_at.desc()).limit(limit)
            )
        ).scalars()
        return [search_to_domain(row) for row in rows]

    async def count_searches_between(self, start: datetime, end: datetime) -> int:
        return (
            await self._session.execute(
                select(func.count())
                .select_from(SearchRow)
                .where(SearchRow.created_at >= start, SearchRow.created_at < end)
            )
        ).scalar_one()

    async def running_search_id(self, *, excluding: UUID | None = None) -> UUID | None:
        """Which search is at the scraper right now, if any."""
        statement = select(SearchRow.id).where(SearchRow.status == SearchStatus.RUNNING)
        if excluding is not None:
            statement = statement.where(SearchRow.id != excluding)
        return (await self._session.execute(statement.limit(1))).scalar_one_or_none()

    async def mark_running(
        self,
        search_id: UUID,
        *,
        scraper_job_id: str,
        started_at: datetime,
        deadline_at: datetime,
    ) -> None:
        row = await self._session.get(SearchRow, search_id)
        if row is None:
            return
        row.status = SearchStatus.RUNNING
        row.scraper_job_id = scraper_job_id
        row.started_at = started_at
        row.deadline_at = deadline_at
        row.error_message = None
        await self._session.flush()

    async def mark_done(self, search_id: UUID, *, total_found: int) -> None:
        row = await self._session.get(SearchRow, search_id)
        if row is None:
            return
        row.status = SearchStatus.DONE
        row.total_found = total_found
        row.finished_at = now_utc()
        row.error_message = None
        await self._session.flush()

    async def mark_failed(self, search_id: UUID, *, message: str) -> None:
        row = await self._session.get(SearchRow, search_id)
        if row is None:
            return
        row.status = SearchStatus.FAILED
        row.error_message = message
        row.finished_at = now_utc()
        await self._session.flush()

    # ── prospects ─────────────────────────────────────────────

    async def upsert_prospects(self, search_id: UUID, companies: list[FoundCompany]) -> int:
        """Write what a search found.

        A place already in the base keeps the work done on it - its status,
        its ``do_not_contact`` flag and the client it became - and only has
        the scraped data refreshed.
        """
        if not companies:
            return 0

        moment = now_utc()
        values = [
            {
                "id": uuid.uuid4(),
                "search_id": search_id,
                "name": company.name,
                "maps_link": company.maps_link,
                "category": company.category,
                "address": company.address,
                "borough": company.borough,
                "city": company.city,
                "latitude": company.latitude,
                "longitude": company.longitude,
                "phone": company.phone,
                "website": company.website,
                "emails": list(company.emails),
                "review_rating": company.review_rating,
                "review_count": company.review_count,
                "status": ProspectStatus.NEW.value,
                "do_not_contact": False,
                "created_at": moment,
                "updated_at": moment,
            }
            for company in _deduplicate(companies)
        ]

        statement = insert(ProspectRow).values(values)
        statement = statement.on_conflict_do_update(
            index_elements=[ProspectRow.maps_link],
            set_={
                "search_id": statement.excluded.search_id,
                "name": statement.excluded.name,
                "category": statement.excluded.category,
                "address": statement.excluded.address,
                "borough": statement.excluded.borough,
                "city": statement.excluded.city,
                "latitude": statement.excluded.latitude,
                "longitude": statement.excluded.longitude,
                "phone": statement.excluded.phone,
                "website": statement.excluded.website,
                "emails": statement.excluded.emails,
                "review_rating": statement.excluded.review_rating,
                "review_count": statement.excluded.review_count,
                "updated_at": moment,
            },
        )
        await self._session.execute(statement)
        return len(values)

    async def list_prospects(self, search_id: UUID, *, limit: int = 500) -> list[Prospect]:
        rows = (
            await self._session.execute(
                select(ProspectRow)
                .where(ProspectRow.search_id == search_id)
                .order_by(ProspectRow.review_count.desc(), ProspectRow.name)
                .limit(limit)
            )
        ).scalars()
        return [prospect_to_domain(row) for row in rows]

    async def count_prospects(self, search_id: UUID) -> int:
        return (
            await self._session.execute(
                select(func.count())
                .select_from(ProspectRow)
                .where(ProspectRow.search_id == search_id)
            )
        ).scalar_one()


def _deduplicate(companies: list[FoundCompany]) -> list[FoundCompany]:
    """One row per maps_link, or the upsert would hit itself."""
    unique: dict[str, FoundCompany] = {}
    for company in companies:
        unique[company.maps_link] = company
    return list(unique.values())
