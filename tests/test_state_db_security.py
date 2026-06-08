import sqlite3

import pytest

from tests.test_support import SparkTestCase
from spark_intelligence.identity.service import resolve_inbound_dm
from spark_intelligence.state.db import StateDB


def test_ensure_column_rejects_unsafe_identifiers() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE safe_table (id TEXT PRIMARY KEY)")

    with pytest.raises(ValueError):
        StateDB._ensure_column(conn, "safe_table; DROP TABLE safe_table; --", "new_column", "TEXT")

    with pytest.raises(ValueError):
        StateDB._ensure_column(conn, "safe_table", "new_column; DROP TABLE safe_table; --", "TEXT")

    rows = conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'safe_table'").fetchall()
    assert len(rows) == 1


def test_ensure_column_adds_allowed_identifier() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE safe_table (id TEXT PRIMARY KEY)")

    StateDB._ensure_column(conn, "safe_table", "new_column", "TEXT")

    columns = {str(row["name"]) for row in conn.execute('PRAGMA table_info("safe_table")').fetchall()}
    assert "new_column" in columns


def test_initialize_repairs_turn_id_columns_before_creating_indexes(tmp_path) -> None:
    db_path = tmp_path / "state.sqlite"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            """
            CREATE TABLE builder_events (
                event_id TEXT PRIMARY KEY,
                event_type TEXT NOT NULL,
                run_id TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE event_log (
                event_id TEXT PRIMARY KEY,
                event_type TEXT NOT NULL,
                recorded_at TEXT NOT NULL,
                trace_ref TEXT,
                run_id TEXT,
                session_id TEXT
            )
            """
        )
        conn.commit()
    finally:
        conn.close()

    StateDB(db_path).initialize()

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        columns = {
            table: {str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
            for table in ("builder_events", "event_log", "tool_call_ledger")
        }
        assert "turn_id" in columns["builder_events"]
        assert "turn_id" in columns["event_log"]
        assert "turn_id" in columns["tool_call_ledger"]

        index_names = {str(row["name"]) for row in conn.execute("PRAGMA index_list(builder_events)").fetchall()}
        assert "idx_builder_events_turn_id" in index_names
        index_names = {str(row["name"]) for row in conn.execute("PRAGMA index_list(event_log)").fetchall()}
        assert "idx_event_log_turn_id" in index_names
    finally:
        conn.close()


class TelegramAllowlistSecurityTests(SparkTestCase):
    def test_resolve_inbound_dm_rejects_non_decimal_telegram_user_id(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        allowed = resolve_inbound_dm(
            state_db=self.state_db,
            channel_id="telegram",
            external_user_id="111",
            display_name="allowed",
        )
        assert allowed.allowed is True

        for external_user_id in ("111.0", " 111 ", "+111", "0", "-111", "111 OR 1=1"):
            with self.subTest(external_user_id=external_user_id):
                blocked = resolve_inbound_dm(
                    state_db=self.state_db,
                    channel_id="telegram",
                    external_user_id=external_user_id,
                    display_name="blocked",
                )
                assert blocked.allowed is False
                assert blocked.decision == "blocked"
