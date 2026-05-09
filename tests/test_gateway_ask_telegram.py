from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

from spark_intelligence.adapters.telegram.runtime import (
    _memory_doctor_distress_score,
    _memory_doctor_distress_signals,
    build_telegram_runtime_summary,
)
from spark_intelligence.gateway.tracing import append_gateway_trace
from spark_intelligence.gateway.runtime import gateway_ask_telegram
from spark_intelligence.observability.store import record_event

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

    def test_memory_doctor_contextual_trigger_signal_matrix(self) -> None:
        direct_context_loss_cases = {
            "you forgot what we were discussing": {"memory_context_reference", "memory_distress_verb"},
            "you lost the conversation": {"memory_context_reference", "memory_distress_verb"},
            "the context disappeared": {"memory_context_reference", "memory_distress_verb"},
            "you blanked on what I just said": {"memory_context_reference", "memory_distress_verb"},
            "the thread got wiped": {"memory_context_reference", "memory_distress_verb"},
            "you skipped my last message": {"memory_context_reference", "memory_distress_verb"},
            "did you forget the last thing I said": {
                "memory_context_reference",
                "memory_distress_verb",
                "diagnostic_question",
            },
        }
        for phrase, expected_signals in direct_context_loss_cases.items():
            with self.subTest(phrase=phrase):
                signal_names = {str(signal["name"]) for signal in _memory_doctor_distress_signals(phrase)}
                self.assertGreaterEqual(_memory_doctor_distress_score(phrase), 4)
                self.assertTrue(expected_signals.issubset(signal_names))

        direct_repeat_complaint_cases = {
            "why are you asking me again": {
                "close_turn_repeat_frustration",
                "operator_frustration",
                "diagnostic_question",
            },
        }
        for phrase, expected_signals in direct_repeat_complaint_cases.items():
            with self.subTest(phrase=phrase):
                signal_names = {str(signal["name"]) for signal in _memory_doctor_distress_signals(phrase)}
                self.assertGreaterEqual(_memory_doctor_distress_score(phrase), 4)
                self.assertTrue(expected_signals.issubset(signal_names))

        previous_failure_only_cases = {
            "are you still with me": {"operator_frustration"},
            "you asked me again": {"close_turn_repeat_frustration", "operator_frustration"},
            "I already answered that": {"close_turn_repeat_frustration"},
            "we literally just covered this": {"close_turn_repeat_frustration"},
            "you lost the plot": {"memory_distress_verb"},
            "you went silent": {"operator_frustration"},
            "Spark froze again": {"operator_frustration"},
        }
        for phrase, expected_signals in previous_failure_only_cases.items():
            with self.subTest(phrase=phrase):
                signal_names = {str(signal["name"]) for signal in _memory_doctor_distress_signals(phrase)}
                self.assertLess(_memory_doctor_distress_score(phrase), 4)
                self.assertGreaterEqual(_memory_doctor_distress_score(phrase) + 2, 3)
                self.assertTrue(expected_signals.issubset(signal_names))

        plain_chat_cases = (
            "can you summarize the plan",
            "hello",
            "what do you think about the update",
        )
        for phrase in plain_chat_cases:
            with self.subTest(phrase=phrase):
                self.assertLess(_memory_doctor_distress_score(phrase), 3)

    def test_gateway_ask_telegram_runs_memory_doctor_from_natural_language(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])
        self.config_manager.set_path("operator.experimental.telegram_terminal_bridge_enabled", True)

        output = json.loads(
            gateway_ask_telegram(
                config_manager=self.config_manager,
                state_db=self.state_db,
                message="check memory deletes",
                user_id="111",
                as_json=True,
            )
        )

        self.assertEqual(output["result"]["detail"]["response_text"].splitlines()[0], "Memory Doctor: healthy.")

    def test_gateway_ask_telegram_shows_memory_doctor_help(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])
        self.config_manager.set_path("operator.experimental.telegram_terminal_bridge_enabled", True)

        output = json.loads(
            gateway_ask_telegram(
                config_manager=self.config_manager,
                state_db=self.state_db,
                message="how do I use memory doctor?",
                user_id="111",
                as_json=True,
            )
        )

        detail = output["result"]["detail"]
        response_text = detail["response_text"]
        self.assertEqual(response_text.splitlines()[0], "Memory Doctor helps when memory or close context feels wrong.")
        self.assertIn("Try: run memory doctor for last request", response_text)
        self.assertIn("Try: you lost the thread", response_text)
        self.assertNotIn("runtime_command_metadata", detail)

    def test_gateway_ask_telegram_runs_topic_memory_doctor_from_natural_language(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])
        self.config_manager.set_path("operator.experimental.telegram_terminal_bridge_enabled", True)

        output = json.loads(
            gateway_ask_telegram(
                config_manager=self.config_manager,
                state_db=self.state_db,
                message="why did memory recall Maya",
                user_id="111",
                as_json=True,
            )
        )

        response_text = output["result"]["detail"]["response_text"]
        self.assertEqual(response_text.splitlines()[0], "Memory Doctor: healthy.")
        self.assertIn("Topic: Maya.", response_text)

    def test_gateway_ask_telegram_runs_memory_doctor_for_request_id(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])
        self.config_manager.set_path("operator.experimental.telegram_terminal_bridge_enabled", True)
        append_gateway_trace(
            self.config_manager,
            {
                "event": "telegram_update_processed",
                "channel_id": "telegram",
                "request_id": "req-doctor-prior",
                "telegram_user_id": "111",
                "chat_id": "111",
                "session_id": "sess-doctor-request",
                "user_message_preview": "The phrase is Cedar Compass 509.",
                "response_preview": "Noted.",
            },
        )
        append_gateway_trace(
            self.config_manager,
            {
                "event": "telegram_update_processed",
                "channel_id": "telegram",
                "request_id": "req-doctor-target",
                "telegram_user_id": "111",
                "chat_id": "111",
                "session_id": "sess-doctor-request",
                "user_message_preview": "What phrase did I just give you?",
                "response_preview": "I can see the context capsule, but not the message before this.",
            },
        )
        record_event(
            self.state_db,
            event_type="context_capsule_compiled",
            component="researcher_bridge",
            summary="Target context capsule.",
            request_id="req-doctor-target",
            human_id="human:telegram:111",
            facts={"source_counts": {"recent_conversation": 0}, "source_ledger": []},
        )
        record_event(
            self.state_db,
            event_type="context_capsule_compiled",
            component="researcher_bridge",
            summary="Newer healthy context capsule.",
            request_id="req-doctor-newer",
            human_id="human:telegram:111",
            facts={
                "source_counts": {"recent_conversation": 2},
                "source_ledger": [
                    {"source": "recent_conversation", "present": True, "count": 2, "priority": 8, "role": "supporting"}
                ],
            },
        )

        output = json.loads(
            gateway_ask_telegram(
                config_manager=self.config_manager,
                state_db=self.state_db,
                message="run memory doctor for request req-doctor-target",
                user_id="111",
                as_json=True,
            )
        )

        response_text = output["result"]["detail"]["response_text"]
        self.assertEqual(response_text.splitlines()[0], "Memory Doctor: needs attention.")
        self.assertIn("Request: req-doctor-target.", response_text)
        self.assertIn("gateway had 1 earlier same-session message", response_text)

    def test_gateway_ask_telegram_runs_memory_doctor_for_last_request(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])
        self.config_manager.set_path("operator.experimental.telegram_terminal_bridge_enabled", True)
        append_gateway_trace(
            self.config_manager,
            {
                "event": "telegram_update_processed",
                "channel_id": "telegram",
                "request_id": "req-doctor-last-seed",
                "telegram_user_id": "111",
                "chat_id": "111",
                "session_id": "session:telegram:dm:111",
                "user_message_preview": "The phrase is Violet Harbor 912.",
                "response_preview": "Noted.",
            },
        )
        append_gateway_trace(
            self.config_manager,
            {
                "event": "telegram_update_processed",
                "channel_id": "telegram",
                "request_id": "req-doctor-last-target",
                "telegram_user_id": "111",
                "chat_id": "111",
                "session_id": "session:telegram:dm:111",
                "user_message_preview": "What phrase did I just give you?",
                "response_preview": "I do not have the previous message in context.",
            },
        )

        output = json.loads(
            gateway_ask_telegram(
                config_manager=self.config_manager,
                state_db=self.state_db,
                message="run memory doctor for last request",
                user_id="111",
                as_json=True,
            )
        )

        response_text = output["result"]["detail"]["response_text"]
        metadata = output["result"]["detail"]["runtime_command_metadata"]
        self.assertEqual(response_text.splitlines()[0], "Memory Doctor: needs attention.")
        self.assertIn("Request: req-doctor-last-target.", response_text)
        self.assertIn("no provider capsule event was recorded", response_text)
        self.assertEqual(metadata["diagnosed_request_id"], "req-doctor-last-target")
        self.assertEqual(metadata["request_selector"], "previous_gateway_turn")
        self.assertFalse(metadata["memory_doctor_ok"])

        blank_output = json.loads(
            gateway_ask_telegram(
                config_manager=self.config_manager,
                state_db=self.state_db,
                message="why did Spark go blank?",
                user_id="111",
                as_json=True,
            )
        )

        blank_response_text = blank_output["result"]["detail"]["response_text"]
        blank_metadata = blank_output["result"]["detail"]["runtime_command_metadata"]
        self.assertEqual(blank_response_text.splitlines()[0], "Memory Doctor: needs attention.")
        self.assertIn("Trigger: memory/context loss complaint; previous turn looked like memory failure.", blank_response_text)
        self.assertIn("Request: req-doctor-last-target.", blank_response_text)
        self.assertGreaterEqual(blank_metadata["contextual_trigger_score"], 3)
        self.assertEqual(blank_metadata["contextual_trigger_threshold"], 3)
        self.assertIn("previous_turn_memory_failure_signal", blank_metadata["contextual_trigger_signals"])
        self.assertIn("previous_user_close_turn_probe", blank_metadata["previous_failure_signals"])
        self.assertIn("previous_response_context_gap", blank_metadata["previous_failure_signals"])

        frustration_output = json.loads(
            gateway_ask_telegram(
                config_manager=self.config_manager,
                state_db=self.state_db,
                message="are you there",
                user_id="111",
                as_json=True,
            )
        )

        frustration_response_text = frustration_output["result"]["detail"]["response_text"]
        frustration_metadata = frustration_output["result"]["detail"]["runtime_command_metadata"]
        self.assertEqual(frustration_response_text.splitlines()[0], "Memory Doctor: needs attention.")
        self.assertIn("Request: req-doctor-last-target.", frustration_response_text)
        self.assertGreaterEqual(frustration_metadata["contextual_trigger_score"], 3)
        self.assertEqual(frustration_metadata["contextual_trigger_threshold"], 3)
        self.assertIn("operator_frustration", frustration_metadata["contextual_trigger_signals"])
        self.assertTrue(frustration_metadata["previous_failure_signal"])
        self.assertIn("previous_response_context_gap", frustration_metadata["previous_failure_signals"])

    def test_gateway_ask_telegram_does_not_run_memory_doctor_for_weak_blankness_after_normal_turn(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])
        self.config_manager.set_path("operator.experimental.telegram_terminal_bridge_enabled", True)
        append_gateway_trace(
            self.config_manager,
            {
                "event": "telegram_update_processed",
                "channel_id": "telegram",
                "request_id": "req-normal-prior",
                "telegram_user_id": "111",
                "chat_id": "111",
                "session_id": "session:telegram:dm:111",
                "user_message_preview": "Can you summarize today's plan?",
                "response_preview": "Sure. The current plan is to keep working through the memory diagnostics.",
                "bridge_mode": "external_autodiscovered",
                "routing_decision": "researcher_advisory",
            },
        )

        output = json.loads(
            gateway_ask_telegram(
                config_manager=self.config_manager,
                state_db=self.state_db,
                message="why did Spark go blank?",
                user_id="111",
                as_json=True,
            )
        )

        detail = output["result"]["detail"]
        self.assertNotEqual(detail["response_text"].splitlines()[0], "Memory Doctor: needs attention.")
        self.assertNotIn("runtime_command_metadata", detail)

    def test_gateway_ask_telegram_runs_memory_doctor_for_close_turn_repeat_frustration(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])
        self.config_manager.set_path("operator.experimental.telegram_terminal_bridge_enabled", True)
        append_gateway_trace(
            self.config_manager,
            {
                "event": "telegram_update_processed",
                "channel_id": "telegram",
                "request_id": "req-name-repeat",
                "telegram_user_id": "111",
                "chat_id": "111",
                "session_id": "session:telegram:dm:111",
                "user_message_preview": "not Maya",
                "response_preview": "Got it, you're not Maya. What should I call you instead?",
                "bridge_mode": "external_autodiscovered",
                "routing_decision": "researcher_advisory",
            },
        )

        output = json.loads(
            gateway_ask_telegram(
                config_manager=self.config_manager,
                state_db=self.state_db,
                message="i just told you",
                user_id="111",
                as_json=True,
            )
        )

        detail = output["result"]["detail"]
        metadata = detail["runtime_command_metadata"]
        self.assertEqual(detail["response_text"].splitlines()[0], "Memory Doctor: needs attention.")
        self.assertIn("Trigger: close-turn repeat complaint; previous turn looked like memory failure.", detail["response_text"])
        self.assertIn("Request: req-name-repeat.", detail["response_text"])
        self.assertEqual(metadata["diagnosed_request_id"], "req-name-repeat")
        self.assertEqual(metadata["request_selector"], "previous_gateway_turn")
        self.assertGreaterEqual(metadata["contextual_trigger_score"], 3)
        self.assertEqual(metadata["contextual_trigger_threshold"], 3)
        self.assertIn("close_turn_repeat_frustration", metadata["contextual_trigger_signals"])
        self.assertIn("previous_turn_memory_failure_signal", metadata["contextual_trigger_signals"])
        self.assertIn("previous_response_context_gap", metadata["previous_failure_signals"])
        self.assertIn("previous_response_identity_conflict", metadata["previous_failure_signals"])

    def test_gateway_ask_telegram_runs_memory_doctor_for_identity_correction_after_wrong_name(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])
        self.config_manager.set_path("operator.experimental.telegram_terminal_bridge_enabled", True)
        append_gateway_trace(
            self.config_manager,
            {
                "event": "telegram_update_processed",
                "channel_id": "telegram",
                "request_id": "req-name-conflict",
                "telegram_user_id": "111",
                "chat_id": "111",
                "session_id": "session:telegram:dm:111",
                "user_message_preview": "My written name is Cem, pronounced like Gem.",
                "response_preview": "Got it, Maya. I'll write it as Cem and pronounce it like Gem.",
                "bridge_mode": "external_autodiscovered",
                "routing_decision": "researcher_advisory",
            },
        )

        output = json.loads(
            gateway_ask_telegram(
                config_manager=self.config_manager,
                state_db=self.state_db,
                message="not Maya",
                user_id="111",
                as_json=True,
            )
        )

        detail = output["result"]["detail"]
        metadata = detail["runtime_command_metadata"]
        self.assertEqual(detail["response_text"].splitlines()[0], "Memory Doctor: needs attention.")
        self.assertIn("Trigger: identity correction complaint; previous turn looked like memory failure.", detail["response_text"])
        self.assertEqual(metadata["diagnosed_request_id"], "req-name-conflict")
        self.assertEqual(metadata["request_selector"], "previous_gateway_turn")
        self.assertIn("identity_correction_after_wrong_name", metadata["contextual_trigger_signals"])
        self.assertIn("previous_response_identity_conflict", metadata["previous_failure_signals"])

    def test_gateway_ask_telegram_runs_memory_doctor_for_strong_context_loss_complaint(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])
        self.config_manager.set_path("operator.experimental.telegram_terminal_bridge_enabled", True)
        append_gateway_trace(
            self.config_manager,
            {
                "event": "telegram_update_processed",
                "channel_id": "telegram",
                "request_id": "req-context-loss-prior",
                "telegram_user_id": "111",
                "chat_id": "111",
                "session_id": "session:telegram:dm:111",
                "user_message_preview": "Can you summarize today's plan?",
                "response_preview": "Sure. The current plan is to keep working through the memory diagnostics.",
                "bridge_mode": "external_autodiscovered",
                "routing_decision": "researcher_advisory",
            },
        )

        output = json.loads(
            gateway_ask_telegram(
                config_manager=self.config_manager,
                state_db=self.state_db,
                message="you lost the thread",
                user_id="111",
                as_json=True,
            )
        )

        detail = output["result"]["detail"]
        metadata = detail["runtime_command_metadata"]
        self.assertIn("Memory Doctor:", detail["response_text"].splitlines()[0])
        self.assertIn("Trigger: memory/context loss complaint.", detail["response_text"])
        self.assertEqual(metadata["diagnosed_request_id"], "req-context-loss-prior")
        self.assertEqual(metadata["request_selector"], "previous_gateway_turn")
        self.assertGreaterEqual(metadata["contextual_trigger_score"], 4)
        self.assertEqual(metadata["contextual_trigger_threshold"], 4)
        self.assertEqual(metadata["contextual_trigger_signals"], ["memory_context_reference", "memory_distress_verb"])
        self.assertFalse(metadata["previous_failure_signal"])

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
