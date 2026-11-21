"""Interfaces the hunter service depends on."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.hunter.domain import (
    FoundCompany,
    Prospect,
    ScrapeStatus,
    Search,
    SearchInput,
    SearchRequest,
)


class ScraperError(RuntimeError):
    """The scraper could not be reached or answered something unusable."""


class CompanySearchProvider(Protocol):
    """Finds businesses. Implemented by ``adapters.mazyos_scraper``.

    The service never sees the provider's JSON: the adapter converts it to
    the domain types above.
    """

    async def start(self, request: SearchRequest) -> str:
        """Begin a search and return the provider's job id."""
        ...

    async def status(self, external_job_id: str) -> ScrapeStatus: ...

    async def results(self, external_job_id: str) -> list[FoundCompany]: ...

    async def discard(self, external_job_id: str) -> None:
        """Drop the finished job at the provider, once results are imported."""
        ...


class HunterRepository(Protocol):
    """Persistence contract. Implemented by ``app.hunter.repository``."""

    async def create_search(
        self, data: SearchInput, *, depth: int, max_time_seconds: int
    ) -> Search: ...

    async def get_search(self, search_id: UUID) -> Search | None: ...

    async def list_searches(self, *, limit: int = 50) -> list[Search]: ...

    async def count_searches_between(self, start: datetime, end: datetime) -> int: ...

    async def running_search_id(self, *, excluding: UUID | None = None) -> UUID | None: ...

    async def mark_running(
        self,
        search_id: UUID,
        *,
        scraper_job_id: str,
        started_at: datetime,
        deadline_at: datetime,
    ) -> None: ...

    async def mark_done(self, search_id: UUID, *, total_found: int) -> None: ...

    async def mark_failed(self, search_id: UUID, *, message: str) -> None: ...

    async def upsert_prospects(self, search_id: UUID, companies: list[FoundCompany]) -> int: ...

    async def list_prospects(self, search_id: UUID, *, limit: int = 500) -> list[Prospect]: ...

    async def count_prospects(self, search_id: UUID) -> int: ...
