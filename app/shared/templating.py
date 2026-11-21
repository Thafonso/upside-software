"""Jinja2 setup and the pt-BR formatting filters used by every screen."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.templating import Jinja2Templates

from app.shared.clock import to_local
from app.shared.models import AppUser

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"

Number = int | float | Decimal


def _group_thousands(digits: str) -> str:
    chunks: list[str] = []
    while len(digits) > 3:
        chunks.insert(0, digits[-3:])
        digits = digits[:-3]
    chunks.insert(0, digits)
    return ".".join(chunks)


def format_number(value: Number | None, decimals: int = 0) -> str:
    """1234.5 -> '1.234,5' (pt-BR separators)."""
    if value is None:
        return "—"
    quantized = f"{Decimal(str(value)):.{decimals}f}"
    sign = "-" if quantized.startswith("-") else ""
    quantized = quantized.lstrip("-")
    whole, _, fraction = quantized.partition(".")
    grouped = _group_thousands(whole)
    return f"{sign}{grouped},{fraction}" if fraction else f"{sign}{grouped}"


def format_brl(value: Number | None) -> str:
    if value is None:
        return "—"
    return f"R$ {format_number(value, 2)}"


def format_date_br(value: date | datetime | None) -> str:
    if value is None:
        return "—"
    moment = to_local(value) if isinstance(value, datetime) else value
    return moment.strftime("%d/%m/%Y")


def format_datetime_br(value: datetime | None) -> str:
    if value is None:
        return "—"
    return to_local(value).strftime("%d/%m/%Y %H:%M")


def format_percent_br(value: Number | None, decimals: int = 1) -> str:
    if value is None:
        return "—"
    return f"{format_number(value, decimals)}%"


def format_phone_br(digits: str | None) -> str:
    """'5541999887766' -> '(41) 99988-7766'."""
    if not digits:
        return "—"
    only = "".join(char for char in digits if char.isdigit())
    only = only.removeprefix("55") if len(only) in (12, 13) else only
    if len(only) == 11:
        return f"({only[:2]}) {only[2:7]}-{only[7:]}"
    if len(only) == 10:
        return f"({only[:2]}) {only[2:6]}-{only[6:]}"
    return digits


templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
templates.env.filters["brl"] = format_brl
templates.env.filters["num"] = format_number
templates.env.filters["date_br"] = format_date_br
templates.env.filters["datetime_br"] = format_datetime_br
templates.env.filters["pct_br"] = format_percent_br
templates.env.filters["phone_br"] = format_phone_br
templates.env.globals["app_name"] = "LeadHunter"
templates.env.globals["brand_name"] = "Upside"


def is_htmx(request: Request) -> bool:
    return request.headers.get("hx-request") == "true"


def page(
    request: Request,
    template: str,
    user: AppUser | None = None,
    *,
    active: str = "",
    status_code: int = 200,
    **context: Any,
) -> Any:
    """Render a full screen with the context the base layout expects."""
    return templates.TemplateResponse(
        request,
        template,
        {"user": user, "active_nav": active, **context},
        status_code=status_code,
    )


def fragment(request: Request, template: str, *, status_code: int = 200, **context: Any) -> Any:
    """Render an HTMX partial (no base layout)."""
    return templates.TemplateResponse(request, template, context, status_code=status_code)
