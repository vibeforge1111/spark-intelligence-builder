from __future__ import annotations

import unittest

from spark_intelligence.harness_runtime.service import (
    HarnessExecutionResult,
    HarnessTaskEnvelope,
    _derive_follow_up_task,
)


def _make_envelope(*, harness_id: str = "researcher.advisory", task: str = "What is the difference?") -> HarnessTaskEnvelope:
    return HarnessTaskEnvelope(
        envelope_id="htask:test-001",
        task=task,
        harness_id=harness_id,
        owner_system="Spark Researcher",
        backend_kind="provider_bridge",
        session_scope="current_conversation",
        prompt_strategy="ephemeral_contextual_task_through_researcher_bridge",
        route_mode="researcher_advisory",
        required_capabilities=[],
        artifacts_expected=[],
        next_actions=[],
        limitations=[],
        channel_kind="cli",
        session_id="session-1",
        human_id="human-1",
        agent_id="agent-1",
    )


def _make_result(
    *,
    envelope: HarnessTaskEnvelope,
    artifacts: dict | None = None,
    summary: str = "Captured the reply trace.",
    status: str = "completed",
) -> HarnessExecutionResult:
    return HarnessExecutionResult(
        envelope=envelope,
        run_id="run-1",
        status=status,
        summary=summary,
        artifacts=artifacts or {},
        next_actions=[],
    )


class DeriveFollowUpTaskTests(unittest.TestCase):
    def test_voice_target_uses_reply_text_when_present(self) -> None:
        envelope = _make_envelope()
        result = _make_result(envelope=envelope, artifacts={"reply_text": "Here is the answer."})
        derived = _derive_follow_up_task(current_result=result, target_harness_id="voice.io")
        self.assertEqual(derived, "Say: Here is the answer.")

    def test_voice_target_falls_back_to_summary_when_reply_text_missing(self) -> None:
        envelope = _make_envelope()
        result = _make_result(envelope=envelope, artifacts={}, summary="Researcher harness summary.")
        derived = _derive_follow_up_task(current_result=result, target_harness_id="voice.io")
        self.assertEqual(derived, "Say: Researcher harness summary.")

    def test_voice_target_falls_back_to_summary_when_reply_text_whitespace_only(self) -> None:
        envelope = _make_envelope()
        result = _make_result(envelope=envelope, artifacts={"reply_text": "   "}, summary="Researcher harness summary.")
        derived = _derive_follow_up_task(current_result=result, target_harness_id="voice.io")
        self.assertEqual(derived, "Say: Researcher harness summary.")

    def test_swarm_target_assembles_multi_line_brief_with_reply(self) -> None:
        envelope = _make_envelope(task="Original ask.")
        result = _make_result(envelope=envelope, artifacts={"reply_text": "Upstream reply body."})
        derived = _derive_follow_up_task(current_result=result, target_harness_id="swarm.escalation")
        self.assertIn("Coordinate this through Swarm.", derived)
        self.assertIn("Original harness: researcher.advisory", derived)
        self.assertIn("Original task: Original ask.", derived)
        self.assertIn("Upstream reply: Upstream reply body.", derived)

    def test_swarm_target_uses_summary_when_reply_missing(self) -> None:
        envelope = _make_envelope(task="Original ask.")
        result = _make_result(envelope=envelope, artifacts={}, summary="Researcher captured a summary.")
        derived = _derive_follow_up_task(current_result=result, target_harness_id="swarm.escalation")
        self.assertIn("Upstream summary: Researcher captured a summary.", derived)
        self.assertNotIn("Upstream reply:", derived)

    def test_researcher_target_uses_reply_text_when_present(self) -> None:
        envelope = _make_envelope(task="Original ask.")
        result = _make_result(envelope=envelope, artifacts={"reply_text": "Carried-forward reply."})
        derived = _derive_follow_up_task(current_result=result, target_harness_id="researcher.advisory")
        self.assertEqual(derived, "Carried-forward reply.")

    def test_researcher_target_falls_back_to_original_task(self) -> None:
        envelope = _make_envelope(task="Original ask.")
        result = _make_result(envelope=envelope, artifacts={})
        derived = _derive_follow_up_task(current_result=result, target_harness_id="researcher.advisory")
        self.assertEqual(derived, "Original ask.")

    def test_builder_direct_target_falls_back_to_summary(self) -> None:
        envelope = _make_envelope()
        result = _make_result(envelope=envelope, artifacts={}, summary="Researcher harness summary.")
        derived = _derive_follow_up_task(current_result=result, target_harness_id="builder.direct")
        self.assertEqual(derived, "Researcher harness summary.")

    def test_unknown_target_returns_original_task_unchanged(self) -> None:
        envelope = _make_envelope(task="Original ask.")
        result = _make_result(envelope=envelope, artifacts={"reply_text": "irrelevant"})
        derived = _derive_follow_up_task(current_result=result, target_harness_id="unknown.harness")
        self.assertEqual(derived, "Original ask.")


if __name__ == "__main__":
    unittest.main()
