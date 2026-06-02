"""Field-level encryption for the credentials store.

Fernet (AES-128-CBC + HMAC) with a key derived from `JOS_ENCRYPTION_KEY`.
A tenant's stored API keys are encrypted on write and transparently
decrypted on read inside credentials.py — endpoints + UI never see
the ciphertext.

Backward compatibility: existing rows are plaintext. `decrypt()`
detects ciphertext by prefix (`fernet:`) and passes plaintext through
untouched. The first save on a field rewrites it as ciphertext, so
the store self-migrates as the user re-saves keys.

Honest scope:
  * Key lives in env. Real prod (Phase 2) wraps this with KMS so the
    env doesn't carry the master key.
  * Single key across all tenants. Phase 2 splits per-tenant keys
    (KEK/DEK pattern) so a tenant-export doesn't expose every other
    tenant's secrets.
"""

from __future__ import annotations

import base64
import hashlib
import os

from cryptography.fernet import Fernet, InvalidToken


_CIPHER_PREFIX = "fernet:"


def _key() -> bytes:
    """32-byte Fernet key from env. Falls back to a derived key in dev
    so signup-during-development doesn't require setting yet another
    env var. Set JOS_ENCRYPTION_KEY=$(openssl rand -base64 32) in prod."""
    raw = (os.environ.get("JOS_ENCRYPTION_KEY") or "").strip()
    if raw:
        # Accept either a Fernet-style urlsafe-b64 key or raw 32 bytes
        # that we'll convert.
        try:
            decoded = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
            if len(decoded) == 32:
                return base64.urlsafe_b64encode(decoded)
        except (ValueError, base64.binascii.Error):
            pass
        return base64.urlsafe_b64encode(hashlib.sha256(raw.encode()).digest())
    # Dev fallback — derive from the db URL.
    from .config import settings
    src = (settings.database_url or "no-db")[:120].encode()
    if not getattr(_key, "_warned", False):
        print(
            "[encryption] WARN: JOS_ENCRYPTION_KEY not set; using "
            "dev-derived key. Set this in prod or every restart with "
            "a different DB URL invalidates stored API keys."
        )
        _key._warned = True  # type: ignore[attr-defined]
    return base64.urlsafe_b64encode(hashlib.sha256(src).digest())


_fernet: Fernet | None = None


def _f() -> Fernet:
    global _fernet
    if _fernet is None:
        _fernet = Fernet(_key())
    return _fernet


def encrypt(plain: str) -> str:
    """Encrypt a string; returns ciphertext prefixed with 'fernet:' so
    decrypt() can detect it. Empty strings pass through (no point
    encrypting nothing)."""
    if not plain:
        return ""
    token = _f().encrypt(plain.encode("utf-8")).decode("ascii")
    return _CIPHER_PREFIX + token


def decrypt(value: str) -> str:
    """Decrypt if ciphertext; pass through plaintext (back-compat).
    Returns '' for falsy inputs."""
    if not value:
        return ""
    if not value.startswith(_CIPHER_PREFIX):
        # Legacy plaintext row — return as-is so existing keys still
        # work; the next save will rewrite as ciphertext.
        return value
    try:
        return _f().decrypt(value[len(_CIPHER_PREFIX):].encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        # Corrupted or wrong-key — fail closed (empty) rather than
        # surface the ciphertext to the caller.
        return ""


def is_encrypted(value: str) -> bool:
    return isinstance(value, str) and value.startswith(_CIPHER_PREFIX)


__all__ = ["encrypt", "decrypt", "is_encrypted"]
