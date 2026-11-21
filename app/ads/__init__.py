"""Ads module - public API, read-only against Meta.

Other modules may only import this package::

    from app.ads import AdsApi

Importing it also registers the module's queue handlers, which is how the
worker learns about the sync jobs.
"""

from __future__ import annotations

from app.ads import jobs as _jobs  # noqa: F401  (registers the queue handlers)
from app.ads.api import AdsApi
from app.ads.domain import AdAccount, Campaign, DailyRow, SyncKind, SyncStatus
from app.ads.web import router

__all__ = [
    "AdAccount",
    "AdsApi",
    "Campaign",
    "DailyRow",
    "SyncKind",
    "SyncStatus",
    "router",
]
