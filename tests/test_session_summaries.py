from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from spark_intelligence.memory import build_session_memory_summary, write_session_summary_to_memory
from spark_intelligence.observability.store import latest_events_by_type, record_event

from tests.test_support import SparkTestCase


class _FakeMemoryClient:
    def __init__(self) -> None:
        self.observation_calls: list[dict[str, object]] = []

    def write_observation(self, **payload):
        self.observation_calls.append(payload)
        return {
            "status": "accepted",
            "memory_role": str(payload.get("memory_role") or "structured_evidence"),
            "provenance": [{"memory_role": str(payload.get("memory_role") or "structured_evidence")}],
            "retrieval_trace": {"trace_id": "session-summary-write"},
        }


class SessionSummaryTests(SparkTestCase):
    def test_build_session_memory_summary_groups_ledger_events(self) -> None:
        session_id = "session:memory-summary"
        record_event(
            self.state_db,
            event_type="memory_write_requested",
            component="memory_orchestrator",
            summary="Spark saved a GTM launch decision.",
            session_id=session_id,
            human_id="human:test",
            agent_id="agent:test",
            facts={
                "repo_full_name": "vibeforge1111/spark-intelligence-builder",
                "artifact_path": "docs/memory-plan.md",
                "observations": [
                    {
                        "predicate": "entity.decision",
                        "value": "concise landing page first",
                    }
                ],
            },
        )
        record_event(
            self.state_db,
            event_type="memory_write_requested",
            component="memory_orchestrator",
            summary="Spark saved the next action.",
            session_id=session_id,
            human_id="human:test",
            agent_id="agent:test",
            facts={
                "observations": [
                    {
                        "predicate": "entity.next_action",
                        "value": "get creator signoff",
                    }
                ],
            },
        )
        record_event(
            self.state_db,
            event_type="delivery_failed",
            component="telegram_gateway",
            summary="Open question: who signs off creators?",
            session_id=session_id,
            human_id="human:test",
            agent_id="agent:test",
            reason_code="blocked_by_missing_approver",
            facts={"message_text": "Who signs off creators?"},
        )
        record_event(
            self.state_db,
            event_type="memory_write_requested",
            component="memory_orchestrator",
            summary="Unrelated session event.",
            session_id="session:other",
            human_id="human:test",
            facts={
                "observations": [
                    {
                        "predicate": "entity.decision",
                        "value": "do not include this",
                    }
                ],
            },
        )

        summary = build_session_memory_summary(state_db=self.state_db, session_id=session_id)

        self.assertEqual(summary.event_count, 3)
        self.assertEqual(summary.human_id, "human:test")
        self.assertEqual(summary.agent_id, "agent:test")
        self.assertTrue(any("concise landing page first" in item for item in summary.decisions))
        self.assertTrue(any("get creator signoff" in item for item in summary.next_actions))
        self.assertTrue(any("get creator signoff" in item for item in summary.promises_made))
        self.assertTrue(any("who signs off creators" in item.lower() for item in summary.open_questions))
        self.assertIn("vibeforge1111/spark-intelligence-builder", summary.repos_touched)
        self.assertIn("docs/memory-plan.md", summary.artifacts_created)
        self.assertEqual(len(summary.source_event_ids), 3)
        self.assertNotIn("do not include this", summary.to_text())

    def test_write_session_summary_to_memory_persists_structured_evidence(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)
        session_id = "session:summary-write"
        record_event(
            self.state_db,
            event_type="memory_write_requested",
            component="memory_orchestrator",
            summary="Spark saved a launch checklist next action.",
            session_id=session_id,
            human_id="human:test",
            agent_id="agent:test",
            facts={
                "observations": [
                    {
                        "predicate": "entity.next_action",
                        "value": "test Stripe recovery",
                    }
                ],
            },
        )
        fake_client = _FakeMemoryClient()
        empty_retrieval = SimpleNamespace(read_result=SimpleNamespace(abstained=True, records=[]))

        with patch("spark_intelligence.memory.orchestrator._load_sdk_client", return_value=fake_client), patch(
            "spark_intelligence.memory.orchestrator.retrieve_memory_evidence_in_memory",
            return_value=empty_retrieval,
        ):
            result = write_session_summary_to_memory(
                config_manager=self.config_manager,
                state_db=self.state_db,
                human_id="human:test",
                session_id=session_id,
                agent_id="agent:test",
                channel_kind="telegram",
            )

        self.assertEqual(result.status, "succeeded")
        self.assertEqual(result.accepted_count, 1)
        self.assertEqual(len(fake_client.observation_calls), 1)
        call = fake_client.observation_calls[0]
        self.assertEqual(call["predicate"], "evidence.telegram.session_summary")
        self.assertEqual(call["memory_role"], "structured_evidence")
        self.assertEqual(call["retention_class"], "episodic_archive")
        self.assertIn("test Stripe recovery", str(call["text"]))
        metadata = call["metadata"]
        self.assertEqual(metadata["domain_pack"], "session_summary")
        self.assertEqual(metadata["promotion_stage"], "structured_evidence")
        self.assertEqual(metadata["why_saved"], "session_summary")

        write_events = latest_events_by_type(self.state_db, event_type="memory_write_requested", limit=10)
        observations = [
            observation
            for event in write_events
            for observation in ((event["facts_json"] or {}).get("observations") or [])
            if observation.get("predicate") == "evidence.telegram.session_summary"
        ]
        self.assertEqual(observations[0]["predicate"], "evidence.telegram.session_summary")
        self.assertEqual(observations[0]["why_saved"], "session_summary")

        summary_events = latest_events_by_type(self.state_db, event_type="memory_session_summary_written", limit=10)
        self.assertTrue(summary_events)
        summary_facts = summary_events[0]["facts_json"] or {}
        self.assertEqual(summary_facts["domain_pack"], "session_summary")
        self.assertEqual(summary_facts["event_count"], 1)
        self.assertTrue(summary_facts["source_event_ids"])

    def test_write_session_summary_skips_empty_sessions(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        result = write_session_summary_to_memory(
            config_manager=self.config_manager,
            state_db=self.state_db,
            human_id="human:test",
            session_id="session:empty",
            channel_kind="telegram",
        )

        self.assertEqual(result.status, "skipped")
        self.assertEqual(result.reason, "no_session_events")
        skip_events = latest_events_by_type(self.state_db, event_type="memory_session_summary_skipped", limit=10)
        self.assertTrue(skip_events)
