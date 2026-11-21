"""The only surface other modules may call."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.hunter.domain import Prospect, Search
from app.hunter.repository import SqlAlchemyHunterRepository
from app.hunter.service import HunterService
from app.shared.config import get_settings


class HunterApi:
    """Facade over the hunter module, built with the caller's session."""

    def __init__(self, session: AsyncSession) -> None:
        self._service = HunterService(
            SqlAlchemyHunterRepository(session),
            daily_limit=get_settings().hunter_daily_limit,
        )

    async def get_search(self, search_id: UUID) -> Search | None:
        return await self._service.get_search(search_id)

    async def list_searches(self, *, limit: int = 50) -> list[Search]:
        return await self._service.list_searches(limit=limit)

    async def list_prospects(self, search_id: UUID, *, limit: int = 500) -> list[Prospect]:
        return await self._service.list_prospects(search_id, limit=limit)

    async def searches_used_today(self) -> int:
        return await self._service.searches_used_today()
