from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _set_spark_state_encryption_key(monkeypatch):
    """Set a deterministic encryption key for all tests.

    Tests that exercise the OAuth token storage/retrieval path now need
    SPARK_STATE_ENCRYPTION_KEY to be set — _token_crypto raises RuntimeError
    when the variable is absent. A fixed test-only value is fine here because
    no real token data is involved.
    """
    monkeypatch.setenv("SPARK_STATE_ENCRYPTION_KEY", "test-only-key-do-not-use-in-production")
