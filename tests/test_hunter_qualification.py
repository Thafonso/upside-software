"""Qualification use cases: site checks, filters, conversion to client."""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from uuid import UUID, uuid4

import pytest

from app.hunter.domain import Prospect, ProspectFilters, ProspectStatus, SiteCheck
from app.hunter.scoring import WebsiteType
from app.hunter.service import HunterService
from app.shared import events
from tests.factories import make_prospect
from tests.test_hunter_service import NOW, FakeHunterRepository


@pytest.fixture
def repository() -> FakeHunterRepository:
    return FakeHunterRepository()


@pytest.fixture
def service(repository: FakeHunterRepository) -> HunterService:
    return HunterService(repository, daily_limit=20)


def _stored(repository: FakeHunterRepository, **overrides: object) -> Prospect:
    return repository.add_prospect(make_prospect(**overrides))


# ── virar cliente ─────────────────────────────────────────────


class FakeClient:
    def __init__(self, client_id: UUID) -> None:
        self.id = client_id


class FakeClientDirectory:
    """Implements app.hunter.ports.ClientDirectory."""

    def __init__(self) -> None:
        self.created: list[dict[str, str | None]] = []
        self.next_id = uuid4()

    async def create_client_from_prospect(
        self,
        *,
        name: str,
        phone: str | None = None,
        email: str | None = None,
        notes: str | None = None,
    ) -> FakeClient:
        self.created.append({"name": name, "phone": phone, "email": email, "notes": notes})
        return FakeClient(self.next_id)


async def test_converting_creates_a_client_and_records_it(
    service: HunterService, repository: FakeHunterRepository
) -> None:
    events.reset()
    seen: list[events.ProspectConverted] = []
    events.subscribe(events.ProspectConverted, seen.append)

    prospect = _stored(
        repository,
        name="Padaria Central",
        whatsapp="5541999887766",
        emails=("contato@padaria.com.br",),
    )
    directory = FakeClientDirectory()

    converted = await service.convert_to_client(prospect.id, directory)

    assert converted is not None
    assert converted.client_id == directory.next_id
    assert converted.status == ProspectStatus.WON
    assert directory.created == [
        {
            "name": "Padaria Central",
            "phone": "5541999887766",
            "email": "contato@padaria.com.br",
            "notes": f"Veio do Hunter. {prospect.maps_link}",
        }
    ]
    assert [event.client_id for event in seen] == [directory.next_id]
    events.reset()


async def test_converting_twice_does_not_create_a_second_client(
    service: HunterService, repository: FakeHunterRepository
) -> None:
    prospect = _stored(repository)
    directory = FakeClientDirectory()

    first = await service.convert_to_client(prospect.id, directory)
    again = await service.convert_to_client(prospect.id, directory)

    assert first is not None
    assert again is not None
    assert first.client_id == again.client_id
    assert len(directory.created) == 1


async def test_converting_an_unknown_prospect_does_nothing(
    service: HunterService,
) -> None:
    directory = FakeClientDirectory()
    assert await service.convert_to_client(uuid4(), directory) is None
    assert directory.created == []


async def test_a_landline_prospect_is_converted_with_its_phone(
    service: HunterService, repository: FakeHunterRepository
) -> None:
    prospect = _stored(repository, phone="(41) 3333-4444", whatsapp=None)
    directory = FakeClientDirectory()
    await service.convert_to_client(prospect.id, directory)
    assert directory.created[0]["phone"] == "(41) 3333-4444"


# ── checagem dos sites ────────────────────────────────────────


class FakeInspector:
    """Implements app.hunter.ports.WebsiteInspector."""

    def __init__(self, results: dict[str, SiteCheck]) -> None:
        self._results = results
        self.visited: list[str] = []

    async def inspect_many(self, urls: Sequence[str]) -> dict[str, SiteCheck]:
        self.visited = list(urls)
        return {url: self._results[url] for url in urls if url in self._results}


async def test_a_site_without_a_pixel_earns_its_points(
    service: HunterService, repository: FakeHunterRepository
) -> None:
    search_id = uuid4()
    prospect = _stored(
        repository,
        search_id=search_id,
        website="https://padaria.com.br",
        website_type=WebsiteType.SITE,
        review_count=100,
        review_rating=Decimal("4.5"),
        score=30,
    )
    inspector = FakeInspector(
        {
            "https://padaria.com.br": SiteCheck(
                reachable=True,
                has_meta_pixel=False,
                has_instagram=True,
                has_whatsapp_link=False,
            )
        }
    )

    assert await service.check_sites(search_id, inspector) == 1

    stored = repository.rows[prospect.id]
    assert stored.has_meta_pixel is False
    assert stored.has_instagram is True
    assert stored.site_checked_at is not None
    # 20 avaliações + 10 nota + 20 site sem pixel
    assert stored.score == 50


async def test_a_site_with_a_pixel_does_not_earn_them(
    service: HunterService, repository: FakeHunterRepository
) -> None:
    search_id = uuid4()
    prospect = _stored(
        repository,
        search_id=search_id,
        website="https://padaria.com.br",
        website_type=WebsiteType.SITE,
        review_count=100,
        review_rating=Decimal("4.5"),
    )
    inspector = FakeInspector(
        {
            "https://padaria.com.br": SiteCheck(
                reachable=True,
                has_meta_pixel=True,
                has_instagram=False,
                has_whatsapp_link=False,
            )
        }
    )
    await service.check_sites(search_id, inspector)
    assert repository.rows[prospect.id].score == 30


async def test_an_unreachable_site_is_recorded_without_guessing(
    service: HunterService, repository: FakeHunterRepository
) -> None:
    search_id = uuid4()
    prospect = _stored(
        repository,
        search_id=search_id,
        website="https://fora-do-ar.com.br",
        website_type=WebsiteType.SITE,
        review_count=0,
        review_rating=None,
    )
    inspector = FakeInspector({"https://fora-do-ar.com.br": SiteCheck.unreachable()})

    await service.check_sites(search_id, inspector)

    stored = repository.rows[prospect.id]
    assert stored.site_unreachable is True
    assert stored.has_meta_pixel is None
    assert stored.score == 0


async def test_only_unvisited_own_sites_are_checked(
    service: HunterService, repository: FakeHunterRepository
) -> None:
    search_id = uuid4()
    _stored(repository, search_id=search_id, website_type=WebsiteType.NONE)
    _stored(
        repository,
        search_id=search_id,
        website="https://instagram.com/padaria",
        website_type=WebsiteType.SOCIAL,
    )
    _stored(
        repository,
        search_id=search_id,
        website="https://ja-visitado.com.br",
        website_type=WebsiteType.SITE,
        site_checked_at=NOW,
    )

    inspector = FakeInspector({})
    assert await service.check_sites(search_id, inspector) == 0
    assert inspector.visited == []


async def test_check_sites_with_nothing_to_do(service: HunterService) -> None:
    assert await service.check_sites(uuid4(), FakeInspector({})) == 0


# ── lista, filtros e ações ────────────────────────────────────


async def test_filters_narrow_the_list(
    service: HunterService, repository: FakeHunterRepository
) -> None:
    search_id = uuid4()
    _stored(
        repository,
        search_id=search_id,
        name="Sem site",
        score=70,
        website_type=WebsiteType.NONE,
    )
    _stored(
        repository,
        search_id=search_id,
        name="So Instagram",
        score=50,
        website_type=WebsiteType.SOCIAL,
    )
    _stored(
        repository,
        search_id=search_id,
        name="Com WhatsApp",
        score=30,
        website_type=WebsiteType.SITE,
        whatsapp="5541999887766",
    )

    ordered = await service.search_prospects(ProspectFilters(search_id=search_id))
    assert [item.name for item in ordered] == ["Sem site", "So Instagram", "Com WhatsApp"]

    async def names(filters: ProspectFilters) -> list[str]:
        return [item.name for item in await service.search_prospects(filters)]

    assert await names(ProspectFilters(no_website=True)) == ["Sem site"]
    assert await names(ProspectFilters(social_only=True)) == ["So Instagram"]
    assert await names(ProspectFilters(has_whatsapp=True)) == ["Com WhatsApp"]
    assert await names(ProspectFilters(min_score=60)) == ["Sem site"]


async def test_do_not_contact_removes_a_prospect_from_everything(
    service: HunterService, repository: FakeHunterRepository
) -> None:
    prospect = _stored(repository, name="Padaria Central", whatsapp="5541999887766")
    assert await service.count_prospects(ProspectFilters()) == 1

    await service.set_do_not_contact(prospect.id, flag=True)

    assert await service.count_prospects(ProspectFilters()) == 0
    assert await service.search_prospects(ProspectFilters()) == []
    assert await service.phone_list(ProspectFilters()) == []
    assert "Padaria Central" not in await service.export_csv(ProspectFilters())


async def test_status_can_be_changed(
    service: HunterService, repository: FakeHunterRepository
) -> None:
    prospect = _stored(repository)
    updated = await service.set_prospect_status(prospect.id, ProspectStatus.CONTACTED)
    assert updated is not None
    assert updated.status == ProspectStatus.CONTACTED
    assert await service.set_prospect_status(uuid4(), ProspectStatus.WON) is None
