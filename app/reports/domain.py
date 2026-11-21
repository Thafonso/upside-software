"""Report entities and pure rules. No FastAPI, no SQLAlchemy, no httpx.

Everything a report shows is frozen into a :class:`Snapshot` the moment it is
generated. Reopening the PDF reads that snapshot again, so a report never
changes after the fact - even though Meta keeps adjusting the numbers behind
it.
"""

from __future__ import annotations

import calendar
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any
from uuid import UUID

SNAPSHOT_VERSION = 1
MIN_REPORT_YEAR = 2020
COMMENT_MAX_LENGTH = 4000
FILENAME_SLUG_MAX_LENGTH = 40

MONTH_NAMES_PT: tuple[str, ...] = (
    "janeiro",
    "fevereiro",
    "março",
    "abril",
    "maio",
    "junho",
    "julho",
    "agosto",
    "setembro",
    "outubro",
    "novembro",
    "dezembro",
)

# The campaign statuses Meta sends, in the words the client reads.
CAMPAIGN_STATUS_LABELS: dict[str, str] = {
    "ACTIVE": "em veiculação",
    "PAUSED": "pausada",
    "ARCHIVED": "arquivada",
    "DELETED": "removida",
    "CAMPAIGN_PAUSED": "pausada",
    "ADSET_PAUSED": "conjunto pausado",
    "IN_PROCESS": "em processamento",
    "WITH_ISSUES": "com pendência",
    "UNKNOWN": "situação desconhecida",
}

ZERO = Decimal("0.00")
CENTS = Decimal("0.01")
ONE_DECIMAL = Decimal("0.1")


class ReportValidationError(ValueError):
    """Form-level validation failure, one message per field."""

    def __init__(self, errors: dict[str, str]) -> None:
        self.errors = errors
        super().__init__("; ".join(f"{field}: {msg}" for field, msg in errors.items()))


class UnknownSnapshotVersion(ValueError):
    """A stored snapshot was written by a newer version of the app."""


# ── mês de referência ─────────────────────────────────────────


@dataclass(frozen=True, slots=True, order=True)
class MonthPeriod:
    """A whole calendar month, which is the only period a report covers."""

    year: int
    month: int

    def __post_init__(self) -> None:
        if not 1 <= self.month <= 12:
            msg = f"mês inválido: {self.month}"
            raise ValueError(msg)

    @property
    def since(self) -> date:
        return date(self.year, self.month, 1)

    @property
    def until(self) -> date:
        return date(self.year, self.month, calendar.monthrange(self.year, self.month)[1])

    @property
    def label(self) -> str:
        """Reads as 'novembro de 2026' - a title the client understands."""
        return f"{MONTH_NAMES_PT[self.month - 1]} de {self.year}"

    @property
    def short_label(self) -> str:
        return f"{self.month:02d}/{self.year}"

    @property
    def short_key(self) -> str:
        """``2026-11``: what the month select sends back."""
        return f"{self.year}-{self.month:02d}"

    @property
    def days(self) -> int:
        return calendar.monthrange(self.year, self.month)[1]

    def previous(self) -> MonthPeriod:
        if self.month == 1:
            return MonthPeriod(self.year - 1, 12)
        return MonthPeriod(self.year, self.month - 1)

    def is_running_on(self, day: date) -> bool:
        """True while the month has not finished, so the report is partial."""
        return self.since <= day <= self.until

    def is_future_on(self, day: date) -> bool:
        return self.since > day

    @classmethod
    def of(cls, day: date) -> MonthPeriod:
        return cls(day.year, day.month)


# ── o que o módulo de anúncios entrega ────────────────────────


@dataclass(frozen=True, slots=True)
class DailyNumbers:
    """One campaign on one day, already converted by the adapter."""

    day: date
    campaign_id: str
    spend: Decimal = ZERO
    impressions: int = 0
    reach: int = 0
    link_clicks: int = 0
    results: int = 0


@dataclass(frozen=True, slots=True)
class CampaignInfo:
    """A campaign's name and situation, as Meta had them at sync time."""

    id: str
    name: str
    status: str = "UNKNOWN"


# ── números do relatório ──────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Totals:
    """What a month added up to."""

    spend: Decimal = ZERO
    results: int = 0
    impressions: int = 0
    reach: int = 0
    link_clicks: int = 0

    @property
    def cost_per_result(self) -> Decimal | None:
        if not self.results:
            return None
        return (self.spend / self.results).quantize(CENTS)

    @property
    def has_data(self) -> bool:
        return bool(self.spend or self.impressions or self.results)

    def to_dict(self) -> dict[str, Any]:
        return {
            "spend": str(self.spend),
            "results": self.results,
            "impressions": self.impressions,
            "reach": self.reach,
            "link_clicks": self.link_clicks,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Totals:
        return cls(
            spend=_as_money(raw.get("spend")),
            results=_as_int(raw.get("results")),
            impressions=_as_int(raw.get("impressions")),
            reach=_as_int(raw.get("reach")),
            link_clicks=_as_int(raw.get("link_clicks")),
        )


@dataclass(frozen=True, slots=True)
class DailyPoint:
    """One day on the chart."""

    day: date
    spend: Decimal
    results: int

    def to_dict(self) -> dict[str, Any]:
        return {"day": self.day.isoformat(), "spend": str(self.spend), "results": self.results}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> DailyPoint:
        return cls(
            day=date.fromisoformat(str(raw["day"])),
            spend=_as_money(raw.get("spend")),
            results=_as_int(raw.get("results")),
        )


@dataclass(frozen=True, slots=True)
class CampaignLine:
    """One campaign over the month, as the table shows it."""

    campaign_id: str
    name: str
    status: str
    spend: Decimal
    results: int
    impressions: int
    link_clicks: int

    @property
    def cost_per_result(self) -> Decimal | None:
        if not self.results:
            return None
        return (self.spend / self.results).quantize(CENTS)

    @property
    def is_running(self) -> bool:
        return self.status.upper() == "ACTIVE"

    @property
    def status_label(self) -> str:
        """The client reads the PDF, so Meta's English never reaches it."""
        return CAMPAIGN_STATUS_LABELS.get(self.status.upper(), "situação desconhecida")

    def to_dict(self) -> dict[str, Any]:
        return {
            "campaign_id": self.campaign_id,
            "name": self.name,
            "status": self.status,
            "spend": str(self.spend),
            "results": self.results,
            "impressions": self.impressions,
            "link_clicks": self.link_clicks,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> CampaignLine:
        return cls(
            campaign_id=str(raw.get("campaign_id", "")),
            name=str(raw.get("name", "")),
            status=str(raw.get("status", "UNKNOWN")),
            spend=_as_money(raw.get("spend")),
            results=_as_int(raw.get("results")),
            impressions=_as_int(raw.get("impressions")),
            link_clicks=_as_int(raw.get("link_clicks")),
        )


class MetricUnit(StrEnum):
    MONEY = "money"
    COUNT = "count"


class MetricGoal(StrEnum):
    """Which way is the good way for this number."""

    HIGHER = "higher"
    LOWER = "lower"
    NEUTRAL = "neutral"


@dataclass(frozen=True, slots=True)
class Metric:
    """One line of the summary: this month against the month before."""

    key: str
    label: str
    unit: MetricUnit
    goal: MetricGoal
    value: Decimal | None
    previous: Decimal | None

    @property
    def change(self) -> Decimal | None:
        """Percent change. ``None`` when the month before gives no base."""
        if self.value is None or self.previous is None or self.previous == 0:
            return None
        return ((self.value - self.previous) / self.previous * 100).quantize(ONE_DECIMAL)

    @property
    def direction(self) -> str:
        """``up`` / ``down`` / ``flat``, or ``none`` without a base."""
        change = self.change
        if change is None:
            return "none"
        if change > 0:
            return "up"
        if change < 0:
            return "down"
        return "flat"

    @property
    def is_good(self) -> bool | None:
        """Whether the move is good news. ``None`` when it cannot be judged.

        A cost per result that went down is good news; spend going down is
        neither good nor bad, so the report does not colour it.
        """
        if self.goal == MetricGoal.NEUTRAL:
            return None
        direction = self.direction
        if direction in ("none", "flat"):
            return None
        if self.goal == MetricGoal.HIGHER:
            return direction == "up"
        return direction == "down"

    @property
    def is_money(self) -> bool:
        return self.unit == MetricUnit.MONEY

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "unit": self.unit.value,
            "goal": self.goal.value,
            "value": None if self.value is None else str(self.value),
            "previous": None if self.previous is None else str(self.previous),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Metric:
        return cls(
            key=str(raw.get("key", "")),
            label=str(raw.get("label", "")),
            unit=MetricUnit(str(raw.get("unit", MetricUnit.COUNT.value))),
            goal=MetricGoal(str(raw.get("goal", MetricGoal.NEUTRAL.value))),
            value=_as_optional_number(raw.get("value")),
            previous=_as_optional_number(raw.get("previous")),
        )


# ── agregação (funções puras) ─────────────────────────────────


def aggregate(rows: list[DailyNumbers]) -> Totals:
    """Add every day of every campaign together."""
    spend = ZERO
    results = impressions = reach = link_clicks = 0
    for row in rows:
        spend += row.spend
        results += row.results
        impressions += row.impressions
        # Reach cannot honestly be summed across days - the same person is
        # counted again - so this is an upper bound, and the PDF never shows
        # it as a headline number.
        reach += row.reach
        link_clicks += row.link_clicks
    return Totals(
        spend=spend.quantize(CENTS),
        results=results,
        impressions=impressions,
        reach=reach,
        link_clicks=link_clicks,
    )


def daily_series(rows: list[DailyNumbers], period: MonthPeriod) -> list[DailyPoint]:
    """One point per day of the month, oldest first, zeros included.

    Every day is there on purpose: a chart that skipped the days with no
    spend would hide exactly the gaps the client should see.
    """
    spend_by_day: dict[date, Decimal] = {}
    results_by_day: dict[date, int] = {}
    for row in rows:
        spend_by_day[row.day] = spend_by_day.get(row.day, ZERO) + row.spend
        results_by_day[row.day] = results_by_day.get(row.day, 0) + row.results

    return [
        DailyPoint(
            day=day,
            spend=spend_by_day.get(day, ZERO).quantize(CENTS),
            results=results_by_day.get(day, 0),
        )
        for day in _each_day(period)
    ]


def campaign_lines(rows: list[DailyNumbers], campaigns: list[CampaignInfo]) -> list[CampaignLine]:
    """Per campaign over the month, biggest spender first."""
    known = {campaign.id: campaign for campaign in campaigns}
    grouped: dict[str, list[DailyNumbers]] = {}
    for row in rows:
        grouped.setdefault(row.campaign_id, []).append(row)

    lines: list[CampaignLine] = []
    for campaign_id, owned in grouped.items():
        totals = aggregate(owned)
        info = known.get(campaign_id)
        lines.append(
            CampaignLine(
                campaign_id=campaign_id,
                # A campaign deleted at Meta still has spend in our tables.
                name=info.name if info else campaign_id,
                status=info.status if info else "UNKNOWN",
                spend=totals.spend,
                results=totals.results,
                impressions=totals.impressions,
                link_clicks=totals.link_clicks,
            )
        )

    return sorted(lines, key=lambda line: (-line.spend, line.name.lower()))


def build_metrics(current: Totals, previous: Totals) -> tuple[Metric, ...]:
    """The summary table, in the order the PDF reads best."""
    return (
        Metric(
            key="spend",
            label="Investimento",
            unit=MetricUnit.MONEY,
            goal=MetricGoal.NEUTRAL,
            value=current.spend,
            previous=previous.spend,
        ),
        Metric(
            key="results",
            label="Resultados",
            unit=MetricUnit.COUNT,
            goal=MetricGoal.HIGHER,
            value=Decimal(current.results),
            previous=Decimal(previous.results),
        ),
        Metric(
            key="cost_per_result",
            label="Custo por resultado",
            unit=MetricUnit.MONEY,
            goal=MetricGoal.LOWER,
            value=current.cost_per_result,
            previous=previous.cost_per_result,
        ),
        Metric(
            key="link_clicks",
            label="Cliques no link",
            unit=MetricUnit.COUNT,
            goal=MetricGoal.HIGHER,
            value=Decimal(current.link_clicks),
            previous=Decimal(previous.link_clicks),
        ),
        Metric(
            key="impressions",
            label="Impressões",
            unit=MetricUnit.COUNT,
            goal=MetricGoal.NEUTRAL,
            value=Decimal(current.impressions),
            previous=Decimal(previous.impressions),
        ),
    )


# ── snapshot ──────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Snapshot:
    """Everything the report shows, frozen at generation time."""

    client_name: str
    result_label: str
    period: MonthPeriod
    totals: Totals
    previous_totals: Totals
    metrics: tuple[Metric, ...]
    daily: tuple[DailyPoint, ...]
    campaigns: tuple[CampaignLine, ...]
    comment: str = ""
    generated_at: datetime | None = None
    generated_by: str = ""
    account_ids: tuple[str, ...] = ()
    partial: bool = False
    version: int = SNAPSHOT_VERSION

    @property
    def previous_period(self) -> MonthPeriod:
        return self.period.previous()

    @property
    def client_initials(self) -> str:
        """Stands in for the client logo on the cover."""
        parts = [piece for piece in self.client_name.split() if piece]
        if not parts:
            return "?"
        if len(parts) == 1:
            return parts[0][:2].upper()
        return (parts[0][:1] + parts[1][:1]).upper()

    @property
    def has_data(self) -> bool:
        return self.totals.has_data

    @property
    def has_comparison(self) -> bool:
        return self.previous_totals.has_data

    @property
    def days_with_spend(self) -> int:
        return sum(1 for point in self.daily if point.spend)

    @property
    def best_day(self) -> DailyPoint | None:
        """The day with most results, worth a line in the summary."""
        with_results = [point for point in self.daily if point.results]
        if not with_results:
            return None
        return max(with_results, key=lambda point: (point.results, point.spend))

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "client_name": self.client_name,
            "result_label": self.result_label,
            "period": {"year": self.period.year, "month": self.period.month},
            "totals": self.totals.to_dict(),
            "previous_totals": self.previous_totals.to_dict(),
            "metrics": [metric.to_dict() for metric in self.metrics],
            "daily": [point.to_dict() for point in self.daily],
            "campaigns": [line.to_dict() for line in self.campaigns],
            "comment": self.comment,
            "generated_at": None if self.generated_at is None else self.generated_at.isoformat(),
            "generated_by": self.generated_by,
            "account_ids": list(self.account_ids),
            "partial": self.partial,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Snapshot:
        version = _as_int(raw.get("version", SNAPSHOT_VERSION))
        if version > SNAPSHOT_VERSION:
            msg = f"snapshot versão {version} não é legível por esta versão do app"
            raise UnknownSnapshotVersion(msg)

        period_raw = raw.get("period") or {}
        moment = raw.get("generated_at")
        return cls(
            client_name=str(raw.get("client_name", "")),
            result_label=str(raw.get("result_label", "")),
            period=MonthPeriod(
                year=_as_int(period_raw.get("year", MIN_REPORT_YEAR)),
                month=_as_int(period_raw.get("month", 1)) or 1,
            ),
            totals=Totals.from_dict(raw.get("totals") or {}),
            previous_totals=Totals.from_dict(raw.get("previous_totals") or {}),
            metrics=tuple(Metric.from_dict(item) for item in raw.get("metrics") or []),
            daily=tuple(DailyPoint.from_dict(item) for item in raw.get("daily") or []),
            campaigns=tuple(CampaignLine.from_dict(item) for item in raw.get("campaigns") or []),
            comment=str(raw.get("comment") or ""),
            generated_at=datetime.fromisoformat(str(moment)) if moment else None,
            generated_by=str(raw.get("generated_by") or ""),
            account_ids=tuple(str(item) for item in raw.get("account_ids") or []),
            partial=bool(raw.get("partial", False)),
            version=version,
        )


def build_snapshot(
    *,
    client_name: str,
    result_label: str,
    period: MonthPeriod,
    rows: list[DailyNumbers],
    previous_rows: list[DailyNumbers],
    campaigns: list[CampaignInfo],
    account_ids: tuple[str, ...] = (),
    comment: str = "",
    generated_at: datetime | None = None,
    generated_by: str = "",
    today: date | None = None,
) -> Snapshot:
    """Build the whole snapshot from the month's numbers. Pure."""
    totals = aggregate(rows)
    previous_totals = aggregate(previous_rows)
    reference = today or (generated_at.date() if generated_at else period.until)

    return Snapshot(
        client_name=client_name,
        result_label=result_label,
        period=period,
        totals=totals,
        previous_totals=previous_totals,
        metrics=build_metrics(totals, previous_totals),
        daily=tuple(daily_series(rows, period)),
        campaigns=tuple(campaign_lines(rows, campaigns)),
        comment=comment,
        generated_at=generated_at,
        generated_by=generated_by,
        account_ids=account_ids,
        partial=period.is_running_on(reference),
    )


# ── relatório ─────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class Report:
    """A generated report. Immutable once created."""

    id: UUID
    client_id: UUID
    year: int
    month: int
    comment: str
    snapshot: Snapshot
    generated_by: str
    created_at: datetime
    updated_at: datetime

    @property
    def period(self) -> MonthPeriod:
        return MonthPeriod(self.year, self.month)

    @property
    def title(self) -> str:
        return f"Relatório de {self.period.label}"

    @property
    def pdf_filename(self) -> str:
        slug = filename_slug(self.snapshot.client_name) or "cliente"
        return f"relatorio-{slug}-{self.year}-{self.month:02d}.pdf"


@dataclass(frozen=True, slots=True)
class ReportTally:
    """How many reports a client has, and which month was the last one."""

    total: int = 0
    latest_period: MonthPeriod | None = None
    latest_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ClientReports:
    """A client on the reports list, with its tally."""

    client_id: UUID
    client_name: str
    tally: ReportTally

    @property
    def total(self) -> int:
        return self.tally.total

    @property
    def latest_period(self) -> MonthPeriod | None:
        return self.tally.latest_period

    @property
    def latest_at(self) -> datetime | None:
        return self.tally.latest_at

    @property
    def has_reports(self) -> bool:
        return self.tally.total > 0


# ── validação do formulário ───────────────────────────────────


@dataclass(frozen=True, slots=True)
class ReportRequest:
    """Validated input for ``GenerateMonthlyReport``."""

    client_id: UUID
    period: MonthPeriod
    comment: str


def recent_months(today: date, count: int = 24) -> list[MonthPeriod]:
    """The months the form offers, newest first, starting with this one."""
    months: list[MonthPeriod] = []
    period = MonthPeriod.of(today)
    for _ in range(max(count, 1)):
        if period.year < MIN_REPORT_YEAR:
            break
        months.append(period)
        period = period.previous()
    return months


def clean_comment(raw: str | None) -> str:
    """Keep the manager's paragraphs, drop the trailing noise."""
    if not raw:
        return ""
    lines = [line.rstrip() for line in raw.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    return "\n".join(lines).strip()[:COMMENT_MAX_LENGTH]


def filename_slug(value: str) -> str:
    """Turns 'Padaria Central' into 'padaria-central' for the file name."""
    ascii_only = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    hyphenated = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_only).strip("-").lower()
    return hyphenated[:FILENAME_SLUG_MAX_LENGTH].strip("-")


def build_report_request(
    *,
    client_id: str,
    year: str | int,
    month: str | int,
    comment: str | None = None,
    today: date,
) -> ReportRequest:
    """Validate raw form values. Raises :class:`ReportValidationError`."""
    errors: dict[str, str] = {}

    parsed_client: UUID | None = None
    try:
        parsed_client = UUID(str(client_id))
    except (ValueError, AttributeError, TypeError):
        errors["client_id"] = "Escolha o cliente deste relatório."

    parsed_year = _as_optional_int(year)
    if parsed_year is None or not MIN_REPORT_YEAR <= parsed_year <= today.year:
        errors["year"] = f"Informe um ano entre {MIN_REPORT_YEAR} e {today.year}."

    parsed_month = _as_optional_int(month)
    if parsed_month is None or not 1 <= parsed_month <= 12:
        errors["month"] = "Escolha um mês de 1 a 12."

    period: MonthPeriod | None = None
    if parsed_year is not None and parsed_month is not None and not errors.get("month"):
        period = MonthPeriod(parsed_year, parsed_month)
        if period.is_future_on(today):
            errors["month"] = "Escolha um mês que já começou."

    if errors or parsed_client is None or period is None:
        raise ReportValidationError(errors)

    return ReportRequest(
        client_id=parsed_client,
        period=period,
        comment=clean_comment(comment),
    )


# ── conversões tolerantes de JSON ─────────────────────────────


def _each_day(period: MonthPeriod) -> list[date]:
    return [date(period.year, period.month, day) for day in range(1, period.days + 1)]


def _as_money(raw: Any) -> Decimal:
    try:
        return Decimal(str(raw if raw is not None else "0")).quantize(CENTS)
    except (InvalidOperation, ValueError, TypeError):
        return ZERO


def _as_int(raw: Any) -> int:
    try:
        return int(raw)
    except (ValueError, TypeError):
        return 0


def _as_optional_int(raw: Any) -> int | None:
    try:
        return int(str(raw).strip())
    except (ValueError, TypeError, AttributeError):
        return None


def _as_optional_number(raw: Any) -> Decimal | None:
    if raw is None:
        return None
    try:
        return Decimal(str(raw))
    except (InvalidOperation, ValueError, TypeError):
        return None
