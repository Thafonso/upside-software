"""Visits a prospect's own site to see what is already running on it.

Only the home page, only what is visible in the HTML: whether a Meta pixel
is installed, and whether the page links to Instagram or WhatsApp. A site
that does not answer is simply unknown, never an error.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager

import httpx
from selectolax.lexbor import LexborHTMLParser

from app.hunter.domain import SiteCheck
from app.hunter.scoring import (
    INSTAGRAM_MARKERS,
    WHATSAPP_MARKERS,
    find_meta_pixel,
)

logger = logging.getLogger("app.hunter.sites")

REQUEST_TIMEOUT = 10.0
MAX_PARALLEL = 20
# Enough to reach the scripts in the head and the links in the footer.
MAX_BYTES = 1_000_000
# A plain desktop browser: sites answer differently to anything else.
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def _normalize(url: str) -> str:
    cleaned = url.strip()
    if not cleaned:
        return ""
    if not cleaned.startswith(("http://", "https://")):
        return f"https://{cleaned}"
    return cleaned


def read_page(html: str) -> SiteCheck:
    """Read one page. Links are taken from the markup, not from free text."""
    pixel = find_meta_pixel(html)

    tree = LexborHTMLParser(html)
    hrefs = [(node.attributes.get("href") or "").lower() for node in tree.css("a[href]")]
    instagram = any(marker in href for href in hrefs for marker in INSTAGRAM_MARKERS)
    whatsapp = any(marker in href for href in hrefs for marker in WHATSAPP_MARKERS)

    return SiteCheck(
        reachable=True,
        has_meta_pixel=pixel,
        has_instagram=instagram,
        has_whatsapp_link=whatsapp,
    )


class HttpWebsiteInspector:
    """Implements :class:`app.hunter.ports.WebsiteInspector`."""

    def __init__(self, client: httpx.AsyncClient, *, max_parallel: int = MAX_PARALLEL) -> None:
        self._client = client
        self._limit = asyncio.Semaphore(max_parallel)

    async def inspect_many(self, urls: Sequence[str]) -> dict[str, SiteCheck]:
        # Two prospects can share an address: fetch it once.
        unique = list(dict.fromkeys(url for url in urls if url and url.strip()))
        if not unique:
            return {}

        results = await asyncio.gather(
            *(self._inspect_one(url) for url in unique), return_exceptions=False
        )
        return dict(zip(unique, results, strict=True))

    async def _inspect_one(self, url: str) -> SiteCheck:
        target = _normalize(url)
        if not target:
            return SiteCheck.unreachable()

        async with self._limit:
            try:
                response = await self._client.get(target)
            except (httpx.HTTPError, UnicodeError) as exc:
                logger.info("site unreachable %s (%s)", target, type(exc).__name__)
                return SiteCheck.unreachable()

        if response.status_code >= httpx.codes.BAD_REQUEST:
            logger.info("site answered %s %s", response.status_code, target)
            return SiteCheck.unreachable()

        content_type = response.headers.get("content-type", "")
        if "html" not in content_type.lower():
            return SiteCheck.unreachable()

        return read_page(response.text[:MAX_BYTES])


@asynccontextmanager
async def website_inspector() -> AsyncIterator[HttpWebsiteInspector]:
    """Build an inspector bound to a client that is closed afterwards."""
    async with httpx.AsyncClient(
        timeout=REQUEST_TIMEOUT,
        follow_redirects=True,
        headers={"user-agent": USER_AGENT, "accept": "text/html,*/*"},
    ) as client:
        yield HttpWebsiteInspector(client)
