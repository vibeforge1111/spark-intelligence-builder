import json

from spark_intelligence.adapters.telegram.runtime import simulate_telegram_update
from spark_intelligence.gateway.runtime import gateway_outbound_view, gateway_trace_view
from spark_intelligence.gateway.tracing import append_gateway_trace, append_outbound_audit
from spark_intelligence.identity.service import hold_pairing, review_pairings
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
