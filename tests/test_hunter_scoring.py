"""Signals and the score. Pure functions, so these are plain unit tests."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.hunter.export import prospects_to_csv, prospects_to_phone_list
from app.hunter.scoring import (
    MAX_SCORE,
    ScoreRule,
    Signals,
    WebsiteType,
    classify_website,
    compute_score,
    extract_whatsapp,
    find_instagram,
    find_meta_pixel,
    find_whatsapp_link,
)
from tests.factories import make_prospect

# ── WhatsApp ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("phone", "expected"),
    [
        # Mobile numbers: the digit after the area code is a 9.
        ("(41) 99988-7766", "5541999887766"),
        ("41999887766", "5541999887766"),
        ("5541999887766", "5541999887766"),
        ("+55 (41) 99988-7766", "5541999887766"),
        # Landlines are not on WhatsApp.
        ("(41) 3333-4444", None),
        ("4133334444", None),
        ("554133334444", None),
        # Nothing usable.
        ("", None),
        (None, None),
        ("sem telefone", None),
        ("123", None),
    ],
)
def test_extract_whatsapp(phone: str | None, expected: str | None) -> None:
    assert extract_whatsapp(phone) == expected


# ── Tipo de site ──────────────────────────────────────────────


@pytest.mark.parametrize(
    ("website", "expected"),
    [
        ("", WebsiteType.NONE),
        (None, WebsiteType.NONE),
        ("   ", WebsiteType.NONE),
        ("https://padaria.com.br", WebsiteType.SITE),
        ("http://www.padaria.com.br/contato", WebsiteType.SITE),
        ("https://instagram.com/padaria", WebsiteType.SOCIAL),
        ("https://www.facebook.com/padaria", WebsiteType.SOCIAL),
        ("https://linktr.ee/padaria", WebsiteType.SOCIAL),
        ("https://wa.me/5541999887766", WebsiteType.SOCIAL),
        ("https://api.whatsapp.com/send?phone=55", WebsiteType.SOCIAL),
        ("https://www.tiktok.com/@padaria", WebsiteType.SOCIAL),
        ("HTTPS://INSTAGRAM.COM/PADARIA", WebsiteType.SOCIAL),
    ],
)
def test_classify_website(website: str | None, expected: WebsiteType) -> None:
    assert classify_website(website) == expected


def test_website_type_labels() -> None:
    assert WebsiteType.NONE.label == "Sem site"
    assert WebsiteType.SOCIAL.label == "Só rede social"
    assert WebsiteType.SITE.label == "Site próprio"


# ── Leitura da página ─────────────────────────────────────────


def test_meta_pixel_needs_both_markers() -> None:
    installed = """
    <script>!function(f,b,e,v,n,t,s){...}(window,document,'script',
    'https://connect.facebook.net/en_US/fbevents.js');fbq('init','1');</script>
    """
    assert find_meta_pixel(installed)
    # Mentioning Facebook is not having a pixel.
    assert not find_meta_pixel('<a href="https://facebook.com/padaria">Facebook</a>')
    assert not find_meta_pixel("<script src='https://connect.facebook.net/sdk.js'>")
    assert not find_meta_pixel("")


def test_find_social_markers() -> None:
    assert find_instagram('<a href="https://instagram.com/padaria">')
    assert not find_instagram("<p>sem redes</p>")
    assert find_whatsapp_link('<a href="https://wa.me/5541999887766">')
    assert find_whatsapp_link('<a href="https://api.whatsapp.com/send?phone=1">')
    assert not find_whatsapp_link('<a href="/contato">')


# ── A nota ────────────────────────────────────────────────────


def test_nothing_known_scores_only_for_having_no_website() -> None:
    score = compute_score(Signals())
    assert score.value == 25
    assert set(score.details) == {ScoreRule.NO_WEBSITE.value}


def test_every_rule_adds_its_points() -> None:
    score = compute_score(
        Signals(
            review_count=120,
            review_rating=Decimal("4.8"),
            website_type=WebsiteType.NONE,
            whatsapp="5541999887766",
            emails=("contato@padaria.com.br",),
        )
    )
    # 20 reviews + 10 rating + 25 no website + 15 whatsapp + 10 e-mail
    assert score.value == 80
    assert score.details == {
        ScoreRule.MANY_REVIEWS.value: 20,
        ScoreRule.GOOD_RATING.value: 10,
        ScoreRule.NO_WEBSITE.value: 25,
        ScoreRule.WHATSAPP.value: 15,
        ScoreRule.EMAIL.value: 10,
    }


def test_thresholds_are_inclusive() -> None:
    assert ScoreRule.MANY_REVIEWS.value in compute_score(Signals(review_count=30)).details
    assert ScoreRule.MANY_REVIEWS.value not in compute_score(Signals(review_count=29)).details
    assert (
        ScoreRule.GOOD_RATING.value in compute_score(Signals(review_rating=Decimal("4.3"))).details
    )
    assert (
        ScoreRule.GOOD_RATING.value
        not in compute_score(Signals(review_rating=Decimal("4.2"))).details
    )


def test_website_rules_are_exclusive() -> None:
    """A prospect has one kind of web presence, so only one rule applies."""
    none = compute_score(Signals(website_type=WebsiteType.NONE)).details
    social = compute_score(Signals(website_type=WebsiteType.SOCIAL)).details
    site = compute_score(Signals(website_type=WebsiteType.SITE, has_meta_pixel=False)).details

    assert none == {ScoreRule.NO_WEBSITE.value: 25}
    assert social == {ScoreRule.SOCIAL_ONLY.value: 20}
    assert site == {ScoreRule.SITE_WITHOUT_PIXEL.value: 20}


def test_an_unchecked_site_earns_nothing_for_the_pixel() -> None:
    """Not knowing is not evidence: the point waits for the visit."""
    unchecked = compute_score(Signals(website_type=WebsiteType.SITE, has_meta_pixel=None))
    assert unchecked.details == {}
    assert unchecked.value == 0


def test_a_site_with_a_pixel_earns_nothing_either() -> None:
    with_pixel = compute_score(Signals(website_type=WebsiteType.SITE, has_meta_pixel=True))
    assert with_pixel.details == {}


def test_the_score_is_capped_at_one_hundred() -> None:
    score = compute_score(
        Signals(
            review_count=5000,
            review_rating=Decimal("5.0"),
            website_type=WebsiteType.NONE,
            whatsapp="5541999887766",
            emails=("a@b.com", "c@d.com"),
        )
    )
    assert score.value <= MAX_SCORE


def test_reasons_come_out_sorted_for_the_tooltip() -> None:
    score = compute_score(
        Signals(review_count=50, website_type=WebsiteType.NONE, whatsapp="5541999887766")
    )
    assert score.reasons == [
        ("Sem site", 25),
        ("30+ avaliações", 20),
        ("Tem WhatsApp", 15),
    ]


# ── Exportação ────────────────────────────────────────────────


def test_csv_opens_correctly_in_brazilian_excel() -> None:
    csv_text = prospects_to_csv([make_prospect(name="Padaria São José", score=70)])
    assert csv_text.startswith("﻿")  # BOM, or accents break
    header, first = csv_text.lstrip("﻿").splitlines()[:2]
    assert header.startswith("Nome;Nota;")
    assert first.startswith("Padaria São José;70;")


def test_csv_always_has_a_header_even_with_no_rows() -> None:
    assert prospects_to_csv([]).lstrip("﻿").startswith("Nome;")


def test_csv_shows_the_rating_with_a_comma() -> None:
    csv_text = prospects_to_csv([make_prospect(review_rating=Decimal("4.7"))])
    assert ";4,7;" in csv_text


def test_phone_list_prefers_whatsapp_and_drops_repeats() -> None:
    numbers = prospects_to_phone_list(
        [
            make_prospect(phone="(41) 3333-4444", whatsapp="5541999887766"),
            make_prospect(phone="(41) 3333-4444", whatsapp=None),
            make_prospect(phone="(41) 3333-4444", whatsapp=None),
            make_prospect(phone=None, whatsapp=None),
        ]
    )
    assert numbers == ["5541999887766", "(41) 3333-4444"]
