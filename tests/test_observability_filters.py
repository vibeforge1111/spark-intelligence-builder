import json

from spark_intelligence.adapters.telegram.runtime import simulate_telegram_update
from spark_intelligence.gateway.runtime import gateway_outbound_view, gateway_trace_view
from spark_intelligence.gateway.tracing import append_gateway_trace, append_outbound_audit
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
        self.assertEqual(payload[0]["telegram_user_id"], "222")

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
