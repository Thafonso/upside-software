"""Clients module - public API.

Other modules may only import this package::

    from app.clients import ClientsApi

Everything inside (``repository``, ``service``, ``web``) is private to the
module; ``import-linter`` enforces it.
"""

from __future__ import annotations

from app.clients.api import ClientsApi
from app.clients.domain import Client, ClientStatus
from app.clients.web import router

__all__ = ["Client", "ClientStatus", "ClientsApi", "router"]
