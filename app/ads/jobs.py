"""Queue handlers for the ads sync. One job per account, always idempotent."""

from __future__ import annotations

import logging
from typing import Any

from app.ads.adapters.meta_graph import meta_gateway
from app.ads.domain import SyncKind
from app.ads.ports import AdsGatewayError, RateLimited
from app.ads.repository import SqlAlchemyAdsRepository
from app.ads.service import AccountNotLinked, AdsService
from app.clients import ClientsApi
from app.shared.jobs import JobContext, RetryLater, enqueue, job_handler

logger = logging.getLogger("app.ads.jobs")

ENQUEUE_HOURLY_SYNC = "ads.enqueue_hourly_sync"
ENQUEUE_RESYNC_7D = "ads.enqueue_resync_7d"
SYNC_ACCOUNT = "ads.sync_account"
RESYNC_7D = "ads.resync_7d"
SYNC_STRUCTURE = "ads.sync_structure"


def _service(ctx: JobContext) -> AdsService:
    # The ads module composes with the clients module here and in web.py,
    # always through the public API.
    return AdsService(SqlAlchemyAdsRepository(ctx.session), ClientsApi(ctx.session))


def _account_id(payload: dict[str, Any]) -> str:
    return str(payload["account_id"])


async def _fan_out(ctx: JobContext, job_type: str) -> int:
    """One job per active account, so a slow account never blocks the rest."""
    accounts = await _service(ctx).list_accounts(only_active=True)
    for account in accounts:
        await enqueue(ctx.session, job_type, {"account_id": account.id})
    logger.info("enqueued %s for %s account(s)", job_type, len(accounts))
    return len(accounts)


@job_handler(ENQUEUE_HOURLY_SYNC)
async def enqueue_hourly_sync(payload: dict[str, Any], ctx: JobContext) -> None:
    """Every hour: ask each account for today's numbers."""
    await _fan_out(ctx, SYNC_ACCOUNT)


@job_handler(ENQUEUE_RESYNC_7D)
async def enqueue_resync_7d(payload: dict[str, Any], ctx: JobContext) -> None:
    """Once a day: redo the last seven days, and refresh names and statuses.

    The structure sync rides along here because it is also a daily job and
    the specification arms only two clock triggers.
    """
    await _fan_out(ctx, RESYNC_7D)
    await _fan_out(ctx, SYNC_STRUCTURE)


@job_handler(SYNC_ACCOUNT)
async def sync_account(payload: dict[str, Any], ctx: JobContext) -> None:
    account_id = _account_id(payload)
    try:
        async with meta_gateway() as gateway:
            await _service(ctx).sync_day(account_id, gateway)
    except RateLimited as limit:
        raise RetryLater(seconds=limit.retry_after) from limit
    except AccountNotLinked:
        logger.info("account %s is no longer linked, nothing to sync", account_id)
    except AdsGatewayError as exc:
        # Raised before the sync could start, so the service never saw it:
        # a missing token, for one. Logged instead of retried, because
        # trying again changes nothing until someone fixes the setting.
        await _service(ctx).record_failure(account_id, SyncKind.DAILY, str(exc))
        logger.error("sync could not start for %s: %s", account_id, exc)


@job_handler(RESYNC_7D)
async def resync_7d(payload: dict[str, Any], ctx: JobContext) -> None:
    account_id = _account_id(payload)
    try:
        async with meta_gateway() as gateway:
            await _service(ctx).resync_recent(account_id, gateway)
    except RateLimited as limit:
        raise RetryLater(seconds=limit.retry_after) from limit
    except AccountNotLinked:
        logger.info("account %s is no longer linked, nothing to resync", account_id)
    except AdsGatewayError as exc:
        # Raised before the sync could start, so the service never saw it:
        # a missing token, for one. Logged instead of retried, because
        # trying again changes nothing until someone fixes the setting.
        await _service(ctx).record_failure(account_id, SyncKind.RESYNC, str(exc))
        logger.error("sync could not start for %s: %s", account_id, exc)


@job_handler(SYNC_STRUCTURE)
async def sync_structure(payload: dict[str, Any], ctx: JobContext) -> None:
    account_id = _account_id(payload)
    try:
        async with meta_gateway() as gateway:
            await _service(ctx).sync_structure(account_id, gateway)
    except RateLimited as limit:
        raise RetryLater(seconds=limit.retry_after) from limit
    except AccountNotLinked:
        logger.info("account %s is no longer linked, nothing to refresh", account_id)
    except AdsGatewayError as exc:
        # Raised before the sync could start, so the service never saw it:
        # a missing token, for one. Logged instead of retried, because
        # trying again changes nothing until someone fixes the setting.
        await _service(ctx).record_failure(account_id, SyncKind.STRUCTURE, str(exc))
        logger.error("sync could not start for %s: %s", account_id, exc)
