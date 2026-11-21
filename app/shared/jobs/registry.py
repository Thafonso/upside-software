"""Handler registry and the context a handler receives."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from importlib import import_module
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class RetryLater(Exception):
    """Run this job again later without counting an attempt.

    Used while waiting on something outside the app, such as the scraper
    still working on a search.
    """

    def __init__(self, seconds: int = 15) -> None:
        self.seconds = max(int(seconds), 1)
        super().__init__(f"retry in {self.seconds}s")


@dataclass(frozen=True, slots=True)
class JobContext:
    """What a handler is given besides its payload.

    ``session`` is the transaction the handler runs in: the work it does and
    the job being marked done commit together, which is what keeps a retry
    after a crash from double-applying anything.
    """

    job_id: UUID
    job_type: str
    attempt: int
    max_attempts: int
    session: AsyncSession

    @property
    def is_last_attempt(self) -> bool:
        return self.attempt >= self.max_attempts


JobHandler = Callable[[dict[str, Any], JobContext], Awaitable[None]]

_handlers: dict[str, JobHandler] = {}


class DuplicateHandlerError(RuntimeError):
    """Two handlers registered under the same job type."""


class UnknownJobTypeError(LookupError):
    """A job in the queue has no handler in this process."""


def job_handler(job_type: str) -> Callable[[JobHandler], JobHandler]:
    """Register a queue handler::

    @job_handler("hunter.poll_scrape")
    async def poll_scrape(payload: dict, ctx: JobContext) -> None: ...
    """

    def decorator(handler: JobHandler) -> JobHandler:
        existing = _handlers.get(job_type)
        if existing is not None and existing is not handler:
            msg = f"job type already registered: {job_type}"
            raise DuplicateHandlerError(msg)
        _handlers[job_type] = handler
        return handler

    return decorator


def get_handler(job_type: str) -> JobHandler:
    handler = _handlers.get(job_type)
    if handler is None:
        raise UnknownJobTypeError(job_type)
    return handler


def is_registered(job_type: str) -> bool:
    return job_type in _handlers


def registered_types() -> tuple[str, ...]:
    return tuple(sorted(_handlers))


def clear_handlers() -> None:
    """Drop every registration. Used by tests."""
    _handlers.clear()


def load_handler_modules(module_names: tuple[str, ...]) -> None:
    """Import the modules that register handlers, so the worker knows them."""
    for name in module_names:
        import_module(name)
        logger.info("handlers loaded from %s", name)
