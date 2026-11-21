"""Reports against a real Postgres: the snapshot, the history and the screens."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.ads.domain import Campaign, DailyInsight
from app.ads.repository import SqlAlchemyAdsRepository
from app.ads.service import AdsService
from app.clients import ClientsApi
from app.clients.domain import ClientInput, ClientStatus
from app.clients.repository import SqlAlchemyClientRepository
from app.clients.service import ClientsService
from app.reports.api import build_service
from app.reports.domain import MonthPeriod
from app.reports.render import snapshot_to_pdf
from app.reports.service import ReportsService, UnknownClient
from app.shared import events
from app.shared.clock import today_local
from app.shared.seed import seed_admin
from tests.conftest import ADMIN_EMAIL, ADMIN_PASSWORD
from tests.test_integration_ads import FakeGateway

pytestmark = pytest.mark.integration

ACCOUNT = "act_123456789"
LEADS = "23851"
REMARKETING = "23852"

# Derived from today, so the suite does not rot when the calendar moves on.
LAST_MONTH = MonthPeriod.of(today_local()).previous()
MONTH_BEFORE = LAST_MONTH.previous()


def _reports(session: AsyncSession) -> ReportsService:
    return build_service(session)


def _ads(session: AsyncSession) -> AdsService:
    return AdsService(SqlAlchemyAdsRepository(session), ClientsApi(session))


async def _make_client(session: AsyncSession, name: str):
    service = ClientsService(SqlAlchemyClientRepository(session))
    return await service.create(
        ClientInput(name=name, status=ClientStatus.ACTIVE, result_action_type="lead")
    )


def _insight(
    day: date,
    campaign_id: str = LEADS,
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


async def _sync(session: AsyncSession, insights: list[DailyInsight]) -> None:
    """Store those insights, one day at a time, as the hourly job would."""
    gateway = FakeGateway(insights=insights)
    for day in sorted({insight.day for insight in insights}):
        await _ads(session).sync_day(ACCOUNT, gateway, day=day)


async def _seed(session: AsyncSession) -> UUID:
    """One client with two months of history: the month before is the base."""
    client = await _make_client(session, "Padaria Central")
    await session.commit()

    ads = _ads(session)
    await ads.link_account(ACCOUNT, client_id=client.id)
    await ads.sync_structure(
        ACCOUNT,
        FakeGateway(
            campaigns=[
                Campaign(id=LEADS, account_id=ACCOUNT, name="Leads - Batel", status="ACTIVE"),
                Campaign(id=REMARKETING, account_id=ACCOUNT, name="Remarketing", status="PAUSED"),
            ]
        ),
    )

    first = LAST_MONTH.since
    await _sync(
        session,
        [
            # Mês do relatório: 220,00 e 10 resultados.
            _insight(first, spend="100.00", leads=5),
            _insight(first + timedelta(days=1), spend="80.00", leads=3),
            _insight(
                first + timedelta(days=1),
                REMARKETING,
                spend="40.00",
                leads=2,
                name="Remarketing",
            ),
            # Mês anterior: 100,00 e 10 resultados.
            _insight(MONTH_BEFORE.since, spend="100.00", leads=10),
        ],
    )
    await session.commit()
    return client.id


# ── snapshot ──────────────────────────────────────────────────


async def test_the_snapshot_holds_the_month_it_was_asked_for(
    clean_tables: AsyncSession,
) -> None:
    client_id = await _seed(clean_tables)

    report = await _reports(clean_tables).generate_monthly_report(
        client_id=client_id, period=LAST_MONTH, comment="Mês de teste."
    )

    snapshot = report.snapshot
    assert snapshot.client_name == "Padaria Central"
    assert snapshot.result_label == "Cadastro de lead"
    assert snapshot.totals.spend == Decimal("220.00")
    assert snapshot.totals.results == 10
    assert snapshot.totals.cost_per_result == Decimal("22.00")
    assert snapshot.account_ids == (ACCOUNT,)
    assert snapshot.comment == "Mês de teste."
    assert snapshot.partial is False
    assert len(snapshot.daily) == LAST_MONTH.days


async def test_the_snapshot_compares_with_the_month_before(
    clean_tables: AsyncSession,
) -> None:
    client_id = await _seed(clean_tables)

    report = await _reports(clean_tables).generate_monthly_report(
        client_id=client_id, period=LAST_MONTH
    )

    by_key = {metric.key: metric for metric in report.snapshot.metrics}
    assert report.snapshot.previous_totals.spend == Decimal("100.00")
    assert by_key["spend"].change == Decimal("120.0")
    # Same number of results on more than twice the money: worse, and said so.
    assert by_key["results"].change == Decimal("0.0")
    assert by_key["cost_per_result"].change == Decimal("120.0")
    assert by_key["cost_per_result"].is_good is False


async def test_the_campaign_table_names_every_campaign_with_spend(
    clean_tables: AsyncSession,
) -> None:
    client_id = await _seed(clean_tables)

    report = await _reports(clean_tables).generate_monthly_report(
        client_id=client_id, period=LAST_MONTH
    )

    campaigns = report.snapshot.campaigns
    assert [line.name for line in campaigns] == ["Leads - Batel", "Remarketing"]
    assert campaigns[0].spend == Decimal("180.00")
    assert campaigns[0].is_running is True
    assert campaigns[1].spend == Decimal("40.00")


async def test_a_generated_report_never_changes(clean_tables: AsyncSession) -> None:
    """The whole point of the snapshot: Meta can keep adjusting the month."""
    client_id = await _seed(clean_tables)
    service = _reports(clean_tables)

    report = await service.generate_monthly_report(client_id=client_id, period=LAST_MONTH)
    await clean_tables.commit()

    # Meta revises the month upwards after the report went out.
    await _sync(clean_tables, [_insight(LAST_MONTH.since, spend="999.00", leads=99)])
    await clean_tables.commit()

    reopened = await service.get_report(report.id)
    assert reopened is not None
    assert reopened.snapshot.totals.spend == Decimal("220.00")
    assert reopened.snapshot == report.snapshot


async def test_a_client_without_an_ad_account_still_gets_a_report(
    clean_tables: AsyncSession,
) -> None:
    client = await _make_client(clean_tables, "Clinica Vida")
    await clean_tables.commit()

    report = await _reports(clean_tables).generate_monthly_report(
        client_id=client.id, period=LAST_MONTH, comment="Começamos agora."
    )

    assert report.snapshot.has_data is False
    assert report.snapshot.account_ids == ()
    assert report.snapshot.campaigns == ()
    assert report.snapshot.comment == "Começamos agora."


async def test_a_report_for_a_client_that_does_not_exist_is_refused(
    clean_tables: AsyncSession,
) -> None:
    with pytest.raises(UnknownClient):
        await _reports(clean_tables).generate_monthly_report(client_id=uuid4(), period=LAST_MONTH)


async def test_generating_a_report_announces_it(clean_tables: AsyncSession) -> None:
    client_id = await _seed(clean_tables)
    events.reset()
    seen: list[events.ReportGenerated] = []
    events.subscribe(events.ReportGenerated, seen.append)

    try:
        report = await _reports(clean_tables).generate_monthly_report(
            client_id=client_id, period=LAST_MONTH
        )
    finally:
        events.reset()

    assert [event.report_id for event in seen] == [report.id]


# ── histórico ─────────────────────────────────────────────────


async def test_the_history_keeps_every_run_newest_month_first(
    clean_tables: AsyncSession,
) -> None:
    client_id = await _seed(clean_tables)
    service = _reports(clean_tables)

    await service.generate_monthly_report(client_id=client_id, period=MONTH_BEFORE)
    await service.generate_monthly_report(client_id=client_id, period=LAST_MONTH)
    await service.generate_monthly_report(
        client_id=client_id, period=LAST_MONTH, comment="Segunda versão."
    )

    history = await service.list_for_client(client_id)
    assert len(history) == 3
    assert [report.period for report in history] == [LAST_MONTH, LAST_MONTH, MONTH_BEFORE]
    assert history[0].comment == "Segunda versão."
    assert await service.reports_in_month(client_id, LAST_MONTH) == 2
    assert await service.reports_in_month(client_id, MonthPeriod(2020, 1)) == 0


async def test_the_client_list_counts_and_names_the_latest_month(
    clean_tables: AsyncSession,
) -> None:
    client_id = await _seed(clean_tables)
    other = await _make_client(clean_tables, "Clinica Vida")
    await clean_tables.commit()
    service = _reports(clean_tables)

    # Generated out of order on purpose: the latest month is not the last run.
    await service.generate_monthly_report(client_id=client_id, period=LAST_MONTH)
    await service.generate_monthly_report(client_id=client_id, period=MONTH_BEFORE)

    rows = {row.client_name: row for row in await service.client_rows()}
    assert rows["Padaria Central"].total == 2
    assert rows["Padaria Central"].latest_period == LAST_MONTH
    assert rows["Clinica Vida"].has_reports is False
    assert rows["Clinica Vida"].total == 0
    assert other.id == rows["Clinica Vida"].client_id


# ── PDF ───────────────────────────────────────────────────────


async def test_the_pdf_comes_out_of_the_stored_snapshot(
    clean_tables: AsyncSession,
) -> None:
    client_id = await _seed(clean_tables)
    service = _reports(clean_tables)
    report = await service.generate_monthly_report(
        client_id=client_id, period=LAST_MONTH, comment="Comentário no PDF."
    )

    reopened = await service.get_report(report.id)
    assert reopened is not None
    assert snapshot_to_pdf(reopened.snapshot).startswith(b"%PDF")
    assert reopened.pdf_filename.startswith("relatorio-padaria-central-")
    assert reopened.pdf_filename.endswith(".pdf")


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


async def test_the_list_screen_shows_every_client(
    http: httpx.AsyncClient, clean_tables: AsyncSession
) -> None:
    await _seed(clean_tables)

    response = await http.get("/reports")
    assert response.status_code == 200
    assert "Padaria Central" in response.text
    assert "nenhum relatório ainda" in response.text


async def test_the_client_screen_offers_the_months(
    http: httpx.AsyncClient, clean_tables: AsyncSession
) -> None:
    client_id = await _seed(clean_tables)

    response = await http.get(f"/reports/clients/{client_id}")
    assert response.status_code == 200
    assert "Gerar relatório" in response.text
    assert LAST_MONTH.label in response.text
    # The month that just ended is the one already chosen.
    assert f'value="{LAST_MONTH.short_key}" selected' in response.text


async def test_generating_from_the_screen_opens_the_report(
    http: httpx.AsyncClient, clean_tables: AsyncSession
) -> None:
    client_id = await _seed(clean_tables)

    created = await http.post(
        f"/reports/clients/{client_id}",
        data={"period": LAST_MONTH.short_key, "comment": "Fechamos bem o mês."},
    )
    assert created.status_code == 303

    opened = await http.get(created.headers["location"])
    assert opened.status_code == 200
    assert "R$ 220,00" in opened.text
    assert "Fechamos bem o mês." in opened.text
    assert "Relatório gerado." in opened.text


async def test_a_month_that_has_not_started_is_refused_on_the_screen(
    http: httpx.AsyncClient, clean_tables: AsyncSession
) -> None:
    client_id = await _seed(clean_tables)
    ahead = MonthPeriod.of(today_local() + timedelta(days=45))

    response = await http.post(
        f"/reports/clients/{client_id}", data={"period": ahead.short_key, "comment": ""}
    )
    assert response.status_code == 422
    assert "Escolha um mês que já começou." in response.text


async def test_the_pdf_downloads_with_a_readable_name(
    http: httpx.AsyncClient, clean_tables: AsyncSession
) -> None:
    client_id = await _seed(clean_tables)
    created = await http.post(
        f"/reports/clients/{client_id}", data={"period": LAST_MONTH.short_key}
    )
    report_url = created.headers["location"].split("?")[0]

    response = await http.get(f"{report_url}/pdf")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert "relatorio-padaria-central-" in response.headers["content-disposition"]
    assert response.content.startswith(b"%PDF")


async def test_a_report_that_does_not_exist_answers_404(http: httpx.AsyncClient) -> None:
    assert (await http.get(f"/reports/{uuid4()}")).status_code == 404
    assert (await http.get(f"/reports/{uuid4()}/pdf")).status_code == 404
    assert (await http.get(f"/reports/clients/{uuid4()}")).status_code == 404
