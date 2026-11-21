"""Queue rules that need no database: backoff, registry, RetryLater."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest

from app.shared.jobs.models import BACKOFF_CAP, JobStatus, backoff_delay, next_run_after
from app.shared.jobs.registry import (
    DuplicateHandlerError,
    JobContext,
    RetryLater,
    UnknownJobTypeError,
    clear_handlers,
    get_handler,
    is_registered,
    job_handler,
    registered_types,
    restore_handlers,
    snapshot_handlers,
)


@pytest.fixture(autouse=True)
def _isolated_registry() -> Iterator[None]:
    """Start from an empty registry and put the real handlers back after."""
    saved = snapshot_handlers()
    clear_handlers()
    yield
    restore_handlers(saved)


# ── Backoff ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("attempts", "minutes"),
    [(1, 2), (2, 4), (3, 8), (4, 16), (5, 32)],
)
def test_backoff_doubles_each_attempt(attempts: int, minutes: int) -> None:
    assert backoff_delay(attempts) == timedelta(minutes=minutes)


def test_backoff_floors_at_the_first_attempt() -> None:
    assert backoff_delay(0) == timedelta(minutes=2)
    assert backoff_delay(-3) == timedelta(minutes=2)


def test_backoff_is_capped() -> None:
    assert backoff_delay(12) == BACKOFF_CAP
    assert backoff_delay(500) == BACKOFF_CAP


def test_next_run_after_adds_the_delay() -> None:
    now = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
    assert next_run_after(now, 1) == now + timedelta(minutes=2)
    assert next_run_after(now, 3) == now + timedelta(minutes=8)


# ── Situações ─────────────────────────────────────────────────


def test_status_labels_are_portuguese() -> None:
    assert JobStatus.QUEUED.label == "Na fila"
    assert JobStatus.RUNNING.label == "Rodando"
    assert JobStatus.DONE.label == "Concluído"
    assert JobStatus.FAILED.label == "Falhou"


# ── RetryLater ────────────────────────────────────────────────


def test_retry_later_keeps_a_sane_delay() -> None:
    assert RetryLater().seconds == 15
    assert RetryLater(seconds=30).seconds == 30
    assert RetryLater(seconds=0).seconds == 1
    assert RetryLater(seconds=-5).seconds == 1


# ── Registro de handlers ──────────────────────────────────────


async def test_handler_is_found_by_type() -> None:
    @job_handler("test.ping")
    async def ping(payload: dict, ctx: JobContext) -> None:
        return None

    assert is_registered("test.ping")
    assert get_handler("test.ping") is ping
    assert registered_types() == ("test.ping",)


def test_unknown_type_raises() -> None:
    with pytest.raises(UnknownJobTypeError):
        get_handler("test.inexistente")
    assert not is_registered("test.inexistente")


def test_two_handlers_for_one_type_is_a_bug() -> None:
    @job_handler("test.dup")
    async def first(payload: dict, ctx: JobContext) -> None:
        return None

    with pytest.raises(DuplicateHandlerError):

        @job_handler("test.dup")
        async def second(payload: dict, ctx: JobContext) -> None:
            return None


def test_registering_the_same_function_twice_is_allowed() -> None:
    async def handler(payload: dict, ctx: JobContext) -> None:
        return None

    job_handler("test.same")(handler)
    job_handler("test.same")(handler)
    assert get_handler("test.same") is handler


def test_registered_types_are_sorted() -> None:
    for name in ("test.c", "test.a", "test.b"):

        async def handler(payload: dict, ctx: JobContext) -> None:
            return None

        job_handler(name)(handler)

    assert registered_types() == ("test.a", "test.b", "test.c")


# ── Contexto ──────────────────────────────────────────────────


def test_context_knows_when_it_is_the_last_attempt() -> None:
    from uuid import uuid4

    def build(attempt: int) -> JobContext:
        return JobContext(
            job_id=uuid4(),
            job_type="test.ping",
            attempt=attempt,
            max_attempts=5,
            session=None,  # pyright: ignore[reportArgumentType]
        )

    assert build(1).is_last_attempt is False
    assert build(4).is_last_attempt is False
    assert build(5).is_last_attempt is True
