from __future__ import annotations

from spark_intelligence.self_awareness.operating_panel import build_agent_operating_panel

from tests.test_support import SparkTestCase


class AgentSourceLedgerTests(SparkTestCase):
    def test_panel_source_ledger_labels_freshness(self) -> None:
        panel = build_agent_operating_panel(
            config_manager=self.config_manager,
            state_db=self.state_db,
            user_message="patch the mission memory loop",
            spark_access_level="4",
            runner_writable=False,
            runner_label="read-only chat runner",
        )

        ledger = panel.to_payload()["source_ledger"]
        by_source = {item["source"]: item for item in ledger["items"]}

        self.assertEqual(ledger["schema_version"], "spark.agent_source_ledger.v1")
        self.assertEqual(by_source["current_user_message"]["freshness"], "fresh")
        self.assertEqual(by_source["operator_supplied_access"]["freshness"], "fresh")
        self.assertEqual(by_source["runner_preflight"]["freshness"], "live_probed")
        self.assertEqual(by_source["stale_context_sweep"]["freshness"], "fresh")
        self.assertIn("latest user turn win", ledger["source_policy"])
