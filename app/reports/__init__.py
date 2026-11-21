"""Reports module - public API.

Other modules may only import this package::

    from app.reports import ReportsApi

The module has no queue handlers: a report is built from numbers that are
already in our database, so generating it is fast enough to do in the request
that asked for it.
"""

from __future__ import annotations

from app.reports.api import ReportsApi
from app.reports.domain import MonthPeriod, Report, Snapshot
from app.reports.web import router

__all__ = ["MonthPeriod", "Report", "ReportsApi", "Snapshot", "router"]
