from __future__ import annotations

from spark_intelligence.self_awareness.operating_context import build_agent_operating_context
from spark_intelligence.self_awareness.operating_strip import (
    AGENT_OPERATING_STRIP_SCHEMA_VERSION,
    build_agent_operating_strip,
)

from tests.test_support import SparkTestCase


class AgentOperatingStripTests(SparkTestCase):
    def test_strip_renders_top_level_aoc_status(self) -> None:
        context = build_agent_operating_context(
            config_manager=self.config_manager,
            state_db=self.state_db,
            user_message="patch the mission memory loop",
            spark_access_level="4",
            runner_writable=False,
            runner_label="read-only chat runner",
        )

        strip = build_agent_operating_strip(context.to_payload())
        payload = strip.to_payload()
        text = strip.to_text()

        self.assertEqual(payload["schema_version"], AGENT_OPERATING_STRIP_SCHEMA_VERSION)
        self.assertEqual(payload["status"], "Ready with warnings")
        self.assertEqual(payload["best_route"], "writable Spawner/Codex mission")
        self.assertEqual(payload["access"], "Level 4 - sandboxed workspace allowed")
        self.assertEqual(payload["runner"], "read-only chat runner")
        self.assertIn("AOC: Ready with warnings", text)
        self.assertIn("Access: Level 4 - sandboxed workspace allowed", text)
