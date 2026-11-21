"""Client use cases. Receives its repository by injection."""

from __future__ import annotations

from uuid import UUID

from app.clients.domain import (
    DEFAULT_RESULT_ACTION_TYPE,
    Client,
    ClientInput,
    ClientStatus,
    ClientValidationError,
    next_slug_candidate,
    normalize_phone,
    slugify,
)
from app.clients.ports import ClientRepository

MAX_SLUG_ATTEMPTS = 50


class ClientNotFound(LookupError):
    """No client with that id."""


class ClientsService:
    def __init__(self, repository: ClientRepository) -> None:
        self._repository = repository

    async def list_clients(
        self,
        *,
        search: str | None = None,
        statuses: tuple[ClientStatus, ...] | None = None,
    ) -> list[Client]:
        return await self._repository.list_clients(search=search, statuses=statuses)

    async def list_active(self) -> list[Client]:
        return await self._repository.list_clients(statuses=(ClientStatus.ACTIVE,))

    async def get(self, client_id: UUID) -> Client | None:
        return await self._repository.get_by_id(client_id)

    async def require(self, client_id: UUID) -> Client:
        client = await self._repository.get_by_id(client_id)
        if client is None:
            raise ClientNotFound(str(client_id))
        return client

    async def _free_slug(self, name: str, *, excluding: UUID | None = None) -> str:
        base = slugify(name) or "cliente"
        for attempt in range(1, MAX_SLUG_ATTEMPTS + 1):
            candidate = next_slug_candidate(base, attempt)
            if not await self._repository.slug_exists(candidate, excluding=excluding):
                return candidate
        raise ClientValidationError({"name": "Já existem clientes demais com esse nome."})

    async def create(self, data: ClientInput) -> Client:
        slug = await self._free_slug(data.name)
        return await self._repository.insert(data, slug=slug)

    async def update(self, client_id: UUID, data: ClientInput) -> Client:
        await self.require(client_id)
        updated = await self._repository.update(client_id, data)
        if updated is None:
            raise ClientNotFound(str(client_id))
        return updated

    async def set_status(self, client_id: UUID, status: ClientStatus) -> Client:
        updated = await self._repository.set_status(client_id, status)
        if updated is None:
            raise ClientNotFound(str(client_id))
        return updated

    async def create_from_prospect(
        self,
        *,
        name: str,
        phone: str | None = None,
        email: str | None = None,
        notes: str | None = None,
    ) -> Client:
        """Used when a hunter prospect is marked as won."""
        existing_slug = slugify(name)
        if existing_slug:
            found = await self._repository.get_by_slug(existing_slug)
            if found is not None:
                return found

        data = ClientInput(
            name=name.strip()[:160] or "Cliente sem nome",
            status=ClientStatus.ACTIVE,
            result_action_type=DEFAULT_RESULT_ACTION_TYPE,
            contact_phone=normalize_phone(phone),
            contact_email=(email or "").strip().lower() or None,
            notes=notes,
        )
        return await self.create(data)
