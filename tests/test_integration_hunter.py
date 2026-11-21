"""Hunter against a real Postgres: dedup, the queue flow and the screens."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import timedelta
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.hunter import jobs as hunter_jobs
from app.hunter.domain import (
    FoundCompany,
    ProspectStatus,
    ScrapeStatus,
    SearchStatus,
    build_search_input,
)
from app.hunter.repository import ProspectRow, SearchRow, SqlAlchemyHunterRepository
from app.hunter.service import HunterService
from app.shared.clock import now_utc
from app.shared.db import get_sessionmaker
from app.shared.jobs import JobStatus, queue
from app.shared.jobs.models import Job
from app.shared.jobs.runner import run_next
from app.shared.seed import seed_admin
from tests.conftest import ADMIN_EMAIL, ADMIN_PASSWORD
from tests.test_hunter_service import FakeProvider

pytestmark = pytest.mark.integration


def _company(name: str, link: str, **overrides: object) -> FoundCompany:
    base: dict[str, object] = {"name": name, "maps_link": link}
    return FoundCompany(**{**base, **overrides})  # pyright: ignore[reportArgumentType]


def _repository(session: AsyncSession) -> SqlAlchemyHunterRepository:
    return SqlAlchemyHunterRepository(session)


def _service(session: AsyncSession, *, daily_limit: int = 20) -> HunterService:
    return HunterService(_repository(session), daily_limit=daily_limit)


async def _create_search(session: AsyncSession, **kwargs: str):
    data = build_search_input(
        niches=kwargs.get("niches", "dentista"),
        city=kwargs.get("city", "Curitiba"),
        neighborhood=kwargs.get("neighborhood", "Batel"),
    )
    return await _service(session).create_search(data)


# ── repositório ───────────────────────────────────────────────


async def test_search_round_trip(clean_tables: AsyncSession) -> None:
    search = await _create_search(clean_tables)
    await clean_tables.commit()

    found = await _repository(clean_tables).get_search(search.id)
    assert found is not None
    assert found.niches == ("dentista",)
    assert found.city == "Curitiba"
    assert found.neighborhood == "Batel"
    assert found.status == SearchStatus.QUEUED
    assert found.where == "Batel, Curitiba"
    assert found.to_request().queries == ("dentista em Batel, Curitiba",)


async def test_status_transitions_are_persisted(clean_tables: AsyncSession) -> None:
    repository = _repository(clean_tables)
    search = await _create_search(clean_tables)

    started = now_utc()
    await repository.mark_running(
        search.id,
        scraper_job_id="job-1",
        started_at=started,
        deadline_at=started + timedelta(minutes=25),
    )
    assert await repository.running_search_id() == search.id
    assert await repository.running_search_id(excluding=search.id) is None

    await repository.mark_done(search.id, total_found=7)
    reloaded = await repository.get_search(search.id)
    assert reloaded is not None
    assert reloaded.status == SearchStatus.DONE
    assert reloaded.total_found == 7
    assert reloaded.finished_at is not None

    await repository.mark_failed(search.id, message="deu ruim")
    reloaded = await repository.get_search(search.id)
    assert reloaded is not None
    assert reloaded.status == SearchStatus.FAILED
    assert reloaded.error_message == "deu ruim"


async def test_daily_limit_counts_only_today(clean_tables: AsyncSession) -> None:
    search = await _create_search(clean_tables)
    await clean_tables.commit()
    assert await _service(clean_tables).searches_used_today() == 1

    # Push it to last week: today's count must drop back to zero.
    await clean_tables.execute(
        update(SearchRow)
        .where(SearchRow.id == search.id)
        .values(created_at=now_utc() - timedelta(days=7))
    )
    await clean_tables.commit()
    assert await _service(clean_tables).searches_used_today() == 0


# ── deduplicação ──────────────────────────────────────────────


async def test_prospects_are_written_with_their_data(
    clean_tables: AsyncSession,
) -> None:
    search = await _create_search(clean_tables)
    written = await _repository(clean_tables).upsert_prospects(
        search.id,
        [
            _company(
                "Padaria Central",
                "https://maps.google.com/padaria",
                phone="(41) 3333-4444",
                website="https://padaria.com.br",
                emails=("contato@padaria.com.br",),
                review_rating=Decimal("4.7"),
                review_count=312,
                borough="Batel",
                city="Curitiba",
                latitude=Decimal("-25.4405070"),
            )
        ],
    )
    await clean_tables.commit()

    assert written == 1
    prospects = await _repository(clean_tables).list_prospects(search.id)
    assert len(prospects) == 1
    prospect = prospects[0]
    assert prospect.name == "Padaria Central"
    assert prospect.emails == ("contato@padaria.com.br",)
    assert prospect.review_rating == Decimal("4.7")
    assert prospect.review_count == 312
    assert prospect.status == ProspectStatus.NEW
    assert prospect.do_not_contact is False
    assert prospect.client_id is None


async def test_duplicates_inside_one_batch_collapse(
    clean_tables: AsyncSession,
) -> None:
    search = await _create_search(clean_tables)
    written = await _repository(clean_tables).upsert_prospects(
        search.id,
        [
            _company("Padaria", "https://maps.google.com/mesma"),
            _company("Padaria Central", "https://maps.google.com/mesma"),
        ],
    )
    await clean_tables.commit()

    assert written == 1
    prospects = await _repository(clean_tables).list_prospects(search.id)
    assert [item.name for item in prospects] == ["Padaria Central"]


async def test_finding_a_place_again_keeps_the_work_done_on_it(
    clean_tables: AsyncSession,
) -> None:
    first = await _create_search(clean_tables)
    link = "https://maps.google.com/padaria"
    await _repository(clean_tables).upsert_prospects(
        first.id, [_company("Padaria", link, phone="(41) 3333-4444")]
    )
    await clean_tables.commit()

    # The team worked this prospect.
    client_id = (await _repository(clean_tables).list_prospects(first.id))[0].id
    await clean_tables.execute(
        update(ProspectRow)
        .where(ProspectRow.maps_link == link)
        .values(
            status=ProspectStatus.CONTACTED.value,
            do_not_contact=True,
            client_id=client_id,
        )
    )
    await clean_tables.commit()

    # A later search finds the same place with fresher data.
    second = await _create_search(clean_tables, niches="padaria")
    await _repository(clean_tables).upsert_prospects(
        second.id,
        [_company("Padaria Central", link, phone="(41) 9999-8888", review_count=10)],
    )
    await clean_tables.commit()

    rows = (await clean_tables.execute(select(ProspectRow))).scalars().all()
    assert len(rows) == 1
    row = rows[0]
    # Scraped data refreshed, and it now belongs to the newer search...
    assert row.name == "Padaria Central"
    assert row.phone == "(41) 9999-8888"
    assert row.review_count == 10
    assert row.search_id == second.id
    # ...but the team's own work is untouched.
    assert row.status == ProspectStatus.CONTACTED
    assert row.do_not_contact is True
    assert row.client_id == client_id


async def test_upsert_of_nothing_is_a_no_op(clean_tables: AsyncSession) -> None:
    search = await _create_search(clean_tables)
    assert await _repository(clean_tables).upsert_prospects(search.id, []) == 0


# ── fluxo pela fila ───────────────────────────────────────────


@pytest.fixture
def stub_provider(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeProvider]:
    """Swap the real scraper for a stub inside the job handlers."""
    provider = FakeProvider(
        companies=[
            _company("Padaria Central", "https://maps.google.com/padaria"),
            _company("Café du Centre", "https://maps.google.com/cafe"),
        ]
    )

    @asynccontextmanager
    async def fake_scraper_provider() -> AsyncIterator[FakeProvider]:
        yield provider

    monkeypatch.setattr(hunter_jobs, "scraper_provider", fake_scraper_provider)
    yield provider


async def _run_queue(times: int = 1) -> None:
    for _ in range(times):
        await run_next(get_sessionmaker())


async def _make_jobs_due() -> None:
    async with get_sessionmaker()() as session:
        await session.execute(
            update(Job)
            .where(Job.status == JobStatus.QUEUED)
            .values(run_after=now_utc() - timedelta(seconds=1))
        )
        await session.commit()


async def test_a_search_runs_from_queue_to_results(
    clean_tables: AsyncSession, stub_provider: FakeProvider
) -> None:
    search = await _create_search(clean_tables)
    await queue.enqueue(clean_tables, hunter_jobs.START_SCRAPE, {"search_id": str(search.id)})
    await clean_tables.commit()

    await _run_queue()  # start_scrape
    async with get_sessionmaker()() as session:
        reloaded = await _repository(session).get_search(search.id)
    assert reloaded is not None
    assert reloaded.status == SearchStatus.RUNNING
    assert reloaded.scraper_job_id == "scraper-job-1"

    await _make_jobs_due()
    await _run_queue()  # poll_scrape

    async with get_sessionmaker()() as session:
        reloaded = await _repository(session).get_search(search.id)
        prospects = await _repository(session).list_prospects(search.id)
    assert reloaded is not None
    assert reloaded.status == SearchStatus.DONE
    assert reloaded.total_found == 2
    assert len(prospects) == 2
    assert stub_provider.discarded == ["scraper-job-1"]


async def test_the_scraper_still_working_only_reschedules(
    clean_tables: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = FakeProvider(status=ScrapeStatus.RUNNING)

    @asynccontextmanager
    async def fake_scraper_provider() -> AsyncIterator[FakeProvider]:
        yield provider

    monkeypatch.setattr(hunter_jobs, "scraper_provider", fake_scraper_provider)

    search = await _create_search(clean_tables)
    await queue.enqueue(clean_tables, hunter_jobs.START_SCRAPE, {"search_id": str(search.id)})
    await clean_tables.commit()

    await _run_queue()
    await _make_jobs_due()
    await _run_queue()

    async with get_sessionmaker()() as session:
        jobs = await queue.list_recent(session)
    poll = next(job for job in jobs if job.type == hunter_jobs.POLL_SCRAPE)
    # Waiting is not failing: the attempt count stays at zero.
    assert JobStatus(poll.status) == JobStatus.QUEUED
    assert poll.attempts == 0
    assert poll.last_error is None


async def test_a_second_search_waits_for_the_first(
    clean_tables: AsyncSession, stub_provider: FakeProvider
) -> None:
    first = await _create_search(clean_tables)
    second = await _create_search(clean_tables, niches="padaria")
    for search in (first, second):
        await queue.enqueue(clean_tables, hunter_jobs.START_SCRAPE, {"search_id": str(search.id)})
    await clean_tables.commit()

    await _run_queue(2)  # first starts, second finds the scraper busy

    async with get_sessionmaker()() as session:
        reloaded_second = await _repository(session).get_search(second.id)
        jobs = await queue.list_recent(session)
    assert reloaded_second is not None
    assert reloaded_second.status == SearchStatus.QUEUED

    waiting = [job for job in jobs if job.type == hunter_jobs.START_SCRAPE]
    assert any(job.attempts == 0 and JobStatus(job.status) == JobStatus.QUEUED for job in waiting)


async def test_a_failed_scrape_closes_the_search_with_a_readable_message(
    clean_tables: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = FakeProvider(status=ScrapeStatus.FAILED)

    @asynccontextmanager
    async def fake_scraper_provider() -> AsyncIterator[FakeProvider]:
        yield provider

    monkeypatch.setattr(hunter_jobs, "scraper_provider", fake_scraper_provider)

    search = await _create_search(clean_tables)
    await queue.enqueue(clean_tables, hunter_jobs.START_SCRAPE, {"search_id": str(search.id)})
    await clean_tables.commit()

    await _run_queue()
    await _make_jobs_due()
    await _run_queue()

    async with get_sessionmaker()() as session:
        reloaded = await _repository(session).get_search(search.id)
    assert reloaded is not None
    assert reloaded.status == SearchStatus.FAILED
    assert "Google" in (reloaded.error_message or "")


# ── telas ─────────────────────────────────────────────────────


@pytest.fixture
async def client(clean_tables: AsyncSession) -> AsyncIterator[httpx.AsyncClient]:
    from app.main import app

    await seed_admin()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="https://testserver", follow_redirects=False
    ) as http:
        yield http
    await clean_tables.rollback()


async def _sign_in(http: httpx.AsyncClient) -> None:
    response = await http.post("/login", data={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert response.status_code == 303


async def test_hunter_needs_a_session(client: httpx.AsyncClient) -> None:
    response = await client.get("/hunter/searches")
    assert response.status_code == 303
    assert response.headers["location"] == "/login?next=/hunter/searches"


async def test_empty_hunter_screen_invites_a_search(client: httpx.AsyncClient) -> None:
    await _sign_in(client)
    response = await client.get("/hunter/searches")
    assert response.status_code == 200
    assert "Nenhuma busca ainda" in response.text


async def test_creating_a_search_enqueues_it_and_redirects(
    client: httpx.AsyncClient,
) -> None:
    await _sign_in(client)
    response = await client.post(
        "/hunter/searches",
        data={
            "niches": "dentista\nortodontia",
            "city": "Curitiba",
            "neighborhood": "Batel",
            "quantity": "60",
            "fetch_emails": "",
        },
    )
    assert response.status_code == 303
    location = response.headers["location"]
    assert location.startswith("/hunter/searches/")

    async with get_sessionmaker()() as session:
        searches = await _repository(session).list_searches()
        jobs = await queue.list_recent(session)
    assert len(searches) == 1
    assert searches[0].niches == ("dentista", "ortodontia")
    assert [job.type for job in jobs] == [hunter_jobs.START_SCRAPE]
    assert jobs[0].payload == {"search_id": str(searches[0].id)}


async def test_invalid_form_comes_back_with_errors(client: httpx.AsyncClient) -> None:
    await _sign_in(client)
    response = await client.post(
        "/hunter/searches", data={"niches": "  ", "city": "", "quantity": "60"}
    )
    assert response.status_code == 422
    assert "Informe ao menos um nicho." in response.text
    assert "Informe a cidade." in response.text


async def test_daily_limit_is_enforced_by_the_screen(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.shared.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "hunter_daily_limit", 1, raising=False)

    await _sign_in(client)
    form = {"niches": "dentista", "city": "Curitiba", "quantity": "60"}
    assert (await client.post("/hunter/searches", data=form)).status_code == 303

    blocked = await client.post("/hunter/searches", data=form)
    assert blocked.status_code == 422
    assert "limite de 1 buscas por dia" in blocked.text

    async with get_sessionmaker()() as session:
        assert len(await _repository(session).list_searches()) == 1


async def test_detail_screen_polls_while_the_search_is_open(
    client: httpx.AsyncClient,
) -> None:
    await _sign_in(client)
    created = await client.post(
        "/hunter/searches",
        data={"niches": "dentista", "city": "Curitiba", "quantity": "60"},
    )
    detail_url = created.headers["location"]

    detail = await client.get(detail_url)
    assert detail.status_code == 200
    assert "Na fila" in detail.text
    assert 'hx-trigger="every 5s"' in detail.text

    progress = await client.get(f"{detail_url}/progress", headers={"hx-request": "true"})
    assert progress.status_code == 200
    assert "<!doctype html>" not in progress.text.lower()


async def test_finished_search_stops_polling_and_shows_results(
    client: httpx.AsyncClient, stub_provider: FakeProvider
) -> None:
    await _sign_in(client)
    created = await client.post(
        "/hunter/searches",
        data={"niches": "dentista", "city": "Curitiba", "quantity": "60"},
    )
    detail_url = created.headers["location"]

    await _run_queue()
    await _make_jobs_due()
    await _run_queue()

    detail = await client.get(detail_url)
    assert "2 empresas encontradas" in detail.text
    assert "Padaria Central" in detail.text
    assert "Café du Centre" in detail.text
    # Nothing left to wait for, so the polling attribute is gone.
    assert 'hx-trigger="every 5s"' not in detail.text


async def test_unknown_search_shows_the_not_found_screen(
    client: httpx.AsyncClient,
) -> None:
    await _sign_in(client)
    response = await client.get("/hunter/searches/0f1b4a3c-0000-4000-8000-000000000000")
    assert response.status_code == 404
    assert "Busca não encontrada." in response.text
