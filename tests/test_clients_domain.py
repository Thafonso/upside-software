"""Pure rules of the clients module."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest

from app.clients.domain import (
    Client,
    ClientStatus,
    ClientValidationError,
    build_client_input,
    next_slug_candidate,
    normalize_phone,
    parse_money,
    slugify,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Padaria São José", "padaria-sao-jose"),
        ("  Café  &  Cia  ", "cafe-cia"),
        ("Dr. Álvaro - Odontologia", "dr-alvaro-odontologia"),
        ("ACADEMIA 24H", "academia-24h"),
        ("...", ""),
        ("日本語", ""),
    ],
)
def test_slugify(raw: str, expected: str) -> None:
    assert slugify(raw) == expected


def test_slugify_respects_max_length() -> None:
    assert len(slugify("a" * 200)) == 60


def test_next_slug_candidate() -> None:
    assert next_slug_candidate("padaria", 1) == "padaria"
    assert next_slug_candidate("padaria", 2) == "padaria-2"
    assert next_slug_candidate("padaria", 13) == "padaria-13"


def test_next_slug_candidate_keeps_length_within_limit() -> None:
    assert len(next_slug_candidate("a" * 60, 7)) == 60


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("(41) 99988-7766", "5541999887766"),
        ("41999887766", "5541999887766"),
        ("5541999887766", "5541999887766"),
        ("4133334444", "554133334444"),
        ("+55 (41) 3333-4444", "554133334444"),
        ("", None),
        (None, None),
        ("abc", None),
    ],
)
def test_normalize_phone(raw: str | None, expected: str | None) -> None:
    assert normalize_phone(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2.500,00", Decimal("2500.00")),
        ("2500", Decimal("2500.00")),
        ("2500.50", Decimal("2500.50")),
        ("R$ 1.234,56", Decimal("1234.56")),
        ("", None),
        (None, None),
    ],
)
def test_parse_money(raw: str | None, expected: Decimal | None) -> None:
    assert parse_money(raw) == expected


def test_parse_money_rejects_negative() -> None:
    with pytest.raises(ValueError, match="negativo"):
        parse_money("-10")


def test_build_client_input_normalizes_everything() -> None:
    data = build_client_input(
        name="  Padaria   São   José  ",
        status="paused",
        result_action_type="purchase",
        contact_name="  Maria  Silva ",
        contact_email="  MARIA@Padaria.com.BR ",
        contact_phone="(41) 99988-7766",
        monthly_fee="2.500,00",
        notes="  cliente novo  ",
    )
    assert data.name == "Padaria São José"
    assert data.status is ClientStatus.PAUSED
    assert data.result_action_type == "purchase"
    assert data.contact_name == "Maria Silva"
    assert data.contact_email == "maria@padaria.com.br"
    assert data.contact_phone == "5541999887766"
    assert data.monthly_fee == Decimal("2500.00")
    assert data.notes == "cliente novo"


def test_build_client_input_defaults() -> None:
    data = build_client_input(name="Clinica Vida")
    assert data.status is ClientStatus.ACTIVE
    assert data.result_action_type == "lead"
    assert data.contact_email is None
    assert data.monthly_fee is None


def test_build_client_input_collects_every_error() -> None:
    with pytest.raises(ClientValidationError) as caught:
        build_client_input(
            name=" ",
            status="nope",
            result_action_type="inventado",
            contact_email="nao-e-email",
            monthly_fee="-5",
        )
    errors = caught.value.errors
    assert set(errors) == {"name", "status", "result_action_type", "contact_email", "monthly_fee"}
    assert errors["name"] == "Informe o nome do cliente."


def test_build_client_input_rejects_name_without_letters() -> None:
    with pytest.raises(ClientValidationError) as caught:
        build_client_input(name="---")
    assert "name" in caught.value.errors


def _client(**overrides: object) -> Client:
    moment = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
    base = {
        "id": uuid4(),
        "name": "Padaria São José",
        "slug": "padaria-sao-jose",
        "status": ClientStatus.ACTIVE,
        "result_action_type": "lead",
        "contact_name": None,
        "contact_email": None,
        "contact_phone": None,
        "monthly_fee": None,
        "notes": None,
        "created_at": moment,
        "updated_at": moment,
    }
    return Client(**{**base, **overrides})  # pyright: ignore[reportArgumentType]


def test_client_labels_are_portuguese() -> None:
    assert _client().status_label == "Ativo"
    assert _client(status=ClientStatus.PAUSED).status_label == "Pausado"
    assert _client(status=ClientStatus.ARCHIVED).status_label == "Arquivado"
    assert _client().result_label == "Cadastro de lead"
    assert _client(result_action_type="desconhecido").result_label == "desconhecido"


def test_client_initials() -> None:
    assert _client().initials == "PS"
    assert _client(name="Vida").initials == "VI"
    assert _client().is_active is True
    assert _client(status=ClientStatus.ARCHIVED).is_active is False
