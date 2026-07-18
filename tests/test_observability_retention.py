import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from spark_intelligence.jobs.service import jobs_tick
from spark_intelligence.observability.store import prune_observability_store, record_event
from spark_intelligence.state.db import StateDB
from tests.test_support import SparkTestCase


def _age_event(state_db: StateDB, event_id: str, timestamp: str) -> None:
    with state_db.connect() as conn:
        conn.execute("UPDATE builder_events SET created_at = ? WHERE event_id = ?", (timestamp, event_id))
        conn.execute("UPDATE event_log SET recorded_at = ? WHERE event_id = ?", (timestamp, event_id))
        conn.commit()


def test_retention_preview_only_targets_recoverable_event_log_mirrors(tmp_path) -> None:
    state_db = StateDB(tmp_path / "state.sqlite")
    state_db.initialize()
    old_event_id = record_event(state_db, event_type="retention_probe", component="test", summary="Old event")
    current_event_id = record_event(state_db, event_type="retention_probe", component="test", summary="Current event")
    _age_event(state_db, old_event_id, "2025-01-01T00:00:00+00:00")
    _age_event(state_db, current_event_id, "2026-01-03T00:00:00+00:00")
    with state_db.connect() as conn:
        conn.execute(
            """
            INSERT INTO event_log(event_id, event_type, recorded_at, payload_json)
            VALUES ('orphan:old', 'legacy_orphan', '2025-01-01T00:00:00+00:00', '{}')
            """
        )
        conn.commit()

    result = prune_observability_store(state_db, older_than="2026-01-02T00:00:00+00:00")

    assert result.mode == "preview"
    assert result.eligible_counts == {"event_log": 1}
    assert result.deleted_counts == {"event_log": 0}
    assert result.protected_tables == ("builder_events", "provider_runtime_events")
    assert result.plan_sha256
    assert result.backup_path is None
    with state_db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM event_log WHERE event_id = ?", (old_event_id,)).fetchone()[0] == 1
        assert conn.execute("SELECT COUNT(*) FROM event_log WHERE event_id = 'orphan:old'").fetchone()[0] == 1


def test_retention_rejects_naive_or_future_cutoffs(tmp_path) -> None:
    state_db = StateDB(tmp_path / "state.sqlite")
    state_db.initialize()

    with pytest.raises(ValueError, match="timezone"):
        prune_observability_store(state_db, older_than="2025-01-01T00:00:00")
    with pytest.raises(ValueError, match="future"):
        prune_observability_store(state_db, older_than=datetime.now(UTC) + timedelta(days=1))


def test_retention_apply_requires_exact_preview_and_keeps_verified_backup(tmp_path) -> None:
    state_db = StateDB(tmp_path / "state.sqlite")
    state_db.initialize()
    old_event_id = record_event(state_db, event_type="retention_probe", component="test", summary="Old event")
    _age_event(state_db, old_event_id, "2025-01-01T00:00:00+00:00")
    cutoff = "2026-01-02T00:00:00+00:00"
    preview = prune_observability_store(state_db, older_than=cutoff)

    with pytest.raises(ValueError, match="preview digest"):
        prune_observability_store(
            state_db,
            older_than=cutoff,
            apply=True,
            confirm_plan_sha256="wrong",
            backup_dir=tmp_path / "backups",
        )

    applied = prune_observability_store(
        state_db,
        older_than=cutoff,
        apply=True,
        confirm_plan_sha256=preview.plan_sha256,
        backup_dir=tmp_path / "backups",
    )

    assert applied.mode == "applied"
    assert applied.deleted_counts == {"event_log": 1}
    assert applied.backup_path is not None
    assert applied.backup_sha256
    assert applied.recovery_verified is True
    with state_db.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM event_log WHERE event_id = ?", (old_event_id,)).fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM builder_events WHERE event_id = ?", (old_event_id,)).fetchone()[0] == 1
    backup = StateDB(applied.backup_path)
    with backup.connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM event_log WHERE event_id = ?", (old_event_id,)).fetchone()[0] == 1


class ObservabilityRetentionCliTests(SparkTestCase):
    def test_jobs_tick_observes_retention_without_deleting(self) -> None:
        old_event_id = record_event(
            self.state_db,
            event_type="retention_job_probe",
            component="test",
            summary="Old scheduled preview mirror",
        )
        _age_event(self.state_db, old_event_id, "2025-01-01T00:00:00+00:00")
        fake_memory = SimpleNamespace(
            status="succeeded",
            reason=None,
            maintenance={
                "manual_observations_before": 0,
                "manual_observations_after": 0,
                "active_deletion_count": 0,
                "active_state_still_current_count": 0,
                "active_state_stale_preserved_count": 0,
                "active_state_superseded_count": 0,
                "active_state_archived_count": 0,
            },
        )

        with patch(
            "spark_intelligence.jobs.service.run_oauth_refresh_maintenance",
            return_value={"scanned": 0, "due": 0, "refreshed": [], "failed": [], "skipped": []},
        ), patch(
            "spark_intelligence.jobs.service.run_memory_sdk_maintenance",
            return_value=fake_memory,
        ):
            output = jobs_tick(self.config_manager, self.state_db)

        self.assertIn("observability:retention-preview", output)
        self.assertIn("mode=preview", output)
        with self.state_db.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM event_log WHERE event_id = ?", (old_event_id,)).fetchone()[0], 1)
            job = conn.execute(
                "SELECT last_result FROM job_records WHERE job_id = 'observability:retention-preview'"
            ).fetchone()
        self.assertIsNotNone(job)
        self.assertIn("mode=preview", job["last_result"])

    def test_jobs_prune_observability_defaults_to_preview(self) -> None:
        old_event_id = record_event(
            self.state_db,
            event_type="retention_cli_probe",
            component="test",
            summary="Old CLI mirror",
        )
        _age_event(self.state_db, old_event_id, "2025-01-01T00:00:00+00:00")

        exit_code, stdout, stderr = self.run_cli(
            "jobs",
            "prune-observability",
            "--home",
            str(self.home),
            "--older-than",
            "2026-01-01T00:00:00+00:00",
            "--json",
        )

        self.assertEqual(exit_code, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["mode"], "preview")
        self.assertEqual(payload["eligible_counts"], {"event_log": 1})
        self.assertIn("plan_sha256", payload)
        with self.state_db.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM event_log WHERE event_id = ?", (old_event_id,)).fetchone()[0], 1)
