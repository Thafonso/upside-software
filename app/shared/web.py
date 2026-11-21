"""Sign in, sign out and the health endpoint."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse, Response
from sqlalchemy import text

from app.shared.auth import (
    DbSession,
    authenticate,
    clear_session,
    current_user,
    issue_session,
)
from app.shared.models import AppUser
from app.shared.templating import page

router = APIRouter(tags=["auth"])

HOME_PATH = "/clients"


def safe_next(candidate: str | None) -> str:
    """Only allow same-site relative redirects."""
    if not candidate or not candidate.startswith("/") or candidate.startswith("//"):
        return HOME_PATH
    return candidate


@router.get("/healthz", include_in_schema=False)
async def healthz(session: DbSession) -> dict[str, str]:
    await session.execute(text("SELECT 1"))
    return {"status": "ok"}


@router.get("/", include_in_schema=False)
async def root(user: Annotated[AppUser | None, Depends(current_user)]) -> RedirectResponse:
    return RedirectResponse(HOME_PATH if user else "/login", status_code=303)


@router.get("/login")
async def login_form(
    request: Request,
    user: Annotated[AppUser | None, Depends(current_user)],
    next: str = "",
) -> Response:
    if user is not None:
        return RedirectResponse(safe_next(next), status_code=303)
    return page(request, "auth/login.html", next=safe_next(next))


@router.post("/login")
async def login_submit(
    request: Request,
    session: DbSession,
    email: Annotated[str, Form()],
    password: Annotated[str, Form()],
    next: Annotated[str, Form()] = "",
) -> Response:
    user = await authenticate(session, email, password)
    if user is None:
        return page(
            request,
            "auth/login.html",
            next=safe_next(next),
            email=email,
            error="E-mail ou senha incorretos.",
            status_code=401,
        )
    response = RedirectResponse(safe_next(next), status_code=303)
    issue_session(response, user.id)
    return response


@router.post("/logout")
async def logout() -> RedirectResponse:
    response = RedirectResponse("/login", status_code=303)
    clear_session(response)
    return response
