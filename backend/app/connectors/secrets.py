"""Credentials at rest: Fernet (AES-128-CBC + HMAC-SHA256) with a key from FRED_SECRET_KEY.

The API never returns a stored secret; the UI only ever sees the last four characters.
"""
from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from ..config import get_settings


def _fernet() -> Fernet:
    raw = get_settings().secret_key
    if not raw:
        raise RuntimeError("FRED_SECRET_KEY is not set; refusing to store credentials.")
    # Accept any string; derive a stable 32-byte key from it.
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(raw.encode()).digest()))


def encrypt(secret: str) -> str:
    return _fernet().encrypt(secret.encode()).decode()


def decrypt(token: str) -> str:
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as e:
        raise RuntimeError("Stored credential cannot be decrypted (was FRED_SECRET_KEY changed?)") from e


def hint(secret: str) -> str:
    return "…" + secret[-4:] if len(secret) >= 8 else "…"
