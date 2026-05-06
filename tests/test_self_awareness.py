from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from spark_intelligence.observability.store import record_event
from spark_intelligence.adapters.telegram.runtime import simulate_telegram_update
from spark_intelligence.llm_wiki import promote_llm_wiki_improvement, promote_llm_wiki_user_note
from spark_intelligence.memory import run_memory_sdk_smoke_test
from spark_intelligence.researcher_bridge.advisory import build_researcher_reply
from spark_intelligence.self_awareness import (
    build_agent_operating_context,
    build_capability_drift_heartbeat,
    build_capability_proposal_packet,
    build_connector_harness_envelope,
    build_handoff_freshness_check,
    build_self_awareness_capsule,
    build_self_improvement_plan,
    capability_is_active,
    load_capability_ledger,
    record_capability_ledger_event,
    record_capability_proposal,
    record_route_probe_evidence,
    run_route_probe_and_record,
    redact_connector_probe_sample,
)

from tests.test_support import SparkTestCase, create_fake_hook_chip, make_telegram_update


class SelfAwarenessCapsuleTests(SparkTestCase):
    def test_self_awareness_capsule_separates_observed_recent_unverified_lacks_and_improvements(self) -> None:
        chip_root = create_fake_hook_chip(self.home, chip_key="startup-yc")
        self.config_manager.set_path("spark.chips.roots", [str(chip_root)])
        self.config_manager.set_path("spark.chips.active_keys", ["startup-yc"])
        record_event(
            self.state_db,
            event_type="tool_result_received",
            component="researcher_bridge",
            summary="Startup chip route succeeded",
            status="succeeded",
            facts={
                "routing_decision": "researcher_advisory",
                "bridge_mode": "external_typed",
                "active_chip_key": "startup-yc",
                "route_latency_ms": 432,
                "eval_suite": "self-awareness-route-regression",
            },
            provenance={"source_kind": "chip_hook", "source_ref": "startup-yc"},
        )
        record_event(
            self.state_db,
            event_type="dispatch_failed",
            component="researcher_bridge",
            summary="Browser route timed out",
            status="failed",
            facts={
                "routing_decision": "browser_search",
                "failure_reason": "timeout",
            },
            provenance={"source_kind": "route", "source_ref": "browser_search"},
        )

        capsule = build_self_awareness_capsule(
            config_manager=self.config_manager,
            state_db=self.state_db,
            human_id="telegram:123",
            session_id="session:telegram:123",
            channel_kind="telegram",
            user_message="where do you lack and how can you improve?",
        )
        payload = capsule.to_payload()

        self.assertTrue(payload["observed_now"])
        self.assertTrue(payload["recently_verified"])
        self.assertTrue(payload["available_unverified"])
        self.assertTrue(payload["lacks"])
        self.assertTrue(payload["improvement_options"])
        self.assertTrue(payload["capability_evidence"])
        self.assertTrue(payload["natural_language_routes"])
        lack_text = json.dumps(payload["lacks"])
        self.assertIn("Registry visibility does not prove", lack_text)
        self.assertIn("Natural-language invocability", lack_text)
        recent_text = json.dumps(payload["recently_verified"])
        self.assertIn("startup-yc", recent_text)
        evidence_text = json.dumps(payload["capability_evidence"])
        self.assertIn("last_success_at", evidence_text)
        self.assertIn("last_failure_at", evidence_text)
        self.assertIn("route_latency_ms", evidence_text)
        self.assertIn("eval_coverage_status", evidence_text)
        self.assertIn("confidence_level", evidence_text)
        self.assertIn("freshness_status", evidence_text)
        self.assertIn("goal_relevance", evidence_text)
        self.assertIn("can_claim_confidently", evidence_text)

    def test_self_awareness_capability_freshness_scores_recent_goal_relevant_success(self) -> None:
        record_event(
            self.state_db,
            event_type="tool_result_received",
            component="researcher_bridge",
            summary="Startup YC route succeeded",
            status="succeeded",
            facts={
                "routing_decision": "researcher_advisory",
                "active_chip_key": "startup-yc",
                "route_latency_ms": 321,
                "eval_suite": "capability-freshness-regression",
            },
            provenance={"source_kind": "chip_hook", "source_ref": "startup-yc"},
        )

        capsule = build_self_awareness_capsule(
            config_manager=self.config_manager,
            state_db=self.state_db,
            human_id="human:test-capability-freshness",
            session_id="session:test-capability-freshness",
            channel_kind="telegram",
            user_message="can you use startup yc for this?",
        )
        payload = capsule.to_payload()

        startup = next(row for row in payload["capability_evidence"] if row["capability_key"] == "startup-yc")
        self.assertEqual(startup["confidence_level"], "recent_success")
        self.assertEqual(startup["freshness_status"], "fresh")
        self.assertEqual(startup["goal_relevance"], "direct")
        self.assertEqual(startup["eval_coverage_status"], "covered")
        self.assertIn("capability-freshness-regression", startup["eval_coverage_sources"])
        self.assertTrue(startup["can_claim_confidently"])
        self.assertIn("confidence=recent_success", capsule.to_text())
        self.assertIn("eval=covered", capsule.to_text())
        self.assertIn("goal=direct", capsule.to_text())

    def test_self_awareness_prioritizes_high_surprise_user_relevant_weak_spots(self) -> None:
        record_event(
            self.state_db,
            event_type="tool_result_received",
            component="researcher_bridge",
            summary="Startup YC route succeeded with eval coverage",
            status="succeeded",
            facts={
                "active_chip_key": "startup-yc",
                "route_latency_ms": 321,
                "eval_suite": "capability-freshness-regression",
            },
            provenance={"source_kind": "chip_hook", "source_ref": "startup-yc"},
        )
        record_event(
            self.state_db,
            event_type="dispatch_failed",
            component="researcher_bridge",
            summary="Browser search route timed out",
            status="failed",
            facts={
                "routing_decision": "browser_search",
                "failure_reason": "timeout",
            },
            provenance={"source_kind": "route", "source_ref": "browser_search"},
        )

        capsule = build_self_awareness_capsule(
            config_manager=self.config_manager,
            state_db=self.state_db,
            human_id="human:test-surprise-priority",
            session_id="session:test-surprise-priority",
            channel_kind="telegram",
            user_message="please improve the browser route weak spot",
        )
        payload = capsule.to_payload()

        top = payload["weak_spot_priorities"][0]
        self.assertEqual(top["priority_key"], "capability:browser-search")
        self.assertGreater(top["surprise_score"], 10)
        self.assertEqual(top["score_components"]["failure_evidence"], 6)
        self.assertEqual(top["score_components"]["user_relevance"], 3)
        self.assertIn("failure_evidence", top["priority_reasons"])
        self.assertIn("Weak spot priorities", capsule.to_text())

    def test_self_status_cli_emits_machine_readable_capsule(self) -> None:
        exit_code, stdout, stderr = self.run_cli(
            "self",
            "status",
            "--home",
            str(self.home),
            "--user-message",
            "what can you improve?",
            "--json",
        )

        self.assertEqual(exit_code, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["workspace_id"], "default")
        self.assertIn("observed_now", payload)
        self.assertIn("capability_evidence", payload)
        self.assertIn("lacks", payload)
        self.assertIn("improvement_options", payload)
        self.assertIn("source_ledger", payload)
        self.assertIn("memory_cognition", payload)
        self.assertIn("user_awareness", payload)
        self.assertIn("project_awareness", payload)
        self.assertIn("capability_probe_registry", payload)
        self.assertNotIn("self_status_memory", payload["memory_cognition"])

    def test_agent_operating_context_separates_access_from_runner_writability(self) -> None:
        result = build_agent_operating_context(
            config_manager=self.config_manager,
            state_db=self.state_db,
            human_id="human:cem",
            session_id="session:telegram:cem",
            channel_kind="telegram",
            user_message="inspect and patch the mission memory loop",
            spark_access_level="4",
            runner_writable=False,
            runner_label="read-only Codex sandbox",
        )

        payload = result.to_payload()
        self.assertEqual(payload["schema_version"], "spark.agent_operating_context.v1")
        self.assertTrue(payload["access"]["local_workspace_allowed"])
        self.assertFalse(payload["runner"]["writable"])
        self.assertEqual(payload["task_fit"]["recommended_route"], "writable_spawner_codex_mission")
        self.assertIn("current_runner_read_only", payload["task_fit"]["blocked_here_by"])
        self.assertIn("Permission is not proof of runner writability", payload["truth_boundary"])
        rendered = result.to_text()
        self.assertIn("Access: Level 4 - local workspace allowed", rendered)
        self.assertIn("Runner: read-only Codex sandbox", rendered)
        self.assertIn("writable Spawner/Codex mission", rendered)

    def test_agent_operating_context_exposes_route_health_with_claim_boundaries(self) -> None:
        result = build_agent_operating_context(
            config_manager=self.config_manager,
            state_db=self.state_db,
            user_message="what should Rec use for this task?",
            runner_writable=None,
        )
        payload = result.to_payload()

        route_keys = {route["key"] for route in payload["routes"]}
        self.assertIn("chat", route_keys)
        self.assertIn("spark_intelligence_builder", route_keys)
        self.assertIn("spark_memory", route_keys)
        self.assertIn("spark_spawner", route_keys)
        for route in payload["routes"]:
            self.assertIn("status", route)
            self.assertIn("claim_boundary", route)
            self.assertIn("evidence_status", route)
            self.assertIn("next_probe", route)
            self.assertIn("eval_coverage_status", route)
        ledger_sources = {item["source"] for item in payload["source_ledger"]}
        self.assertIn("operator_supplied_access", ledger_sources)
        self.assertIn("runner_preflight", ledger_sources)
        self.assertIn("system_registry", ledger_sources)
        self.assertIn("memory_context", ledger_sources)
        self.assertIn("wiki_context", ledger_sources)

    def test_agent_operating_context_routes_write_tasks_away_from_chat_when_runner_unknown(self) -> None:
        result = build_agent_operating_context(
            config_manager=self.config_manager,
            state_db=self.state_db,
            user_message="/aoc fix mission memory loop",
            spark_access_level="4",
            runner_writable=None,
            runner_label="telegram bot runner unknown",
        )

        payload = result.to_payload()
        self.assertEqual(payload["task_fit"]["recommended_route"], "probe_runner_or_spawner_codex_mission")
        self.assertIn("current_runner_unknown", payload["task_fit"]["blocked_here_by"])
        self.assertTrue(payload["task_fit"]["needs_local_workspace"])
        self.assertIn("probe runner or Spawner/Codex mission", result.to_text())

    def test_agent_operating_context_labels_builder_command_path_available_with_warnings(self) -> None:
        registry_payload = {
            "workspace_id": "default",
            "records": [
                {
                    "kind": "system",
                    "key": "spark_intelligence_builder",
                    "label": "Spark Intelligence Builder",
                    "status": "degraded",
                    "available": True,
                    "degraded": True,
                    "active": True,
                    "attached": True,
                    "limitations": ["Gateway/provider/channel readiness is not fully green yet."],
                }
            ],
        }
        capsule_payload = {
            "capability_evidence": [],
            "user_awareness": {},
            "memory_cognition": {},
        }
        with patch(
            "spark_intelligence.self_awareness.operating_context.build_system_registry",
            return_value=SimpleNamespace(to_payload=lambda: registry_payload),
        ), patch(
            "spark_intelligence.self_awareness.operating_context.build_self_awareness_capsule",
            return_value=SimpleNamespace(to_payload=lambda: capsule_payload),
        ):
            result = build_agent_operating_context(config_manager=self.config_manager, state_db=self.state_db)

        payload = result.to_payload()
        builder = next(route for route in payload["routes"] if route["key"] == "spark_intelligence_builder")
        self.assertEqual(builder["status"], "available_with_warnings")
        self.assertEqual(builder["registry_status"], "degraded")
        self.assertFalse(builder["degraded"])
        self.assertTrue(builder["ecosystem_degraded"])
        self.assertIn("Builder: available with warnings", result.to_text())

    def test_agent_operating_context_marks_route_evidence_gaps_without_claiming_success(self) -> None:
        registry_payload = {
            "workspace_id": "default",
            "records": [
                {
                    "kind": "system",
                    "key": "spark_spawner",
                    "label": "Spark Spawner",
                    "status": "available",
                    "available": True,
                    "degraded": False,
                    "active": True,
                    "attached": True,
                    "limitations": [],
                }
            ],
        }
        capsule_payload = {
            "capability_evidence": [],
            "user_awareness": {},
            "memory_cognition": {},
        }
        with patch(
            "spark_intelligence.self_awareness.operating_context.build_system_registry",
            return_value=SimpleNamespace(to_payload=lambda: registry_payload),
        ), patch(
            "spark_intelligence.self_awareness.operating_context.build_self_awareness_capsule",
            return_value=SimpleNamespace(to_payload=lambda: capsule_payload),
        ):
            result = build_agent_operating_context(config_manager=self.config_manager, state_db=self.state_db)

        payload = result.to_payload()
        spawner = next(route for route in payload["routes"] if route["key"] == "spark_spawner")
        self.assertEqual(spawner["status"], "healthy")
        self.assertEqual(spawner["evidence_status"], "current_probe_missing")
        self.assertEqual(spawner["eval_coverage_status"], "missing")
        self.assertIn("Spawner health/status probe", spawner["next_probe"])
        self.assertIn("not proof the route succeeded", spawner["claim_boundary"])

    def test_route_probe_evidence_moves_aoc_route_from_missing_to_last_success(self) -> None:
        result = record_route_probe_evidence(
            self.state_db,
            capability_key="spark_spawner",
            status="success",
            route_latency_ms=123,
            eval_ref="pytest:aoc-route-probe",
            source_ref="test:spawner-health",
            actor_id="operator:test",
            probe_summary="mission status=attention drift=ok active_systems=6",
        )

        self.assertEqual(result.status, "success")
        context = build_agent_operating_context(
            config_manager=self.config_manager,
            state_db=self.state_db,
            user_message="/aoc fix mission memory loop",
            spark_access_level="4",
            runner_writable=True,
            runner_label="test writable runner",
        )
        payload = context.to_payload()
        spawner = next(route for route in payload["routes"] if route["key"] == "spark_spawner")
        self.assertEqual(spawner["evidence_status"], "last_success_recorded")
        self.assertEqual(spawner["route_latency_ms"], 123)
        self.assertEqual(spawner["eval_coverage_status"], "covered")
        self.assertEqual(spawner["latest_probe_summary"], "mission status=attention drift=ok active_systems=6")
        self.assertIsNotNone(spawner["last_success_at"])
        self.assertIn("Route Evidence", context.to_text())
        self.assertIn("- Spark Spawner: mission status=attention drift=ok", context.to_text())
        ledger = {item["source"]: item for item in payload["source_ledger"]}
        self.assertTrue(ledger["capability_evidence"]["present"])

    def test_recent_route_probe_failure_downgrades_aoc_route_status(self) -> None:
        record_route_probe_evidence(
            self.state_db,
            capability_key="spark_swarm",
            status="failure",
            route_latency_ms=77,
            eval_ref="pytest:swarm-route-probe",
            source_ref="test:swarm-health",
            failure_reason="swarm_payload_not_ready",
            actor_id="operator:test",
            probe_summary="swarm payload_ready=False api_ready=False auth_state=missing",
        )

        context = build_agent_operating_context(
            config_manager=self.config_manager,
            state_db=self.state_db,
        )
        swarm = next(route for route in context.to_payload()["routes"] if route["key"] == "spark_swarm")
        self.assertEqual(swarm["status"], "degraded")
        self.assertEqual(swarm["evidence_status"], "last_failure_recorded")
        self.assertIn("- Spark Swarm: swarm payload_ready=False", context.to_text())
        repairs = context.to_payload()["route_repairs"]
        swarm_repair = next(repair for repair in repairs if repair["route_key"] == "spark_swarm")
        self.assertIn("local payload readiness", swarm_repair["next_action"])
        self.assertIn("fresh route probe succeeds", swarm_repair["claim_boundary"])
        self.assertIn("Route Repairs", context.to_text())
        self.assertIn("- Spark Swarm: Run swarm status/doctor", context.to_text())

    def test_agent_operating_context_text_prefers_recent_success_over_stale_failure(self) -> None:
        record_route_probe_evidence(
            self.state_db,
            capability_key="spark_intelligence_builder",
            status="failure",
            route_latency_ms=77,
            eval_ref="pytest:builder-route-probe",
            source_ref="test:builder-health",
            failure_reason=".env-permissions",
            actor_id="operator:test",
            probe_summary="gateway ready=False doctor_blocking_ok=False providers=1 channels=1",
        )
        record_route_probe_evidence(
            self.state_db,
            capability_key="spark_intelligence_builder",
            status="success",
            route_latency_ms=88,
            eval_ref="pytest:builder-route-probe",
            source_ref="test:builder-health",
            actor_id="operator:test",
            probe_summary="gateway ready=True doctor_blocking_ok=True providers=1 channels=1",
        )

        context = build_agent_operating_context(
            config_manager=self.config_manager,
            state_db=self.state_db,
        )

        builder = next(route for route in context.to_payload()["routes"] if route["key"] == "spark_intelligence_builder")
        self.assertEqual(builder["confidence_level"], "recent_success")
        self.assertEqual(builder["evidence_status"], "last_success_recorded")
        rendered = context.to_text()
        self.assertIn("Spark Intelligence Builder: available with warnings, last success:", rendered)
        self.assertNotIn("Spark Intelligence Builder: available with warnings, last failure: .env-permissions", rendered)
        builder_repair = next(repair for repair in context.to_payload()["route_repairs"] if repair["route_key"] == "spark_intelligence_builder")
        self.assertIn("gateway ready=True", builder_repair["reason"])
        self.assertNotEqual(builder_repair["reason"], ".env-permissions")

    def test_self_route_probe_cli_records_evidence_for_aoc(self) -> None:
        exit_code, stdout, stderr = self.run_cli(
            "self",
            "route-probe",
            "spark_memory",
            "--home",
            str(self.home),
            "--status",
            "success",
            "--latency-ms",
            "45",
            "--eval-ref",
            "memory-smoke",
            "--source-ref",
            "test:memory-smoke",
            "--json",
        )

        self.assertEqual(exit_code, 0, stderr)
        probe_payload = json.loads(stdout)
        self.assertEqual(probe_payload["capability_key"], "spark_memory")
        self.assertEqual(probe_payload["status"], "success")

        exit_code, stdout, stderr = self.run_cli(
            "self",
            "context",
            "--home",
            str(self.home),
            "--json",
        )

        self.assertEqual(exit_code, 0, stderr)
        context_payload = json.loads(stdout)
        memory = next(route for route in context_payload["routes"] if route["key"] == "spark_memory")
        self.assertEqual(memory["evidence_status"], "last_success_recorded")
        self.assertEqual(memory["route_latency_ms"], 45)
        self.assertEqual(memory["eval_coverage_status"], "covered")

    def test_run_route_probe_records_registry_backed_success(self) -> None:
        registry_payload = {
            "workspace_id": "default",
            "records": [
                {
                    "kind": "system",
                    "key": "spark_local_work",
                    "label": "Spark Local Work",
                    "status": "ready",
                    "available": True,
                    "degraded": False,
                    "limitations": [],
                }
            ],
        }
        with patch(
            "spark_intelligence.self_awareness.route_probe.build_system_registry",
            return_value=SimpleNamespace(to_payload=lambda: registry_payload),
        ):
            result = run_route_probe_and_record(
                self.config_manager,
                self.state_db,
                capability_key="spark_local_work",
                actor_id="operator:test",
            )

        self.assertEqual(result.status, "success")
        self.assertEqual(result.capability_key, "spark_local_work")
        self.assertIn("registry status=ready", result.probe_summary)

    def test_run_route_probe_uses_memory_smoke_for_memory_route(self) -> None:
        smoke_result = SimpleNamespace(
            write_result=SimpleNamespace(status="succeeded", accepted_count=1, reason=None),
            read_result=SimpleNamespace(abstained=False, records=[{"value": "ok"}], reason=None),
            cleanup_result=SimpleNamespace(accepted_count=1),
        )
        with patch(
            "spark_intelligence.memory.run_memory_sdk_smoke_test",
            return_value=smoke_result,
        ) as smoke:
            result = run_route_probe_and_record(
                self.config_manager,
                self.state_db,
                capability_key="spark_memory",
                actor_id="operator:test",
            )

        self.assertEqual(result.status, "success")
        self.assertIn("memory smoke", result.probe_summary)
        smoke.assert_called_once()

    def test_spawner_route_probe_fails_when_mission_control_is_degraded(self) -> None:
        mission_payload = {
            "summary": {
                "top_level_state": "degraded",
                "active_systems": ["Spark Spawner"],
            },
            "panels": {
                "spawner_payload_drift": {"status": "ok"},
            },
        }
        with patch(
            "spark_intelligence.mission_control.build_mission_control_snapshot",
            return_value=SimpleNamespace(to_payload=lambda: mission_payload),
        ):
            result = run_route_probe_and_record(
                self.config_manager,
                self.state_db,
                capability_key="spark_spawner",
                actor_id="operator:test",
            )

        self.assertEqual(result.status, "failure")
        self.assertEqual(result.failure_reason, "mission status=degraded drift=ok")
        self.assertIn("mission status=degraded", result.probe_summary)

    def test_run_route_probe_records_registry_backed_failure(self) -> None:
        registry_payload = {
            "workspace_id": "default",
            "records": [
                {
                    "kind": "system",
                    "key": "spark_browser",
                    "label": "Spark Browser",
                    "status": "degraded",
                    "available": True,
                    "degraded": True,
                    "limitations": ["Browser hook failed."],
                }
            ],
        }
        with patch(
            "spark_intelligence.self_awareness.route_probe.build_system_registry",
            return_value=SimpleNamespace(to_payload=lambda: registry_payload),
        ):
            result = run_route_probe_and_record(
                self.config_manager,
                self.state_db,
                capability_key="spark_browser",
                actor_id="operator:test",
            )

        self.assertEqual(result.status, "failure")
        self.assertEqual(result.failure_reason, "Browser hook failed.")
        context = build_agent_operating_context(
            config_manager=self.config_manager,
            state_db=self.state_db,
        )
        browser = next(route for route in context.to_payload()["routes"] if route["key"] == "spark_browser")
        self.assertEqual(browser["evidence_status"], "last_failure_recorded")
        self.assertEqual(browser["last_failure_reason"], "Browser hook failed.")

    def test_self_route_probe_cli_can_run_builtin_probe(self) -> None:
        exit_code, stdout, stderr = self.run_cli(
            "self",
            "route-probe",
            "spark_memory",
            "--home",
            str(self.home),
            "--run",
            "--json",
        )

        self.assertEqual(exit_code, 0, stderr)
        probe_payload = json.loads(stdout)
        self.assertEqual(probe_payload["capability_key"], "spark_memory")
        self.assertEqual(probe_payload["status"], "success")
        self.assertIn("memory smoke", probe_payload["probe_summary"])

    def test_self_context_cli_emits_machine_readable_preflight(self) -> None:
        exit_code, stdout, stderr = self.run_cli(
            "self",
            "context",
            "--home",
            str(self.home),
            "--spark-access-level",
            "4",
            "--runner-writable",
            "no",
            "--user-message",
            "fix mission memory loop",
            "--json",
        )

        self.assertEqual(exit_code, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["schema_version"], "spark.agent_operating_context.v1")
        self.assertEqual(payload["task_fit"]["recommended_route"], "writable_spawner_codex_mission")
        self.assertFalse(payload["runner"]["writable"])

    def test_builder_aggregate_readiness_points_to_concrete_provider_records(self) -> None:
        registry_payload = {
            "workspace_id": "default",
            "record_count": 2,
            "summary": {"current_capabilities": []},
            "records": [
                {
                    "kind": "system",
                    "key": "spark_intelligence_builder",
                    "record_id": "system:spark_intelligence_builder",
                    "label": "Spark Intelligence Builder",
                    "status": "degraded",
                    "available": True,
                    "degraded": True,
                    "limitations": ["Gateway/provider/channel readiness is not fully green yet."],
                },
                {
                    "kind": "provider",
                    "key": "custom",
                    "record_id": "provider:custom",
                    "label": "custom",
                    "status": "degraded",
                    "available": False,
                    "degraded": True,
                    "limitations": ["Provider secret or token is not currently available."],
                },
            ],
        }

        with patch(
            "spark_intelligence.self_awareness.capsule.build_system_registry",
            return_value=SimpleNamespace(to_payload=lambda: registry_payload),
        ):
            capsule = build_self_awareness_capsule(
                config_manager=self.config_manager,
                state_db=self.state_db,
                human_id="human:test-builder-aggregate",
                session_id="session:test-builder-aggregate",
                channel_kind="telegram",
                user_message="what is broken in builder right now?",
            )

        payload = capsule.to_payload()
        builder_claim = next(
            claim
            for claim in payload["degraded_or_missing"]
            if claim.get("capability_key") == "spark_intelligence_builder"
        )
        provider_claim = next(
            claim
            for claim in payload["degraded_or_missing"]
            if claim.get("capability_key") == "custom"
        )

        self.assertEqual(builder_claim["verification_status"], "aggregate_readiness_warning")
        self.assertEqual(builder_claim["confidence"], "medium")
        self.assertIn("provider/channel records", builder_claim["claim"])
        self.assertIn("gateway status --json", builder_claim["next_probe"])
        self.assertIn("concrete provider/channel blocker", builder_claim["improvement_action"])
        self.assertEqual(provider_claim["verification_status"], "degraded_or_missing")
        self.assertEqual(provider_claim["confidence"], "high")
        self.assertIn("Provider secret or token", provider_claim["claim"])

    def test_self_awareness_user_awareness_labels_current_context_without_promoting_doctrine(self) -> None:
        run_memory_sdk_smoke_test(
            config_manager=self.config_manager,
            state_db=self.state_db,
            sdk_module="domain_chip_memory",
            subject="human:test-user-awareness",
            predicate="profile.current_focus",
            value="hardening Spark self-awareness",
            cleanup=False,
        )
        run_memory_sdk_smoke_test(
            config_manager=self.config_manager,
            state_db=self.state_db,
            sdk_module="domain_chip_memory",
            subject="human:test-user-awareness",
            predicate="profile.current_decision",
            value="keep user context separate from Spark doctrine",
            cleanup=False,
        )
        promote_llm_wiki_user_note(
            config_manager=self.config_manager,
            human_id="human:test-user-awareness",
            title="User hardening cadence",
            summary="The user wants self-awareness hardening work committed in small checkpoints.",
            consent_ref="consent:test:self-awareness",
            evidence_refs=["pytest tests/test_self_awareness.py::user_awareness"],
        )

        capsule = build_self_awareness_capsule(
            config_manager=self.config_manager,
            state_db=self.state_db,
            human_id="human:test-user-awareness",
            session_id="session:test-user-awareness",
            channel_kind="telegram",
            user_message="what do you know about me and this project?",
            personality_profile={"user_deltas_applied": True},
        )
        payload = capsule.to_payload()

        user_awareness = payload["user_awareness"]
        self.assertEqual(user_awareness["scope_kind"], "user_specific")
        self.assertEqual(user_awareness["current_goal"]["label"], "recent")
        self.assertEqual(user_awareness["current_goal"]["value"], "hardening Spark self-awareness")
        self.assertEqual(user_awareness["stable_preferences"][0]["label"], "stable")
        self.assertEqual(user_awareness["recent_decisions"][0]["label"], "recent")
        self.assertEqual(user_awareness["user_wiki_context"]["candidate_note_count"], 1)
        self.assertFalse(user_awareness["user_wiki_context"]["can_override_current_state_memory"])
        self.assertIn("candidate", user_awareness["label_counts"])
        self.assertIn(
            "user_memory_stays_separate_from_global_spark_doctrine",
            user_awareness["boundaries"],
        )
        self.assertIn("User awareness", capsule.to_text())
        self.assertIn("User wiki candidates: 1", capsule.to_text())

    def test_self_awareness_project_awareness_names_projects_with_live_state_boundary(self) -> None:
        project_root = self.home / "project-alpha"
        project_root.mkdir()
        self.config_manager.set_path("spark.local_projects.include_known_spark_repos", False)
        self.config_manager.set_path("spark.local_projects.include_attachment_repos", False)
        self.config_manager.set_path(
            "spark.local_projects.records",
            {
                "project-alpha": {
                    "path": str(project_root),
                    "label": "Project Alpha",
                    "components": ["builder_hardening"],
                    "owner_system": "spark_local_work",
                }
            },
        )

        capsule = build_self_awareness_capsule(
            config_manager=self.config_manager,
            state_db=self.state_db,
            human_id="human:test-project-awareness",
            session_id="session:test-project-awareness",
            channel_kind="telegram",
            user_message="what project are we working on?",
        )
        payload = capsule.to_payload()

        project_awareness = payload["project_awareness"]
        self.assertTrue(project_awareness["present"])
        self.assertEqual(project_awareness["project_count"], 1)
        self.assertEqual(project_awareness["active_project"]["key"], "project-alpha")
        self.assertEqual(project_awareness["active_project"]["source_ref"], "registry:repo:project-alpha")
        self.assertEqual(project_awareness["active_project"]["source_kind"], "local_project_index")
        self.assertEqual(
            project_awareness["authority"],
            "observed_configuration_not_live_git_truth",
        )
        self.assertIn("live_git_status_outranks_project_awareness_snapshot", project_awareness["boundaries"])
        self.assertIn("Project awareness", capsule.to_text())
        self.assertIn("Known projects: 1", capsule.to_text())

    def test_self_awareness_exposes_safe_capability_probe_registry(self) -> None:
        chip_root = create_fake_hook_chip(self.home, chip_key="startup-yc")
        self.config_manager.set_path("spark.chips.roots", [str(chip_root)])
        self.config_manager.set_path("spark.chips.active_keys", ["startup-yc"])

        capsule = build_self_awareness_capsule(
            config_manager=self.config_manager,
            state_db=self.state_db,
            human_id="human:test-probe-registry",
            session_id="session:test-probe-registry",
            channel_kind="telegram",
            user_message="how do you prove a capability works?",
        )
        payload = capsule.to_payload()

        probes = payload["capability_probe_registry"]
        self.assertTrue(probes)
        startup_probe = next(probe for probe in probes if probe["target_key"] == "startup-yc")
        self.assertEqual(startup_probe["target_kind"], "chip")
        self.assertIn("spark-intelligence chips why", startup_probe["safe_probe"])
        self.assertEqual(startup_probe["access_boundary"], "read_only_routing_or_attachment_check")
        self.assertEqual(startup_probe["claim_boundary"], "configured_or_available_is_not_recent_success")
        self.assertFalse(startup_probe["records_current_success"])
        self.assertIn("startup-yc:", "\n".join(payload["recommended_probes"]))

    def test_capability_drift_heartbeat_names_stale_and_failed_routes_with_safe_probes(self) -> None:
        chip_root = create_fake_hook_chip(self.home, chip_key="startup-yc")
        self.config_manager.set_path("spark.chips.roots", [str(chip_root)])
        self.config_manager.set_path("spark.chips.active_keys", ["startup-yc"])
        stale_event_id = record_event(
            self.state_db,
            event_type="tool_result_received",
            component="researcher_bridge",
            summary="Startup YC route succeeded in an old probe",
            status="succeeded",
            facts={
                "active_chip_key": "startup-yc",
                "route_latency_ms": 321,
                "eval_suite": "capability-drift-regression",
            },
            provenance={"source_kind": "chip_hook", "source_ref": "startup-yc"},
        )
        old_timestamp = (datetime.now(UTC) - timedelta(days=45)).replace(microsecond=0).isoformat()
        with self.state_db.connect() as conn:
            conn.execute("UPDATE builder_events SET created_at = ? WHERE event_id = ?", (old_timestamp, stale_event_id))
            conn.commit()
        record_event(
            self.state_db,
            event_type="dispatch_failed",
            component="researcher_bridge",
            summary="Browser search route timed out",
            status="failed",
            facts={
                "routing_decision": "browser_search",
                "failure_reason": "timeout",
            },
            provenance={"source_kind": "route", "source_ref": "browser_search"},
        )

        result = build_capability_drift_heartbeat(
            config_manager=self.config_manager,
            state_db=self.state_db,
            user_message="probe startup yc and browser drift",
        )
        payload = result.payload

        self.assertEqual(payload["kind"], "capability_drift_heartbeat")
        self.assertEqual(payload["authority"], "observability_non_authoritative")
        self.assertEqual(payload["memory_policy"], "typed_report_not_chat_memory")
        self.assertEqual(payload["status"], "warn")
        self.assertTrue(payload["report_written"])
        self.assertTrue((self.home / "artifacts" / "capability-drift-heartbeat" / "latest.json").exists())
        self.assertTrue(Path(payload["report_path"]).exists())
        self.assertEqual(payload["summary"]["stale_success_count"], 1)
        self.assertEqual(payload["summary"]["recent_failure_count"], 1)
        drift_by_key = {row["capability_key"]: row for row in payload["capabilities_needing_probe"]}
        self.assertEqual(drift_by_key["startup-yc"]["drift_kind"], "stale_success")
        self.assertGreaterEqual(drift_by_key["startup-yc"]["last_success_age_days"], 45)
        self.assertIn("spark-intelligence chips why", drift_by_key["startup-yc"]["safe_probe"])
        self.assertEqual(drift_by_key["browser_search"]["drift_kind"], "recent_failure")
        self.assertIn("capability_last_success_stale", payload["warnings"])
        self.assertIn("capability_recent_failure_needs_probe", payload["warnings"])
        self.assertEqual(
            payload["truth_boundary"],
            "capability_drift_reports_do_not_promote_runtime_success_or_failure",
        )

    def test_self_heartbeat_cli_emits_machine_readable_drift_report(self) -> None:
        exit_code, stdout, stderr = self.run_cli(
            "self",
            "heartbeat",
            "--home",
            str(self.home),
            "--user-message",
            "what capabilities need probes?",
            "--json",
        )

        self.assertEqual(exit_code, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["kind"], "capability_drift_heartbeat")
        self.assertEqual(payload["authority"], "observability_non_authoritative")
        self.assertTrue(payload["report_written"])
        self.assertTrue(Path(payload["report_path"]).exists())
        self.assertIn("probe_plan", payload)
        self.assertEqual(payload["memory_policy"], "typed_report_not_chat_memory")

    def test_handoff_freshness_blocks_self_awareness_changes_without_doc_updates(self) -> None:
        result = build_handoff_freshness_check(
            config_manager=self.config_manager,
            changed_paths=["src/spark_intelligence/self_awareness/capsule.py"],
            write_report=False,
        )
        payload = result.payload

        self.assertEqual(payload["kind"], "self_awareness_handoff_freshness_check")
        self.assertEqual(payload["status"], "blocked")
        self.assertTrue(payload["doc_update_required"])
        self.assertFalse(payload["doc_update_present"])
        self.assertIn("handoff_docs_not_updated_with_self_awareness_change", payload["warnings"])

    def test_handoff_freshness_passes_when_required_docs_move_with_source_change(self) -> None:
        result = build_handoff_freshness_check(
            config_manager=self.config_manager,
            changed_paths=[
                "src/spark_intelligence/self_awareness/capsule.py",
                "docs/SPARK_SELF_AWARENESS_HARDENING_TASKS_2026-05-01.md",
                "docs/SPARK_SELF_AWARENESS_LLM_WIKI_HANDOFF_2026-05-01.md",
                "docs/SPARK_LLM_WIKI_ARCHITECTURE_PLAN_2026-05-01.md",
            ],
        )
        payload = result.payload

        self.assertEqual(payload["status"], "pass")
        self.assertTrue(payload["report_written"])
        self.assertTrue((self.home / "artifacts" / "handoff-freshness" / "latest.json").exists())
        self.assertTrue(payload["doc_update_present"])
        self.assertEqual(payload["memory_policy"], "typed_report_not_chat_memory")
        self.assertIn("self handoff-check", payload["continuation_prompt"])
        for row in payload["doc_status"]:
            self.assertEqual(row["missing_tokens"], [])

    def test_self_handoff_check_cli_emits_machine_readable_report(self) -> None:
        exit_code, stdout, stderr = self.run_cli(
            "self",
            "handoff-check",
            "--home",
            str(self.home),
            "--json",
        )

        self.assertIn(exit_code, {0, 1}, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["kind"], "self_awareness_handoff_freshness_check")
        self.assertIn(payload["status"], {"pass", "blocked"})
        self.assertTrue(payload["report_written"])
        self.assertIn("continuation_prompt", payload)
        self.assertEqual(payload["authority"], "observability_non_authoritative")

    def test_self_awareness_adds_memory_cognition_after_wiki_source_families_are_visible(self) -> None:
        kb_dir = self.home / "artifacts" / "spark-memory-kb" / "wiki" / "current-state"
        kb_dir.mkdir(parents=True)
        (kb_dir / "runtime.md").write_text(
            "---\n"
            "title: Runtime Memory KB\n"
            "authority: supporting_not_authoritative\n"
            "owner_system: domain-chip-memory\n"
            "wiki_family: memory_kb_current_state\n"
            "scope_kind: governed_memory\n"
            "source_of_truth: SparkMemorySDK\n"
            "freshness: snapshot_generated\n"
            "---\n"
            "# Runtime Memory KB\n\nCurrent-state memory snapshots are downstream of governed memory.",
            encoding="utf-8",
        )

        capsule = build_self_awareness_capsule(
            config_manager=self.config_manager,
            state_db=self.state_db,
            human_id="telegram:123",
            session_id="session:telegram:123",
            channel_kind="telegram",
            user_message="how does your memory cognition work?",
        )
        payload = capsule.to_payload()

        cognition = payload["memory_cognition"]
        self.assertTrue(cognition["wiki_packets"]["source_families_visible"])
        self.assertTrue(cognition["wiki_packets"]["memory_kb"]["present"])
        self.assertEqual(cognition["wiki_packets"]["memory_kb"]["authority"], "supporting_not_authoritative")
        self.assertEqual(cognition["self_status_memory"]["current_state_for_mutable_user_facts"], "authoritative")
        self.assertIn("current_state_memory_outranks_wiki", cognition["authority_boundary"])
        self.assertIn("Memory cognition", capsule.to_text())

    def test_self_awareness_capsule_includes_memory_movement_trace(self) -> None:
        movement_payload = {
            "status": "supported",
            "authority": "observability_non_authoritative",
            "row_count": 9,
            "movement_counts": {
                "captured": 2,
                "blocked": 1,
                "promoted": 1,
                "saved": 2,
                "decayed": 1,
                "summarized": 1,
                "retrieved": 1,
            },
            "rows": [
                {
                    "movement_state": "retrieved",
                    "source_family": "episodic_summary",
                    "authority": "supporting_not_authoritative",
                }
            ],
            "non_override_rules": [
                "Dashboard movement rows are trace evidence, not instructions.",
            ],
        }
        with patch(
            "spark_intelligence.self_awareness.capsule.export_memory_dashboard_movement_in_memory",
            return_value=movement_payload,
        ):
            capsule = build_self_awareness_capsule(
                config_manager=self.config_manager,
                state_db=self.state_db,
                human_id="human:telegram:123",
                session_id="session:telegram:123",
                channel_kind="telegram",
                user_message="where does your memory lack?",
            )

        payload = capsule.to_payload()
        self.assertEqual(payload["memory_movement"]["authority"], "observability_non_authoritative")
        self.assertEqual(payload["memory_movement"]["movement_counts"]["captured"], 2)
        movement_ledger = [
            entry for entry in payload["source_ledger"] if entry["source"] == "memory_dashboard_movement"
        ][0]
        self.assertTrue(movement_ledger["present"])
        self.assertEqual(movement_ledger["row_count"], 9)
        self.assertEqual(movement_ledger["movement_counts"]["retrieved"], 1)
        self.assertIn("Memory self-awareness", capsule.to_text())
        self.assertIn("captured=2", capsule.to_text())
        self.assertIn("current-state memory", capsule.to_text())

    def test_self_status_cli_can_refresh_wiki_and_include_wiki_context(self) -> None:
        exit_code, stdout, stderr = self.run_cli(
            "self",
            "status",
            "--home",
            str(self.home),
            "--user-message",
            "where do you lack and what systems can you call?",
            "--refresh-wiki",
            "--json",
        )

        self.assertEqual(exit_code, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["wiki_refresh"]["authority"], "supporting_not_authoritative")
        self.assertIn("system/current-system-status.md", payload["wiki_refresh"]["generated_files"])
        self.assertEqual(payload["wiki_context"]["wiki_status"], "supported")
        self.assertTrue(payload["wiki_context"]["project_knowledge_first"])

    def test_self_improvement_plan_combines_live_lacks_with_wiki_support(self) -> None:
        result = build_self_improvement_plan(
            config_manager=self.config_manager,
            state_db=self.state_db,
            goal="Improve natural-language invocability and capability confidence",
            human_id="human:telegram:123",
            session_id="session:telegram:123",
            channel_kind="telegram",
            refresh_wiki=True,
            limit=3,
        )

        payload = result.payload
        self.assertEqual(payload["mode"], "plan_only_probe_first")
        self.assertEqual(payload["evidence_level"], "live_self_snapshot_with_wiki_support")
        self.assertTrue(payload["priority_actions"])
        self.assertTrue(payload["wiki_sources"])
        self.assertIn("probe", payload["guardrail"].lower())
        action_text = json.dumps(payload["priority_actions"])
        self.assertIn("evidence_to_collect", action_text)
        self.assertIn("surprise_score", action_text)
        self.assertTrue(payload["live_self_awareness"]["weak_spot_priorities"])
        self.assertIn("Natural-language invocability", action_text)
        proposal = payload["capability_proposal_packet"]
        self.assertEqual(proposal["schema_version"], "spark.capability_proposal.v1")
        self.assertEqual(proposal["status"], "proposal_plan_only")
        self.assertIn("capability_ledger_key", proposal)
        self.assertIn("safe_probe", proposal)
        self.assertIn("claim_boundary", proposal)

    def test_self_improve_cli_emits_machine_readable_plan(self) -> None:
        exit_code, stdout, stderr = self.run_cli(
            "self",
            "improve",
            "Improve weak spots in self-awareness",
            "--home",
            str(self.home),
            "--refresh-wiki",
            "--json",
        )

        self.assertEqual(exit_code, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["authority"], "current_snapshot_plus_supporting_wiki_not_execution")
        self.assertTrue(payload["priority_actions"])
        self.assertTrue(payload["natural_language_invocations"])
        self.assertEqual(payload["mode"], "plan_only_probe_first")
        self.assertEqual(payload["capability_proposal_packet"]["schema_version"], "spark.capability_proposal.v1")

    def test_self_improvement_text_leads_with_requested_capability_proposal(self) -> None:
        result = build_self_improvement_plan(
            config_manager=self.config_manager,
            state_db=self.state_db,
            goal="can you actually install a voice to yourself?",
            human_id="human:telegram:123",
            session_id="session:telegram:123",
            channel_kind="telegram",
        )

        text = result.to_text()
        capability_index = text.index("Capability proposal")
        hardening_index = text.index("Related hardening probes")
        self.assertLess(capability_index, hardening_index)
        first_block = text[:hardening_index]
        self.assertIn("requested capability: can you actually install a voice to yourself", first_block)
        self.assertIn("status: proposal_plan_only (not installed yet)", first_block)
        self.assertIn("connector: voice", first_block)
        self.assertIn("safe probe: Run voice.status", first_block)
        self.assertIn("not proof of a live capability", first_block)
        self.assertNotIn("swarm_decision_unavailable", first_block)

    def test_capability_proposal_packet_classifies_connector_and_workflow_routes(self) -> None:
        email_packet = build_capability_proposal_packet(
            goal="Build this for you, Spark: read my emails and summarize my inbox.",
            user_message="Build this for you, Spark: read my emails and summarize my inbox.",
        ).to_payload()
        self.assertEqual(email_packet["implementation_route"], "capability_connector")
        self.assertIn("email_account_access", email_packet["permissions_required"])
        self.assertIn("redacted", email_packet["safe_probe"])
        self.assertIn("capability_connector:", email_packet["capability_ledger_key"])
        self.assertEqual(email_packet["connector_harness"]["schema_version"], "spark.connector_harness.v1")
        self.assertEqual(email_packet["connector_harness"]["connector_key"], "email")
        self.assertEqual(email_packet["connector_harness"]["authority_stage"], "proposal_only")
        self.assertIn("read_message_body", email_packet["connector_harness"]["blocked_live_actions"])
        self.assertIn("human_approval_recorded", email_packet["connector_harness"]["live_access_blocked_until"])

        report_packet = build_capability_proposal_packet(
            goal="Set up daily reports of my memories so I know what changed.",
            user_message="Set up daily reports of my memories so I know what changed.",
        ).to_payload()
        self.assertEqual(report_packet["implementation_route"], "workflow_automation")
        self.assertIn("scheduler_write_scope", report_packet["permissions_required"])
        self.assertIn("dry-run", report_packet["safe_probe"])
        self.assertEqual(report_packet["connector_harness"]["connector_key"], "workflow")
        self.assertIn("enable_delivery", report_packet["connector_harness"]["blocked_live_actions"])

    def test_capability_proposal_packet_keeps_mission_artifacts_distinct(self) -> None:
        packet = build_capability_proposal_packet(
            goal="Build a Spark memory dashboard.",
            user_message="Build a Spark memory dashboard.",
        ).to_payload()

        self.assertEqual(packet["implementation_route"], "mission_artifact")
        self.assertEqual(packet["owner_system"], "Spark Spawner / Mission Control")
        self.assertIn("not proof of a live capability", packet["claim_boundary"])
        self.assertNotIn("connector_harness", packet)

    def test_connector_harness_envelope_blocks_live_access_until_approval(self) -> None:
        harness = build_connector_harness_envelope(
            goal="Let Spark read my calendar and summarize upcoming events.",
            implementation_route="capability_connector",
            permissions_required=["calendar_account_access", "event_read_scope"],
        )

        self.assertIsNotNone(harness)
        payload = harness.to_payload()
        self.assertEqual(payload["connector_key"], "calendar")
        self.assertEqual(payload["authority_stage"], "proposal_only")
        self.assertIn("calendar_account_access", payload["permissions_required"])
        self.assertIn("metadata sample", payload["dry_run_probe"])
        self.assertIn("create_event", payload["blocked_live_actions"])
        self.assertIn("approval", payload["approval_prompt"].lower())
        self.assertIn("capability_ledger_key", payload["trace_fields"])

    def test_connector_probe_redaction_removes_private_content_and_secrets(self) -> None:
        redacted = redact_connector_probe_sample(
            connector_key="email",
            sample={
                "id": "msg_123",
                "from": "alice@example.com",
                "subject": "Private launch details",
                "snippet": "Revenue and medical info",
                "headers": {"authorization": "Bearer sk-test-abcdefghijklmnopqrstuvwxyz"},
                "metadata_count": 1,
            },
        )

        self.assertEqual(redacted["id"], "msg_123")
        self.assertEqual(redacted["metadata_count"], 1)
        self.assertIn("<redacted email from>", redacted["from"])
        self.assertIn("<redacted email subject>", redacted["subject"])
        self.assertIn("<redacted email snippet>", redacted["snippet"])
        self.assertIn("<redacted email authorization>", redacted["headers"]["authorization"])

    def test_capability_ledger_records_proposal_without_activation(self) -> None:
        packet = build_capability_proposal_packet(
            goal="Build this for you, Spark: read my emails and summarize my inbox.",
            user_message="Build this for you, Spark: read my emails and summarize my inbox.",
        ).to_payload()

        result = record_capability_proposal(
            config_manager=self.config_manager,
            proposal_packet=packet,
            actor_id="human:test-ledger",
            source_ref="pytest:proposal-only",
        )
        entry = result.payload

        self.assertEqual(entry["status"], "proposed")
        self.assertEqual(entry["proposal_packet"]["status"], "proposal_plan_only")
        self.assertEqual(entry["activation_evidence"], [])
        self.assertFalse(capability_is_active(entry))
        ledger = load_capability_ledger(self.config_manager).payload
        self.assertIn(packet["capability_ledger_key"], ledger["entries"])

    def test_capability_ledger_rejects_packet_status_as_activation(self) -> None:
        packet = build_capability_proposal_packet(
            goal="Give Spark a voice so it can reply with audio.",
            user_message="Give Spark a voice so it can reply with audio.",
        ).to_payload()
        packet["status"] = "activated"

        with self.assertRaises(ValueError):
            record_capability_proposal(
                config_manager=self.config_manager,
                proposal_packet=packet,
                actor_id="human:test-ledger",
                source_ref="pytest:tampered-status",
            )

    def test_capability_ledger_requires_separate_activation_evidence(self) -> None:
        packet = build_capability_proposal_packet(
            goal="Give Spark a voice so it can reply with audio.",
            user_message="Give Spark a voice so it can reply with audio.",
        ).to_payload()
        record_capability_proposal(
            config_manager=self.config_manager,
            proposal_packet=packet,
            actor_id="human:test-ledger",
            source_ref="pytest:activation-evidence",
        )
        key = packet["capability_ledger_key"]

        with self.assertRaises(ValueError):
            record_capability_ledger_event(
                config_manager=self.config_manager,
                capability_ledger_key=key,
                lifecycle_state="activated",
                actor_id="human:test-ledger",
            )

        record_capability_ledger_event(
            config_manager=self.config_manager,
            capability_ledger_key=key,
            lifecycle_state="probed",
            actor_id="human:test-ledger",
            source_ref="pytest:probe",
            evidence={"probe_ref": "voice.status:ok"},
        )
        record_capability_ledger_event(
            config_manager=self.config_manager,
            capability_ledger_key=key,
            lifecycle_state="approved",
            actor_id="human:test-ledger",
            source_ref="pytest:approval",
            evidence={"approval_ref": "operator:approved"},
        )
        activated = record_capability_ledger_event(
            config_manager=self.config_manager,
            capability_ledger_key=key,
            lifecycle_state="activated",
            actor_id="human:test-ledger",
            source_ref="pytest:voice-smoke",
            evidence={"approval_ref": "operator:approved", "eval_ref": "voice-smoke:passed"},
        ).payload

        self.assertTrue(capability_is_active(activated))
        self.assertEqual(activated["proposal_packet"]["status"], "proposal_plan_only")
        self.assertNotIn("eval_ref", activated["proposal_packet"])
        self.assertEqual(activated["current_activation"]["evidence"]["eval_ref"], "voice-smoke:passed")

    def test_capability_ledger_preserves_unknown_future_packet_fields(self) -> None:
        packet = build_capability_proposal_packet(
            goal="Create a daily memory report for Spark.",
            user_message="Create a daily memory report for Spark.",
        ).to_payload()
        packet["future_upgrade_field"] = {"kept": True, "ignored_by_v1": True}

        result = record_capability_proposal(
            config_manager=self.config_manager,
            proposal_packet=packet,
            actor_id="human:test-ledger",
            source_ref="pytest:future-field",
        )

        self.assertEqual(result.payload["proposal_packet"]["future_upgrade_field"]["kept"], True)
        ledger = load_capability_ledger(self.config_manager).payload
        saved = ledger["entries"][packet["capability_ledger_key"]]
        self.assertEqual(saved["proposal_packet"]["future_upgrade_field"]["ignored_by_v1"], True)
        self.assertEqual(saved["status"], "proposed")

    def test_self_improve_cli_can_record_capability_ledger_entry(self) -> None:
        exit_code, stdout, stderr = self.run_cli(
            "self",
            "improve",
            "Give Spark a voice so it can reply with audio.",
            "--home",
            str(self.home),
            "--record-ledger",
            "--json",
        )

        self.assertEqual(exit_code, 0, stderr)
        payload = json.loads(stdout)
        entry = payload["capability_ledger_entry"]
        self.assertEqual(entry["status"], "proposed")
        self.assertEqual(entry["proposal_packet"]["status"], "proposal_plan_only")
        ledger = load_capability_ledger(self.config_manager).payload
        self.assertIn(entry["capability_ledger_key"], ledger["entries"])

    def test_self_awareness_capsule_uses_personality_style_lens_without_raw_trait_dump(self) -> None:
        capsule = build_self_awareness_capsule(
            config_manager=self.config_manager,
            state_db=self.state_db,
            human_id="human:telegram:123",
            session_id="session:telegram:123",
            channel_kind="telegram",
            user_message="what do you know about yourself?",
            personality_profile={
                "agent_persona_name": "Spark AGI",
                "agent_persona_summary": "warm, curious, and direct without turning into a status page",
                "traits": {
                    "warmth": 0.82,
                    "directness": 0.78,
                    "playfulness": 0.62,
                    "pacing": 0.74,
                    "assertiveness": 0.7,
                },
                "agent_behavioral_rules": [
                    "keep evidence visible",
                    "sound conversational",
                ],
                "agent_persona_applied": True,
                "user_deltas_applied": True,
            },
        )

        payload = capsule.to_payload()
        text = capsule.to_text()
        self.assertEqual(payload["style_lens"]["persona_name"], "Spark AGI")
        self.assertIn("How I am tuned for you", text)
        self.assertIn("warm, curious, and direct", text)
        self.assertIn("Tone: direct, warm, and fast-moving", text)
        self.assertIn("keeping the evidence visible", text)
        self.assertNotIn("trait", text.lower())
        self.assertLess(len(text), 2800)

    def test_natural_self_awareness_query_uses_capsule_direct_route(self) -> None:
        result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-self-awareness",
            agent_id="agent-1",
            human_id="human:telegram:123",
            session_id="session:telegram:123",
            channel_kind="telegram",
            user_message="Where do you lack and how can you improve those parts?",
        )

        self.assertEqual(result.mode, "self_awareness_direct")
        self.assertEqual(result.routing_decision, "self_awareness_direct")
        self.assertIn("Spark self-awareness", result.reply_text)
        self.assertIn("Memory cognition", result.reply_text)
        self.assertIn("supporting_not_authoritative", result.reply_text)
        self.assertIn("current-state memory wins over wiki", result.reply_text)
        self.assertIn("Where I still lack", result.reply_text)
        self.assertIn("What I should improve next", result.reply_text)
        self.assertNotIn("LLM wiki", result.reply_text)
        self.assertLess(len(result.reply_text), 3000)
        self.assertIn("wiki_refresh=skipped", result.evidence_summary)
        self.assertEqual(result.output_keepability, "ephemeral_context")
        self.assertEqual(result.promotion_disposition, "not_promotable")

    def test_route_explanation_for_self_awareness_names_route_sources_stale_and_missing_probes(self) -> None:
        build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-self-awareness-to-explain",
            agent_id="agent-1",
            human_id="human:telegram:123",
            session_id="session:telegram:123",
            channel_kind="telegram",
            user_message="Where do you lack and how can you improve those parts?",
        )

        result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-self-awareness-explanation",
            agent_id="agent-1",
            human_id="human:telegram:123",
            session_id="session:telegram:123",
            channel_kind="telegram",
            user_message="Why did you answer that way?",
        )

        self.assertEqual(result.mode, "context_source_debug")
        self.assertEqual(result.routing_decision, "context_source_debug")
        self.assertIn("Route explanation", result.reply_text)
        self.assertIn("routing_decision: self_awareness_direct", result.reply_text)
        self.assertIn("Sources used", result.reply_text)
        self.assertIn("source=self_awareness_capsule", result.reply_text)
        self.assertIn("Stale evidence ignored", result.reply_text)
        self.assertIn("none recorded for this route", result.reply_text)
        self.assertIn("Missing probes", result.reply_text)
        self.assertIn("run the exact safe probe", result.reply_text)
        self.assertIn("explained_request_id=req-self-awareness-to-explain", result.evidence_summary)
        self.assertEqual(result.output_keepability, "operator_debug_only")
        self.assertEqual(result.promotion_disposition, "not_promotable")

    def test_natural_self_awareness_query_exposes_memory_kb_families_without_promoting_wiki(self) -> None:
        kb_dir = self.home / "artifacts" / "spark-memory-kb" / "wiki" / "current-state"
        kb_dir.mkdir(parents=True)
        (kb_dir / "focus.md").write_text(
            "---\n"
            "title: Current Focus Snapshot\n"
            "authority: supporting_not_authoritative\n"
            "owner_system: domain-chip-memory\n"
            "wiki_family: memory_kb_current_state\n"
            "scope_kind: governed_memory\n"
            "source_of_truth: SparkMemorySDK\n"
            "freshness: snapshot_generated\n"
            "---\n"
            "# Current Focus Snapshot\n\nA downstream memory KB snapshot for source-family discovery.",
            encoding="utf-8",
        )

        result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-self-awareness-memory-kb",
            agent_id="agent-1",
            human_id="human:telegram:123",
            session_id="session:telegram:123",
            channel_kind="telegram",
            user_message=(
                "What systems can you call, what do you know about your memory system, "
                "what outranks wiki, and where do you lack?"
            ),
        )

        self.assertEqual(result.mode, "self_awareness_direct")
        self.assertIn("Memory KB: present", result.reply_text)
        self.assertIn("memory_kb_current_state", result.reply_text)
        self.assertIn("supporting_not_authoritative", result.reply_text)
        self.assertIn("current-state memory wins over wiki", result.reply_text)
        self.assertIn("Graph sidecar: advisory until evals pass", result.reply_text)
        self.assertIn("user memory stays separate from Spark doctrine", result.reply_text)
        self.assertNotIn("LLM wiki", result.reply_text)
        self.assertEqual(result.output_keepability, "ephemeral_context")
        self.assertEqual(result.promotion_disposition, "not_promotable")

    def test_memory_self_awareness_phrase_routes_without_systems_magic_words(self) -> None:
        run_memory_sdk_smoke_test(
            config_manager=self.config_manager,
            state_db=self.state_db,
            sdk_module="domain_chip_memory",
            subject="human:self-awareness:movement",
            predicate="system.memory.route_detection",
            value="ok",
            cleanup=False,
        )
        result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-self-awareness-memory-phrase",
            agent_id="agent-1",
            human_id="human:telegram:123",
            session_id="session:telegram:123",
            channel_kind="telegram",
            user_message="What do you know about your memory system and what outranks wiki?",
        )

        self.assertEqual(result.mode, "self_awareness_direct")
        self.assertEqual(result.routing_decision, "self_awareness_direct")
        self.assertIn("Memory cognition", result.reply_text)
        self.assertIn("Memory movement", result.reply_text)
        self.assertIn("captured=", result.reply_text)
        self.assertIn("current-state memory wins over wiki", result.reply_text)
        self.assertIn("supporting_not_authoritative", result.reply_text)
        self.assertEqual(result.output_keepability, "ephemeral_context")
        self.assertEqual(result.promotion_disposition, "not_promotable")

    def test_natural_wiki_candidate_inbox_query_routes_to_review_surface(self) -> None:
        promote_llm_wiki_improvement(
            config_manager=self.config_manager,
            title="Natural wiki candidate route",
            summary="Spark should expose candidate wiki notes through natural language without promoting them.",
            evidence_refs=["pytest tests/test_self_awareness.py::wiki_candidate_route"],
            source_refs=["operator_session:self-awareness-hardening"],
        )

        result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-wiki-candidate-inbox",
            agent_id="agent-1",
            human_id="human:telegram:123",
            session_id="session:telegram:123",
            channel_kind="telegram",
            user_message="What candidate wiki learnings need verification?",
        )

        self.assertEqual(result.mode, "llm_wiki_candidate_inbox")
        self.assertEqual(result.routing_decision, "llm_wiki_candidate_inbox")
        self.assertIn("LLM wiki candidate inbox", result.reply_text)
        self.assertIn("supporting_not_authoritative", result.reply_text)
        self.assertIn("not live runtime truth", result.reply_text)
        self.assertIn("Natural wiki candidate route", result.reply_text)
        self.assertIn("authority=supporting_not_authoritative", result.evidence_summary)
        self.assertEqual(result.output_keepability, "ephemeral_context")
        self.assertEqual(result.promotion_disposition, "not_promotable")

    def test_natural_wiki_candidate_scan_query_routes_to_contradiction_surface(self) -> None:
        promote_llm_wiki_improvement(
            config_manager=self.config_manager,
            title="User prefers global doctrine",
            summary="User prefers global doctrine forever.",
            evidence_refs=["pytest tests/test_memory_orchestrator.py::current_state"],
            source_refs=["operator_session:self-awareness-hardening"],
        )

        result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-wiki-candidate-scan",
            agent_id="agent-1",
            human_id="human:telegram:123",
            session_id="session:telegram:123",
            channel_kind="telegram",
            user_message="Scan your wiki candidates for contradictions",
        )

        self.assertEqual(result.mode, "llm_wiki_candidate_scan")
        self.assertEqual(result.routing_decision, "llm_wiki_candidate_scan")
        self.assertIn("LLM wiki candidate scan", result.reply_text)
        self.assertIn("rewrite", result.reply_text)
        self.assertIn("mutable_user_fact_requires_user_memory_lane", result.reply_text)
        self.assertIn("current-state memory", result.reply_text)
        self.assertIn("authority=supporting_not_authoritative", result.evidence_summary)
        self.assertEqual(result.output_keepability, "ephemeral_context")
        self.assertEqual(result.promotion_disposition, "not_promotable")

    def test_memory_self_awareness_without_kb_names_gap_without_overclaiming(self) -> None:
        result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-self-awareness-memory-no-kb",
            agent_id="agent-1",
            human_id="human:telegram:123",
            session_id="session:telegram:123",
            channel_kind="telegram",
            user_message="What do you know about your memory system and where do you lack?",
        )

        self.assertEqual(result.mode, "self_awareness_direct")
        self.assertIn("Memory cognition", result.reply_text)
        self.assertIn("families=not visible", result.reply_text)
        self.assertNotIn("Memory KB: present", result.reply_text)
        self.assertIn("Where I still lack", result.reply_text)
        self.assertEqual(result.output_keepability, "ephemeral_context")
        self.assertEqual(result.promotion_disposition, "not_promotable")

    def test_self_awareness_keeps_user_memory_separate_from_spark_doctrine(self) -> None:
        run_memory_sdk_smoke_test(
            config_manager=self.config_manager,
            state_db=self.state_db,
            sdk_module="domain_chip_memory",
            subject="human:telegram:123",
            predicate="profile.favorite_color",
            value="cobalt blue",
            cleanup=False,
        )

        result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-self-awareness-user-memory-separation",
            agent_id="agent-1",
            human_id="human:telegram:123",
            session_id="session:telegram:123",
            channel_kind="telegram",
            user_message="What do you know about your memory system and what belongs to Spark doctrine?",
        )

        self.assertEqual(result.mode, "self_awareness_direct")
        self.assertIn("user memory stays separate from Spark doctrine", result.reply_text)
        self.assertNotIn("cobalt blue", result.reply_text)
        self.assertNotIn("favorite color", result.reply_text.lower())
        self.assertEqual(result.output_keepability, "ephemeral_context")
        self.assertEqual(result.promotion_disposition, "not_promotable")

    def test_memory_lack_query_uses_self_awareness_direct_route(self) -> None:
        result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-memory-lack-self-awareness",
            agent_id="agent-1",
            human_id="human:telegram:123",
            session_id="session:telegram:123",
            channel_kind="telegram",
            user_message="Where does your memory still lack right now, and how would we improve it?",
        )

        self.assertEqual(result.mode, "self_awareness_direct")
        self.assertEqual(result.routing_decision, "self_awareness_direct")
        self.assertIn("Memory self-awareness", result.reply_text)
        self.assertIn("Where memory still lacks", result.reply_text)
        self.assertIn("How we improve it next", result.reply_text)
        self.assertNotIn("Spark Browser", result.reply_text)
        self.assertNotIn("Builder memory path", result.reply_text)

    def test_memory_architecture_query_uses_self_awareness_direct_route(self) -> None:
        result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-memory-architecture-self-awareness",
            agent_id="agent-1",
            human_id="human:telegram:123",
            session_id="session:telegram:123",
            channel_kind="telegram",
            user_message=(
                "Without using a checklist, what do you understand about our memory architecture right now, "
                "and which sources are current versus supporting?"
            ),
        )

        self.assertEqual(result.mode, "self_awareness_direct")
        self.assertEqual(result.routing_decision, "self_awareness_direct")
        self.assertIn("Spark memory architecture", result.reply_text)
        self.assertIn("Current sources", result.reply_text)
        self.assertIn("Supporting sources", result.reply_text)
        self.assertIn("current-state memory", result.reply_text.lower())
        self.assertIn("supporting_not_authoritative", result.reply_text)
        self.assertNotIn("biological memory", result.reply_text.lower())

    def test_dashboard_movement_query_uses_memory_self_awareness_route(self) -> None:
        result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-dashboard-movement-self-awareness",
            agent_id="agent-1",
            human_id="human:telegram:123",
            session_id="session:telegram:123",
            channel_kind="telegram",
            user_message="When you answer from memory, what movement evidence should the dashboard show?",
        )

        self.assertEqual(result.mode, "self_awareness_direct")
        self.assertEqual(result.routing_decision, "self_awareness_direct")
        self.assertIn("Memory movement evidence", result.reply_text)
        self.assertIn("captured", result.reply_text)
        self.assertIn("retrieved", result.reply_text)
        self.assertIn("saved", result.reply_text)
        self.assertIn("trace evidence only", result.reply_text)
        self.assertNotIn("Where memory still lacks", result.reply_text)

    def test_dashboard_reveal_query_uses_memory_movement_route(self) -> None:
        result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-dashboard-reveal-self-awareness",
            agent_id="agent-1",
            human_id="human:telegram:123",
            session_id="session:telegram:123",
            channel_kind="telegram",
            user_message="Show me what memory movement the dashboard should reveal after this conversation.",
        )

        self.assertEqual(result.mode, "self_awareness_direct")
        self.assertEqual(result.routing_decision, "self_awareness_direct")
        self.assertIn("Memory movement evidence", result.reply_text)
        self.assertIn("captured", result.reply_text)
        self.assertIn("retrieved", result.reply_text)
        self.assertNotIn("Where memory still lacks", result.reply_text)

    def test_self_awareness_query_beats_entity_state_summary_route(self) -> None:
        result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-self-awareness-summary-trap",
            agent_id="agent-1",
            human_id="human:telegram:123",
            session_id="session:telegram:123",
            channel_kind="telegram",
            user_message="What do you know about yourself and where do you lack?",
        )

        self.assertEqual(result.mode, "self_awareness_direct")
        self.assertEqual(result.routing_decision, "self_awareness_direct")
        self.assertIn("Spark self-awareness", result.reply_text)
        self.assertNotIn("saved entity state", result.reply_text)

    def test_normal_telegram_message_uses_self_awareness_with_wiki_context(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        result = simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=1001,
                user_id="111",
                username="alice",
                text="What systems can you call, where do you lack, and how can you improve?",
            ),
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.decision, "allowed")
        self.assertEqual(result.detail["bridge_mode"], "self_awareness_direct")
        self.assertEqual(result.detail["routing_decision"], "self_awareness_direct")
        self.assertIn("Spark self-awareness", result.detail["response_text"])
        self.assertIn("Memory cognition", result.detail["response_text"])
        self.assertIn("current-state memory wins over wiki", result.detail["response_text"])
        self.assertIn("Where I still lack", result.detail["response_text"])
        self.assertNotIn("LLM wiki", result.detail["response_text"])
        self.assertLess(len(result.detail["response_text"]), 3000)

    def test_telegram_self_and_wiki_runtime_commands_are_invocable(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])
        promote_llm_wiki_improvement(
            config_manager=self.config_manager,
            title="Telegram command candidate",
            summary="The live Telegram runtime should expose wiki candidates without promoting them.",
            evidence_refs=["pytest tests/test_self_awareness.py::telegram_runtime_commands"],
            source_refs=["operator_session:self-awareness-hardening"],
        )

        self_result = simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=1002,
                user_id="111",
                username="alice",
                text="/self",
            ),
        )
        wiki_result = simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=1003,
                user_id="111",
                username="alice",
                text="/wiki candidates",
            ),
        )
        context_result = simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=1004,
                user_id="111",
                username="alice",
                text="/context",
            ),
        )

        self.assertTrue(self_result.ok)
        self.assertEqual(self_result.detail["bridge_mode"], "runtime_command")
        self.assertEqual(self_result.detail["routing_decision"], "runtime_command")
        self.assertIn("Spark self-awareness", self_result.detail["response_text"])
        self.assertTrue(wiki_result.ok)
        self.assertEqual(wiki_result.detail["bridge_mode"], "runtime_command")
        self.assertIn("Spark LLM wiki candidate inbox", wiki_result.detail["response_text"])
        self.assertIn("Telegram command candidate", wiki_result.detail["response_text"])
        self.assertIn("not live Spark truth", wiki_result.detail["response_text"])
        self.assertTrue(context_result.ok)
        self.assertEqual(context_result.detail["bridge_mode"], "runtime_command")
        self.assertIn("Agent Operating Context", context_result.detail["response_text"])
        self.assertIn("Best route:", context_result.detail["response_text"])
        self.assertIn("Guardrails", context_result.detail["response_text"])
