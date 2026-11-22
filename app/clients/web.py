"""Client screens. Viewing is open to the team, editing is admin-only."""

from __future__ import annotations

import contextlib
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse, Response

from app.clients.domain import (
    DEFAULT_RESULT_ACTION_TYPE,
    RESULT_ACTION_TYPES,
    ClientInput,
    ClientStatus,
    ClientValidationError,
    build_client_input,
)
from app.clients.repository import SqlAlchemyClientRepository
from app.clients.service import ClientNotFound, ClientsService
from app.shared.auth import AdminUser, CurrentUser, DbSession
from app.shared.templating import fragment, page

router = APIRouter(prefix="/clients", tags=["clients"])

ALL_STATUSES = tuple(ClientStatus)


def _service(session: DbSession) -> ClientsService:
    return ClientsService(SqlAlchemyClientRepository(session))


def _parse_status_filter(raw: str) -> tuple[ClientStatus, ...] | None:
    """Empty filter hides archived clients; an explicit value is honoured."""
    if not raw:
        return (ClientStatus.ACTIVE, ClientStatus.PAUSED)
    if raw == "all":
        return None
    try:
        return (ClientStatus(raw),)
    except ValueError:
        return (ClientStatus.ACTIVE, ClientStatus.PAUSED)


@router.get("/options.json")
async def client_options(user: CurrentUser, session: DbSession) -> dict[str, list[dict[str, str]]]:
    """Every client the command palette can jump to.

    Deliberately tiny and unpaged: the palette fetches it once when it first
    opens and filters in the browser, because a round trip per keystroke is
    slower than the navigation the palette exists to replace. It is declared
    before ``/{client_id}`` so the path is not read as an id.
    """
    clients = await _service(session).list_clients(
        statuses=(ClientStatus.ACTIVE, ClientStatus.PAUSED)
    )
    return {
        "clients": [
            {"id": str(client.id), "name": client.name, "slug": client.slug} for client in clients
        ]
    }


@router.get("")
async def list_clients(
    request: Request,
    user: CurrentUser,
    session: DbSession,
    q: str = "",
    status: str = "",
) -> Response:
    clients = await _service(session).list_clients(
        search=q or None, statuses=_parse_status_filter(status)
    )
    context = {
        "clients": clients,
        "q": q,
        "status": status,
        "statuses": ALL_STATUSES,
        "can_edit": user.is_admin,
    }
    if request.headers.get("hx-request") == "true":
        return fragment(request, "clients/_rows.html", **context)
    return page(request, "clients/list.html", user, active="clients", **context)


@router.get("/new")
async def new_client_form(request: Request, user: AdminUser) -> Response:
    return page(
        request,
        "clients/form.html",
        user,
        active="clients",
        mode="new",
        client=None,
        values={
            "status": ClientStatus.ACTIVE.value,
            "result_action_type": DEFAULT_RESULT_ACTION_TYPE,
        },
        errors={},
        statuses=ALL_STATUSES,
        result_types=RESULT_ACTION_TYPES,
    )


@router.post("")
async def create_client(
    request: Request,
    user: AdminUser,
    session: DbSession,
    name: Annotated[str, Form()],
    status: Annotated[str, Form()] = ClientStatus.ACTIVE.value,
    result_action_type: Annotated[str, Form()] = DEFAULT_RESULT_ACTION_TYPE,
    contact_name: Annotated[str, Form()] = "",
    contact_email: Annotated[str, Form()] = "",
    contact_phone: Annotated[str, Form()] = "",
    monthly_fee: Annotated[str, Form()] = "",
    notes: Annotated[str, Form()] = "",
) -> Response:
    raw = {
        "name": name,
        "status": status,
        "result_action_type": result_action_type,
        "contact_name": contact_name,
        "contact_email": contact_email,
        "contact_phone": contact_phone,
        "monthly_fee": monthly_fee,
        "notes": notes,
    }
    try:
        data = _build(raw)
    except ClientValidationError as exc:
        return page(
            request,
            "clients/form.html",
            user,
            active="clients",
            mode="new",
            client=None,
            values=raw,
            errors=exc.errors,
            statuses=ALL_STATUSES,
            result_types=RESULT_ACTION_TYPES,
            status_code=422,
        )

    client = await _service(session).create(data)
    return RedirectResponse(f"/clients/{client.id}/edit?created=1", status_code=303)


@router.get("/{client_id}/edit")
async def edit_client_form(
    request: Request,
    user: AdminUser,
    session: DbSession,
    client_id: UUID,
    created: int = 0,
) -> Response:
    client = await _service(session).get(client_id)
    if client is None:
        return page(
            request,
            "errors/not_found.html",
            user,
            active="clients",
            message="Cliente não encontrado.",
            status_code=404,
        )
    return page(
        request,
        "clients/form.html",
        user,
        active="clients",
        mode="edit",
        client=client,
        values={
            "name": client.name,
            "status": client.status.value,
            "result_action_type": client.result_action_type,
            "contact_name": client.contact_name or "",
            "contact_email": client.contact_email or "",
            "contact_phone": client.contact_phone or "",
            "monthly_fee": "" if client.monthly_fee is None else f"{client.monthly_fee:.2f}",
            "notes": client.notes or "",
        },
        errors={},
        statuses=ALL_STATUSES,
        result_types=RESULT_ACTION_TYPES,
        created=bool(created),
    )


@router.post("/{client_id}")
async def update_client(
    request: Request,
    user: AdminUser,
    session: DbSession,
    client_id: UUID,
    name: Annotated[str, Form()],
    status: Annotated[str, Form()] = ClientStatus.ACTIVE.value,
    result_action_type: Annotated[str, Form()] = DEFAULT_RESULT_ACTION_TYPE,
    contact_name: Annotated[str, Form()] = "",
    contact_email: Annotated[str, Form()] = "",
    contact_phone: Annotated[str, Form()] = "",
    monthly_fee: Annotated[str, Form()] = "",
    notes: Annotated[str, Form()] = "",
) -> Response:
    raw = {
        "name": name,
        "status": status,
        "result_action_type": result_action_type,
        "contact_name": contact_name,
        "contact_email": contact_email,
        "contact_phone": contact_phone,
        "monthly_fee": monthly_fee,
        "notes": notes,
    }
    service = _service(session)
    client = await service.get(client_id)
    if client is None:
        return page(
            request,
            "errors/not_found.html",
            user,
            active="clients",
            message="Cliente não encontrado.",
            status_code=404,
        )
    try:
        data = _build(raw)
    except ClientValidationError as exc:
        return page(
            request,
            "clients/form.html",
            user,
            active="clients",
            mode="edit",
            client=client,
            values=raw,
            errors=exc.errors,
            statuses=ALL_STATUSES,
            result_types=RESULT_ACTION_TYPES,
            status_code=422,
        )

    await service.update(client_id, data)
    return RedirectResponse(f"/clients/{client_id}/edit?created=0", status_code=303)


@router.post("/{client_id}/status")
async def change_status(
    request: Request,
    user: AdminUser,
    session: DbSession,
    client_id: UUID,
    status: Annotated[str, Form()],
    q: Annotated[str, Form()] = "",
    filter_status: Annotated[str, Form()] = "",
) -> Response:
    service = _service(session)
    # A row the list no longer has, or a value outside the enum: re-render as is.
    with contextlib.suppress(ClientNotFound, ValueError):
        await service.set_status(client_id, ClientStatus(status))

    clients = await service.list_clients(
        search=q or None, statuses=_parse_status_filter(filter_status)
    )
    return fragment(
        request,
        "clients/_rows.html",
        clients=clients,
        q=q,
        status=filter_status,
        statuses=ALL_STATUSES,
        can_edit=True,
    )


def _build(raw: dict[str, str]) -> ClientInput:
    return build_client_input(
        name=raw["name"],
        status=raw["status"],
        result_action_type=raw["result_action_type"],
        contact_name=raw["contact_name"] or None,
        contact_email=raw["contact_email"] or None,
        contact_phone=raw["contact_phone"] or None,
        monthly_fee=raw["monthly_fee"] or None,
        notes=raw["notes"] or None,
    )
