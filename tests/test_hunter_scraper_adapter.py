"""The MazyOS adapter against recorded responses from the real service."""

from __future__ import annotations

from decimal import Decimal

import httpx
import pytest
import respx

from app.hunter.adapters.mazyos_scraper import (
    MAPS_LINK_MAX,
    MazyosScraperProvider,
    parse_emails,
    to_found_company,
)
from app.hunter.domain import ScrapeStatus, SearchRequest
from app.hunter.ports import ScraperError

BASE_URL = "http://scraper:8080"

# Copied from a real answer of the vendored scraper.
PLACE_ROW = {
    "title": "Café du Centre",
    "address": "R. Francisco Rocha, 612 - Batel, Curitiba - PR, 80420-130",
    "latitude": -25.440507,
    "longitude": -49.2901,
    "link": "https://www.google.com/maps/place/Cafe+du+Centre/data=!4m7!3m6",
    "category": "Cafeteria",
    "phone": "(41) 3044-5566",
    "website": "",
    "review_rating": 4.6,
    "review_count": 2275,
    "emails": "",
    "borough": "Batel",
    "city": "Curitiba",
}

REQUEST = SearchRequest(
    name="Dentista · Batel",
    queries=("dentista em Batel, Curitiba",),
    depth=5,
    max_time_seconds=1200,
    fetch_emails=False,
)


@pytest.fixture
async def provider():
    async with httpx.AsyncClient(base_url=BASE_URL) as client:
        yield MazyosScraperProvider(client)


# ── e-mails ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", ()),
        (None, ()),
        ("   ", ()),
        ("a@x.com", ("a@x.com",)),
        ("A@X.com, b@y.com", ("a@x.com", "b@y.com")),
        ("a@x.com,a@x.com", ("a@x.com",)),
        ("a@x.com; b@y.com", ("a@x.com", "b@y.com")),
        ("nao-e-email, b@y.com", ("b@y.com",)),
    ],
)
def test_parse_emails(raw: str | None, expected: tuple[str, ...]) -> None:
    assert parse_emails(raw) == expected


# ── conversão de uma linha ────────────────────────────────────


def test_place_row_becomes_a_domain_company() -> None:
    company = to_found_company(PLACE_ROW)
    assert company is not None
    assert company.name == "Café du Centre"
    assert company.category == "Cafeteria"
    assert company.phone == "(41) 3044-5566"
    assert company.website is None  # the scraper sends "" for "no site"
    assert company.borough == "Batel"
    assert company.review_rating == Decimal("4.6")
    assert company.review_count == 2275
    assert company.latitude == Decimal("-25.440507")
    assert company.emails == ()


def test_row_without_name_or_link_is_dropped() -> None:
    assert to_found_company({**PLACE_ROW, "title": ""}) is None
    assert to_found_company({**PLACE_ROW, "link": ""}) is None
    assert to_found_company({}) is None


def test_missing_rating_and_count_default_safely() -> None:
    company = to_found_company({**PLACE_ROW, "review_rating": 0, "review_count": 0, "phone": ""})
    assert company is not None
    assert company.review_rating is None
    assert company.review_count == 0
    assert company.phone is None


def test_long_link_is_capped_for_the_unique_index() -> None:
    long_link = "https://maps.google.com/?q=" + "a" * 3000
    company = to_found_company({**PLACE_ROW, "link": long_link})
    assert company is not None
    assert len(company.maps_link) == MAPS_LINK_MAX


# ── start ─────────────────────────────────────────────────────


@respx.mock
async def test_start_sends_the_contract_the_scraper_expects(
    provider: MazyosScraperProvider,
) -> None:
    route = respx.post(f"{BASE_URL}/api/v1/jobs").mock(
        # The real service answers 201, not 200.
        return_value=httpx.Response(201, json={"id": "job-1"})
    )

    assert await provider.start(REQUEST) == "job-1"

    sent = route.calls.last.request
    import json

    body = json.loads(sent.content)
    assert body == {
        "name": "Dentista · Batel",
        "keywords": ["dentista em Batel, Curitiba"],
        "lang": "pt",
        "depth": 5,
        "email": False,
        "max_time": 1200,
    }


@respx.mock
async def test_start_accepts_a_plain_200(provider: MazyosScraperProvider) -> None:
    respx.post(f"{BASE_URL}/api/v1/jobs").mock(
        return_value=httpx.Response(200, json={"id": "job-2"})
    )
    assert await provider.start(REQUEST) == "job-2"


@respx.mock
async def test_start_reports_a_validation_refusal(
    provider: MazyosScraperProvider,
) -> None:
    respx.post(f"{BASE_URL}/api/v1/jobs").mock(
        return_value=httpx.Response(422, json={"code": 422, "message": "missing depth"})
    )
    with pytest.raises(ScraperError, match="missing depth"):
        await provider.start(REQUEST)


@respx.mock
async def test_start_without_an_id_is_an_error(
    provider: MazyosScraperProvider,
) -> None:
    respx.post(f"{BASE_URL}/api/v1/jobs").mock(return_value=httpx.Response(201, json={}))
    with pytest.raises(ScraperError, match="id"):
        await provider.start(REQUEST)


@respx.mock
async def test_network_failure_becomes_a_scraper_error(
    provider: MazyosScraperProvider,
) -> None:
    respx.post(f"{BASE_URL}/api/v1/jobs").mock(side_effect=httpx.ConnectError("connection refused"))
    with pytest.raises(ScraperError, match="falar com o scraper"):
        await provider.start(REQUEST)


# ── status ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("pending", ScrapeStatus.PENDING),
        ("working", ScrapeStatus.RUNNING),
        ("ok", ScrapeStatus.DONE),
        ("failed", ScrapeStatus.FAILED),
    ],
)
@respx.mock
async def test_status_reads_the_pascal_case_payload(
    provider: MazyosScraperProvider, raw: str, expected: ScrapeStatus
) -> None:
    respx.get(f"{BASE_URL}/api/v1/jobs/job-1").mock(
        return_value=httpx.Response(
            200,
            json={
                "ID": "job-1",
                "Name": "Dentista · Batel",
                "Date": "2026-11-21T02:04:33Z",
                "Status": raw,
                "Data": {"keywords": ["dentista em Batel, Curitiba"]},
            },
        )
    )
    assert await provider.status("job-1") == expected


@respx.mock
async def test_unknown_status_is_refused(provider: MazyosScraperProvider) -> None:
    respx.get(f"{BASE_URL}/api/v1/jobs/job-1").mock(
        return_value=httpx.Response(200, json={"Status": "quem-sabe"})
    )
    with pytest.raises(ScraperError, match="desconhecida"):
        await provider.status("job-1")


@respx.mock
async def test_missing_job_is_reported(provider: MazyosScraperProvider) -> None:
    respx.get(f"{BASE_URL}/api/v1/jobs/sumiu").mock(return_value=httpx.Response(404))
    with pytest.raises(ScraperError, match="não existe mais"):
        await provider.status("sumiu")


# ── resultados ────────────────────────────────────────────────


@respx.mock
async def test_results_convert_every_usable_row(
    provider: MazyosScraperProvider,
) -> None:
    respx.get(f"{BASE_URL}/api/v1/jobs/job-1/places").mock(
        return_value=httpx.Response(
            200,
            json=[
                PLACE_ROW,
                {
                    **PLACE_ROW,
                    "title": "Padaria Central",
                    "link": "https://maps.google.com/padaria",
                    "website": "https://padaria.com.br",
                    "emails": "contato@padaria.com.br, VENDAS@padaria.com.br",
                },
                {**PLACE_ROW, "title": "", "link": ""},
            ],
        )
    )

    companies = await provider.results("job-1")
    assert [company.name for company in companies] == [
        "Café du Centre",
        "Padaria Central",
    ]
    assert companies[1].website == "https://padaria.com.br"
    assert companies[1].emails == ("contato@padaria.com.br", "vendas@padaria.com.br")


@respx.mock
async def test_results_tolerate_an_empty_answer(
    provider: MazyosScraperProvider,
) -> None:
    respx.get(f"{BASE_URL}/api/v1/jobs/job-1/places").mock(
        return_value=httpx.Response(200, json=[])
    )
    assert await provider.results("job-1") == []


@respx.mock
async def test_results_refuse_an_unexpected_shape(
    provider: MazyosScraperProvider,
) -> None:
    respx.get(f"{BASE_URL}/api/v1/jobs/job-1/places").mock(
        return_value=httpx.Response(200, json={"places": []})
    )
    with pytest.raises(ScraperError, match="formato inesperado"):
        await provider.results("job-1")


@respx.mock
async def test_results_error_status_is_reported(
    provider: MazyosScraperProvider,
) -> None:
    respx.get(f"{BASE_URL}/api/v1/jobs/job-1/places").mock(
        return_value=httpx.Response(500, text="boom")
    )
    with pytest.raises(ScraperError, match="500"):
        await provider.results("job-1")


# ── discard ───────────────────────────────────────────────────


@respx.mock
async def test_discard_deletes_the_job(provider: MazyosScraperProvider) -> None:
    route = respx.delete(f"{BASE_URL}/api/v1/jobs/job-1").mock(return_value=httpx.Response(200))
    await provider.discard("job-1")
    assert route.called


@respx.mock
async def test_discard_of_a_gone_job_is_not_an_error(
    provider: MazyosScraperProvider,
) -> None:
    respx.delete(f"{BASE_URL}/api/v1/jobs/job-1").mock(return_value=httpx.Response(404))
    await provider.discard("job-1")
