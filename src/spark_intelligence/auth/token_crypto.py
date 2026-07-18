from __future__ import annotations

import base64
import os
import sqlite3
import stat
from pathlib import Path

from nacl.exceptions import CryptoError
from nacl.secret import SecretBox
from nacl.utils import random as random_bytes


_ENVELOPE_PREFIX = "spark.oauth.v1:"
_ENVELOPE_FAMILY_PREFIX = "spark.oauth."
_KEY_FILE_NAME = ".spark-oauth-token.key"


def encrypt_token(state_dir: Path, plaintext: str) -> str:
    if not isinstance(plaintext, str) or not plaintext:
        raise RuntimeError("OAuth token encryption requires a non-empty token.")
    key = _load_or_create_key(state_dir)
    return _encrypt_with_key(key, plaintext)


def decrypt_token(state_dir: Path, value: str) -> str:
    if not value.startswith(_ENVELOPE_PREFIX):
        raise RuntimeError(
            "OAuth token store contains an unsupported token envelope. Reconnect the provider."
        )
    key = _load_existing_key(state_dir)
    return _decrypt_with_key(key, value)


def migrate_and_validate_oauth_tokens(
    conn: sqlite3.Connection,
    state_dir: Path,
) -> int:
    rows = conn.execute(
        """
        SELECT auth_profile_id, access_token_ciphertext, refresh_token_ciphertext
        FROM oauth_credentials
        WHERE access_token_ciphertext IS NOT NULL
           OR refresh_token_ciphertext IS NOT NULL
        ORDER BY auth_profile_id
        """
    ).fetchall()
    if not rows:
        return 0

    encrypted_values: list[str] = []
    has_legacy = False
    for row in rows:
        for column in ("access_token_ciphertext", "refresh_token_ciphertext"):
            raw = row[column]
            if raw is None or str(raw) == "":
                continue
            value = str(raw)
            if value.startswith(_ENVELOPE_PREFIX):
                encrypted_values.append(value)
            elif value.startswith(_ENVELOPE_FAMILY_PREFIX):
                raise RuntimeError(
                    "OAuth token store contains an unsupported token envelope. Reconnect the provider."
                )
            else:
                has_legacy = True

    key: bytes | None = None
    if encrypted_values:
        key = _load_existing_key(state_dir)
        for value in encrypted_values:
            _decrypt_with_key(key, value)
    if not has_legacy:
        return 0
    if key is None:
        key = _load_or_create_key(state_dir)

    migrated = 0
    for row in rows:
        access_value = _migrate_value(key, row["access_token_ciphertext"])
        refresh_value = _migrate_value(key, row["refresh_token_ciphertext"])
        if (
            access_value == row["access_token_ciphertext"]
            and refresh_value == row["refresh_token_ciphertext"]
        ):
            continue
        conn.execute(
            """
            UPDATE oauth_credentials
            SET access_token_ciphertext = ?,
                refresh_token_ciphertext = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE auth_profile_id = ?
            """,
            (access_value, refresh_value, str(row["auth_profile_id"])),
        )
        migrated += 1
    return migrated


def key_path(state_dir: Path) -> Path:
    return state_dir / _KEY_FILE_NAME


def _migrate_value(key: bytes, raw: object) -> object:
    if raw is None or str(raw) == "":
        return raw
    value = str(raw)
    if value.startswith(_ENVELOPE_PREFIX):
        return value
    return _encrypt_with_key(key, value)


def _encrypt_with_key(key: bytes, plaintext: str) -> str:
    encrypted = bytes(SecretBox(key).encrypt(plaintext.encode("utf-8")))
    payload = base64.urlsafe_b64encode(encrypted).decode("ascii")
    return f"{_ENVELOPE_PREFIX}{payload}"


def _decrypt_with_key(key: bytes, value: str) -> str:
    try:
        encoded = value[len(_ENVELOPE_PREFIX) :]
        encrypted = base64.b64decode(encoded.encode("ascii"), altchars=b"-_", validate=True)
        return SecretBox(key).decrypt(encrypted).decode("utf-8")
    except (ValueError, UnicodeError, CryptoError):
        raise RuntimeError(
            "OAuth token store could not be decrypted safely. Reconnect the provider."
        ) from None


def _load_or_create_key(state_dir: Path) -> bytes:
    path = key_path(state_dir)
    try:
        return _read_key(path)
    except FileNotFoundError:
        pass

    state_dir.mkdir(parents=True, exist_ok=True)
    key = random_bytes(SecretBox.KEY_SIZE)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
    except FileExistsError:
        return _load_existing_key(state_dir)
    except OSError:
        raise RuntimeError(
            "OAuth token encryption key could not be created safely."
        ) from None

    try:
        view = memoryview(key)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short key write")
            view = view[written:]
        os.fsync(descriptor)
    except OSError:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        raise RuntimeError(
            "OAuth token encryption key could not be created safely."
        ) from None
    finally:
        os.close(descriptor)

    if os.name != "nt":
        os.chmod(path, 0o600)
    return key


def _load_existing_key(state_dir: Path) -> bytes:
    try:
        return _read_key(key_path(state_dir))
    except FileNotFoundError:
        raise RuntimeError(
            "OAuth token encryption key is missing. Restore the workspace key or reconnect the provider."
        ) from None


def _read_key(path: Path) -> bytes:
    if path.is_symlink():
        raise RuntimeError("OAuth token encryption key must not be a symbolic link.")
    try:
        metadata = path.stat()
        data = path.read_bytes()
    except FileNotFoundError:
        raise
    except OSError:
        raise RuntimeError("OAuth token encryption key could not be read safely.") from None
    if not stat.S_ISREG(metadata.st_mode):
        raise RuntimeError("OAuth token encryption key must be a regular file.")
    if os.name != "nt" and stat.S_IMODE(metadata.st_mode) & 0o077:
        raise RuntimeError("OAuth token encryption key permissions are too broad.")
    if len(data) != SecretBox.KEY_SIZE:
        raise RuntimeError("OAuth token encryption key has an invalid format.")
    return data
