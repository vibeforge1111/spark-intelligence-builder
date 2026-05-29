import sqlite3
import pytest
from spark_intelligence.workflow_recovery.pending_tasks import latest_pending_tasks


@pytest.fixture
def state_db(tmp_path):
    db_path = tmp_path / "state.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE pending_tasks (
            id TEXT PRIMARY KEY,
            human_id TEXT,
            status TEXT,
            closed_at TEXT
        )
    """)
    conn.executemany("INSERT INTO pending_tasks VALUES (?,?,?,?)", [
        ("t1", "u1", "open", None),
        ("t2", "u1", "blocked", None),
        ("t3", "u1", "done", "2024-01-01"),
        ("t4", "u1", "open", "2024-01-02"),
    ])
    conn.commit()
    conn.close()
    return str(db_path)


def test_open_only_excludes_closed_at_set(state_db):
    results = latest_pending_tasks(state_db, open_only=True)
    ids = [r["id"] for r in results]
    assert "t3" not in ids
    assert "t4" not in ids


def test_open_only_with_status_still_excludes_closed(state_db):
    results = latest_pending_tasks(state_db, status="open", open_only=True)
    ids = [r["id"] for r in results]
    assert "t3" not in ids
    assert "t4" not in ids
    assert "t1" in ids


def test_open_only_false_includes_closed(state_db):
    results = latest_pending_tasks(state_db, open_only=False)
    ids = [r["id"] for r in results]
    assert "t3" in ids


def test_status_and_open_only_both_applied(state_db):
    results = latest_pending_tasks(state_db, status="open", open_only=True)
    for r in results:
        assert r["status"] == "open"
        assert r["closed_at"] is None


def test_blocked_with_open_only_excludes_closed_blocked(state_db):
    results = latest_pending_tasks(state_db, status="blocked", open_only=True)
    ids = [r["id"] for r in results]
    assert "t2" in ids
    assert "t3" not in ids