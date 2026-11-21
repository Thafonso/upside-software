"""FastAPI application: wires the shared routes and each module's router."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles

from app.ads import router as ads_router
from app.clients import router as clients_router
from app.hunter import router as hunter_router
from app.reports import router as reports_router
from app.shared.auth import NotAuthenticated, NotAuthorized
from app.shared.config import get_settings
from app.shared.db import dispose_engine
from app.shared.jobs import router as jobs_router
from app.shared.templating import page
from app.shared.web import router as shared_router

STATIC_DIR = Path(__file__).resolve().parent / "static"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    get_settings()  # fail fast on a bad environment
    yield
    await dispose_engine()


app = FastAPI(
    title="Upside LeadHunter",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.include_router(shared_router)
app.include_router(clients_router)
app.include_router(hunter_router)
app.include_router(ads_router)
app.include_router(reports_router)
app.include_router(jobs_router)


@app.exception_handler(NotAuthenticated)
async def handle_not_authenticated(request: Request, _: NotAuthenticated) -> Response:
    """Send browsers to the login screen; tell HTMX to do a full redirect."""
    target = f"/login?next={request.url.path}"
    if request.headers.get("hx-request") == "true":
        return Response(status_code=401, headers={"HX-Redirect": target})
    return RedirectResponse(target, status_code=303)


@app.exception_handler(NotAuthorized)
async def handle_not_authorized(request: Request, _: NotAuthorized) -> Response:
    return page(
        request,
        "errors/forbidden.html",
        None,
        message="Esta tela é restrita aos administradores.",
        status_code=403,
    )
