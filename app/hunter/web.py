"""Hunter screens: the prospect list, the searches, and starting one."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Form, Request
from fastapi.responses import PlainTextResponse, RedirectResponse, Response

from app.clients import ClientsApi
from app.hunter.domain import (
    DEFAULT_QUANTITY,
    QUANTITY_CHOICES,
    DailyLimitReached,
    ProspectFilters,
    ProspectStatus,
    SearchValidationError,
    build_prospect_filters,
    build_search_input,
)
from app.hunter.jobs import START_SCRAPE
from app.hunter.repository import SqlAlchemyHunterRepository
from app.hunter.scoring import SCORING_RULES, ScoreRule
from app.hunter.service import HunterService
from app.shared.auth import CurrentUser, DbSession
from app.shared.clock import today_local
from app.shared.config import get_settings
from app.shared.jobs import enqueue
from app.shared.templating import fragment, page

router = APIRouter(prefix="/hunter", tags=["hunter"])

SEARCH_LIST_LIMIT = 50
PROSPECT_LIST_LIMIT = 500
EXPORT_LIMIT = 5000

ALL_PROSPECT_STATUSES = tuple(ProspectStatus)
SCORE_CHOICES: tuple[int, ...] = (0, 40, 60, 80)


def _service(session: DbSession) -> HunterService:
    return HunterService(
        SqlAlchemyHunterRepository(session),
        daily_limit=get_settings().hunter_daily_limit,
    )


def _filters(
    search_id: str = "",
    status: str = "",
    no_website: str = "",
    social_only: str = "",
    has_whatsapp: str = "",
    has_email: str = "",
    min_score: str = "",
) -> ProspectFilters:
    return build_prospect_filters(
        search_id=search_id,
        status=status,
        no_website=no_website,
        social_only=social_only,
        has_whatsapp=has_whatsapp,
        has_email=has_email,
        min_score=min_score,
    )


async def _list_context(session: DbSession, filters: ProspectFilters) -> dict[str, Any]:
    service = _service(session)
    return {
        "prospects": await service.search_prospects(filters, limit=PROSPECT_LIST_LIMIT),
        "total": await service.count_prospects(filters),
        "filters": filters,
        "query": filters.as_query(),
        "statuses": ALL_PROSPECT_STATUSES,
        "score_choices": SCORE_CHOICES,
        "limit": PROSPECT_LIST_LIMIT,
    }


# ── lista de empresas: a tela de trabalho do dia ──────────────


@router.get("")
async def list_prospects(
    request: Request,
    user: CurrentUser,
    session: DbSession,
    search_id: str = "",
    status: str = "",
    no_website: str = "",
    social_only: str = "",
    has_whatsapp: str = "",
    has_email: str = "",
    min_score: str = "",
) -> Response:
    filters = _filters(
        search_id, status, no_website, social_only, has_whatsapp, has_email, min_score
    )
    context = await _list_context(session, filters)
    if request.headers.get("hx-request") == "true":
        return fragment(request, "hunter/_prospects.html", **context)
    return page(
        request,
        "hunter/prospects.html",
        user,
        active="hunter",
        searches=await _service(session).list_searches(limit=SEARCH_LIST_LIMIT),
        scoring_rules={rule.label: SCORING_RULES[rule] for rule in ScoreRule},
        **context,
    )


async def _refreshed_list(request: Request, session: DbSession, form: dict[str, str]) -> Response:
    """Every row action answers with the list as it now stands."""
    filters = _filters(
        form.get("search_id", ""),
        form.get("filter_status", ""),
        form.get("no_website", ""),
        form.get("social_only", ""),
        form.get("has_whatsapp", ""),
        form.get("has_email", ""),
        form.get("min_score", ""),
    )
    return fragment(request, "hunter/_prospects.html", **await _list_context(session, filters))


@router.post("/prospects/{prospect_id}/status")
async def change_prospect_status(
    request: Request,
    user: CurrentUser,
    session: DbSession,
    prospect_id: UUID,
    status: Annotated[str, Form()],
    search_id: Annotated[str, Form()] = "",
    filter_status: Annotated[str, Form()] = "",
    no_website: Annotated[str, Form()] = "",
    social_only: Annotated[str, Form()] = "",
    has_whatsapp: Annotated[str, Form()] = "",
    has_email: Annotated[str, Form()] = "",
    min_score: Annotated[str, Form()] = "",
) -> Response:
    try:
        chosen = ProspectStatus(status)
    except ValueError:
        chosen = None  # a value outside the list leaves the row alone

    if chosen is ProspectStatus.WON:
        # Marking a prospect as won *is* turning it into a client: this is
        # the one place that composes the hunter with the clients module.
        await _service(session).convert_to_client(prospect_id, ClientsApi(session))
    elif chosen is not None:
        await _service(session).set_prospect_status(prospect_id, chosen)

    return await _refreshed_list(
        request,
        session,
        {
            "search_id": search_id,
            "filter_status": filter_status,
            "no_website": no_website,
            "social_only": social_only,
            "has_whatsapp": has_whatsapp,
            "has_email": has_email,
            "min_score": min_score,
        },
    )


@router.post("/prospects/{prospect_id}/do-not-contact")
async def mark_do_not_contact(
    request: Request,
    user: CurrentUser,
    session: DbSession,
    prospect_id: UUID,
    search_id: Annotated[str, Form()] = "",
    filter_status: Annotated[str, Form()] = "",
    no_website: Annotated[str, Form()] = "",
    social_only: Annotated[str, Form()] = "",
    has_whatsapp: Annotated[str, Form()] = "",
    has_email: Annotated[str, Form()] = "",
    min_score: Annotated[str, Form()] = "",
) -> Response:
    await _service(session).set_do_not_contact(prospect_id, flag=True)
    return await _refreshed_list(
        request,
        session,
        {
            "search_id": search_id,
            "filter_status": filter_status,
            "no_website": no_website,
            "social_only": social_only,
            "has_whatsapp": has_whatsapp,
            "has_email": has_email,
            "min_score": min_score,
        },
    )


@router.get("/prospects.csv")
async def export_prospects_csv(
    user: CurrentUser,
    session: DbSession,
    search_id: str = "",
    status: str = "",
    no_website: str = "",
    social_only: str = "",
    has_whatsapp: str = "",
    has_email: str = "",
    min_score: str = "",
) -> Response:
    filters = _filters(
        search_id, status, no_website, social_only, has_whatsapp, has_email, min_score
    )
    body = await _service(session).export_csv(filters, limit=EXPORT_LIMIT)
    filename = f"empresas-{today_local().strftime('%Y-%m-%d')}.csv"
    return Response(
        content=body.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"content-disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/prospects/phones")
async def list_phones(
    user: CurrentUser,
    session: DbSession,
    search_id: str = "",
    status: str = "",
    no_website: str = "",
    social_only: str = "",
    has_whatsapp: str = "",
    has_email: str = "",
    min_score: str = "",
) -> PlainTextResponse:
    """Plain numbers, one per line, for the copy button."""
    filters = _filters(
        search_id, status, no_website, social_only, has_whatsapp, has_email, min_score
    )
    numbers = await _service(session).phone_list(filters, limit=EXPORT_LIMIT)
    return PlainTextResponse("\n".join(numbers))


# ── buscas ────────────────────────────────────────────────────


@router.get("/searches")
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
        prospects=await service.list_prospects(search_id, limit=PROSPECT_LIST_LIMIT),
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
        prospects=await service.list_prospects(search_id, limit=PROSPECT_LIST_LIMIT),
    )
