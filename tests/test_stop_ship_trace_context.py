from __future__ import annotations

from spark_intelligence.observability.checks import StopShipIssue, _reconcile_stop_ship_contradictions
from spark_intelligence.observability.store import latest_events_by_type

from tests.test_support import SparkTestCase


class StopShipTraceContextTests(SparkTestCase):
    def test_stop_ship_contradiction_events_emit_trace_ref(self) -> None:
        _reconcile_stop_ship_contradictions(
            state_db=self.state_db,
            issues=[
                StopShipIssue(
                    name="stop_ship_trace_context",
                    ok=False,
                    detail="trace context missing",
                    severity="high",
                )
            ],
        )

        open_events = latest_events_by_type(self.state_db, event_type="contradiction_recorded", limit=1)

        self.assertEqual(open_events[0]["request_id"], "stop_ship:stop_ship_trace_context")
        self.assertEqual(open_events[0]["trace_ref"], "trace:stop_ship:stop_ship_trace_context")

        _reconcile_stop_ship_contradictions(
            state_db=self.state_db,
            issues=[
                StopShipIssue(
                    name="stop_ship_trace_context",
                    ok=True,
                    detail="trace context restored",
                    severity="high",
                )
            ],
        )

        resolved_events = latest_events_by_type(self.state_db, event_type="contradiction_recorded", limit=5)
        resolved_event = next(event for event in resolved_events if event["status"] == "resolved")

        self.assertEqual(resolved_event["request_id"], "stop_ship:stop_ship_trace_context")
        self.assertEqual(resolved_event["trace_ref"], "trace:stop_ship:stop_ship_trace_context")
