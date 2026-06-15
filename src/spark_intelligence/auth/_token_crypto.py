from __future__ import annotations

import base64
import hashlib
import os

import nacl.secret
import nacl.utils


def _get_encryption_key() -> bytes:
    key_str = os.environ.get("SPARK_STATE_ENCRYPTION_KEY", "").strip()
    if not key_str:
        raise RuntimeError(
            "SPARK_STATE_ENCRYPTION_KEY is not set. "
            "OAuth tokens cannot be stored or retrieved without an encryption key."
        )
    return hashlib.sha256(key_str.encode("utf-8")).digest()


def encrypt_token(plaintext: str) -> str:
    """Encrypt a token string; returns base64-encoded nacl.SecretBox ciphertext."""
    box = nacl.secret.SecretBox(_get_encryption_key())
    return base64.b64encode(bytes(box.encrypt(plaintext.encode("utf-8")))).decode("ascii")


def decrypt_token(ciphertext: str) -> str:
    """Decrypt a base64-encoded nacl.SecretBox ciphertext; returns plaintext token."""
    box = nacl.secret.SecretBox(_get_encryption_key())
    return box.decrypt(base64.b64decode(ciphertext.encode("ascii"))).decode("utf-8")
