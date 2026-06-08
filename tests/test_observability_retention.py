import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from spark_intelligence.gateway.tracing import (
    outbound_log_path,
    prune_gateway_logs,
    read_gateway_traces,
    rotate_gateway_logs_if_oversized,
    trace_log_path,
)
from spark_intelligence.observability.store import build_observability_store_report, prune_observability_store, record_event
from spark_intelligence.state.db import StateDB
from tests.test_support import SparkTestCase


def test_state_db_connections_apply_write_pragmas(tmp_path) -> None:
    state_db = StateDB(tmp_path / "state.sqlite")
    state_db.initialize()

    with state_db.connect() as conn:
        synchronous = int(conn.execute("PRAGMA synchronous").fetchone()[0])
        busy_timeout = int(conn.execute("PRAGMA busy_timeout").fetchone()[0])

    assert synchronous == 1
    assert busy_timeout == 5000


def test_prune_observability_store_prunes_mirrors_and_ledgers_by_default(tmp_path) -> None:
    state_db = StateDB(tmp_path / "state.sqlite")
    state_db.initialize()
    old_timestamp = "2025-01-01T00:00:00+00:00"
    current_timestamp = "2026-01-03T00:00:00+00:00"
    cutoff = "2026-01-02T00:00:00+00:00"

    old_event_id = record_event(
        state_db,
        event_type="retention_probe",
        component="test",
        summary="Old event.",
    )
    current_event_id = record_event(
        state_db,
        event_type="retention_probe",
        component="test",
        summary="Current event.",
    )
    with state_db.connect() as conn:
        conn.execute("UPDATE builder_events SET created_at = ? WHERE event_id = ?", (old_timestamp, old_event_id))
        conn.execute("UPDATE event_log SET recorded_at = ? WHERE event_id = ?", (old_timestamp, old_event_id))
        conn.execute("UPDATE builder_events SET created_at = ? WHERE event_id = ?", (current_timestamp, current_event_id))
        conn.execute("UPDATE event_log SET recorded_at = ? WHERE event_id = ?", (current_timestamp, current_event_id))
        conn.execute(
            """
            INSERT INTO tool_call_ledger(ledger_id, turn_id, status, ledger_json, created_at, updated_at)
            VALUES ('ledger:old', 'turn:old', 'success', '{}', ?, ?)
            """,
            (old_timestamp, old_timestamp),
        )
        conn.execute(
            """
            INSERT INTO tool_call_ledger(ledger_id, turn_id, status, ledger_json, created_at, updated_at)
            VALUES ('ledger:current', 'turn:current', 'success', '{}', ?, ?)
            """,
            (current_timestamp, current_timestamp),
        )
        conn.execute(
            """
            INSERT INTO provider_runtime_events(provider_id, event_kind, detail, created_at)
            VALUES ('provider-old', 'probe', 'old', ?)
            """,
            (old_timestamp,),
        )
        conn.commit()

    result = prune_observability_store(state_db, older_than=cutoff)

    assert result.deleted_counts == {
        "event_log": 1,
        "tool_call_ledger": 1,
        "provider_runtime_events": 1,
    }
    assert result.total_deleted == 3
    with state_db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM builder_events WHERE event_id = ?", (old_event_id,)).fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM event_log WHERE event_id = ?", (old_event_id,)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM event_log WHERE event_id = ?", (current_event_id,)).fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM tool_call_ledger WHERE ledger_id = 'ledger:old'").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM tool_call_ledger WHERE ledger_id = 'ledger:current'").fetchone()[0] == 1


def test_observability_store_report_counts_prunable_rows_without_deleting(tmp_path) -> None:
    state_db = StateDB(tmp_path / "state.sqlite")
    state_db.initialize()
    old_timestamp = "2025-01-01T00:00:00+00:00"
    current_timestamp = "2026-01-03T00:00:00+00:00"
    cutoff = "2026-01-02T00:00:00+00:00"

    old_event_id = record_event(
        state_db,
        event_type="retention_report_probe",
        component="test",
        summary="Old report probe.",
    )
    current_event_id = record_event(
        state_db,
        event_type="retention_report_probe",
        component="test",
        summary="Current report probe.",
    )
    with state_db.connect() as conn:
        conn.execute("UPDATE builder_events SET created_at = ? WHERE event_id = ?", (old_timestamp, old_event_id))
        conn.execute("UPDATE event_log SET recorded_at = ? WHERE event_id = ?", (old_timestamp, old_event_id))
        conn.execute("UPDATE builder_events SET created_at = ? WHERE event_id = ?", (current_timestamp, current_event_id))
        conn.execute("UPDATE event_log SET recorded_at = ? WHERE event_id = ?", (current_timestamp, current_event_id))
        conn.execute(
            """
            INSERT INTO tool_call_ledger(ledger_id, turn_id, status, ledger_json, created_at, updated_at)
            VALUES ('ledger:report-old', 'turn:report-old', 'success', '{}', ?, ?)
            """,
            (old_timestamp, old_timestamp),
        )
        conn.execute(
            """
            INSERT INTO tool_call_ledger(ledger_id, turn_id, status, ledger_json, created_at, updated_at)
            VALUES ('ledger:report-current', 'turn:report-current', 'success', '{}', ?, ?)
            """,
            (current_timestamp, current_timestamp),
        )
        conn.commit()

    report = build_observability_store_report(state_db, older_than=cutoff)

    assert report.cutoff == cutoff
    assert report.state_db_bytes > 0
    assert report.table_counts["event_log"] == 2
    assert report.table_counts["builder_events"] == 2
    assert report.table_counts["tool_call_ledger"] == 2
    assert report.prunable_counts == {
        "event_log": 1,
        "tool_call_ledger": 1,
        "provider_runtime_events": 0,
    }
    assert report.total_prunable == 2
    with state_db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM event_log").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM tool_call_ledger").fetchone()[0] == 2


def test_prune_gateway_logs_prunes_old_trace_and_outbound_records(tmp_path) -> None:
    from spark_intelligence.config.loader import ConfigManager

    home = tmp_path / "home"
    config_manager = ConfigManager.from_home(str(home))
    config_manager.bootstrap()
    trace_path = trace_log_path(config_manager)
    outbound_path = outbound_log_path(config_manager)
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    trace_path.write_text(
        "\n".join(
            [
                json.dumps({"recorded_at": "2025-01-01T00:00:00+00:00", "event": "old"}),
                json.dumps({"recorded_at": "2026-01-03T00:00:00+00:00", "event": "current"}),
                "not-json",
                "",
            ]
        ),
        encoding="utf-8",
    )
    outbound_path.write_text(
        "\n".join(
            [
                json.dumps({"recorded_at": "2025-01-01T00:00:00+00:00", "event": "old_outbound"}),
                json.dumps({"recorded_at": "2026-01-03T00:00:00+00:00", "event": "current_outbound"}),
                "",
            ]
        ),
        encoding="utf-8",
    )

    result = prune_gateway_logs(config_manager, older_than="2026-01-02T00:00:00+00:00")

    assert result.deleted_counts == {"gateway_trace": 1, "gateway_outbound": 1}
    assert result.total_deleted == 2
    assert [record["event"] for record in read_gateway_traces(config_manager, limit=10)] == ["current"]
    assert "not-json" in trace_path.read_text(encoding="utf-8")


def test_rotate_gateway_logs_if_oversized_keeps_bounded_backups(tmp_path) -> None:
    from spark_intelligence.config.loader import ConfigManager

    home = tmp_path / "home"
    config_manager = ConfigManager.from_home(str(home))
    config_manager.bootstrap()
    trace_path = trace_log_path(config_manager)
    trace_path.parent.mkdir(parents=True, exist_ok=True)
    trace_path.write_text("x" * 20, encoding="utf-8")

    rotated = rotate_gateway_logs_if_oversized(config_manager, max_bytes=10, backups=2)

    assert rotated["gateway_trace"] is True
    assert (trace_path.with_name(f"{trace_path.name}.1")).read_text(encoding="utf-8") == "x" * 20
    assert not trace_path.exists()


def test_prune_observability_store_can_explicitly_prune_builder_events(tmp_path) -> None:
    state_db = StateDB(tmp_path / "state.sqlite")
    state_db.initialize()
    old_timestamp = datetime(2025, 1, 1, tzinfo=UTC)
    cutoff = old_timestamp + timedelta(days=1)
    old_event_id = record_event(
        state_db,
        event_type="retention_probe",
        component="test",
        summary="Old event.",
    )
    with state_db.connect() as conn:
        conn.execute(
            "UPDATE builder_events SET created_at = ? WHERE event_id = ?",
            (old_timestamp.isoformat(timespec="seconds"), old_event_id),
        )
        conn.execute(
            "UPDATE event_log SET recorded_at = ? WHERE event_id = ?",
            (old_timestamp.isoformat(timespec="seconds"), old_event_id),
        )
        conn.commit()

    result = prune_observability_store(state_db, older_than=cutoff, include_builder_events=True)

    assert result.deleted_counts["builder_events"] == 1
    with state_db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM builder_events WHERE event_id = ?", (old_event_id,)).fetchone()[0] == 0


class ObservabilityRetentionCliTests(SparkTestCase):
    def test_jobs_observability_report_cli_reports_counts_without_pruning(self) -> None:
        old_timestamp = "2025-01-01T00:00:00+00:00"
        current_timestamp = "2026-01-03T00:00:00+00:00"
        cutoff = "2026-01-02T00:00:00+00:00"
        old_event_id = record_event(
            self.state_db,
            event_type="retention_report_cli_probe",
            component="test",
            summary="Old CLI report probe.",
        )
        with self.state_db.connect() as conn:
            conn.execute("UPDATE event_log SET recorded_at = ? WHERE event_id = ?", (old_timestamp, old_event_id))
            conn.execute("UPDATE builder_events SET created_at = ? WHERE event_id = ?", (old_timestamp, old_event_id))
            conn.execute(
                """
                INSERT INTO tool_call_ledger(ledger_id, turn_id, status, ledger_json, created_at, updated_at)
                VALUES ('ledger:retention-report-cli-old', 'turn:retention-report-cli-old', 'success', '{}', ?, ?)
                """,
                (old_timestamp, old_timestamp),
            )
            conn.commit()
        trace_path = trace_log_path(self.config_manager)
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        trace_path.write_text(
            "\n".join(
                [
                    json.dumps({"recorded_at": old_timestamp, "event": "old_gateway"}),
                    json.dumps({"recorded_at": current_timestamp, "event": "current_gateway"}),
                    "not-json",
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        exit_code, stdout, stderr = self.run_cli(
            "jobs",
            "observability-report",
            "--home",
            str(self.home),
            "--older-than",
            cutoff,
            "--include-gateway-logs",
            "--json",
        )

        self.assertEqual(exit_code, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["state_db"]["cutoff"], cutoff)
        self.assertEqual(payload["state_db"]["prunable_counts"]["event_log"], 1)
        self.assertEqual(payload["state_db"]["prunable_counts"]["tool_call_ledger"], 1)
        self.assertEqual(payload["gateway_logs"]["logs"]["gateway_trace"]["records"], 2)
        self.assertEqual(payload["gateway_logs"]["logs"]["gateway_trace"]["old_records"], 1)
        self.assertEqual(payload["gateway_logs"]["logs"]["gateway_trace"]["invalid_records"], 1)
        self.assertEqual(len(read_gateway_traces(self.config_manager, limit=10)), 2)
        with self.state_db.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM event_log WHERE event_id = ?", (old_event_id,)).fetchone()[0], 1)
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM tool_call_ledger WHERE ledger_id = 'ledger:retention-report-cli-old'").fetchone()[0],
                1,
            )

    def test_jobs_observability_report_cli_reports_unowned_jsonl_without_opening(self) -> None:
        spark_root = self.home / "fake-spark-root"
        (spark_root / "recursion").mkdir(parents=True)
        (spark_root / "state" / "spark-intelligence" / "logs").mkdir(parents=True)
        (spark_root / "outcomes.jsonl").write_text("{}\n", encoding="utf-8")
        (spark_root / "recursion" / "mutations.jsonl").write_text("x" * 25, encoding="utf-8")
        gateway_log = spark_root / "state" / "spark-intelligence" / "logs" / "gateway-trace.jsonl"
        gateway_log.write_text("owned\n", encoding="utf-8")

        exit_code, stdout, stderr = self.run_cli(
            "jobs",
            "observability-report",
            "--home",
            str(spark_root / "state" / "spark-intelligence"),
            "--include-unowned-jsonl",
            "--spark-root",
            str(spark_root),
            "--jsonl-limit",
            "10",
            "--json",
        )

        self.assertEqual(exit_code, 0, stderr)
        payload = json.loads(stdout)
        report = payload["unowned_jsonl"]
        by_relative_path = {item["relative_path"]: item for item in report["reported_files"]}
        self.assertEqual(report["total_files"], 3)
        self.assertEqual(report["candidate_files"], 3)
        self.assertEqual(report["below_min_bytes_files"], 0)
        self.assertEqual(
            report["candidate_manifest_action_counts"],
            {
                "archive_candidate": 1,
                "canonical_retention_path": 1,
                "freeze_pending_reference_scan": 1,
            },
        )
        self.assertEqual(
            report["candidate_movement_blocker_counts"],
            {
                "none": 1,
                "owned_by_gateway_retention": 1,
                "reference_scan_or_owner_signoff_required": 1,
            },
        )
        self.assertIn("outcomes.jsonl", by_relative_path)
        self.assertEqual(by_relative_path["outcomes.jsonl"]["classification"], "root_unowned_jsonl")
        self.assertEqual(by_relative_path["outcomes.jsonl"]["manifest_action"], "freeze_pending_reference_scan")
        self.assertEqual(
            by_relative_path["outcomes.jsonl"]["movement_blocker"],
            "reference_scan_or_owner_signoff_required",
        )
        self.assertTrue(by_relative_path["outcomes.jsonl"]["requires_reference_scan"])
        self.assertFalse(by_relative_path["outcomes.jsonl"]["delete_allowed"])
        self.assertEqual(by_relative_path[str(Path("recursion") / "mutations.jsonl")]["classification"], "legacy_runtime_river")
        self.assertEqual(
            by_relative_path[str(Path("recursion") / "mutations.jsonl")]["manifest_action"],
            "archive_candidate",
        )
        self.assertTrue(
            by_relative_path[str(Path("recursion") / "mutations.jsonl")]["archive_before_quarantine"]
        )
        self.assertEqual(
            by_relative_path[str(Path("state") / "spark-intelligence" / "logs" / "gateway-trace.jsonl")]["classification"],
            "builder_gateway_log",
        )
        self.assertEqual(
            by_relative_path[str(Path("state") / "spark-intelligence" / "logs" / "gateway-trace.jsonl")][
                "manifest_action"
            ],
            "canonical_retention_path",
        )
        self.assertEqual(
            by_relative_path[str(Path("state") / "spark-intelligence" / "logs" / "gateway-trace.jsonl")][
                "movement_blocker"
            ],
            "owned_by_gateway_retention",
        )
        self.assertEqual(gateway_log.read_text(encoding="utf-8"), "owned\n")

        exit_code, stdout, stderr = self.run_cli(
            "jobs",
            "observability-report",
            "--home",
            str(spark_root / "state" / "spark-intelligence"),
            "--include-unowned-jsonl",
            "--spark-root",
            str(spark_root),
            "--jsonl-min-bytes",
            "10",
            "--json",
        )

        self.assertEqual(exit_code, 0, stderr)
        filtered_report = json.loads(stdout)["unowned_jsonl"]
        self.assertEqual(filtered_report["total_files"], 3)
        self.assertEqual(filtered_report["candidate_files"], 1)
        self.assertEqual(filtered_report["below_min_bytes_files"], 2)
        self.assertEqual(filtered_report["candidate_manifest_action_counts"], {"archive_candidate": 1})

    def test_jobs_observability_report_cli_reference_scans_non_jsonl_text(self) -> None:
        spark_root = self.home / "fake-spark-root"
        reference_root = self.home / "reference-root"
        (spark_root / "recursion").mkdir(parents=True)
        (reference_root / "src").mkdir(parents=True)
        (reference_root / "config").mkdir(parents=True)
        (spark_root / "outcomes.jsonl").write_text("jsonl-content\n", encoding="utf-8")
        (spark_root / "recursion" / "mutations.jsonl").write_text("legacy-content\n", encoding="utf-8")
        (reference_root / "src" / "reader.py").write_text(
            'open("outcomes.jsonl", encoding="utf-8")\n',
            encoding="utf-8",
        )
        (reference_root / "config" / "runtime.toml").write_text(
            'legacy = "recursion/mutations.jsonl"\n',
            encoding="utf-8",
        )

        exit_code, stdout, stderr = self.run_cli(
            "jobs",
            "observability-report",
            "--home",
            str(spark_root / "state" / "spark-intelligence"),
            "--include-unowned-jsonl",
            "--spark-root",
            str(spark_root),
            "--jsonl-reference-scan",
            "--jsonl-reference-root",
            str(reference_root),
            "--jsonl-reference-root",
            str(reference_root / "src"),
            "--json",
        )

        self.assertEqual(exit_code, 0, stderr)
        report = json.loads(stdout)["unowned_jsonl"]
        by_relative_path = {item["relative_path"]: item for item in report["reported_files"]}
        self.assertTrue(report["reference_scan_enabled"])
        self.assertEqual(report["reference_scan_roots"], [str(reference_root)])
        self.assertEqual(report["candidate_reference_scan_status_counts"], {"matches_found": 2})
        self.assertEqual(by_relative_path["outcomes.jsonl"]["reference_scan"]["status"], "matches_found")
        self.assertEqual(by_relative_path["outcomes.jsonl"]["reference_scan"]["match_count"], 1)
        self.assertIn("outcomes.jsonl", by_relative_path["outcomes.jsonl"]["reference_scan"]["patterns"])
        legacy_path = str(Path("recursion") / "mutations.jsonl")
        self.assertEqual(by_relative_path[legacy_path]["reference_scan"]["status"], "matches_found")
        self.assertEqual(by_relative_path[legacy_path]["reference_scan"]["match_count"], 1)
        self.assertEqual((spark_root / "outcomes.jsonl").read_text(encoding="utf-8"), "jsonl-content\n")
        self.assertEqual((spark_root / "recursion" / "mutations.jsonl").read_text(encoding="utf-8"), "legacy-content\n")

    def test_jobs_prune_observability_cli_can_prune_gateway_logs(self) -> None:
        old_timestamp = "2025-01-01T00:00:00+00:00"
        cutoff = "2026-01-01T00:00:00+00:00"
        trace_path = trace_log_path(self.config_manager)
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        trace_path.write_text(
            json.dumps({"recorded_at": old_timestamp, "event": "old_gateway"}) + "\n",
            encoding="utf-8",
        )

        exit_code, stdout, stderr = self.run_cli(
            "jobs",
            "prune-observability",
            "--home",
            str(self.home),
            "--older-than",
            cutoff,
            "--include-gateway-logs",
            "--json",
        )

        self.assertEqual(exit_code, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["gateway_logs"]["deleted_counts"]["gateway_trace"], 1)
        self.assertEqual(payload["gateway_logs"]["total_deleted"], 1)
        self.assertEqual(read_gateway_traces(self.config_manager, limit=10), [])

    def test_jobs_prune_observability_cli_prunes_old_rows(self) -> None:
        old_timestamp = "2025-01-01T00:00:00+00:00"
        cutoff = "2026-01-01T00:00:00+00:00"
        old_event_id = record_event(
            self.state_db,
            event_type="retention_cli_probe",
            component="test",
            summary="Old CLI prune probe.",
        )
        with self.state_db.connect() as conn:
            conn.execute("UPDATE event_log SET recorded_at = ? WHERE event_id = ?", (old_timestamp, old_event_id))
            conn.execute("UPDATE builder_events SET created_at = ? WHERE event_id = ?", (old_timestamp, old_event_id))
            conn.execute(
                """
                INSERT INTO tool_call_ledger(ledger_id, turn_id, status, ledger_json, created_at, updated_at)
                VALUES ('ledger:retention-cli-old', 'turn:retention-cli-old', 'success', '{}', ?, ?)
                """,
                (old_timestamp, old_timestamp),
            )
            conn.commit()

        exit_code, stdout, stderr = self.run_cli(
            "jobs",
            "prune-observability",
            "--home",
            str(self.home),
            "--older-than",
            cutoff,
            "--json",
        )

        self.assertEqual(exit_code, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["cutoff"], cutoff)
        self.assertEqual(payload["deleted_counts"]["event_log"], 1)
        self.assertEqual(payload["deleted_counts"]["tool_call_ledger"], 1)
        self.assertEqual(payload["total_deleted"], 2)
        with self.state_db.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM event_log WHERE event_id = ?", (old_event_id,)).fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM builder_events WHERE event_id = ?", (old_event_id,)).fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM tool_call_ledger WHERE ledger_id = 'ledger:retention-cli-old'").fetchone()[0], 0)
