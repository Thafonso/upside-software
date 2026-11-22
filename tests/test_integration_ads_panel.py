"""The panel against a real Postgres: aggregation in SQL and the screens."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.ads.domain import Campaign, DailyInsight, Period, PeriodPreset, resolve_period
from app.ads.repository import SqlAlchemyAdsRepository
from app.ads.service import AdsService
from app.clients import ClientsApi
from app.clients.domain import ClientInput, ClientStatus
from app.clients.repository import SqlAlchemyClientRepository
from app.clients.service import ClientsService
from app.shared.clock import today_local
from app.shared.seed import seed_admin
from tests.conftest import ADMIN_EMAIL, ADMIN_PASSWORD
from tests.test_integration_ads import FakeGateway

pytestmark = pytest.mark.integration

ACCOUNT = "act_123456789"
OTHER_ACCOUNT = "act_987654321"


def _service(session: AsyncSession) -> AdsService:
    return AdsService(SqlAlchemyAdsRepository(session), ClientsApi(session))


async def _make_client(session: AsyncSession, name: str, action: str = "lead"):
    service = ClientsService(SqlAlchemyClientRepository(session))
    return await service.create(
        ClientInput(name=name, status=ClientStatus.ACTIVE, result_action_type=action)
    )


def _insight(
    day: date,
    campaign_id: str = "23851",
    *,
    spend: str = "100.00",
    leads: int = 5,
    name: str = "Leads - Batel",
) -> DailyInsight:
    return DailyInsight(
        day=day,
        campaign_id=campaign_id,
        campaign_name=name,
        spend=Decimal(spend),
        impressions=1000,
        reach=800,
        link_clicks=50,
        actions={"lead": leads},
        action_values={},
    )


async def _seed(session: AsyncSession) -> tuple[UUID, UUID]:
    """Two clients, one with two campaigns over three days."""
    padaria = await _make_client(session, "Padaria Central")
    clinica = await _make_client(session, "Clinica Vida")
    await session.commit()

    service = _service(session)
    await service.link_account(ACCOUNT, client_id=padaria.id)
    await service.link_account(OTHER_ACCOUNT, client_id=clinica.id)

    today = today_local()
    await service.sync_structure(
        ACCOUNT,
        FakeGateway(
            campaigns=[
                Campaign(id="23851", account_id=ACCOUNT, name="Leads", status="ACTIVE"),
                Campaign(id="23852", account_id=ACCOUNT, name="Remarketing", status="PAUSED"),
            ]
        ),
    )
    await service.resync_recent(
        ACCOUNT,
        FakeGateway(
            insights=[
                _insight(today, spend="100.00", leads=5),
                _insight(today - timedelta(days=1), spend="80.00", leads=4),
                _insight(today, "23852", spend="40.00", leads=1, name="Remarketing"),
            ]
        ),
    )
    await service.resync_recent(
        OTHER_ACCOUNT,
        FakeGateway(insights=[_insight(today, "9001", spend="300.00", leads=2)]),
    )
    await session.commit()
    return padaria.id, clinica.id


def _this_month() -> Period:
    return resolve_period(PeriodPreset.THIS_MONTH, today_local())


# ── agregação ─────────────────────────────────────────────────


async def test_client_totals_add_up_the_period(clean_tables: AsyncSession) -> None:
    padaria, _ = await _seed(clean_tables)

    totals = await _service(clean_tables).client_totals(padaria, _this_month())
    # 100 + 80 + 40
    assert totals.spend == Decimal("220.00")
    assert totals.results == 10
    assert totals.link_clicks == 150
    assert totals.cost_per_result == Decimal("22.00")


async def test_a_period_with_nothing_in_it(clean_tables: AsyncSession) -> None:
    padaria, _ = await _seed(clean_tables)
    long_ago = Period(since=date(2020, 1, 1), until=date(2020, 1, 31))

    totals = await _service(clean_tables).client_totals(padaria, long_ago)
    assert totals.spend == Decimal("0.00")
    assert not totals.has_data
    assert await _service(clean_tables).client_series(padaria, long_ago) == []


async def test_the_daily_series_has_one_point_per_day(
    clean_tables: AsyncSession,
) -> None:
    padaria, _ = await _seed(clean_tables)

    series = await _service(clean_tables).client_series(padaria, _this_month())
    assert len(series) == 2
    assert series[0].day < series[1].day
    # Today holds both campaigns: 100 + 40.
    assert series[-1].spend == Decimal("140.00")
    assert series[-1].results == 6


async def test_campaign_totals_come_biggest_spender_first(
    clean_tables: AsyncSession,
) -> None:
    padaria, _ = await _seed(clean_tables)

    campaigns = await _service(clean_tables).client_campaigns(padaria, _this_month())
    assert [campaign.name for campaign in campaigns] == ["Leads", "Remarketing"]
    assert campaigns[0].spend == Decimal("180.00")
    assert campaigns[0].is_running is True
    assert campaigns[1].spend == Decimal("40.00")
    assert campaigns[1].is_running is False


async def test_one_clients_numbers_never_leak_into_another(
    clean_tables: AsyncSession,
) -> None:
    padaria, clinica = await _seed(clean_tables)
    service = _service(clean_tables)

    assert (await service.client_totals(padaria, _this_month())).spend == Decimal("220.00")
    assert (await service.client_totals(clinica, _this_month())).spend == Decimal("300.00")


async def test_the_panel_list_is_ordered_by_spend(
    clean_tables: AsyncSession,
) -> None:
    await _seed(clean_tables)

    rows = await _service(clean_tables).panel_rows(_this_month())
    assert [row.client_name for row in rows] == ["Clinica Vida", "Padaria Central"]
    assert rows[0].totals.spend == Decimal("300.00")
    assert rows[1].totals.results == 10


async def test_a_client_with_two_accounts_is_one_row(
    clean_tables: AsyncSession,
) -> None:
    padaria = await _make_client(clean_tables, "Padaria Central")
    await clean_tables.commit()
    service = _service(clean_tables)
    await service.link_account(ACCOUNT, client_id=padaria.id)
    await service.link_account(OTHER_ACCOUNT, client_id=padaria.id)

    today = today_local()
    await service.sync_day(
        ACCOUNT, FakeGateway(insights=[_insight(today, spend="100.00")]), day=today
    )
    await service.sync_day(
        OTHER_ACCOUNT,
        FakeGateway(insights=[_insight(today, "9001", spend="50.00")]),
        day=today,
    )
    await clean_tables.commit()

    rows = await service.panel_rows(_this_month())
    assert len(rows) == 1
    assert len(rows[0].account_ids) == 2
    assert rows[0].totals.spend == Decimal("150.00")


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


async def test_the_empty_panel_points_at_linking_an_account(
    http: httpx.AsyncClient,
) -> None:
    response = await http.get("/ads")
    assert response.status_code == 200
    assert "Nenhuma conta vinculada ainda" in response.text


async def test_the_panel_lists_each_client(
    http: httpx.AsyncClient, clean_tables: AsyncSession
) -> None:
    await _seed(clean_tables)

    response = await http.get("/ads")
    assert "Padaria Central" in response.text
    assert "Clinica Vida" in response.text
    assert "R$ 300,00" in response.text
    assert "R$ 220,00" in response.text


async def test_the_period_filter_changes_what_is_shown(
    http: httpx.AsyncClient, clean_tables: AsyncSession
) -> None:
    await _seed(clean_tables)

    empty = await http.get("/ads", params={"since": "2020-01-01", "until": "2020-01-31"})
    assert "R$ 0,00" in empty.text
    assert "01/01/2020 a 31/01/2020" in empty.text


async def test_the_client_screen_shows_cards_chart_and_campaigns(
    http: httpx.AsyncClient, clean_tables: AsyncSession
) -> None:
    padaria, _ = await _seed(clean_tables)

    response = await http.get(f"/ads/clients/{padaria}")
    assert response.status_code == 200
    assert "Padaria Central" in response.text
    # Cards
    assert "R$ 220,00" in response.text
    assert "Custo por resultado" in response.text
    assert "R$ 22,00" in response.text
    # Two charts, never one with two axes
    assert 'id="chart-spend"' in response.text
    assert 'id="chart-results"' in response.text
    assert "chart.umd.min.js" in response.text
    # The numbers behind the chart are also available as a table
    assert "Ver os números dia a dia" in response.text
    # Per campaign
    assert "Remarketing" in response.text


async def test_the_client_screen_without_data_says_so(
    http: httpx.AsyncClient, clean_tables: AsyncSession
) -> None:
    client = await _make_client(clean_tables, "Sem Anúncios")
    await clean_tables.commit()

    response = await http.get(f"/ads/clients/{client.id}")
    assert response.status_code == 200
    assert "Sem dados neste período" in response.text
    assert 'id="chart-spend"' not in response.text


async def test_an_unknown_client(http: httpx.AsyncClient) -> None:
    response = await http.get("/ads/clients/0f1b4a3c-0000-4000-8000-000000000000")
    assert response.status_code == 404
    assert "Cliente não encontrado." in response.text


async def test_the_accounts_screen_moved_but_still_works(
    http: httpx.AsyncClient,
) -> None:
    response = await http.get("/ads/accounts")
    assert response.status_code == 200
    assert "Contas de anúncios" in response.text


async def test_the_panel_needs_a_session(clean_tables: AsyncSession) -> None:
    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="https://testserver", follow_redirects=False
    ) as anonymous:
        response = await anonymous.get("/ads")
    assert response.status_code == 303
    assert response.headers["location"] == "/login?next=/ads"


# ── Sparklines, variação e o controle de período ──────────────


async def test_the_panel_row_carries_its_own_daily_series(
    clean_tables: AsyncSession,
) -> None:
    """The sparkline needs a shape, and one query has to produce them all."""
    await _seed(clean_tables)

    rows = {row.client_name: row for row in await _service(clean_tables).panel_rows(_this_month())}
    padaria = rows["Padaria Central"]

    assert padaria.has_shape is True
    assert len(padaria.series) == 2
    assert padaria.series[0].day < padaria.series[1].day
    # Today holds both campaigns of that account: 100 + 40.
    assert padaria.series[-1].spend == Decimal("140.00")


async def test_two_accounts_of_one_client_become_one_line(
    clean_tables: AsyncSession,
) -> None:
    padaria = await _make_client(clean_tables, "Padaria Central")
    await clean_tables.commit()
    service = _service(clean_tables)
    await service.link_account(ACCOUNT, client_id=padaria.id)
    await service.link_account(OTHER_ACCOUNT, client_id=padaria.id)

    today = today_local()
    await service.sync_day(
        ACCOUNT, FakeGateway(insights=[_insight(today, spend="100.00")]), day=today
    )
    await service.sync_day(
        OTHER_ACCOUNT,
        FakeGateway(insights=[_insight(today, "9001", spend="50.00")]),
        day=today,
    )
    await clean_tables.commit()

    rows = await service.panel_rows(_this_month())
    assert len(rows[0].series) == 1
    assert rows[0].series[0].spend == Decimal("150.00")


async def test_a_client_screen_compares_with_the_period_before(
    http: httpx.AsyncClient, clean_tables: AsyncSession
) -> None:
    padaria, _ = await _seed(clean_tables)

    response = await http.get(f"/ads/clients/{padaria}")

    assert response.status_code == 200
    # The cards count up, so the value is also an attribute.
    assert 'data-count="220.00"' in response.text
    # Nothing came before this month in the fixture, so the cards say so
    # instead of inventing a percentage.
    assert "sem base de comparação" in response.text
    assert 'class="spark' in response.text


async def test_the_period_control_is_a_set_of_radios(
    http: httpx.AsyncClient, clean_tables: AsyncSession
) -> None:
    await _seed(clean_tables)

    response = await http.get("/ads", params={"preset": PeriodPreset.LAST_7.value})

    assert 'type="radio"' in response.text
    assert 'name="preset"' in response.text
    assert f'value="{PeriodPreset.LAST_7.value}"' in response.text
