from __future__ import annotations

from spark_intelligence.gateway.guardrails import prepare_outbound_text
from spark_intelligence.observability.policy import looks_secret_like
from spark_intelligence.jobs.service import jobs_tick
from spark_intelligence.observability.checks import evaluate_stop_ship_issues
from spark_intelligence.observability.store import (
    latest_events_by_type,
    open_run,
    record_environment_snapshot,
    record_event,
)
from spark_intelligence.researcher_bridge.advisory import build_researcher_reply

from tests.test_support import SparkTestCase


class BuilderPrelaunchContractTests(SparkTestCase):
    def test_secret_policy_detects_common_secret_families(self) -> None:
        self.assertTrue(looks_secret_like("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.c2lnbmF0dXJl"))
        self.assertTrue(looks_secret_like("TELEGRAM_BOT_TOKEN=1234567890:abcdefghijklmnopqrstuvwxyzABCDE"))
        self.assertTrue(looks_secret_like("api_key: sk-proj-abcdefghijklmnopqrstuvwxyz123456"))
        self.assertTrue(looks_secret_like("-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----"))
        self.assertFalse(looks_secret_like("This is a normal operational note with no credentials in it."))

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
            user_message="hello",
        )

        self.assertIn(result.routing_decision, {"bridge_disabled", "stub"})
        events = latest_events_by_type(self.state_db, event_type="plugin_or_chip_influence_recorded", limit=10)
        self.assertTrue(events)
        facts = events[0]["facts_json"]
        self.assertEqual(facts["keepability"], "ephemeral_context")

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

    def test_build_researcher_reply_blocks_secret_like_model_visible_context(self) -> None:
        result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-secret-block",
            agent_id="agent:test",
            human_id="human:test",
            session_id="session:test",
            channel_kind="telegram",
            user_message="here is my token sk-abcdefghijklmnopqrstuvwxyz123456",
        )

        self.assertEqual(result.routing_decision, "secret_boundary_blocked")
        self.assertEqual(result.mode, "blocked")
        self.assertEqual(result.escalation_hint, "secret_boundary_violation")
        self.assertTrue(latest_events_by_type(self.state_db, event_type="secret_boundary_violation", limit=10))
        with self.state_db.connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS c FROM quarantine_records").fetchone()
        self.assertGreaterEqual(int(row["c"]), 1)
