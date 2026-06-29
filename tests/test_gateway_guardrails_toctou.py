"""Tests for the TOCTOU race fix in gateway guardrails.

Verifies that ``is_duplicate_event`` and ``apply_inbound_rate_limit`` behave
correctly under concurrent access by running multiple threads that attempt
to process the same event_id / user simultaneously.
"""

from __future__ import annotations

import json
import tempfile
import threading
from pathlib import Path

import pytest

from spark_intelligence.gateway.guardrails import (
    apply_inbound_rate_limit,
    is_duplicate_event,
)
from spark_intelligence.state.db import StateDB


@pytest.fixture()
def state_db(tmp_path: Path) -> StateDB:
    db = StateDB(tmp_path / "test.db")
    db.initialize()
    return db


class TestIsDuplicateEventTOCTOU:
    """is_duplicate_event must reject duplicates even under concurrency."""

    def test_single_call_basic(self, state_db: StateDB) -> None:
        assert is_duplicate_event(
            state_db=state_db,
            channel_id="ch1",
            event_id=100,
            window_size=10,
        ) is False
        assert is_duplicate_event(
            state_db=state_db,
            channel_id="ch1",
            event_id=100,
            window_size=10,
        ) is True

    def test_concurrent_same_event_only_one_passes(self, state_db: StateDB) -> None:
        """Exactly one thread should see the event as non-duplicate; the rest
        must see it as a duplicate."""
        event_id = 9999
        results: list[bool] = []
        lock = threading.Lock()

        def worker() -> None:
            dup = is_duplicate_event(
                state_db=state_db,
                channel_id="ch_concurrent",
                event_id=event_id,
                window_size=50,
            )
            with lock:
                results.append(dup)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Exactly one call should have returned False (non-duplicate)
        false_count = sum(1 for r in results if r is False)
        true_count = sum(1 for r in results if r is True)
        assert false_count == 1, f"Expected exactly 1 non-duplicate, got {false_count}"
        assert true_count == 9, f"Expected 9 duplicates, got {true_count}"

    def test_sequential_events_all_unique(self, state_db: StateDB) -> None:
        # Use window_size=20 so all 20 events are retained
        for i in range(20):
            assert is_duplicate_event(
                state_db=state_db,
                channel_id="ch_seq",
                event_id=i,
                window_size=20,
            ) is False
        # Now all 20 should be duplicates
        for i in range(20):
            assert is_duplicate_event(
                state_db=state_db,
                channel_id="ch_seq",
                event_id=i,
                window_size=20,
            ) is True


class TestApplyInboundRateLimitTOCTOU:
    """apply_inbound_rate_limit must enforce limits under concurrency."""

    def test_under_limit_allows(self, state_db: StateDB) -> None:
        result = apply_inbound_rate_limit(
            state_db=state_db,
            channel_id="ch_rl",
            external_user_id="user1",
            limit_per_minute=5,
            notice_cooldown_seconds=10,
        )
        assert result["allowed"] is True

    def test_over_limit_blocks(self, state_db: StateDB) -> None:
        for _ in range(5):
            result = apply_inbound_rate_limit(
                state_db=state_db,
                channel_id="ch_rl2",
                external_user_id="user1",
                limit_per_minute=5,
                notice_cooldown_seconds=10,
            )
            assert result["allowed"] is True
        # 6th should be blocked
        result = apply_inbound_rate_limit(
            state_db=state_db,
            channel_id="ch_rl2",
            external_user_id="user1",
            limit_per_minute=5,
            notice_cooldown_seconds=10,
        )
        assert result["allowed"] is False

    def test_concurrent_requests_respect_limit(self, state_db: StateDB) -> None:
        """With a limit of 5 and 10 concurrent requests, exactly 5 should be
        allowed and 5 should be blocked."""
        results: list[dict] = []
        lock = threading.Lock()

        def worker() -> None:
            res = apply_inbound_rate_limit(
                state_db=state_db,
                channel_id="ch_concurrent_rl",
                external_user_id="user_race",
                limit_per_minute=5,
                notice_cooldown_seconds=0,
            )
            with lock:
                results.append(res)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        allowed_count = sum(1 for r in results if r["allowed"] is True)
        blocked_count = sum(1 for r in results if r["allowed"] is False)
        assert allowed_count == 5, f"Expected exactly 5 allowed, got {allowed_count}"
        assert blocked_count == 5, f"Expected exactly 5 blocked, got {blocked_count}"

    def test_different_users_independent(self, state_db: StateDB) -> None:
        for _ in range(5):
            res = apply_inbound_rate_limit(
                state_db=state_db,
                channel_id="ch_ind",
                external_user_id="user_a",
                limit_per_minute=5,
                notice_cooldown_seconds=10,
            )
            assert res["allowed"] is True
        # user_b should still be allowed
        res = apply_inbound_rate_limit(
            state_db=state_db,
            channel_id="ch_ind",
            external_user_id="user_b",
            limit_per_minute=5,
            notice_cooldown_seconds=10,
        )
        assert res["allowed"] is True
