"""Tables owned by ``app.shared``: team accounts (and, from step 2, the job queue)."""

from __future__ import annotations

from enum import StrEnum

from sqlalchemy import Boolean, String
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db import (
    Base,
    CreatedAt,
    EnumAsString,
    Timestamptz,
    UpdatedAt,
    UuidPk,
)


class UserRole(StrEnum):
    ADMIN = "admin"
    TEAM = "team"

    @property
    def label(self) -> str:
        return {UserRole.ADMIN: "Administrador", UserRole.TEAM: "Equipe"}[self]


class AppUser(Base):
    __tablename__ = "app_user"

    id: Mapped[UuidPk]
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[UserRole] = mapped_column(EnumAsString(UserRole, 16), default=UserRole.TEAM)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_login_at: Mapped[Timestamptz | None] = mapped_column(default=None)
    created_at: Mapped[CreatedAt]
    updated_at: Mapped[UpdatedAt]

    @property
    def is_admin(self) -> bool:
        return self.role == UserRole.ADMIN

    @property
    def initials(self) -> str:
        parts = [piece for piece in self.name.split() if piece]
        if not parts:
            return self.email[:1].upper()
        if len(parts) == 1:
            return parts[0][:2].upper()
        return (parts[0][:1] + parts[-1][:1]).upper()
