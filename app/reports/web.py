"""Report screens: the client list, a client's history, one report and its PDF."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

import anyio.to_thread
from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse, Response

from app.clients import ClientsApi
from app.reports.api import build_service
from app.reports.domain import (
    MonthPeriod,
    Report,
    ReportValidationError,
    build_report_request,
    recent_months,
)
from app.reports.render import snapshot_to_pdf
from app.reports.service import ReportsService, UnknownClient
from app.shared.auth import CurrentUser, DbSession
from app.shared.clock import today_local
from app.shared.templating import page

router = APIRouter(prefix="/reports", tags=["reports"])

MONTHS_OFFERED = 24
HISTORY_LIMIT = 60


def _service(session: DbSession) -> ReportsService:
    return build_service(session)


def split_period(raw: str) -> tuple[str, str]:
    """The month select sends ``2026-11``; the domain wants year and month."""
    year, _, month = (raw or "").partition("-")
    return year.strip(), month.strip()


def default_period() -> MonthPeriod:
    """The month that just ended - the one a monthly report is usually about."""
    return MonthPeriod.of(today_local()).previous()


def _not_found(request: Request, user: Any, message: str) -> Response:
    return page(
        request,
        "errors/not_found.html",
        user,
        active="reports",
        message=message,
        status_code=404,
    )


# ── lista de clientes ─────────────────────────────────────────


@router.get("")
async def list_clients(request: Request, user: CurrentUser, session: DbSession) -> Response:
    return page(
        request,
        "reports/list.html",
        user,
        active="reports",
        rows=await _service(session).client_rows(),
    )


# ── histórico de um cliente ───────────────────────────────────


async def _client_screen(
    request: Request,
    user: Any,
    session: DbSession,
    client_id: UUID,
    *,
    values: dict[str, str],
    errors: dict[str, str] | None = None,
    alert: str = "",
    status_code: int = 200,
) -> Response:
    client = await ClientsApi(session).get_client(client_id)
    if client is None:
        return _not_found(request, user, "Cliente não encontrado.")

    service = _service(session)
    reports: list[Report] = await service.list_for_client(client_id, limit=HISTORY_LIMIT)
    chosen = values.get("period") or ""
    year, month = split_period(chosen)
    already = 0
    if year.isdigit() and month.isdigit() and 1 <= int(month) <= 12:
        already = await service.reports_in_month(client_id, MonthPeriod(int(year), int(month)))

    return page(
        request,
        "reports/client.html",
        user,
        active="reports",
        client=client,
        reports=reports,
        months=recent_months(today_local(), MONTHS_OFFERED),
        values=values,
        errors=errors or {},
        alert=alert,
        already_in_month=already,
        status_code=status_code,
    )


@router.get("/clients/{client_id}")
async def client_reports(
    request: Request,
    user: CurrentUser,
    session: DbSession,
    client_id: UUID,
    period: str = "",
) -> Response:
    chosen = period or default_period().short_key
    return await _client_screen(
        request,
        user,
        session,
        client_id,
        values={"period": chosen, "comment": ""},
    )


@router.post("/clients/{client_id}")
async def generate_report(
    request: Request,
    user: CurrentUser,
    session: DbSession,
    client_id: UUID,
    period: Annotated[str, Form()] = "",
    comment: Annotated[str, Form()] = "",
) -> Response:
    raw = {"period": period, "comment": comment}
    year, month = split_period(period)

    try:
        wanted = build_report_request(
            client_id=str(client_id),
            year=year,
            month=month,
            comment=comment,
            today=today_local(),
        )
    except ReportValidationError as exc:
        errors = exc.errors
        if "year" in errors or "month" in errors:
            errors = {"period": errors.get("month") or errors["year"]}
        return await _client_screen(
            request, user, session, client_id, values=raw, errors=errors, status_code=422
        )

    try:
        report = await _service(session).generate_monthly_report(
            client_id=wanted.client_id,
            period=wanted.period,
            comment=wanted.comment,
            generated_by=user.name,
        )
    except UnknownClient:
        return _not_found(request, user, "Cliente não encontrado.")

    return RedirectResponse(f"/reports/{report.id}?new=1", status_code=303)


# ── um relatório ──────────────────────────────────────────────


@router.get("/{report_id}")
async def show_report(
    request: Request,
    user: CurrentUser,
    session: DbSession,
    report_id: UUID,
    new: str = "",
) -> Response:
    report = await _service(session).get_report(report_id)
    if report is None:
        return _not_found(request, user, "Relatório não encontrado.")

    snapshot = report.snapshot
    return page(
        request,
        "reports/detail.html",
        user,
        active="reports",
        report=report,
        snapshot=snapshot,
        just_generated=bool(new),
        chart={
            "days": [point.day.strftime("%d/%m") for point in snapshot.daily],
            "spend": [float(point.spend) for point in snapshot.daily],
            "results": [point.results for point in snapshot.daily],
        },
    )


@router.get("/{report_id}/pdf")
async def download_pdf(
    user: CurrentUser,
    session: DbSession,
    report_id: UUID,
) -> Response:
    report = await _service(session).get_report(report_id)
    if report is None:
        return Response("Relatório não encontrado.", status_code=404, media_type="text/plain")

    # Rendering is CPU work; off the event loop so one PDF does not hold up
    # every other request.
    pdf = await anyio.to_thread.run_sync(snapshot_to_pdf, report.snapshot)
    return Response(
        pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{report.pdf_filename}"'},
    )
