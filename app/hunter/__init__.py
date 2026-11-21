"""Hunter module - public API.

Other modules may only import this package::

    from app.hunter import HunterApi

Importing it also registers the module's queue handlers, which is how the
worker learns about ``hunter.start_scrape`` and ``hunter.poll_scrape``.
"""

from __future__ import annotations

from app.hunter import jobs as _jobs  # noqa: F401  (registers the queue handlers)
from app.hunter.api import HunterApi
from app.hunter.domain import Prospect, ProspectStatus, Search, SearchStatus
from app.hunter.web import router

__all__ = [
    "HunterApi",
    "Prospect",
    "ProspectStatus",
    "Search",
    "SearchStatus",
    "router",
]
