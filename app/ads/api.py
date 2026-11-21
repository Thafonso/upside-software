"""The only surface other modules may call."""

from __future__ import annotations

from datetime import date
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.ads.domain import AdAccount, Campaign, DailyRow
from app.ads.repository import SqlAlchemyAdsRepository
from app.ads.service import AdsService
from app.clients import ClientsApi


class AdsApi:
    """Facade over the ads module, built with the caller's session.

    The reports module reads its numbers through this, never from the ads
    tables directly.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._repository = SqlAlchemyAdsRepository(session)
        self._service = AdsService(self._repository, ClientsApi(session))

    async def list_accounts(
        self, *, client_id: UUID | None = None, only_active: bool = False
    ) -> list[AdAccount]:
        return await self._service.list_accounts(client_id=client_id, only_active=only_active)

    async def get_account(self, account_id: str) -> AdAccount | None:
        return await self._service.get_account(account_id)

    async def list_campaigns(self, account_id: str) -> list[Campaign]:
        return await self._repository.list_campaigns(account_id)

    async def daily_rows(self, account_id: str, since: date, until: date) -> list[DailyRow]:
        from app.ads.repository import CampaignDailyRow

        rows: list[CampaignDailyRow] = await self._repository.daily_rows_between(
            account_id, since, until
        )
        return [
            DailyRow(
                day=row.date,
                campaign_id=row.campaign_id,
                account_id=row.account_id,
                spend=row.spend,
                impressions=row.impressions,
                reach=row.reach,
                link_clicks=row.link_clicks,
                results=row.results,
                actions=dict(row.actions or {}),
                action_values={},
            )
            for row in rows
        ]
