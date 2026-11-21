"""Client entities and pure rules. No FastAPI, no SQLAlchemy, no httpx."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from uuid import UUID

SLUG_MAX_LENGTH = 60
NAME_MAX_LENGTH = 160
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[a-zA-Z]{2,}$")


class ClientStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    ARCHIVED = "archived"

    @property
    def label(self) -> str:
        return {
            ClientStatus.ACTIVE: "Ativo",
            ClientStatus.PAUSED: "Pausado",
            ClientStatus.ARCHIVED: "Arquivado",
        }[self]


# Meta action types the agency actually counts as a "resultado".
RESULT_ACTION_TYPES: dict[str, str] = {
    "lead": "Cadastro de lead",
    "onsite_conversion.messaging_conversation_started_7d": "Conversa iniciada",
    "onsite_conversion.lead_grouped": "Lead no site (agrupado)",
    "complete_registration": "Registro concluído",
    "purchase": "Compra",
    "schedule": "Agendamento",
    "landing_page_view": "Visualização da página",
    "link_click": "Clique no link",
}
DEFAULT_RESULT_ACTION_TYPE = "lead"


class ClientValidationError(ValueError):
    """Form-level validation failure, with one message per field."""

    def __init__(self, errors: dict[str, str]) -> None:
        self.errors = errors
        super().__init__("; ".join(f"{field}: {msg}" for field, msg in errors.items()))


@dataclass(frozen=True, slots=True)
class ClientInput:
    """Validated data needed to create or update a client."""

    name: str
    status: ClientStatus
    result_action_type: str
    contact_name: str | None = None
    contact_email: str | None = None
    contact_phone: str | None = None
    monthly_fee: Decimal | None = None
    notes: str | None = None


@dataclass(frozen=True, slots=True)
class Client:
    """A client of the agency, as seen by every module."""

    id: UUID
    name: str
    slug: str
    status: ClientStatus
    result_action_type: str
    contact_name: str | None
    contact_email: str | None
    contact_phone: str | None
    monthly_fee: Decimal | None
    notes: str | None
    created_at: datetime
    updated_at: datetime

    @property
    def is_active(self) -> bool:
        return self.status == ClientStatus.ACTIVE

    @property
    def status_label(self) -> str:
        return self.status.label

    @property
    def result_label(self) -> str:
        return RESULT_ACTION_TYPES.get(self.result_action_type, self.result_action_type)

    @property
    def initials(self) -> str:
        parts = [piece for piece in self.name.split() if piece]
        if not parts:
            return "?"
        if len(parts) == 1:
            return parts[0][:2].upper()
        return (parts[0][:1] + parts[1][:1]).upper()


def slugify(value: str) -> str:
    """Turn a display name into an ASCII slug."""
    stripped = unicodedata.normalize("NFKD", value)
    ascii_only = stripped.encode("ascii", "ignore").decode("ascii")
    hyphenated = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_only).strip("-").lower()
    return hyphenated[:SLUG_MAX_LENGTH].strip("-")


def next_slug_candidate(base: str, attempt: int) -> str:
    """Slug for the n-th collision: base, base-2, base-3."""
    if attempt <= 1:
        return base
    suffix = f"-{attempt}"
    trimmed = base[: SLUG_MAX_LENGTH - len(suffix)].strip("-")
    return f"{trimmed}{suffix}"


def normalize_phone(raw: str | None) -> str | None:
    """Keep digits only and make the country code explicit (55)."""
    if not raw:
        return None
    digits = "".join(char for char in raw if char.isdigit())
    if not digits:
        return None
    if digits.startswith("55") and len(digits) in (12, 13):
        return digits
    if len(digits) in (10, 11):
        return f"55{digits}"
    return digits


def _clean(value: str | None, *, limit: int) -> str | None:
    if value is None:
        return None
    collapsed = " ".join(value.split())
    return collapsed[:limit] or None


def parse_money(raw: str | None) -> Decimal | None:
    """Accept 2.500,00 / 2500.00 / 2500 and return a Decimal."""
    if raw is None:
        return None
    text = raw.strip()
    if not text:
        return None
    text = re.sub(r"[^\d,.-]", "", text)
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    try:
        amount = Decimal(text).quantize(Decimal("0.01"))
    except InvalidOperation as exc:
        msg = "valor inválido"
        raise ValueError(msg) from exc
    if amount < 0:
        msg = "valor não pode ser negativo"
        raise ValueError(msg)
    return amount


def build_client_input(
    *,
    name: str,
    status: str = ClientStatus.ACTIVE.value,
    result_action_type: str = DEFAULT_RESULT_ACTION_TYPE,
    contact_name: str | None = None,
    contact_email: str | None = None,
    contact_phone: str | None = None,
    monthly_fee: str | None = None,
    notes: str | None = None,
) -> ClientInput:
    """Validate raw form values. Raises :class:`ClientValidationError`."""
    errors: dict[str, str] = {}

    clean_name = _clean(name, limit=NAME_MAX_LENGTH)
    if not clean_name or len(clean_name) < 2:
        errors["name"] = "Informe o nome do cliente."
    elif not slugify(clean_name):
        errors["name"] = "O nome precisa ter letras ou números."

    try:
        parsed_status = ClientStatus(status)
    except ValueError:
        parsed_status = ClientStatus.ACTIVE
        errors["status"] = "Situação inválida."

    if result_action_type not in RESULT_ACTION_TYPES:
        errors["result_action_type"] = "Escolha um tipo de resultado da lista."

    clean_email = _clean(contact_email, limit=255)
    if clean_email is not None:
        clean_email = clean_email.lower()
        if not EMAIL_PATTERN.match(clean_email):
            errors["contact_email"] = "E-mail inválido."

    fee: Decimal | None = None
    try:
        fee = parse_money(monthly_fee)
    except ValueError as exc:
        errors["monthly_fee"] = f"{str(exc).capitalize()}."

    if errors:
        raise ClientValidationError(errors)

    return ClientInput(
        name=clean_name or "",
        status=parsed_status,
        result_action_type=result_action_type,
        contact_name=_clean(contact_name, limit=120),
        contact_email=clean_email,
        contact_phone=normalize_phone(contact_phone),
        monthly_fee=fee,
        notes=_clean(notes, limit=2000),
    )
