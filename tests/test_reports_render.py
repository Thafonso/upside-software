"""The chart and the PDF: a snapshot in, bytes out."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from app.reports.domain import CampaignInfo, DailyNumbers, MonthPeriod, Snapshot, build_snapshot
from app.reports.render import chart_data_uri, monthly_chart_png, snapshot_to_html, snapshot_to_pdf

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
PDF_MAGIC = b"%PDF"

NOVEMBER = MonthPeriod(2026, 11)


def _rows() -> list[DailyNumbers]:
    return [
        DailyNumbers(
            day=date(2026, 11, day),
            campaign_id="23851",
            spend=Decimal("100.00") + Decimal(day),
            impressions=1000 * day,
            reach=800 * day,
            link_clicks=50,
            results=day % 7,
        )
        for day in range(1, 31)
    ]


def _snapshot(**overrides) -> Snapshot:
    base = {
        "client_name": "Padaria Central",
        "result_label": "Cadastro de lead",
        "period": NOVEMBER,
        "rows": _rows(),
        "previous_rows": _rows()[:10],
        "campaigns": [
            CampaignInfo(id="23851", name="Leads - Batel", status="ACTIVE"),
        ],
        "account_ids": ("act_123456789",),
        "comment": "Primeiro mês com a campanha de bairro.\n\nSeguimos em dezembro.",
        "generated_at": datetime(2026, 12, 1, 9, 30, tzinfo=UTC),
        "generated_by": "Ana Souza",
        "today": date(2026, 12, 1),
    }
    return build_snapshot(**{**base, **overrides})


# ── gráfico ───────────────────────────────────────────────────


def test_the_daily_chart_is_a_png() -> None:
    png = monthly_chart_png(_snapshot().daily)

    assert png.startswith(PNG_MAGIC)
    assert len(png) > 5_000


def test_a_month_without_movement_still_draws() -> None:
    png = monthly_chart_png(_snapshot(rows=[], previous_rows=[]).daily)
    assert png.startswith(PNG_MAGIC)


def test_the_chart_travels_inside_the_document() -> None:
    uri = chart_data_uri(_snapshot().daily)
    assert uri.startswith("data:image/png;base64,")


# ── página do PDF ─────────────────────────────────────────────


def test_the_printable_page_says_what_the_client_needs() -> None:
    html = snapshot_to_html(_snapshot())

    assert "Padaria Central" in html
    assert "novembro de 2026" in html
    assert "Relatório mensal de anúncios" in html
    assert "Leads - Batel" in html
    assert "Comentário do gestor" in html
    assert "Seguimos em dezembro." in html
    # The chart is embedded, never fetched while printing.
    assert "data:image/png;base64," in html
    assert "http://" not in html


def test_a_partial_month_warns_on_the_page() -> None:
    html = snapshot_to_html(_snapshot(today=date(2026, 11, 20)))
    assert "ainda estava em andamento" in html


def test_a_month_without_data_explains_itself() -> None:
    html = snapshot_to_html(_snapshot(rows=[], previous_rows=[], account_ids=()))

    assert "Não há métricas de anúncios" in html
    assert "Nenhuma conta de anúncios" in html
    assert "Por campanha" not in html


def test_the_summary_shows_the_variation_against_the_month_before() -> None:
    html = snapshot_to_html(_snapshot())

    assert "Variação" in html
    assert "10/2026" in html
    assert "delta--" in html


# ── PDF ───────────────────────────────────────────────────────


def test_the_report_renders_as_a_pdf() -> None:
    pdf = snapshot_to_pdf(_snapshot())

    assert pdf.startswith(PDF_MAGIC)
    assert len(pdf) > 10_000


def test_the_same_snapshot_always_gives_the_same_report() -> None:
    snapshot = _snapshot()
    assert snapshot_to_html(snapshot) == snapshot_to_html(snapshot)


def test_an_empty_report_is_still_a_valid_pdf() -> None:
    pdf = snapshot_to_pdf(_snapshot(rows=[], previous_rows=[], campaigns=[], comment=""))
    assert pdf.startswith(PDF_MAGIC)
