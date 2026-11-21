"""Interfaces the hunter service depends on."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol
from uuid import UUID

from app.hunter.domain import (
    FoundCompany,
    Prospect,
    ProspectFilters,
    ProspectStatus,
    ScrapeStatus,
    Search,
    SearchInput,
    SearchRequest,
    SiteCheck,
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


class WebsiteInspector(Protocol):
    """Visits prospects' own sites. Implemented by ``adapters.site_checker``.

    Takes every address at once so the adapter can fetch them in parallel
    and visit a shared address only once.
    """

    async def inspect_many(self, urls: Sequence[str]) -> dict[str, SiteCheck]: ...


class ConvertedClient(Protocol):
    """Just enough of a client for the hunter to record the conversion."""

    @property
    def id(self) -> UUID: ...


class ClientDirectory(Protocol):
    """Turning a prospect into a client.

    Structural on purpose: ``app.clients.ClientsApi`` satisfies it without
    the hunter importing anything from the clients module. Only the web
    layer, which composes both, knows the concrete class.
    """

    async def create_client_from_prospect(
        self,
        *,
        name: str,
        phone: str | None = None,
        email: str | None = None,
        notes: str | None = None,
    ) -> ConvertedClient: ...


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

    async def search_prospects(
        self, filters: ProspectFilters, *, limit: int = 500
    ) -> list[Prospect]: ...

    async def count_filtered(self, filters: ProspectFilters) -> int: ...

    async def get_prospect(self, prospect_id: UUID) -> Prospect | None: ...

    async def set_prospect_status(
        self, prospect_id: UUID, status: ProspectStatus
    ) -> Prospect | None: ...

    async def set_do_not_contact(
        self, prospect_id: UUID, *, flag: bool = True
    ) -> Prospect | None: ...

    async def attach_client(self, prospect_id: UUID, client_id: UUID) -> Prospect | None: ...

    async def list_for_site_check(self, search_id: UUID) -> list[Prospect]: ...

    async def apply_site_check(
        self,
        prospect_id: UUID,
        *,
        has_meta_pixel: bool | None,
        has_instagram: bool | None,
        has_whatsapp_link: bool | None,
        unreachable: bool,
    ) -> Prospect | None: ...
