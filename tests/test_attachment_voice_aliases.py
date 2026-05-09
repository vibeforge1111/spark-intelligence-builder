from __future__ import annotations

from spark_intelligence.attachments.snapshot import build_attachment_context

from tests.test_support import SparkTestCase, create_fake_hook_chip


class AttachmentVoiceAliasTests(SparkTestCase):
    def test_build_attachment_context_hides_legacy_voice_chip_when_canonical_exists(self) -> None:
        legacy_root = create_fake_hook_chip(self.home, chip_key="domain-chip-voice-comms")
        canonical_root = create_fake_hook_chip(self.home, chip_key="spark-voice-comms")
        self.config_manager.set_path("spark.chips.roots", [str(legacy_root), str(canonical_root)])
        self.config_manager.set_path("spark.chips.active_keys", ["domain-chip-voice-comms", "spark-voice-comms"])

        context = build_attachment_context(self.config_manager)

        self.assertIn("spark-voice-comms", context["active_chip_keys"])
        self.assertIn("spark-voice-comms", context["attached_chip_keys"])
        self.assertNotIn("domain-chip-voice-comms", context["active_chip_keys"])
        self.assertNotIn("domain-chip-voice-comms", context["attached_chip_keys"])
