"""Period maths and the aggregates the panel is built from."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import uuid4

import pytest

from app.ads.domain import (
    CampaignTotals,
    ClientPanelRow,
    Period,
    PeriodPreset,
    Totals,
    month_bounds,
    resolve_period,
)
from app.ads.web import read_period

TODAY = date(2026, 11, 21)


# ── Períodos ──────────────────────────────────────────────────


def test_month_bounds_handles_the_turn_of_the_year() -> None:
    assert month_bounds(date(2026, 12, 15)) == (date(2026, 12, 1), date(2026, 12, 31))
    assert month_bounds(date(2027, 1, 1)) == (date(2027, 1, 1), date(2027, 1, 31))
    assert month_bounds(date(2028, 2, 10)) == (date(2028, 2, 1), date(2028, 2, 29))


def test_this_month_stops_today_not_at_the_end_of_the_month() -> None:
    period = resolve_period(PeriodPreset.THIS_MONTH, TODAY)
    assert period.since == date(2026, 11, 1)
    assert period.until == TODAY
    assert period.label == "Este mês"


def test_last_month_is_the_whole_previous_month() -> None:
    period = resolve_period(PeriodPreset.LAST_MONTH, TODAY)
    assert period.since == date(2026, 10, 1)
    assert period.until == date(2026, 10, 31)


def test_last_month_crosses_january() -> None:
    period = resolve_period(PeriodPreset.LAST_MONTH, date(2027, 1, 10))
    assert period.since == date(2026, 12, 1)
    assert period.until == date(2026, 12, 31)


@pytest.mark.parametrize(("preset", "days"), [(PeriodPreset.LAST_7, 7), (PeriodPreset.LAST_30, 30)])
def test_rolling_windows_include_today(preset: PeriodPreset, days: int) -> None:
    period = resolve_period(preset, TODAY)
    assert period.until == TODAY
    assert period.days == days


def test_a_custom_period_describes_itself() -> None:
    period = Period(since=date(2026, 11, 1), until=date(2026, 11, 10))
    assert period.label == "01/11/2026 a 10/11/2026"
    assert period.days == 10


def test_the_previous_period_is_the_same_length_right_before() -> None:
    period = Period(since=date(2026, 11, 11), until=date(2026, 11, 20))
    previous = period.previous()
    assert previous.since == date(2026, 11, 1)
    assert previous.until == date(2026, 11, 10)
    assert previous.days == period.days


# ── Leitura do filtro ─────────────────────────────────────────


def test_two_valid_dates_make_a_custom_period() -> None:
    period = read_period("this_month", "2026-11-01", "2026-11-10")
    assert period.preset is None
    assert period.since == date(2026, 11, 1)
    assert period.until == date(2026, 11, 10)


@pytest.mark.parametrize(
    ("since", "until"),
    [
        ("", ""),
        ("2026-11-10", ""),
        ("ontem", "hoje"),
        ("2026-11-20", "2026-11-10"),  # invertido
    ],
)
def test_an_unusable_range_falls_back_to_the_preset(since: str, until: str) -> None:
    assert read_period("last_7", since, until).preset == PeriodPreset.LAST_7


def test_an_unknown_preset_falls_back_to_this_month() -> None:
    assert read_period("semana_passada", "", "").preset == PeriodPreset.THIS_MONTH


# ── Totais ────────────────────────────────────────────────────


def test_cost_per_result() -> None:
    assert Totals(spend=Decimal("150.00"), results=7).cost_per_result == Decimal("21.43")


def test_cost_per_result_without_results() -> None:
    assert Totals(spend=Decimal("150.00"), results=0).cost_per_result is None


def test_has_data_tells_an_empty_period_apart() -> None:
    assert not Totals().has_data
    assert Totals(spend=Decimal("1.00")).has_data
    assert Totals(impressions=10).has_data


def test_campaign_totals_carry_their_own_cost() -> None:
    campaign = CampaignTotals(
        campaign_id="1",
        name="Leads",
        status="ACTIVE",
        spend=Decimal("300.00"),
        results=12,
        impressions=1000,
        link_clicks=90,
    )
    assert campaign.cost_per_result == Decimal("25.00")
    assert campaign.is_running


def test_a_panel_row_knows_whether_it_has_accounts() -> None:
    row = ClientPanelRow(client_id=uuid4(), client_name="Padaria", account_ids=(), totals=Totals())
    assert not row.has_accounts
    assert not row.totals.has_data
