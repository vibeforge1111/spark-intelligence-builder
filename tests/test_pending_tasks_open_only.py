from __future__ import annotations

from pathlib import Path

import pytest

from spark_intelligence.state.db import StateDB
from spark_intelligence.workflow_recovery.pending_tasks import latest_pending_tasks


@pytest.fixture
def state_db(tmp_path: Path) -> StateDB:
    db = StateDB(tmp_path / "state.db")
    db.initialize()
    return db


def _insert_task(
    state_db: StateDB, task_key: str, status: str, *, closed_at: str | None = None
) -> None:
    """Direct SQL insert — lets tests set arbitrary closed_at for edge-case scenarios
    that the public API cannot produce (e.g. timed_out with closed_at set)."""
    with state_db.connect() as conn:
        conn.execute(
            """
            INSERT INTO pending_task_records(
                pending_task_id, task_key, status, original_request,
                created_at, updated_at, closed_at
            ) VALUES (?, ?, ?, ?, '2024-01-01T00:00:00', '2024-01-01T00:00:00', ?)
            """,
            (f"pt-{task_key}", task_key, status, f"test request: {task_key}", closed_at),
        )


# ---------------------------------------------------------------------------
# Unsafe-path / negative tests (maintainer-requested coverage)
# ---------------------------------------------------------------------------


def test_status_and_open_only_excludes_closed_task(state_db: StateDB) -> None:
    """THE BUG PATH: status=timed_out + open_only=True must still exclude a closed task.

    Before the fix, closed_at IS NULL lived inside elif open_only: and was skipped
    whenever status was truthy. A closed timed_out task was returned as actionable
    pending work in the recovery context.
    """
    _insert_task(state_db, "open-timed-out", "timed_out", closed_at=None)
    _insert_task(state_db, "closed-timed-out", "timed_out", closed_at="2024-01-02T00:00:00")

    results = latest_pending_tasks(state_db, status="timed_out", open_only=True)
    keys = {r.task_key for r in results}

    assert "closed-timed-out" not in keys, (
        "closed task with matching status must not appear when open_only=True"
    )
    assert "open-timed-out" in keys, "open timed_out task must still be returned"


def test_all_closed_matching_status_returns_empty(state_db: StateDB) -> None:
    """Edge: all tasks with the given status are closed — result must be empty, not leaked."""
    _insert_task(state_db, "closed-blocked-1", "blocked", closed_at="2024-01-02T00:00:00")
    _insert_task(state_db, "closed-blocked-2", "blocked", closed_at="2024-01-03T00:00:00")

    results = latest_pending_tasks(state_db, status="blocked", open_only=True)
    assert results == [], f"expected empty list, got {[r.task_key for r in results]}"


def test_closed_task_included_when_open_only_false(state_db: StateDB) -> None:
    """Baseline: open_only=False must NOT apply the closed_at filter — closed tasks
    with matching status are returned. Guards against over-filtering regression."""
    _insert_task(state_db, "closed-timed-out", "timed_out", closed_at="2024-01-02T00:00:00")

    results = latest_pending_tasks(state_db, status="timed_out", open_only=False)
    keys = {r.task_key for r in results}
    assert "closed-timed-out" in keys, "closed task must appear when open_only=False"


def test_open_only_without_status_excludes_closed(state_db: StateDB) -> None:
    """The existing caller path (no status, open_only=True) must still exclude closed tasks."""
    _insert_task(state_db, "open-task", "open", closed_at=None)
    _insert_task(state_db, "closed-task", "open", closed_at="2024-01-02T00:00:00")

    results = latest_pending_tasks(state_db, open_only=True)
    keys = {r.task_key for r in results}
    assert "open-task" in keys
    assert "closed-task" not in keys


def test_closed_interrupted_task_excluded(state_db: StateDB) -> None:
    """Edge: closed task with status=interrupted must not appear with open_only=True."""
    _insert_task(state_db, "closed-interrupted", "interrupted", closed_at="2024-01-05T00:00:00")
    _insert_task(state_db, "open-interrupted", "interrupted", closed_at=None)

    results = latest_pending_tasks(state_db, status="interrupted", open_only=True)
    keys = {r.task_key for r in results}
    assert "closed-interrupted" not in keys
    assert "open-interrupted" in keys


def test_combined_status_and_open_only_on_multiple_statuses(state_db: StateDB) -> None:
    """Negative: closed tasks across multiple statuses are all excluded when open_only=True.
    Proves the guard is not specific to a single status value."""
    for status in ("timed_out", "blocked", "interrupted"):
        _insert_task(state_db, f"closed-{status}", status, closed_at="2024-01-02T00:00:00")
        _insert_task(state_db, f"open-{status}", status, closed_at=None)

    for status in ("timed_out", "blocked", "interrupted"):
        results = latest_pending_tasks(state_db, status=status, open_only=True)
        keys = {r.task_key for r in results}
        assert f"closed-{status}" not in keys, f"closed {status} task must be excluded"
        assert f"open-{status}" in keys, f"open {status} task must be included"
