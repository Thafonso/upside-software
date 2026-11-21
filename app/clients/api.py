"""The only surface other modules may call."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.domain import Client, ClientStatus
from app.clients.repository import SqlAlchemyClientRepository
from app.clients.service import ClientsService


class ClientsApi:
    """Facade over the clients module.

    Built with the caller's database session so a conversion done inside a
    request shares that request's transaction::

        client = await ClientsApi(session).create_client_from_prospect(name="...")
    """

    def __init__(self, session: AsyncSession) -> None:
        self._service = ClientsService(SqlAlchemyClientRepository(session))

    async def get_client(self, client_id: UUID) -> Client | None:
        return await self._service.get(client_id)

    async def list_active_clients(self) -> list[Client]:
        return await self._service.list_active()

    async def list_clients(
        self, *, statuses: tuple[ClientStatus, ...] | None = None
    ) -> list[Client]:
        return await self._service.list_clients(statuses=statuses)

    async def create_client_from_prospect(
        self,
        *,
        name: str,
        phone: str | None = None,
        email: str | None = None,
        notes: str | None = None,
    ) -> Client:
        return await self._service.create_from_prospect(
            name=name, phone=phone, email=email, notes=notes
        )
