import json
from pathlib import Path

from spark_intelligence.adapters.telegram.runtime import simulate_telegram_update
from spark_intelligence.gateway.runtime import gateway_outbound_view, gateway_trace_view
from spark_intelligence.gateway.tracing import append_gateway_trace, append_outbound_audit, redact_gateway_trace_log, trace_log_path
from spark_intelligence.identity.service import hold_pairing, review_pairings
from spark_intelligence.observability.store import latest_events_by_type
from spark_intelligence.ops.service import list_operator_events, log_operator_event

from tests.test_support import SparkTestCase, make_telegram_update


class ObservabilityFilterTests(SparkTestCase):
    def test_gateway_trace_view_filters_by_channel_event_user_and_decision(self) -> None:
        append_gateway_trace(
            self.config_manager,
            {
                "event": "telegram_pending_pairing",
                "channel_id": "telegram",
                "update_id": 201,
                "telegram_user_id": "111",
                "decision": "pending_pairing",
            },
        )
        append_gateway_trace(
            self.config_manager,
            {
                "event": "telegram_update_denied",
                "channel_id": "telegram",
                "update_id": 202,
                "telegram_user_id": "222",
                "decision": "revoked",
            },
        )
        append_gateway_trace(
            self.config_manager,
            {
                "event": "discord_message_ignored",
                "channel_id": "discord",
                "update_id": 203,
                "external_user_id": "333",
                "decision": "ignored",
            },
        )

        payload = json.loads(
            gateway_trace_view(
                self.config_manager,
                limit=10,
                channel_id="telegram",
                event="telegram_update_denied",
                user="222",
                decision="revoked",
                as_json=True,
            )
        )

        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["update_id"], 202)
        self.assertNotIn("telegram_user_id", payload[0])
        self.assertRegex(payload[0]["telegram_user_ref"], r"^telegram_user:sha256:[a-f0-9]{16}$")
        self.assertNotIn('"222"', json.dumps(payload))

    def test_gateway_outbound_view_filters_by_user_delivery_and_contains(self) -> None:
        append_outbound_audit(
            self.config_manager,
            {
                "event": "telegram_denied_outbound",
                "channel_id": "telegram",
                "update_id": 301,
                "telegram_user_id": "111",
                "decision": "revoked",
                "delivery_ok": False,
                "response_preview": "This Telegram account is no longer paired with Spark Intelligence.",
            },
        )
        append_outbound_audit(
            self.config_manager,
            {
                "event": "telegram_bridge_outbound",
                "channel_id": "telegram",
                "update_id": 302,
                "telegram_user_id": "222",
                "decision": "allowed",
                "delivery_ok": True,
                "response_preview": "Spark reply sent successfully.",
            },
        )

        payload = json.loads(
            gateway_outbound_view(
                self.config_manager,
                limit=10,
                channel_id="telegram",
                user="111",
                delivery="failed",
                contains="no longer paired",
                as_json=True,
            )
        )

        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["update_id"], 301)
        self.assertFalse(payload[0]["delivery_ok"])
        self.assertNotIn("telegram_user_id", payload[0])
        self.assertRegex(payload[0]["telegram_user_ref"], r"^telegram_user:sha256:[a-f0-9]{16}$")

    def test_gateway_trace_redacts_secret_values_before_logging(self) -> None:
        append_gateway_trace(
            self.config_manager,
            {
                "event": "telegram_update_processed",
                "telegram_user_id": "111",
                "bot_token": "1234567890:AAabcdefghijklmnopqrstuvwxyz1234567890",
                "detail": {
                    "message": "Authorization: Bearer sk-proj-secretvalue1234567890",
                    "safe": "keep this",
                },
            },
        )

        payload = json.loads(gateway_trace_view(self.config_manager, limit=1, as_json=True))[0]

        self.assertNotIn("telegram_user_id", payload)
        self.assertRegex(payload["telegram_user_ref"], r"^telegram_user:sha256:[a-f0-9]{16}$")
        self.assertEqual(payload["bot_token"], "[REDACTED]")
        self.assertEqual(payload["detail"]["message"], "Authorization: [REDACTED] [REDACTED]")
        self.assertEqual(payload["detail"]["safe"], "keep this")
        serialized = json.dumps(payload)
        self.assertNotIn('"111"', serialized)
        self.assertNotIn("1234567890:AA", serialized)
        self.assertNotIn("sk-proj-secretvalue", serialized)

    def test_gateway_trace_replaces_raw_ids_paths_and_policy_reasons(self) -> None:
        append_gateway_trace(
            self.config_manager,
            {
                "event": "telegram_update_processed",
                "channel_id": "telegram",
                "chat_id": "8319079055",
                "external_user_id": "8319079055",
                "user_id": "8319079055",
                "detail": {
                    "path": "/Users/alchemistab/private/workspace",
                    "reason": "tool_not_allowed_by_policy",
                    "safe": "keep this",
                },
            },
        )

        payload = json.loads(gateway_trace_view(self.config_manager, limit=1, as_json=True))[0]
        serialized = json.dumps(payload)

        self.assertNotIn("chat_id", payload)
        self.assertNotIn("external_user_id", payload)
        self.assertNotIn("user_id", payload)
        self.assertRegex(payload["chat_ref"], r"^chat:sha256:[a-f0-9]{16}$")
        self.assertRegex(payload["external_user_ref"], r"^external_user:sha256:[a-f0-9]{16}$")
        self.assertRegex(payload["user_ref"], r"^user:sha256:[a-f0-9]{16}$")
        self.assertEqual(payload["detail"]["path"], "<path>")
        self.assertEqual(payload["detail"]["reason"], "internal policy reason")
        self.assertEqual(payload["detail"]["safe"], "keep this")
        self.assertNotIn("8319079055", serialized)
        self.assertNotIn("/Users/alchemistab", serialized)
        self.assertNotIn("tool_not_allowed_by_policy", serialized)
        raw_log = trace_log_path(self.config_manager).read_text(encoding="utf-8")
        self.assertNotIn('"chat_id"', raw_log)
        self.assertNotIn('"external_user_id"', raw_log)
        self.assertNotIn('"user_id"', raw_log)
        self.assertNotIn("8319079055", raw_log)
        self.assertNotIn("/Users/alchemistab", raw_log)
        self.assertNotIn("tool_not_allowed_by_policy", raw_log)

    def test_gateway_trace_redaction_repair_rewrites_live_row_shape(self) -> None:
        trace_path = trace_log_path(self.config_manager)
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        trace_path.write_text(
            json.dumps(
                {
                    "recorded_at": "2026-06-24T10:07:28+00:00",
                    "event": "telegram_update_processed",
                    "channel_id": "telegram",
                    "update_id": 749543762,
                    "telegram_user_id": "1278511160",
                    "chat_id": "1278511160",
                    "session_id": "session:telegram:dm:1278511160",
                    "trace_ref": "/Users/alchemistab/.spark/modules/spark-researcher/source/artifacts/traces/private.jsonl",
                    "routing_decision": "researcher_advisory",
                    "attachment_context": {
                        "attached_chip_records": [
                            {
                                "key": "domain-chip-memory",
                                "repo_root": "/Users/alchemistab/.spark/modules/domain-chip-memory/source",
                            }
                        ],
                        "snapshot_path": "/Users/alchemistab/.spark/state/spark-intelligence/attachments.snapshot.json",
                    },
                    "response_preview": "tool_not_allowed_by_policy",
                    "harnessProofRef": "turn:sha256:acb315f0302a6a2b",
                    "request_id": "telegram:749543762",
                }
            )
            + "\n",
            encoding="utf-8",
        )

        result = redact_gateway_trace_log(self.config_manager)
        raw_log = trace_path.read_text(encoding="utf-8")
        payload = json.loads(raw_log)

        self.assertTrue(result["ok"])
        self.assertEqual(result["rows_read"], 1)
        self.assertEqual(result["rows_written"], 1)
        self.assertTrue(result["backup_path"])
        self.assertNotIn("telegram_user_id", payload)
        self.assertNotIn("chat_id", payload)
        self.assertRegex(payload["telegram_user_ref"], r"^telegram_user:sha256:[a-f0-9]{16}$")
        self.assertRegex(payload["chat_ref"], r"^chat:sha256:[a-f0-9]{16}$")
        self.assertRegex(payload["session_id"], r"^session:telegram:dm:telegram_session:sha256:[a-f0-9]{16}$")
        self.assertEqual(payload["trace_ref"], "<path>")
        self.assertEqual(payload["attachment_context"]["attached_chip_records"][0]["repo_root"], "<path>")
        self.assertEqual(payload["attachment_context"]["snapshot_path"], "<path>")
        self.assertEqual(payload["response_preview"], "internal policy reason")
        self.assertEqual(payload["harnessProofRef"], "turn:sha256:acb315f0302a6a2b")
        self.assertNotIn("1278511160", raw_log)
        self.assertNotIn("/Users/alchemistab", raw_log)
        self.assertNotIn("tool_not_allowed_by_policy", raw_log)
        self.assertIn("1278511160", Path(str(result["backup_path"])).read_text(encoding="utf-8"))

    def test_review_pairings_filters_status_and_limit(self) -> None:
        self.add_telegram_channel()
        for update_id, user_id in ((401, "111"), (402, "222")):
            simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=update_id,
                    user_id=user_id,
                    username=f"user{user_id}",
                    text="hello",
                ),
            )
        hold_pairing(state_db=self.state_db, channel_id="telegram", external_user_id="333")

        pending = review_pairings(
            self.state_db,
            channel_id="telegram",
            status="pending",
            limit=1,
        )
        held = review_pairings(
            self.state_db,
            channel_id="telegram",
            status="held",
        )

        self.assertEqual(len(pending.rows), 1)
        self.assertEqual(pending.rows[0]["status"], "pending")
        self.assertEqual(held.rows[0]["external_user_id"], "333")
        self.assertEqual(held.rows[0]["status"], "held")

    def test_telegram_runtime_origin_uses_real_request_ids_and_trace_labels(self) -> None:
        self.config_manager.set_path("spark.researcher.enabled", True)
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        runtime_result = simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=501,
                user_id="111",
                username="user111",
                text="My mom came over yesterday and I spent time with my sister at the park.",
            ),
            simulation=False,
        )

        self.assertTrue(runtime_result.ok)
        self.assertEqual(runtime_result.detail["request_id"], "telegram:501")
        self.assertFalse(runtime_result.detail["simulation"])
        self.assertEqual(runtime_result.detail["origin_surface"], "telegram_runtime")
        self.assertEqual(runtime_result.detail["routing_decision"], "memory_generic_observation")

        write_events = latest_events_by_type(self.state_db, event_type="memory_write_requested", limit=5)
        self.assertTrue(write_events)
        self.assertEqual(write_events[0]["request_id"], "telegram:501")

        runtime_trace = json.loads(
            gateway_trace_view(
                self.config_manager,
                limit=10,
                channel_id="telegram",
                event="telegram_update_processed",
                user="111",
                as_json=True,
            )
        )[0]
        self.assertEqual(runtime_trace["request_id"], "telegram:501")
        self.assertFalse(runtime_trace["simulation"])
        self.assertEqual(runtime_trace["origin_surface"], "telegram_runtime")

        simulation_result = simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=502,
                user_id="111",
                username="user111",
                text="Which family members did I spend time with recently?",
            ),
        )

        self.assertTrue(simulation_result.ok)
        self.assertEqual(simulation_result.detail["request_id"], "sim:502")
        self.assertTrue(simulation_result.detail["simulation"])
        self.assertEqual(simulation_result.detail["origin_surface"], "simulation_cli")

    def test_operator_history_filters_by_action_target_kind_and_contains(self) -> None:
        log_operator_event(
            state_db=self.state_db,
            action="approve_latest_pairing",
            target_kind="pairing",
            target_ref="telegram:111",
            reason="approved",
        )
        log_operator_event(
            state_db=self.state_db,
            action="set_channel",
            target_kind="channel",
            target_ref="telegram",
            reason="pause",
        )
        log_operator_event(
            state_db=self.state_db,
            action="approve_latest_pairing",
            target_kind="pairing",
            target_ref="discord:222",
            reason="approved",
        )

        report = list_operator_events(
            self.state_db,
            action="approve_latest_pairing",
            target_kind="pairing",
            contains="telegram:111",
        )

        self.assertEqual(len(report.rows), 1)
        self.assertEqual(report.rows[0]["target_ref"], "telegram:111")
