from __future__ import annotations

from spark_intelligence.memory.orchestrator import (
    MemoryReadResult,
    MemoryWriteResult,
    _memory_trace_ref,
    _record_memory_read_event,
    _record_memory_read_requested_subject,
    _record_memory_write_event,
    _record_memory_write_requested_observations,
)
from spark_intelligence.observability.store import latest_events_by_type

from tests.test_support import SparkTestCase


class MemoryOrchestratorTraceContextTests(SparkTestCase):
    def test_memory_trace_ref_uses_session_scope_and_turn_id(self) -> None:
        self.assertEqual(
            _memory_trace_ref(session_id="memory-retrieval:context_capsule", turn_id="context_capsule:lookup"),
            "trace:memory-retrieval:context_capsule:lookup",
        )
        self.assertEqual(
            _memory_trace_ref(session_id="memory-retrieval:context_capsule", turn_id="trace:upstream:turn"),
            "trace:upstream:turn",
        )
        self.assertIsNone(_memory_trace_ref(session_id=None, turn_id=None))

    def test_memory_read_events_emit_trace_ref_from_turn_context(self) -> None:
        _record_memory_read_requested_subject(
            state_db=self.state_db,
            method="hybrid_memory_retrieve",
            subject="human:test",
            predicate="profile.current_focus",
            query="what should we focus on?",
            session_id="memory-retrieval:context_capsule",
            turn_id="context_capsule:lookup",
            actor_id="context_capsule",
        )
        _record_memory_read_event(
            state_db=self.state_db,
            result=MemoryReadResult(
                status="not_found",
                method="hybrid_memory_retrieve",
                memory_role="aggregate",
                records=[],
                provenance=[],
                retrieval_trace=None,
                answer_explanation=None,
                abstained=True,
                reason="not_found",
            ),
            human_id="human:test",
            session_id="memory-retrieval:context_capsule",
            turn_id="context_capsule:lookup",
            actor_id="context_capsule",
        )

        requested = latest_events_by_type(self.state_db, event_type="memory_read_requested", limit=1)
        abstained = latest_events_by_type(self.state_db, event_type="memory_read_abstained", limit=1)

        self.assertEqual(requested[0]["trace_ref"], "trace:memory-retrieval:context_capsule:lookup")
        self.assertEqual(abstained[0]["trace_ref"], "trace:memory-retrieval:context_capsule:lookup")

    def test_memory_write_request_events_emit_trace_ref_from_turn_context(self) -> None:
        _record_memory_write_requested_observations(
            state_db=self.state_db,
            operation="upsert",
            human_id="human:test",
            observations=[
                {
                    "subject": "human:test",
                    "predicate": "profile.current_focus",
                    "value": "trace repair",
                    "memory_role": "current_state",
                }
            ],
            session_id="memory-write:context_capsule",
            turn_id="context_capsule:write",
            actor_id="context_capsule",
        )

        events = latest_events_by_type(self.state_db, event_type="memory_write_requested", limit=1)

        self.assertEqual(events[0]["trace_ref"], "trace:memory-write:context_capsule:write")

    def test_memory_write_succeeded_events_emit_trace_ref_from_turn_context(self) -> None:
        _record_memory_write_event(
            state_db=self.state_db,
            result=MemoryWriteResult(
                status="ok",
                operation="upsert",
                method="write_observation",
                memory_role="current_state",
                accepted_count=1,
                rejected_count=0,
                skipped_count=0,
                abstained=False,
                retrieval_trace=None,
                provenance=[],
                reason=None,
            ),
            human_id="human:test",
            session_id="memory-write:context_capsule",
            turn_id="context_capsule:write",
            actor_id="context_capsule",
        )

        events = latest_events_by_type(self.state_db, event_type="memory_write_succeeded", limit=1)

        self.assertEqual(events[0]["trace_ref"], "trace:memory-write:context_capsule:write")
