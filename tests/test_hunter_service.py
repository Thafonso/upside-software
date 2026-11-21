"""Hunter use cases, driven through fakes: no database, no HTTP."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest

from app.hunter.domain import (
    SCRAPER_FAILURE_MESSAGE,
    TIMEOUT_MESSAGE,
    DailyLimitReached,
    FoundCompany,
    Prospect,
    ScrapeStatus,
    Search,
    SearchInput,
    SearchStatus,
    build_search_input,
)
from app.hunter.ports import ScraperError
from app.hunter.service import HunterService, PollOutcome, SearchNotFound, StartOutcome

NOW = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)


class FakeHunterRepository:
    """Implements app.hunter.ports.HunterRepository in memory."""

    def __init__(self) -> None:
        self.searches: dict[UUID, Search] = {}
        self.prospects: dict[UUID, list[FoundCompany]] = {}
        self.created_today = 0

    async def create_search(
        self, data: SearchInput, *, depth: int, max_time_seconds: int
    ) -> Search:
        search = Search(
            id=uuid4(),
            name=data.display_name,
            niches=data.niches,
            city=data.city,
            neighborhood=data.neighborhood,
            quantity=data.quantity,
            fetch_emails=data.fetch_emails,
            status=SearchStatus.QUEUED,
            scraper_job_id=None,
            total_found=0,
            error_message=None,
            depth=depth,
            max_time_seconds=max_time_seconds,
            started_at=None,
            deadline_at=None,
            finished_at=None,
            created_at=NOW,
            updated_at=NOW,
        )
        self.searches[search.id] = search
        self.created_today += 1
        return search

    async def get_search(self, search_id: UUID) -> Search | None:
        return self.searches.get(search_id)

    async def list_searches(self, *, limit: int = 50) -> list[Search]:
        return list(self.searches.values())[:limit]

    async def count_searches_between(self, start: datetime, end: datetime) -> int:
        assert start < end
        return self.created_today

    async def running_search_id(self, *, excluding: UUID | None = None) -> UUID | None:
        for search in self.searches.values():
            if search.status == SearchStatus.RUNNING and search.id != excluding:
                return search.id
        return None

    async def mark_running(
        self,
        search_id: UUID,
        *,
        scraper_job_id: str,
        started_at: datetime,
        deadline_at: datetime,
    ) -> None:
        self.searches[search_id] = dataclasses.replace(
            self.searches[search_id],
            status=SearchStatus.RUNNING,
            scraper_job_id=scraper_job_id,
            started_at=started_at,
            deadline_at=deadline_at,
        )

    async def mark_done(self, search_id: UUID, *, total_found: int) -> None:
        self.searches[search_id] = dataclasses.replace(
            self.searches[search_id],
            status=SearchStatus.DONE,
            total_found=total_found,
        )

    async def mark_failed(self, search_id: UUID, *, message: str) -> None:
        self.searches[search_id] = dataclasses.replace(
            self.searches[search_id],
            status=SearchStatus.FAILED,
            error_message=message,
        )

    async def upsert_prospects(self, search_id: UUID, companies: list[FoundCompany]) -> int:
        self.prospects[search_id] = companies
        return len(companies)

    async def list_prospects(self, search_id: UUID, *, limit: int = 500) -> list[Prospect]:
        assert limit > 0
        return []

    async def count_prospects(self, search_id: UUID) -> int:
        return len(self.prospects.get(search_id, []))


class FakeProvider:
    """Implements app.hunter.ports.CompanySearchProvider."""

    def __init__(
        self,
        *,
        status: ScrapeStatus = ScrapeStatus.DONE,
        companies: list[FoundCompany] | None = None,
        fail_on_start: bool = False,
    ) -> None:
        self._status = status
        self._companies = companies or []
        self._fail_on_start = fail_on_start
        self.started: list[str] = []
        self.discarded: list[str] = []

    async def start(self, request) -> str:
        if self._fail_on_start:
            raise ScraperError("scraper fora do ar")
        self.started.append(request.name)
        return "scraper-job-1"

    async def status(self, external_job_id: str) -> ScrapeStatus:
        return self._status

    async def results(self, external_job_id: str) -> list[FoundCompany]:
        return self._companies

    async def discard(self, external_job_id: str) -> None:
        self.discarded.append(external_job_id)


def _company(name: str, link: str) -> FoundCompany:
    return FoundCompany(name=name, maps_link=link)


@pytest.fixture
def repository() -> FakeHunterRepository:
    return FakeHunterRepository()


@pytest.fixture
def service(repository: FakeHunterRepository) -> HunterService:
    return HunterService(repository, daily_limit=3)


async def _queued_search(service: HunterService) -> Search:
    return await service.create_search(
        build_search_input(niches="dentista", city="Curitiba", neighborhood="Batel")
    )


# ── limite diário ─────────────────────────────────────────────


async def test_daily_limit_blocks_the_fourth_search(
    service: HunterService, repository: FakeHunterRepository
) -> None:
    for _ in range(3):
        await _queued_search(service)

    assert await service.remaining_today() == 0
    with pytest.raises(DailyLimitReached) as caught:
        await _queued_search(service)
    assert caught.value.used == 3
    assert caught.value.limit == 3
    assert repository.created_today == 3


async def test_remaining_today_counts_down(service: HunterService) -> None:
    assert await service.remaining_today() == 3
    await _queued_search(service)
    assert await service.remaining_today() == 2
    assert await service.searches_used_today() == 1


async def test_created_search_keeps_the_mapped_parameters(
    service: HunterService,
) -> None:
    search = await service.create_search(
        build_search_input(niches="dentista", city="Curitiba", quantity="120", fetch_emails=True)
    )
    assert search.depth == 10
    assert search.max_time_seconds == 3600  # 1800 doubled for e-mails
    assert search.status == SearchStatus.QUEUED


async def test_require_search_raises_for_an_unknown_id(service: HunterService) -> None:
    with pytest.raises(SearchNotFound):
        await service.require_search(uuid4())


# ── começar a raspagem ────────────────────────────────────────


async def test_begin_scrape_hands_the_search_to_the_provider(
    service: HunterService, repository: FakeHunterRepository
) -> None:
    search = await _queued_search(service)
    provider = FakeProvider()

    assert await service.begin_scrape(search.id, provider) == StartOutcome.STARTED

    stored = repository.searches[search.id]
    assert stored.status == SearchStatus.RUNNING
    assert stored.scraper_job_id == "scraper-job-1"
    assert stored.deadline_at is not None
    assert stored.started_at is not None
    assert stored.deadline_at - stored.started_at == timedelta(
        seconds=stored.max_time_seconds
    ) + timedelta(minutes=5)
    assert provider.started == ["dentista · Batel"]


async def test_only_one_search_runs_at_a_time(service: HunterService) -> None:
    first = await _queued_search(service)
    second = await _queued_search(service)
    provider = FakeProvider()

    assert await service.begin_scrape(first.id, provider) == StartOutcome.STARTED
    assert await service.begin_scrape(second.id, provider) == StartOutcome.BUSY
    assert provider.started == ["dentista · Batel"]


async def test_begin_scrape_is_idempotent(service: HunterService) -> None:
    search = await _queued_search(service)
    provider = FakeProvider()

    await service.begin_scrape(search.id, provider)
    assert await service.begin_scrape(search.id, provider) == StartOutcome.SKIPPED
    assert len(provider.started) == 1


async def test_begin_scrape_of_an_unknown_search_is_skipped(
    service: HunterService,
) -> None:
    assert await service.begin_scrape(uuid4(), FakeProvider()) == StartOutcome.SKIPPED


async def test_provider_failure_propagates_so_the_job_retries(
    service: HunterService, repository: FakeHunterRepository
) -> None:
    search = await _queued_search(service)

    with pytest.raises(ScraperError):
        await service.begin_scrape(search.id, FakeProvider(fail_on_start=True))

    # Still queued: the queue will try again instead of losing the search.
    assert repository.searches[search.id].status == SearchStatus.QUEUED


# ── colher os resultados ──────────────────────────────────────


async def _running_search(service: HunterService, repository: FakeHunterRepository) -> Search:
    search = await _queued_search(service)
    await service.begin_scrape(search.id, FakeProvider())
    return repository.searches[search.id]


async def test_collect_waits_while_the_scraper_works(
    service: HunterService, repository: FakeHunterRepository
) -> None:
    search = await _running_search(service, repository)
    for status in (ScrapeStatus.PENDING, ScrapeStatus.RUNNING):
        outcome = await service.collect_results(search.id, FakeProvider(status=status))
        assert outcome == PollOutcome.WAITING
    assert repository.searches[search.id].status == SearchStatus.RUNNING


async def test_collect_imports_and_closes_the_search(
    service: HunterService, repository: FakeHunterRepository
) -> None:
    search = await _running_search(service, repository)
    provider = FakeProvider(companies=[_company("Padaria", "link-1"), _company("Café", "link-2")])

    assert await service.collect_results(search.id, provider) == PollOutcome.DONE

    stored = repository.searches[search.id]
    assert stored.status == SearchStatus.DONE
    assert stored.total_found == 2
    assert len(repository.prospects[search.id]) == 2
    # The job is removed at the scraper once its results are ours.
    assert provider.discarded == ["scraper-job-1"]


async def test_collect_handles_a_scraper_failure(
    service: HunterService, repository: FakeHunterRepository
) -> None:
    search = await _running_search(service, repository)
    provider = FakeProvider(status=ScrapeStatus.FAILED)

    assert await service.collect_results(search.id, provider) == PollOutcome.FAILED

    stored = repository.searches[search.id]
    assert stored.status == SearchStatus.FAILED
    assert stored.error_message == SCRAPER_FAILURE_MESSAGE
    assert provider.discarded == ["scraper-job-1"]


async def test_collect_gives_up_after_the_deadline(
    service: HunterService, repository: FakeHunterRepository
) -> None:
    search = await _running_search(service, repository)
    repository.searches[search.id] = dataclasses.replace(
        repository.searches[search.id],
        deadline_at=datetime(2020, 1, 1, tzinfo=UTC),
    )

    provider = FakeProvider(status=ScrapeStatus.RUNNING)
    assert await service.collect_results(search.id, provider) == PollOutcome.FAILED

    stored = repository.searches[search.id]
    assert stored.status == SearchStatus.FAILED
    assert stored.error_message == TIMEOUT_MESSAGE


async def test_collect_skips_a_search_that_is_not_running(
    service: HunterService,
) -> None:
    search = await _queued_search(service)
    assert await service.collect_results(search.id, FakeProvider()) == PollOutcome.SKIPPED
    assert await service.collect_results(uuid4(), FakeProvider()) == PollOutcome.SKIPPED


async def test_collect_fails_a_running_search_without_a_job_id(
    service: HunterService, repository: FakeHunterRepository
) -> None:
    search = await _running_search(service, repository)
    repository.searches[search.id] = dataclasses.replace(
        repository.searches[search.id], scraper_job_id=None
    )

    assert await service.collect_results(search.id, FakeProvider()) == PollOutcome.FAILED
    assert repository.searches[search.id].status == SearchStatus.FAILED


async def test_a_failed_cleanup_does_not_lose_the_results(
    service: HunterService, repository: FakeHunterRepository
) -> None:
    """Deleting the job at the scraper is housekeeping, not part of the import."""
    search = await _running_search(service, repository)

    class StubbornProvider(FakeProvider):
        async def discard(self, external_job_id: str) -> None:
            raise ScraperError("não deu para apagar")

    provider = StubbornProvider(companies=[_company("Padaria", "link-1")])
    assert await service.collect_results(search.id, provider) == PollOutcome.DONE
    assert repository.searches[search.id].status == SearchStatus.DONE
