"""The ads module seen through the reports port.

This file is the only place in ``app.reports`` that knows the ads module
exists, and it reaches it through the public ``AdsApi`` - never a table, a
repository or an ads service.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.ads import AdsApi
from app.reports.domain import CampaignInfo, DailyNumbers


class AdsApiNumbers:
    """Implements :class:`app.reports.ports.AdsNumbers`."""

    def __init__(self, session: AsyncSession) -> None:
        self._ads = AdsApi(session)

    async def account_ids(self, client_id: UUID) -> tuple[str, ...]:
        accounts = await self._ads.list_accounts(client_id=client_id)
        return tuple(account.id for account in accounts)

    async def daily_rows(
        self, account_ids: Sequence[str], since: date, until: date
    ) -> list[DailyNumbers]:
        """Every account's days, converted into this module's own type."""
        rows: list[DailyNumbers] = []
        for account_id in account_ids:
            for row in await self._ads.daily_rows(account_id, since, until):
                rows.append(
                    DailyNumbers(
                        day=row.day,
                        campaign_id=row.campaign_id,
                        spend=row.spend,
                        impressions=row.impressions,
                        reach=row.reach,
                        link_clicks=row.link_clicks,
                        results=row.results,
                    )
                )
        return rows

    async def campaigns(self, account_ids: Sequence[str]) -> list[CampaignInfo]:
        found: list[CampaignInfo] = []
        for account_id in account_ids:
            for campaign in await self._ads.list_campaigns(account_id):
                found.append(
                    CampaignInfo(id=campaign.id, name=campaign.name, status=campaign.status)
                )
        return found
