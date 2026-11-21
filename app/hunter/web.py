"""Hunter screens: start a search and watch it run."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse, Response

from app.hunter.domain import (
    DEFAULT_QUANTITY,
    QUANTITY_CHOICES,
    DailyLimitReached,
    SearchValidationError,
    build_search_input,
)
from app.hunter.jobs import START_SCRAPE
from app.hunter.repository import SqlAlchemyHunterRepository
from app.hunter.service import HunterService
from app.shared.auth import CurrentUser, DbSession
from app.shared.config import get_settings
from app.shared.jobs import enqueue
from app.shared.templating import fragment, page

router = APIRouter(prefix="/hunter", tags=["hunter"])

SEARCH_LIST_LIMIT = 50
PROSPECT_PREVIEW_LIMIT = 500


def _service(session: DbSession) -> HunterService:
    return HunterService(
        SqlAlchemyHunterRepository(session),
        daily_limit=get_settings().hunter_daily_limit,
    )


@router.get("")
async def list_searches(request: Request, user: CurrentUser, session: DbSession) -> Response:
    service = _service(session)
    return page(
        request,
        "hunter/list.html",
        user,
        active="hunter",
        searches=await service.list_searches(limit=SEARCH_LIST_LIMIT),
        used_today=await service.searches_used_today(),
        daily_limit=service.daily_limit,
    )


@router.get("/searches/new")
async def new_search_form(request: Request, user: CurrentUser, session: DbSession) -> Response:
    service = _service(session)
    return page(
        request,
        "hunter/form.html",
        user,
        active="hunter",
        values={"quantity": str(DEFAULT_QUANTITY)},
        errors={},
        quantities=QUANTITY_CHOICES,
        used_today=await service.searches_used_today(),
        daily_limit=service.daily_limit,
    )


@router.post("/searches")
async def create_search(
    request: Request,
    user: CurrentUser,
    session: DbSession,
    # Defaults on purpose: an empty field must reach our own validation and
    # come back as a message in the form, not as FastAPI's raw 422 JSON.
    niches: Annotated[str, Form()] = "",
    city: Annotated[str, Form()] = "",
    neighborhood: Annotated[str, Form()] = "",
    quantity: Annotated[str, Form()] = str(DEFAULT_QUANTITY),
    fetch_emails: Annotated[str, Form()] = "",
) -> Response:
    service = _service(session)
    raw = {
        "niches": niches,
        "city": city,
        "neighborhood": neighborhood,
        "quantity": quantity,
        "fetch_emails": fetch_emails,
    }

    async def back(errors: dict[str, str], alert: str = "") -> Response:
        return page(
            request,
            "hunter/form.html",
            user,
            active="hunter",
            values=raw,
            errors=errors,
            alert=alert,
            quantities=QUANTITY_CHOICES,
            used_today=await service.searches_used_today(),
            daily_limit=service.daily_limit,
            status_code=422,
        )

    try:
        data = build_search_input(
            niches=niches,
            city=city,
            neighborhood=neighborhood or None,
            quantity=quantity,
            fetch_emails=bool(fetch_emails),
        )
    except SearchValidationError as exc:
        return await back(exc.errors)

    try:
        search = await service.create_search(data)
    except DailyLimitReached as limit:
        return await back(
            {},
            alert=(
                f"O limite de {limit.limit} buscas por dia já foi usado. "
                "A contagem recomeça amanhã."
            ),
        )

    await enqueue(session, START_SCRAPE, {"search_id": str(search.id)})
    return RedirectResponse(f"/hunter/searches/{search.id}", status_code=303)


@router.get("/searches/{search_id}")
async def search_detail(
    request: Request, user: CurrentUser, session: DbSession, search_id: UUID
) -> Response:
    service = _service(session)
    search = await service.get_search(search_id)
    if search is None:
        return page(
            request,
            "errors/not_found.html",
            user,
            active="hunter",
            message="Busca não encontrada.",
            status_code=404,
        )
    return page(
        request,
        "hunter/detail.html",
        user,
        active="hunter",
        search=search,
        prospects=await service.list_prospects(search_id, limit=PROSPECT_PREVIEW_LIMIT),
    )


@router.get("/searches/{search_id}/progress")
async def search_progress(
    request: Request, user: CurrentUser, session: DbSession, search_id: UUID
) -> Response:
    """Polled by the detail screen while the search is open."""
    service = _service(session)
    search = await service.get_search(search_id)
    if search is None:
        return fragment(request, "hunter/_progress_missing.html", status_code=404)
    return fragment(
        request,
        "hunter/_progress.html",
        search=search,
        prospects=await service.list_prospects(search_id, limit=PROSPECT_PREVIEW_LIMIT),
    )
