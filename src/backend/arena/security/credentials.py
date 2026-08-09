"""Authenticated encryption for provider credentials stored in SQLite."""

from __future__ import annotations

import base64
import hashlib
import os

from cryptography.fernet import Fernet, InvalidToken

MASTER_KEY_ENV = "DOUDIZHU_CREDENTIAL_MASTER_KEY"
PREFIX = "enc:v1:"


class CredentialError(RuntimeError):
    pass


def master_key_configured() -> bool:
    return bool(os.getenv(MASTER_KEY_ENV))


def encrypt_secret(value: str) -> str:
    if not value or value.startswith(PREFIX) or value.startswith("${"):
        return value
    secret = os.getenv(MASTER_KEY_ENV)
    if not secret:
        return value  # Preserve legacy/test behavior; Web writes enforce the key.
    token = _fernet(secret).encrypt(value.encode("utf-8")).decode("ascii")
    return PREFIX + token


def decrypt_secret(value: str) -> str:
    if not value or not value.startswith(PREFIX):
        return value
    secret = os.getenv(MASTER_KEY_ENV)
    if not secret:
        raise CredentialError(f"{MASTER_KEY_ENV} is required to decrypt provider credentials")
    try:
        return _fernet(secret).decrypt(value[len(PREFIX):].encode("ascii")).decode("utf-8")
    except InvalidToken as exc:
        raise CredentialError("credential master key is invalid") from exc


def _fernet(secret: str) -> Fernet:
    key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode("utf-8")).digest())
    return Fernet(key)
