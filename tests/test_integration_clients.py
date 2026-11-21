"""Repository, auth and the HTTP flows against a real Postgres."""

from __future__ import annotations

from collections.abc import AsyncIterator
from decimal import Decimal

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.api import ClientsApi
from app.clients.domain import ClientInput, ClientStatus
from app.clients.repository import SqlAlchemyClientRepository
from app.clients.service import ClientsService
from app.shared.auth import authenticate, hash_password
from app.shared.models import AppUser, UserRole
from app.shared.seed import seed_admin
from tests.conftest import ADMIN_EMAIL, ADMIN_PASSWORD

pytestmark = pytest.mark.integration


def _input(name: str, **overrides: object) -> ClientInput:
    base: dict[str, object] = {
        "name": name,
        "status": ClientStatus.ACTIVE,
        "result_action_type": "lead",
    }
    return ClientInput(**{**base, **overrides})  # pyright: ignore[reportArgumentType]


# ── Repositório ───────────────────────────────────────────────


async def test_insert_and_read_back(clean_tables: AsyncSession) -> None:
    repository = SqlAlchemyClientRepository(clean_tables)
    created = await repository.insert(
        _input(
            "Padaria São José",
            contact_name="Maria",
            contact_phone="5541999887766",
            contact_email="maria@padaria.com.br",
            monthly_fee=Decimal("2500.00"),
            notes="indicação",
        ),
        slug="padaria-sao-jose",
    )

    found = await repository.get_by_id(created.id)
    assert found is not None
    assert found.name == "Padaria São José"
    assert found.monthly_fee == Decimal("2500.00")
    assert found.contact_phone == "5541999887766"
    assert found.created_at.tzinfo is not None

    by_slug = await repository.get_by_slug("padaria-sao-jose")
    assert by_slug is not None and by_slug.id == created.id


async def test_slug_exists_ignores_the_row_being_edited(
    clean_tables: AsyncSession,
) -> None:
    repository = SqlAlchemyClientRepository(clean_tables)
    created = await repository.insert(_input("Clinica Vida"), slug="clinica-vida")

    assert await repository.slug_exists("clinica-vida") is True
    assert await repository.slug_exists("clinica-vida", excluding=created.id) is False
    assert await repository.slug_exists("outro") is False


async def test_search_matches_name_slug_and_contact(clean_tables: AsyncSession) -> None:
    repository = SqlAlchemyClientRepository(clean_tables)
    await repository.insert(_input("Padaria São José", contact_name="Maria"), slug="padaria")
    await repository.insert(_input("Clinica Vida"), slug="clinica-vida")

    assert len(await repository.list_clients(search="padaria")) == 1
    assert len(await repository.list_clients(search="MARIA")) == 1
    assert len(await repository.list_clients(search="vida")) == 1
    assert len(await repository.list_clients(search="inexistente")) == 0


async def test_status_filter_and_update(clean_tables: AsyncSession) -> None:
    repository = SqlAlchemyClientRepository(clean_tables)
    created = await repository.insert(_input("Clinica Vida"), slug="clinica-vida")

    await repository.set_status(created.id, ClientStatus.ARCHIVED)
    assert await repository.list_clients(statuses=(ClientStatus.ACTIVE,)) == []
    archived = await repository.list_clients(statuses=(ClientStatus.ARCHIVED,))
    assert len(archived) == 1

    updated = await repository.update(
        created.id, _input("Clinica Vida Odonto", status=ClientStatus.ACTIVE)
    )
    assert updated is not None
    assert updated.name == "Clinica Vida Odonto"
    assert updated.slug == "clinica-vida"


async def test_updated_at_moves_on_write(clean_tables: AsyncSession) -> None:
    repository = SqlAlchemyClientRepository(clean_tables)
    created = await repository.insert(_input("Clinica Vida"), slug="clinica-vida")
    changed = await repository.set_status(created.id, ClientStatus.PAUSED)
    assert changed is not None
    assert changed.updated_at >= created.updated_at


async def test_public_api_creates_client_from_prospect(
    clean_tables: AsyncSession,
) -> None:
    api = ClientsApi(clean_tables)
    client = await api.create_client_from_prospect(name="Barbearia do Zé", phone="(41) 99988-7766")
    assert client.contact_phone == "5541999887766"

    again = await api.create_client_from_prospect(name="Barbearia do Zé")
    assert again.id == client.id
    assert [item.id for item in await api.list_active_clients()] == [client.id]
    assert await api.get_client(client.id) is not None


# ── Autenticação ──────────────────────────────────────────────


async def test_authenticate_accepts_and_rejects(clean_tables: AsyncSession) -> None:
    clean_tables.add(
        AppUser(
            email="ana@upsidemkt.com.br",
            name="Ana",
            password_hash=hash_password("senha-certa"),
            role=UserRole.TEAM,
            is_active=True,
        )
    )
    await clean_tables.commit()

    assert await authenticate(clean_tables, "ANA@upsidemkt.com.br", "senha-certa")
    assert await authenticate(clean_tables, "ana@upsidemkt.com.br", "errada") is None
    assert await authenticate(clean_tables, "ninguem@upsidemkt.com.br", "x") is None


async def test_authenticate_refuses_inactive_user(clean_tables: AsyncSession) -> None:
    clean_tables.add(
        AppUser(
            email="saiu@upsidemkt.com.br",
            name="Saiu",
            password_hash=hash_password("senha-certa"),
            role=UserRole.TEAM,
            is_active=False,
        )
    )
    await clean_tables.commit()
    assert await authenticate(clean_tables, "saiu@upsidemkt.com.br", "senha-certa") is None


async def test_seed_admin_is_idempotent(clean_tables: AsyncSession) -> None:
    first = await seed_admin()
    second = await seed_admin()
    assert "criado" in first
    assert "atualizado" in second


# ── Fluxos HTTP ───────────────────────────────────────────────


@pytest.fixture
async def client(clean_tables: AsyncSession) -> AsyncIterator[httpx.AsyncClient]:
    from app.main import app

    await seed_admin()
    transport = httpx.ASGITransport(app=app)
    # https, because outside dev the session cookie is set with Secure.
    async with httpx.AsyncClient(
        transport=transport, base_url="https://testserver", follow_redirects=False
    ) as http:
        yield http


async def _sign_in(http: httpx.AsyncClient) -> None:
    response = await http.post(
        "/login",
        data={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD},
    )
    assert response.status_code == 303, response.text
    assert response.headers["location"] == "/clients"


async def test_health_endpoint(client: httpx.AsyncClient) -> None:
    response = await client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_anonymous_visitor_is_sent_to_login(client: httpx.AsyncClient) -> None:
    assert (await client.get("/")).headers["location"] == "/login"

    protected = await client.get("/clients")
    assert protected.status_code == 303
    assert protected.headers["location"] == "/login?next=/clients"


async def test_htmx_request_without_session_gets_hx_redirect(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/clients", headers={"hx-request": "true"})
    assert response.status_code == 401
    assert response.headers["hx-redirect"] == "/login?next=/clients"


async def test_login_screen_is_in_portuguese(client: httpx.AsyncClient) -> None:
    body = (await client.get("/login")).text
    assert "Entrar" in body
    assert "Acesso da equipe Upside" in body


async def test_wrong_password_shows_a_single_message(client: httpx.AsyncClient) -> None:
    response = await client.post("/login", data={"email": ADMIN_EMAIL, "password": "errada"})
    assert response.status_code == 401
    assert "E-mail ou senha incorretos." in response.text


async def test_sign_in_then_create_and_edit_a_client(
    client: httpx.AsyncClient,
) -> None:
    await _sign_in(client)

    listing = await client.get("/clients")
    assert listing.status_code == 200
    assert "Comece cadastrando um cliente" in listing.text

    created = await client.post(
        "/clients",
        data={
            "name": "Padaria São José",
            "status": "active",
            "result_action_type": "lead",
            "contact_name": "Maria",
            "contact_email": "maria@padaria.com.br",
            "contact_phone": "(41) 99988-7766",
            "monthly_fee": "2.500,00",
            "notes": "",
        },
    )
    assert created.status_code == 303
    edit_url = created.headers["location"]

    form = await client.get(edit_url)
    assert form.status_code == 200
    assert "Padaria São José" in form.text
    assert "2500.00" in form.text

    listing = await client.get("/clients")
    assert "Padaria São José" in listing.text
    assert "R$ 2.500,00" in listing.text
    assert "(41) 99988-7766" in listing.text


async def test_invalid_form_comes_back_with_field_errors(
    client: httpx.AsyncClient,
) -> None:
    await _sign_in(client)
    response = await client.post(
        "/clients",
        data={
            "name": " ",
            "status": "active",
            "result_action_type": "lead",
            "contact_email": "nao-e-email",
            "monthly_fee": "",
        },
    )
    assert response.status_code == 422
    assert "Informe o nome do cliente." in response.text
    assert "E-mail inválido." in response.text


async def test_filters_return_only_the_table_fragment(
    client: httpx.AsyncClient,
) -> None:
    await _sign_in(client)
    await client.post(
        "/clients",
        data={"name": "Clinica Vida", "status": "active", "result_action_type": "lead"},
    )

    fragment = await client.get("/clients", params={"q": "clinica"}, headers={"hx-request": "true"})
    assert fragment.status_code == 200
    assert "<!doctype html>" not in fragment.text.lower()
    assert "Clinica Vida" in fragment.text

    empty = await client.get("/clients", params={"q": "nao-existe"}, headers={"hx-request": "true"})
    assert "Nenhum cliente nesse filtro" in empty.text


async def test_status_change_re_renders_the_list(client: httpx.AsyncClient) -> None:
    await _sign_in(client)
    created = await client.post(
        "/clients",
        data={"name": "Clinica Vida", "status": "active", "result_action_type": "lead"},
    )
    client_id = created.headers["location"].split("/")[2]

    response = await client.post(
        f"/clients/{client_id}/status",
        data={"status": "archived", "q": "", "filter_status": ""},
        headers={"hx-request": "true"},
    )
    assert response.status_code == 200
    # Archived clients leave the default view.
    assert "Nenhum cliente nesse filtro" in response.text or "0 cliente" in response.text

    archived = await client.get("/clients", params={"status": "archived"})
    assert "Clinica Vida" in archived.text


async def test_unknown_client_renders_the_not_found_screen(
    client: httpx.AsyncClient,
) -> None:
    await _sign_in(client)
    response = await client.get("/clients/0f1b4a3c-0000-4000-8000-000000000000/edit")
    assert response.status_code == 404
    assert "Cliente não encontrado." in response.text


async def test_team_member_cannot_reach_the_form(
    client: httpx.AsyncClient, clean_tables: AsyncSession
) -> None:
    clean_tables.add(
        AppUser(
            email="equipe@upsidemkt.com.br",
            name="Equipe",
            password_hash=hash_password("senha-equipe"),
            role=UserRole.TEAM,
            is_active=True,
        )
    )
    await clean_tables.commit()

    await client.post(
        "/login", data={"email": "equipe@upsidemkt.com.br", "password": "senha-equipe"}
    )
    listing = await client.get("/clients")
    assert listing.status_code == 200
    assert "Cadastrar cliente" not in listing.text

    forbidden = await client.get("/clients/new")
    assert forbidden.status_code == 403
    assert "Acesso restrito" in forbidden.text


async def test_logout_clears_the_session(client: httpx.AsyncClient) -> None:
    await _sign_in(client)
    response = await client.post("/logout")
    assert response.status_code == 303
    assert (await client.get("/clients")).status_code == 303


async def test_service_layer_create_persists(clean_tables: AsyncSession) -> None:
    service = ClientsService(SqlAlchemyClientRepository(clean_tables))
    created = await service.create(_input("Padaria São José"))
    await clean_tables.commit()

    reloaded = await ClientsApi(clean_tables).get_client(created.id)
    assert reloaded is not None
    assert reloaded.slug == "padaria-sao-jose"
