"""Talks to the MazyOS scraper service over HTTP.

The scraper is vendored unchanged, so every quirk of its API is absorbed
here: job fields come back in PascalCase (a Go struct without tags),
``emails`` is one comma-separated string, and creating a job answers 201.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from app.hunter.domain import FoundCompany, ScrapeStatus, SearchRequest
from app.hunter.ports import ScraperError
from app.shared.config import get_settings

logger = logging.getLogger("app.hunter.scraper")

# Starting a job is quick; waiting on a page of results is not.
CONNECT_TIMEOUT = 5.0
READ_TIMEOUT = 60.0

# Keeps the unique index on maps_link inside the btree page limit.
MAPS_LINK_MAX = 2000

STATUS_BY_NAME: dict[str, ScrapeStatus] = {
    "pending": ScrapeStatus.PENDING,
    "working": ScrapeStatus.RUNNING,
    "ok": ScrapeStatus.DONE,
    "failed": ScrapeStatus.FAILED,
}


def _decimal(value: Any) -> Decimal | None:
    if value in (None, "", 0):
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError):
        return None


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned or None


def parse_emails(raw: Any) -> tuple[str, ...]:
    """``"a@x.com, B@X.com"`` -> ``("a@x.com",)`` - lowercased, deduplicated."""
    if not isinstance(raw, str) or not raw.strip():
        return ()
    found: list[str] = []
    for piece in raw.replace(";", ",").split(","):
        address = piece.strip().lower()
        if "@" in address and address not in found:
            found.append(address)
    return tuple(found)


def to_found_company(row: dict[str, Any]) -> FoundCompany | None:
    """Convert one ``/places`` row. ``None`` when it is unusable."""
    name = _text(row.get("title"))
    maps_link = _text(row.get("link"))
    if not name or not maps_link:
        return None

    count = row.get("review_count") or 0
    try:
        review_count = max(int(count), 0)
    except (TypeError, ValueError):
        review_count = 0

    return FoundCompany(
        name=name[:200],
        # The link is the dedup key and carries a unique index.
        maps_link=maps_link[:MAPS_LINK_MAX],
        address=_text(row.get("address")),
        category=_text(row.get("category")),
        phone=_text(row.get("phone")),
        website=_text(row.get("website")),
        borough=_text(row.get("borough")),
        city=_text(row.get("city")),
        latitude=_decimal(row.get("latitude")),
        longitude=_decimal(row.get("longitude")),
        review_rating=_decimal(row.get("review_rating")),
        review_count=review_count,
        emails=parse_emails(row.get("emails")),
    )


class MazyosScraperProvider:
    """Implements :class:`app.hunter.ports.CompanySearchProvider`."""

    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def start(self, request: SearchRequest) -> str:
        body = {
            "name": request.name,
            "keywords": list(request.queries),
            "lang": request.language,
            "depth": request.depth,
            "email": request.fetch_emails,
            "max_time": request.max_time_seconds,
        }
        response = await self._request("POST", "/api/v1/jobs", json=body)

        if response.status_code == httpx.codes.UNPROCESSABLE_ENTITY:
            raise ScraperError(f"o scraper recusou a busca: {_message(response)}")
        if response.status_code not in (httpx.codes.OK, httpx.codes.CREATED):
            raise ScraperError(f"o scraper respondeu {response.status_code} ao criar a busca")

        job_id = _text(_json(response).get("id"))
        if not job_id:
            raise ScraperError("o scraper não devolveu o id da busca")
        return job_id

    async def status(self, external_job_id: str) -> ScrapeStatus:
        response = await self._request("GET", f"/api/v1/jobs/{external_job_id}")
        if response.status_code == httpx.codes.NOT_FOUND:
            raise ScraperError("a busca não existe mais no scraper")
        if response.status_code != httpx.codes.OK:
            raise ScraperError(f"o scraper respondeu {response.status_code} ao consultar a busca")

        # Go struct without json tags: the keys come back in PascalCase.
        raw = _text(_json(response).get("Status"))
        known = STATUS_BY_NAME.get((raw or "").lower())
        if known is None:
            raise ScraperError(f"situação desconhecida no scraper: {raw!r}")
        return known

    async def results(self, external_job_id: str) -> list[FoundCompany]:
        response = await self._request("GET", f"/api/v1/jobs/{external_job_id}/places")
        if response.status_code != httpx.codes.OK:
            raise ScraperError(
                f"o scraper respondeu {response.status_code} ao buscar os resultados"
            )

        payload = _json(response, default=[])
        if not isinstance(payload, list):
            raise ScraperError("o scraper devolveu resultados em formato inesperado")

        companies: list[FoundCompany] = []
        for row in payload:
            if not isinstance(row, dict):
                continue
            company = to_found_company(row)
            if company is not None:
                companies.append(company)

        skipped = len(payload) - len(companies)
        if skipped:
            logger.warning("%s resultado(s) sem nome ou link foram ignorados", skipped)
        return companies

    async def discard(self, external_job_id: str) -> None:
        response = await self._request("DELETE", f"/api/v1/jobs/{external_job_id}")
        if response.status_code not in (
            httpx.codes.OK,
            httpx.codes.NO_CONTENT,
            httpx.codes.NOT_FOUND,
        ):
            logger.warning(
                "scraper respondeu %s ao apagar o job %s",
                response.status_code,
                external_job_id,
            )

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            return await self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise ScraperError(f"não foi possível falar com o scraper: {exc}") from exc


def _json(response: httpx.Response, default: Any = None) -> Any:
    try:
        return response.json()
    except ValueError:
        if default is not None:
            return default
        raise ScraperError("o scraper devolveu uma resposta que não é JSON") from None


def _message(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text[:200]
    if isinstance(payload, dict):
        return str(payload.get("message") or payload)[:200]
    return str(payload)[:200]


@asynccontextmanager
async def scraper_provider() -> AsyncIterator[MazyosScraperProvider]:
    """Build a provider bound to a client that is closed afterwards."""
    timeout = httpx.Timeout(READ_TIMEOUT, connect=CONNECT_TIMEOUT)
    async with httpx.AsyncClient(base_url=get_settings().scraper_url, timeout=timeout) as client:
        yield MazyosScraperProvider(client)
