"""Signals read off a prospect and the score built from them.

Pure functions: no database, no network. The same input always gives the
same score, which is what lets the screen explain itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

MAX_SCORE = 100

# A link to one of these is a social profile, not a site of its own.
SOCIAL_HOSTS: tuple[str, ...] = (
    "instagram.com",
    "facebook.com",
    "linktr.ee",
    "wa.me",
    "whatsapp.com",
    "tiktok.com",
)

# What a Meta pixel looks like in the page source.
PIXEL_MARKERS: tuple[str, ...] = ("connect.facebook.net/", "fbevents.js")

INSTAGRAM_MARKERS: tuple[str, ...] = ("instagram.com",)
WHATSAPP_MARKERS: tuple[str, ...] = ("wa.me", "api.whatsapp.com")

REVIEW_COUNT_THRESHOLD = 30
RATING_THRESHOLD = Decimal("4.3")


class WebsiteType(StrEnum):
    NONE = "none"
    SOCIAL = "social"
    SITE = "site"

    @property
    def label(self) -> str:
        return {
            WebsiteType.NONE: "Sem site",
            WebsiteType.SOCIAL: "Só rede social",
            WebsiteType.SITE: "Site próprio",
        }[self]


class ScoreRule(StrEnum):
    """Why a prospect scored what it scored."""

    MANY_REVIEWS = "many_reviews"
    GOOD_RATING = "good_rating"
    NO_WEBSITE = "no_website"
    SOCIAL_ONLY = "social_only"
    SITE_WITHOUT_PIXEL = "site_without_pixel"
    WHATSAPP = "whatsapp"
    EMAIL = "email"

    @property
    def label(self) -> str:
        return {
            ScoreRule.MANY_REVIEWS: f"{REVIEW_COUNT_THRESHOLD}+ avaliações",
            ScoreRule.GOOD_RATING: "Nota média 4,3 ou mais",
            ScoreRule.NO_WEBSITE: "Sem site",
            ScoreRule.SOCIAL_ONLY: "Só rede social",
            ScoreRule.SITE_WITHOUT_PIXEL: "Site sem pixel da Meta",
            ScoreRule.WHATSAPP: "Tem WhatsApp",
            ScoreRule.EMAIL: "E-mail encontrado",
        }[self]


SCORING_RULES: dict[ScoreRule, int] = {
    ScoreRule.MANY_REVIEWS: 20,
    ScoreRule.GOOD_RATING: 10,
    ScoreRule.NO_WEBSITE: 25,
    ScoreRule.SOCIAL_ONLY: 20,
    ScoreRule.SITE_WITHOUT_PIXEL: 20,
    ScoreRule.WHATSAPP: 15,
    ScoreRule.EMAIL: 10,
}


@dataclass(frozen=True, slots=True)
class Signals:
    """Everything the score is built from."""

    review_count: int = 0
    review_rating: Decimal | None = None
    website_type: WebsiteType = WebsiteType.NONE
    whatsapp: str | None = None
    emails: tuple[str, ...] = ()
    has_meta_pixel: bool | None = None  # None: the site was not checked yet

    @property
    def has_email(self) -> bool:
        return bool(self.emails)


@dataclass(frozen=True, slots=True)
class Score:
    """A score and the reasons behind it, ready to show on screen."""

    value: int
    details: dict[str, int]

    @property
    def reasons(self) -> list[tuple[str, int]]:
        """Human-readable reasons, biggest first."""
        pairs = [(ScoreRule(rule).label, points) for rule, points in self.details.items()]
        return sorted(pairs, key=lambda pair: -pair[1])


def extract_whatsapp(phone: str | None) -> str | None:
    """A Brazilian mobile number, ready for a wa.me link.

    Digits only; a leading country code is dropped before deciding, and the
    number is only a mobile when the digit after the area code is a 9.
    """
    if not phone:
        return None
    digits = "".join(char for char in phone if char.isdigit())
    if digits.startswith("55") and len(digits) == 13:
        digits = digits[2:]
    if len(digits) == 11 and digits[2] == "9":
        return f"55{digits}"
    return None


def classify_website(website: str | None) -> WebsiteType:
    """No link, a social profile, or a site of their own."""
    if not website or not website.strip():
        return WebsiteType.NONE
    lowered = website.strip().lower()
    if any(host in lowered for host in SOCIAL_HOSTS):
        return WebsiteType.SOCIAL
    return WebsiteType.SITE


def find_meta_pixel(html: str) -> bool:
    """Both markers must be there: the loader and the events script."""
    lowered = html.lower()
    return all(marker in lowered for marker in PIXEL_MARKERS)


def find_instagram(html: str) -> bool:
    lowered = html.lower()
    return any(marker in lowered for marker in INSTAGRAM_MARKERS)


def find_whatsapp_link(html: str) -> bool:
    lowered = html.lower()
    return any(marker in lowered for marker in WHATSAPP_MARKERS)


def compute_score(signals: Signals) -> Score:
    """Score a prospect from 0 to 100, keeping the reasons.

    Reading the table: a business the agency can help the most scores
    highest - plenty of customers already, and no digital presence doing
    the work for them.
    """
    details: dict[str, int] = {}

    def award(rule: ScoreRule) -> None:
        details[rule.value] = SCORING_RULES[rule]

    if signals.review_count >= REVIEW_COUNT_THRESHOLD:
        award(ScoreRule.MANY_REVIEWS)

    if signals.review_rating is not None and signals.review_rating >= RATING_THRESHOLD:
        award(ScoreRule.GOOD_RATING)

    # These three are exclusive: a prospect has one kind of web presence.
    if signals.website_type == WebsiteType.NONE:
        award(ScoreRule.NO_WEBSITE)
    elif signals.website_type == WebsiteType.SOCIAL:
        award(ScoreRule.SOCIAL_ONLY)
    elif signals.has_meta_pixel is False:
        # Only after the site was actually checked: not knowing is not a point.
        award(ScoreRule.SITE_WITHOUT_PIXEL)

    if signals.whatsapp:
        award(ScoreRule.WHATSAPP)

    if signals.has_email:
        award(ScoreRule.EMAIL)

    return Score(value=min(sum(details.values()), MAX_SCORE), details=details)
