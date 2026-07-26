from __future__ import annotations

from spark_intelligence.observability.guardrail_repair import resolve_expected_guardrail_denial_events
from spark_intelligence.observability.store import record_event
from spark_intelligence.state.db import StateDB


def test_resolves_expected_guardrail_denial_events_append_only(tmp_path) -> None:
    state_db = StateDB(tmp_path / "state.sqlite")
    state_db.initialize()
    record_event(
        state_db,
        event_type="tool_call_ledger_recorded",
        component="telegram_runtime",
        target_surface="telegram",
        evidence_lane="realworld_validated",
        request_id="telegram:test",
        trace_ref="trace:test",
        reason_code="tool_not_allowed_by_policy",
        severity="high",
        status="blocked",
        summary="Expected policy denial was previously recorded as high blocked.",
    )

    dry_run = resolve_expected_guardrail_denial_events(state_db)
    assert dry_run["applied"] is False
    assert dry_run["would_resolve_group_count"] == 1
    assert dry_run["resolved_group_count"] == 0

    applied = resolve_expected_guardrail_denial_events(state_db, apply=True)
    assert applied["applied"] is True
    assert applied["resolved_group_count"] == 1

    second = resolve_expected_guardrail_denial_events(state_db, apply=True)
    assert second["resolved_group_count"] == 0
    assert second["skipped_group_count"] == 1

    with state_db.connect() as conn:
        rows = conn.execute(
            """
            select status, severity
            from builder_events
            where component = 'telegram_runtime'
              and event_type = 'tool_call_ledger_recorded'
              and reason_code = 'tool_not_allowed_by_policy'
            order by datetime(created_at) desc, rowid desc
            """
        ).fetchall()
    assert (rows[0]["status"], rows[0]["severity"]) == ("recorded", "medium")
    assert (rows[1]["status"], rows[1]["severity"]) == ("blocked", "high")


def test_owner_mismatch_denial_is_not_repaired(tmp_path) -> None:
    state_db = StateDB(tmp_path / "state.sqlite")
    state_db.initialize()
    record_event(
        state_db,
        event_type="tool_call_ledger_recorded",
        component="telegram_runtime",
        target_surface="telegram",
        evidence_lane="realworld_validated",
        request_id="telegram:test",
        trace_ref="trace:test",
        reason_code="owner_mismatch",
        severity="high",
        status="blocked",
        summary="Integrity denial should remain high blocked.",
    )

    result = resolve_expected_guardrail_denial_events(state_db, apply=True)
    assert result["candidate_group_count"] == 0
    assert result["resolved_group_count"] == 0
