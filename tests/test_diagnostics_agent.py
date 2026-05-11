from __future__ import annotations

import json

from spark_intelligence.diagnostics import (
    build_diagnostic_report,
    classify_log_entry,
    discover_log_sources,
    discover_service_checks,
    record_diagnostic_capability_events,
    render_diagnostic_markdown,
)
from spark_intelligence.diagnostics.agent import LogSource, parse_log_line
from spark_intelligence.self_awareness.agent_events import build_agent_black_box_report

from tests.test_support import SparkTestCase


class DiagnosticsAgentTests(SparkTestCase):
    def test_classifies_jsonl_failures_by_payload_component(self) -> None:
        source = LogSource(
            path=self.home / "logs" / "memory.jsonl",
            subsystem="memory",
            source_kind="jsonl",
        )
        entry = parse_log_line(
            source=source,
            line_number=1,
            raw=json.dumps(
                {
                    "created_at": "2026-04-25T12:00:00Z",
                    "event_type": "memory_read_abstained",
                    "component": "memory_orchestrator",
                    "status": "failed",
                    "reason": "sdk_unavailable",
                    "summary": "Spark memory read abstained.",
                }
            ),
        )

        classified = classify_log_entry(entry)

        self.assertIsNotNone(classified)
        assert classified is not None
        self.assertEqual(entry.subsystem, "memory")
        self.assertEqual(classified.failure_class, "memory_failure")
        self.assertEqual(classified.severity, "high")

    def test_build_report_detects_recurring_failures_and_writes_obsidian_markdown(self) -> None:
        logs_dir = self.home / "logs"
        logs_dir.mkdir(exist_ok=True)
        (logs_dir / "builder.log").write_text(
            "\n".join(
                [
                    "2026-04-25T12:00:00Z INFO gateway ready",
                    "2026-04-25T12:01:00Z ERROR dispatch failed: provider timeout after 30s",
                    "2026-04-25T12:02:00Z ERROR dispatch failed: provider timeout after 45s",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        (logs_dir / "semantic_retrieval.jsonl").write_text(
            json.dumps(
                {
                    "created_at": "2026-04-25T12:03:00Z",
                    "component": "memory_orchestrator",
                    "event_type": "memory_write_failed",
                    "status": "failed",
                    "error": {"message": "database is locked"},
                }
            )
            + "\n",
            encoding="utf-8",
        )

        report = build_diagnostic_report(
            self.config_manager,
            max_lines_per_file=50,
            write_markdown=True,
        )

        self.assertEqual(report.failure_line_count, 3)
        self.assertGreaterEqual(len(report.findings), 2)
        self.assertTrue(any(finding.recurring for finding in report.findings))
        self.assertIn("builder", report.counts_by_subsystem)
        self.assertIn("memory", report.counts_by_subsystem)
        self.assertIsNotNone(report.markdown_path)
        assert report.markdown_path is not None
        markdown = (self.home / "diagnostics" / "Spark Diagnostics.md").read_text(encoding="utf-8")
        self.assertIn("# Spark Diagnostic Report", markdown)
        self.assertIn("[[Builder]]", markdown)
        self.assertIn("## Recurring Bugs", markdown)

    def test_discover_log_sources_scopes_all_known_runtime_roots(self) -> None:
        (self.home / "logs").mkdir(exist_ok=True)
        (self.home / "logs" / "builder.log").write_text("ERROR builder failed\n", encoding="utf-8")
        researcher_root = self.home / "spark-researcher"
        (researcher_root / "logs").mkdir(parents=True)
        (researcher_root / "logs" / "researcher.log").write_text("ERROR bridge_error\n", encoding="utf-8")
        self.config_manager.set_path("spark.researcher.runtime_root", str(researcher_root))

        sources = discover_log_sources(self.config_manager)

        source_names = {source.path.name for source in sources}
        subsystems = {source.subsystem for source in sources}
        self.assertIn("builder.log", source_names)
        self.assertIn("researcher.log", source_names)
        self.assertIn("builder", subsystems)
        self.assertIn("researcher", subsystems)

    def test_service_checks_discover_connectors_and_render_markdown(self) -> None:
        telegram_root = self.home / "modules" / "spark-telegram-bot" / "source"
        telegram_root.mkdir(parents=True)
        (telegram_root / "package.json").write_text("{}", encoding="utf-8")
        memory_root = self.home / "modules" / "domain-chip-memory" / "source"
        memory_root.mkdir(parents=True)
        (memory_root / "pyproject.toml").write_text("[project]\nname='domain-chip-memory'\n", encoding="utf-8")
        researcher_root = self.home / "modules" / "spark-researcher" / "source"
        (researcher_root / "artifacts" / "traces").mkdir(parents=True)
        (researcher_root / "pyproject.toml").write_text("[project]\nname='spark-researcher'\n", encoding="utf-8")
        self.config_manager.set_path("spark.diagnostics.module_search_roots", [str(self.home / "modules")])
        self.config_manager.set_path("spark.researcher.runtime_root", str(researcher_root))

        checks = discover_service_checks(self.config_manager)
        by_service = {check.service: check for check in checks}

        self.assertEqual(by_service["spark_intelligence_home"].status, "ok")
        self.assertEqual(by_service["spark_telegram_bot_source"].status, "ok")
        self.assertEqual(by_service["domain_chip_memory_source"].status, "ok")
        self.assertEqual(by_service["spark_researcher_source"].status, "ok")
        self.assertEqual(by_service["spark_researcher_traces"].status, "ok")
        self.assertIn(by_service["spark_telegram_bot_spark_agi"].status, {"ok", "unreachable"})

        report = build_diagnostic_report(
            self.config_manager,
            max_lines_per_file=10,
            write_markdown=False,
        )
        markdown = render_diagnostic_markdown(report)

        self.assertTrue(report.service_checks)
        self.assertIn("service_checks", report.to_dict())
        self.assertIn("## Connector Health", markdown)
        self.assertIn("spark_telegram_bot_source", markdown)
        self.assertIn("domain_chip_memory_source", markdown)
        self.assertIn("spark_researcher_source", markdown)

    def test_service_checks_report_unavailable_optional_connectors(self) -> None:
        isolated_modules = self.home / "isolated-modules"
        isolated_modules.mkdir()
        self.config_manager.set_path("spark.diagnostics.module_search_roots", [str(isolated_modules)])

        checks = discover_service_checks(self.config_manager)
        by_service = {check.service: check for check in checks}

        self.assertEqual(by_service["spark_telegram_bot_source"].status, "missing")
        self.assertEqual(by_service["domain_chip_memory_source"].status, "missing")
        self.assertEqual(by_service["spark_researcher_source"].status, "missing")
        self.assertIn(by_service["spawner_ui_api"].status, {"ok", "unreachable"})

    def test_markdown_renderer_handles_empty_report(self) -> None:
        report = build_diagnostic_report(
            self.config_manager,
            max_lines_per_file=10,
            write_markdown=False,
        )

        markdown = render_diagnostic_markdown(report)

        self.assertIn("No failures found", markdown)
        self.assertIn("spark/passive-monitoring", markdown)

    def test_diagnostics_scan_cli_reports_and_writes_markdown(self) -> None:
        logs_dir = self.home / "logs"
        logs_dir.mkdir(exist_ok=True)
        (logs_dir / "researcher.log").write_text(
            "2026-04-25T12:00:00Z ERROR researcher bridge_error provider auth failed\n",
            encoding="utf-8",
        )

        exit_code, stdout, stderr = self.run_cli(
            "diagnostics",
            "scan",
            "--home",
            str(self.home),
        )

        self.assertEqual(exit_code, 0, stderr)
        self.assertIn("Spark diagnostics", stdout)
        self.assertIn("- findings: 1", stdout)
        self.assertTrue((self.home / "diagnostics" / "Spark Diagnostics.md").exists())

    def test_diagnostic_report_can_record_aoc_capability_events(self) -> None:
        memory_root = self.home / "modules" / "domain-chip-memory" / "source"
        memory_root.mkdir(parents=True)
        (memory_root / "pyproject.toml").write_text("[project]\nname='domain-chip-memory'\n", encoding="utf-8")
        self.config_manager.set_path("spark.diagnostics.module_search_roots", [str(self.home / "modules")])

        report = build_diagnostic_report(
            self.config_manager,
            max_lines_per_file=10,
            write_markdown=False,
        )
        event_ids = record_diagnostic_capability_events(
            self.state_db,
            report,
            request_id="req-diagnostics-aoc",
            actor_id="diagnostics_test",
        )

        black_box = build_agent_black_box_report(self.state_db, request_id="req-diagnostics-aoc").to_payload()
        by_route = {entry["route_chosen"]: entry for entry in black_box["entries"]}

        self.assertTrue(event_ids)
        self.assertIn("spark_intelligence_builder", by_route)
        self.assertIn("spark_memory", by_route)
        self.assertEqual(by_route["spark_memory"]["event_type"], "capability_probed")
        self.assertEqual(by_route["spark_memory"]["sources_used"][0]["source"], "current_diagnostics")
        self.assertEqual(by_route["spark_memory"]["sources_used"][0]["freshness"], "live_probed")

    def test_diagnostics_scan_cli_can_feed_aoc_black_box(self) -> None:
        exit_code, stdout, stderr = self.run_cli(
            "diagnostics",
            "scan",
            "--home",
            str(self.home),
            "--no-write",
            "--record-aoc-events",
            "--request-id",
            "req-diagnostics-cli",
            "--json",
        )

        self.assertEqual(exit_code, 0, stderr)
        payload = json.loads(stdout)
        self.assertTrue(payload["agent_event_ids"])

        exit_code, stdout, stderr = self.run_cli(
            "self",
            "black-box",
            "--home",
            str(self.home),
            "--request-id",
            "req-diagnostics-cli",
            "--json",
        )

        self.assertEqual(exit_code, 0, stderr)
        black_box = json.loads(stdout)
        self.assertGreaterEqual(black_box["counts"]["entries"], 1)
        self.assertTrue(any(entry["event_type"] == "capability_probed" for entry in black_box["entries"]))
