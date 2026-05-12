from __future__ import annotations

from spark_intelligence.self_awareness import build_agent_operating_context
from spark_intelligence.self_awareness.route_confidence import build_route_confidence
from spark_intelligence.self_awareness.route_confidence_doctrine import build_route_confidence_doctrine
from spark_intelligence.self_awareness.route_confidence_gate import build_route_confidence_gate

from tests.test_support import SparkTestCase


class RouteConfidenceTests(SparkTestCase):
    def test_route_confidence_doctrine_defines_agency_not_llm_confidence(self) -> None:
        doctrine = build_route_confidence_doctrine()

        self.assertEqual(doctrine["schema_version"], "spark.route_confidence_doctrine.v1")
        self.assertEqual(doctrine["owner_system"], "spark-intelligence-builder")
        self.assertIn("justified", doctrine["definition"])
        self.assertIn("not LLM answer confidence", doctrine["not_definition"])
        self.assertEqual(doctrine["decision_values"], ["act", "ask", "explain", "refuse"])
        self.assertIn("explicit_no_execution_wins_over_action_keywords", doctrine["hard_precedence_rules"])
        self.assertIn("bare_go_only_applies_to_active_pending_action", doctrine["hard_precedence_rules"])
        self.assertIn("live_health", doctrine["deterministic_surfaces"])
        self.assertIn("brainstorming", doctrine["contextual_surfaces"])

    def test_route_confidence_doctrine_keeps_regression_prompts(self) -> None:
        cases = {item["id"]: item for item in build_route_confidence_doctrine()["regression_cases"]}

        self.assertEqual(cases["latest_constraint_wins"]["expected_decision"], "explain")
        self.assertEqual(cases["action_keywords_with_prohibition"]["expected_decision"], "explain")
        self.assertEqual(cases["bare_go_requires_active_pending_action"]["expected_decision"], "act_only_if_pending_action_exists")
        self.assertEqual(cases["global_agent_change_is_proposal"]["expected_decision"], "explain_or_ask")
        self.assertEqual(cases["bounded_no_edit_mission"]["expected_decision"], "act_if_spawner_permission_and_capability_pass")

    def test_spawner_route_confidence_uses_runner_and_route_health(self) -> None:
        report = build_route_confidence(
            task_fit={
                "recommended_route": "writable_spawner_codex_mission",
                "blocked_here_by": ["current_runner_read_only"],
                "why": ["The request needs local code or file work."],
            },
            runner={"writable": False, "label": "read-only chat runner"},
            access={"label": "Level 4 - sandboxed workspace allowed", "local_workspace_allowed": True},
            routes=[
                {
                    "key": "spark_spawner",
                    "status": "healthy",
                    "available": True,
                    "last_success_at": "2026-05-09T10:00:00Z",
                }
            ],
        )

        payload = report.to_payload()
        self.assertEqual(payload["confidence"], "high")
        self.assertGreaterEqual(payload["score"], 80)
        self.assertIn("current_runner_read_only", payload["risks"])
        self.assertTrue(any("Spawner route is healthy" in item for item in payload["evidence"]))

    def test_missing_access_marks_route_confidence_blocked(self) -> None:
        report = build_route_confidence(
            task_fit={
                "recommended_route": "ask_for_access_or_route",
                "blocked_here_by": ["local_workspace_access_unknown_or_denied"],
                "why": ["The request appears to need local workspace work."],
            },
            runner={"writable": None, "label": "unknown"},
            access={"label": "unknown", "local_workspace_allowed": False},
            routes=[],
        )

        self.assertEqual(report.confidence, "blocked")
        self.assertIn("local_workspace_access_not_confirmed", report.risks)

    def test_agent_operating_context_includes_route_confidence(self) -> None:
        context = build_agent_operating_context(
            config_manager=self.config_manager,
            state_db=self.state_db,
            user_message="patch the mission memory loop",
            spark_access_level="4",
            runner_writable=False,
            runner_label="read-only chat runner",
        )

        payload = context.to_payload()

        self.assertEqual(payload["route_confidence"]["recommended_route"], payload["task_fit"]["recommended_route"])
        self.assertIn(payload["route_confidence"]["confidence"], {"high", "medium", "low", "blocked", "unknown"})
        self.assertIn("Route confidence:", context.to_text())
        ledger_sources = {item["source"] for item in payload["source_ledger"]}
        self.assertIn("route_confidence", ledger_sources)

    def test_route_confidence_gate_answers_only_from_source_owned_provider_evidence(self) -> None:
        gate = build_route_confidence_gate(
            intent="status",
            candidate_route="spawner.latest_job_provider",
            latest_spawner_job={
                "schema_version": "spark.latest_spawner_job_evidence.v1",
                "status": "present",
                "provider": "codex",
                "model": "gpt-test",
                "provider_source": "agent-events:provider_result_received",
                "freshness": "current",
                "confidence": "high",
                "joined_sources": ["mission-control", "spawner-prd-trace", "agent-events"],
                "missing_sources": [],
                "blockers": [],
                "request_ref_redacted": "request_id:redacted:abc",
                "trace_ref_redacted": "trace_ref:redacted:def",
                "data_boundary": {
                    "metadata_only": True,
                    "raw_prompt_exported": False,
                    "provider_output_exported": False,
                    "chat_or_user_id_exported": False,
                    "memory_body_exported": False,
                    "artifact_body_exported": False,
                    "transcript_or_audio_exported": False,
                    "env_or_secret_exported": False,
                },
            },
        )

        self.assertEqual(gate["schema_version"], "spark.route_confidence_gate.v1")
        self.assertEqual(gate["decision"], "explain")
        self.assertEqual(gate["confidence"], "high")
        self.assertEqual(gate["provider"], "codex")
        self.assertEqual(gate["model"], "gpt-test")
        self.assertEqual(gate["safe_reply_policy"], "answer_live")
        self.assertFalse(gate["authority_required"])
        self.assertEqual(gate["doctrine"]["schema_version"], "spark.route_confidence_doctrine.v1")
        self.assertIn("act", gate["doctrine"]["decision_values"])

    def test_route_confidence_gate_refuses_to_invent_missing_provider(self) -> None:
        gate = build_route_confidence_gate(
            intent="status",
            candidate_route="spawner.latest_job_provider",
            latest_spawner_job={
                "schema_version": "spark.latest_spawner_job_evidence.v1",
                "status": "partial",
                "freshness": "current",
                "confidence": "low",
                "joined_sources": ["spawner-prd-trace"],
                "missing_sources": ["agent_events", "mission_control"],
                "blockers": ["missing_executed_provider"],
                "data_boundary": {"metadata_only": True},
            },
        )

        self.assertEqual(gate["decision"], "explain")
        self.assertEqual(gate["confidence"], "low")
        self.assertIsNone(gate["provider"])
        self.assertIn("missing_executed_provider", gate["missing_evidence"])
        self.assertIn("missing_executed_provider_model", gate["missing_evidence"])
        self.assertEqual(gate["safe_reply_policy"], "explain_missing")

    def test_route_confidence_gate_blocks_privacy_violations(self) -> None:
        gate = build_route_confidence_gate(
            intent="status",
            candidate_route="spawner.latest_job_provider",
            latest_spawner_job={
                "status": "present",
                "provider": "codex",
                "freshness": "current",
                "confidence": "high",
                "data_boundary": {"provider_output_exported": True},
            },
        )

        self.assertEqual(gate["decision"], "refuse")
        self.assertEqual(gate["confidence"], "blocked")
        self.assertEqual(gate["safe_reply_policy"], "refuse_privacy_violation")
        self.assertIn("privacy_violation:provider_output_exported", gate["missing_evidence"])

    def test_route_confidence_gate_allows_explicit_build_dispatch(self) -> None:
        gate = build_route_confidence_gate(
            intent="build_dispatch",
            candidate_route="spawner.build",
            route_context={
                "latest_instruction": "allow_execution",
                "intent_clarity": "explicit",
                "route_fit": "exact",
                "consequence_risk": "medium",
                "permission_required": "spawner_build",
                "authority_verdict": {
                    "schema_version": "spark.authority_verdict.v1",
                    "decision": "allowed",
                    "source_owner": "spark-telegram-bot",
                    "action_family": "spawner.build",
                },
                "capability_state": "available",
                "runner_state": "available",
                "confirmation_state": "not_required",
                "reversibility": "reversible",
                "joined_sources": ["telegram_access_policy", "spawner_health"],
                "data_boundary": {"exports_raw_prompt": False, "exports_chat_id": False},
            },
        )

        self.assertEqual(gate["decision"], "act")
        self.assertEqual(gate["confidence"], "high")
        self.assertEqual(gate["safe_reply_policy"], "execute_with_trace")
        self.assertEqual(gate["permission_required"], "spawner_build")
        self.assertTrue(gate["authority_required"])

    def test_route_confidence_gate_explains_no_execution_build_boundary(self) -> None:
        gate = build_route_confidence_gate(
            intent="build_dispatch",
            candidate_route="spawner.build",
            route_context={
                "latest_instruction": "no_execution",
                "explicit_no_execution": True,
                "intent_clarity": "explicit",
                "route_fit": "exact",
                "capability_state": "available",
                "runner_state": "available",
            },
        )

        self.assertEqual(gate["decision"], "explain")
        self.assertEqual(gate["confidence"], "high")
        self.assertEqual(gate["safe_reply_policy"], "explain_no_execution_boundary")

    def test_route_confidence_gate_asks_for_confirmation_on_external_action(self) -> None:
        gate = build_route_confidence_gate(
            intent="build_dispatch",
            candidate_route="spawner.build",
            route_context={
                "latest_instruction": "allow_execution",
                "intent_clarity": "explicit",
                "route_fit": "exact",
                "consequence_risk": "external",
                "confirmation_state": "missing",
                "authority_verdict": {
                    "schema_version": "spark.authority_verdict.v1",
                    "decision": "allowed",
                    "source_owner": "spark-telegram-bot",
                    "action_family": "spawner.build",
                },
                "capability_state": "available",
                "runner_state": "available",
            },
        )

        self.assertEqual(gate["decision"], "ask")
        self.assertEqual(gate["safe_reply_policy"], "ask_for_confirmation")
        self.assertIn("confirmation_required", gate["missing_evidence"])

    def test_route_confidence_gate_fails_closed_without_action_context(self) -> None:
        gate = build_route_confidence_gate(
            intent="build_dispatch",
            candidate_route="spawner.build",
            route_context={},
        )

        self.assertEqual(gate["decision"], "ask")
        self.assertEqual(gate["confidence"], "blocked")
        self.assertEqual(gate["safe_reply_policy"], "ask_for_route_evidence")
        self.assertIn("structured_authority_verdict_missing", gate["missing_evidence"])
        self.assertIn("runner_capability_state_missing", gate["missing_evidence"])

    def test_route_confidence_gate_requires_structured_authority_for_actions(self) -> None:
        gate = build_route_confidence_gate(
            intent="build_dispatch",
            candidate_route="spawner.build",
            route_context={
                "latest_instruction": "allow_execution",
                "intent_clarity": "explicit",
                "route_fit": "exact",
                "consequence_risk": "medium",
                "permission_required": "spawner_build",
                "authority_verdict": "allowed",
                "capability_state": "available",
                "runner_state": "available",
                "confirmation_state": "not_required",
                "data_boundary": {"exports_raw_prompt": False},
            },
        )

        self.assertEqual(gate["decision"], "ask")
        self.assertEqual(gate["safe_reply_policy"], "ask_for_route_evidence")
        self.assertIn("structured_authority_verdict_missing", gate["missing_evidence"])

    def test_route_confidence_gate_blocks_truthy_string_privacy_flags_and_raw_keys(self) -> None:
        gate = build_route_confidence_gate(
            intent="build_dispatch",
            candidate_route="spawner.build",
            route_context={
                "latest_instruction": "allow_execution",
                "intent_clarity": "explicit",
                "route_fit": "exact",
                "consequence_risk": "medium",
                "permission_required": "none",
                "authority_verdict": "not_required",
                "capability_state": "available",
                "runner_state": "available",
                "raw_prompt": "do not export this",
                "data_boundary": {"exports_secret": "true"},
            },
        )

        self.assertEqual(gate["decision"], "refuse")
        self.assertEqual(gate["safe_reply_policy"], "refuse_privacy_violation")
        self.assertIn("privacy_violation:env_or_secret_exported", gate["missing_evidence"])
        self.assertTrue(any("forbidden_payload_key:raw_prompt" in item for item in gate["missing_evidence"]))

    def test_route_confidence_gate_allows_bounded_local_repair_with_fresh_health_evidence(self) -> None:
        gate = build_route_confidence_gate(
            intent="repair",
            candidate_route="spark.repair",
            route_context={
                "latest_instruction": "allow_execution",
                "intent_clarity": "explicit",
                "route_fit": "exact",
                "consequence_risk": "medium",
                "permission_required": "spark_repair",
                "authority_verdict": {
                    "schema_version": "spark.authority_verdict.v1",
                    "decision": "allowed",
                    "source_owner": "spark-cli",
                    "action_family": "spark.repair",
                },
                "capability_state": "available",
                "runner_state": "available",
                "confirmation_state": "not_required",
                "reversibility": "reversible",
                "repair_target": "telegram_runtime",
                "repair_scope": "local_supervised_restart",
                "health_evidence": "fresh_degraded",
                "data_boundary": {"exports_raw_prompt": False, "exports_chat_id": False},
            },
        )

        self.assertEqual(gate["decision"], "act")
        self.assertEqual(gate["safe_reply_policy"], "execute_with_trace")
        self.assertEqual(gate["repair_target"], "telegram_runtime")
        self.assertEqual(gate["repair_scope"], "local_supervised_restart")

    def test_route_confidence_gate_explains_no_repair_needed_with_false_boundary_flags(self) -> None:
        gate = build_route_confidence_gate(
            intent="repair",
            candidate_route="spark.repair",
            route_context={
                "latest_instruction": "allow_execution",
                "intent_clarity": "explicit",
                "route_fit": "blocked",
                "consequence_risk": "medium",
                "permission_required": "none",
                "authority_verdict": {
                    "schema_version": "spark.authority_verdict.v1",
                    "decision": "not_required",
                    "source_owner": "spark-cli",
                    "action_family": "spark.repair",
                },
                "capability_state": "available",
                "runner_state": "available",
                "confirmation_state": "not_required",
                "reversibility": "reversible",
                "repair_target": "none_needed",
                "repair_scope": "no_repair_after_fresh_health_check",
                "health_evidence": "fresh_healthy",
                "data_boundary": {
                    "exports_raw_prompt": False,
                    "exports_chat_id": False,
                    "exports_provider_output": False,
                    "exports_memory_body": False,
                    "exports_transcript_body": False,
                    "exports_audio": False,
                    "exports_env_value": False,
                    "exports_secret": False,
                },
            },
        )

        self.assertEqual(gate["decision"], "explain")
        self.assertEqual(gate["confidence"], "high")
        self.assertEqual(gate["safe_reply_policy"], "explain_no_repair_needed")
        self.assertNotIn("privacy_violation:forbidden_payload_key:data_boundary.exports_secret", gate["missing_evidence"])

    def test_route_confidence_gate_fails_repair_closed_without_repair_evidence(self) -> None:
        gate = build_route_confidence_gate(
            intent="repair",
            candidate_route="spark.repair",
            route_context={
                "latest_instruction": "allow_execution",
                "intent_clarity": "explicit",
                "route_fit": "exact",
                "consequence_risk": "medium",
                "permission_required": "spark_repair",
                "authority_verdict": {
                    "schema_version": "spark.authority_verdict.v1",
                    "decision": "allowed",
                    "source_owner": "spark-cli",
                    "action_family": "spark.repair",
                },
                "capability_state": "available",
                "runner_state": "available",
                "confirmation_state": "not_required",
                "reversibility": "reversible",
            },
        )

        self.assertEqual(gate["decision"], "ask")
        self.assertEqual(gate["confidence"], "blocked")
        self.assertEqual(gate["safe_reply_policy"], "ask_for_route_evidence")
        self.assertIn("repair_target_missing", gate["missing_evidence"])
        self.assertIn("repair_health_evidence_missing", gate["missing_evidence"])

    def test_route_confidence_gate_requires_confirmation_for_secret_repair(self) -> None:
        gate = build_route_confidence_gate(
            intent="repair",
            candidate_route="spark.repair",
            route_context={
                "latest_instruction": "allow_execution",
                "intent_clarity": "explicit",
                "route_fit": "exact",
                "consequence_risk": "credential",
                "permission_required": "spark_repair",
                "authority_verdict": {
                    "schema_version": "spark.authority_verdict.v1",
                    "decision": "allowed",
                    "source_owner": "spark-cli",
                    "action_family": "spark.repair",
                },
                "capability_state": "available",
                "runner_state": "available",
                "confirmation_state": "missing",
                "reversibility": "partially_reversible",
                "repair_target": "provider_auth",
                "repair_scope": "credential_rotation_guidance",
                "health_evidence": "fresh_degraded",
            },
        )

        self.assertEqual(gate["decision"], "ask")
        self.assertEqual(gate["safe_reply_policy"], "ask_for_confirmation")
        self.assertIn("confirmation_required", gate["missing_evidence"])

    def test_route_confidence_gate_blocks_memory_action_without_source_verdict(self) -> None:
        gate = build_route_confidence_gate(
            intent="memory_action",
            candidate_route="memory.forget",
            route_context={
                "latest_instruction": "allow_execution",
                "intent_clarity": "explicit",
                "route_fit": "exact",
                "consequence_risk": "medium",
                "permission_required": "memory_action",
                "authority_verdict": {
                    "schema_version": "spark.authority_verdict.v1",
                    "decision": "allowed",
                    "source_owner": "spark-intelligence-builder",
                    "action_family": "memory.forget",
                },
                "capability_state": "available",
                "runner_state": "available",
                "confirmation_state": "not_required",
                "reversibility": "reversible",
            },
        )

        self.assertEqual(gate["decision"], "ask")
        self.assertEqual(gate["safe_reply_policy"], "ask_for_route_evidence")
        self.assertIn("memory_action_verdict_missing", gate["missing_evidence"])

    def test_route_confidence_gate_refuses_blocked_memory_action_verdict(self) -> None:
        gate = build_route_confidence_gate(
            intent="memory_action",
            candidate_route="memory.forget",
            route_context={
                "latest_instruction": "allow_execution",
                "intent_clarity": "explicit",
                "route_fit": "exact",
                "consequence_risk": "medium",
                "permission_required": "memory_action",
                "authority_verdict": {
                    "schema_version": "spark.authority_verdict.v1",
                    "decision": "allowed",
                    "source_owner": "spark-intelligence-builder",
                    "action_family": "memory.forget",
                },
                "memory_action_verdict": {
                    "schema_version": "spark.memory_action_verdict.v1",
                    "verdict": "blocked",
                    "owner_system": "spark-intelligence-builder",
                    "action_family": "memory_forget",
                },
                "capability_state": "available",
                "runner_state": "available",
                "confirmation_state": "not_required",
                "reversibility": "reversible",
            },
        )

        self.assertEqual(gate["decision"], "refuse")
        self.assertEqual(gate["safe_reply_policy"], "refuse_authority_blocked")
        self.assertIn("memory_action_blocked", gate["missing_evidence"])

    def test_route_confidence_gate_requires_publish_target_and_confirmation(self) -> None:
        missing_target = build_route_confidence_gate(
            intent="publish",
            candidate_route="spark.publish",
            route_context={
                "latest_instruction": "allow_execution",
                "intent_clarity": "explicit",
                "route_fit": "exact",
                "consequence_risk": "external",
                "permission_required": "external_publish",
                "authority_verdict": {
                    "schema_version": "spark.authority_verdict.v1",
                    "decision": "allowed",
                    "source_owner": "spark-cli",
                    "action_family": "spark.publish",
                },
                "capability_state": "available",
                "runner_state": "available",
                "confirmation_state": "missing",
                "reversibility": "rollback_available",
            },
        )
        self.assertEqual(missing_target["decision"], "ask")
        self.assertEqual(missing_target["safe_reply_policy"], "ask_for_route_evidence")
        self.assertIn("publication_target_missing", missing_target["missing_evidence"])

        needs_confirmation = build_route_confidence_gate(
            intent="publish",
            candidate_route="spark.publish",
            route_context={
                "latest_instruction": "allow_execution",
                "intent_clarity": "explicit",
                "route_fit": "exact",
                "consequence_risk": "external",
                "permission_required": "external_publish",
                "authority_verdict": {
                    "schema_version": "spark.authority_verdict.v1",
                    "decision": "allowed",
                    "source_owner": "spark-cli",
                    "action_family": "spark.publish",
                },
                "capability_state": "available",
                "runner_state": "available",
                "confirmation_state": "missing",
                "reversibility": "rollback_available",
                "publication_target": "agent.sparkswarm.ai",
            },
        )
        self.assertEqual(needs_confirmation["decision"], "ask")
        self.assertEqual(needs_confirmation["safe_reply_policy"], "ask_for_confirmation")
