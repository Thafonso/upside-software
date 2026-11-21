"""In-process synchronous event bus.

Modules stay independent: the publisher knows the event, not the subscriber.
Handlers run inline, in registration order; a failing handler is logged and
does not stop the remaining handlers or the caller.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Event:
    """Marker base class for everything published on the bus."""


@dataclass(frozen=True, slots=True)
class ProspectConverted(Event):
    """A hunter prospect became an Upside client."""

    prospect_id: UUID
    client_id: UUID


@dataclass(frozen=True, slots=True)
class ReportGenerated(Event):
    """A monthly report snapshot was created."""

    report_id: UUID


Handler = Callable[[Event], None]

_subscribers: dict[type[Event], list[Handler]] = defaultdict(list)


def subscribe[E: Event](event_type: type[E], handler: Callable[[E], None]) -> None:
    _subscribers[event_type].append(handler)  # pyright: ignore[reportArgumentType]


def publish(event: Event) -> None:
    for handler in _subscribers.get(type(event), []):
        try:
            handler(event)
        except Exception:
            logger.exception("event handler failed", extra={"event": type(event).__name__})


def reset() -> None:
    """Drop every subscription. Used by tests."""
    _subscribers.clear()
