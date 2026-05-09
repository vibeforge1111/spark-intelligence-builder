from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from spark_intelligence.gateway.tracing import append_gateway_trace
from spark_intelligence.memory.doctor import run_memory_doctor
from spark_intelligence.observability.store import latest_events_by_type, record_event

from tests.test_support import SparkTestCase


class MemoryDoctorBrainTests(SparkTestCase):
    def test_memory_doctor_brain_reports_trace_coverage_and_proactive_gaps(self) -> None:
        record_event(
            self.state_db,
            event_type="context_capsule_compiled",
            component="researcher_bridge",
            summary="Spark context capsule was compiled for the provider prompt.",
            request_id="req-brain-coverage",
            human_id="human-1",
            facts={
                "source_counts": {"current_state": 1, "recent_conversation": 0},
                "source_ledger": [
                    {"source": "current_state", "present": True, "count": 1, "priority": 1, "role": "authority"},
                    {"source": "recent_conversation", "present": False, "count": 0, "priority": 8, "role": "supporting"},
                ],
            },
        )

        with patch(
            "spark_intelligence.memory.doctor_brain.memory_orchestrator.inspect_wiki_packet_metadata",
            return_value={
                "status": "not_configured",
                "reason": "wiki_packet_paths_not_configured",
                "packet_count": 0,
                "source_families_visible": False,
                "memory_kb": {"present": False, "packet_count": 0, "family_counts": {}},
            },
        ), patch(
            "spark_intelligence.memory.doctor_brain.memory_orchestrator.inspect_memory_movement_status",
            return_value={"status": "unavailable", "reason": "sdk_unavailable", "row_count": 0},
        ):
            report = run_memory_doctor(
                self.state_db,
                config_manager=self.config_manager,
                human_id="human-1",
                topic="Maya",
            )

        self.assertIn("brain", report.to_dict())
        self.assertLess(report.brain["coverage"]["score"], 100)
        gap_names = {gap["name"] for gap in report.brain["gaps"]}
        self.assertIn("gateway_trace_visibility_gap", gap_names)
        self.assertIn("llm_wiki_packet_visibility_gap", gap_names)
        self.assertIn("dashboard_movement_export_gap", gap_names)
        self.assertIn("Brain: visibility", report.to_telegram_text())
        self.assertTrue(report.brain["proactive_improvements"])
        brain_events = latest_events_by_type(self.state_db, event_type="memory_doctor_brain_evaluated", limit=1)
        self.assertEqual(len(brain_events), 1)
        self.assertEqual(brain_events[0]["facts_json"]["authority"], "observability_non_authoritative")
        self.assertIn("gateway_trace_lineage", brain_events[0]["facts_json"]["missing_senses"])

    def test_memory_doctor_brain_uses_sdk_wiki_movement_and_llm_wiki_senses(self) -> None:
        (self.home / "wiki").mkdir()
        append_gateway_trace(
            self.config_manager,
            {
                "event": "telegram_update_processed",
                "channel_id": "telegram",
                "request_id": "req-brain-context",
                "telegram_user_id": "human-1",
                "chat_id": "chat-1",
                "session_id": "session-brain",
                "user_message_preview": "what did I just tell you?",
                "bridge_mode": "external_configured",
                "routing_decision": "provider_fallback_chat",
            },
        )
        record_event(
            self.state_db,
            event_type="context_capsule_compiled",
            component="researcher_bridge",
            summary="Spark context capsule was compiled for the provider prompt.",
            request_id="req-brain-context",
            human_id="human-1",
            facts={
                "source_counts": {"current_state": 1, "recent_conversation": 1},
                "source_ledger": [
                    {"source": "current_state", "present": True, "count": 1, "priority": 1, "role": "authority"},
                    {"source": "recent_conversation", "present": True, "count": 1, "priority": 8, "role": "supporting"},
                ],
            },
        )

        wiki_status = SimpleNamespace(
            payload={
                "healthy": True,
                "output_dir": str(self.home / "wiki"),
                "markdown_page_count": 5,
                "wiki_retrieval_status": "supported",
                "wiki_record_count": 3,
                "memory_kb_discovery": {"present": True},
                "freshness_health": {"stale_page_count": 0},
                "warnings": [],
                "authority": "supporting_not_authoritative",
            }
        )
        with patch(
            "spark_intelligence.memory.doctor_brain.memory_orchestrator.inspect_memory_sdk_runtime",
            return_value={"ready": True, "client_kind": "FakeMemoryClient", "status": "ready"},
        ), patch(
            "spark_intelligence.memory.doctor_brain.memory_orchestrator.inspect_wiki_packet_metadata",
            return_value={
                "status": "supported",
                "packet_count": 3,
                "source_families_visible": True,
                "wiki_family_counts": {"memory_kb_current_state": 1},
                "memory_kb": {
                    "present": True,
                    "packet_count": 1,
                    "family_counts": {"memory_kb_current_state": 1},
                },
            },
        ), patch(
            "spark_intelligence.memory.doctor_brain.memory_orchestrator.inspect_memory_movement_status",
            return_value={
                "status": "supported",
                "row_count": 4,
                "movement_counts": {"captured": 2, "recalled": 2},
                "authority": "observability_non_authoritative",
            },
        ), patch("spark_intelligence.llm_wiki.status.build_llm_wiki_status", return_value=wiki_status):
            report = run_memory_doctor(
                self.state_db,
                config_manager=self.config_manager,
                human_id="human-1",
            )

        senses = {sense["name"]: sense for sense in report.brain["senses"]}
        self.assertTrue(senses["memory_sdk_runtime"]["present"])
        self.assertTrue(senses["dashboard_movement_export"]["present"])
        self.assertTrue(senses["llm_wiki_packet_metadata"]["present"])
        self.assertTrue(senses["llm_wiki_health"]["present"])
        self.assertEqual(report.brain["wiki_packets"]["packet_count"], 3)
        self.assertEqual(report.brain["llm_wiki"]["wiki_record_count"], 3)
        gap_names = {gap["name"] for gap in report.brain["gaps"]}
        self.assertNotIn("llm_wiki_packet_visibility_gap", gap_names)
        self.assertNotIn("dashboard_movement_export_gap", gap_names)
