"""Hunter entities and pure rules. No FastAPI, no SQLAlchemy, no httpx."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from app.hunter.scoring import (
    Score,
    Signals,
    WebsiteType,
    classify_website,
    compute_score,
    extract_whatsapp,
)

MAX_NICHES = 5
MAX_TEXT = 80
# How long after the scraper's own budget we stop waiting on a search.
TIMEOUT_MARGIN = timedelta(minutes=5)


class SearchStatus(StrEnum):
    """Where one of our searches stands."""

    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"

    @property
    def label(self) -> str:
        return {
            SearchStatus.QUEUED: "Na fila",
            SearchStatus.RUNNING: "Buscando",
            SearchStatus.DONE: "Concluída",
            SearchStatus.FAILED: "Falhou",
        }[self]

    @property
    def is_open(self) -> bool:
        """Still worth polling the screen for."""
        return self in (SearchStatus.QUEUED, SearchStatus.RUNNING)


class ScrapeStatus(StrEnum):
    """What the scraper says about its own job."""

    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"

    @property
    def is_finished(self) -> bool:
        return self in (ScrapeStatus.DONE, ScrapeStatus.FAILED)


class ProspectStatus(StrEnum):
    NEW = "new"
    CONTACTED = "contacted"
    MEETING = "meeting"
    WON = "won"
    DISCARDED = "discarded"

    @property
    def label(self) -> str:
        return {
            ProspectStatus.NEW: "Novo",
            ProspectStatus.CONTACTED: "Contatado",
            ProspectStatus.MEETING: "Reunião",
            ProspectStatus.WON: "Virou cliente",
            ProspectStatus.DISCARDED: "Descartado",
        }[self]


# Quantity the user asks for -> what the scraper needs. Same table the
# MazyOS interface uses, so results match what the team is used to.
QUANTITY_PRESETS: dict[int, tuple[int, int]] = {
    20: (2, 600),
    60: (5, 1200),
    120: (10, 1800),
    200: (18, 3600),
}
DEFAULT_QUANTITY = 60
QUANTITY_CHOICES: tuple[int, ...] = tuple(sorted(QUANTITY_PRESETS))


class SearchValidationError(ValueError):
    """Form-level validation failure, one message per field."""

    def __init__(self, errors: dict[str, str]) -> None:
        self.errors = errors
        super().__init__("; ".join(f"{field}: {msg}" for field, msg in errors.items()))


class DailyLimitReached(Exception):
    """The team already used today's search budget."""

    def __init__(self, used: int, limit: int) -> None:
        self.used = used
        self.limit = limit
        super().__init__(f"{used}/{limit} searches used today")


@dataclass(frozen=True, slots=True)
class SearchRequest:
    """What we ask the scraper for."""

    name: str
    queries: tuple[str, ...]
    depth: int
    max_time_seconds: int
    fetch_emails: bool
    language: str = "pt"


@dataclass(frozen=True, slots=True)
class SearchInput:
    """A validated search form, before it becomes a row."""

    niches: tuple[str, ...]
    city: str
    neighborhood: str | None
    quantity: int
    fetch_emails: bool

    @property
    def queries(self) -> tuple[str, ...]:
        return tuple(build_query(niche, self.neighborhood, self.city) for niche in self.niches)

    @property
    def display_name(self) -> str:
        return build_search_name(self.niches, self.neighborhood, self.city)

    def to_request(self) -> SearchRequest:
        depth, max_time = scrape_parameters(self.quantity, fetch_emails=self.fetch_emails)
        return SearchRequest(
            name=self.display_name,
            queries=self.queries,
            depth=depth,
            max_time_seconds=max_time,
            fetch_emails=self.fetch_emails,
        )


@dataclass(frozen=True, slots=True)
class FoundCompany:
    """One business as the scraper found it, already in our vocabulary."""

    name: str
    maps_link: str
    address: str | None = None
    category: str | None = None
    phone: str | None = None
    website: str | None = None
    borough: str | None = None
    city: str | None = None
    latitude: Decimal | None = None
    longitude: Decimal | None = None
    review_rating: Decimal | None = None
    review_count: int = 0
    emails: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True, slots=True)
class Prospect:
    """A business found by a search, as the team works with it."""

    id: UUID
    search_id: UUID
    name: str
    maps_link: str
    category: str | None
    address: str | None
    borough: str | None
    city: str | None
    phone: str | None
    website: str | None
    emails: tuple[str, ...]
    review_rating: Decimal | None
    review_count: int
    status: ProspectStatus
    do_not_contact: bool
    client_id: UUID | None
    created_at: datetime
    updated_at: datetime
    # Signals, written together with the row.
    whatsapp: str | None = None
    website_type: WebsiteType = WebsiteType.NONE
    has_meta_pixel: bool | None = None
    has_instagram: bool | None = None
    has_whatsapp_link: bool | None = None
    site_unreachable: bool = False
    site_checked_at: datetime | None = None
    score: int = 0
    score_details: dict[str, int] = field(default_factory=dict)

    @property
    def status_label(self) -> str:
        return self.status.label

    @property
    def first_email(self) -> str | None:
        return self.emails[0] if self.emails else None

    @property
    def whatsapp_link(self) -> str | None:
        return f"https://wa.me/{self.whatsapp}" if self.whatsapp else None

    @property
    def website_label(self) -> str:
        return self.website_type.label

    @property
    def score_reasons(self) -> list[tuple[str, int]]:
        return Score(value=self.score, details=self.score_details).reasons

    @property
    def score_explanation(self) -> str:
        """One line for the tooltip on the score."""
        reasons = self.score_reasons
        if not reasons:
            return "Nenhum sinal encontrado."
        return " · ".join(f"{label} +{points}" for label, points in reasons)

    @property
    def needs_site_check(self) -> bool:
        """Own site, never reached yet: worth visiting."""
        return (
            self.website_type == WebsiteType.SITE
            and self.site_checked_at is None
            and not self.site_unreachable
        )


def signals_for(
    *,
    phone: str | None,
    website: str | None,
    emails: tuple[str, ...],
    review_count: int,
    review_rating: Decimal | None,
    has_meta_pixel: bool | None = None,
) -> Signals:
    """Read every signal off a prospect's raw data."""
    return Signals(
        review_count=review_count,
        review_rating=review_rating,
        website_type=classify_website(website),
        whatsapp=extract_whatsapp(phone),
        emails=emails,
        has_meta_pixel=has_meta_pixel,
    )


def score_for(signals: Signals) -> Score:
    return compute_score(signals)


@dataclass(frozen=True, slots=True)
class SiteCheck:
    """What visiting a prospect's own site told us."""

    reachable: bool
    has_meta_pixel: bool | None = None
    has_instagram: bool | None = None
    has_whatsapp_link: bool | None = None

    @classmethod
    def unreachable(cls) -> SiteCheck:
        """A site we could not open: not a failure, just unknown."""
        return cls(reachable=False)


@dataclass(frozen=True, slots=True)
class ProspectFilters:
    """What the list screen is showing right now."""

    search_id: UUID | None = None
    status: ProspectStatus | None = None
    no_website: bool = False
    social_only: bool = False
    has_whatsapp: bool = False
    has_email: bool = False
    min_score: int = 0

    @property
    def is_filtered(self) -> bool:
        return any(
            (
                self.search_id,
                self.status,
                self.no_website,
                self.social_only,
                self.has_whatsapp,
                self.has_email,
                self.min_score,
            )
        )

    def as_query(self) -> dict[str, str]:
        """The filters as form fields, to carry them through an HTMX post."""
        return {
            "search_id": str(self.search_id) if self.search_id else "",
            "status": self.status.value if self.status else "",
            "no_website": "1" if self.no_website else "",
            "social_only": "1" if self.social_only else "",
            "has_whatsapp": "1" if self.has_whatsapp else "",
            "has_email": "1" if self.has_email else "",
            "min_score": str(self.min_score) if self.min_score else "",
        }


def build_prospect_filters(
    *,
    search_id: str = "",
    status: str = "",
    no_website: str = "",
    social_only: str = "",
    has_whatsapp: str = "",
    has_email: str = "",
    min_score: str = "",
) -> ProspectFilters:
    """Read the filter bar. Anything unreadable simply does not filter."""
    parsed_search: UUID | None = None
    if search_id:
        try:
            parsed_search = UUID(search_id)
        except ValueError:
            parsed_search = None

    parsed_status: ProspectStatus | None = None
    if status:
        try:
            parsed_status = ProspectStatus(status)
        except ValueError:
            parsed_status = None

    try:
        parsed_score = max(0, min(int(min_score or 0), 100))
    except ValueError:
        parsed_score = 0

    return ProspectFilters(
        search_id=parsed_search,
        status=parsed_status,
        no_website=bool(no_website),
        social_only=bool(social_only),
        has_whatsapp=bool(has_whatsapp),
        has_email=bool(has_email),
        min_score=parsed_score,
    )


@dataclass(frozen=True, slots=True)
class Search:
    """A search the team ran."""

    id: UUID
    name: str
    niches: tuple[str, ...]
    city: str
    neighborhood: str | None
    quantity: int
    fetch_emails: bool
    status: SearchStatus
    scraper_job_id: str | None
    total_found: int
    error_message: str | None
    depth: int
    max_time_seconds: int
    started_at: datetime | None
    deadline_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime

    @property
    def status_label(self) -> str:
        return self.status.label

    @property
    def is_open(self) -> bool:
        return self.status.is_open

    @property
    def where(self) -> str:
        return f"{self.neighborhood}, {self.city}" if self.neighborhood else self.city

    def to_request(self) -> SearchRequest:
        """Rebuild what we ask the provider, from the stored row."""
        return SearchRequest(
            name=self.name,
            queries=tuple(
                build_query(niche, self.neighborhood, self.city) for niche in self.niches
            ),
            depth=self.depth,
            max_time_seconds=self.max_time_seconds,
            fetch_emails=self.fetch_emails,
        )


def normalize_text(value: str | None, *, limit: int = MAX_TEXT) -> str | None:
    if value is None:
        return None
    collapsed = " ".join(value.split())
    return collapsed[:limit] or None


def parse_niches(raw: str) -> tuple[str, ...]:
    """Accept niches one per line or separated by commas or semicolons."""
    separated = raw.replace(";", "\n").replace(",", "\n")
    seen: list[str] = []
    for piece in separated.splitlines():
        niche = normalize_text(piece)
        if niche and niche.lower() not in {item.lower() for item in seen}:
            seen.append(niche)
    return tuple(seen)


def build_query(niche: str, neighborhood: str | None, city: str) -> str:
    """The phrase the scraper searches for, as the MazyOS interface builds it."""
    if neighborhood:
        return f"{niche} em {neighborhood}, {city}"
    return f"{niche} em {city}"


def build_search_name(niches: tuple[str, ...], neighborhood: str | None, city: str) -> str:
    where = neighborhood or city
    return f"{' + '.join(niches)} · {where}"


def scrape_parameters(quantity: int, *, fetch_emails: bool) -> tuple[int, int]:
    """Map a wanted quantity to the scraper's depth and time budget.

    Looking for e-mails means visiting each site, so the budget doubles.
    """
    if quantity not in QUANTITY_PRESETS:
        msg = f"quantidade sem mapeamento: {quantity}"
        raise ValueError(msg)
    depth, max_time = QUANTITY_PRESETS[quantity]
    return depth, max_time * 2 if fetch_emails else max_time


def deadline_for(started_at: datetime, max_time_seconds: int) -> datetime:
    """When we stop waiting: the scraper's own budget plus a margin."""
    return started_at + timedelta(seconds=max_time_seconds) + TIMEOUT_MARGIN


def build_search_input(
    *,
    niches: str,
    city: str,
    neighborhood: str | None = None,
    quantity: str | int = DEFAULT_QUANTITY,
    fetch_emails: bool = False,
) -> SearchInput:
    """Validate the raw form. Raises :class:`SearchValidationError`."""
    errors: dict[str, str] = {}

    parsed_niches = parse_niches(niches or "")
    if not parsed_niches:
        errors["niches"] = "Informe ao menos um nicho."
    elif len(parsed_niches) > MAX_NICHES:
        errors["niches"] = f"No máximo {MAX_NICHES} nichos por busca."

    clean_city = normalize_text(city)
    if not clean_city:
        errors["city"] = "Informe a cidade."

    try:
        parsed_quantity = int(quantity)
    except (TypeError, ValueError):
        parsed_quantity = DEFAULT_QUANTITY
        errors["quantity"] = "Quantidade inválida."
    else:
        if parsed_quantity not in QUANTITY_PRESETS:
            errors["quantity"] = "Escolha uma das quantidades da lista."

    if errors:
        raise SearchValidationError(errors)

    return SearchInput(
        niches=parsed_niches,
        city=clean_city or "",
        neighborhood=normalize_text(neighborhood),
        quantity=parsed_quantity,
        fetch_emails=fetch_emails,
    )


SCRAPER_FAILURE_MESSAGE = (
    "O Google não devolveu resultados ou bloqueou temporariamente. Tente mais tarde."
)
TIMEOUT_MESSAGE = (
    "A busca passou do tempo previsto e foi encerrada. Tente de novo com uma quantidade menor."
)
