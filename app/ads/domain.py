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
