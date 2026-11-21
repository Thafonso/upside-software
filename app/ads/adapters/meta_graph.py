"""Reads the Meta Graph API. Nothing here ever writes to Meta.

Two things this layer absorbs: the Graph API pages its answers, and it
reports how much of our rate budget we have used in a header rather than
in the body. Both are turned into domain terms before anything else sees
them.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx

from app.ads.domain import (
    Campaign,
    DailyInsight,
    parse_action_counts,
    parse_action_values,
)
from app.ads.ports import AdsGatewayError, RateLimited
from app.shared.config import get_settings

logger = logging.getLogger("app.ads.meta")

CONNECT_TIMEOUT = 5.0
READ_TIMEOUT = 60.0

# Above this share of the window Meta starts throttling, so we back off first.
USAGE_THRESHOLD = 75
DEFAULT_BACKOFF_SECONDS = 300
# Meta reports the wait in minutes.
SECONDS_PER_MINUTE = 60

# A throttle, not a bug in our request.
RATE_LIMIT_CODES = {17, 80004}

INSIGHT_FIELDS = (
    "campaign_id",
    "campaign_name",
    "spend",
    "impressions",
    "reach",
    "inline_link_clicks",
    "actions",
    "action_values",
)
CAMPAIGN_FIELDS = ("id", "name", "status", "objective")

# A runaway "next" chain must not keep a worker busy forever.
MAX_PAGES = 50


def _decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value or "0")).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0.00")


def _int(value: Any) -> int:
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _day(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()  # noqa: DTZ007
    except ValueError:
        return None


def worst_usage(header: str | None) -> int:
    """Highest percentage Meta reports in ``x-business-use-case-usage``."""
    if not header:
        return 0
    try:
        payload = json.loads(header)
    except ValueError:
        return 0
    if not isinstance(payload, dict):
        return 0

    highest = 0
    for entries in payload.values():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            for key in ("call_count", "total_cputime", "total_time"):
                highest = max(highest, _int(entry.get(key)))
    return highest


def suggested_wait(header: str | None) -> int:
    """How long Meta says we should wait, in seconds."""
    if not header:
        return DEFAULT_BACKOFF_SECONDS
    try:
        payload = json.loads(header)
    except ValueError:
        return DEFAULT_BACKOFF_SECONDS
    if not isinstance(payload, dict):
        return DEFAULT_BACKOFF_SECONDS

    minutes = 0
    for entries in payload.values():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if isinstance(entry, dict):
                minutes = max(minutes, _int(entry.get("estimated_time_to_regain_access")))
    return minutes * SECONDS_PER_MINUTE or DEFAULT_BACKOFF_SECONDS


def to_campaign(row: dict[str, Any], account_id: str) -> Campaign | None:
    campaign_id = row.get("id")
    if not isinstance(campaign_id, str) or not campaign_id:
        return None
    return Campaign(
        id=campaign_id,
        account_id=account_id,
        name=str(row.get("name") or campaign_id)[:200],
        status=str(row.get("status") or "UNKNOWN")[:32],
        objective=(str(row["objective"])[:64] if row.get("objective") else None),
    )


def to_daily_insight(row: dict[str, Any]) -> DailyInsight | None:
    """One row of ``/insights`` with ``time_increment=1``."""
    day = _day(row.get("date_start"))
    campaign_id = row.get("campaign_id")
    if day is None or not isinstance(campaign_id, str) or not campaign_id:
        return None

    return DailyInsight(
        day=day,
        campaign_id=campaign_id,
        campaign_name=str(row.get("campaign_name") or campaign_id)[:200],
        spend=_decimal(row.get("spend")),
        impressions=_int(row.get("impressions")),
        reach=_int(row.get("reach")),
        link_clicks=_int(row.get("inline_link_clicks")),
        actions=parse_action_counts(row.get("actions")),
        action_values=parse_action_values(row.get("action_values")),
    )


class MetaGraphGateway:
    """Implements :class:`app.ads.ports.AdsPlatformGateway`."""

    def __init__(self, client: httpx.AsyncClient, *, token: str, version: str) -> None:
        self._client = client
        self._token = token
        self._version = version

    async def list_campaigns(self, account_id: str) -> list[Campaign]:
        rows = await self._collect(
            f"/{self._version}/{account_id}/campaigns",
            {"fields": ",".join(CAMPAIGN_FIELDS), "limit": 200},
        )
        campaigns = [to_campaign(row, account_id) for row in rows]
        return [campaign for campaign in campaigns if campaign is not None]

    async def daily_insights(self, account_id: str, since: date, until: date) -> list[DailyInsight]:
        rows = await self._collect(
            f"/{self._version}/{account_id}/insights",
            {
                "level": "campaign",
                "time_increment": 1,
                "fields": ",".join(INSIGHT_FIELDS),
                "time_range": json.dumps({"since": since.isoformat(), "until": until.isoformat()}),
                "limit": 500,
            },
        )
        insights = [to_daily_insight(row) for row in rows]
        return [insight for insight in insights if insight is not None]

    async def _collect(self, path: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        """Walk the ``paging.next`` chain and return every row."""
        collected: list[dict[str, Any]] = []
        url: str | None = path
        query: dict[str, Any] | None = {**params, "access_token": self._token}

        for page in range(MAX_PAGES):
            payload = await self._get(url, query)
            data = payload.get("data")
            if isinstance(data, list):
                collected.extend(item for item in data if isinstance(item, dict))

            paging = payload.get("paging")
            following = paging.get("next") if isinstance(paging, dict) else None
            if not isinstance(following, str) or not following:
                return collected

            # "next" is absolute and already carries the token and the cursor.
            url, query = following, None
            if page == MAX_PAGES - 1:
                logger.warning("stopped paging %s after %s pages", path, MAX_PAGES)

        return collected

    async def _get(self, url: str, params: dict[str, Any] | None) -> dict[str, Any]:
        try:
            response = await self._client.get(url, params=params)
        except httpx.HTTPError as exc:
            raise AdsGatewayError(f"não foi possível falar com a Meta: {exc}") from exc

        usage_header = response.headers.get("x-business-use-case-usage")

        if response.status_code == httpx.codes.OK:
            # Back off before Meta starts refusing, not after.
            usage = worst_usage(usage_header)
            if usage >= USAGE_THRESHOLD:
                raise RateLimited(
                    suggested_wait(usage_header),
                    f"uso da API da Meta em {usage}%",
                )
            return _payload(response)

        error = _error(response)
        code = _int(error.get("code"))
        if code in RATE_LIMIT_CODES or response.status_code == httpx.codes.TOO_MANY_REQUESTS:
            raise RateLimited(
                suggested_wait(usage_header), f"Meta pediu para esperar (código {code})"
            )

        message = str(error.get("message") or response.text)[:300]
        raise AdsGatewayError(f"Meta respondeu {response.status_code}: {message}")


def _payload(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise AdsGatewayError("a Meta devolveu algo que não é JSON") from exc
    if not isinstance(payload, dict):
        raise AdsGatewayError("a Meta devolveu um formato inesperado")
    return payload


def _error(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError:
        return {}
    if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
        return payload["error"]
    return {}


@asynccontextmanager
async def meta_gateway() -> AsyncIterator[MetaGraphGateway]:
    """Build a gateway bound to a client that is closed afterwards."""
    settings = get_settings()
    if not settings.meta_system_token:
        msg = "META_SYSTEM_TOKEN não está configurado"
        raise AdsGatewayError(msg)

    timeout = httpx.Timeout(READ_TIMEOUT, connect=CONNECT_TIMEOUT)
    async with httpx.AsyncClient(base_url="https://graph.facebook.com", timeout=timeout) as client:
        yield MetaGraphGateway(
            client, token=settings.meta_system_token, version=settings.meta_api_version
        )
