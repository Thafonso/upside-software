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
    ProspectFilters,
    ProspectStatus,
    Search,
    SearchInput,
    SearchStatus,
    score_for,
    signals_for,
)
from app.hunter.scoring import WebsiteType
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
    # Signals, written with the row; the site ones are filled by check_sites.
    whatsapp: Mapped[str | None] = mapped_column(String(15), default=None)
    website_type: Mapped[WebsiteType] = mapped_column(
        EnumAsString(WebsiteType, 10), default=WebsiteType.NONE, index=True
    )
    has_meta_pixel: Mapped[bool | None] = mapped_column(Boolean, default=None)
    has_instagram: Mapped[bool | None] = mapped_column(Boolean, default=None)
    has_whatsapp_link: Mapped[bool | None] = mapped_column(Boolean, default=None)
    site_unreachable: Mapped[bool] = mapped_column(Boolean, default=False)
    site_checked_at: Mapped[Timestamptz | None] = mapped_column(default=None)
    score: Mapped[int] = mapped_column(Integer, default=0, index=True)
    score_details: Mapped[dict[str, int]] = mapped_column(JSONB, default=dict)
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
        whatsapp=row.whatsapp,
        website_type=WebsiteType(row.website_type),
        has_meta_pixel=row.has_meta_pixel,
        has_instagram=row.has_instagram,
        has_whatsapp_link=row.has_whatsapp_link,
        site_unreachable=row.site_unreachable,
        site_checked_at=row.site_checked_at,
        score=row.score,
        score_details=dict(row.score_details or {}),
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
        values = [_row_values(search_id, company, moment) for company in _deduplicate(companies)]

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
                "whatsapp": statement.excluded.whatsapp,
                "website_type": statement.excluded.website_type,
                "score": statement.excluded.score,
                "score_details": statement.excluded.score_details,
                # The site may have changed, so what we knew about it is stale:
                # check_sites runs again right after this import.
                "has_meta_pixel": None,
                "has_instagram": None,
                "has_whatsapp_link": None,
                "site_unreachable": False,
                "site_checked_at": None,
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

    def _filtered(self, filters: ProspectFilters):
        """The base query for the working list.

        A prospect marked do_not_contact never shows up again: not on the
        list, not in the CSV, not in the copied phone numbers.
        """
        statement = select(ProspectRow).where(ProspectRow.do_not_contact.is_(False))
        if filters.search_id is not None:
            statement = statement.where(ProspectRow.search_id == filters.search_id)
        if filters.status is not None:
            statement = statement.where(ProspectRow.status == filters.status)
        if filters.no_website:
            statement = statement.where(ProspectRow.website_type == WebsiteType.NONE)
        if filters.social_only:
            statement = statement.where(ProspectRow.website_type == WebsiteType.SOCIAL)
        if filters.has_whatsapp:
            statement = statement.where(ProspectRow.whatsapp.is_not(None))
        if filters.has_email:
            statement = statement.where(func.jsonb_array_length(ProspectRow.emails) > 0)
        if filters.min_score:
            statement = statement.where(ProspectRow.score >= filters.min_score)
        return statement

    async def search_prospects(
        self, filters: ProspectFilters, *, limit: int = 500
    ) -> list[Prospect]:
        """Best prospects first, which is the order the team works in."""
        statement = (
            self._filtered(filters)
            .order_by(ProspectRow.score.desc(), ProspectRow.review_count.desc())
            .limit(limit)
        )
        rows = (await self._session.execute(statement)).scalars()
        return [prospect_to_domain(row) for row in rows]

    async def count_filtered(self, filters: ProspectFilters) -> int:
        statement = self._filtered(filters).with_only_columns(func.count()).order_by(None)
        return (await self._session.execute(statement)).scalar_one()

    async def get_prospect(self, prospect_id: UUID) -> Prospect | None:
        row = await self._session.get(ProspectRow, prospect_id)
        return prospect_to_domain(row) if row else None

    async def set_prospect_status(
        self, prospect_id: UUID, status: ProspectStatus
    ) -> Prospect | None:
        row = await self._session.get(ProspectRow, prospect_id)
        if row is None:
            return None
        row.status = status
        await self._session.flush()
        await self._session.refresh(row)
        return prospect_to_domain(row)

    async def set_do_not_contact(self, prospect_id: UUID, *, flag: bool = True) -> Prospect | None:
        row = await self._session.get(ProspectRow, prospect_id)
        if row is None:
            return None
        row.do_not_contact = flag
        await self._session.flush()
        await self._session.refresh(row)
        return prospect_to_domain(row)

    async def attach_client(self, prospect_id: UUID, client_id: UUID) -> Prospect | None:
        row = await self._session.get(ProspectRow, prospect_id)
        if row is None:
            return None
        row.client_id = client_id
        row.status = ProspectStatus.WON
        await self._session.flush()
        await self._session.refresh(row)
        return prospect_to_domain(row)

    async def list_for_site_check(self, search_id: UUID) -> list[Prospect]:
        """Prospects of a search whose own site has not been visited yet."""
        statement = select(ProspectRow).where(
            ProspectRow.search_id == search_id,
            ProspectRow.website_type == WebsiteType.SITE,
            ProspectRow.site_checked_at.is_(None),
        )
        rows = (await self._session.execute(statement)).scalars()
        return [prospect_to_domain(row) for row in rows]

    async def apply_site_check(
        self,
        prospect_id: UUID,
        *,
        has_meta_pixel: bool | None,
        has_instagram: bool | None,
        has_whatsapp_link: bool | None,
        unreachable: bool,
    ) -> Prospect | None:
        """Store what the visit found and score the prospect again."""
        row = await self._session.get(ProspectRow, prospect_id)
        if row is None:
            return None

        row.has_meta_pixel = has_meta_pixel
        row.has_instagram = has_instagram
        row.has_whatsapp_link = has_whatsapp_link
        row.site_unreachable = unreachable
        row.site_checked_at = now_utc()

        signals = signals_for(
            phone=row.phone,
            website=row.website,
            emails=tuple(row.emails or ()),
            review_count=row.review_count,
            review_rating=row.review_rating,
            has_meta_pixel=has_meta_pixel,
        )
        score = score_for(signals)
        row.score = score.value
        row.score_details = score.details

        await self._session.flush()
        await self._session.refresh(row)
        return prospect_to_domain(row)

    async def count_prospects(self, search_id: UUID) -> int:
        return (
            await self._session.execute(
                select(func.count())
                .select_from(ProspectRow)
                .where(ProspectRow.search_id == search_id)
            )
        ).scalar_one()


def _row_values(search_id: UUID, company: FoundCompany, moment: datetime) -> dict[str, object]:
    """One row, with its signals and score already worked out."""
    signals = signals_for(
        phone=company.phone,
        website=company.website,
        emails=company.emails,
        review_count=company.review_count,
        review_rating=company.review_rating,
    )
    score = score_for(signals)
    return {
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
        "whatsapp": signals.whatsapp,
        "website_type": signals.website_type.value,
        "score": score.value,
        "score_details": score.details,
        "created_at": moment,
        "updated_at": moment,
    }


def _deduplicate(companies: list[FoundCompany]) -> list[FoundCompany]:
    """One row per maps_link, or the upsert would hit itself."""
    unique: dict[str, FoundCompany] = {}
    for company in companies:
        unique[company.maps_link] = company
    return list(unique.values())
