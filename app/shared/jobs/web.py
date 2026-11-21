"""Admin screen for the queue: what ran, what failed, what to run again."""

from __future__ import annotations

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Form, Request
from fastapi.responses import Response

from app.shared.auth import AdminUser, DbSession
from app.shared.jobs import queue
from app.shared.jobs.models import JobStatus
from app.shared.jobs.registry import registered_types
from app.shared.templating import fragment, page

router = APIRouter(prefix="/admin/jobs", tags=["admin"])

LIST_LIMIT = 100
ALL_STATUSES = tuple(JobStatus)


def _parse_status(raw: str) -> JobStatus | None:
    try:
        return JobStatus(raw)
    except ValueError:
        return None


async def _context(session: DbSession, status: str, job_type: str) -> dict[str, Any]:
    chosen = _parse_status(status)
    jobs = await queue.list_recent(
        session, status=chosen, job_type=job_type or None, limit=LIST_LIMIT
    )
    return {
        "jobs": jobs,
        "counts": await queue.count_by_status(session),
        "status": status,
        "job_type": job_type,
        "statuses": ALL_STATUSES,
        "known_types": registered_types(),
        "limit": LIST_LIMIT,
    }


@router.get("")
async def list_jobs(
    request: Request,
    user: AdminUser,
    session: DbSession,
    status: str = "",
    job_type: str = "",
) -> Response:
    context = await _context(session, status, job_type)
    if request.headers.get("hx-request") == "true":
        return fragment(request, "jobs/_rows.html", **context)
    return page(request, "jobs/list.html", user, active="jobs", **context)


@router.post("/{job_id}/requeue")
async def requeue_job(
    request: Request,
    user: AdminUser,
    session: DbSession,
    job_id: UUID,
    status: Annotated[str, Form()] = "",
    job_type: Annotated[str, Form()] = "",
) -> Response:
    await queue.requeue(session, job_id)
    context = await _context(session, status, job_type)
    return fragment(request, "jobs/_rows.html", **context)
