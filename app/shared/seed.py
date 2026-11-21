"""``make seed`` - create or refresh the admin account from the environment."""

from __future__ import annotations

import asyncio
import logging

from sqlalchemy import select

from app.shared.auth import hash_password
from app.shared.config import get_settings
from app.shared.db import dispose_engine, get_sessionmaker
from app.shared.models import AppUser, UserRole

logger = logging.getLogger("app.seed")


async def seed_admin() -> str:
    """Upsert the admin user. Returns a message describing what happened."""
    settings = get_settings()
    email = settings.admin_email.strip().lower()
    password = settings.admin_password

    if not email or not password:
        msg = "ADMIN_EMAIL e ADMIN_PASSWORD precisam estar definidos no .env"
        raise RuntimeError(msg)

    async with get_sessionmaker()() as session:
        existing = (
            await session.execute(select(AppUser).where(AppUser.email == email))
        ).scalar_one_or_none()

        if existing is None:
            session.add(
                AppUser(
                    email=email,
                    name="Administrador",
                    password_hash=hash_password(password),
                    role=UserRole.ADMIN,
                    is_active=True,
                )
            )
            outcome = f"admin criado: {email}"
        else:
            existing.password_hash = hash_password(password)
            existing.role = UserRole.ADMIN
            existing.is_active = True
            outcome = f"admin atualizado: {email}"

        await session.commit()

    return outcome


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        logger.info(await seed_admin())
    finally:
        await dispose_engine()


if __name__ == "__main__":
    asyncio.run(main())
