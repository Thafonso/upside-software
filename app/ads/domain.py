"""Ads entities and pure rules. No FastAPI, no SQLAlchemy, no httpx.

Read-only by design: the MVP never calls a write endpoint at Meta.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Any
from uuid import UUID

ACCOUNT_ID_PATTERN = re.compile(r"^act_\d{5,20}$")
DIGITS_ONLY = re.compile(r"^\d{5,20}$")

# Meta keeps adjusting the last few days, so a daily sync is not enough.
RESYNC_DAYS = 7


class AccountStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    ARCHIVED = "archived"

    @property
    def label(self) -> str:
        return {
            AccountStatus.ACTIVE: "Sincronizando",
            AccountStatus.PAUSED: "Pausada",
            AccountStatus.ARCHIVED: "Arquivada",
        }[self]


class SyncKind(StrEnum):
    """Which of the three syncs ran."""

    DAILY = "daily"
    RESYNC = "resync"
    STRUCTURE = "structure"

    @property
    def label(self) -> str:
        return {
            SyncKind.DAILY: "Dia de hoje",
            SyncKind.RESYNC: "Últimos 7 dias",
            SyncKind.STRUCTURE: "Nomes e situações",
        }[self]


class SyncStatus(StrEnum):
    OK = "ok"
    FAILED = "failed"

    @property
    def label(self) -> str:
        return {SyncStatus.OK: "Concluída", SyncStatus.FAILED: "Falhou"}[self]


class AccountValidationError(ValueError):
    """Form-level validation failure, one message per field."""

    def __init__(self, errors: dict[str, str]) -> None:
        self.errors = errors
        super().__init__("; ".join(f"{field}: {msg}" for field, msg in errors.items()))


def normalize_account_id(raw: str | None) -> str | None:
    """Accept ``act_123`` or ``123`` and always store ``act_123``."""
    if not raw:
        return None
    cleaned = raw.strip().lower().replace(" ", "")
    if DIGITS_ONLY.match(cleaned):
        cleaned = f"act_{cleaned}"
    return cleaned if ACCOUNT_ID_PATTERN.match(cleaned) else None


@dataclass(frozen=True, slots=True)
class AdAccount:
    """A Meta ad account the agency reads for one client."""

    id: str
    client_id: UUID
    name: str
    status: AccountStatus
    currency: str
    last_synced_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @property
    def status_label(self) -> str:
        return self.status.label

    @property
    def is_active(self) -> bool:
        return self.status == AccountStatus.ACTIVE

    @property
    def numeric_id(self) -> str:
        return self.id.removeprefix("act_")


@dataclass(frozen=True, slots=True)
class Campaign:
    """A campaign, as Meta names it."""

    id: str
    account_id: str
    name: str
    status: str
    objective: str | None = None

    @property
    def is_running(self) -> bool:
        return self.status.upper() == "ACTIVE"


@dataclass(frozen=True, slots=True)
class DailyInsight:
    """One campaign on one day, straight from the Graph API."""

    day: date
    campaign_id: str
    campaign_name: str
    spend: Decimal = Decimal("0.00")
    impressions: int = 0
    reach: int = 0
    link_clicks: int = 0
    actions: dict[str, int] = field(default_factory=dict)
    action_values: dict[str, Decimal] = field(default_factory=dict)

    def results_for(self, action_type: str) -> int:
        """How many results this day produced for the client's own goal."""
        return self.actions.get(action_type, 0)


@dataclass(frozen=True, slots=True)
class DailyRow:
    """A stored day of a campaign, with the result already counted."""

    day: date
    campaign_id: str
    account_id: str
    spend: Decimal
    impressions: int
    reach: int
    link_clicks: int
    results: int
    actions: dict[str, int]
    action_values: dict[str, Decimal]

    @property
    def cost_per_result(self) -> Decimal | None:
        if not self.results:
            return None
        return (self.spend / self.results).quantize(Decimal("0.01"))


@dataclass(frozen=True, slots=True)
class SyncOutcome:
    """What one sync did, for the log and for the screen."""

    account_id: str
    kind: SyncKind
    status: SyncStatus
    rows_written: int = 0
    message: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.status == SyncStatus.OK


class PeriodPreset(StrEnum):
    """The ranges the panel offers. Anything else is a custom range."""

    THIS_MONTH = "this_month"
    LAST_MONTH = "last_month"
    LAST_7 = "last_7"
    LAST_30 = "last_30"

    @property
    def label(self) -> str:
        return {
            PeriodPreset.THIS_MONTH: "Este mês",
            PeriodPreset.LAST_MONTH: "Mês passado",
            PeriodPreset.LAST_7: "Últimos 7 dias",
            PeriodPreset.LAST_30: "Últimos 30 dias",
        }[self]


@dataclass(frozen=True, slots=True)
class Period:
    """An inclusive range of days the panel is showing."""

    since: date
    until: date
    preset: PeriodPreset | None = None

    @property
    def label(self) -> str:
        if self.preset is not None:
            return self.preset.label
        return f"{self.since.strftime('%d/%m/%Y')} a {self.until.strftime('%d/%m/%Y')}"

    @property
    def days(self) -> int:
        return (self.until - self.since).days + 1

    def previous(self) -> Period:
        """The stretch of the same length right before this one."""
        length = timedelta(days=self.days)
        return Period(since=self.since - length, until=self.since - timedelta(days=1))


def month_bounds(day: date) -> tuple[date, date]:
    first = day.replace(day=1)
    next_first = (first + timedelta(days=32)).replace(day=1)
    return first, next_first - timedelta(days=1)


def resolve_period(preset: PeriodPreset, today: date) -> Period:
    if preset == PeriodPreset.THIS_MONTH:
        first, _ = month_bounds(today)
        return Period(since=first, until=today, preset=preset)
    if preset == PeriodPreset.LAST_MONTH:
        first_of_this, _ = month_bounds(today)
        first, last = month_bounds(first_of_this - timedelta(days=1))
        return Period(since=first, until=last, preset=preset)
    span = 7 if preset == PeriodPreset.LAST_7 else 30
    return Period(since=today - timedelta(days=span - 1), until=today, preset=preset)


@dataclass(frozen=True, slots=True)
class Totals:
    """What a period added up to."""

    spend: Decimal = Decimal("0.00")
    results: int = 0
    impressions: int = 0
    reach: int = 0
    link_clicks: int = 0

    @property
    def cost_per_result(self) -> Decimal | None:
        if not self.results:
            return None
        return (self.spend / self.results).quantize(Decimal("0.01"))

    @property
    def has_data(self) -> bool:
        return bool(self.spend or self.impressions or self.results)


class Better(StrEnum):
    """Which way is the good way for a number on a card."""

    HIGHER = "higher"
    LOWER = "lower"
    NEUTRAL = "neutral"


@dataclass(frozen=True, slots=True)
class Change:
    """One card's number against the stretch of days right before it."""

    current: Decimal | None
    previous: Decimal | None
    better: Better = Better.NEUTRAL

    @property
    def percent(self) -> Decimal | None:
        """``None`` when the period before gives nothing to compare against."""
        if self.current is None or self.previous is None or self.previous == 0:
            return None
        return ((self.current - self.previous) / self.previous * 100).quantize(Decimal("0.1"))

    @property
    def direction(self) -> str:
        percent = self.percent
        if percent is None:
            return "none"
        if percent > 0:
            return "up"
        return "down" if percent < 0 else "flat"

    @property
    def is_good(self) -> bool | None:
        """Whether the move is good news, or ``None`` when it cannot be said.

        Spend moving is neither good nor bad on its own, and a cost per
        result going down is good even though the number got smaller.
        """
        if self.better == Better.NEUTRAL:
            return None
        direction = self.direction
        if direction in ("none", "flat"):
            return None
        return (direction == "up") is (self.better == Better.HIGHER)


def compare_periods(current: Totals, previous: Totals) -> dict[str, Change]:
    """The four cards of the client screen, each against the period before."""
    return {
        "spend": Change(current.spend, previous.spend, Better.NEUTRAL),
        "results": Change(Decimal(current.results), Decimal(previous.results), Better.HIGHER),
        "cost_per_result": Change(current.cost_per_result, previous.cost_per_result, Better.LOWER),
        "link_clicks": Change(
            Decimal(current.link_clicks), Decimal(previous.link_clicks), Better.HIGHER
        ),
    }


@dataclass(frozen=True, slots=True)
class DailyPoint:
    """One day on the chart."""

    day: date
    spend: Decimal
    results: int


@dataclass(frozen=True, slots=True)
class CampaignTotals:
    """One campaign over the period."""

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
        return (self.spend / self.results).quantize(Decimal("0.01"))

    @property
    def is_running(self) -> bool:
        return self.status.upper() == "ACTIVE"


@dataclass(frozen=True, slots=True)
class ClientPanelRow:
    """A client as the panel list shows it."""

    client_id: UUID
    client_name: str
    account_ids: tuple[str, ...]
    totals: Totals
    # The period day by day, for the row's sparkline. Empty is fine: the
    # row still reads, it just has no shape to show.
    series: tuple[DailyPoint, ...] = ()

    @property
    def has_accounts(self) -> bool:
        return bool(self.account_ids)

    @property
    def has_shape(self) -> bool:
        """Two points or more: one day is a dot, not a line."""
        return len(self.series) > 1


def sync_window(today: date, *, days: int = RESYNC_DAYS) -> tuple[date, date]:
    """The inclusive range a resync covers, ending today."""
    return today - timedelta(days=days - 1), today


def parse_action_counts(raw: Any) -> dict[str, int]:
    """Meta sends ``[{"action_type": "lead", "value": "3"}]``."""
    counts: dict[str, int] = {}
    if not isinstance(raw, list):
        return counts
    for item in raw:
        if not isinstance(item, dict):
            continue
        action_type = item.get("action_type")
        if not isinstance(action_type, str):
            continue
        try:
            value = int(float(item.get("value", 0)))
        except (TypeError, ValueError):
            continue
        counts[action_type] = counts.get(action_type, 0) + value
    return counts


def parse_action_values(raw: Any) -> dict[str, Decimal]:
    """Same shape as the counts, but the values are money."""
    values: dict[str, Decimal] = {}
    if not isinstance(raw, list):
        return values
    for item in raw:
        if not isinstance(item, dict):
            continue
        action_type = item.get("action_type")
        if not isinstance(action_type, str):
            continue
        try:
            amount = Decimal(str(item.get("value", "0"))).quantize(Decimal("0.01"))
        except (TypeError, ValueError, ArithmeticError):
            continue
        values[action_type] = values.get(action_type, Decimal("0.00")) + amount
    return values


def to_daily_row(insight: DailyInsight, *, account_id: str, result_action_type: str) -> DailyRow:
    """Turn an insight into the row we store, counting the client's result."""
    return DailyRow(
        day=insight.day,
        campaign_id=insight.campaign_id,
        account_id=account_id,
        spend=insight.spend,
        impressions=insight.impressions,
        reach=insight.reach,
        link_clicks=insight.link_clicks,
        results=insight.results_for(result_action_type),
        actions=insight.actions,
        action_values=insight.action_values,
    )


def build_account_input(
    *, account_id: str, client_id: str, name: str = "", status: str = ""
) -> tuple[str, UUID, str, AccountStatus]:
    """Validate the linking form. Raises :class:`AccountValidationError`."""
    errors: dict[str, str] = {}

    normalized = normalize_account_id(account_id)
    if normalized is None:
        errors["account_id"] = "Informe o ID da conta, como act_123456789."

    parsed_client: UUID | None = None
    try:
        parsed_client = UUID(client_id)
    except (ValueError, AttributeError, TypeError):
        errors["client_id"] = "Escolha o cliente desta conta."

    parsed_status = AccountStatus.ACTIVE
    if status:
        try:
            parsed_status = AccountStatus(status)
        except ValueError:
            errors["status"] = "Situação inválida."

    clean_name = " ".join((name or "").split())[:160]

    if errors or normalized is None or parsed_client is None:
        raise AccountValidationError(errors)

    return normalized, parsed_client, clean_name, parsed_status
