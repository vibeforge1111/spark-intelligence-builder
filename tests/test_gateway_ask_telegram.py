from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

from spark_intelligence.adapters.telegram.runtime import build_telegram_runtime_summary
from spark_intelligence.gateway.runtime import gateway_ask_telegram

from tests.test_support import SparkTestCase


class GatewayAskTelegramTests(SparkTestCase):
    def test_telegram_runtime_summary_reports_gateway_effective_allowlist_source(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["8319079055"], bot_token="test-token")
        with self.state_db.connect() as conn:
            conn.executemany(
                "INSERT INTO allowlist_entries(channel_id, external_user_id, role) VALUES ('telegram', ?, 'paired_user')",
                [("58",), ("222",), ("333",)],
            )

        summary = build_telegram_runtime_summary(self.config_manager, self.state_db)

        self.assertEqual(summary.allowed_user_count, 1)
        self.assertEqual(summary.runtime_allowlist_entry_count, 4)
        self.assertIn("allowed_users=1", summary.to_line())
        self.assertIn("allowlist_source=config.allowed_users", summary.to_line())
        self.assertIn("raw_runtime_allowlist_entries=4", summary.to_line())

    def test_gateway_ask_telegram_routes_generic_memory_deletes_before_instruction_shortcircuit(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])
        self.config_manager.set_path("operator.experimental.telegram_terminal_bridge_enabled", True)
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)
        self.config_manager.set_path("spark.researcher.enabled", True)

        update = json.loads(
            gateway_ask_telegram(
                config_manager=self.config_manager,
                state_db=self.state_db,
                message="My favorite color is cobalt blue.",
                user_id="111",
                as_json=True,
            )
        )
        deletion = json.loads(
            gateway_ask_telegram(
                config_manager=self.config_manager,
                state_db=self.state_db,
                message="Forget my favorite color.",
                user_id="111",
                as_json=True,
            )
        )
        post_delete_query = json.loads(
            gateway_ask_telegram(
                config_manager=self.config_manager,
                state_db=self.state_db,
                message="What is my favorite color?",
                user_id="111",
                as_json=True,
            )
        )

        self.assertEqual(
            update["result"]["detail"]["bridge_mode"],
            "external_autodiscovered",
        )
        self.assertEqual(
            deletion["result"]["detail"]["bridge_mode"],
            "memory_generic_observation_delete",
        )
        self.assertIn("I'll forget your favorite color.", deletion["result"]["detail"]["response_text"])
        self.assertEqual(
            post_delete_query["result"]["detail"]["response_text"],
            "I don't currently have that saved.",
        )

    def test_gateway_ask_telegram_routes_active_state_memory_deletes_before_instruction_shortcircuit(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])
        self.config_manager.set_path("operator.experimental.telegram_terminal_bridge_enabled", True)
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)
        self.config_manager.set_path("spark.researcher.enabled", True)

        cases = (
            (
                "We plan to complete live memory checks.",
                "Forget my current plan.",
                "What is my current plan?",
                "current plan",
            ),
            (
                "We committed to closing the pilot by June 1.",
                "Forget our commitment.",
                "What is our commitment?",
                "current commitment",
            ),
        )

        for update_message, delete_message, query_message, label in cases:
            with self.subTest(label=label):
                update = json.loads(
                    gateway_ask_telegram(
                        config_manager=self.config_manager,
                        state_db=self.state_db,
                        message=update_message,
                        user_id="111",
                        as_json=True,
                    )
                )
                deletion = json.loads(
                    gateway_ask_telegram(
                        config_manager=self.config_manager,
                        state_db=self.state_db,
                        message=delete_message,
                        user_id="111",
                        as_json=True,
                    )
                )
                post_delete_query = json.loads(
                    gateway_ask_telegram(
                        config_manager=self.config_manager,
                        state_db=self.state_db,
                        message=query_message,
                        user_id="111",
                        as_json=True,
                    )
                )

                self.assertEqual(
                    update["result"]["detail"]["bridge_mode"],
                    "external_autodiscovered",
                )
                self.assertEqual(
                    deletion["result"]["detail"]["bridge_mode"],
                    "memory_generic_observation_delete",
                )
                self.assertIn(label, deletion["result"]["detail"]["response_text"])
                self.assertEqual(
                    post_delete_query["result"]["detail"]["response_text"],
                    "I don't currently have that saved.",
                )

    def test_gateway_ask_telegram_uses_single_allowed_user_and_formats_reply(self) -> None:
        self.add_telegram_channel(allowed_users=["111"])
        self.config_manager.set_path("operator.experimental.telegram_terminal_bridge_enabled", True)
        simulated_result = SimpleNamespace(
            ok=True,
            decision="allowed",
            detail={
                "bridge_mode": "researcher_advisory",
                "routing_decision": "stay_builder",
                "trace_ref": "trace-123",
                "response_text": "Spark reply text",
            },
        )

        with patch(
            "spark_intelligence.gateway.runtime.simulate_telegram_update",
            return_value=simulated_result,
        ) as simulate:
            output = gateway_ask_telegram(
                config_manager=self.config_manager,
                state_db=self.state_db,
                message="What are you connected to right now?",
            )

        payload = simulate.call_args.kwargs["update_payload"]
        self.assertEqual(payload["message"]["from"]["id"], "111")
        self.assertEqual(payload["message"]["chat"]["id"], "111")
        self.assertEqual(payload["message"]["text"], "What are you connected to right now?")
        self.assertIn("Telegram direct ask", output)
        self.assertIn("- user: 111", output)
        self.assertIn("- decision: allowed", output)
        self.assertIn("- mode: researcher_advisory", output)
        self.assertIn("- route: stay_builder", output)
        self.assertIn("Spark reply text", output)

    def test_gateway_ask_telegram_prefers_latest_recent_telegram_user(self) -> None:
        self.add_telegram_channel(allowed_users=["111", "222"])
        self.config_manager.set_path("operator.experimental.telegram_terminal_bridge_enabled", True)
        simulated_result = SimpleNamespace(
            ok=True,
            decision="allowed",
            detail={"response_text": "Spark reply text"},
        )

        with (
            patch(
                "spark_intelligence.gateway.runtime.read_gateway_traces",
                return_value=[{"channel_id": "telegram", "external_user_id": "222"}],
            ),
            patch("spark_intelligence.gateway.runtime.read_outbound_audit", return_value=[]),
            patch(
                "spark_intelligence.gateway.runtime.simulate_telegram_update",
                return_value=simulated_result,
            ) as simulate,
        ):
            output = gateway_ask_telegram(
                config_manager=self.config_manager,
                state_db=self.state_db,
                message="hello",
                as_json=True,
            )

        payload = simulate.call_args.kwargs["update_payload"]
        self.assertEqual(payload["message"]["from"]["id"], "222")
        rendered = json.loads(output)
        self.assertEqual(rendered["user_id"], "222")
        self.assertEqual(rendered["message"], "hello")
        self.assertEqual(rendered["result"]["decision"], "allowed")

    def test_gateway_ask_telegram_prefers_single_allowlisted_user_over_stale_recent_trace(self) -> None:
        self.add_telegram_channel(allowed_users=["111"])
        self.config_manager.set_path("operator.experimental.telegram_terminal_bridge_enabled", True)
        simulated_result = SimpleNamespace(
            ok=True,
            decision="allowed",
            detail={"response_text": "Spark reply text"},
        )

        with (
            patch(
                "spark_intelligence.gateway.runtime.read_gateway_traces",
                return_value=[{"channel_id": "telegram", "external_user_id": "7777777"}],
            ),
            patch("spark_intelligence.gateway.runtime.read_outbound_audit", return_value=[]),
            patch(
                "spark_intelligence.gateway.runtime.simulate_telegram_update",
                return_value=simulated_result,
            ) as simulate,
        ):
            gateway_ask_telegram(
                config_manager=self.config_manager,
                state_db=self.state_db,
                message="hello",
            )

        payload = simulate.call_args.kwargs["update_payload"]
        self.assertEqual(payload["message"]["from"]["id"], "111")

    def test_gateway_ask_telegram_ignores_recent_trace_outside_allowlist_when_multiple_candidates_exist(self) -> None:
        self.add_telegram_channel(allowed_users=["111", "222"])
        self.config_manager.set_path("operator.experimental.telegram_terminal_bridge_enabled", True)

        with (
            patch(
                "spark_intelligence.gateway.runtime.read_gateway_traces",
                return_value=[{"channel_id": "telegram", "external_user_id": "7777777"}],
            ),
            patch("spark_intelligence.gateway.runtime.read_outbound_audit", return_value=[]),
        ):
            with self.assertRaisesRegex(ValueError, "multiple possible Telegram users"):
                gateway_ask_telegram(
                    config_manager=self.config_manager,
                    state_db=self.state_db,
                    message="hello",
                )

    def test_gateway_ask_telegram_requires_explicit_user_when_multiple_candidates_exist(self) -> None:
        self.add_telegram_channel(allowed_users=["111", "222"])
        self.config_manager.set_path("operator.experimental.telegram_terminal_bridge_enabled", True)

        with (
            patch("spark_intelligence.gateway.runtime.read_gateway_traces", return_value=[]),
            patch("spark_intelligence.gateway.runtime.read_outbound_audit", return_value=[]),
        ):
            with self.assertRaisesRegex(ValueError, "multiple possible Telegram users"):
                gateway_ask_telegram(
                    config_manager=self.config_manager,
                    state_db=self.state_db,
                    message="hello",
                )

    def test_gateway_ask_telegram_fails_closed_when_bridge_is_not_enabled(self) -> None:
        self.add_telegram_channel(allowed_users=["111"])

        with self.assertRaisesRegex(ValueError, "terminal-to-Telegram bridge is disabled"):
            gateway_ask_telegram(
                config_manager=self.config_manager,
                state_db=self.state_db,
                message="hello",
            )
