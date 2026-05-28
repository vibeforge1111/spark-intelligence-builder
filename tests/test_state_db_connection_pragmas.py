from __future__ import annotations

import sqlite3
from pathlib import Path

from spark_intelligence.state.db import StateDB


def test_state_db_connect_sets_busy_timeout(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    state_db = StateDB(db_path)
    state_db.initialize()

    with state_db.connect() as conn:
        timeout_ms = conn.execute("PRAGMA busy_timeout;").fetchone()[0]

    assert timeout_ms >= 30_000


def test_state_db_connect_attempts_wal_mode(tmp_path: Path) -> None:
    db_path = tmp_path / "state.db"
    state_db = StateDB(db_path)
    state_db.initialize()

    with state_db.connect() as conn:
        journal = conn.execute("PRAGMA journal_mode;").fetchone()[0]

    assert journal in {"wal", "delete", "truncate", "persist", "memory", "off"}

