from __future__ import annotations

from spark_intelligence.observability.store import latest_events_by_type, record_event
from spark_intelligence.researcher_bridge.trace_context import researcher_bridge_trace_ref

from tests.test_support import SparkTestCase


class ResearcherBridgeTraceRefTests(SparkTestCase):
    def test_researcher_bridge_trace_ref_helper(self) -> None:
        self.assertEqual(
            researcher_bridge_trace_ref(agent_id="agent-1", human_id="human-1", request_id="req-1"),
            "trace:agent-1:human-1:req-1",
        )
        self.assertEqual(
            researcher_bridge_trace_ref(agent_id=None, human_id=None, request_id=None),
            "trace:agent:human:unknown",
        )

    def test_researcher_bridge_events_can_emit_trace_ref(self) -> None:
        trace_ref = researcher_bridge_trace_ref(agent_id="hunt", human_id="tester", request_id="req-bridge")
        record_event(
            self.state_db,
            event_type="dispatch_started",
            component="researcher_bridge",
            summary="hunt trace ref probe",
            request_id="req-bridge",
            trace_ref=trace_ref,
            actor_id="researcher_bridge",
            reason_code="hunt_verify",
        )
        rows = [
            event
            for event in latest_events_by_type(self.state_db, event_type="dispatch_started", limit=5)
            if event.get("request_id") == "req-bridge"
        ]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["trace_ref"], "trace:hunt:tester:req-bridge")
