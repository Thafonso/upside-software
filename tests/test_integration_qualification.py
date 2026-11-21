"""Qualification against a real Postgres: signals in SQL, screens, exports."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator, Sequence
from contextlib import asynccontextmanager
from decimal import Decimal
from uuid import UUID

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.repository import ClientRow
from app.hunter import jobs as hunter_jobs
from app.hunter.domain import (
    FoundCompany,
    ProspectFilters,
    ProspectStatus,
    SiteCheck,
    build_search_input,
)
from app.hunter.repository import ProspectRow, SqlAlchemyHunterRepository
from app.hunter.scoring import WebsiteType
from app.hunter.service import HunterService
from app.shared.db import get_sessionmaker
from app.shared.jobs import queue
from app.shared.jobs.runner import run_next
from app.shared.seed import seed_admin
from tests.conftest import ADMIN_EMAIL, ADMIN_PASSWORD

pytestmark = pytest.mark.integration


def _repository(session: AsyncSession) -> SqlAlchemyHunterRepository:
    return SqlAlchemyHunterRepository(session)


def _service(session: AsyncSession) -> HunterService:
    return HunterService(_repository(session), daily_limit=20)


def _company(name: str, link: str, **overrides: object) -> FoundCompany:
    base: dict[str, object] = {"name": name, "maps_link": link}
    return FoundCompany(**{**base, **overrides})  # pyright: ignore[reportArgumentType]


async def _search(session: AsyncSession) -> UUID:
    data = build_search_input(niches="padaria", city="Curitiba", neighborhood="Batel")
    search = await _service(session).create_search(data)
    return search.id


# ── sinais escritos na gravação ───────────────────────────────


async def test_signals_and_score_are_written_with_the_row(
    clean_tables: AsyncSession,
) -> None:
    search_id = await _search(clean_tables)
    await _repository(clean_tables).upsert_prospects(
        search_id,
        [
            _company(
                "Padaria Central",
                "https://maps.google.com/padaria",
                phone="(41) 99988-7766",
                website=None,
                emails=("contato@padaria.com.br",),
                review_count=120,
                review_rating=Decimal("4.8"),
            )
        ],
    )
    await clean_tables.commit()

    prospect = (await _service(clean_tables).search_prospects(ProspectFilters()))[0]
    assert prospect.whatsapp == "5541999887766"
    assert prospect.website_type == WebsiteType.NONE
    # 20 avaliações + 10 nota + 25 sem site + 15 whatsapp + 10 e-mail
    assert prospect.score == 80
    assert prospect.score_details["no_website"] == 25
    assert "Sem site +25" in prospect.score_explanation


async def test_a_social_profile_is_not_a_site(clean_tables: AsyncSession) -> None:
    search_id = await _search(clean_tables)
    await _repository(clean_tables).upsert_prospects(
        search_id,
        [
            _company(
                "Café do Zé",
                "https://maps.google.com/cafe",
                website="https://instagram.com/cafedoze",
                phone="(41) 3333-4444",
                review_count=10,
            )
        ],
    )
    await clean_tables.commit()

    prospect = (await _service(clean_tables).search_prospects(ProspectFilters()))[0]
    assert prospect.website_type == WebsiteType.SOCIAL
    assert prospect.whatsapp is None
    assert prospect.score == 20


async def test_refinding_a_place_clears_what_we_knew_about_its_site(
    clean_tables: AsyncSession,
) -> None:
    """The site may have changed, so the old visit no longer counts."""
    search_id = await _search(clean_tables)
    link = "https://maps.google.com/padaria"
    repository = _repository(clean_tables)

    await repository.upsert_prospects(
        search_id,
        [_company("Padaria", link, website="https://padaria.com.br", review_count=50)],
    )
    await clean_tables.commit()

    prospect = (await repository.search_prospects(ProspectFilters()))[0]
    await repository.apply_site_check(
        prospect.id,
        has_meta_pixel=False,
        has_instagram=True,
        has_whatsapp_link=False,
        unreachable=False,
    )
    await clean_tables.commit()
    assert (await repository.get_prospect(prospect.id)).score == 40  # type: ignore[union-attr]

    await repository.upsert_prospects(
        search_id,
        [_company("Padaria", link, website="https://padaria.com.br", review_count=50)],
    )
    await clean_tables.commit()

    refound = await repository.get_prospect(prospect.id)
    assert refound is not None
    assert refound.has_meta_pixel is None
    assert refound.site_checked_at is None
    assert refound.score == 20  # only the reviews, until the site is visited again


# ── filtros em SQL ────────────────────────────────────────────


async def _three_prospects(session: AsyncSession) -> UUID:
    search_id = await _search(session)
    await _repository(session).upsert_prospects(
        search_id,
        [
            _company(
                "Sem site",
                "https://maps.google.com/1",
                phone="(41) 99988-7766",
                review_count=100,
                review_rating=Decimal("4.9"),
            ),
            _company(
                "So Instagram",
                "https://maps.google.com/2",
                website="https://instagram.com/loja",
                review_count=40,
            ),
            _company(
                "Com site",
                "https://maps.google.com/3",
                website="https://loja.com.br",
                emails=("contato@loja.com.br",),
                review_count=5,
            ),
        ],
    )
    await session.commit()
    return search_id


async def test_the_list_is_ordered_by_score(clean_tables: AsyncSession) -> None:
    await _three_prospects(clean_tables)
    found = await _service(clean_tables).search_prospects(ProspectFilters())
    assert [item.name for item in found] == ["Sem site", "So Instagram", "Com site"]
    # 70 = 20 avaliacoes + 10 nota + 25 sem site + 15 whatsapp
    # 40 = 20 avaliacoes + 20 so rede social
    # 10 = e-mail; o site ainda nao foi visitado
    assert [item.score for item in found] == [70, 40, 10]


@pytest.mark.parametrize(
    ("filters", "expected"),
    [
        (ProspectFilters(no_website=True), ["Sem site"]),
        (ProspectFilters(social_only=True), ["So Instagram"]),
        (ProspectFilters(has_whatsapp=True), ["Sem site"]),
        (ProspectFilters(has_email=True), ["Com site"]),
        (ProspectFilters(min_score=20), ["Sem site", "So Instagram"]),
        (ProspectFilters(min_score=100), []),
    ],
)
async def test_each_filter_narrows_the_list(
    clean_tables: AsyncSession, filters: ProspectFilters, expected: list[str]
) -> None:
    await _three_prospects(clean_tables)
    found = await _service(clean_tables).search_prospects(filters)
    assert [item.name for item in found] == expected
    assert await _service(clean_tables).count_prospects(filters) == len(expected)


async def test_filtering_by_search_and_status(clean_tables: AsyncSession) -> None:
    search_id = await _three_prospects(clean_tables)
    service = _service(clean_tables)

    assert len(await service.search_prospects(ProspectFilters(search_id=search_id))) == 3

    first = (await service.search_prospects(ProspectFilters()))[0]
    await service.set_prospect_status(first.id, ProspectStatus.CONTACTED)
    await clean_tables.commit()

    contacted = await service.search_prospects(ProspectFilters(status=ProspectStatus.CONTACTED))
    assert [item.name for item in contacted] == ["Sem site"]


async def test_do_not_contact_hides_a_prospect_for_good(
    clean_tables: AsyncSession,
) -> None:
    await _three_prospects(clean_tables)
    service = _service(clean_tables)
    first = (await service.search_prospects(ProspectFilters()))[0]

    await service.set_do_not_contact(first.id, flag=True)
    await clean_tables.commit()

    remaining = await service.search_prospects(ProspectFilters())
    assert "Sem site" not in [item.name for item in remaining]
    assert "Sem site" not in await service.export_csv(ProspectFilters())
    assert "5541999887766" not in await service.phone_list(ProspectFilters())
    # The row is still there, it is simply never offered again.
    assert await _repository(clean_tables).get_prospect(first.id) is not None


# ── virar cliente, de verdade ─────────────────────────────────


async def test_converting_creates_a_real_client_row(
    clean_tables: AsyncSession,
) -> None:
    from app.clients import ClientsApi

    await _three_prospects(clean_tables)
    service = _service(clean_tables)
    prospect = (await service.search_prospects(ProspectFilters()))[0]

    converted = await service.convert_to_client(prospect.id, ClientsApi(clean_tables))
    await clean_tables.commit()

    assert converted is not None
    assert converted.status == ProspectStatus.WON

    clients = (await clean_tables.execute(select(ClientRow))).scalars().all()
    assert len(clients) == 1
    assert clients[0].name == "Sem site"
    assert clients[0].contact_phone == "5541999887766"
    assert clients[0].id == converted.client_id


# ── check_sites pela fila ─────────────────────────────────────


@pytest.fixture
def stub_inspector(monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, SiteCheck]]:
    results: dict[str, SiteCheck] = {}

    class StubInspector:
        async def inspect_many(self, urls: Sequence[str]) -> dict[str, SiteCheck]:
            return {url: results[url] for url in urls if url in results}

    @asynccontextmanager
    async def fake_inspector() -> AsyncIterator[StubInspector]:
        yield StubInspector()

    monkeypatch.setattr(hunter_jobs, "website_inspector", fake_inspector)
    yield results


async def test_check_sites_job_rescoring(
    clean_tables: AsyncSession, stub_inspector: dict[str, SiteCheck]
) -> None:
    search_id = await _three_prospects(clean_tables)
    stub_inspector["https://loja.com.br"] = SiteCheck(
        reachable=True,
        has_meta_pixel=False,
        has_instagram=True,
        has_whatsapp_link=True,
    )

    await queue.enqueue(clean_tables, hunter_jobs.CHECK_SITES, {"search_id": str(search_id)})
    await clean_tables.commit()
    await run_next(get_sessionmaker())

    async with get_sessionmaker()() as session:
        rows = (await session.execute(select(ProspectRow))).scalars().all()
    by_name = {row.name: row for row in rows}

    checked = by_name["Com site"]
    assert checked.has_meta_pixel is False
    assert checked.has_instagram is True
    assert checked.site_checked_at is not None
    assert checked.score == 30  # 10 e-mail + 20 site sem pixel

    # Only own sites are visited.
    assert by_name["So Instagram"].site_checked_at is None
    assert by_name["Sem site"].site_checked_at is None


async def test_check_sites_job_is_idempotent(
    clean_tables: AsyncSession, stub_inspector: dict[str, SiteCheck]
) -> None:
    search_id = await _three_prospects(clean_tables)
    stub_inspector["https://loja.com.br"] = SiteCheck(
        reachable=True, has_meta_pixel=True, has_instagram=False, has_whatsapp_link=False
    )

    for _ in range(2):
        async with get_sessionmaker()() as session:
            await queue.enqueue(session, hunter_jobs.CHECK_SITES, {"search_id": str(search_id)})
            await session.commit()
        await run_next(get_sessionmaker())

    async with get_sessionmaker()() as session:
        rows = (await session.execute(select(ProspectRow))).scalars().all()
    assert sum(1 for row in rows if row.site_checked_at is not None) == 1


# ── telas ─────────────────────────────────────────────────────


@pytest.fixture
async def client(clean_tables: AsyncSession) -> AsyncIterator[httpx.AsyncClient]:
    from app.main import app

    await seed_admin()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="https://testserver", follow_redirects=False
    ) as http:
        response = await http.post(
            "/login", data={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
        )
        assert response.status_code == 303
        yield http


async def test_the_working_list_shows_score_and_signals(
    client: httpx.AsyncClient, clean_tables: AsyncSession
) -> None:
    await _three_prospects(clean_tables)

    response = await client.get("/hunter")
    assert response.status_code == 200
    assert "Empresas encontradas" in response.text
    assert "Sem site" in response.text
    assert "Como a nota é calculada" in response.text


async def test_filters_return_only_the_fragment(
    client: httpx.AsyncClient, clean_tables: AsyncSession
) -> None:
    await _three_prospects(clean_tables)

    fragment = await client.get(
        "/hunter", params={"no_website": "1"}, headers={"hx-request": "true"}
    )
    assert "<!doctype html>" not in fragment.text.lower()
    assert "Sem site" in fragment.text
    assert "So Instagram" not in fragment.text

    empty = await client.get("/hunter", params={"min_score": "100"}, headers={"hx-request": "true"})
    assert "Nenhuma empresa nesse filtro" in empty.text


async def test_changing_a_status_from_the_list(
    client: httpx.AsyncClient, clean_tables: AsyncSession
) -> None:
    await _three_prospects(clean_tables)
    prospect = (await _service(clean_tables).search_prospects(ProspectFilters()))[0]

    response = await client.post(
        f"/hunter/prospects/{prospect.id}/status",
        data={"status": "meeting"},
        headers={"hx-request": "true"},
    )
    assert response.status_code == 200

    async with get_sessionmaker()() as session:
        reloaded = await _repository(session).get_prospect(prospect.id)
    assert reloaded is not None
    assert reloaded.status == ProspectStatus.MEETING


async def test_marking_do_not_contact_from_the_list(
    client: httpx.AsyncClient, clean_tables: AsyncSession
) -> None:
    await _three_prospects(clean_tables)
    prospect = (await _service(clean_tables).search_prospects(ProspectFilters()))[0]

    response = await client.post(
        f"/hunter/prospects/{prospect.id}/do-not-contact",
        data={},
        headers={"hx-request": "true"},
    )
    assert response.status_code == 200
    assert prospect.name not in response.text


async def test_marking_a_prospect_as_won_creates_the_client(
    client: httpx.AsyncClient, clean_tables: AsyncSession
) -> None:
    """Choosing "Virou cliente" in the list is the conversion itself."""
    await _three_prospects(clean_tables)
    prospect = (await _service(clean_tables).search_prospects(ProspectFilters()))[0]

    response = await client.post(
        f"/hunter/prospects/{prospect.id}/status",
        data={"status": "won"},
        headers={"hx-request": "true"},
    )
    assert response.status_code == 200

    async with get_sessionmaker()() as session:
        reloaded = await _repository(session).get_prospect(prospect.id)
    assert reloaded is not None
    assert reloaded.status == ProspectStatus.WON
    assert reloaded.client_id is not None

    clients_screen = await client.get("/clients")
    assert prospect.name in clients_screen.text


async def test_marking_as_won_twice_keeps_one_client(
    client: httpx.AsyncClient, clean_tables: AsyncSession
) -> None:
    await _three_prospects(clean_tables)
    prospect = (await _service(clean_tables).search_prospects(ProspectFilters()))[0]

    for _ in range(2):
        await client.post(
            f"/hunter/prospects/{prospect.id}/status",
            data={"status": "won"},
            headers={"hx-request": "true"},
        )

    async with get_sessionmaker()() as session:
        clients = (await session.execute(select(ClientRow))).scalars().all()
    assert len(clients) == 1


async def test_csv_download_is_ready_for_excel(
    client: httpx.AsyncClient, clean_tables: AsyncSession
) -> None:
    await _three_prospects(clean_tables)

    response = await client.get("/hunter/prospects.csv")
    assert response.status_code == 200
    assert "text/csv" in response.headers["content-type"]
    assert "attachment" in response.headers["content-disposition"]

    body = response.content.decode("utf-8")
    assert body.startswith("﻿")
    assert "Nome;Nota;" in body
    assert "Sem site;70;" in body


async def test_csv_follows_the_filters(
    client: httpx.AsyncClient, clean_tables: AsyncSession
) -> None:
    await _three_prospects(clean_tables)
    response = await client.get("/hunter/prospects.csv", params={"social_only": "1"})
    body = response.content.decode("utf-8")
    assert "So Instagram" in body
    assert "Sem site" not in body


async def test_phone_list_is_plain_text(
    client: httpx.AsyncClient, clean_tables: AsyncSession
) -> None:
    await _three_prospects(clean_tables)
    response = await client.get("/hunter/prospects/phones")
    assert response.status_code == 200
    assert "5541999887766" in response.text


async def test_the_searches_screen_moved_but_still_works(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/hunter/searches")
    assert response.status_code == 200
    assert "Nenhuma busca ainda" in response.text
