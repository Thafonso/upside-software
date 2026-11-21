"""Client use cases, driven through an in-memory repository."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from app.clients.domain import Client, ClientInput, ClientStatus, ClientValidationError
from app.clients.service import ClientNotFound, ClientsService


class FakeClientRepository:
    """Implements app.clients.ports.ClientRepository against a dict."""

    def __init__(self) -> None:
        self.rows: dict[UUID, Client] = {}

    def _store(self, client: Client) -> Client:
        self.rows[client.id] = client
        return client

    async def list_clients(
        self,
        *,
        search: str | None = None,
        statuses: tuple[ClientStatus, ...] | None = None,
    ) -> list[Client]:
        found = list(self.rows.values())
        if statuses:
            found = [item for item in found if item.status in statuses]
        if search:
            needle = search.lower()
            found = [item for item in found if needle in item.name.lower()]
        return sorted(found, key=lambda item: item.name.lower())

    async def get_by_id(self, client_id: UUID) -> Client | None:
        return self.rows.get(client_id)

    async def get_by_slug(self, slug: str) -> Client | None:
        return next((item for item in self.rows.values() if item.slug == slug), None)

    async def slug_exists(self, slug: str, *, excluding: UUID | None = None) -> bool:
        return any(item.slug == slug and item.id != excluding for item in self.rows.values())

    async def insert(self, data: ClientInput, *, slug: str) -> Client:
        moment = datetime(2026, 11, 20, tzinfo=UTC)
        return self._store(
            Client(
                id=uuid4(),
                name=data.name,
                slug=slug,
                status=data.status,
                result_action_type=data.result_action_type,
                contact_name=data.contact_name,
                contact_email=data.contact_email,
                contact_phone=data.contact_phone,
                monthly_fee=data.monthly_fee,
                notes=data.notes,
                created_at=moment,
                updated_at=moment,
            )
        )

    async def update(self, client_id: UUID, data: ClientInput) -> Client | None:
        current = self.rows.get(client_id)
        if current is None:
            return None
        import dataclasses

        return self._store(
            dataclasses.replace(
                current,
                name=data.name,
                status=data.status,
                result_action_type=data.result_action_type,
                contact_name=data.contact_name,
                contact_email=data.contact_email,
                contact_phone=data.contact_phone,
                monthly_fee=data.monthly_fee,
                notes=data.notes,
            )
        )

    async def set_status(self, client_id: UUID, status: ClientStatus) -> Client | None:
        current = self.rows.get(client_id)
        if current is None:
            return None
        import dataclasses

        return self._store(dataclasses.replace(current, status=status))


def _input(name: str, **overrides: object) -> ClientInput:
    base: dict[str, object] = {
        "name": name,
        "status": ClientStatus.ACTIVE,
        "result_action_type": "lead",
    }
    return ClientInput(**{**base, **overrides})  # pyright: ignore[reportArgumentType]


@pytest.fixture
def service() -> ClientsService:
    return ClientsService(FakeClientRepository())


async def test_create_derives_slug_from_name(service: ClientsService) -> None:
    client = await service.create(_input("Padaria São José"))
    assert client.slug == "padaria-sao-jose"


async def test_create_resolves_slug_collisions(service: ClientsService) -> None:
    first = await service.create(_input("Padaria São José"))
    second = await service.create(_input("Padaria Sao Jose"))
    third = await service.create(_input("padaria sao jose"))
    assert (first.slug, second.slug, third.slug) == (
        "padaria-sao-jose",
        "padaria-sao-jose-2",
        "padaria-sao-jose-3",
    )


async def test_create_falls_back_when_name_has_no_ascii(service: ClientsService) -> None:
    client = await service.create(_input("日本語"))
    assert client.slug == "cliente"


async def test_update_changes_fields_and_keeps_slug(service: ClientsService) -> None:
    client = await service.create(_input("Clinica Vida"))
    updated = await service.update(
        client.id, _input("Clinica Vida Odontologia", status=ClientStatus.PAUSED)
    )
    assert updated.name == "Clinica Vida Odontologia"
    assert updated.status is ClientStatus.PAUSED
    assert updated.slug == client.slug


async def test_update_unknown_client_raises(service: ClientsService) -> None:
    with pytest.raises(ClientNotFound):
        await service.update(uuid4(), _input("Fantasma"))


async def test_set_status_unknown_client_raises(service: ClientsService) -> None:
    with pytest.raises(ClientNotFound):
        await service.set_status(uuid4(), ClientStatus.ARCHIVED)


async def test_list_active_hides_other_statuses(service: ClientsService) -> None:
    await service.create(_input("Ativo"))
    paused = await service.create(_input("Pausado"))
    await service.set_status(paused.id, ClientStatus.PAUSED)

    names = [client.name for client in await service.list_active()]
    assert names == ["Ativo"]


async def test_create_from_prospect_normalizes_phone(service: ClientsService) -> None:
    client = await service.create_from_prospect(
        name="Barbearia do Zé", phone="(41) 99988-7766", email="ZE@Barbearia.com"
    )
    assert client.contact_phone == "5541999887766"
    assert client.contact_email == "ze@barbearia.com"
    assert client.status is ClientStatus.ACTIVE


async def test_create_from_prospect_is_idempotent(service: ClientsService) -> None:
    first = await service.create_from_prospect(name="Barbearia do Zé")
    again = await service.create_from_prospect(name="Barbearia do Zé")
    assert first.id == again.id


async def test_slug_exhaustion_is_reported_on_the_name_field() -> None:
    repository = FakeClientRepository()
    service = ClientsService(repository)
    for _ in range(50):
        await service.create(_input("Repetido"))

    with pytest.raises(ClientValidationError) as caught:
        await service.create(_input("Repetido"))
    assert "name" in caught.value.errors
