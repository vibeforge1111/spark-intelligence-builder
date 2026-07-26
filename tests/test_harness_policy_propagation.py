from __future__ import annotations

from spark_intelligence.harness_registry import (
    build_harness_prompt_context,
    build_harness_registry,
    build_harness_selection,
)
from spark_intelligence.harness_runtime import build_harness_task_envelope, execute_harness_task

from tests.test_support import SparkTestCase


class HarnessPolicyPropagationTests(SparkTestCase):
    def test_routed_selection_retains_contract_retry_and_approval_policy(self) -> None:
        selection = build_harness_selection(
            config_manager=self.config_manager,
            state_db=self.state_db,
            task="How would Builder execute this local task?",
        )
        contracts = {
            contract.harness_id: contract
            for contract in build_harness_registry(self.config_manager, self.state_db).contracts
        }
        contract = contracts[selection.harness_id]

        self.assertEqual(selection.retry_policy, contract.retry_policy)
        self.assertEqual(selection.approval_mode, contract.approval_mode)
        self.assertEqual(selection.to_payload()["retry_policy"], selection.retry_policy)
        self.assertEqual(selection.to_payload()["approval_mode"], selection.approval_mode)

    def test_forced_envelope_retains_the_selected_contract_policy(self) -> None:
        envelope = build_harness_task_envelope(
            config_manager=self.config_manager,
            state_db=self.state_db,
            task="Say hello when the voice provider is ready.",
            forced_harness_id="voice.io",
            channel_kind="telegram",
        )

        self.assertEqual(envelope.retry_policy, "retry_after_provider_or_encoding_repair")
        self.assertEqual(envelope.approval_mode, "operator_governed")
        self.assertEqual(envelope.to_payload()["retry_policy"], envelope.retry_policy)
        self.assertEqual(envelope.to_payload()["approval_mode"], envelope.approval_mode)

    def test_execution_evidence_retains_policy_from_routed_envelope(self) -> None:
        envelope = build_harness_task_envelope(
            config_manager=self.config_manager,
            state_db=self.state_db,
            task="Prepare this local Builder task.",
        )

        result = execute_harness_task(
            config_manager=self.config_manager,
            state_db=self.state_db,
            envelope=envelope,
        )
        contracts = {
            contract.harness_id: contract
            for contract in build_harness_registry(self.config_manager, self.state_db).contracts
        }
        contract = contracts[envelope.harness_id]

        payload = result.to_payload()
        self.assertEqual(payload["envelope"]["retry_policy"], contract.retry_policy)
        self.assertEqual(payload["envelope"]["approval_mode"], contract.approval_mode)

    def test_prompt_context_names_policy_underneath_the_human_reply(self) -> None:
        context = build_harness_prompt_context(
            config_manager=self.config_manager,
            state_db=self.state_db,
            user_message="What harness would Spark use to execute this local task?",
        )

        self.assertIn("retry_policy=", context)
        self.assertIn("approval_mode=operator_governed", context)
        self.assertIn("never grants authorization", context)
