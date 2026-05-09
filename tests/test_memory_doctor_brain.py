from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from spark_intelligence.gateway.tracing import append_gateway_trace
from spark_intelligence.memory.doctor import run_memory_doctor
from spark_intelligence.observability.store import build_watchtower_snapshot, latest_events_by_type, record_event

from tests.test_support import SparkTestCase


class MemoryDoctorBrainTests(SparkTestCase):
    def test_memory_doctor_brain_panel_no_data_has_stable_intake_fields(self) -> None:
        panel = build_watchtower_snapshot(self.state_db)["panels"]["memory_doctor_brain"]

        self.assertEqual(panel["status"], "no_data")
        self.assertEqual(panel["intake_trigger_counts"], {})
        self.assertEqual(panel["intake_calibration_counts"], {})
        self.assertEqual(panel["previous_failure_signal_counts"], {})
        self.assertEqual(panel["root_cause_primary_gap_counts"], {})
        self.assertEqual(panel["root_cause_failure_layer_counts"], {})
        self.assertEqual(panel["recent_intake_triggers"], [])
        self.assertEqual(panel["recent_root_causes"], [])

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
        self.assertIn("benchmark", report.to_dict())
        self.assertLess(report.brain["coverage"]["score"], 100)
        gap_names = {gap["name"] for gap in report.brain["gaps"]}
        self.assertIn("gateway_trace_visibility_gap", gap_names)
        self.assertIn("llm_wiki_packet_visibility_gap", gap_names)
        self.assertIn("dashboard_movement_export_gap", gap_names)
        self.assertIn("Brain: visibility", report.to_telegram_text())
        self.assertIn("Benchmark:", report.to_telegram_text())
        self.assertTrue(report.brain["proactive_improvements"])
        self.assertEqual(report.brain["root_cause"]["status"], "clear")
        alignment = report.brain["creator_system_alignment"]
        self.assertEqual(alignment["schema_version"], "spark-creator-intent.v1")
        self.assertEqual(alignment["status"], "aligned_candidate")
        self.assertEqual(alignment["target_domain"], "memory-doctor")
        self.assertIn("specialization_path", alignment["artifact_targets"])
        self.assertIn("benchmark_gate", alignment["promotion_gates"])
        self.assertEqual(alignment["validation_issues"], [])
        brain_events = latest_events_by_type(self.state_db, event_type="memory_doctor_brain_evaluated", limit=1)
        self.assertEqual(len(brain_events), 1)
        self.assertEqual(brain_events[0]["facts_json"]["authority"], "observability_non_authoritative")
        self.assertIn("gateway_trace_lineage", brain_events[0]["facts_json"]["missing_senses"])
        self.assertEqual(brain_events[0]["facts_json"]["root_cause_status"], "clear")
        self.assertEqual(brain_events[0]["facts_json"]["creator_alignment_status"], "aligned_candidate")
        self.assertIn("specialization_path", brain_events[0]["facts_json"]["creator_alignment_artifact_targets"])
        self.assertEqual(brain_events[0]["facts_json"]["creator_alignment_validation_issue_count"], 0)

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

    def test_memory_doctor_maps_cross_session_channel_lineage(self) -> None:
        append_gateway_trace(
            self.config_manager,
            {
                "event": "telegram_update_processed",
                "channel_id": "telegram",
                "request_id": "req-old-session",
                "telegram_user_id": "human-1",
                "chat_id": "chat-1",
                "session_id": "session-old",
                "user_message_preview": "my name is pronounced like Gem",
                "bridge_mode": "external_configured",
                "routing_decision": "provider_fallback_chat",
            },
        )
        append_gateway_trace(
            self.config_manager,
            {
                "event": "telegram_update_processed",
                "channel_id": "telegram",
                "request_id": "req-current-session",
                "telegram_user_id": "human-1",
                "chat_id": "chat-1",
                "session_id": "session-current",
                "user_message_preview": "what name should you use for me now?",
                "bridge_mode": "external_configured",
                "routing_decision": "provider_fallback_chat",
            },
        )
        record_event(
            self.state_db,
            event_type="context_capsule_compiled",
            component="researcher_bridge",
            summary="Spark context capsule was compiled for the provider prompt.",
            request_id="req-current-session",
            human_id="human-1",
            facts={
                "source_counts": {"current_state": 1, "recent_conversation": 1},
                "source_ledger": [
                    {"source": "current_state", "present": True, "count": 1, "priority": 1, "role": "authority"},
                    {"source": "recent_conversation", "present": True, "count": 1, "priority": 8, "role": "supporting"},
                ],
            },
        )

        report = run_memory_doctor(
            self.state_db,
            config_manager=self.config_manager,
            human_id="human-1",
        )

        cross_scope = report.context_capsule["gateway_trace"]["cross_scope_lineage"]
        self.assertEqual(cross_scope["status"], "checked")
        self.assertEqual(cross_scope["identity_key"], "telegram_user_id")
        self.assertEqual(cross_scope["session_count"], 2)
        self.assertEqual(cross_scope["channel_count"], 1)
        self.assertTrue(cross_scope["cross_session_visible"])
        self.assertFalse(cross_scope["cross_channel_visible"])
        self.assertEqual(
            cross_scope["recent_cross_session_messages"][0]["user_message_preview"],
            "my name is pronounced like Gem",
        )
        stages = {stage["stage"]: stage for stage in report.movement_trace["stages"]}
        self.assertEqual(stages["cross_session_channel_lineage"]["session_count"], 2)
        senses = {sense["name"]: sense for sense in report.brain["senses"]}
        self.assertTrue(senses["cross_session_channel_lineage"]["present"])
        self.assertIn("Lineage scope: 2 session(s), 1 channel(s) visible.", report.to_telegram_text())

    def test_memory_doctor_brain_maps_telegram_intake_lineage(self) -> None:
        append_gateway_trace(
            self.config_manager,
            {
                "event": "telegram_update_processed",
                "channel_id": "telegram",
                "request_id": "req-blank-seed",
                "telegram_user_id": "human-1",
                "chat_id": "chat-1",
                "session_id": "session-blank",
                "user_message_preview": "The phrase is Cedar Compass 509.",
                "response_preview": "Noted.",
                "bridge_mode": "external_configured",
                "routing_decision": "provider_fallback_chat",
            },
        )
        append_gateway_trace(
            self.config_manager,
            {
                "event": "telegram_update_processed",
                "channel_id": "telegram",
                "request_id": "req-blank-target",
                "telegram_user_id": "human-1",
                "chat_id": "chat-1",
                "session_id": "session-blank",
                "user_message_preview": "What phrase did I just give you?",
                "response_preview": "I do not have the previous message in context.",
                "bridge_mode": "external_configured",
                "routing_decision": "researcher_advisory",
            },
        )
        append_gateway_trace(
            self.config_manager,
            {
                "event": "telegram_update_processed",
                "channel_id": "telegram",
                "request_id": "req-blank-doctor",
                "telegram_user_id": "human-1",
                "chat_id": "chat-1",
                "session_id": "session-blank",
                "user_message_preview": "i just told you",
                "response_preview": "Memory Doctor: needs attention.",
                "bridge_mode": "runtime_command",
                "routing_decision": "runtime_command",
                "runtime_command": "/memory doctor",
                "runtime_command_metadata": {
                    "diagnosed_request_id": "req-blank-target",
                    "request_selector": "previous_gateway_turn",
                    "contextual_trigger_score": 4,
                    "contextual_trigger_threshold": 3,
                    "contextual_trigger_signals": [
                        "close_turn_repeat_frustration",
                        "previous_turn_memory_failure_signal",
                    ],
                    "previous_failure_signal": True,
                    "previous_failure_signals": ["previous_response_context_gap"],
                    "memory_doctor_ok": False,
                },
            },
        )
        record_event(
            self.state_db,
            event_type="context_capsule_compiled",
            component="researcher_bridge",
            summary="Spark context capsule was compiled for a blankness target.",
            request_id="req-blank-target",
            human_id="human-1",
            facts={
                "source_counts": {"recent_conversation": 0},
                "source_ledger": [
                    {"source": "recent_conversation", "present": False, "count": 0, "priority": 8, "role": "supporting"}
                ],
            },
        )

        report = run_memory_doctor(
            self.state_db,
            config_manager=self.config_manager,
            human_id="human-1",
            request_id="req-blank-target",
        )

        gateway_trace = report.context_capsule["gateway_trace"]
        stages = {stage["stage"]: stage for stage in report.movement_trace["stages"]}
        self.assertEqual(gateway_trace["diagnostic_invocation_count"], 1)
        self.assertEqual(gateway_trace["diagnostic_invocations"][0]["request_id"], "req-blank-doctor")
        self.assertEqual(gateway_trace["diagnostic_invocations"][0]["contextual_trigger_score"], 4)
        self.assertEqual(gateway_trace["diagnostic_invocations"][0]["contextual_trigger_threshold"], 3)
        self.assertEqual(stages["memory_doctor_intake"]["status"], "checked")
        self.assertEqual(stages["memory_doctor_intake"]["diagnostic_invocation_count"], 1)
        self.assertEqual(stages["memory_doctor_intake"]["contextual_trigger_count"], 1)
        self.assertEqual(stages["memory_doctor_intake"]["previous_failure_signal_count"], 1)
        self.assertEqual(stages["memory_doctor_intake"]["latest_request_id"], "req-blank-doctor")
        self.assertEqual(stages["memory_doctor_intake"]["latest_contextual_trigger_score"], 4)
        self.assertIn(
            "close_turn_repeat_frustration",
            gateway_trace["diagnostic_invocations"][0]["contextual_trigger_signals"],
        )
        self.assertTrue(gateway_trace["diagnostic_invocations"][0]["previous_failure_signal"])
        self.assertEqual(
            gateway_trace["diagnostic_invocations"][0]["previous_failure_signals"],
            ["previous_response_context_gap"],
        )
        brain_events = latest_events_by_type(self.state_db, event_type="memory_doctor_brain_evaluated", limit=1)
        telegram_intake = brain_events[0]["facts_json"]["telegram_intake"]
        self.assertEqual(telegram_intake["request_id"], "req-blank-doctor")
        self.assertEqual(telegram_intake["contextual_trigger_threshold"], 3)
        self.assertEqual(telegram_intake["previous_failure_signals"], ["previous_response_context_gap"])
        self.assertIn("close_turn_repeat_frustration", telegram_intake["contextual_trigger_signals"])
        self.assertEqual(report.brain["root_cause"]["primary_gap"], "context_capsule_gateway_trace_gap")
        self.assertEqual(report.brain["root_cause"]["failure_layer"], "context_ingress")
        self.assertEqual(brain_events[0]["facts_json"]["root_cause_primary_gap"], "context_capsule_gateway_trace_gap")
        self.assertEqual(brain_events[0]["facts_json"]["root_cause_failure_layer"], "context_ingress")
        senses = {sense["name"]: sense for sense in report.brain["senses"]}
        self.assertTrue(senses["telegram_doctor_intake_lineage"]["present"])
        self.assertTrue(senses["root_cause_classification"]["present"])
        cases = {case["category"]: case for case in report.benchmark["cases"]}
        self.assertEqual(cases["doctor_intake"]["status"], "pass")

    def test_watchtower_tracks_memory_doctor_brain_trends(self) -> None:
        record_event(
            self.state_db,
            event_type="memory_doctor_brain_evaluated",
            component="memory_doctor",
            summary="Memory Doctor Brain evaluated diagnostic coverage score=45 gaps=3",
            human_id="human-1",
            facts={
                "authority": "observability_non_authoritative",
                "coverage_score": 45,
                "missing_senses": ["gateway_trace_lineage", "llm_wiki_health"],
                "gap_names": ["gateway_trace_visibility_gap", "llm_wiki_packet_visibility_gap"],
                "highest_severity": "high",
                "next_probe": "check memory for Maya",
                "topic": "Maya",
            },
        )
        with self.state_db.connect() as conn:
            conn.execute(
                """
                UPDATE builder_events
                SET created_at = ?
                WHERE event_type = ? AND summary = ?
                """,
                (
                    "2026-01-01T00:00:00.000000+00:00",
                    "memory_doctor_brain_evaluated",
                    "Memory Doctor Brain evaluated diagnostic coverage score=45 gaps=3",
                ),
            )
            conn.commit()
        record_event(
            self.state_db,
            event_type="memory_doctor_brain_evaluated",
            component="memory_doctor",
            summary="Memory Doctor Brain evaluated diagnostic coverage score=70 gaps=1",
            human_id="human-1",
            facts={
                "authority": "observability_non_authoritative",
                "coverage_score": 70,
                "missing_senses": ["gateway_trace_lineage"],
                "gap_names": ["gateway_trace_visibility_gap"],
                "highest_severity": "medium",
                "next_probe": "run memory doctor after the next Telegram turn",
                "topic": "Maya",
                "request_id": "req-blank-target",
                "root_cause_status": "identified",
                "root_cause_primary_gap": "context_capsule_gateway_trace_gap",
                "root_cause_failure_layer": "context_ingress",
                "root_cause_chain": ["telegram_gateway", "context_capsule", "provider_context"],
                "root_cause_confidence": "high",
                "root_cause_summary": "gateway -> provider context gap",
                "creator_alignment_status": "aligned_candidate",
                "creator_alignment_artifact_targets": ["domain_chip", "benchmark_pack", "specialization_path"],
                "creator_alignment_validation_issue_count": 0,
                "telegram_intake": {
                    "request_id": "req-blank-doctor",
                    "user_message_preview": "i just told you",
                    "request_selector": "previous_gateway_turn",
                    "contextual_trigger_score": 4,
                    "contextual_trigger_threshold": 3,
                    "contextual_trigger_signals": [
                        "close_turn_repeat_frustration",
                        "previous_turn_memory_failure_signal",
                    ],
                    "previous_failure_signal": True,
                    "previous_failure_signals": ["previous_response_context_gap"],
                },
            },
        )

        panel = build_watchtower_snapshot(self.state_db)["panels"]["memory_doctor_brain"]

        self.assertEqual(panel["panel"], "memory_doctor_brain")
        self.assertEqual(panel["status"], "watching")
        self.assertEqual(panel["authority"], "observability_non_authoritative")
        self.assertEqual(panel["counts"]["evaluations"], 2)
        self.assertEqual(panel["score"]["latest"], 70)
        self.assertEqual(panel["score"]["delta"], 25)
        self.assertEqual(panel["repeated_missing_senses"]["gateway_trace_lineage"], 2)
        self.assertEqual(panel["repeated_gaps"]["gateway_trace_visibility_gap"], 2)
        self.assertEqual(panel["intake_trigger_counts"]["close_turn_repeat_frustration"], 1)
        self.assertEqual(panel["intake_calibration_counts"]["previous_turn_boosted"], 1)
        self.assertEqual(panel["previous_failure_signal_counts"]["previous_response_context_gap"], 1)
        self.assertEqual(panel["root_cause_primary_gap_counts"]["context_capsule_gateway_trace_gap"], 1)
        self.assertEqual(panel["root_cause_failure_layer_counts"]["context_ingress"], 1)
        self.assertEqual(panel["creator_alignment"]["status"], "aligned_candidate")
        self.assertIn("specialization_path", panel["creator_alignment"]["artifact_targets"])
        self.assertEqual(panel["creator_alignment"]["validation_issue_count"], 0)
        self.assertEqual(panel["recent_intake_triggers"][0]["doctor_request_id"], "req-blank-doctor")
        self.assertEqual(panel["recent_intake_triggers"][0]["diagnosed_request_id"], "req-blank-target")
        self.assertEqual(panel["recent_intake_triggers"][0]["contextual_trigger_margin"], 1)
        self.assertEqual(panel["recent_intake_triggers"][0]["calibration_label"], "previous_turn_boosted")
        self.assertEqual(panel["recent_intake_triggers"][0]["previous_failure_signals"], ["previous_response_context_gap"])
        self.assertEqual(panel["recent_root_causes"][0]["failure_layer"], "context_ingress")
        self.assertEqual(panel["recent_root_causes"][0]["summary"], "gateway -> provider context gap")
        self.assertEqual(panel["latest"]["root_cause"]["primary_gap"], "context_capsule_gateway_trace_gap")
        self.assertEqual(panel["latest"]["topic"], "Maya")
