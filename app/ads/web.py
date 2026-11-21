"""Ads screens. Step 5 covers the wiring: which account belongs to whom."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse, Response

from app.ads.domain import (
    AccountStatus,
    AccountValidationError,
    build_account_input,
)
from app.ads.jobs import RESYNC_7D, SYNC_ACCOUNT, SYNC_STRUCTURE
from app.ads.repository import SqlAlchemyAdsRepository
from app.ads.service import AdsService, UnknownClient
from app.clients import ClientsApi
from app.shared.auth import AdminUser, CurrentUser, DbSession
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


@router.get("")
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
    return RedirectResponse("/ads?linked=1", status_code=303)


@router.post("/accounts/{account_id}/sync")
async def sync_now(user: AdminUser, session: DbSession, account_id: str) -> RedirectResponse:
    """Ask for this account's numbers now instead of waiting for the hour."""
    if await _service(session).get_account(account_id) is not None:
        await enqueue(session, SYNC_ACCOUNT, {"account_id": account_id})
    return RedirectResponse("/ads?queued=1", status_code=303)


@router.post("/accounts/{account_id}/unlink")
async def unlink_account(user: AdminUser, session: DbSession, account_id: str) -> RedirectResponse:
    await _service(session).unlink_account(account_id)
    return RedirectResponse("/ads?unlinked=1", status_code=303)
