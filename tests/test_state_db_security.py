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
