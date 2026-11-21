"""The queue against a real Postgres: claiming, retrying, failing, requeuing."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from datetime import timedelta
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.repository import ClientRow
from app.shared.auth import hash_password
from app.shared.clock import now_utc
from app.shared.db import get_sessionmaker
from app.shared.jobs import queue
from app.shared.jobs.models import Job, JobStatus
from app.shared.jobs.registry import (
    JobContext,
    RetryLater,
    clear_handlers,
    job_handler,
    restore_handlers,
    snapshot_handlers,
)
from app.shared.jobs.runner import run_next
from app.shared.jobs.scheduler import ScheduledJob, build_scheduler
from app.shared.models import AppUser, UserRole
from app.shared.seed import seed_admin
from tests.conftest import ADMIN_EMAIL, ADMIN_PASSWORD

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _isolated_registry() -> Iterator[None]:
    """Start from an empty registry and put the real handlers back after."""
    saved = snapshot_handlers()
    clear_handlers()
    yield
    restore_handlers(saved)


async def _reload(job_id: UUID) -> Job:
    async with get_sessionmaker()() as session:
        job = await session.get(Job, job_id)
        assert job is not None
        await session.refresh(job)
        return job


async def _enqueue(job_type: str, payload: dict[str, Any] | None = None, **kwargs: Any) -> UUID:
    async with get_sessionmaker()() as session:
        job_id = await queue.enqueue(session, job_type, payload, **kwargs)
        await session.commit()
    return job_id


# ── enqueue ───────────────────────────────────────────────────


async def test_enqueue_creates_a_due_job(clean_tables: AsyncSession) -> None:
    before = now_utc()
    job_id = await _enqueue("test.ping", {"n": 1})

    job = await _reload(job_id)
    assert JobStatus(job.status) == JobStatus.QUEUED
    assert job.type == "test.ping"
    assert job.payload == {"n": 1}
    assert job.attempts == 0
    assert job.max_attempts == 5
    assert job.last_error is None
    assert job.run_after >= before


async def test_enqueue_defaults_to_an_empty_payload(clean_tables: AsyncSession) -> None:
    job = await _reload(await _enqueue("test.ping"))
    assert job.payload == {}


async def test_enqueue_shares_the_caller_transaction(clean_tables: AsyncSession) -> None:
    """A rolled-back caller must not leave an orphan job behind."""
    async with get_sessionmaker()() as session:
        await queue.enqueue(session, "test.ping")
        await session.rollback()

    async with get_sessionmaker()() as session:
        assert await queue.list_recent(session) == []


# ── claim ─────────────────────────────────────────────────────


async def test_claim_marks_the_job_running(clean_tables: AsyncSession) -> None:
    job_id = await _enqueue("test.ping", {"n": 7})

    async with get_sessionmaker()() as session:
        claimed = await queue.claim_next(session)
        await session.commit()

    assert claimed is not None
    assert claimed.id == job_id
    assert claimed.payload == {"n": 7}
    assert claimed.attempt_number == 1
    assert JobStatus((await _reload(job_id)).status) == JobStatus.RUNNING


async def test_claim_ignores_jobs_scheduled_for_later(
    clean_tables: AsyncSession,
) -> None:
    await _enqueue("test.ping", run_after=now_utc() + timedelta(minutes=10))

    async with get_sessionmaker()() as session:
        assert await queue.claim_next(session) is None


async def test_claim_takes_the_oldest_due_job_first(
    clean_tables: AsyncSession,
) -> None:
    second = await _enqueue("test.second", run_after=now_utc() - timedelta(minutes=1))
    first = await _enqueue("test.first", run_after=now_utc() - timedelta(minutes=5))

    async with get_sessionmaker()() as session:
        claimed = await queue.claim_next(session)
        await session.commit()
    assert claimed is not None
    assert claimed.id == first

    async with get_sessionmaker()() as session:
        claimed = await queue.claim_next(session)
        await session.commit()
    assert claimed is not None
    assert claimed.id == second


async def test_two_claims_never_get_the_same_job(clean_tables: AsyncSession) -> None:
    """FOR UPDATE SKIP LOCKED is what makes a second worker safe."""
    await _enqueue("test.a", run_after=now_utc() - timedelta(minutes=2))
    await _enqueue("test.b", run_after=now_utc() - timedelta(minutes=1))

    sessions = get_sessionmaker()
    async with sessions() as first_session, sessions() as second_session:
        first = await queue.claim_next(first_session)
        second = await queue.claim_next(second_session)
        await first_session.commit()
        await second_session.commit()

    assert first is not None
    assert second is not None
    assert first.id != second.id


async def test_a_single_job_is_claimed_only_once(clean_tables: AsyncSession) -> None:
    await _enqueue("test.only")

    sessions = get_sessionmaker()
    async with sessions() as first_session, sessions() as second_session:
        first = await queue.claim_next(first_session)
        second = await queue.claim_next(second_session)
        await first_session.commit()
        await second_session.commit()

    assert first is not None
    assert second is None


# ── execução ──────────────────────────────────────────────────


async def test_successful_job_is_marked_done(clean_tables: AsyncSession) -> None:
    seen: list[dict[str, Any]] = []

    @job_handler("test.ping")
    async def ping(payload: dict[str, Any], ctx: JobContext) -> None:
        seen.append(payload)

    job_id = await _enqueue("test.ping", {"n": 3})
    assert await run_next(get_sessionmaker()) is True

    assert seen == [{"n": 3}]
    job = await _reload(job_id)
    assert JobStatus(job.status) == JobStatus.DONE
    assert job.attempts == 0
    assert job.last_error is None


async def test_run_next_on_an_empty_queue(clean_tables: AsyncSession) -> None:
    assert await run_next(get_sessionmaker()) is False


async def test_handler_writes_commit_with_the_job(clean_tables: AsyncSession) -> None:
    @job_handler("test.create_client")
    async def create_client(payload: dict[str, Any], ctx: JobContext) -> None:
        ctx.session.add(ClientRow(name=payload["name"], slug=payload["slug"], status="active"))

    await _enqueue("test.create_client", {"name": "Padaria", "slug": "padaria"})
    await run_next(get_sessionmaker())

    async with get_sessionmaker()() as session:
        rows = (await session.execute(select(ClientRow))).scalars().all()
    assert [row.slug for row in rows] == ["padaria"]


async def test_handler_writes_roll_back_when_it_raises(
    clean_tables: AsyncSession,
) -> None:
    @job_handler("test.half_way")
    async def half_way(payload: dict[str, Any], ctx: JobContext) -> None:
        ctx.session.add(ClientRow(name="Fantasma", slug="fantasma", status="active"))
        await ctx.session.flush()
        msg = "quebrou depois de escrever"
        raise RuntimeError(msg)

    job_id = await _enqueue("test.half_way")
    await run_next(get_sessionmaker())

    async with get_sessionmaker()() as session:
        rows = (await session.execute(select(ClientRow))).scalars().all()
    assert rows == []

    job = await _reload(job_id)
    assert job.attempts == 1
    assert JobStatus(job.status) == JobStatus.QUEUED


async def test_context_carries_the_job_identity(clean_tables: AsyncSession) -> None:
    captured: list[JobContext] = []

    @job_handler("test.context")
    async def handler(payload: dict[str, Any], ctx: JobContext) -> None:
        captured.append(ctx)

    job_id = await _enqueue("test.context")
    await run_next(get_sessionmaker())

    assert len(captured) == 1
    assert captured[0].job_id == job_id
    assert captured[0].job_type == "test.context"
    assert captured[0].attempt == 1
    assert captured[0].max_attempts == 5


# ── falha e nova tentativa ────────────────────────────────────


async def test_failure_schedules_a_retry_with_backoff(
    clean_tables: AsyncSession,
) -> None:
    @job_handler("test.boom")
    async def boom(payload: dict[str, Any], ctx: JobContext) -> None:
        msg = "falhou de proposito"
        raise RuntimeError(msg)

    job_id = await _enqueue("test.boom")
    before = now_utc()
    await run_next(get_sessionmaker())
    after = now_utc()

    job = await _reload(job_id)
    assert JobStatus(job.status) == JobStatus.QUEUED
    assert job.attempts == 1
    assert job.last_error is not None
    assert "falhou de proposito" in job.last_error
    # The first retry waits exactly two minutes from some instant during the
    # run, so bracket it instead of guessing how long the run took.
    assert before + timedelta(minutes=2) <= job.run_after <= after + timedelta(minutes=2)


async def test_job_fails_for_good_after_max_attempts(
    clean_tables: AsyncSession,
) -> None:
    runs = 0

    @job_handler("test.always_boom")
    async def always_boom(payload: dict[str, Any], ctx: JobContext) -> None:
        nonlocal runs
        runs += 1
        msg = "sempre falha"
        raise RuntimeError(msg)

    job_id = await _enqueue("test.always_boom", max_attempts=3)

    for _ in range(3):
        # Make the job due again instead of waiting out the backoff.
        async with get_sessionmaker()() as session:
            job = await session.get(Job, job_id)
            assert job is not None
            job.run_after = now_utc() - timedelta(seconds=1)
            await session.commit()
        await run_next(get_sessionmaker())

    assert runs == 3
    job = await _reload(job_id)
    assert JobStatus(job.status) == JobStatus.FAILED
    assert job.attempts == 3
    assert "sempre falha" in (job.last_error or "")

    # A failed job is not picked up again.
    assert await run_next(get_sessionmaker()) is False


async def test_job_without_handler_is_recorded_as_a_failure(
    clean_tables: AsyncSession,
) -> None:
    job_id = await _enqueue("test.sem_handler")
    assert await run_next(get_sessionmaker()) is True

    job = await _reload(job_id)
    assert job.attempts == 1
    assert "nenhum handler registrado" in (job.last_error or "")


async def test_retry_later_does_not_count_an_attempt(
    clean_tables: AsyncSession,
) -> None:
    @job_handler("test.wait")
    async def wait(payload: dict[str, Any], ctx: JobContext) -> None:
        raise RetryLater(seconds=20)

    job_id = await _enqueue("test.wait")
    before = now_utc()
    await run_next(get_sessionmaker())
    after = now_utc()

    job = await _reload(job_id)
    assert JobStatus(job.status) == JobStatus.QUEUED
    assert job.attempts == 0
    assert job.last_error is None
    assert before + timedelta(seconds=20) <= job.run_after <= after + timedelta(seconds=20)


async def test_a_job_can_enqueue_the_next_one(clean_tables: AsyncSession) -> None:
    @job_handler("test.first_step")
    async def first_step(payload: dict[str, Any], ctx: JobContext) -> None:
        await queue.enqueue(ctx.session, "test.second_step", {"from": str(ctx.job_id)})

    @job_handler("test.second_step")
    async def second_step(payload: dict[str, Any], ctx: JobContext) -> None:
        return None

    first_id = await _enqueue("test.first_step")
    await run_next(get_sessionmaker())
    assert await run_next(get_sessionmaker()) is True

    async with get_sessionmaker()() as session:
        jobs = await queue.list_recent(session)
    by_type = {job.type: job for job in jobs}
    assert by_type["test.second_step"].payload == {"from": str(first_id)}
    assert JobStatus(by_type["test.second_step"].status) == JobStatus.DONE


# ── administração ─────────────────────────────────────────────


async def test_requeue_only_touches_failed_jobs(clean_tables: AsyncSession) -> None:
    failed_id = await _enqueue("test.boom", max_attempts=1)
    await run_next(get_sessionmaker())
    assert JobStatus((await _reload(failed_id)).status) == JobStatus.FAILED

    queued_id = await _enqueue("test.ping")
    async with get_sessionmaker()() as session:
        assert await queue.requeue(session, queued_id) is False
        assert await queue.requeue(session, uuid4()) is False

    async with get_sessionmaker()() as session:
        assert await queue.requeue(session, failed_id) is True
        await session.commit()

    job = await _reload(failed_id)
    assert JobStatus(job.status) == JobStatus.QUEUED
    assert job.attempts == 0
    assert job.last_error is None


async def test_orphans_from_a_dead_worker_are_requeued(
    clean_tables: AsyncSession,
) -> None:
    job_id = await _enqueue("test.ping")
    async with get_sessionmaker()() as session:
        await queue.claim_next(session)
        await session.commit()
    assert JobStatus((await _reload(job_id)).status) == JobStatus.RUNNING

    async with get_sessionmaker()() as session:
        rescued = await queue.requeue_orphans(session)
        await session.commit()

    assert rescued == 1
    assert JobStatus((await _reload(job_id)).status) == JobStatus.QUEUED


async def test_counts_and_filters(clean_tables: AsyncSession) -> None:
    @job_handler("test.ok")
    async def ok(payload: dict[str, Any], ctx: JobContext) -> None:
        return None

    await _enqueue("test.ok")
    await run_next(get_sessionmaker())
    await _enqueue("test.later", run_after=now_utc() + timedelta(hours=1))

    async with get_sessionmaker()() as session:
        counts = await queue.count_by_status(session)
        assert counts[JobStatus.DONE] == 1
        assert counts[JobStatus.QUEUED] == 1
        assert counts[JobStatus.FAILED] == 0

        assert len(await queue.list_recent(session, status=JobStatus.DONE)) == 1
        assert len(await queue.list_recent(session, job_type="test.later")) == 1
        assert len(await queue.list_recent(session, limit=1)) == 1


# ── agendador ─────────────────────────────────────────────────


def test_scheduler_arms_only_handlers_this_process_knows() -> None:
    @job_handler("test.scheduled")
    async def scheduled(payload: dict[str, Any], ctx: JobContext) -> None:
        return None

    jobs = (
        ScheduledJob("test.scheduled", hour="*", minute="5", description="armado"),
        ScheduledJob("test.sem_handler", hour="3", minute="0", description="ignorado"),
    )
    scheduler = build_scheduler(get_sessionmaker(), scheduled_jobs=jobs)

    assert [job.id for job in scheduler.get_jobs()] == ["test.scheduled"]


def test_the_real_triggers_match_the_specification() -> None:
    from app.shared.jobs.scheduler import SCHEDULED_JOBS

    by_type = {job.job_type: job for job in SCHEDULED_JOBS}
    assert by_type["ads.enqueue_hourly_sync"].minute == "5"
    assert by_type["ads.enqueue_hourly_sync"].hour == "*"
    assert by_type["ads.enqueue_resync_7d"].hour == "3"
    assert by_type["ads.enqueue_resync_7d"].minute == "0"
    assert str(by_type["ads.enqueue_resync_7d"].trigger().timezone) == "America/Sao_Paulo"


# ── tela /admin/jobs ──────────────────────────────────────────


@pytest.fixture
async def client(clean_tables: AsyncSession) -> AsyncIterator[httpx.AsyncClient]:
    from app.main import app

    await seed_admin()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="https://testserver", follow_redirects=False
    ) as http:
        yield http


async def _sign_in_admin(http: httpx.AsyncClient) -> None:
    response = await http.post("/login", data={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert response.status_code == 303


async def test_queue_screen_needs_a_session(client: httpx.AsyncClient) -> None:
    response = await client.get("/admin/jobs")
    assert response.status_code == 303
    assert response.headers["location"] == "/login?next=/admin/jobs"


async def test_queue_screen_is_admin_only(
    client: httpx.AsyncClient, clean_tables: AsyncSession
) -> None:
    clean_tables.add(
        AppUser(
            email="equipe@upsidemkt.com.br",
            name="Equipe",
            password_hash=hash_password("senha-equipe"),
            role=UserRole.TEAM,
            is_active=True,
        )
    )
    await clean_tables.commit()
    await client.post(
        "/login", data={"email": "equipe@upsidemkt.com.br", "password": "senha-equipe"}
    )

    response = await client.get("/admin/jobs")
    assert response.status_code == 403
    assert "Acesso restrito" in response.text


async def test_empty_queue_screen(client: httpx.AsyncClient) -> None:
    await _sign_in_admin(client)
    response = await client.get("/admin/jobs")
    assert response.status_code == 200
    assert "A fila está vazia" in response.text


async def test_queue_screen_lists_jobs_and_filters(
    client: httpx.AsyncClient,
) -> None:
    await _sign_in_admin(client)
    await _enqueue("test.visivel")
    await _enqueue("test.outro")

    listing = await client.get("/admin/jobs")
    assert "test.visivel" in listing.text
    assert "test.outro" in listing.text

    filtered = await client.get(
        "/admin/jobs", params={"job_type": "test.visivel"}, headers={"hx-request": "true"}
    )
    assert "<!doctype html>" not in filtered.text.lower()
    assert "test.visivel" in filtered.text
    assert "test.outro" not in filtered.text

    by_status = await client.get("/admin/jobs", params={"status": "failed"})
    assert "Nenhum job nesse filtro" in by_status.text


async def test_requeue_button_puts_a_failed_job_back(
    client: httpx.AsyncClient,
) -> None:
    await _sign_in_admin(client)
    job_id = await _enqueue("test.sem_handler", max_attempts=1)
    await run_next(get_sessionmaker())
    assert JobStatus((await _reload(job_id)).status) == JobStatus.FAILED

    response = await client.post(
        f"/admin/jobs/{job_id}/requeue",
        data={"status": "", "job_type": ""},
        headers={"hx-request": "true"},
    )
    assert response.status_code == 200
    assert JobStatus((await _reload(job_id)).status) == JobStatus.QUEUED


async def test_requeue_of_an_unknown_job_is_harmless(
    client: httpx.AsyncClient,
) -> None:
    await _sign_in_admin(client)
    response = await client.post(
        f"/admin/jobs/{uuid4()}/requeue", data={"status": "", "job_type": ""}
    )
    assert response.status_code == 200
