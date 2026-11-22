"""Shared building blocks: auth, crypto, events, clock and the pt-BR filters."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi import Response
from itsdangerous import URLSafeTimedSerializer
from starlette.requests import Request

from app.shared import auth, events
from app.shared.clock import local_day_bounds, to_local, today_local
from app.shared.config import get_settings
from app.shared.crypto import DecryptionError, decrypt_secret, encrypt_secret
from app.shared.models import AppUser, UserRole
from app.shared.templating import (
    format_brl,
    format_date_br,
    format_datetime_br,
    format_number,
    format_percent_br,
    format_phone_br,
)
from app.shared.web import safe_next

# ── Senhas ────────────────────────────────────────────────────


def test_password_hash_is_salted_and_verifiable() -> None:
    first = auth.hash_password("senha-correta")
    second = auth.hash_password("senha-correta")
    assert first != second
    assert auth.verify_password(first, "senha-correta")
    assert not auth.verify_password(first, "senha-errada")


def test_verify_password_rejects_garbage_hash() -> None:
    assert not auth.verify_password("nao-e-um-hash", "qualquer")
    assert auth.needs_rehash("nao-e-um-hash")


# ── Cookie de sessão ──────────────────────────────────────────


def _request_with_cookie(raw: str | None) -> Request:
    headers = []
    if raw is not None:
        cookie = f"{get_settings().session_cookie_name}={raw}"
        headers.append((b"cookie", cookie.encode("latin-1")))
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": headers,
        "query_string": b"",
    }
    return Request(scope)


def test_session_round_trip() -> None:
    user_id = uuid4()
    response = Response()
    auth.issue_session(response, user_id)
    cookie_header = response.headers["set-cookie"]
    raw = cookie_header.split("=", 1)[1].split(";", 1)[0]

    assert auth.read_session(_request_with_cookie(raw)) == user_id


def test_session_cookie_is_httponly_and_lax() -> None:
    response = Response()
    auth.issue_session(response, uuid4())
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie
    assert "samesite=lax" in cookie


def test_read_session_without_cookie() -> None:
    assert auth.read_session(_request_with_cookie(None)) is None


def test_read_session_rejects_tampered_value() -> None:
    assert auth.read_session(_request_with_cookie("abc.def.ghi")) is None


def test_read_session_rejects_other_secret() -> None:
    forged = URLSafeTimedSerializer("outro-segredo", salt=auth.SESSION_SALT).dumps(str(uuid4()))
    assert auth.read_session(_request_with_cookie(forged)) is None


def test_read_session_rejects_non_uuid_payload() -> None:
    signed = URLSafeTimedSerializer(get_settings().session_secret, salt=auth.SESSION_SALT).dumps(
        "nao-e-uuid"
    )
    assert auth.read_session(_request_with_cookie(signed)) is None


def test_clear_session_expires_the_cookie() -> None:
    response = Response()
    auth.clear_session(response)
    assert get_settings().session_cookie_name in response.headers["set-cookie"]


# ── Redirecionamento pós-login ────────────────────────────────


@pytest.mark.parametrize(
    ("candidate", "expected"),
    [
        ("/clients", "/clients"),
        ("/clients/new", "/clients/new"),
        ("", "/clients"),
        (None, "/clients"),
        ("https://exemplo.com", "/clients"),
        ("//exemplo.com", "/clients"),
        ("javascript:alert(1)", "/clients"),
    ],
)
def test_safe_next_only_allows_same_site_paths(candidate: str | None, expected: str) -> None:
    assert safe_next(candidate) == expected


# ── Usuário ───────────────────────────────────────────────────


def test_app_user_initials_and_role_label() -> None:
    user = AppUser(email="ana@upsidemkt.com.br", name="Ana Paula Souza", role=UserRole.ADMIN)
    assert user.initials == "AS"
    assert user.is_admin
    assert user.role.label == "Administrador"
    assert UserRole.TEAM.label == "Equipe"

    assert AppUser(email="x@y.com", name="Ana", role=UserRole.TEAM).initials == "AN"
    assert AppUser(email="zelda@y.com", name="", role=UserRole.TEAM).initials == "Z"


# ── Criptografia ──────────────────────────────────────────────


def test_encrypt_decrypt_round_trip() -> None:
    secret = "EAAB-token-da-meta"
    encrypted = encrypt_secret(secret)
    assert encrypted != secret
    assert decrypt_secret(encrypted) == secret


def test_decrypt_rejects_foreign_ciphertext() -> None:
    with pytest.raises(DecryptionError):
        decrypt_secret("gAAAAABnao-e-valido")


# ── Barramento de eventos ─────────────────────────────────────


def test_publish_reaches_only_matching_subscribers() -> None:
    events.reset()
    seen: list[str] = []
    events.subscribe(events.ProspectConverted, lambda event: seen.append(str(event.client_id)))
    events.subscribe(events.ReportGenerated, lambda event: seen.append("relatorio"))

    client_id = uuid4()
    events.publish(events.ProspectConverted(prospect_id=uuid4(), client_id=client_id))
    assert seen == [str(client_id)]
    events.reset()


def test_failing_handler_does_not_block_the_others() -> None:
    events.reset()
    seen: list[str] = []

    def explode(_: events.Event) -> None:
        raise RuntimeError("falhou")

    events.subscribe(events.ReportGenerated, explode)
    events.subscribe(events.ReportGenerated, lambda _: seen.append("segundo"))

    events.publish(events.ReportGenerated(report_id=uuid4()))
    assert seen == ["segundo"]
    events.reset()


# ── Relógio ───────────────────────────────────────────────────


def test_local_day_bounds_is_a_half_open_utc_range() -> None:
    start, end = local_day_bounds(date(2026, 11, 20))
    # Sao Paulo is UTC-3 all year since 2019.
    assert start == datetime(2026, 11, 20, 3, 0, tzinfo=UTC)
    assert end == datetime(2026, 11, 21, 3, 0, tzinfo=UTC)
    assert (end - start).total_seconds() == 24 * 3600


def test_to_local_assumes_utc_for_naive_input() -> None:
    assert to_local(datetime(2026, 11, 20, 12, 0)).hour == 9
    assert to_local(datetime(2026, 11, 20, 12, 0, tzinfo=UTC)).hour == 9


def test_today_local_is_a_date() -> None:
    assert isinstance(today_local(), date)


# ── Formatação pt-BR ──────────────────────────────────────────


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (Decimal("1234.5"), "R$ 1.234,50"),
        (Decimal("0"), "R$ 0,00"),
        (1234567.891, "R$ 1.234.567,89"),
        (Decimal("-50"), "R$ -50,00"),
        (None, "—"),
    ],
)
def test_format_brl(value: object, expected: str) -> None:
    assert format_brl(value) == expected  # pyright: ignore[reportArgumentType]


def test_format_number_groups_thousands() -> None:
    assert format_number(1234567) == "1.234.567"
    assert format_number(999) == "999"
    assert format_number(1234.567, 2) == "1.234,57"
    assert format_number(None) == "—"


def test_format_percent_and_phone() -> None:
    assert format_percent_br(12.345) == "12,3%"
    assert format_phone_br("5541999887766") == "(41) 99988-7766"
    assert format_phone_br("554133334444") == "(41) 3333-4444"
    assert format_phone_br(None) == "—"
    assert format_phone_br("123") == "123"


def test_format_dates_use_brazilian_order() -> None:
    assert format_date_br(date(2026, 11, 20)) == "20/11/2026"
    assert format_date_br(datetime(2026, 11, 20, 2, 0, tzinfo=UTC)) == "19/11/2026"
    assert format_datetime_br(datetime(2026, 11, 20, 12, 30, tzinfo=UTC)) == "20/11/2026 09:30"
    assert format_date_br(None) == "—"
    assert format_datetime_br(None) == "—"


# ── Destaque do termo buscado ─────────────────────────────────


def test_highlight_marks_every_occurrence() -> None:
    from app.shared.templating import highlight

    assert str(highlight("Padaria Padaria Central", "padaria")) == (
        "<mark>Padaria</mark> <mark>Padaria</mark> Central"
    )


def test_highlight_ignores_accents_the_way_the_search_does() -> None:
    from app.shared.templating import highlight

    # Typing "jose" finds the row, so it has to point at something in it.
    assert str(highlight("Padaria São José", "jose")) == "Padaria São <mark>José</mark>"
    assert str(highlight("Café du Centre", "CAFE")) == "<mark>Café</mark> du Centre"


def test_highlight_escapes_what_it_did_not_write() -> None:
    from app.shared.templating import highlight

    marked = str(highlight('<script>alert("x")</script> Padaria', "padaria"))
    assert "<script>" not in marked
    assert "&lt;script&gt;" in marked
    assert "<mark>Padaria</mark>" in marked


def test_highlight_leaves_the_text_alone_when_there_is_nothing_to_mark() -> None:
    from app.shared.templating import highlight

    assert str(highlight("Padaria Central", "")) == "Padaria Central"
    assert str(highlight("Padaria Central", None)) == "Padaria Central"
    # One letter would light up half the list, so it is not a term.
    assert str(highlight("Padaria Central", "a")) == "Padaria Central"
    assert str(highlight("Padaria Central", "clinica")) == "Padaria Central"
    assert str(highlight(None, "padaria")) == ""
