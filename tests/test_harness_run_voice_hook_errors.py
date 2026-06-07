from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from spark_intelligence.harness_runtime.service import (
    HarnessTaskEnvelope,
    _run_voice_hook,
)

from tests.test_support import SparkTestCase


def _make_envelope() -> HarnessTaskEnvelope:
    return HarnessTaskEnvelope(
        envelope_id="htask:voice-run",
        task="Say: Hello.",
        harness_id="voice.io",
        owner_system="Spark Voice",
        backend_kind="voice_bridge",
        session_scope="message_turn",
        prompt_strategy="transcript_then_spoken_reply_render",
        route_mode="voice_io",
        required_capabilities=[],
        artifacts_expected=[],
        next_actions=[],
        limitations=[],
        channel_kind="cli",
        session_id="session-vh",
        human_id="human-vh",
        agent_id="agent-vh",
    )


class RunVoiceHookErrorTests(SparkTestCase):
    def test_raises_when_no_chip_supports_hook(self) -> None:
        envelope = _make_envelope()
        with patch(
            "spark_intelligence.attachments.run_first_chip_hook_supporting",
            return_value=None,
        ):
            with self.assertRaises(RuntimeError) as ctx:
                _run_voice_hook(
                    config_manager=self.config_manager,
                    state_db=self.state_db,
                    envelope=envelope,
                    hook="voice.status",
                    payload={"surface": "cli"},
                    run_id="run-1",
                )
            self.assertIn("voice.status", str(ctx.exception))
            self.assertIn("No attached chip", str(ctx.exception))

    def test_raises_with_stderr_message_when_chip_execution_not_ok(self) -> None:
        envelope = _make_envelope()
        fake_execution = SimpleNamespace(
            ok=False,
            stderr="chip provider unreachable",
            stdout="",
            output={},
            chip_key="domain-chip-voice-comms",
        )
        with patch(
            "spark_intelligence.attachments.run_first_chip_hook_supporting",
            return_value=fake_execution,
        ):
            with self.assertRaises(RuntimeError) as ctx:
                _run_voice_hook(
                    config_manager=self.config_manager,
                    state_db=self.state_db,
                    envelope=envelope,
                    hook="voice.speak",
                    payload={"text": "Hi."},
                    run_id="run-2",
                )
            self.assertIn("chip provider unreachable", str(ctx.exception))

    def test_raises_with_stdout_fallback_when_stderr_empty(self) -> None:
        envelope = _make_envelope()
        fake_execution = SimpleNamespace(
            ok=False,
            stderr="",
            stdout="speak hook returned no audio",
            output={},
            chip_key="domain-chip-voice-comms",
        )
        with patch(
            "spark_intelligence.attachments.run_first_chip_hook_supporting",
            return_value=fake_execution,
        ):
            with self.assertRaises(RuntimeError) as ctx:
                _run_voice_hook(
                    config_manager=self.config_manager,
                    state_db=self.state_db,
                    envelope=envelope,
                    hook="voice.speak",
                    payload={"text": "Hi."},
                    run_id="run-3",
                )
            self.assertIn("speak hook returned no audio", str(ctx.exception))

    def test_raises_with_generic_default_message_when_no_stream_output(self) -> None:
        envelope = _make_envelope()
        fake_execution = SimpleNamespace(
            ok=False,
            stderr="",
            stdout="",
            output={},
            chip_key="domain-chip-voice-comms",
        )
        with patch(
            "spark_intelligence.attachments.run_first_chip_hook_supporting",
            return_value=fake_execution,
        ):
            with self.assertRaises(RuntimeError) as ctx:
                _run_voice_hook(
                    config_manager=self.config_manager,
                    state_db=self.state_db,
                    envelope=envelope,
                    hook="voice.status",
                    payload={"surface": "cli"},
                    run_id="run-4",
                )
            self.assertIn("voice.status", str(ctx.exception))
            self.assertIn("failed", str(ctx.exception))

    def test_success_returns_output_dict_and_chip_key(self) -> None:
        envelope = _make_envelope()
        fake_execution = SimpleNamespace(
            ok=True,
            stderr="",
            stdout="",
            output={"result": {"ready": True}},
            chip_key="domain-chip-voice-comms",
        )
        with (
            patch(
                "spark_intelligence.attachments.run_first_chip_hook_supporting",
                return_value=fake_execution,
            ),
            patch(
                "spark_intelligence.attachments.record_chip_hook_execution",
                return_value=None,
            ),
        ):
            output, chip_key = _run_voice_hook(
                config_manager=self.config_manager,
                state_db=self.state_db,
                envelope=envelope,
                hook="voice.status",
                payload={"surface": "cli"},
                run_id="run-5",
            )
            self.assertEqual(output, {"result": {"ready": True}})
            self.assertEqual(chip_key, "domain-chip-voice-comms")

    def test_success_with_non_dict_output_returns_empty_dict(self) -> None:
        envelope = _make_envelope()
        fake_execution = SimpleNamespace(
            ok=True,
            stderr="",
            stdout="",
            output="not a dict",
            chip_key="domain-chip-voice-comms",
        )
        with (
            patch(
                "spark_intelligence.attachments.run_first_chip_hook_supporting",
                return_value=fake_execution,
            ),
            patch(
                "spark_intelligence.attachments.record_chip_hook_execution",
                return_value=None,
            ),
        ):
            output, chip_key = _run_voice_hook(
                config_manager=self.config_manager,
                state_db=self.state_db,
                envelope=envelope,
                hook="voice.status",
                payload={"surface": "cli"},
                run_id="run-6",
            )
            # The helper normalizes non-dict output to an empty dict
            self.assertEqual(output, {})
            self.assertEqual(chip_key, "domain-chip-voice-comms")
