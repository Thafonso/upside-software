"""Symmetric encryption for secrets stored in the database (Meta tokens)."""

from __future__ import annotations

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.shared.config import get_settings


class DecryptionError(RuntimeError):
    """Raised when a stored secret cannot be read with the current FERNET_KEY."""


@lru_cache(maxsize=1)
def _cipher() -> Fernet:
    return Fernet(get_settings().fernet_key.encode("utf-8"))


def encrypt_secret(plaintext: str) -> str:
    """Encrypt a secret for storage. Never log the input or the output."""
    return _cipher().encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_secret(ciphertext: str) -> str:
    try:
        return _cipher().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError) as exc:
        msg = "stored secret could not be decrypted with the current FERNET_KEY"
        raise DecryptionError(msg) from exc
