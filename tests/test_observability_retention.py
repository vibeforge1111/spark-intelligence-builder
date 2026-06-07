import json
from datetime import UTC, datetime, timedelta

from spark_intelligence.observability.store import prune_observability_store, record_event
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
