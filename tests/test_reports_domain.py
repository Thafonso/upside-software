"""The report rules, with no database and no PDF in sight."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from app.reports.domain import (
    CampaignInfo,
    DailyNumbers,
    Metric,
    MetricGoal,
    MetricUnit,
    MonthPeriod,
    ReportValidationError,
    Snapshot,
    Totals,
    aggregate,
    build_metrics,
    build_report_request,
    build_snapshot,
    campaign_lines,
    clean_comment,
    daily_series,
    filename_slug,
    recent_months,
)

TODAY = date(2026, 11, 21)
NOVEMBER = MonthPeriod(2026, 11)
OCTOBER = MonthPeriod(2026, 10)


def _row(day: int, *, campaign: str = "23851", spend: str = "100.00", results: int = 5):
    return DailyNumbers(
        day=date(2026, 11, day),
        campaign_id=campaign,
        spend=Decimal(spend),
        impressions=1000,
        reach=800,
        link_clicks=50,
        results=results,
    )


# ── mês de referência ─────────────────────────────────────────


def test_a_month_knows_its_first_and_last_day() -> None:
    assert NOVEMBER.since == date(2026, 11, 1)
    assert NOVEMBER.until == date(2026, 11, 30)
    assert NOVEMBER.days == 30


def test_february_follows_the_leap_year() -> None:
    assert MonthPeriod(2027, 2).until == date(2027, 2, 28)
    assert MonthPeriod(2028, 2).until == date(2028, 2, 29)


def test_the_previous_month_crosses_the_year() -> None:
    assert NOVEMBER.previous() == OCTOBER
    assert MonthPeriod(2027, 1).previous() == MonthPeriod(2026, 12)


def test_a_month_reads_in_portuguese() -> None:
    assert NOVEMBER.label == "novembro de 2026"
    assert MonthPeriod(2026, 3).label == "março de 2026"
    assert NOVEMBER.short_label == "11/2026"
    assert NOVEMBER.short_key == "2026-11"


def test_a_month_that_has_not_finished_is_still_running() -> None:
    assert NOVEMBER.is_running_on(TODAY) is True
    assert OCTOBER.is_running_on(TODAY) is False
    assert MonthPeriod(2026, 12).is_future_on(TODAY) is True


def test_an_impossible_month_is_refused() -> None:
    with pytest.raises(ValueError, match="mês inválido"):
        MonthPeriod(2026, 13)


def test_the_form_offers_this_month_first_and_goes_back() -> None:
    months = recent_months(TODAY, 3)
    assert months == [NOVEMBER, OCTOBER, MonthPeriod(2026, 9)]


def test_the_form_never_offers_a_month_before_the_app_existed() -> None:
    months = recent_months(date(2020, 2, 10), 12)
    assert months == [MonthPeriod(2020, 2), MonthPeriod(2020, 1)]


# ── agregação ─────────────────────────────────────────────────


def test_totals_add_every_day_of_every_campaign() -> None:
    totals = aggregate([_row(1), _row(2, spend="80.00", results=4), _row(2, campaign="23852")])

    assert totals.spend == Decimal("280.00")
    assert totals.results == 14
    assert totals.link_clicks == 150
    assert totals.cost_per_result == Decimal("20.00")


def test_a_month_without_results_has_no_cost_per_result() -> None:
    totals = aggregate([_row(1, results=0)])
    assert totals.results == 0
    assert totals.cost_per_result is None
    assert totals.has_data is True


def test_an_empty_month_has_nothing_to_show() -> None:
    assert aggregate([]) == Totals()
    assert aggregate([]).has_data is False


def test_the_daily_series_covers_every_day_of_the_month() -> None:
    series = daily_series([_row(1), _row(1, campaign="23852"), _row(15)], NOVEMBER)

    assert len(series) == 30
    assert series[0].day == date(2026, 11, 1)
    # Both campaigns of the first day land on the same point.
    assert series[0].spend == Decimal("200.00")
    assert series[0].results == 10
    # A day with no spend is a zero, not a missing point.
    assert series[1].spend == Decimal("0.00")
    assert series[-1].day == date(2026, 11, 30)


def test_campaigns_come_biggest_spender_first_and_keep_their_names() -> None:
    lines = campaign_lines(
        [_row(1), _row(2), _row(1, campaign="23852", spend="40.00", results=1)],
        [
            CampaignInfo(id="23851", name="Leads - Batel", status="ACTIVE"),
            CampaignInfo(id="23852", name="Remarketing", status="PAUSED"),
        ],
    )

    assert [line.name for line in lines] == ["Leads - Batel", "Remarketing"]
    assert lines[0].spend == Decimal("200.00")
    assert lines[0].is_running is True
    assert lines[1].status_label == "pausada"
    assert lines[1].cost_per_result == Decimal("40.00")


def test_a_campaign_deleted_at_meta_still_appears() -> None:
    lines = campaign_lines([_row(1, campaign="99999")], [])

    assert len(lines) == 1
    assert lines[0].name == "99999"
    assert lines[0].status == "UNKNOWN"
    assert lines[0].status_label == "situação desconhecida"


# ── variação ──────────────────────────────────────────────────


def _metric(value: str | None, previous: str | None, goal: MetricGoal) -> Metric:
    return Metric(
        key="test",
        label="Teste",
        unit=MetricUnit.MONEY,
        goal=goal,
        value=None if value is None else Decimal(value),
        previous=None if previous is None else Decimal(previous),
    )


def test_a_percent_change_is_rounded_to_one_decimal() -> None:
    metric = _metric("150.00", "120.00", MetricGoal.HIGHER)
    assert metric.change == Decimal("25.0")
    assert metric.direction == "up"


def test_without_a_previous_month_there_is_no_percentage() -> None:
    assert _metric("150.00", "0.00", MetricGoal.HIGHER).change is None
    assert _metric("150.00", None, MetricGoal.HIGHER).change is None
    assert _metric(None, "10.00", MetricGoal.HIGHER).change is None
    assert _metric("150.00", "0.00", MetricGoal.HIGHER).direction == "none"


def test_more_results_is_good_news_and_a_cheaper_result_too() -> None:
    assert _metric("20", "10", MetricGoal.HIGHER).is_good is True
    assert _metric("5", "10", MetricGoal.HIGHER).is_good is False
    assert _metric("8.00", "10.00", MetricGoal.LOWER).is_good is True
    assert _metric("12.00", "10.00", MetricGoal.LOWER).is_good is False


def test_spending_more_is_neither_good_nor_bad() -> None:
    assert _metric("200.00", "100.00", MetricGoal.NEUTRAL).is_good is None
    assert _metric("100.00", "100.00", MetricGoal.HIGHER).is_good is None
    assert _metric("100.00", "100.00", MetricGoal.HIGHER).direction == "flat"


def test_the_summary_compares_the_five_numbers_that_matter() -> None:
    current = aggregate([_row(1), _row(2)])
    previous = Totals(spend=Decimal("100.00"), results=10, impressions=500, link_clicks=40)

    metrics = build_metrics(current, previous)

    assert [metric.key for metric in metrics] == [
        "spend",
        "results",
        "cost_per_result",
        "link_clicks",
        "impressions",
    ]
    by_key = {metric.key: metric for metric in metrics}
    assert by_key["spend"].value == Decimal("200.00")
    assert by_key["spend"].change == Decimal("100.0")
    # 200 / 10 = 20 now, against 100 / 10 = 10 before: twice as expensive.
    assert by_key["cost_per_result"].change == Decimal("100.0")
    assert by_key["cost_per_result"].is_good is False


# ── snapshot ──────────────────────────────────────────────────


def _snapshot(**overrides) -> Snapshot:
    base = {
        "client_name": "Padaria Central",
        "result_label": "Cadastro de lead",
        "period": NOVEMBER,
        "rows": [_row(1), _row(2, spend="80.00", results=4)],
        "previous_rows": [],
        "campaigns": [CampaignInfo(id="23851", name="Leads", status="ACTIVE")],
        "account_ids": ("act_1",),
        "comment": "Mês de teste.",
        "generated_at": datetime(2026, 12, 1, 9, 0, tzinfo=UTC),
        "generated_by": "Ana",
        "today": date(2026, 12, 1),
    }
    return build_snapshot(**{**base, **overrides})


def test_a_snapshot_holds_everything_the_pdf_shows() -> None:
    snapshot = _snapshot()

    assert snapshot.totals.spend == Decimal("180.00")
    assert snapshot.has_data is True
    assert snapshot.has_comparison is False
    assert len(snapshot.daily) == 30
    assert snapshot.days_with_spend == 2
    assert snapshot.best_day is not None
    assert snapshot.best_day.day == date(2026, 11, 1)
    assert snapshot.campaigns[0].name == "Leads"
    assert snapshot.previous_period == OCTOBER
    assert snapshot.client_initials == "PC"


def test_a_month_still_running_is_marked_partial() -> None:
    assert _snapshot(today=date(2026, 11, 15)).partial is True
    assert _snapshot(today=date(2026, 12, 1)).partial is False


def test_a_snapshot_survives_the_round_trip_through_json() -> None:
    import json

    original = _snapshot(previous_rows=[_row(1, spend="50.00", results=2)])
    reloaded = Snapshot.from_dict(json.loads(json.dumps(original.to_dict())))

    assert reloaded == original


def test_a_snapshot_from_a_newer_version_is_refused() -> None:
    from app.reports.domain import UnknownSnapshotVersion

    raw = _snapshot().to_dict()
    raw["version"] = 99

    with pytest.raises(UnknownSnapshotVersion):
        Snapshot.from_dict(raw)


def test_an_empty_month_still_makes_a_snapshot() -> None:
    snapshot = _snapshot(rows=[], previous_rows=[], campaigns=[])

    assert snapshot.has_data is False
    assert snapshot.campaigns == ()
    assert snapshot.best_day is None
    assert len(snapshot.daily) == 30


# ── formulário ────────────────────────────────────────────────


def test_the_form_accepts_a_past_month() -> None:
    client_id = uuid4()
    request = build_report_request(
        client_id=str(client_id),
        year="2026",
        month="10",
        comment="  Primeiro mês.  ",
        today=TODAY,
    )

    assert request.client_id == client_id
    assert request.period == OCTOBER
    assert request.comment == "Primeiro mês."


def test_the_form_refuses_a_month_that_has_not_started() -> None:
    with pytest.raises(ReportValidationError) as raised:
        build_report_request(client_id=str(uuid4()), year="2026", month="12", today=TODAY)

    assert "month" in raised.value.errors


@pytest.mark.parametrize(
    ("year", "month", "field"),
    [
        ("2019", "5", "year"),
        ("abc", "5", "year"),
        ("2026", "0", "month"),
        ("2026", "13", "month"),
        ("2026", "", "month"),
    ],
)
def test_the_form_says_which_field_is_wrong(year: str, month: str, field: str) -> None:
    with pytest.raises(ReportValidationError) as raised:
        build_report_request(client_id=str(uuid4()), year=year, month=month, today=TODAY)

    assert field in raised.value.errors


def test_the_form_refuses_something_that_is_not_a_client() -> None:
    with pytest.raises(ReportValidationError) as raised:
        build_report_request(client_id="nao-e-uuid", year="2026", month="10", today=TODAY)

    assert "client_id" in raised.value.errors


def test_the_comment_keeps_its_paragraphs() -> None:
    assert clean_comment("Linha um.\r\n\r\nLinha dois.  \n") == "Linha um.\n\nLinha dois."
    assert clean_comment(None) == ""
    assert clean_comment("   ") == ""


def test_the_file_name_is_safe_to_download() -> None:
    assert filename_slug("Padaria Central") == "padaria-central"
    assert filename_slug("Clínica Vida & Saúde") == "clinica-vida-saude"
    assert filename_slug("***") == ""
