"""Turning a snapshot into a PDF: matplotlib for the chart, WeasyPrint for the page.

Nothing here reads the database. Give it a snapshot and it gives back bytes,
which is what makes a report reproducible: the same snapshot always renders
the same document.

WeasyPrint and matplotlib are imported inside the functions on purpose. Both
are heavy, and the web process should not pay for them until someone actually
asks for a PDF.
"""

from __future__ import annotations

import base64
import io
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path

from app.reports.domain import DailyPoint, Snapshot
from app.shared.templating import format_number, templates

PDF_TEMPLATE = "reports/monthly.html"
STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

# The brand palette, repeated here because the PDF cannot load the app's
# stylesheet: a document has to carry its own colours.
INK = "#16120D"
TERRACOTA = "#B85F40"
PAPER = "#F4F1EB"
LINE = "#D8D0C4"
PEDRA = "#8C8174"

CHART_WIDTH_INCHES = 7.4
CHART_HEIGHT_INCHES = 3.9
CHART_DPI = 150


def _money_formatter(value: float, _position: int) -> str:
    """Y axis of the spend chart: 'R$ 1.200' with no cents."""
    return f"R$ {format_number(Decimal(str(round(value))), 0)}"


def _count_formatter(value: float, _position: int) -> str:
    if value != int(value):
        return ""
    return format_number(int(value), 0)


def monthly_chart_png(points: Sequence[DailyPoint]) -> bytes:
    """Two stacked charts over the same days: spend, then results.

    Not one chart with two axes: money and counts have different scales, and
    overlaying them draws a correlation that is not in the data.
    """
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure
    from matplotlib.ticker import FuncFormatter, MaxNLocator

    days = [point.day.day for point in points]
    spend = [float(point.spend) for point in points]
    results = [point.results for point in points]

    figure = Figure(
        figsize=(CHART_WIDTH_INCHES, CHART_HEIGHT_INCHES),
        dpi=CHART_DPI,
        facecolor=PAPER,
    )
    top, bottom = figure.subplots(2, 1, sharex=True)

    top.bar(days, spend, color=TERRACOTA, width=0.68)
    top.set_ylabel("Investimento", color=INK, fontsize=8)
    top.yaxis.set_major_locator(MaxNLocator(nbins=4))
    top.yaxis.set_major_formatter(FuncFormatter(_money_formatter))

    bottom.bar(days, results, color=INK, width=0.68)
    bottom.set_ylabel("Resultados", color=INK, fontsize=8)
    bottom.set_xlabel("Dia do mês", color=INK, fontsize=8)
    bottom.yaxis.set_major_locator(MaxNLocator(integer=True, nbins=5))
    bottom.yaxis.set_major_formatter(FuncFormatter(_count_formatter))

    for axes in (top, bottom):
        axes.set_facecolor(PAPER)
        axes.grid(axis="y", color=LINE, linewidth=0.6)
        axes.set_axisbelow(True)
        axes.tick_params(colors=PEDRA, labelsize=7, length=0)
        for side in ("top", "right", "left"):
            axes.spines[side].set_visible(False)
        axes.spines["bottom"].set_color(LINE)

    if days:
        # Every other day keeps the labels readable on a 31-day month.
        bottom.set_xticks([day for day in days if day % 2 == 1])
        bottom.set_xlim(min(days) - 0.6, max(days) + 0.6)

    figure.tight_layout(pad=0.8)

    buffer = io.BytesIO()
    FigureCanvasAgg(figure).print_png(buffer)
    return buffer.getvalue()


def chart_data_uri(points: Sequence[DailyPoint]) -> str:
    """The chart as a ``data:`` URI, so the PDF carries no external file."""
    encoded = base64.b64encode(monthly_chart_png(points)).decode("ascii")
    return f"data:image/png;base64,{encoded}"


def snapshot_to_html(snapshot: Snapshot) -> str:
    """Render the printable page. Useful on its own for debugging the layout."""
    template = templates.env.get_template(PDF_TEMPLATE)
    return template.render(
        snapshot=snapshot,
        chart_uri=chart_data_uri(snapshot.daily) if snapshot.has_data else "",
        brand_name=templates.env.globals["brand_name"],
    )


def snapshot_to_pdf(snapshot: Snapshot) -> bytes:
    """The report as a PDF file."""
    from weasyprint import HTML

    document = HTML(string=snapshot_to_html(snapshot), base_url=str(STATIC_DIR))
    return document.write_pdf() or b""
