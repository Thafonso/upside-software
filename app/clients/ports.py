"""Interfaces the clients service depends on. Implementations live elsewhere."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from app.clients.domain import Client, ClientInput, ClientStatus


class ClientRepository(Protocol):
    """Persistence contract. Implemented by ``app.clients.repository``."""

    async def list_clients(
        self,
        *,
        search: str | None = None,
        statuses: tuple[ClientStatus, ...] | None = None,
    ) -> list[Client]: ...

    async def get_by_id(self, client_id: UUID) -> Client | None: ...

    async def get_by_slug(self, slug: str) -> Client | None: ...

    async def slug_exists(self, slug: str, *, excluding: UUID | None = None) -> bool: ...

    async def insert(self, data: ClientInput, *, slug: str) -> Client: ...

    async def update(self, client_id: UUID, data: ClientInput) -> Client | None: ...

    async def set_status(self, client_id: UUID, status: ClientStatus) -> Client | None: ...
