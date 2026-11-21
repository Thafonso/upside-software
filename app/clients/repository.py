"""SQLAlchemy persistence for the ``client`` table (owned by this module)."""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

from sqlalchemy import Numeric, String, Text, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.clients.domain import Client, ClientInput, ClientStatus
from app.shared.db import Base, CreatedAt, EnumAsString, UpdatedAt, UuidPk


class ClientRow(Base):
    __tablename__ = "client"

    id: Mapped[UuidPk]
    name: Mapped[str] = mapped_column(String(160))
    slug: Mapped[str] = mapped_column(String(60), unique=True, index=True)
    status: Mapped[ClientStatus] = mapped_column(
        EnumAsString(ClientStatus, 16), default=ClientStatus.ACTIVE, index=True
    )
    result_action_type: Mapped[str] = mapped_column(String(80), default="lead")
    contact_name: Mapped[str | None] = mapped_column(String(120), default=None)
    contact_email: Mapped[str | None] = mapped_column(String(255), default=None)
    contact_phone: Mapped[str | None] = mapped_column(String(20), default=None)
    monthly_fee: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), default=None)
    notes: Mapped[str | None] = mapped_column(Text, default=None)
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[UpdatedAt]


def to_domain(row: ClientRow) -> Client:
    return Client(
        id=row.id,
        name=row.name,
        slug=row.slug,
        status=ClientStatus(row.status),
        result_action_type=row.result_action_type,
        contact_name=row.contact_name,
        contact_email=row.contact_email,
        contact_phone=row.contact_phone,
        monthly_fee=row.monthly_fee,
        notes=row.notes,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _apply(row: ClientRow, data: ClientInput) -> None:
    row.name = data.name
    row.status = data.status
    row.result_action_type = data.result_action_type
    row.contact_name = data.contact_name
    row.contact_email = data.contact_email
    row.contact_phone = data.contact_phone
    row.monthly_fee = data.monthly_fee
    row.notes = data.notes


class SqlAlchemyClientRepository:
    """Implements :class:`app.clients.ports.ClientRepository`."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_clients(
        self,
        *,
        search: str | None = None,
        statuses: tuple[ClientStatus, ...] | None = None,
    ) -> list[Client]:
        stmt = select(ClientRow)
        if statuses:
            stmt = stmt.where(ClientRow.status.in_([status.value for status in statuses]))
        if search:
            pattern = f"%{search.strip().lower()}%"
            stmt = stmt.where(
                func.lower(ClientRow.name).like(pattern)
                | func.lower(ClientRow.slug).like(pattern)
                | func.lower(func.coalesce(ClientRow.contact_name, "")).like(pattern)
            )
        stmt = stmt.order_by(ClientRow.status, func.lower(ClientRow.name))
        rows = (await self._session.execute(stmt)).scalars().all()
        return [to_domain(row) for row in rows]

    async def get_by_id(self, client_id: UUID) -> Client | None:
        row = await self._session.get(ClientRow, client_id)
        return to_domain(row) if row else None

    async def get_by_slug(self, slug: str) -> Client | None:
        row = (
            await self._session.execute(select(ClientRow).where(ClientRow.slug == slug))
        ).scalar_one_or_none()
        return to_domain(row) if row else None

    async def slug_exists(self, slug: str, *, excluding: UUID | None = None) -> bool:
        stmt = select(func.count()).select_from(ClientRow).where(ClientRow.slug == slug)
        if excluding is not None:
            stmt = stmt.where(ClientRow.id != excluding)
        return bool((await self._session.execute(stmt)).scalar_one())

    async def insert(self, data: ClientInput, *, slug: str) -> Client:
        row = ClientRow(slug=slug)
        _apply(row, data)
        self._session.add(row)
        await self._session.flush()
        await self._session.refresh(row)
        return to_domain(row)

    async def update(self, client_id: UUID, data: ClientInput) -> Client | None:
        row = await self._session.get(ClientRow, client_id)
        if row is None:
            return None
        _apply(row, data)
        await self._session.flush()
        await self._session.refresh(row)
        return to_domain(row)

    async def set_status(self, client_id: UUID, status: ClientStatus) -> Client | None:
        row = await self._session.get(ClientRow, client_id)
        if row is None:
            return None
        row.status = status
        await self._session.flush()
        await self._session.refresh(row)
        return to_domain(row)
