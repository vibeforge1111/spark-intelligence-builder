from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from spark_intelligence.auth._token_crypto import decrypt_token, encrypt_token


TEST_KEY = "test-encryption-key-for-oauth-tests"


@pytest.fixture(autouse=True)
def _set_encryption_key(monkeypatch):
    monkeypatch.setenv("SPARK_STATE_ENCRYPTION_KEY", TEST_KEY)


def test_encrypt_then_decrypt_roundtrip():
    token = "ya29.a0AfH6SMB-test-access-token"
    assert decrypt_token(encrypt_token(token)) == token


def test_encrypted_value_is_not_plaintext():
    token = "secret-refresh-token-value"
    ciphertext = encrypt_token(token)
    assert token not in ciphertext
    assert "secret" not in ciphertext


def test_different_encryptions_of_same_token_produce_different_ciphertexts():
    token = "same-token"
    c1 = encrypt_token(token)
    c2 = encrypt_token(token)
    assert c1 != c2


def test_decrypt_raises_on_tampered_ciphertext():
    import base64

    token = "tamper-test-token"
    ciphertext = encrypt_token(token)
    raw = bytearray(base64.b64decode(ciphertext))
    raw[-1] ^= 0xFF
    tampered = base64.b64encode(bytes(raw)).decode("ascii")
    with pytest.raises(Exception):
        decrypt_token(tampered)


def test_missing_encryption_key_raises_runtime_error():
    with patch.dict(os.environ, {"SPARK_STATE_ENCRYPTION_KEY": ""}, clear=False):
        with pytest.raises(RuntimeError, match="SPARK_STATE_ENCRYPTION_KEY"):
            encrypt_token("any-token")
