from __future__ import annotations

from spark_intelligence.harness_runtime import (
    build_harness_runtime_snapshot,
    build_harness_task_envelope,
    execute_harness_task,
)

from tests.test_support import SparkTestCase, create_fake_hook_chip


class HarnessRuntimeTests(SparkTestCase):
    def test_build_harness_task_envelope_uses_router_selection(self) -> None:
        create_fake_hook_chip(self.home, chip_key="spark-browser")
        self.config_manager.set_path("spark.chips.roots", [str(self.home)])
        self.config_manager.set_path("spark.chips.active_keys", ["spark-browser"])

        envelope = build_harness_task_envelope(
            config_manager=self.config_manager,
            state_db=self.state_db,
            task="Search https://example.com and inspect the page.",
            session_id="session-1",
            human_id="human-1",
            agent_id="agent-1",
        )

        self.assertEqual(envelope.harness_id, "browser.grounded")
        self.assertEqual(envelope.backend_kind, "browser_bridge")
        self.assertEqual(envelope.session_id, "session-1")

    def test_execute_builder_direct_harness_records_runtime_run(self) -> None:
        envelope = build_harness_task_envelope(
            config_manager=self.config_manager,
            state_db=self.state_db,
            task="What chips are active right now?",
        )

        result = execute_harness_task(
            config_manager=self.config_manager,
            state_db=self.state_db,
            envelope=envelope,
        )

        self.assertEqual(result.status, "prepared")
        self.assertEqual(result.envelope.harness_id, "builder.direct")
        self.assertIn("execution_contract", result.artifacts)

        snapshot = build_harness_runtime_snapshot(self.config_manager, self.state_db)
        self.assertEqual(snapshot.summary["recent_run_count"], 1)
        self.assertEqual(snapshot.summary["last_harness_id"], "builder.direct")

    def test_execute_browser_grounded_harness_prepares_navigate_payload_for_url(self) -> None:
        create_fake_hook_chip(self.home, chip_key="spark-browser")
        self.config_manager.set_path("spark.chips.roots", [str(self.home)])
        self.config_manager.set_path("spark.chips.active_keys", ["spark-browser"])
        envelope = build_harness_task_envelope(
            config_manager=self.config_manager,
            state_db=self.state_db,
            task="Open https://example.com and inspect it.",
        )

        result = execute_harness_task(
            config_manager=self.config_manager,
            state_db=self.state_db,
            envelope=envelope,
        )

        self.assertEqual(result.status, "prepared")
        payload = result.artifacts.get("browser_navigate_payload") or {}
        self.assertEqual(payload.get("hook_name"), "browser.navigate")
        self.assertEqual((payload.get("arguments") or {}).get("url"), "https://example.com")

    def test_execute_browser_grounded_harness_requires_url_for_first_runner(self) -> None:
        create_fake_hook_chip(self.home, chip_key="spark-browser")
        self.config_manager.set_path("spark.chips.roots", [str(self.home)])
        self.config_manager.set_path("spark.chips.active_keys", ["spark-browser"])
        envelope = build_harness_task_envelope(
            config_manager=self.config_manager,
            state_db=self.state_db,
            task="Search the web for Spark architecture.",
        )

        result = execute_harness_task(
            config_manager=self.config_manager,
            state_db=self.state_db,
            envelope=envelope,
        )

        self.assertEqual(result.status, "needs_input")
        self.assertIn("browser_status_payload", result.artifacts)
        self.assertIn("needs_input", result.artifacts)
