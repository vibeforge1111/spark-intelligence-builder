from __future__ import annotations

import unittest

from spark_intelligence.harness_runtime.service import _classify_voice_task


class HarnessRuntimeVoiceTaskClassifierTests(unittest.TestCase):
    def test_unspecified_for_empty_task(self) -> None:
        self.assertEqual(_classify_voice_task(""), ("unspecified", None))

    def test_unspecified_for_whitespace_only(self) -> None:
        self.assertEqual(_classify_voice_task("   \n   "), ("unspecified", None))

    def test_unspecified_for_none(self) -> None:
        self.assertEqual(_classify_voice_task(None), ("unspecified", None))  # type: ignore[arg-type]

    def test_say_prefix_extracts_spoken_text(self) -> None:
        mode, text = _classify_voice_task("Say: Hello from Spark voice.")
        self.assertEqual(mode, "speak")
        self.assertEqual(text, "Hello from Spark voice.")

    def test_speak_prefix_extracts_spoken_text(self) -> None:
        mode, text = _classify_voice_task("Speak: The build is green.")
        self.assertEqual(mode, "speak")
        self.assertEqual(text, "The build is green.")

    def test_voice_prefix_extracts_spoken_text(self) -> None:
        mode, text = _classify_voice_task("voice: Welcome back, operator.")
        self.assertEqual(mode, "speak")
        self.assertEqual(text, "Welcome back, operator.")

    def test_read_this_prefix_extracts_spoken_text(self) -> None:
        mode, text = _classify_voice_task("Read this: the deploy completed.")
        self.assertEqual(mode, "speak")
        self.assertEqual(text, "the deploy completed.")

    def test_reply_with_voice_prefix_extracts_spoken_text(self) -> None:
        mode, text = _classify_voice_task("Reply with voice: Yes, on my way.")
        self.assertEqual(mode, "speak")
        self.assertEqual(text, "Yes, on my way.")

    def test_say_prefix_is_case_insensitive(self) -> None:
        mode, text = _classify_voice_task("SAY: Loud and clear.")
        self.assertEqual(mode, "speak")
        self.assertEqual(text, "Loud and clear.")

    def test_say_prefix_only_with_no_payload_is_unspecified(self) -> None:
        # No text after the colon means the regex's `.+` capture fails, so the
        # task falls through to the "unspecified" branch.
        self.assertEqual(_classify_voice_task("Say:"), ("unspecified", None))

    def test_transcribe_keyword_classifies_as_transcribe(self) -> None:
        self.assertEqual(
            _classify_voice_task("Please transcribe the voice memo I attached."),
            ("transcribe", None),
        )

    def test_transcription_keyword_classifies_as_transcribe(self) -> None:
        self.assertEqual(
            _classify_voice_task("I need a transcription of this audio."),
            ("transcribe", None),
        )

    def test_arbitrary_prose_without_signals_is_unspecified(self) -> None:
        self.assertEqual(
            _classify_voice_task("Tell me about the project status."),
            ("unspecified", None),
        )

    def test_say_prefix_preserves_internal_newlines(self) -> None:
        mode, text = _classify_voice_task("Say: line one\nline two")
        self.assertEqual(mode, "speak")
        self.assertEqual(text, "line one\nline two")


if __name__ == "__main__":
    unittest.main()
