"""The ``job`` table and its pure scheduling rules."""

from __future__ import annotations

from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from sqlalchemy import Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db import Base, CreatedAt, EnumAsString, Timestamptz, UpdatedAt, UuidPk

DEFAULT_MAX_ATTEMPTS = 5

# Exponential backoff: 2 minutes, then 4, 8, 16, 32.
BACKOFF_BASE_MINUTES = 2
BACKOFF_CAP = timedelta(hours=6)


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"

    @property
    def label(self) -> str:
        return {
            JobStatus.QUEUED: "Na fila",
            JobStatus.RUNNING: "Rodando",
            JobStatus.DONE: "Concluído",
            JobStatus.FAILED: "Falhou",
        }[self]


def backoff_delay(attempts: int) -> timedelta:
    """Wait before retry number ``attempts`` (1-based), capped."""
    safe = max(attempts, 1)
    if safe > 20:  # 2**20 minutes already exceeds the cap by far
        return BACKOFF_CAP
    return min(timedelta(minutes=BACKOFF_BASE_MINUTES**safe), BACKOFF_CAP)


def next_run_after(now: datetime, attempts: int) -> datetime:
    return now + backoff_delay(attempts)


class Job(Base):
    """One unit of background work.

    A handler is looked up by ``type``; ``payload`` carries its arguments.
    Every handler must be idempotent: a job can run again after a crash.
    """

    __tablename__ = "job"
    __table_args__ = (
        # Serves the claim query: status + run_after, in that order.
        Index("ix_job_status_run_after", "status", "run_after"),
        # Serves the admin list, which is newest first.
        Index("ix_job_created_at", "created_at"),
    )

    id: Mapped[UuidPk]
    type: Mapped[str] = mapped_column(String(80), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    status: Mapped[JobStatus] = mapped_column(EnumAsString(JobStatus, 16), default=JobStatus.QUEUED)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=DEFAULT_MAX_ATTEMPTS)
    run_after: Mapped[Timestamptz]
    last_error: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[UpdatedAt]

    @property
    def status_label(self) -> str:
        return JobStatus(self.status).label

    @property
    def can_requeue(self) -> bool:
        return JobStatus(self.status) == JobStatus.FAILED

    @property
    def attempts_label(self) -> str:
        return f"{self.attempts}/{self.max_attempts}"

    @property
    def short_error(self) -> str | None:
        if not self.last_error:
            return None
        first_line = self.last_error.strip().splitlines()[-1]
        return first_line[:160]
