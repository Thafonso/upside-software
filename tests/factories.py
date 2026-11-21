"""Builders for domain objects, so tests say only what they are about."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from app.hunter.domain import Prospect, ProspectStatus
from app.hunter.scoring import WebsiteType

FIXED_MOMENT = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)


def make_prospect(**overrides: Any) -> Prospect:
    """A prospect with sensible defaults; pass only what the test cares about."""
    base: dict[str, Any] = {
        "id": uuid4(),
        "search_id": uuid4(),
        "name": "Padaria Central",
        "maps_link": f"https://maps.google.com/{uuid4()}",
        "category": "Padaria",
        "address": "R. Francisco Rocha, 612",
        "borough": "Batel",
        "city": "Curitiba",
        "phone": "(41) 3333-4444",
        "website": None,
        "emails": (),
        "review_rating": Decimal("4.5"),
        "review_count": 100,
        "status": ProspectStatus.NEW,
        "do_not_contact": False,
        "client_id": None,
        "created_at": FIXED_MOMENT,
        "updated_at": FIXED_MOMENT,
        "whatsapp": None,
        "website_type": WebsiteType.NONE,
        "has_meta_pixel": None,
        "has_instagram": None,
        "has_whatsapp_link": None,
        "site_unreachable": False,
        "site_checked_at": None,
        "score": 0,
        "score_details": {},
    }
    return Prospect(**{**base, **overrides})


def prospect_ids(prospects: list[Prospect]) -> list[UUID]:
    return [prospect.id for prospect in prospects]
