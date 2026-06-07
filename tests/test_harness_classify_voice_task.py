from __future__ import annotations

import unittest

from spark_intelligence.harness_runtime.service import _classify_voice_task


class ClassifyVoiceTaskTests(unittest.TestCase):
    def test_say_prefix_extracts_spoken_text(self) -> None:
        mode, payload = _classify_voice_task("Say: Hello from Spark voice.")
        self.assertEqual(mode, "speak")
        self.assertEqual(payload, "Hello from Spark voice.")

    def test_speak_prefix_with_hyphen_separator_extracts_text(self) -> None:
        mode, payload = _classify_voice_task("Speak - The harness is ready.")
        self.assertEqual(mode, "speak")
        self.assertEqual(payload, "The harness is ready.")

    def test_read_this_prefix_extracts_text(self) -> None:
        mode, payload = _classify_voice_task("Read this: the live release plan.")
        self.assertEqual(mode, "speak")
        self.assertEqual(payload, "the live release plan.")

    def test_reply_with_voice_prefix_extracts_text(self) -> None:
        mode, payload = _classify_voice_task("Reply with voice: standby for handoff.")
        self.assertEqual(mode, "speak")
        self.assertEqual(payload, "standby for handoff.")

    def test_speak_prefix_without_payload_returns_none(self) -> None:
        mode, payload = _classify_voice_task("Say:    ")
        self.assertEqual(mode, "unspecified")
        self.assertIsNone(payload)

    def test_transcribe_keyword_returns_transcribe_mode(self) -> None:
        mode, payload = _classify_voice_task("Transcribe this audio clip.")
        self.assertEqual(mode, "transcribe")
        self.assertIsNone(payload)

    def test_transcription_keyword_returns_transcribe_mode(self) -> None:
        mode, payload = _classify_voice_task("Run a transcription pass on the call.")
        self.assertEqual(mode, "transcribe")
        self.assertIsNone(payload)

    def test_empty_task_returns_unspecified(self) -> None:
        mode, payload = _classify_voice_task("")
        self.assertEqual(mode, "unspecified")
        self.assertIsNone(payload)

    def test_unrelated_task_returns_unspecified(self) -> None:
        mode, payload = _classify_voice_task("Plan a builder review for tomorrow.")
        self.assertEqual(mode, "unspecified")
        self.assertIsNone(payload)


if __name__ == "__main__":
    unittest.main()
