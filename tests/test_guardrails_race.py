from __future__ import annotations

import sqlite3
import tempfile
import threading
from pathlib import Path

import pytest

from spark_intelligence.gateway.guardrails import apply_inbound_rate_limit, is_duplicate_event
from spark_intelligence.state.db import StateDB


def _make_db() -> StateDB:
    tmp = tempfile.mkdtemp()
    db = StateDB(str(Path(tmp) / "test.db"))
    with db.connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS runtime_state (
                state_key TEXT PRIMARY KEY,
                value TEXT,
                component TEXT,
                guard_strategy TEXT,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.commit()
    return db


class TestIsDuplicateEventConcurrency:
    def test_sequential_duplicate_detected(self) -> None:
        db = _make_db()
        assert is_duplicate_event(state_db=db, channel_id="ch1", event_id=42, window_size=10) is False
        assert is_duplicate_event(state_db=db, channel_id="ch1", event_id=42, window_size=10) is True

    def test_sequential_unique_events_pass(self) -> None:
        db = _make_db()
        for i in range(5):
            assert is_duplicate_event(state_db=db, channel_id="ch2", event_id=i, window_size=10) is False

    def test_concurrent_same_event_only_first_passes(self) -> None:
        """Two concurrent calls with the same event_id — at most one should return False."""
        db = _make_db()
        results: list[bool] = []
        errors: list[Exception] = []

        def run() -> None:
            try:
                result = is_duplicate_event(
                    state_db=db, channel_id="ch3", event_id=99, window_size=10
                )
                results.append(result)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=run) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Unexpected errors: {errors}"
        # Exactly one thread should have seen it as NOT a duplicate (first to commit)
        assert results.count(False) == 1, f"Expected exactly 1 non-duplicate, got: {results}"
        assert results.count(True) == 4

    def test_transaction_rolls_back_cleanly_on_failure(self) -> None:
        """State DB with no runtime_state table should raise, not corrupt state."""
        tmp = tempfile.mkdtemp()
        empty_db = StateDB(str(Path(tmp) / "empty.db"))
        # The table doesn't exist — should raise, not silently corrupt
        with pytest.raises(Exception):
            is_duplicate_event(state_db=empty_db, channel_id="ch", event_id=1, window_size=10)


class TestApplyInboundRateLimitConcurrency:
    def test_sequential_within_limit(self) -> None:
        db = _make_db()
        for _ in range(3):
            result = apply_inbound_rate_limit(
                state_db=db,
                channel_id="ch4",
                external_user_id="u1",
                limit_per_minute=5,
                notice_cooldown_seconds=60,
            )
            assert result["allowed"] is True

    def test_sequential_exceeds_limit(self) -> None:
        db = _make_db()
        for _ in range(2):
            apply_inbound_rate_limit(
                state_db=db,
                channel_id="ch5",
                external_user_id="u2",
                limit_per_minute=2,
                notice_cooldown_seconds=60,
            )
        result = apply_inbound_rate_limit(
            state_db=db,
            channel_id="ch5",
            external_user_id="u2",
            limit_per_minute=2,
            notice_cooldown_seconds=60,
        )
        assert result["allowed"] is False

    def test_concurrent_requests_only_allowed_count_passes(self) -> None:
        """With limit=1, only one of many concurrent requests should be allowed."""
        db = _make_db()
        allowed: list[bool] = []
        errors: list[Exception] = []

        def run() -> None:
            try:
                result = apply_inbound_rate_limit(
                    state_db=db,
                    channel_id="ch6",
                    external_user_id="u3",
                    limit_per_minute=1,
                    notice_cooldown_seconds=60,
                )
                allowed.append(result["allowed"])
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=run) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Unexpected errors: {errors}"
        # With limit=1, at most 1 thread should be allowed through
        assert allowed.count(True) <= 1, f"Rate limit bypassed: {allowed}"
