"""Ads screens. Step 5 covers the wiring: which account belongs to whom."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse, Response

from app.ads.domain import (
    AccountStatus,
    AccountValidationError,
    Period,
    PeriodPreset,
    build_account_input,
    compare_periods,
    resolve_period,
)
from app.ads.jobs import RESYNC_7D, SYNC_ACCOUNT, SYNC_STRUCTURE
from app.ads.repository import SqlAlchemyAdsRepository
from app.ads.service import AdsService, UnknownClient
from app.clients import ClientsApi
from app.shared.auth import AdminUser, CurrentUser, DbSession
from app.shared.clock import today_local
from app.shared.jobs import enqueue
from app.shared.templating import page

router = APIRouter(prefix="/ads", tags=["ads"])

SYNC_LOG_LIMIT = 25
ALL_ACCOUNT_STATUSES = tuple(AccountStatus)


def _service(session: DbSession) -> AdsService:
    return AdsService(SqlAlchemyAdsRepository(session), ClientsApi(session))


async def _screen_context(session: DbSession) -> dict[str, Any]:
    service = _service(session)
    clients = await ClientsApi(session).list_active_clients()
    accounts = await service.list_accounts()
    by_id = {client.id: client for client in clients}
    return {
        "accounts": [(account, by_id.get(account.client_id)) for account in accounts],
        "clients": clients,
        "syncs": await SqlAlchemyAdsRepository(session).recent_sync_rows(limit=SYNC_LOG_LIMIT),
        "statuses": ALL_ACCOUNT_STATUSES,
    }


@router.get("/accounts")
async def list_accounts(request: Request, user: CurrentUser, session: DbSession) -> Response:
    return page(
        request,
        "ads/accounts.html",
        user,
        active="ads",
        values={},
        errors={},
        **await _screen_context(session),
    )


@router.post("/accounts")
async def link_account(
    request: Request,
    user: AdminUser,
    session: DbSession,
    account_id: Annotated[str, Form()] = "",
    client_id: Annotated[str, Form()] = "",
    name: Annotated[str, Form()] = "",
    status: Annotated[str, Form()] = AccountStatus.ACTIVE.value,
) -> Response:
    raw = {
        "account_id": account_id,
        "client_id": client_id,
        "name": name,
        "status": status,
    }

    async def back(errors: dict[str, str]) -> Response:
        return page(
            request,
            "ads/accounts.html",
            user,
            active="ads",
            values=raw,
            errors=errors,
            status_code=422,
            **await _screen_context(session),
        )

    try:
        parsed_id, parsed_client, clean_name, parsed_status = build_account_input(
            account_id=account_id, client_id=client_id, name=name, status=status
        )
    except AccountValidationError as exc:
        return await back(exc.errors)

    try:
        account = await _service(session).link_account(
            parsed_id,
            client_id=parsed_client,
            name=clean_name,
            status=parsed_status,
        )
    except UnknownClient:
        return await back({"client_id": "Esse cliente não existe mais."})

    # First sync right away, so the screen is not empty until the next hour.
    await enqueue(session, SYNC_STRUCTURE, {"account_id": account.id})
    await enqueue(session, RESYNC_7D, {"account_id": account.id})
    return RedirectResponse("/ads/accounts?linked=1", status_code=303)


@router.post("/accounts/{account_id}/sync")
async def sync_now(user: AdminUser, session: DbSession, account_id: str) -> RedirectResponse:
    """Ask for this account's numbers now instead of waiting for the hour."""
    if await _service(session).get_account(account_id) is not None:
        await enqueue(session, SYNC_ACCOUNT, {"account_id": account_id})
    return RedirectResponse("/ads/accounts?queued=1", status_code=303)


@router.post("/accounts/{account_id}/unlink")
async def unlink_account(user: AdminUser, session: DbSession, account_id: str) -> RedirectResponse:
    await _service(session).unlink_account(account_id)
    return RedirectResponse("/ads/accounts?unlinked=1", status_code=303)


# ── painel ────────────────────────────────────────────────────

DEFAULT_PRESET = PeriodPreset.THIS_MONTH
ALL_PRESETS = tuple(PeriodPreset)


def _parse_day(raw: str) -> date | None:
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date()  # noqa: DTZ007
    except (ValueError, TypeError):
        return None


def read_period(preset: str, since: str, until: str) -> Period:
    """A named range, or the custom one the dates spell out."""
    start, end = _parse_day(since), _parse_day(until)
    if start and end and start <= end:
        return Period(since=start, until=end)

    try:
        chosen = PeriodPreset(preset)
    except ValueError:
        chosen = DEFAULT_PRESET
    return resolve_period(chosen, today_local())


@router.get("")
async def panel(
    request: Request,
    user: CurrentUser,
    session: DbSession,
    preset: str = DEFAULT_PRESET.value,
    since: str = "",
    until: str = "",
) -> Response:
    period = read_period(preset, since, until)
    rows = await _service(session).panel_rows(period)
    # The bar on each row is drawn against the worst cost per result on
    # screen, so the comparison is between the clients actually listed.
    costs = [row.totals.cost_per_result for row in rows if row.totals.cost_per_result]
    return page(
        request,
        "ads/panel.html",
        user,
        active="ads",
        period=period,
        presets=ALL_PRESETS,
        rows=rows,
        worst_cost=max(costs) if costs else None,
    )


@router.get("/clients/{client_id}")
async def client_panel(
    request: Request,
    user: CurrentUser,
    session: DbSession,
    client_id: UUID,
    preset: str = DEFAULT_PRESET.value,
    since: str = "",
    until: str = "",
) -> Response:
    client = await ClientsApi(session).get_client(client_id)
    if client is None:
        return page(
            request,
            "errors/not_found.html",
            user,
            active="ads",
            message="Cliente não encontrado.",
            status_code=404,
        )

    period = read_period(preset, since, until)
    service = _service(session)
    series = await service.client_series(client_id, period)
    totals = await service.client_totals(client_id, period)
    # The same stretch of days immediately before, so every card can say
    # whether its number moved and which way.
    earlier = await service.client_totals(client_id, period.previous())

    return page(
        request,
        "ads/client.html",
        user,
        active="ads",
        client=client,
        period=period,
        presets=ALL_PRESETS,
        accounts=await service.list_accounts(client_id=client_id),
        totals=totals,
        changes=compare_periods(totals, earlier),
        campaigns=await service.client_campaigns(client_id, period),
        spark={
            "spend": [float(point.spend) for point in series],
            "results": [point.results for point in series],
        },
        chart={
            "days": [point.day.strftime("%d/%m") for point in series],
            "spend": [float(point.spend) for point in series],
            "results": [point.results for point in series],
        },
    )
