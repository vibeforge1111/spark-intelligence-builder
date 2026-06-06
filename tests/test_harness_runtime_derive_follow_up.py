from __future__ import annotations

import unittest

from spark_intelligence.harness_runtime.service import (
    HarnessExecutionResult,
    HarnessTaskEnvelope,
    _derive_follow_up_task,
)


def _make_envelope(*, task: str = "Original task", harness_id: str = "researcher.advisory") -> HarnessTaskEnvelope:
    return HarnessTaskEnvelope(
        envelope_id="htask:abc123abc123",
        task=task,
        harness_id=harness_id,
        owner_system="Spark Researcher",
        backend_kind="provider_bridge",
        session_scope="current_conversation",
        prompt_strategy="ps",
        route_mode="researcher_advisory",
        required_capabilities=["reasoning"],
        artifacts_expected=["reply_text"],
        next_actions=[],
        limitations=[],
        channel_kind=None,
        session_id=None,
        human_id=None,
        agent_id=None,
    )


def _make_result(*, artifacts: dict, summary: str = "Did the thing.") -> HarnessExecutionResult:
    return HarnessExecutionResult(
        envelope=_make_envelope(),
        run_id="run-1",
        status="completed",
        summary=summary,
        artifacts=artifacts,
        next_actions=[],
    )


class HarnessRuntimeDeriveFollowUpTests(unittest.TestCase):
    def test_voice_target_uses_reply_text_prefixed_with_say(self) -> None:
        result = _make_result(artifacts={"reply_text": "Here is the answer."})
        derived = _derive_follow_up_task(current_result=result, target_harness_id="voice.io")
        self.assertEqual(derived, "Say: Here is the answer.")

    def test_voice_target_falls_back_to_summary_without_reply_text(self) -> None:
        result = _make_result(artifacts={}, summary="Did the thing.")
        derived = _derive_follow_up_task(current_result=result, target_harness_id="voice.io")
        self.assertEqual(derived, "Say: Did the thing.")

    def test_swarm_target_builds_coordinate_block_with_reply(self) -> None:
        result = _make_result(artifacts={"reply_text": "Here is the answer."})
        derived = _derive_follow_up_task(current_result=result, target_harness_id="swarm.escalation")
        self.assertIn("Coordinate this through Swarm.", derived)
        self.assertIn("Original harness: researcher.advisory", derived)
        self.assertIn("Original task: Original task", derived)
        self.assertIn("Upstream reply: Here is the answer.", derived)

    def test_swarm_target_falls_back_to_summary_without_reply(self) -> None:
        result = _make_result(artifacts={}, summary="Did the thing.")
        derived = _derive_follow_up_task(current_result=result, target_harness_id="swarm.escalation")
        self.assertIn("Upstream summary: Did the thing.", derived)
        self.assertNotIn("Upstream reply:", derived)

    def test_researcher_target_uses_reply_text_directly(self) -> None:
        result = _make_result(artifacts={"reply_text": "Here is the answer."})
        derived = _derive_follow_up_task(current_result=result, target_harness_id="researcher.advisory")
        self.assertEqual(derived, "Here is the answer.")

    def test_researcher_target_falls_back_to_original_task(self) -> None:
        result = _make_result(artifacts={})
        derived = _derive_follow_up_task(current_result=result, target_harness_id="researcher.advisory")
        self.assertEqual(derived, "Original task")

    def test_builder_target_uses_reply_text(self) -> None:
        result = _make_result(artifacts={"reply_text": "Here is the answer."})
        derived = _derive_follow_up_task(current_result=result, target_harness_id="builder.direct")
        self.assertEqual(derived, "Here is the answer.")

    def test_builder_target_falls_back_to_summary_without_reply(self) -> None:
        result = _make_result(artifacts={}, summary="Did the thing.")
        derived = _derive_follow_up_task(current_result=result, target_harness_id="builder.direct")
        self.assertEqual(derived, "Did the thing.")

    def test_unknown_harness_returns_original_task(self) -> None:
        result = _make_result(artifacts={"reply_text": "Ignored here."})
        derived = _derive_follow_up_task(current_result=result, target_harness_id="totally.unknown")
        self.assertEqual(derived, "Original task")

    def test_whitespace_only_target_returns_original_task(self) -> None:
        result = _make_result(artifacts={"reply_text": "Ignored."})
        derived = _derive_follow_up_task(current_result=result, target_harness_id="   ")
        self.assertEqual(derived, "Original task")


if __name__ == "__main__":
    unittest.main()
