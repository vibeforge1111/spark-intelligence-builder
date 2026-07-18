from __future__ import annotations

from spark_intelligence.harness_runtime import (
    build_harness_task_envelope,
    execute_harness_task,
    with_harness_local_operator_turn_intent,
)
from spark_intelligence.observability.store import latest_events_by_type

from tests.test_support import SparkTestCase


class HarnessRuntimeAuthorityTests(SparkTestCase):
    def _envelope(self, harness_id: str, task: str):
        return build_harness_task_envelope(
            config_manager=self.config_manager,
            state_db=self.state_db,
            task=task,
            forced_harness_id=harness_id,
            channel_kind="cli",
            session_id="session-authority",
            human_id="human-authority",
            agent_id="agent-authority",
        )

    def _ledger_events(self, event_type: str):
        return latest_events_by_type(self.state_db, event_type=event_type, limit=10)

    def test_builder_direct_without_turn_authority_is_blocked(self) -> None:
        result = execute_harness_task(
            config_manager=self.config_manager,
            state_db=self.state_db,
            envelope=self._envelope("builder.direct", "What chips are active right now?"),
        )

        self.assertEqual(result.status, "blocked")
        self.assertNotIn("execution_contract", result.artifacts)
        self.assertIn("missing", " ".join(result.artifacts["harness_authority"]["reason_codes"]))
        self.assertEqual(self._ledger_events("tool_call_ledger_recorded"), [])
        self.assertEqual(self._ledger_events("tool_call_ledger_result_recorded"), [])

    def test_builder_direct_authority_records_linked_partial_result(self) -> None:
        envelope = with_harness_local_operator_turn_intent(
            self._envelope("builder.direct", "What chips are active right now?")
        )

        result = execute_harness_task(
            config_manager=self.config_manager,
            state_db=self.state_db,
            envelope=envelope,
        )

        self.assertEqual(result.status, "prepared")
        self.assertIn("execution_contract", result.artifacts)
        self.assertEqual(result.artifacts["harness_authority"]["outcome"], "execute")
        initial = self._ledger_events("tool_call_ledger_recorded")
        final = self._ledger_events("tool_call_ledger_result_recorded")
        self.assertEqual(len(initial), 1)
        self.assertEqual(len(final), 1)
        self.assertEqual(final[0]["parent_event_id"], initial[0]["event_id"])
        self.assertEqual(final[0]["facts_json"]["result_status"], "partial")

    def test_browser_url_without_turn_authority_is_blocked(self) -> None:
        result = execute_harness_task(
            config_manager=self.config_manager,
            state_db=self.state_db,
            envelope=self._envelope("browser.grounded", "Open https://example.com and inspect it."),
        )

        self.assertEqual(result.status, "blocked")
        self.assertNotIn("browser_navigate_payload", result.artifacts)
        self.assertIn("missing", " ".join(result.artifacts["harness_authority"]["reason_codes"]))
        self.assertEqual(self._ledger_events("tool_call_ledger_recorded"), [])
        self.assertEqual(self._ledger_events("tool_call_ledger_result_recorded"), [])

    def test_browser_url_authority_is_carried_into_payload_and_result_ledger(self) -> None:
        envelope = with_harness_local_operator_turn_intent(
            self._envelope("browser.grounded", "Open https://example.com and inspect it.")
        )

        result = execute_harness_task(
            config_manager=self.config_manager,
            state_db=self.state_db,
            envelope=envelope,
        )

        self.assertEqual(result.status, "prepared")
        payload = result.artifacts["browser_navigate_payload"]
        self.assertEqual(payload["request_id"], envelope.envelope_id)
        self.assertEqual(payload["agent_id"], envelope.agent_id)
        self.assertEqual(payload["governor_decision"]["outcome"], "execute")
        self.assertEqual(payload["turn_intent_envelope_vnext"]["turn_id"], envelope.turn_intent_payload["turn_id"])
        initial = self._ledger_events("tool_call_ledger_recorded")
        final = self._ledger_events("tool_call_ledger_result_recorded")
        self.assertEqual(len(initial), 1)
        self.assertEqual(len(final), 1)
        self.assertEqual(final[0]["parent_event_id"], initial[0]["event_id"])
        self.assertEqual(final[0]["facts_json"]["result_status"], "partial")

    def test_browser_rejects_builder_direct_authority(self) -> None:
        builder = with_harness_local_operator_turn_intent(
            self._envelope("builder.direct", "What chips are active right now?")
        )
        browser = self._envelope("browser.grounded", "Open https://example.com and inspect it.")
        browser = browser.with_turn_intent_payload(builder.turn_intent_payload)

        result = execute_harness_task(
            config_manager=self.config_manager,
            state_db=self.state_db,
            envelope=browser,
        )

        self.assertEqual(result.status, "blocked")
        self.assertNotIn("browser_navigate_payload", result.artifacts)
        initial = self._ledger_events("tool_call_ledger_recorded")
        self.assertEqual(len(initial), 1)
        self.assertEqual(initial[0]["facts_json"]["authorization_verdict"], "deny")

    def test_browser_without_url_needs_input_before_authority(self) -> None:
        result = execute_harness_task(
            config_manager=self.config_manager,
            state_db=self.state_db,
            envelope=self._envelope("browser.grounded", "Search the web for Spark architecture."),
        )

        self.assertEqual(result.status, "needs_input")
        self.assertIn("browser_status_payload", result.artifacts)
        self.assertEqual(self._ledger_events("tool_call_ledger_recorded"), [])
        self.assertEqual(self._ledger_events("tool_call_ledger_result_recorded"), [])
