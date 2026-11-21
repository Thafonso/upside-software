"""The ads module against a real Postgres: linking, syncing, the log."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import date, timedelta
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ads import jobs as ads_jobs
from app.ads.domain import (
    AccountStatus,
    Campaign,
    DailyInsight,
    SyncKind,
    SyncStatus,
)
from app.ads.ports import AdsGatewayError, RateLimited
from app.ads.repository import CampaignDailyRow, SqlAlchemyAdsRepository, SyncLogRow
from app.ads.service import AccountNotLinked, AdsService, UnknownClient
from app.clients import ClientsApi
from app.clients.domain import ClientInput, ClientStatus
from app.clients.repository import SqlAlchemyClientRepository
from app.clients.service import ClientsService
from app.shared.clock import today_local
from app.shared.db import get_sessionmaker
from app.shared.jobs import JobStatus, queue
from app.shared.jobs.models import Job
from app.shared.jobs.runner import run_next
from app.shared.seed import seed_admin
from tests.conftest import ADMIN_EMAIL, ADMIN_PASSWORD

pytestmark = pytest.mark.integration

ACCOUNT = "act_123456789"


def _repository(session: AsyncSession) -> SqlAlchemyAdsRepository:
    return SqlAlchemyAdsRepository(session)


def _service(session: AsyncSession) -> AdsService:
    return AdsService(_repository(session), ClientsApi(session))


async def _client(session: AsyncSession, *, result_action_type: str = "lead"):
    service = ClientsService(SqlAlchemyClientRepository(session))
    return await service.create(
        ClientInput(
            name="Padaria Central",
            status=ClientStatus.ACTIVE,
            result_action_type=result_action_type,
        )
    )


def _insight(day: date, campaign_id: str = "23851", **overrides: object) -> DailyInsight:
    base: dict[str, object] = {
        "day": day,
        "campaign_id": campaign_id,
        "campaign_name": "Leads - Batel",
        "spend": Decimal("150.00"),
        "impressions": 12000,
        "reach": 9000,
        "link_clicks": 310,
        "actions": {"lead": 7, "link_click": 310},
        "action_values": {},
    }
    return DailyInsight(**{**base, **overrides})  # pyright: ignore[reportArgumentType]


class FakeGateway:
    """Implements app.ads.ports.AdsPlatformGateway."""

    def __init__(
        self,
        *,
        insights: list[DailyInsight] | None = None,
        campaigns: list[Campaign] | None = None,
        error: Exception | None = None,
    ) -> None:
        self._insights = insights or []
        self._campaigns = campaigns or []
        self._error = error
        self.windows: list[tuple[date, date]] = []

    async def list_campaigns(self, account_id: str) -> list[Campaign]:
        if self._error:
            raise self._error
        return self._campaigns

    async def daily_insights(self, account_id: str, since: date, until: date) -> list[DailyInsight]:
        self.windows.append((since, until))
        if self._error:
            raise self._error
        return [i for i in self._insights if since <= i.day <= until]


# ── vínculo ───────────────────────────────────────────────────


async def test_linking_an_account_to_a_client(clean_tables: AsyncSession) -> None:
    client = await _client(clean_tables)
    await clean_tables.commit()

    account = await _service(clean_tables).link_account(ACCOUNT, client_id=client.id)
    await clean_tables.commit()

    assert account.id == ACCOUNT
    assert account.client_id == client.id
    # No name given: the client's name is the sensible default.
    assert account.name == "Padaria Central"
    assert account.status == AccountStatus.ACTIVE
    assert account.last_synced_at is None
    assert account.numeric_id == "123456789"


async def test_linking_again_updates_instead_of_duplicating(
    clean_tables: AsyncSession,
) -> None:
    client = await _client(clean_tables)
    await clean_tables.commit()
    service = _service(clean_tables)

    await service.link_account(ACCOUNT, client_id=client.id, name="Primeiro")
    await service.link_account(
        ACCOUNT, client_id=client.id, name="Segundo", status=AccountStatus.PAUSED
    )
    await clean_tables.commit()

    accounts = await service.list_accounts()
    assert len(accounts) == 1
    assert accounts[0].name == "Segundo"
    assert accounts[0].status == AccountStatus.PAUSED


async def test_linking_to_a_client_that_does_not_exist(
    clean_tables: AsyncSession,
) -> None:
    from uuid import uuid4

    with pytest.raises(UnknownClient):
        await _service(clean_tables).link_account(ACCOUNT, client_id=uuid4())


async def test_only_active_accounts_enter_the_routine(
    clean_tables: AsyncSession,
) -> None:
    client = await _client(clean_tables)
    await clean_tables.commit()
    service = _service(clean_tables)

    await service.link_account(ACCOUNT, client_id=client.id)
    await service.link_account("act_999", client_id=client.id, status=AccountStatus.PAUSED)
    await clean_tables.commit()

    assert len(await service.list_accounts()) == 2
    assert [a.id for a in await service.list_accounts(only_active=True)] == [ACCOUNT]


async def test_unlinking_takes_the_metrics_with_it(
    clean_tables: AsyncSession,
) -> None:
    client = await _client(clean_tables)
    await clean_tables.commit()
    service = _service(clean_tables)
    await service.link_account(ACCOUNT, client_id=client.id)
    await service.sync_day(
        ACCOUNT, FakeGateway(insights=[_insight(today_local())]), day=today_local()
    )
    await clean_tables.commit()
    assert await _repository(clean_tables).count_daily_rows(ACCOUNT) == 1

    assert await service.unlink_account(ACCOUNT) is True
    await clean_tables.commit()

    rows = (await clean_tables.execute(select(CampaignDailyRow))).scalars().all()
    assert rows == []
    assert await service.unlink_account(ACCOUNT) is False


# ── sincronização ─────────────────────────────────────────────


async def test_a_day_is_stored_with_the_clients_result(
    clean_tables: AsyncSession,
) -> None:
    client = await _client(clean_tables, result_action_type="lead")
    await clean_tables.commit()
    service = _service(clean_tables)
    await service.link_account(ACCOUNT, client_id=client.id)

    today = today_local()
    outcome = await service.sync_day(ACCOUNT, FakeGateway(insights=[_insight(today)]), day=today)
    await clean_tables.commit()

    assert outcome.status == SyncStatus.OK
    assert outcome.rows_written == 1

    row = (await clean_tables.execute(select(CampaignDailyRow))).scalar_one()
    assert row.date == today
    assert row.spend == Decimal("150.00")
    assert row.impressions == 12000
    assert row.link_clicks == 310
    assert row.results == 7  # only the "lead" actions
    assert row.actions == {"lead": 7, "link_click": 310}


async def test_each_client_counts_its_own_kind_of_result(
    clean_tables: AsyncSession,
) -> None:
    client = await _client(clean_tables, result_action_type="purchase")
    await clean_tables.commit()
    service = _service(clean_tables)
    await service.link_account(ACCOUNT, client_id=client.id)

    today = today_local()
    await service.sync_day(
        ACCOUNT,
        FakeGateway(insights=[_insight(today, actions={"lead": 7, "purchase": 2})]),
        day=today,
    )
    await clean_tables.commit()

    row = (await clean_tables.execute(select(CampaignDailyRow))).scalar_one()
    assert row.results == 2


async def test_resyncing_a_day_overwrites_it(clean_tables: AsyncSession) -> None:
    """Meta keeps adjusting recent days, so the newer number must win."""
    client = await _client(clean_tables)
    await clean_tables.commit()
    service = _service(clean_tables)
    await service.link_account(ACCOUNT, client_id=client.id)

    today = today_local()
    await service.sync_day(
        ACCOUNT,
        FakeGateway(insights=[_insight(today, spend=Decimal("100.00"), actions={"lead": 3})]),
        day=today,
    )
    await service.sync_day(
        ACCOUNT,
        FakeGateway(insights=[_insight(today, spend=Decimal("180.00"), actions={"lead": 9})]),
        day=today,
    )
    await clean_tables.commit()

    rows = (await clean_tables.execute(select(CampaignDailyRow))).scalars().all()
    assert len(rows) == 1
    assert rows[0].spend == Decimal("180.00")
    assert rows[0].results == 9


async def test_the_resync_asks_for_seven_days(clean_tables: AsyncSession) -> None:
    client = await _client(clean_tables)
    await clean_tables.commit()
    service = _service(clean_tables)
    await service.link_account(ACCOUNT, client_id=client.id)

    today = today_local()
    gateway = FakeGateway(
        insights=[_insight(today - timedelta(days=offset)) for offset in range(10)]
    )
    outcome = await service.resync_recent(ACCOUNT, gateway)
    await clean_tables.commit()

    since, until = gateway.windows[0]
    assert (until - since).days == 6
    assert until == today
    # Ten days offered, seven inside the window.
    assert outcome.rows_written == 7


async def test_structure_sync_stores_campaign_names(
    clean_tables: AsyncSession,
) -> None:
    client = await _client(clean_tables)
    await clean_tables.commit()
    service = _service(clean_tables)
    await service.link_account(ACCOUNT, client_id=client.id)

    gateway = FakeGateway(
        campaigns=[
            Campaign(id="23851", account_id=ACCOUNT, name="Leads", status="ACTIVE"),
            Campaign(id="23852", account_id=ACCOUNT, name="Remarketing", status="PAUSED"),
        ]
    )
    outcome = await service.sync_structure(ACCOUNT, gateway)
    await clean_tables.commit()

    assert outcome.rows_written == 2
    campaigns = await _repository(clean_tables).list_campaigns(ACCOUNT)
    assert [campaign.name for campaign in campaigns] == ["Leads", "Remarketing"]


async def test_syncing_an_account_nobody_linked(clean_tables: AsyncSession) -> None:
    with pytest.raises(AccountNotLinked):
        await _service(clean_tables).sync_day(ACCOUNT, FakeGateway())


# ── registro das sincronizações ───────────────────────────────


async def test_a_successful_sync_is_logged(clean_tables: AsyncSession) -> None:
    client = await _client(clean_tables)
    await clean_tables.commit()
    service = _service(clean_tables)
    await service.link_account(ACCOUNT, client_id=client.id)

    today = today_local()
    await service.sync_day(ACCOUNT, FakeGateway(insights=[_insight(today)]), day=today)
    await clean_tables.commit()

    entry = (await clean_tables.execute(select(SyncLogRow))).scalar_one()
    assert entry.account_id == ACCOUNT
    assert SyncKind(entry.kind) == SyncKind.DAILY
    assert SyncStatus(entry.status) == SyncStatus.OK
    assert entry.rows_written == 1
    assert entry.message is None

    account = await service.get_account(ACCOUNT)
    assert account is not None
    assert account.last_synced_at is not None


async def test_a_failure_is_logged_with_its_reason(
    clean_tables: AsyncSession,
) -> None:
    client = await _client(clean_tables)
    await clean_tables.commit()
    service = _service(clean_tables)
    await service.link_account(ACCOUNT, client_id=client.id)

    outcome = await service.sync_day(
        ACCOUNT, FakeGateway(error=AdsGatewayError("token inválido")), day=today_local()
    )
    await clean_tables.commit()

    assert outcome.status == SyncStatus.FAILED
    entry = (await clean_tables.execute(select(SyncLogRow))).scalar_one()
    assert SyncStatus(entry.status) == SyncStatus.FAILED
    assert "token inválido" in (entry.message or "")


async def test_being_throttled_is_not_logged_as_a_failure(
    clean_tables: AsyncSession,
) -> None:
    """A wait is not an outcome: the queue will come back and try again."""
    client = await _client(clean_tables)
    await clean_tables.commit()
    service = _service(clean_tables)
    await service.link_account(ACCOUNT, client_id=client.id)

    with pytest.raises(RateLimited):
        await service.sync_day(ACCOUNT, FakeGateway(error=RateLimited(120)), day=today_local())
    await clean_tables.commit()

    assert (await clean_tables.execute(select(SyncLogRow))).scalars().all() == []


# ── jobs ──────────────────────────────────────────────────────


@pytest.fixture
def stub_gateway(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeGateway]:
    gateway = FakeGateway(
        insights=[_insight(today_local())],
        campaigns=[Campaign(id="23851", account_id=ACCOUNT, name="Leads", status="ACTIVE")],
    )

    @asynccontextmanager
    async def fake_meta_gateway() -> AsyncIterator[FakeGateway]:
        yield gateway

    monkeypatch.setattr(ads_jobs, "meta_gateway", fake_meta_gateway)
    yield gateway


async def _link(session: AsyncSession) -> None:
    client = await _client(session)
    await session.commit()
    await _service(session).link_account(ACCOUNT, client_id=client.id)
    await session.commit()


async def test_the_hourly_trigger_makes_one_job_per_account(
    clean_tables: AsyncSession, stub_gateway: FakeGateway
) -> None:
    await _link(clean_tables)
    await queue.enqueue(clean_tables, ads_jobs.ENQUEUE_HOURLY_SYNC, {})
    await clean_tables.commit()

    await run_next(get_sessionmaker())

    async with get_sessionmaker()() as session:
        jobs = await queue.list_recent(session)
    fanned = [job for job in jobs if job.type == ads_jobs.SYNC_ACCOUNT]
    assert len(fanned) == 1
    assert fanned[0].payload == {"account_id": ACCOUNT}


async def test_the_daily_trigger_also_refreshes_the_structure(
    clean_tables: AsyncSession, stub_gateway: FakeGateway
) -> None:
    await _link(clean_tables)
    await queue.enqueue(clean_tables, ads_jobs.ENQUEUE_RESYNC_7D, {})
    await clean_tables.commit()

    await run_next(get_sessionmaker())

    async with get_sessionmaker()() as session:
        jobs = await queue.list_recent(session)
    types = {job.type for job in jobs}
    assert ads_jobs.RESYNC_7D in types
    assert ads_jobs.SYNC_STRUCTURE in types


async def test_the_sync_job_stores_the_day(
    clean_tables: AsyncSession, stub_gateway: FakeGateway
) -> None:
    await _link(clean_tables)
    await queue.enqueue(clean_tables, ads_jobs.SYNC_ACCOUNT, {"account_id": ACCOUNT})
    await clean_tables.commit()

    await run_next(get_sessionmaker())

    async with get_sessionmaker()() as session:
        rows = (await session.execute(select(CampaignDailyRow))).scalars().all()
    assert len(rows) == 1
    assert rows[0].results == 7


async def test_being_throttled_reschedules_without_counting_an_attempt(
    clean_tables: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    @asynccontextmanager
    async def throttled() -> AsyncIterator[FakeGateway]:
        yield FakeGateway(error=RateLimited(90))

    monkeypatch.setattr(ads_jobs, "meta_gateway", throttled)

    await _link(clean_tables)
    job_id = await queue.enqueue(clean_tables, ads_jobs.SYNC_ACCOUNT, {"account_id": ACCOUNT})
    await clean_tables.commit()

    await run_next(get_sessionmaker())

    async with get_sessionmaker()() as session:
        job = await session.get(Job, job_id)
    assert job is not None
    assert JobStatus(job.status) == JobStatus.QUEUED
    assert job.attempts == 0
    assert job.last_error is None


async def test_a_job_for_an_unlinked_account_just_stops(
    clean_tables: AsyncSession, stub_gateway: FakeGateway
) -> None:
    job_id = await queue.enqueue(
        clean_tables, ads_jobs.SYNC_ACCOUNT, {"account_id": "act_000111222"}
    )
    await clean_tables.commit()

    await run_next(get_sessionmaker())

    async with get_sessionmaker()() as session:
        job = await session.get(Job, job_id)
    assert job is not None
    assert JobStatus(job.status) == JobStatus.DONE


# ── telas ─────────────────────────────────────────────────────


@pytest.fixture
async def http(clean_tables: AsyncSession) -> AsyncIterator[httpx.AsyncClient]:
    from app.main import app

    await seed_admin()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="https://testserver", follow_redirects=False
    ) as client:
        response = await client.post(
            "/login", data={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
        )
        assert response.status_code == 303
        yield client


async def test_the_screen_starts_empty(http: httpx.AsyncClient) -> None:
    response = await http.get("/ads")
    assert response.status_code == 200
    assert "Nenhuma conta vinculada" in response.text


async def test_linking_from_the_screen_queues_the_first_sync(
    http: httpx.AsyncClient, clean_tables: AsyncSession
) -> None:
    client = await _client(clean_tables)
    await clean_tables.commit()

    response = await http.post(
        "/ads/accounts",
        data={"account_id": "123456789", "client_id": str(client.id), "name": ""},
    )
    assert response.status_code == 303

    async with get_sessionmaker()() as session:
        accounts = await _service(session).list_accounts()
        jobs = await queue.list_recent(session)
    assert [account.id for account in accounts] == [ACCOUNT]
    assert {job.type for job in jobs} == {ads_jobs.SYNC_STRUCTURE, ads_jobs.RESYNC_7D}

    screen = await http.get("/ads")
    assert ACCOUNT in screen.text
    assert "Padaria Central" in screen.text


async def test_an_invalid_account_id_comes_back_with_the_message(
    http: httpx.AsyncClient, clean_tables: AsyncSession
) -> None:
    client = await _client(clean_tables)
    await clean_tables.commit()

    response = await http.post(
        "/ads/accounts", data={"account_id": "nope", "client_id": str(client.id)}
    )
    assert response.status_code == 422
    assert "Informe o ID da conta" in response.text


async def test_sync_now_enqueues_a_job(http: httpx.AsyncClient, clean_tables: AsyncSession) -> None:
    await _link(clean_tables)

    response = await http.post(f"/ads/accounts/{ACCOUNT}/sync")
    assert response.status_code == 303

    async with get_sessionmaker()() as session:
        jobs = await queue.list_recent(session)
    assert any(job.type == ads_jobs.SYNC_ACCOUNT for job in jobs)


async def test_unlinking_from_the_screen(
    http: httpx.AsyncClient, clean_tables: AsyncSession
) -> None:
    await _link(clean_tables)

    response = await http.post(f"/ads/accounts/{ACCOUNT}/unlink")
    assert response.status_code == 303

    async with get_sessionmaker()() as session:
        assert await _service(session).list_accounts() == []


async def test_the_team_can_look_but_not_link(
    http: httpx.AsyncClient, clean_tables: AsyncSession
) -> None:
    from app.shared.auth import hash_password
    from app.shared.models import AppUser, UserRole

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
    await http.post("/login", data={"email": "equipe@upsidemkt.com.br", "password": "senha-equipe"})

    screen = await http.get("/ads")
    assert screen.status_code == 200
    assert "Vincular uma conta" not in screen.text

    blocked = await http.post("/ads/accounts", data={"account_id": ACCOUNT, "client_id": ""})
    assert blocked.status_code == 403
