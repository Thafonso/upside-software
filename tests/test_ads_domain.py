"""Pure ads rules: account ids, action parsing, result counting."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app.ads.domain import (
    AccountStatus,
    AccountValidationError,
    DailyInsight,
    SyncKind,
    SyncStatus,
    build_account_input,
    normalize_account_id,
    parse_action_counts,
    parse_action_values,
    sync_window,
    to_daily_row,
)

CLIENT_ID = "0f1b4a3c-0000-4000-8000-000000000000"


# ── ID da conta ───────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("act_123456789", "act_123456789"),
        ("123456789", "act_123456789"),
        ("  ACT_123456789 ", "act_123456789"),
        ("act_12345", "act_12345"),
        ("", None),
        (None, None),
        ("act_", None),
        ("act_abc", None),
        ("1234", None),  # curto demais para ser uma conta
        ("act_123; DROP TABLE", None),
    ],
)
def test_normalize_account_id(raw: str | None, expected: str | None) -> None:
    assert normalize_account_id(raw) == expected


# ── Formulário de vínculo ─────────────────────────────────────


def test_valid_link_form() -> None:
    account_id, client_id, name, status = build_account_input(
        account_id="123456789", client_id=CLIENT_ID, name="  Padaria  Centro  "
    )
    assert account_id == "act_123456789"
    assert str(client_id) == CLIENT_ID
    assert name == "Padaria Centro"
    assert status == AccountStatus.ACTIVE


def test_link_form_reports_every_problem() -> None:
    with pytest.raises(AccountValidationError) as caught:
        build_account_input(account_id="nope", client_id="nao-e-uuid", status="xpto")
    assert set(caught.value.errors) == {"account_id", "client_id", "status"}


def test_link_form_accepts_a_chosen_status() -> None:
    *_, status = build_account_input(account_id="act_1234567", client_id=CLIENT_ID, status="paused")
    assert status == AccountStatus.PAUSED


# ── Ações da Meta ─────────────────────────────────────────────


def test_parse_action_counts() -> None:
    raw = [
        {"action_type": "lead", "value": "3"},
        {"action_type": "link_click", "value": "47"},
        {"action_type": "lead", "value": "2"},  # Meta repete e a soma vale
    ]
    assert parse_action_counts(raw) == {"lead": 5, "link_click": 47}


def test_parse_action_counts_survives_junk() -> None:
    assert parse_action_counts(None) == {}
    assert parse_action_counts([]) == {}
    assert parse_action_counts("nao e lista") == {}
    assert parse_action_counts([{"value": "3"}]) == {}
    assert parse_action_counts([{"action_type": "lead", "value": "x"}]) == {}
    assert parse_action_counts([{"action_type": "lead", "value": "3.0"}]) == {"lead": 3}


def test_parse_action_values_keeps_money_as_decimal() -> None:
    raw = [
        {"action_type": "purchase", "value": "199.90"},
        {"action_type": "purchase", "value": "100.10"},
    ]
    assert parse_action_values(raw) == {"purchase": Decimal("300.00")}


# ── Resultado do cliente ──────────────────────────────────────


def _insight(**overrides: object) -> DailyInsight:
    base: dict[str, object] = {
        "day": date(2026, 11, 20),
        "campaign_id": "23851",
        "campaign_name": "Leads - Batel",
        "spend": Decimal("150.00"),
        "impressions": 12000,
        "reach": 9000,
        "link_clicks": 310,
        "actions": {"lead": 7, "link_click": 310},
        "action_values": {},
    }
    return DailyInsight(**{**base, **overrides})  # pyright: ignore[reportArgumentType]


def test_only_the_clients_own_action_counts_as_a_result() -> None:
    insight = _insight()
    assert insight.results_for("lead") == 7
    assert insight.results_for("purchase") == 0


def test_to_daily_row_counts_the_result_and_keeps_the_raw_actions() -> None:
    row = to_daily_row(_insight(), account_id="act_1", result_action_type="lead")
    assert row.results == 7
    assert row.spend == Decimal("150.00")
    assert row.account_id == "act_1"
    # The raw numbers stay, so the past can be recounted if the goal changes.
    assert row.actions == {"lead": 7, "link_click": 310}


def test_cost_per_result() -> None:
    row = to_daily_row(_insight(), account_id="act_1", result_action_type="lead")
    assert row.cost_per_result == Decimal("21.43")

    none_yet = to_daily_row(_insight(actions={}), account_id="act_1", result_action_type="lead")
    assert none_yet.cost_per_result is None


# ── Janela de ressincronização ────────────────────────────────


def test_sync_window_covers_seven_days_including_today() -> None:
    since, until = sync_window(date(2026, 11, 21))
    assert since == date(2026, 11, 15)
    assert until == date(2026, 11, 21)
    assert (until - since).days == 6


def test_sync_window_accepts_another_length() -> None:
    since, until = sync_window(date(2026, 11, 21), days=1)
    assert since == until == date(2026, 11, 21)


# ── Rótulos ───────────────────────────────────────────────────


def test_labels_are_portuguese() -> None:
    assert AccountStatus.ACTIVE.label == "Sincronizando"
    assert SyncKind.RESYNC.label == "Últimos 7 dias"
    assert SyncStatus.FAILED.label == "Falhou"
