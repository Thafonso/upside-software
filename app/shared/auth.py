"""Team authentication: argon2 passwords and a signed session cookie."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import Depends, Request, Response
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.clock import now_utc
from app.shared.config import get_settings
from app.shared.db import session_scope
from app.shared.models import AppUser, UserRole

_hasher = PasswordHasher()

# Hash of an unusable password, verified when an e-mail does not exist so that
# wrong e-mails and wrong passwords take the same time to answer.
_DUMMY_HASH = _hasher.hash("account-does-not-exist")

SESSION_SALT = "upside.leadhunter.session"


class NotAuthenticated(Exception):
    """No valid session on a request that needs one."""


class NotAuthorized(Exception):
    """Signed in, but the role is not enough for this screen."""


def hash_password(plaintext: str) -> str:
    return _hasher.hash(plaintext)


def verify_password(password_hash: str, plaintext: str) -> bool:
    try:
        return _hasher.verify(password_hash, plaintext)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(get_settings().session_secret, salt=SESSION_SALT)


def issue_session(response: Response, user_id: UUID) -> None:
    settings = get_settings()
    response.set_cookie(
        settings.session_cookie_name,
        _serializer().dumps(str(user_id)),
        max_age=settings.session_max_age_seconds,
        httponly=True,
        samesite="lax",
        secure=not settings.is_dev,
        path="/",
    )


def clear_session(response: Response) -> None:
    response.delete_cookie(get_settings().session_cookie_name, path="/")


def read_session(request: Request) -> UUID | None:
    settings = get_settings()
    raw = request.cookies.get(settings.session_cookie_name)
    if not raw:
        return None
    try:
        value = _serializer().loads(raw, max_age=settings.session_max_age_seconds)
    except (BadSignature, SignatureExpired):
        return None
    try:
        return UUID(str(value))
    except ValueError:
        return None


async def load_user(session: AsyncSession, user_id: UUID) -> AppUser | None:
    user = await session.get(AppUser, user_id)
    if user is None or not user.is_active:
        return None
    return user


async def authenticate(session: AsyncSession, email: str, password: str) -> AppUser | None:
    """Return the user when the credentials match, otherwise ``None``."""
    normalized = email.strip().lower()
    user = (
        await session.execute(select(AppUser).where(AppUser.email == normalized))
    ).scalar_one_or_none()

    if user is None or not user.is_active:
        verify_password(_DUMMY_HASH, password)
        return None
    if not verify_password(user.password_hash, password):
        return None

    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    user.last_login_at = now_utc()
    return user


async def current_user(
    request: Request,
    session: Annotated[AsyncSession, Depends(session_scope)],
) -> AppUser | None:
    """The signed-in user, or ``None``. Use on screens that work either way."""
    user_id = read_session(request)
    if user_id is None:
        return None
    return await load_user(session, user_id)


async def require_user(
    user: Annotated[AppUser | None, Depends(current_user)],
) -> AppUser:
    if user is None:
        raise NotAuthenticated
    return user


async def require_admin(
    user: Annotated[AppUser, Depends(require_user)],
) -> AppUser:
    if user.role != UserRole.ADMIN:
        raise NotAuthorized
    return user


CurrentUser = Annotated[AppUser, Depends(require_user)]
AdminUser = Annotated[AppUser, Depends(require_admin)]
DbSession = Annotated[AsyncSession, Depends(session_scope)]
