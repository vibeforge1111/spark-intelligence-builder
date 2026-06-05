from __future__ import annotations

import base64
import os
from pathlib import Path

try:
    from cryptography.fernet import Fernet
    _CRYPTO_AVAILABLE = True
except ImportError:
    _CRYPTO_AVAILABLE = False

_KEY_FILE_NAME = ".spark-token-key"


def _key_path(state_dir: Path) -> Path:
    return state_dir / _KEY_FILE_NAME


def _load_or_create_key(state_dir: Path) -> bytes:
    path = _key_path(state_dir)
    if path.exists():
        return path.read_bytes()
    key = Fernet.generate_key()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(key)
    os.chmod(str(path), 0o600)
    return key


def _fernet(state_dir: Path) -> "Fernet":
    key = _load_or_create_key(state_dir)
    return Fernet(key)


def encrypt_token(state_dir: Path, plaintext: str) -> str:
    if not _CRYPTO_AVAILABLE:
        return plaintext
    return _fernet(state_dir).encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_token(state_dir: Path, value: str) -> str:
    if not _CRYPTO_AVAILABLE:
        return value
    try:
        return _fernet(state_dir).decrypt(value.encode("ascii")).decode("utf-8")
    except Exception:
        # Fallback: value may be legacy plaintext from before this fix
        return value
