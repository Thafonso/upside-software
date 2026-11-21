"""Visiting a prospect's site, against recorded responses."""

from __future__ import annotations

import httpx
import pytest
import respx

from app.hunter.adapters.site_checker import HttpWebsiteInspector, read_page

PIXEL_PAGE = """
<html><head>
<script>!function(f,b,e,v,n,t,s){}(window,document,'script',
'https://connect.facebook.net/en_US/fbevents.js');fbq('init','123');</script>
</head><body>
<a href="https://instagram.com/padaria">Instagram</a>
<a href="https://wa.me/5541999887766">WhatsApp</a>
</body></html>
"""

PLAIN_PAGE = "<html><body><h1>Padaria</h1><a href='/contato'>Contato</a></body></html>"


@pytest.fixture
async def inspector():
    async with httpx.AsyncClient(follow_redirects=True) as client:
        yield HttpWebsiteInspector(client, max_parallel=4)


# ── leitura da página ─────────────────────────────────────────


def test_a_page_with_everything() -> None:
    check = read_page(PIXEL_PAGE)
    assert check.reachable
    assert check.has_meta_pixel is True
    assert check.has_instagram is True
    assert check.has_whatsapp_link is True


def test_a_plain_page_has_none_of_it() -> None:
    check = read_page(PLAIN_PAGE)
    assert check.reachable
    assert check.has_meta_pixel is False
    assert check.has_instagram is False
    assert check.has_whatsapp_link is False


def test_social_links_are_read_from_markup_not_from_prose() -> None:
    """The word Instagram in a paragraph is not a link to Instagram."""
    page = "<html><body><p>Siga no instagram.com/padaria</p></body></html>"
    assert read_page(page).has_instagram is False


# ── busca em lote ─────────────────────────────────────────────


@respx.mock
async def test_each_site_is_visited_and_read(inspector: HttpWebsiteInspector) -> None:
    respx.get("https://compixel.com.br").mock(
        return_value=httpx.Response(200, html=PIXEL_PAGE, headers={"content-type": "text/html"})
    )
    respx.get("https://sempixel.com.br").mock(
        return_value=httpx.Response(200, html=PLAIN_PAGE, headers={"content-type": "text/html"})
    )

    found = await inspector.inspect_many(["https://compixel.com.br", "https://sempixel.com.br"])

    assert found["https://compixel.com.br"].has_meta_pixel is True
    assert found["https://sempixel.com.br"].has_meta_pixel is False


@respx.mock
async def test_a_shared_address_is_fetched_once(
    inspector: HttpWebsiteInspector,
) -> None:
    """Two branches of the same business often point at the same site."""
    route = respx.get("https://padaria.com.br").mock(
        return_value=httpx.Response(200, html=PLAIN_PAGE, headers={"content-type": "text/html"})
    )

    found = await inspector.inspect_many(["https://padaria.com.br", "https://padaria.com.br"])

    assert route.call_count == 1
    assert len(found) == 1


@respx.mock
async def test_an_address_without_a_scheme_still_works(
    inspector: HttpWebsiteInspector,
) -> None:
    respx.get("https://padaria.com.br").mock(
        return_value=httpx.Response(200, html=PLAIN_PAGE, headers={"content-type": "text/html"})
    )
    found = await inspector.inspect_many(["padaria.com.br"])
    assert found["padaria.com.br"].reachable is True


@respx.mock
async def test_a_site_that_does_not_answer_is_unknown_not_broken(
    inspector: HttpWebsiteInspector,
) -> None:
    respx.get("https://fora-do-ar.com.br").mock(
        side_effect=httpx.ConnectError("connection refused")
    )
    check = (await inspector.inspect_many(["https://fora-do-ar.com.br"]))[
        "https://fora-do-ar.com.br"
    ]
    assert check.reachable is False
    assert check.has_meta_pixel is None


@respx.mock
async def test_a_timeout_is_treated_the_same_way(
    inspector: HttpWebsiteInspector,
) -> None:
    respx.get("https://lento.com.br").mock(side_effect=httpx.ReadTimeout("too slow"))
    assert (await inspector.inspect_many(["https://lento.com.br"]))[
        "https://lento.com.br"
    ].reachable is False


@respx.mock
async def test_an_error_page_is_not_read(inspector: HttpWebsiteInspector) -> None:
    respx.get("https://sumiu.com.br").mock(return_value=httpx.Response(404, html=PIXEL_PAGE))
    assert (await inspector.inspect_many(["https://sumiu.com.br"]))[
        "https://sumiu.com.br"
    ].reachable is False


@respx.mock
async def test_a_pdf_is_not_a_page(inspector: HttpWebsiteInspector) -> None:
    respx.get("https://cardapio.com.br").mock(
        return_value=httpx.Response(
            200, content=b"%PDF-1.4", headers={"content-type": "application/pdf"}
        )
    )
    assert (await inspector.inspect_many(["https://cardapio.com.br"]))[
        "https://cardapio.com.br"
    ].reachable is False


async def test_nothing_to_visit(inspector: HttpWebsiteInspector) -> None:
    assert await inspector.inspect_many([]) == {}
    assert await inspector.inspect_many(["", "   "]) == {}
