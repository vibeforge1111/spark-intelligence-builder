from __future__ import annotations

import json
import sqlite3
from unittest.mock import patch

from spark_intelligence.attachments.snapshot import sync_attachment_snapshot
from spark_intelligence.adapters.telegram.runtime import _send_telegram_reply
from spark_intelligence.gateway.guardrails import prepare_outbound_text
from spark_intelligence.observability.policy import looks_secret_like
from spark_intelligence.jobs.service import jobs_tick
from spark_intelligence.doctor.checks import run_doctor
from spark_intelligence.identity.service import (
    approve_pairing,
    link_spark_swarm_agent,
    record_pairing_context,
    resolve_canonical_agent_identity,
)
from spark_intelligence.ops.service import build_operator_security_report
from spark_intelligence.observability.checks import evaluate_stop_ship_issues
from spark_intelligence.observability.store import (
    build_watchtower_snapshot,
    close_run,
    latest_events_by_type,
    open_run,
    recent_contradictions,
    recent_memory_lane_records,
    recent_runs,
    record_observer_handoff_record,
    recent_observer_packet_records,
    recent_policy_gate_records,
    record_environment_snapshot,
    record_event,
    repair_missing_memory_lane_records,
)
from spark_intelligence.personality.loader import (
    detect_and_persist_nl_preferences,
    detect_personality_query,
    load_personality_profile,
    maybe_evolve_traits,
    record_observation,
)
from spark_intelligence.researcher_bridge.advisory import (
    ResearcherBridgeResult,
    build_researcher_reply,
    record_researcher_bridge_result,
    researcher_bridge_status,
)

from tests.test_support import SparkTestCase, create_fake_hook_chip


class BuilderPrelaunchContractTests(SparkTestCase):
    def test_tranche1_typed_ledger_tables_are_populated(self) -> None:
        run = open_run(
            self.state_db,
            run_kind="telegram_update",
            origin_surface="telegram_runtime",
            summary="run opened",
            request_id="req-ledger",
            session_id="session:test",
            channel_id="telegram",
            actor_id="telegram_runtime",
        )
        record_event(
            self.state_db,
            event_type="delivery_attempted",
            component="telegram_runtime",
            summary="delivery attempted",
            run_id=run.run_id,
            request_id="req-ledger",
            session_id="session:test",
            channel_id="telegram",
            actor_id="telegram_runtime",
            truth_kind="delivery",
            facts={"update_id": 123, "telegram_user_id": "user-1"},
        )
        record_event(
            self.state_db,
            event_type="delivery_succeeded",
            component="telegram_runtime",
            summary="delivery succeeded",
            run_id=run.run_id,
            request_id="req-ledger",
            session_id="session:test",
            channel_id="telegram",
            actor_id="telegram_runtime",
            truth_kind="delivery",
            facts={"update_id": 123, "telegram_user_id": "user-1"},
        )
        record_event(
            self.state_db,
            event_type="plugin_or_chip_influence_recorded",
            component="researcher_bridge",
            summary="chip influence",
            run_id=run.run_id,
            request_id="req-ledger",
            channel_id="telegram",
            actor_id="researcher_bridge",
            facts={"keepability": "ephemeral_context"},
            provenance={"source_kind": "chip_hook", "source_ref": "chip:test"},
        )
        close_run(
            self.state_db,
            run_id=run.run_id,
            status="closed",
            close_reason="test_complete",
            summary="run closed",
        )
        self.config_manager.set_path("runtime.install.profile", "telegram-agent")

        with self.state_db.connect() as conn:
            counts = {
                "event_log": int(conn.execute("SELECT COUNT(*) AS c FROM event_log").fetchone()["c"]),
                "run_registry": int(conn.execute("SELECT COUNT(*) AS c FROM run_registry").fetchone()["c"]),
                "delivery_registry": int(conn.execute("SELECT COUNT(*) AS c FROM delivery_registry").fetchone()["c"]),
                "config_mutation_log": int(conn.execute("SELECT COUNT(*) AS c FROM config_mutation_log").fetchone()["c"]),
                "provenance_mutation_log": int(conn.execute("SELECT COUNT(*) AS c FROM provenance_mutation_log").fetchone()["c"]),
            }

        self.assertGreaterEqual(counts["event_log"], 4)
        self.assertEqual(counts["run_registry"], 1)
        self.assertEqual(counts["delivery_registry"], 1)
        self.assertGreaterEqual(counts["config_mutation_log"], 1)
        self.assertEqual(counts["provenance_mutation_log"], 1)

    def test_secret_policy_detects_common_secret_families(self) -> None:
        self.assertTrue(looks_secret_like("eyJ" + "hbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.c2lnbmF0dXJl"))
        self.assertTrue(looks_secret_like("TELEGRAM_BOT_TOKEN=1234567890:abcdefghijklmnopqrstuvwxyzABCDE"))
        self.assertTrue(looks_secret_like("api_key: " + "sk-proj-" + "abcdefghijklmnopqrstuvwxyz123456"))
        self.assertTrue(looks_secret_like("Authorization: Basic dXNlcjpzdXBlci1zZWNyZXQtcGFzc3dvcmQtMTIz"))
        self.assertTrue(looks_secret_like('{"client_secret":"ZXlKaGJHY2lPaUpJVXpJMU5pSjkuZXlKemRXSWlPaUl4TWpNME5UWTNPRGt3SW4wLnNpZw=="}'))
        self.assertTrue(looks_secret_like("database_url=https://operator:Sup3rSecretPassw0rd@example.com/app"))
        self.assertTrue(looks_secret_like("-----BEGIN " + "PRIVATE KEY-----\nabc\n-----END " + "PRIVATE KEY-----"))
        self.assertFalse(looks_secret_like("This is a normal operational note with no credentials in it."))
        self.assertFalse(looks_secret_like("tokenization_status=phase-b-specialization"))

    def test_config_set_path_records_typed_mutation_audit(self) -> None:
        self.config_manager.set_path("runtime.install.profile", "telegram-agent")

        with self.state_db.connect() as conn:
            row = conn.execute(
                """
                SELECT actor_id, reason_code, rollback_ref, status
                FROM config_mutation_audit
                WHERE target_path = 'runtime.install.profile'
                ORDER BY created_at DESC, mutation_id DESC
                LIMIT 1
                """
            ).fetchone()

        self.assertIsNotNone(row)
        self.assertEqual(row["actor_id"], "local-operator")
        self.assertEqual(row["reason_code"], "config_set_path")
        self.assertEqual(row["status"], "applied")
        self.assertTrue(row["rollback_ref"])
        self.assertTrue(latest_events_by_type(self.state_db, event_type="config_mutation_applied", limit=10))

    def test_env_secret_noop_upsert_is_rejected_without_rewrite(self) -> None:
        self.config_manager.upsert_env_secret("TEST_SECRET", "abc123")
        before_content = self.config_manager.paths.env_file.read_text(encoding="utf-8")

        self.config_manager.upsert_env_secret("TEST_SECRET", "abc123")
        after_content = self.config_manager.paths.env_file.read_text(encoding="utf-8")

        with self.state_db.connect() as conn:
            row = conn.execute(
                """
                SELECT status
                FROM config_mutation_log
                WHERE target_path = 'TEST_SECRET'
                ORDER BY recorded_at DESC, mutation_id DESC
                LIMIT 1
                """
            ).fetchone()

        self.assertEqual(before_content, after_content)
        self.assertIsNotNone(row)
        self.assertEqual(row["status"], "rejected")

    def test_humans_table_exposes_nullable_user_address_column(self) -> None:
        # P2-1 (docs/PERSONALITY_PHASE2_PLAN_2026-04-10.md): the v2 onboarding
        # state machine stores the operator's preferred salutation on
        # humans.user_address. The column must exist on fresh databases,
        # must be NULL by default, and must accept round-trip updates.
        with self.state_db.connect() as conn:
            columns = {
                str(row["name"]): row
                for row in conn.execute("PRAGMA table_info(humans)").fetchall()
            }
            self.assertIn("user_address", columns)
            self.assertEqual(str(columns["user_address"]["type"]).upper(), "TEXT")
            self.assertEqual(int(columns["user_address"]["notnull"]), 0)

            conn.execute(
                """
                INSERT INTO humans(human_id, display_name, status)
                VALUES (?, ?, 'active')
                """,
                ("human:user-address-test", "Alice"),
            )
            conn.commit()

            row = conn.execute(
                "SELECT user_address FROM humans WHERE human_id = ?",
                ("human:user-address-test",),
            ).fetchone()
            self.assertIsNone(row["user_address"])

            conn.execute(
                "UPDATE humans SET user_address = ? WHERE human_id = ?",
                ("boss", "human:user-address-test"),
            )
            conn.commit()

            row = conn.execute(
                "SELECT user_address FROM humans WHERE human_id = ?",
                ("human:user-address-test",),
            ).fetchone()
            self.assertEqual(row["user_address"], "boss")

    def test_watchtower_agent_identity_panel_counts_swarm_links_and_aliases(self) -> None:
        with self.state_db.connect() as conn:
            conn.execute(
                """
                INSERT INTO humans(human_id, display_name, status)
                VALUES (?, ?, 'active')
                """,
                ("human:test", "Alice"),
            )
            conn.commit()

        link_spark_swarm_agent(
            state_db=self.state_db,
            human_id="human:test",
            swarm_agent_id="swarm-agent:test",
            agent_name="Atlas",
            metadata={"workspace_id": "ws-test"},
        )

        snapshot = build_watchtower_snapshot(self.state_db)
        panel = snapshot["panels"]["agent_identity"]

        self.assertEqual(panel["counts"]["canonical_agents"], 1)
        self.assertEqual(panel["counts"]["spark_swarm"], 1)
        self.assertEqual(panel["counts"]["aliases"], 1)
        self.assertEqual(panel["counts"]["identity_conflicts"], 0)

        report = run_doctor(self.config_manager, self.state_db)
        checks = {check.name: check for check in report.checks}
        self.assertIn("watchtower-agent-identity", checks)
        self.assertTrue(checks["watchtower-agent-identity"].ok)

    def test_watchtower_agent_identity_panel_surfaces_identity_conflict(self) -> None:
        with self.state_db.connect() as conn:
            conn.execute(
                """
                INSERT INTO humans(human_id, display_name, status)
                VALUES (?, ?, 'active')
                """,
                ("human:test", "Alice"),
            )
            conn.commit()

        link_spark_swarm_agent(
            state_db=self.state_db,
            human_id="human:test",
            swarm_agent_id="swarm-agent:atlas",
            agent_name="Atlas",
            metadata={"workspace_id": "ws-test"},
        )
        link_spark_swarm_agent(
            state_db=self.state_db,
            human_id="human:test",
            swarm_agent_id="swarm-agent:zephyr",
            agent_name="Zephyr",
            metadata={"workspace_id": "ws-test"},
        )

        snapshot = build_watchtower_snapshot(self.state_db)
        panel = snapshot["panels"]["agent_identity"]

        self.assertEqual(panel["counts"]["canonical_agents"], 1)
        self.assertEqual(panel["counts"]["spark_swarm"], 1)
        self.assertEqual(panel["counts"]["identity_conflicts"], 1)
        self.assertEqual(len(panel["recent_conflicts"]), 1)
        self.assertEqual(panel["recent_conflicts"][0]["canonical_agent_id"], "swarm-agent:zephyr")
        self.assertEqual(panel["recent_conflicts"][0]["conflict_agent_id"], "swarm-agent:atlas")
        self.assertEqual(panel["recent_conflicts"][0]["conflict_reason"], "multiple_agent_ids_for_human")

        report = run_doctor(self.config_manager, self.state_db)
        checks = {check.name: check for check in report.checks}
        self.assertIn("watchtower-agent-identity", checks)
        self.assertFalse(checks["watchtower-agent-identity"].ok)

    def test_outbound_strips_em_dashes_before_delivery(self) -> None:
        guarded = prepare_outbound_text(
            text="Two chips routing live \u2014 X Content and Startup. Yeah \u2014 fair point.",
            bridge_mode=None,
            max_reply_chars=4000,
            redact_secret_like_replies=False,
        )
        self.assertIn("replace_em_dashes", guarded["actions"])
        self.assertNotIn("\u2014", guarded["text"])
        self.assertIn(" - ", guarded["text"])

    def test_outbound_preserves_ascii_hyphen_identifiers(self) -> None:
        guarded = prepare_outbound_text(
            text="Run `/swarm session startup-operator session-123` for domain-chip-voice-comms.",
            bridge_mode=None,
            max_reply_chars=4000,
            redact_secret_like_replies=False,
        )

        self.assertNotIn("replace_em_dashes", guarded["actions"])
        self.assertEqual(
            guarded["text"],
            "Run `/swarm session startup-operator session-123` for domain-chip-voice-comms.",
        )

    def test_outbound_no_op_when_no_em_dashes(self) -> None:
        guarded = prepare_outbound_text(
            text="Plain reply. No funny dashes here.",
            bridge_mode=None,
            max_reply_chars=4000,
            redact_secret_like_replies=False,
        )
        self.assertNotIn("replace_em_dashes", guarded["actions"])
        self.assertEqual(guarded["text"], "Plain reply. No funny dashes here.")

    def test_outbound_redacts_sensitive_text_before_delivery_chunks(self) -> None:
        guarded = prepare_outbound_text(
            text="Use API key sk-proj-abcdefghijklmnopqrstuvwxyz123456 for setup.",
            bridge_mode=None,
            max_reply_chars=4000,
            redact_secret_like_replies=False,
        )

        self.assertIn("redact_sensitive_text", guarded["actions"])
        self.assertNotIn("sk-proj-", guarded["text"])
        self.assertIn("<redacted api key>", guarded["text"])

    def test_outbound_secret_block_records_violation_and_quarantine(self) -> None:
        guarded = prepare_outbound_text(
            state_db=self.state_db,
            text="Bearer abcdefghijklmnopqrstuvwxyz1234567890",
            bridge_mode=None,
            max_reply_chars=3500,
            redact_secret_like_replies=True,
            run_id="run-test",
            request_id="req-test",
            trace_ref="trace-test",
            channel_id="telegram",
            session_id="session:test",
            actor_id="telegram_runtime",
        )

        self.assertIn("block_secret_like_reply", guarded["actions"])
        self.assertTrue(latest_events_by_type(self.state_db, event_type="secret_boundary_violation", limit=10))
        policy_blocks = recent_policy_gate_records(self.state_db, limit=10)
        self.assertTrue(policy_blocks)
        self.assertEqual(policy_blocks[0]["gate_name"], "secret_boundary")
        with self.state_db.connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS c FROM quarantine_records").fetchone()
        self.assertEqual(int(row["c"]), 1)

    def test_stop_ship_flags_intent_without_dispatch_or_result(self) -> None:
        run = open_run(
            self.state_db,
            run_kind="telegram_update",
            origin_surface="telegram_runtime",
            summary="opened",
            request_id="req-1",
            channel_id="telegram",
            actor_id="test",
        )
        record_event(
            self.state_db,
            event_type="intent_committed",
            component="telegram_runtime",
            summary="intent only",
            run_id=run.run_id,
            request_id="req-1",
            channel_id="telegram",
            actor_id="test",
        )

        issues = {issue.name: issue for issue in evaluate_stop_ship_issues(config_manager=self.config_manager, state_db=self.state_db)}
        self.assertFalse(issues["stop_ship_intent_without_proof"].ok)

    def test_stop_ship_accepts_terminal_run_closure_as_execution_proof(self) -> None:
        run = open_run(
            self.state_db,
            run_kind="telegram_update",
            origin_surface="telegram_runtime",
            summary="opened",
            request_id="req-1b",
            channel_id="telegram",
            actor_id="test",
        )
        record_event(
            self.state_db,
            event_type="intent_committed",
            component="telegram_runtime",
            summary="intent closed locally",
            run_id=run.run_id,
            request_id="req-1b",
            channel_id="telegram",
            actor_id="test",
        )
        close_run(
            self.state_db,
            run_id=run.run_id,
            status="closed",
            close_reason="local_command_completed",
            summary="closed after local command handling",
        )

        issues = {
            issue.name: issue
            for issue in evaluate_stop_ship_issues(config_manager=self.config_manager, state_db=self.state_db)
        }
        self.assertTrue(issues["stop_ship_intent_without_proof"].ok)

    def test_stop_ship_contradictions_accumulate_and_resolve(self) -> None:
        run = open_run(
            self.state_db,
            run_kind="telegram_update",
            origin_surface="telegram_runtime",
            summary="opened",
            request_id="req-contradiction",
            channel_id="telegram",
            actor_id="telegram_runtime",
        )
        record_event(
            self.state_db,
            event_type="intent_committed",
            component="telegram_runtime",
            summary="intent only",
            run_id=run.run_id,
            request_id="req-contradiction",
            channel_id="telegram",
            actor_id="telegram_runtime",
        )

        evaluate_stop_ship_issues(
            config_manager=self.config_manager,
            state_db=self.state_db,
            emit_contradictions=True,
        )
        evaluate_stop_ship_issues(
            config_manager=self.config_manager,
            state_db=self.state_db,
            emit_contradictions=True,
        )

        open_rows = recent_contradictions(self.state_db, status="open", limit=10)
        self.assertEqual(len(open_rows), 1)
        self.assertEqual(open_rows[0]["contradiction_key"], "stop_ship:stop_ship_intent_without_proof")
        self.assertEqual(int(open_rows[0]["occurrence_count"]), 2)

        record_event(
            self.state_db,
            event_type="dispatch_failed",
            component="telegram_runtime",
            summary="dispatch failed closes contradiction",
            run_id=run.run_id,
            request_id="req-contradiction",
            channel_id="telegram",
            actor_id="telegram_runtime",
            facts={"failure_family": "test"},
        )

        evaluate_stop_ship_issues(
            config_manager=self.config_manager,
            state_db=self.state_db,
            emit_contradictions=True,
        )

        resolved_rows = recent_contradictions(self.state_db, status="resolved", limit=10)
        self.assertTrue(resolved_rows)
        self.assertEqual(resolved_rows[0]["contradiction_key"], "stop_ship:stop_ship_intent_without_proof")

    def test_jobs_tick_records_closed_background_run(self) -> None:
        jobs_tick(self.config_manager, self.state_db)

        with self.state_db.connect() as conn:
            row = conn.execute(
                """
                SELECT status, close_reason
                FROM builder_runs
                WHERE run_kind = 'job:oauth_refresh_maintenance'
                ORDER BY opened_at DESC, run_id DESC
                LIMIT 1
                """
            ).fetchone()

        self.assertIsNotNone(row)
        self.assertEqual(row["status"], "closed")
        self.assertEqual(row["close_reason"], "job_completed")

    def test_jobs_tick_records_failed_run_on_exception(self) -> None:
        with patch("spark_intelligence.jobs.service.run_oauth_refresh_maintenance", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                jobs_tick(self.config_manager, self.state_db)

        with self.state_db.connect() as conn:
            row = conn.execute(
                """
                SELECT status, close_reason
                FROM builder_runs
                WHERE run_kind = 'job:oauth_refresh_maintenance'
                ORDER BY opened_at DESC, run_id DESC
                LIMIT 1
                """
            ).fetchone()

        self.assertIsNotNone(row)
        self.assertEqual(row["status"], "failed")
        self.assertEqual(row["close_reason"], "job_exception")
        events = latest_events_by_type(self.state_db, event_type="run_failed", limit=10)
        self.assertTrue(events)

    def test_environment_parity_check_fails_on_conflicting_snapshots(self) -> None:
        record_environment_snapshot(
            self.state_db,
            surface="doctor_cli",
            summary="cli",
            provider_id="custom",
            provider_model="MiniMax-M2.7",
            provider_base_url="https://api.minimax.io/v1",
            provider_execution_transport="direct_http",
            runtime_root="C:/researcher",
            config_path="C:/researcher/config.json",
        )
        record_environment_snapshot(
            self.state_db,
            surface="gateway_runtime",
            summary="gateway",
            provider_id="custom",
            provider_model="MiniMax-M2.7",
            provider_base_url="https://api.example.com/v2",
            provider_execution_transport="direct_http",
            runtime_root="C:/researcher",
            config_path="C:/researcher/config.json",
        )

        issues = {issue.name: issue for issue in evaluate_stop_ship_issues(config_manager=self.config_manager, state_db=self.state_db)}
        self.assertFalse(issues["stop_ship_environment_parity"].ok)

    def test_environment_parity_ignores_swarm_bridge_runtime_root_difference(self) -> None:
        record_environment_snapshot(
            self.state_db,
            surface="doctor_cli",
            summary="cli",
            provider_id="custom",
            provider_model="MiniMax-M2.7",
            provider_base_url="https://api.minimax.io/v1",
            provider_execution_transport="direct_http",
            runtime_root="C:/researcher",
            config_path="C:/researcher/config.json",
        )
        record_environment_snapshot(
            self.state_db,
            surface="swarm_bridge",
            summary="swarm",
            provider_id="custom",
            provider_model="MiniMax-M2.7",
            provider_base_url="https://api.minimax.io/v1",
            provider_execution_transport="direct_http",
            runtime_root="C:/spark-swarm",
            config_path="C:/researcher/config.json",
        )

        issues = {
            issue.name: issue
            for issue in evaluate_stop_ship_issues(config_manager=self.config_manager, state_db=self.state_db)
        }
        self.assertTrue(issues["stop_ship_environment_parity"].ok)

    def test_watchtower_snapshot_computes_health_dimensions_from_typed_truth(self) -> None:
        run = open_run(
            self.state_db,
            run_kind="telegram_update",
            origin_surface="telegram_runtime",
            summary="opened",
            request_id="req-watchtower",
            session_id="session:watchtower",
            channel_id="telegram",
            actor_id="telegram_runtime",
        )
        record_event(
            self.state_db,
            event_type="intent_committed",
            component="telegram_runtime",
            summary="intent committed",
            run_id=run.run_id,
            request_id="req-watchtower",
            session_id="session:watchtower",
            channel_id="telegram",
            actor_id="telegram_runtime",
        )
        record_event(
            self.state_db,
            event_type="delivery_attempted",
            component="telegram_runtime",
            summary="delivery attempted",
            run_id=run.run_id,
            request_id="req-watchtower",
            session_id="session:watchtower",
            channel_id="telegram",
            actor_id="telegram_runtime",
            facts={"update_id": 501, "telegram_user_id": "user-watchtower"},
        )
        background_run = open_run(
            self.state_db,
            run_kind="job:watchtower_test",
            origin_surface="jobs_tick",
            summary="background run opened",
            request_id="req-watchtower-job",
            actor_id="jobs_tick",
        )
        close_run(
            self.state_db,
            run_id=background_run.run_id,
            status="stalled",
            close_reason="watchtower_test_stall",
            summary="background run stalled for watchtower test",
        )
        record_environment_snapshot(
            self.state_db,
            surface="doctor_cli",
            summary="cli",
            provider_id="custom",
            provider_model="model-a",
            provider_base_url="https://api.one.example/v1",
            provider_execution_transport="direct_http",
            runtime_root="C:/researcher",
            config_path="C:/researcher/config.json",
        )
        record_environment_snapshot(
            self.state_db,
            surface="gateway_runtime",
            summary="gateway",
            provider_id="custom",
            provider_model="model-a",
            provider_base_url="https://api.two.example/v1",
            provider_execution_transport="direct_http",
            runtime_root="C:/researcher",
            config_path="C:/researcher/config.json",
        )

        snapshot = build_watchtower_snapshot(self.state_db)

        self.assertEqual(snapshot["health_dimensions"]["ingress_health"]["state"], "healthy")
        self.assertEqual(snapshot["health_dimensions"]["execution_health"]["state"], "execution_impaired")
        self.assertEqual(snapshot["health_dimensions"]["delivery_health"]["state"], "delivery_impaired")
        self.assertEqual(snapshot["health_dimensions"]["scheduler_freshness"]["state"], "stalled")
        self.assertEqual(snapshot["health_dimensions"]["environment_parity"]["state"], "parity_broken")
        self.assertEqual(snapshot["top_level_state"], "parity_broken")
        self.assertEqual(snapshot["contradictions"]["counts"]["open"], 0)
        self.assertEqual(snapshot["panels"]["provenance_and_quarantine"]["counts"]["policy_gate_blocks"], 0)
        self.assertEqual(snapshot["panels"]["memory_lane_hygiene"]["counts"]["promotion_attempts"], 0)

    def test_watchtower_execution_health_ignores_unbound_dispatch_started_rows(self) -> None:
        record_event(
            self.state_db,
            event_type="dispatch_started",
            component="researcher_bridge",
            summary="dispatch started without run binding",
            request_id="req-unbound-dispatch",
            actor_id="researcher_bridge",
        )

        snapshot = build_watchtower_snapshot(self.state_db)

        self.assertEqual(snapshot["health_dimensions"]["execution_health"]["state"], "healthy")

    def test_classified_bridge_output_creates_typed_memory_lane_record(self) -> None:
        record_event(
            self.state_db,
            event_type="tool_result_received",
            component="researcher_bridge",
            summary="typed bridge result",
            request_id="req-memory-lane",
            trace_ref="trace:req-memory-lane",
            actor_id="researcher_bridge",
            facts={
                "bridge_mode": "external_typed",
                "routing_decision": "researcher_advisory",
                "keepability": "ephemeral_context",
                "promotion_disposition": "not_promotable",
            },
        )

        lane_records = recent_memory_lane_records(self.state_db, limit=10)

        self.assertEqual(len(lane_records), 1)
        self.assertEqual(lane_records[0]["artifact_kind"], "bridge_output")
        self.assertEqual(lane_records[0]["artifact_lane"], "execution_evidence")
        self.assertEqual(lane_records[0]["promotion_target_lane"], "durable_intelligence_memory")
        self.assertEqual(lane_records[0]["status"], "blocked")

    def test_watchtower_memory_lane_panel_uses_typed_lane_records(self) -> None:
        record_event(
            self.state_db,
            event_type="plugin_or_chip_influence_recorded",
            component="researcher_bridge",
            summary="operator debug influence",
            request_id="req-memory-watchtower",
            actor_id="researcher_bridge",
            facts={"keepability": "operator_debug_only"},
            provenance={"source_kind": "chip_hook", "source_ref": "chip:test"},
        )
        record_event(
            self.state_db,
            event_type="tool_result_received",
            component="researcher_bridge",
            summary="bridge result",
            request_id="req-memory-watchtower",
            trace_ref="trace:req-memory-watchtower",
            actor_id="researcher_bridge",
            facts={
                "keepability": "ephemeral_context",
                "promotion_disposition": "not_promotable",
            },
        )

        snapshot = build_watchtower_snapshot(self.state_db)
        panel = snapshot["panels"]["memory_lane_hygiene"]

        self.assertEqual(panel["counts"]["promotion_attempts"], 2)
        self.assertEqual(panel["counts"]["blocked_promotions"], 2)
        self.assertEqual(panel["counts"]["ops_residue_volume"], 1)
        self.assertTrue(panel["lane_labels_present"]["execution_evidence"])
        self.assertTrue(panel["lane_labels_present"]["ops_transcripts"])
        self.assertFalse(panel["lane_labels_present"]["durable_intelligence_memory"])

    def test_repair_missing_memory_lane_records_backfills_classified_bridge_events(self) -> None:
        record_event(
            self.state_db,
            event_type="tool_result_received",
            component="researcher_bridge",
            summary="bridge result that lost lane mirror",
            request_id="req-memory-repair",
            trace_ref="trace:req-memory-repair",
            actor_id="researcher_bridge",
            facts={
                "bridge_mode": "external_typed",
                "routing_decision": "researcher_advisory",
                "keepability": "ephemeral_context",
                "promotion_disposition": "not_promotable",
            },
        )

        lane_records = recent_memory_lane_records(self.state_db, limit=10)
        self.assertEqual(len(lane_records), 1)
        event_id = str(lane_records[0]["event_id"])

        with self.state_db.connect() as conn:
            conn.execute("DELETE FROM memory_lane_records WHERE event_id = ?", (event_id,))
            conn.commit()

        repaired = repair_missing_memory_lane_records(self.state_db)
        self.assertEqual(repaired, 1)

        lane_records = recent_memory_lane_records(self.state_db, limit=10)
        self.assertEqual(len(lane_records), 1)
        self.assertEqual(str(lane_records[0]["event_id"]), event_id)

    def test_watchtower_session_integrity_panel_tracks_reset_and_resume_surfaces(self) -> None:
        detect_and_persist_nl_preferences(
            human_id="human:test",
            user_message="be more direct",
            state_db=self.state_db,
            config_manager=self.config_manager,
            session_id="session:test",
            turn_id="turn:pref",
            channel_kind="telegram",
        )
        detect_personality_query(
            user_message="reset personality",
            human_id="human:test",
            state_db=self.state_db,
            profile=load_personality_profile(
                human_id="human:test",
                state_db=self.state_db,
                config_manager=self.config_manager,
            ),
            config_manager=self.config_manager,
            session_id="session:test",
            turn_id="turn:reset",
        )
        record_pairing_context(
            state_db=self.state_db,
            channel_id="telegram",
            external_user_id="111",
            context={
                "telegram_username": "alice",
                "chat_id": "chat-1",
                "last_message_text": "hello",
                "last_seen_at": "2026-03-28T00:00:00+00:00",
            },
        )
        record_pairing_context(
            state_db=self.state_db,
            channel_id="telegram",
            external_user_id="111",
            context={"last_seen_at": "2026-03-28T00:05:00+00:00"},
        )

        snapshot = build_watchtower_snapshot(self.state_db)
        panel = snapshot["panels"]["session_integrity"]
        issues = {
            issue.name: issue
            for issue in evaluate_stop_ship_issues(config_manager=self.config_manager, state_db=self.state_db)
        }

        self.assertGreaterEqual(panel["counts"]["registered_reset_sensitive_keys"], 1)
        self.assertEqual(panel["counts"]["recent_reset_events"], 1)
        self.assertEqual(panel["counts"]["resume_richness_guard_interventions"], 1)
        self.assertEqual(panel["counts"]["active_reset_sensitive_keys"], 0)
        self.assertTrue(issues["stop_ship_reset_integrity"].ok)

    def test_promotion_candidate_records_explicit_policy_gate_blocks(self) -> None:
        record_event(
            self.state_db,
            event_type="tool_result_received",
            component="researcher_bridge",
            summary="promotion candidate",
            request_id="req-promotion-gates",
            actor_id="researcher_bridge",
            facts={
                "keepability": "ephemeral_context",
                "promotion_disposition": "durable_candidate",
            },
        )

        policy_blocks = recent_policy_gate_records(self.state_db, limit=10)
        gate_names = {str(row["gate_name"]) for row in policy_blocks}

        self.assertIn("keepability_check", gate_names)
        self.assertIn("residue_check", gate_names)

    def test_watchtower_observer_incident_panel_classifies_contamination_families(self) -> None:
        record_event(
            self.state_db,
            event_type="plugin_or_chip_influence_recorded",
            component="researcher_bridge",
            summary="unlabeled provenance mutation",
            request_id="req-observer-prov",
            actor_id="researcher_bridge",
            facts={"keepability": "ephemeral_context"},
            provenance={},
        )
        record_event(
            self.state_db,
            event_type="tool_result_received",
            component="researcher_bridge",
            summary="promotion candidate",
            request_id="req-observer-promotion",
            actor_id="researcher_bridge",
            facts={
                "keepability": "ephemeral_context",
                "promotion_disposition": "durable_candidate",
            },
        )
        record_pairing_context(
            state_db=self.state_db,
            channel_id="telegram",
            external_user_id="111",
            context={
                "telegram_username": "alice",
                "chat_id": "chat-1",
                "last_message_text": "richer state",
                "last_seen_at": "2026-03-28T00:00:00+00:00",
            },
        )
        record_pairing_context(
            state_db=self.state_db,
            channel_id="telegram",
            external_user_id="111",
            context={"last_seen_at": "2026-03-28T00:05:00+00:00"},
        )

        snapshot = build_watchtower_snapshot(self.state_db)
        panel = snapshot["panels"]["observer_incidents"]

        self.assertGreaterEqual(panel["counts"]["total"], 3)
        self.assertGreaterEqual(panel["counts"]["actionable_total"], 2)
        self.assertEqual(panel["counts"]["informational_total"], 1)
        self.assertIn("provenance_contamination", panel["counts_by_class"])
        self.assertIn("promotion_contamination", panel["counts_by_class"])
        self.assertIn("resume_risk_intercepted", panel["counts_by_class"])
        packet_panel = snapshot["panels"]["observer_packets"]
        self.assertGreaterEqual(packet_panel["counts"]["total"], 8)
        self.assertGreaterEqual(packet_panel["counts"]["distinct_kinds"], 4)
        self.assertIn("self_observation", packet_panel["counts_by_kind"])
        self.assertIn("incident_report", packet_panel["counts_by_kind"])
        self.assertIn("repair_plan", packet_panel["counts_by_kind"])
        self.assertIn("reflection_digest", packet_panel["counts_by_kind"])
        self.assertIn("security_advisory", packet_panel["counts_by_kind"])
        self.assertTrue(any(packet.get("evidence_refs") for packet in packet_panel["recent_packets"]))

    def test_watchtower_persists_observer_packet_records(self) -> None:
        record_event(
            self.state_db,
            event_type="plugin_or_chip_influence_recorded",
            component="researcher_bridge",
            summary="unlabeled provenance mutation",
            request_id="req-persisted-observer-packets",
            actor_id="researcher_bridge",
            facts={"keepability": "ephemeral_context"},
            provenance={},
        )
        record_pairing_context(
            state_db=self.state_db,
            channel_id="telegram",
            external_user_id="111",
            context={
                "telegram_username": "alice",
                "chat_id": "chat-1",
                "last_message_text": "richer state",
                "last_seen_at": "2026-03-28T00:00:00+00:00",
            },
        )
        record_pairing_context(
            state_db=self.state_db,
            channel_id="telegram",
            external_user_id="111",
            context={"last_seen_at": "2026-03-28T00:05:00+00:00"},
        )

        snapshot = build_watchtower_snapshot(self.state_db)
        rows = recent_observer_packet_records(self.state_db, limit=20)

        self.assertGreaterEqual(snapshot["panels"]["observer_packets"]["counts"]["total"], 4)
        self.assertTrue(rows)
        self.assertTrue(all(isinstance(row["active"], bool) for row in rows))
        self.assertTrue(all(row["active"] for row in rows))
        packet_kinds = {str(row["packet_kind"]) for row in rows}
        self.assertIn("self_observation", packet_kinds)
        self.assertIn("reflection_digest", packet_kinds)
        provenance_rows = [
            row for row in rows if str(row.get("source_incident_class") or "") == "provenance_contamination"
        ]
        self.assertTrue(provenance_rows)
        self.assertTrue(any(str(row.get("source_item_ref") or "") for row in provenance_rows))
        self.assertTrue(any(str(row.get("source_ref") or "") for row in provenance_rows))
        self.assertTrue(all(isinstance(row.get("first_recorded_at"), str) for row in rows))
        self.assertTrue(all(isinstance(row.get("last_seen_at"), str) for row in rows))

    def test_stop_ship_flags_invalid_memory_contract_events(self) -> None:
        record_event(
            self.state_db,
            event_type="memory_write_abstained",
            component="memory_orchestrator",
            summary="memory contract violation",
            request_id="req-memory-contract",
            session_id="session:test",
            human_id="human:test",
            actor_id="memory_orchestrator",
            facts={
                "operation": "update",
                "method": "write_observation",
                "memory_role": "unknown",
                "accepted_count": 0,
                "rejected_count": 1,
                "skipped_count": 0,
                "reason": "invalid_memory_role",
            },
            provenance={"memory_role": "unknown"},
        )

        snapshot = build_watchtower_snapshot(self.state_db)
        memory_panel = snapshot["panels"]["memory_shadow"]
        observer_panel = snapshot["panels"]["observer_incidents"]
        packet_panel = snapshot["panels"]["observer_packets"]
        issues = {
            issue.name: issue
            for issue in evaluate_stop_ship_issues(config_manager=self.config_manager, state_db=self.state_db)
        }
        report = run_doctor(self.config_manager, self.state_db)
        checks = {check.name: check for check in report.checks}

        self.assertEqual(memory_panel["counts"]["contract_violations"], 1)
        self.assertIn("memory_contract_drift", observer_panel["counts_by_class"])
        self.assertIn("incident_report", packet_panel["counts_by_kind"])
        self.assertFalse(issues["stop_ship_memory_contract"].ok)
        self.assertIn("violated the Builder memory role contract", issues["stop_ship_memory_contract"].detail)
        self.assertIn("watchtower-memory-contract", checks)
        self.assertFalse(checks["watchtower-memory-contract"].ok)

    def test_stop_ship_ignores_reconciled_memory_role_from_sdk_provenance(self) -> None:
        record_event(
            self.state_db,
            event_type="memory_read_abstained",
            component="memory_orchestrator",
            summary="retrieval evidence reconciled from sdk provenance",
            request_id="req-memory-contract-reconciled",
            session_id="session:test",
            human_id="human:test",
            actor_id="memory_orchestrator",
            facts={
                "method": "retrieve_evidence",
                "memory_role": "unknown",
                "record_count": 0,
                "reason": "invalid_memory_role",
            },
            provenance={
                "memory_role": "unknown",
                "sdk_provenance": [
                    {
                        "memory_role": "unknown",
                        "metadata": {"memory_role": "current_state"},
                    }
                ],
            },
        )

        snapshot = build_watchtower_snapshot(self.state_db)
        memory_panel = snapshot["panels"]["memory_shadow"]
        observer_panel = snapshot["panels"]["observer_incidents"]
        issues = {
            issue.name: issue
            for issue in evaluate_stop_ship_issues(config_manager=self.config_manager, state_db=self.state_db)
        }
        report = run_doctor(self.config_manager, self.state_db)
        checks = {check.name: check for check in report.checks}

        self.assertEqual(memory_panel["counts"]["contract_violations"], 0)
        self.assertEqual(memory_panel["counts"]["invalid_role_events"], 0)
        self.assertNotIn("memory_contract_drift", observer_panel["counts_by_class"])
        self.assertTrue(issues["stop_ship_memory_contract"].ok)
        self.assertIn("watchtower-memory-contract", checks)
        self.assertTrue(checks["watchtower-memory-contract"].ok)

    def test_build_researcher_reply_records_chip_influence_provenance(self) -> None:
        self.config_manager.set_path("spark.chips.active_keys", ["startup-yc"])
        self.config_manager.set_path("spark.specialization_paths.active_path_key", "startup-operator")

        result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-provenance",
            agent_id="agent:test",
            human_id="human:test",
            session_id="session:test",
            channel_kind="telegram",
            user_message="What should this startup focus on next?",
        )

        self.assertIn(result.routing_decision, {"bridge_disabled", "stub"})
        events = latest_events_by_type(self.state_db, event_type="plugin_or_chip_influence_recorded", limit=10)
        self.assertTrue(events)
        facts = events[0]["facts_json"]
        self.assertEqual(facts["keepability"], "ephemeral_context")

    def test_environment_parity_accepts_windows_and_wsl_paths_for_same_runtime(self) -> None:
        record_environment_snapshot(
            self.state_db,
            surface="doctor_cli",
            summary="doctor",
            runtime_root=r"C:\Users\USER\Desktop\spark-researcher",
            config_path=r"C:\Users\USER\Desktop\spark-researcher\spark-researcher.project.json",
        )
        record_environment_snapshot(
            self.state_db,
            surface="researcher_bridge",
            summary="bridge",
            runtime_root="/mnt/c/Users/USER/Desktop/spark-researcher",
            config_path="/mnt/c/Users/USER/Desktop/spark-researcher/spark-researcher.project.json",
        )
        record_environment_snapshot(
            self.state_db,
            surface="swarm_bridge",
            summary="swarm",
            runtime_root="/mnt/c/Users/USER/Desktop/spark-swarm",
            config_path=r"C:\Users\USER\Desktop\spark-researcher\spark-researcher.project.json",
        )

        issues = {
            issue.name: issue
            for issue in evaluate_stop_ship_issues(config_manager=self.config_manager, state_db=self.state_db)
        }

        self.assertTrue(issues["stop_ship_environment_parity"].ok)

    def test_build_researcher_reply_records_chip_hook_result_classification(self) -> None:
        self.config_manager.set_path("spark.chips.active_keys", ["startup-yc"])
        self.config_manager.set_path("spark.specialization_paths.active_path_key", "startup-operator")

        build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-chip-result-classification",
            agent_id="agent:test",
            human_id="human:test",
            session_id="session:test",
            channel_kind="telegram",
            user_message="What should this startup focus on next?",
        )

        events = latest_events_by_type(self.state_db, event_type="tool_result_received", limit=20)
        chip_events = [
            event
            for event in events
            if event.get("component") == "researcher_bridge"
            and str((event.get("provenance_json") or {}).get("source_kind") or "") == "chip_hook"
        ]
        self.assertTrue(chip_events)
        facts = chip_events[0]["facts_json"]
        self.assertEqual(facts["keepability"], "ephemeral_context")
        self.assertEqual(facts["promotion_disposition"], "not_promotable")

    def test_build_researcher_reply_records_output_keepability_for_stub_results(self) -> None:
        self.config_manager.set_path("spark.researcher.enabled", True)

        with patch(
            "spark_intelligence.researcher_bridge.advisory.discover_researcher_runtime_root",
            return_value=(None, "missing"),
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-output-keepability",
                agent_id="agent:test",
                human_id="human:test",
                session_id="session:test",
                channel_kind="telegram",
                user_message="hello",
            )

        self.assertEqual(result.routing_decision, "stub")
        self.assertEqual(result.output_keepability, "operator_debug_only")
        self.assertEqual(result.promotion_disposition, "not_promotable")
        events = latest_events_by_type(self.state_db, event_type="tool_result_received", limit=10)
        self.assertTrue(events)
        facts = events[0]["facts_json"]
        self.assertEqual(facts["keepability"], "operator_debug_only")
        self.assertEqual(facts["promotion_disposition"], "not_promotable")

    def test_build_researcher_reply_records_personality_influence_provenance(self) -> None:
        result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-personality",
            agent_id="agent:test",
            human_id="human:test",
            session_id="session:test",
            channel_kind="telegram",
            user_message="be more direct and stop hedging",
        )

        self.assertIn(result.routing_decision, {"bridge_disabled", "stub"})
        events = latest_events_by_type(self.state_db, event_type="plugin_or_chip_influence_recorded", limit=20)
        personality_events = [
            event
            for event in events
            if str((event.get("provenance_json") or {}).get("source_kind") or "").startswith("personality_")
        ]
        self.assertTrue(personality_events)
        facts = personality_events[0]["facts_json"]
        self.assertEqual(facts["keepability"], "user_preference_ephemeral")
        self.assertTrue(facts["detected_deltas"])

    def test_sync_attachment_snapshot_writes_typed_snapshot_storage(self) -> None:
        snapshot = sync_attachment_snapshot(config_manager=self.config_manager, state_db=self.state_db)

        with self.state_db.connect() as conn:
            row = conn.execute(
                """
                SELECT workspace_id, snapshot_path, warning_count, record_count, summary_json
                FROM attachment_state_snapshots
                ORDER BY generated_at DESC, created_at DESC
                LIMIT 1
                """
            ).fetchone()
            runtime_row = conn.execute(
                "SELECT value FROM runtime_state WHERE state_key = 'attachments:last_snapshot_path'"
            ).fetchone()

        self.assertIsNotNone(row)
        self.assertEqual(row["snapshot_path"], snapshot.snapshot_path)
        self.assertEqual(row["workspace_id"], snapshot.workspace_id)
        self.assertEqual(int(row["warning_count"]), len(snapshot.warnings))
        self.assertEqual(int(row["record_count"]), len(snapshot.records))
        self.assertEqual(json.loads(row["summary_json"])["workspace_id"], snapshot.workspace_id)
        self.assertFalse(json.loads(row["summary_json"])["identity_import"]["ready"])
        self.assertFalse(json.loads(row["summary_json"])["personality_import"]["ready"])
        self.assertEqual(runtime_row["value"], snapshot.snapshot_path)

    def test_watchtower_agent_identity_import_check_flags_builder_local_agents_without_identity_hook(self) -> None:
        self.add_telegram_channel()
        approve_pairing_result = approve_pairing(
            state_db=self.state_db,
            channel_id="telegram",
            external_user_id="111",
            display_name="Alice",
        )
        self.assertIn("human:telegram:111", approve_pairing_result)
        sync_attachment_snapshot(config_manager=self.config_manager, state_db=self.state_db)

        snapshot = build_watchtower_snapshot(self.state_db)
        panel = snapshot["panels"]["agent_identity"]

        self.assertEqual(panel["counts"]["builder_local"], 1)
        self.assertEqual(panel["counts"]["identity_import_ready"], 0)
        self.assertEqual(panel["counts"]["identity_hook_active_chip_records"], 0)
        self.assertFalse(panel["identity_import"]["ready"])

        report = run_doctor(self.config_manager, self.state_db)
        checks = {check.name: check for check in report.checks}
        self.assertIn("watchtower-agent-identity-import", checks)
        self.assertFalse(checks["watchtower-agent-identity-import"].ok)

    def test_watchtower_agent_identity_import_check_passes_with_active_identity_hook_chip(self) -> None:
        chip_root = create_fake_hook_chip(self.home, chip_key="spark-swarm")
        self.config_manager.set_path("spark.chips.roots", [str(chip_root)])
        self.config_manager.set_path("spark.chips.active_keys", ["spark-swarm"])
        self.add_telegram_channel()
        approve_pairing(
            state_db=self.state_db,
            channel_id="telegram",
            external_user_id="111",
            display_name="Alice",
        )
        sync_attachment_snapshot(config_manager=self.config_manager, state_db=self.state_db)

        snapshot = build_watchtower_snapshot(self.state_db)
        panel = snapshot["panels"]["agent_identity"]

        self.assertEqual(panel["counts"]["identity_hook_chip_records"], 1)
        self.assertEqual(panel["counts"]["identity_hook_active_chip_records"], 1)
        self.assertEqual(panel["counts"]["identity_import_ready"], 1)
        self.assertEqual(panel["identity_import"]["active_chip_keys"], ["spark-swarm"])
        self.assertTrue(panel["identity_import"]["ready"])

        report = run_doctor(self.config_manager, self.state_db)
        checks = {check.name: check for check in report.checks}
        self.assertIn("watchtower-agent-identity-import", checks)
        self.assertTrue(checks["watchtower-agent-identity-import"].ok)

    def test_operator_security_refreshes_stale_identity_import_snapshot(self) -> None:
        self.add_telegram_channel()
        approve_pairing(
            state_db=self.state_db,
            channel_id="telegram",
            external_user_id="111",
            display_name="Alice",
        )
        sync_attachment_snapshot(config_manager=self.config_manager, state_db=self.state_db)

        chip_root = create_fake_hook_chip(self.home, chip_key="spark-swarm")
        self.config_manager.set_path("spark.chips.roots", [str(chip_root)])
        self.config_manager.set_path("spark.chips.active_keys", ["spark-swarm"])

        report = build_operator_security_report(
            config_manager=self.config_manager,
            state_db=self.state_db,
            limit=20,
        )

        identity_panel = report.payload["watchtower"]["panels"]["agent_identity"]
        self.assertEqual(identity_panel["counts"]["identity_hook_chip_records"], 1)
        self.assertEqual(identity_panel["counts"]["identity_hook_active_chip_records"], 1)
        self.assertEqual(identity_panel["counts"]["identity_import_ready"], 1)
        self.assertEqual(identity_panel["identity_import"]["active_chip_keys"], ["spark-swarm"])

    def test_personality_state_uses_typed_tables_with_runtime_state_mirrors(self) -> None:
        deltas = detect_and_persist_nl_preferences(
            human_id="human:test",
            user_message="be more direct and stop hedging",
            state_db=self.state_db,
        )
        self.assertIsNotNone(deltas)

        with self.state_db.connect() as conn:
            row = conn.execute(
                "SELECT deltas_json FROM personality_trait_profiles WHERE human_id = ?",
                ("human:test",),
            ).fetchone()
        self.assertIsNotNone(row)
        stored_deltas = json.loads(row["deltas_json"])
        self.assertIn("directness", stored_deltas)

        for _ in range(10):
            record_observation(
                human_id="human:test",
                user_message="I'm confused and this makes no sense",
                traits_active={
                    "warmth": 0.5,
                    "directness": 0.5,
                    "playfulness": 0.5,
                    "pacing": 0.5,
                    "assertiveness": 0.5,
                },
                state_db=self.state_db,
            )

        with self.state_db.connect() as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS count FROM personality_observations WHERE human_id = ?",
                ("human:test",),
            ).fetchone()
        self.assertEqual(int(row["count"]), 10)

        evolved = maybe_evolve_traits(human_id="human:test", state_db=self.state_db)
        self.assertIsNotNone(evolved)
        self.assertIn("directness", evolved)

        with self.state_db.connect() as conn:
            observation_row = conn.execute(
                "SELECT COUNT(*) AS count FROM personality_observations WHERE human_id = ?",
                ("human:test",),
            ).fetchone()
            evolution_row = conn.execute(
                "SELECT COUNT(*) AS count FROM personality_evolution_events WHERE human_id = ?",
                ("human:test",),
            ).fetchone()
            runtime_profile = conn.execute(
                "SELECT value FROM runtime_state WHERE state_key = ?",
                ("personality:human:test:trait_deltas",),
            ).fetchone()

        self.assertEqual(int(observation_row["count"]), 0)
        self.assertEqual(int(evolution_row["count"]), 1)
        self.assertIsNotNone(runtime_profile)
        snapshot = build_watchtower_snapshot(self.state_db)
        personality_panel = snapshot["panels"]["personality"]
        self.assertEqual(personality_panel["counts"]["trait_profiles"], 1)
        self.assertEqual(personality_panel["counts"]["evolution_rows"], 1)
        self.assertEqual(personality_panel["counts"]["mirror_drift"], 0)

    def test_doctor_flags_personality_runtime_mirror_drift(self) -> None:
        with self.state_db.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO runtime_state(state_key, value, updated_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                """,
                (
                    "personality:human:drift:trait_deltas",
                    json.dumps({"deltas": {"directness": 0.4}, "updated_at": "2026-03-28T12:00:00Z"}),
                ),
            )
            conn.commit()

        snapshot = build_watchtower_snapshot(self.state_db)
        personality_panel = snapshot["panels"]["personality"]
        self.assertEqual(personality_panel["counts"]["mirror_drift"], 1)

        report = run_doctor(self.config_manager, self.state_db)
        checks = {check.name: check for check in report.checks}
        self.assertIn("watchtower-personality-mirrors", checks)
        self.assertFalse(checks["watchtower-personality-mirrors"].ok)
        self.assertIn("mirror_drift=1", checks["watchtower-personality-mirrors"].detail)

    def test_watchtower_personality_import_check_flags_missing_personality_hook(self) -> None:
        resolve_canonical_agent_identity(
            state_db=self.state_db,
            human_id="human:telegram:personality-missing",
            display_name="Personality Missing",
        )
        self.config_manager.set_path("spark.chips.roots", [])
        sync_attachment_snapshot(config_manager=self.config_manager, state_db=self.state_db)

        snapshot = build_watchtower_snapshot(self.state_db)
        personality_panel = snapshot["panels"]["personality"]
        self.assertGreaterEqual(personality_panel["counts"]["personality_hook_chip_records"], 1)
        self.assertEqual(personality_panel["counts"]["personality_hook_active_chip_records"], 0)
        self.assertEqual(personality_panel["counts"]["personality_import_ready"], 0)
        self.assertFalse(personality_panel["personality_import"]["ready"])

        report = run_doctor(self.config_manager, self.state_db)
        checks = {check.name: check for check in report.checks}
        self.assertIn("watchtower-personality-import", checks)
        self.assertFalse(checks["watchtower-personality-import"].ok)
        self.assertIn("required=yes", checks["watchtower-personality-import"].detail)
        self.assertIn("personality_import_ready=no", checks["watchtower-personality-import"].detail)

    def test_watchtower_personality_import_check_passes_with_active_personality_hook_chip(self) -> None:
        resolve_canonical_agent_identity(
            state_db=self.state_db,
            human_id="human:telegram:personality-ready",
            display_name="Personality Ready",
        )
        chip_root = create_fake_hook_chip(self.home, chip_key="spark-personality")
        self.config_manager.set_path("spark.chips.roots", [str(chip_root)])
        self.config_manager.set_path("spark.chips.active_keys", ["spark-personality"])
        sync_attachment_snapshot(config_manager=self.config_manager, state_db=self.state_db)

        snapshot = build_watchtower_snapshot(self.state_db)
        personality_panel = snapshot["panels"]["personality"]
        self.assertEqual(personality_panel["counts"]["personality_hook_chip_records"], 1)
        self.assertEqual(personality_panel["counts"]["personality_hook_active_chip_records"], 1)
        self.assertEqual(personality_panel["counts"]["personality_import_ready"], 1)
        self.assertEqual(personality_panel["personality_import"]["active_chip_keys"], ["spark-personality"])
        self.assertTrue(personality_panel["personality_import"]["ready"])

        report = run_doctor(self.config_manager, self.state_db)
        checks = {check.name: check for check in report.checks}
        self.assertIn("watchtower-personality-import", checks)
        self.assertTrue(checks["watchtower-personality-import"].ok)

    def test_doctor_refreshes_stale_personality_import_snapshot(self) -> None:
        resolve_canonical_agent_identity(
            state_db=self.state_db,
            human_id="human:telegram:personality-stale",
            display_name="Personality Stale",
        )
        sync_attachment_snapshot(config_manager=self.config_manager, state_db=self.state_db)

        chip_root = create_fake_hook_chip(self.home, chip_key="spark-personality")
        self.config_manager.set_path("spark.chips.roots", [str(chip_root)])
        self.config_manager.set_path("spark.chips.active_keys", ["spark-personality"])

        report = run_doctor(self.config_manager, self.state_db)

        checks = {check.name: check for check in report.checks}
        self.assertIn("watchtower-personality-import", checks)
        self.assertTrue(checks["watchtower-personality-import"].ok)
        snapshot = build_watchtower_snapshot(self.state_db)
        personality_panel = snapshot["panels"]["personality"]
        self.assertEqual(personality_panel["counts"]["personality_hook_chip_records"], 1)
        self.assertEqual(personality_panel["counts"]["personality_hook_active_chip_records"], 1)
        self.assertEqual(personality_panel["counts"]["personality_import_ready"], 1)
        self.assertEqual(personality_panel["personality_import"]["active_chip_keys"], ["spark-personality"])

    def test_stop_ship_requires_domain_specific_runtime_state_mirrors(self) -> None:
        with self.state_db.connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO runtime_state(state_key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
                ("swarm:last_sync", '{"mode":"uploaded"}'),
            )
            conn.commit()

        record_event(
            self.state_db,
            event_type="tool_result_received",
            component="researcher_bridge",
            summary="unrelated researcher result",
            actor_id="test",
            facts={"routing_decision": "stub"},
        )

        issues = {issue.name: issue for issue in evaluate_stop_ship_issues(config_manager=self.config_manager, state_db=self.state_db)}
        self.assertFalse(issues["stop_ship_runtime_state_authority"].ok)
        self.assertIn("swarm", issues["stop_ship_runtime_state_authority"].detail)

    def test_stop_ship_flags_attachment_runtime_state_without_typed_snapshot(self) -> None:
        with self.state_db.connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO runtime_state(state_key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
                ("attachments:last_snapshot_path", "C:/tmp/attachments.snapshot.json"),
            )
            conn.commit()

        issues = {issue.name: issue for issue in evaluate_stop_ship_issues(config_manager=self.config_manager, state_db=self.state_db)}
        self.assertFalse(issues["stop_ship_runtime_state_authority"].ok)
        self.assertIn("attachments", issues["stop_ship_runtime_state_authority"].detail)

    def test_stop_ship_checks_fail_closed_when_sqlite_event_queries_error(self) -> None:
        with patch(
            "spark_intelligence.observability.checks._runtime_state_authority_issue",
            side_effect=sqlite3.OperationalError("disk I/O error"),
        ):
            issues = {
                issue.name: issue
                for issue in evaluate_stop_ship_issues(
                    config_manager=self.config_manager,
                    state_db=self.state_db,
                )
            }

        self.assertFalse(issues["stop_ship_runtime_state_authority"].ok)
        self.assertIn("SQLite error", issues["stop_ship_runtime_state_authority"].detail)
        self.assertIn("disk I/O error", issues["stop_ship_runtime_state_authority"].detail)

    def test_watchtower_snapshot_degrades_panel_when_memory_lane_query_errors(self) -> None:
        with patch(
            "spark_intelligence.observability.store.recent_memory_lane_records",
            side_effect=sqlite3.DatabaseError("database disk image is malformed"),
        ):
            snapshot = build_watchtower_snapshot(self.state_db)

        panel = snapshot["panels"]["memory_lane_hygiene"]
        self.assertEqual(panel["status"], "degraded")
        self.assertEqual(panel["panel"], "memory_lane_hygiene")
        self.assertIn("database disk image is malformed", panel["error"])

    def test_stop_ship_flags_ungoverned_external_execution_entry_points(self) -> None:
        with patch(
            "spark_intelligence.observability.checks._find_source_pattern_paths",
            side_effect=[
                ["src/spark_intelligence/future_tooling/raw_exec.py"],
                [],
                [],
            ],
        ):
            issues = {
                issue.name: issue
                for issue in evaluate_stop_ship_issues(
                    config_manager=self.config_manager,
                    state_db=self.state_db,
                )
            }

        self.assertFalse(issues["stop_ship_external_execution_governance"].ok)
        self.assertIn("future_tooling/raw_exec.py", issues["stop_ship_external_execution_governance"].detail)

    def test_stop_ship_flags_promotable_ephemeral_bridge_output(self) -> None:
        record_event(
            self.state_db,
            event_type="tool_result_received",
            component="researcher_bridge",
            summary="bridge output incorrectly marked promotable",
            actor_id="test",
            facts={
                "routing_decision": "researcher_advisory",
                "keepability": "ephemeral_context",
                "promotion_disposition": "durable_candidate",
            },
        )

        issues = {issue.name: issue for issue in evaluate_stop_ship_issues(config_manager=self.config_manager, state_db=self.state_db)}
        self.assertFalse(issues["stop_ship_keepability_rules"].ok)
        self.assertIn("promotion-eligible", issues["stop_ship_keepability_rules"].detail)

    def test_stop_ship_flags_missing_typed_memory_lane_record(self) -> None:
        with self.state_db.connect() as conn:
            conn.execute(
                """
                INSERT INTO builder_events(
                    event_id,
                    event_type,
                    truth_kind,
                    target_surface,
                    component,
                    actor_id,
                    evidence_lane,
                    severity,
                    status,
                    summary,
                    facts_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "evt-manual-memory-gap",
                    "tool_result_received",
                    "fact",
                    "spark_intelligence_builder",
                    "researcher_bridge",
                    "test",
                    "realworld_validated",
                    "medium",
                    "recorded",
                    "manually inserted classified event",
                    json.dumps(
                        {
                            "routing_decision": "researcher_advisory",
                            "keepability": "ephemeral_context",
                            "promotion_disposition": "not_promotable",
                        },
                        sort_keys=True,
                    ),
                ),
            )
            conn.commit()

        issues = {issue.name: issue for issue in evaluate_stop_ship_issues(config_manager=self.config_manager, state_db=self.state_db)}
        self.assertFalse(issues["stop_ship_keepability_rules"].ok)
        self.assertIn("lack typed memory-lane records", issues["stop_ship_keepability_rules"].detail)

    def test_stop_ship_uses_direct_lane_lookup_for_classified_events(self) -> None:
        record_event(
            self.state_db,
            event_type="tool_result_received",
            component="researcher_bridge",
            summary="classified bridge result with lane record",
            request_id="req-direct-lane-lookup",
            trace_ref="trace:req-direct-lane-lookup",
            actor_id="researcher_bridge",
            facts={
                "bridge_mode": "external_typed",
                "routing_decision": "researcher_advisory",
                "keepability": "ephemeral_context",
                "promotion_disposition": "not_promotable",
            },
            provenance={"source_kind": "researcher_bridge", "source_ref": "bridge:test"},
        )
        for index in range(450):
            record_event(
                self.state_db,
                event_type="plugin_or_chip_influence_recorded",
                component="researcher_bridge",
                summary=f"filler classified influence {index}",
                actor_id="researcher_bridge",
                facts={"keepability": "operator_debug_only"},
                provenance={"source_kind": "chip_hook", "source_ref": f"chip:{index}"},
            )

        issues = {issue.name: issue for issue in evaluate_stop_ship_issues(config_manager=self.config_manager, state_db=self.state_db)}
        self.assertTrue(issues["stop_ship_keepability_rules"].ok)

    def test_stop_ship_flags_bridge_backed_webhook_delivery_without_keepability(self) -> None:
        record_event(
            self.state_db,
            event_type="delivery_succeeded",
            component="discord_webhook",
            summary="discord webhook reply delivered",
            actor_id="test",
            facts={
                "bridge_mode": "external_autodiscovered",
                "response_length": 42,
            },
        )

        issues = {issue.name: issue for issue in evaluate_stop_ship_issues(config_manager=self.config_manager, state_db=self.state_db)}
        self.assertFalse(issues["stop_ship_keepability_rules"].ok)
        self.assertIn("keepability or promotion classification", issues["stop_ship_keepability_rules"].detail)

    def test_stop_ship_flags_mutated_delivery_without_raw_and_mutated_refs(self) -> None:
        record_event(
            self.state_db,
            event_type="delivery_succeeded",
            component="discord_webhook",
            summary="discord webhook reply delivered with mutation",
            actor_id="test",
            facts={
                "bridge_mode": "external_autodiscovered",
                "keepability": "ephemeral_context",
                "promotion_disposition": "not_promotable",
                "text_mutated": True,
            },
        )

        issues = {issue.name: issue for issue in evaluate_stop_ship_issues(config_manager=self.config_manager, state_db=self.state_db)}
        self.assertFalse(issues["stop_ship_keepability_rules"].ok)
        self.assertIn("raw-vs-mutated text refs", issues["stop_ship_keepability_rules"].detail)

    def test_send_telegram_reply_defaults_runtime_command_to_non_promotable_classification(self) -> None:
        class _Client:
            def send_message(self, *, chat_id: str, text: str) -> None:
                return None

        result = _send_telegram_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            client=_Client(),
            chat_id="111",
            text="Runtime command reply.",
            event="telegram_runtime_command_outbound",
            update_id=111,
            telegram_user_id="111",
            session_id="session:test",
            decision="allowed",
            bridge_mode="runtime_command",
            trace_ref=None,
        )

        self.assertTrue(result["ok"])
        events = latest_events_by_type(self.state_db, event_type="delivery_succeeded", limit=10)
        self.assertTrue(events)
        facts = events[0]["facts_json"]
        self.assertEqual(facts["event"], "telegram_runtime_command_outbound")
        self.assertEqual(facts["keepability"], "operator_debug_only")
        self.assertEqual(facts["promotion_disposition"], "not_promotable")

    def test_record_researcher_bridge_result_sanitizes_failure_runtime_message(self) -> None:
        record_researcher_bridge_result(
            state_db=self.state_db,
            result=ResearcherBridgeResult(
                request_id="req-failure-runtime",
                reply_text="[Spark Researcher bridge error] trace:internal memory_refs: mem-1",
                evidence_summary="External bridge failed closed.",
                escalation_hint="bridge_error",
                trace_ref="trace:internal",
                mode="bridge_error",
                runtime_root="C:/researcher",
                config_path="C:/researcher/config.json",
                attachment_context={},
                routing_decision="bridge_error",
                output_keepability="operator_debug_only",
                promotion_disposition="not_promotable",
            ),
        )

        with self.state_db.connect() as conn:
            row = conn.execute(
                "SELECT value FROM runtime_state WHERE state_key = 'researcher:last_failure' LIMIT 1"
            ).fetchone()
        self.assertIsNotNone(row)
        payload = json.loads(row["value"])
        self.assertEqual(payload["output_keepability"], "operator_debug_only")
        self.assertEqual(payload["promotion_disposition"], "not_promotable")
        self.assertNotIn("[Spark Researcher", payload["message"])
        self.assertNotIn("trace:", payload["message"])
        self.assertIn("Inspect trace and event history", payload["message"])

    def test_researcher_bridge_status_prefers_typed_truth_surfaces(self) -> None:
        record_environment_snapshot(
            self.state_db,
            surface="researcher_bridge",
            summary="typed snapshot",
            provider_id="custom",
            provider_model="MiniMax-M2.7",
            provider_base_url="https://api.minimax.io/v1",
            provider_execution_transport="direct_http",
            runtime_root="C:/typed-runtime",
            config_path="C:/typed-runtime/config.json",
            facts={"model_family": "MiniMax-M2.7"},
        )
        record_event(
            self.state_db,
            event_type="tool_result_received",
            component="researcher_bridge",
            summary="typed bridge result",
            request_id="req-typed",
            trace_ref="trace:typed",
            actor_id="researcher_bridge",
            facts={
                "bridge_mode": "external_typed",
                "routing_decision": "researcher_advisory",
                "evidence_summary": "typed evidence",
                "provider_id": "custom",
                "provider_model": "MiniMax-M2.7",
                "provider_model_family": "MiniMax-M2.7",
                "provider_auth_method": "api_key",
                "provider_execution_transport": "direct_http",
                "active_chip_key": "chip:typed",
                "active_chip_task_type": "advice",
                "active_chip_evaluate_used": True,
                "keepability": "ephemeral_context",
                "promotion_disposition": "not_promotable",
            },
        )
        with self.state_db.connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO runtime_state(state_key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
                ("researcher:last_mode", "runtime_state_only"),
            )
            conn.commit()

        status = researcher_bridge_status(config_manager=self.config_manager, state_db=self.state_db)
        self.assertEqual(status.last_mode, "external_typed")
        self.assertEqual(status.last_trace_ref, "trace:typed")
        self.assertEqual(status.last_runtime_root, "C:/typed-runtime")
        self.assertEqual(status.last_provider_id, "custom")
        self.assertEqual(status.last_active_chip_key, "chip:typed")

    def test_operator_security_report_reads_typed_delivery_run_and_provenance_truth(self) -> None:
        run = open_run(
            self.state_db,
            run_kind="job:test",
            origin_surface="jobs_tick",
            summary="stalled run",
            request_id="req-ops",
            session_id="session:ops",
            actor_id="jobs_tick",
        )
        close_run(
            self.state_db,
            run_id=run.run_id,
            status="stalled",
            close_reason="stalled_for_test",
            summary="stalled run closed",
        )
        record_event(
            self.state_db,
            event_type="delivery_attempted",
            component="telegram_runtime",
            summary="delivery attempted",
            run_id=run.run_id,
            request_id="req-ops",
            session_id="session:ops",
            channel_id="telegram",
            actor_id="telegram_runtime",
            truth_kind="delivery",
            facts={"update_id": 999, "telegram_user_id": "user-ops"},
        )
        record_event(
            self.state_db,
            event_type="delivery_failed",
            component="telegram_runtime",
            summary="delivery failed",
            run_id=run.run_id,
            request_id="req-ops",
            session_id="session:ops",
            channel_id="telegram",
            actor_id="telegram_runtime",
            truth_kind="delivery",
            facts={"update_id": 999, "telegram_user_id": "user-ops", "delivery_error": "HTTP 500"},
        )
        record_event(
            self.state_db,
            event_type="plugin_or_chip_influence_recorded",
            component="researcher_bridge",
            summary="unlabeled provenance incident",
            run_id=run.run_id,
            request_id="req-ops",
            actor_id="researcher_bridge",
            facts={"keepability": "ephemeral_context"},
            provenance={},
        )

        report = build_operator_security_report(
            config_manager=self.config_manager,
            state_db=self.state_db,
            limit=20,
        )

        self.assertEqual(report.payload["counts"]["delivery_failures"], 1)
        self.assertEqual(report.payload["counts"]["stalled_runs"], 1)
        self.assertEqual(report.payload["counts"]["provenance_incidents"], 1)
        self.assertEqual(report.payload["counts"]["contradiction_incidents"], 0)
        self.assertEqual(report.payload["counts"]["policy_block_incidents"], 1)
        self.assertIn("watchtower", report.payload)
        self.assertEqual(report.payload["watchtower"]["health_dimensions"]["scheduler_freshness"]["state"], "stalled")
        self.assertTrue(any("typed delivery ledger" in item["summary"] for item in report.payload["items"]))

    def test_operator_security_report_repairs_foreground_browser_hook_failures_out_of_stalled_bucket(self) -> None:
        run = open_run(
            self.state_db,
            run_kind="operator:browser_hook:browser.status",
            origin_surface="browser_cli",
            summary="opened browser hook",
            request_id="req-browser-failure",
            actor_id="local-operator",
        )
        close_run(
            self.state_db,
            run_id=run.run_id,
            status="stalled",
            close_reason="browser_hook_failed",
            summary="Browser CLI hook returned a governed failure response.",
            facts={"error_code": "BROWSER_SESSION_STALE"},
        )

        report = build_operator_security_report(
            config_manager=self.config_manager,
            state_db=self.state_db,
            limit=20,
        )

        self.assertEqual(report.payload["counts"]["stalled_runs"], 0)
        failed_runs = recent_runs(self.state_db, limit=10, status="failed")
        self.assertTrue(failed_runs)
        self.assertEqual(failed_runs[0]["run_kind"], "operator:browser_hook:browser.status")
        self.assertEqual(failed_runs[0]["closure_reason"], "browser_hook_failed")

    def test_operator_security_report_surfaces_open_contradictions(self) -> None:
        run = open_run(
            self.state_db,
            run_kind="telegram_update",
            origin_surface="telegram_runtime",
            summary="opened",
            request_id="req-operator-contradiction",
            channel_id="telegram",
            actor_id="telegram_runtime",
        )
        record_event(
            self.state_db,
            event_type="intent_committed",
            component="telegram_runtime",
            summary="intent only",
            run_id=run.run_id,
            request_id="req-operator-contradiction",
            channel_id="telegram",
            actor_id="telegram_runtime",
        )
        evaluate_stop_ship_issues(
            config_manager=self.config_manager,
            state_db=self.state_db,
            emit_contradictions=True,
        )

        report = build_operator_security_report(
            config_manager=self.config_manager,
            state_db=self.state_db,
            limit=20,
        )

        self.assertEqual(report.payload["counts"]["contradiction_incidents"], 1)
        self.assertEqual(report.payload["watchtower"]["contradictions"]["counts"]["open"], 1)
        self.assertTrue(any("contradiction" in item["summary"].lower() for item in report.payload["items"]))

    def test_operator_security_report_surfaces_observer_incident_counts(self) -> None:
        record_event(
            self.state_db,
            event_type="plugin_or_chip_influence_recorded",
            component="researcher_bridge",
            summary="unlabeled provenance mutation",
            request_id="req-security-observer",
            actor_id="researcher_bridge",
            facts={"keepability": "ephemeral_context"},
            provenance={},
        )
        record_pairing_context(
            state_db=self.state_db,
            channel_id="telegram",
            external_user_id="111",
            context={
                "telegram_username": "alice",
                "chat_id": "chat-1",
                "last_message_text": "richer state",
                "last_seen_at": "2026-03-28T00:00:00+00:00",
            },
        )
        record_pairing_context(
            state_db=self.state_db,
            channel_id="telegram",
            external_user_id="111",
            context={"last_seen_at": "2026-03-28T00:05:00+00:00"},
        )

        report = build_operator_security_report(
            config_manager=self.config_manager,
            state_db=self.state_db,
            limit=20,
        )

        self.assertGreaterEqual(report.payload["counts"]["observer_incidents"], 2)
        self.assertGreaterEqual(report.payload["counts"]["observer_packets"], 2)
        self.assertTrue(any("observer contamination or integrity incident" in item["summary"].lower() for item in report.payload["items"]))
        self.assertTrue(any("observer packet" in item["summary"].lower() for item in report.payload["items"]))

    def test_operator_security_ignores_info_only_observer_incidents(self) -> None:
        record_pairing_context(
            state_db=self.state_db,
            channel_id="telegram",
            external_user_id="111",
            context={
                "telegram_username": "alice",
                "chat_id": "chat-1",
                "last_message_text": "richer state",
                "last_seen_at": "2026-03-28T00:00:00+00:00",
            },
        )
        record_pairing_context(
            state_db=self.state_db,
            channel_id="telegram",
            external_user_id="111",
            context={"last_seen_at": "2026-03-28T00:05:00+00:00"},
        )

        report = build_operator_security_report(
            config_manager=self.config_manager,
            state_db=self.state_db,
            limit=20,
        )

        self.assertEqual(report.payload["counts"]["observer_incidents"], 0)
        self.assertFalse(any("observer contamination or integrity incident" in item["summary"].lower() for item in report.payload["items"]))
        self.assertGreaterEqual(report.payload["counts"]["observer_packets"], 1)

    def test_watchtower_and_operator_security_surface_problematic_observer_handoffs(self) -> None:
        record_observer_handoff_record(
            self.state_db,
            handoff_id="observer-handoff-failed",
            chip_key="startup-yc",
            hook="packets",
            run_id="run:test",
            request_id="req:test",
            bundle_path=str(self.home / "artifacts" / "observer-handoffs" / "failed.bundle.json"),
            result_path=None,
            packet_count=4,
            packet_kind_filter=None,
            active_only=True,
            status="failed",
            summary="Observer handoff failed because the chip exited non-zero.",
            exit_code=1,
            error_text="chip_exit_nonzero",
            payload={"packet_count": 4},
            output={"stderr": "boom"},
        )

        snapshot = build_watchtower_snapshot(self.state_db)
        handoff_panel = snapshot["panels"]["observer_handoffs"]
        report = build_operator_security_report(
            config_manager=self.config_manager,
            state_db=self.state_db,
            limit=20,
        )

        self.assertEqual(handoff_panel["counts"]["total"], 1)
        self.assertEqual(handoff_panel["counts"]["failed"], 1)
        self.assertEqual(handoff_panel["counts"]["problematic"], 1)
        self.assertEqual(report.payload["counts"]["observer_handoffs"], 1)
        self.assertEqual(report.payload["counts"]["observer_handoff_failures"], 1)
        self.assertTrue(any("observer handoff" in item["summary"].lower() for item in report.payload["items"]))

    def test_doctor_report_includes_watchtower_health_checks(self) -> None:
        run = open_run(
            self.state_db,
            run_kind="telegram_update",
            origin_surface="telegram_runtime",
            summary="opened",
            request_id="req-doctor-watchtower",
            channel_id="telegram",
            actor_id="telegram_runtime",
        )
        record_event(
            self.state_db,
            event_type="intent_committed",
            component="telegram_runtime",
            summary="intent only",
            run_id=run.run_id,
            request_id="req-doctor-watchtower",
            channel_id="telegram",
            actor_id="telegram_runtime",
        )

        report = run_doctor(self.config_manager, self.state_db)
        checks = {check.name: check for check in report.checks}

        self.assertIn("watchtower-execution", checks)
        self.assertFalse(checks["watchtower-execution"].ok)
        self.assertIn("execution_impaired", checks["watchtower-execution"].detail)
        self.assertIn("watchtower-contradictions", checks)
        self.assertFalse(checks["watchtower-contradictions"].ok)

    def test_doctor_repairs_missing_chip_hook_promotion_disposition_before_stop_ship(self) -> None:
        record_event(
            self.state_db,
            event_type="tool_result_received",
            component="researcher_bridge",
            summary="Chip hook startup-yc.evaluate produced a result.",
            actor_id="researcher_bridge",
            facts={"keepability": "ephemeral_context"},
            provenance={"source_kind": "chip_hook", "source_ref": "startup-yc"},
        )

        report = run_doctor(self.config_manager, self.state_db)
        checks = {check.name: check for check in report.checks}

        self.assertTrue(checks["stop_ship_keepability_rules"].ok)
        events = latest_events_by_type(self.state_db, event_type="tool_result_received", limit=10)
        repaired = [
            event
            for event in events
            if event.get("component") == "researcher_bridge"
            and str((event.get("provenance_json") or {}).get("source_kind") or "") == "chip_hook"
        ]
        self.assertTrue(repaired)
        self.assertEqual(repaired[0]["facts_json"]["promotion_disposition"], "not_promotable")
        open_keys = {
            str(row.get("contradiction_key") or "")
            for row in recent_contradictions(self.state_db, limit=20, status="open")
        }
        self.assertNotIn("stop_ship:stop_ship_keepability_rules", open_keys)

    def test_doctor_report_includes_observer_incident_check(self) -> None:
        record_event(
            self.state_db,
            event_type="plugin_or_chip_influence_recorded",
            component="researcher_bridge",
            summary="unlabeled provenance mutation",
            request_id="req-doctor-observer",
            actor_id="researcher_bridge",
            facts={"keepability": "ephemeral_context"},
            provenance={},
        )

        report = run_doctor(self.config_manager, self.state_db)
        checks = {check.name: check for check in report.checks}

        self.assertIn("watchtower-observer-incidents", checks)
        self.assertFalse(checks["watchtower-observer-incidents"].ok)
        self.assertIn("watchtower-observer-packets", checks)
        self.assertTrue(checks["watchtower-observer-packets"].ok)
        self.assertIn("watchtower-observer-packet-kinds", checks)
        self.assertTrue(checks["watchtower-observer-packet-kinds"].ok)

    def test_doctor_ignores_info_only_observer_incidents(self) -> None:
        record_pairing_context(
            state_db=self.state_db,
            channel_id="telegram",
            external_user_id="111",
            context={
                "telegram_username": "alice",
                "chat_id": "chat-1",
                "last_message_text": "richer state",
                "last_seen_at": "2026-03-28T00:00:00+00:00",
            },
        )
        record_pairing_context(
            state_db=self.state_db,
            channel_id="telegram",
            external_user_id="111",
            context={"last_seen_at": "2026-03-28T00:05:00+00:00"},
        )

        report = run_doctor(self.config_manager, self.state_db)
        checks = {check.name: check for check in report.checks}

        self.assertIn("watchtower-observer-incidents", checks)
        self.assertTrue(checks["watchtower-observer-incidents"].ok)
        self.assertIn("actionable=0", checks["watchtower-observer-incidents"].detail)

    def test_doctor_report_includes_observer_handoff_check(self) -> None:
        record_observer_handoff_record(
            self.state_db,
            handoff_id="observer-handoff-blocked",
            chip_key="startup-yc",
            hook="packets",
            run_id="run:test",
            request_id="req:test",
            bundle_path=str(self.home / "artifacts" / "observer-handoffs" / "blocked.bundle.json"),
            result_path=None,
            packet_count=3,
            packet_kind_filter=None,
            active_only=True,
            status="blocked",
            summary="Observer handoff output was blocked by the secret boundary.",
            exit_code=0,
            error_text="secret_boundary_blocked:q-1",
            payload={"packet_count": 3},
            output=None,
        )

        report = run_doctor(self.config_manager, self.state_db)
        checks = {check.name: check for check in report.checks}

        self.assertIn("watchtower-observer-handoffs", checks)
        self.assertFalse(checks["watchtower-observer-handoffs"].ok)
        self.assertIn("blocked=1", checks["watchtower-observer-handoffs"].detail)

    def test_unlabeled_provenance_is_quarantined(self) -> None:
        record_event(
            self.state_db,
            event_type="plugin_or_chip_influence_recorded",
            component="researcher_bridge",
            summary="unlabeled provenance mutation",
            request_id="req-unlabeled-prov",
            actor_id="researcher_bridge",
            facts={"keepability": "ephemeral_context"},
            provenance={},
        )

        with self.state_db.connect() as conn:
            mutation = conn.execute(
                """
                SELECT source_kind, source_id, quarantined
                FROM provenance_mutation_log
                ORDER BY recorded_at DESC, mutation_id DESC
                LIMIT 1
                """
            ).fetchone()
            quarantine = conn.execute(
                """
                SELECT policy_domain, reason_code
                FROM quarantine_records
                WHERE source_kind = 'provenance_mutation'
                ORDER BY created_at DESC, quarantine_id DESC
                LIMIT 1
                """
            ).fetchone()

        self.assertIsNotNone(mutation)
        self.assertEqual(mutation["source_kind"], "unknown")
        self.assertEqual(int(mutation["quarantined"]), 1)
        self.assertIsNotNone(quarantine)
        self.assertEqual(quarantine["policy_domain"], "provenance_mutation")
        policy_blocks = recent_policy_gate_records(self.state_db, limit=10)
        self.assertTrue(policy_blocks)
        self.assertEqual(policy_blocks[0]["gate_name"], "provenance_missing")

        issues = {
            issue.name: issue
            for issue in evaluate_stop_ship_issues(
                config_manager=self.config_manager,
                state_db=self.state_db,
            )
        }
        self.assertTrue(issues["stop_ship_unlabeled_provenance_quarantine"].ok)

    def test_stop_ship_flags_raw_bridge_residue_persistence(self) -> None:
        with self.state_db.connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO runtime_state(state_key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
                (
                    "researcher:last_failure",
                    json.dumps({"message": "[Spark Researcher bridge error] trace:internal memory_refs: mem-1"}),
                ),
            )
            conn.commit()

        issues = {issue.name: issue for issue in evaluate_stop_ship_issues(config_manager=self.config_manager, state_db=self.state_db)}
        self.assertFalse(issues["stop_ship_bridge_residue_persistence"].ok)
        self.assertIn("raw reply/debug residue", issues["stop_ship_bridge_residue_persistence"].detail)

    def test_stop_ship_flags_new_bridge_reply_consumers_outside_delivery_surfaces(self) -> None:
        with patch(
            "spark_intelligence.observability.checks._find_source_pattern_paths",
            side_effect=[
                [],
                [],
                ["src/spark_intelligence/future_memory/promoter.py"],
            ],
        ):
            issues = {
                issue.name: issue
                for issue in evaluate_stop_ship_issues(
                    config_manager=self.config_manager,
                    state_db=self.state_db,
                )
            }

        self.assertFalse(issues["stop_ship_bridge_output_governance"].ok)
        self.assertIn("future_memory/promoter.py", issues["stop_ship_bridge_output_governance"].detail)

    def test_build_researcher_reply_blocks_secret_like_model_visible_context(self) -> None:
        result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-secret-block",
            agent_id="agent:test",
            human_id="human:test",
            session_id="session:test",
            channel_kind="telegram",
            user_message="here is my token " + "sk-" + "abcdefghijklmnopqrstuvwxyz123456",
        )

        self.assertEqual(result.routing_decision, "secret_boundary_blocked")
        self.assertEqual(result.mode, "blocked")
        self.assertEqual(result.escalation_hint, "secret_boundary_violation")
        self.assertTrue(latest_events_by_type(self.state_db, event_type="secret_boundary_violation", limit=10))
        with self.state_db.connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS c FROM quarantine_records").fetchone()
        self.assertGreaterEqual(int(row["c"]), 1)
