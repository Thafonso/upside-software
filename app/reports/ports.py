"""Interfaces the reports service depends on.

The ads numbers arrive through :class:`AdsNumbers`, implemented by
``adapters.ads_numbers`` on top of ``app.ads.AdsApi``. The service never sees
an ads table or an ads type: the adapter converts them into this module's own
domain objects.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Protocol
from uuid import UUID

from app.reports.domain import CampaignInfo, DailyNumbers, Report, ReportTally, Snapshot


class ClientProfile(Protocol):
    """The three things a report needs about a client."""

    @property
    def id(self) -> UUID: ...

    @property
    def name(self) -> str: ...

    @property
    def result_label(self) -> str: ...


class ClientDirectory(Protocol):
    """Looking clients up.

    Structural on purpose: ``app.clients.ClientsApi`` satisfies it without
    this module importing anything inside the clients module.
    """

    async def get_client(self, client_id: UUID) -> ClientProfile | None: ...

    async def list_clients(self) -> Sequence[ClientProfile]: ...


class AdsNumbers(Protocol):
    """What the report needs from the ads module, in reports' own types."""

    async def account_ids(self, client_id: UUID) -> tuple[str, ...]: ...

    async def daily_rows(
        self, account_ids: Sequence[str], since: date, until: date
    ) -> list[DailyNumbers]: ...

    async def campaigns(self, account_ids: Sequence[str]) -> list[CampaignInfo]: ...


class ReportsRepository(Protocol):
    """Persistence contract. Implemented by ``app.reports.repository``."""

    async def create(
        self,
        *,
        client_id: UUID,
        year: int,
        month: int,
        comment: str,
        snapshot: Snapshot,
        generated_by: str,
    ) -> Report: ...

    async def get(self, report_id: UUID) -> Report | None: ...

    async def list_for_client(self, client_id: UUID, *, limit: int = 100) -> list[Report]: ...

    async def list_recent(self, *, limit: int = 50) -> list[Report]: ...

    async def count_for_month(self, client_id: UUID, year: int, month: int) -> int: ...

    async def tally_by_client(self) -> dict[UUID, ReportTally]: ...
