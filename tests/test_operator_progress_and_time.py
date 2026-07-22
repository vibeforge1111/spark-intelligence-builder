from __future__ import annotations

from datetime import datetime, timezone

from spark_intelligence.adapters.telegram.runtime import _humanize_created_at_for_operator


def test_savepoint_time_is_human_readable_without_losing_exact_source() -> None:
    now = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)

    assert _humanize_created_at_for_operator("2026-07-22T11:57:00Z", now=now) == "3 minutes ago"
    assert _humanize_created_at_for_operator("not-a-time", now=now) == "not-a-time"
