"""Pure hunter rules: queries, quantity mapping, validation, deadlines."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.hunter.domain import (
    DEFAULT_QUANTITY,
    MAX_NICHES,
    QUANTITY_PRESETS,
    ProspectStatus,
    ScrapeStatus,
    SearchStatus,
    SearchValidationError,
    build_query,
    build_search_input,
    build_search_name,
    deadline_for,
    parse_niches,
    scrape_parameters,
)

# ── Consultas ─────────────────────────────────────────────────


def test_query_uses_neighborhood_when_given() -> None:
    assert build_query("dentista", "Batel", "Curitiba") == "dentista em Batel, Curitiba"


def test_query_falls_back_to_the_city() -> None:
    assert build_query("dentista", None, "Curitiba") == "dentista em Curitiba"
    assert build_query("dentista", "", "Curitiba") == "dentista em Curitiba"


def test_search_name_shows_niches_and_place() -> None:
    assert build_search_name(("Dentista",), "Batel", "Curitiba") == "Dentista · Batel"
    assert build_search_name(("Dentista",), None, "Curitiba") == "Dentista · Curitiba"
    assert (
        build_search_name(("Dentista", "Ortodontia"), "Batel", "Curitiba")
        == "Dentista + Ortodontia · Batel"
    )


# ── Nichos ────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("dentista", ("dentista",)),
        ("dentista\nortodontia", ("dentista", "ortodontia")),
        ("dentista, ortodontia", ("dentista", "ortodontia")),
        ("dentista; ortodontia", ("dentista", "ortodontia")),
        ("  dentista  \n\n  ortodontia ", ("dentista", "ortodontia")),
        ("dentista\nDENTISTA", ("dentista",)),
        ("", ()),
        ("   \n  ", ()),
    ],
)
def test_parse_niches(raw: str, expected: tuple[str, ...]) -> None:
    assert parse_niches(raw) == expected


# ── Quantidade -> parâmetros do scraper ───────────────────────


@pytest.mark.parametrize(
    ("quantity", "depth", "max_time"),
    [(20, 2, 600), (60, 5, 1200), (120, 10, 1800), (200, 18, 3600)],
)
def test_quantity_maps_to_the_mazyos_table(quantity: int, depth: int, max_time: int) -> None:
    assert scrape_parameters(quantity, fetch_emails=False) == (depth, max_time)


def test_looking_for_emails_doubles_the_budget() -> None:
    assert scrape_parameters(60, fetch_emails=True) == (5, 2400)
    assert scrape_parameters(200, fetch_emails=True) == (18, 7200)


def test_unmapped_quantity_is_refused() -> None:
    with pytest.raises(ValueError, match="quantidade"):
        scrape_parameters(37, fetch_emails=False)


def test_no_preset_has_a_zero(
    # depth and max_time of zero are rejected by the scraper itself
) -> None:
    for depth, max_time in QUANTITY_PRESETS.values():
        assert depth > 0
        assert max_time > 0


# ── Prazo ─────────────────────────────────────────────────────


def test_deadline_adds_the_budget_plus_margin() -> None:
    started = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)
    assert deadline_for(started, 1200) == started + timedelta(minutes=25)


# ── Validação do formulário ───────────────────────────────────


def test_valid_form_becomes_queries() -> None:
    data = build_search_input(
        niches="dentista\nortodontia",
        city="  Curitiba ",
        neighborhood=" Batel ",
        quantity="120",
        fetch_emails=True,
    )
    assert data.niches == ("dentista", "ortodontia")
    assert data.city == "Curitiba"
    assert data.neighborhood == "Batel"
    assert data.quantity == 120
    assert data.fetch_emails is True
    assert data.queries == (
        "dentista em Batel, Curitiba",
        "ortodontia em Batel, Curitiba",
    )
    assert data.display_name == "dentista + ortodontia · Batel"

    request = data.to_request()
    assert request.depth == 10
    assert request.max_time_seconds == 3600
    assert request.language == "pt"


def test_defaults_are_sane() -> None:
    data = build_search_input(niches="padaria", city="Curitiba")
    assert data.quantity == DEFAULT_QUANTITY
    assert data.neighborhood is None
    assert data.fetch_emails is False
    assert data.queries == ("padaria em Curitiba",)


def test_missing_niche_and_city_are_reported() -> None:
    with pytest.raises(SearchValidationError) as caught:
        build_search_input(niches="  ", city=" ")
    assert set(caught.value.errors) == {"niches", "city"}


def test_too_many_niches_is_refused() -> None:
    with pytest.raises(SearchValidationError) as caught:
        build_search_input(
            niches="\n".join(f"nicho {index}" for index in range(MAX_NICHES + 1)),
            city="Curitiba",
        )
    assert "niches" in caught.value.errors


def test_quantity_outside_the_list_is_refused() -> None:
    with pytest.raises(SearchValidationError) as caught:
        build_search_input(niches="padaria", city="Curitiba", quantity="37")
    assert "quantity" in caught.value.errors

    with pytest.raises(SearchValidationError) as caught:
        build_search_input(niches="padaria", city="Curitiba", quantity="muitas")
    assert "quantity" in caught.value.errors


# ── Situações ─────────────────────────────────────────────────


def test_search_status_labels_and_openness() -> None:
    assert SearchStatus.QUEUED.label == "Na fila"
    assert SearchStatus.RUNNING.label == "Buscando"
    assert SearchStatus.DONE.label == "Concluída"
    assert SearchStatus.FAILED.label == "Falhou"

    assert SearchStatus.QUEUED.is_open
    assert SearchStatus.RUNNING.is_open
    assert not SearchStatus.DONE.is_open
    assert not SearchStatus.FAILED.is_open


def test_scrape_status_knows_when_it_is_over() -> None:
    assert not ScrapeStatus.PENDING.is_finished
    assert not ScrapeStatus.RUNNING.is_finished
    assert ScrapeStatus.DONE.is_finished
    assert ScrapeStatus.FAILED.is_finished


def test_prospect_status_labels_are_portuguese() -> None:
    assert ProspectStatus.NEW.label == "Novo"
    assert ProspectStatus.WON.label == "Virou cliente"
    assert ProspectStatus.DISCARDED.label == "Descartado"
