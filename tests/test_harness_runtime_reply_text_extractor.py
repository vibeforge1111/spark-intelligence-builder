from __future__ import annotations

import unittest

from spark_intelligence.harness_runtime.service import (
    HarnessExecutionResult,
    HarnessTaskEnvelope,
    _extract_reply_text_from_result,
)


def _make_envelope() -> HarnessTaskEnvelope:
    return HarnessTaskEnvelope(
        envelope_id="htask:abc",
        task="t",
        harness_id="h",
        owner_system="o",
        backend_kind="b",
        session_scope="s",
        prompt_strategy="p",
        route_mode="r",
        required_capabilities=[],
        artifacts_expected=[],
        next_actions=[],
        limitations=[],
        channel_kind=None,
        session_id=None,
        human_id=None,
        agent_id=None,
    )


def _result_with_artifacts(artifacts: dict) -> HarnessExecutionResult:
    return HarnessExecutionResult(
        envelope=_make_envelope(),
        run_id="run-1",
        status="completed",
        summary="s",
        artifacts=artifacts,
        next_actions=[],
    )


class HarnessRuntimeReplyTextExtractorTests(unittest.TestCase):
    def test_returns_reply_text_when_string(self) -> None:
        self.assertEqual(
            _extract_reply_text_from_result(_result_with_artifacts({"reply_text": "hello"})),
            "hello",
        )

    def test_strips_surrounding_whitespace_from_reply_text(self) -> None:
        self.assertEqual(
            _extract_reply_text_from_result(_result_with_artifacts({"reply_text": "  hello  "})),
            "hello",
        )

    def test_whitespace_only_reply_text_returns_none(self) -> None:
        self.assertIsNone(
            _extract_reply_text_from_result(_result_with_artifacts({"reply_text": "   "}))
        )

    def test_empty_reply_text_returns_none(self) -> None:
        self.assertIsNone(
            _extract_reply_text_from_result(_result_with_artifacts({"reply_text": ""}))
        )

    def test_non_string_reply_text_returns_none(self) -> None:
        self.assertIsNone(
            _extract_reply_text_from_result(_result_with_artifacts({"reply_text": 123}))
        )

    def test_missing_reply_text_returns_none_when_no_spoken_audio(self) -> None:
        self.assertIsNone(_extract_reply_text_from_result(_result_with_artifacts({})))

    def test_falls_back_to_spoken_audio_text_when_reply_text_absent(self) -> None:
        self.assertEqual(
            _extract_reply_text_from_result(
                _result_with_artifacts({"spoken_audio": {"text": "transcribed!"}})
            ),
            "transcribed!",
        )

    def test_non_dict_spoken_audio_falls_through_to_none(self) -> None:
        self.assertIsNone(
            _extract_reply_text_from_result(
                _result_with_artifacts({"spoken_audio": "not-a-dict"})
            )
        )

    def test_spoken_audio_without_text_key_returns_none(self) -> None:
        self.assertIsNone(
            _extract_reply_text_from_result(
                _result_with_artifacts({"spoken_audio": {"filename": "foo.ogg"}})
            )
        )

    def test_reply_text_takes_precedence_over_spoken_audio(self) -> None:
        self.assertEqual(
            _extract_reply_text_from_result(
                _result_with_artifacts(
                    {"reply_text": "primary", "spoken_audio": {"text": "fallback"}}
                )
            ),
            "primary",
        )


if __name__ == "__main__":
    unittest.main()
