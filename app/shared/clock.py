"""Time helpers. The product reasons in America/Sao_Paulo, stores in UTC."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

from app.shared.config import get_settings


@lru_cache(maxsize=1)
def local_timezone() -> ZoneInfo:
    return ZoneInfo(get_settings().tz)


def now_utc() -> datetime:
    return datetime.now(UTC)


def now_local() -> datetime:
    return datetime.now(local_timezone())


def today_local() -> date:
    return now_local().date()


def to_local(moment: datetime) -> datetime:
    """Render an aware (or UTC-assumed) instant in the local timezone."""
    aware = moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)
    return aware.astimezone(local_timezone())


def local_day_bounds(day: date) -> tuple[datetime, datetime]:
    """UTC half-open range ``[start, end)`` covering a local calendar day."""
    tz = local_timezone()
    start = datetime.combine(day, time.min, tzinfo=tz)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=tz)
    return start.astimezone(UTC), end.astimezone(UTC)
