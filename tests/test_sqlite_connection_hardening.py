from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock, patch

from spark_intelligence.researcher_bridge.advisory import _read_sib_active_personality_id
from spark_intelligence.state.db import StateDB

from tests.test_support import SparkTestCase


class SQLiteConnectionHardeningTests(SparkTestCase):
    def test_state_db_connection_uses_wal_and_bounded_busy_timeout(self) -> None:
        with self.state_db.connect() as conn:
            journal_mode = str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower()
            busy_timeout_ms = int(conn.execute("PRAGMA busy_timeout").fetchone()[0])

        self.assertEqual(journal_mode, "wal")
        self.assertEqual(busy_timeout_ms, 10_000)

    def test_state_db_closes_connection_when_wal_setup_fails(self) -> None:
        connection = MagicMock()
        connection.execute.side_effect = sqlite3.DatabaseError("wal unavailable")

        with patch("spark_intelligence.state.db.sqlite3.connect", return_value=connection):
            with self.assertRaisesRegex(sqlite3.DatabaseError, "wal unavailable"):
                StateDB(self.config_manager.paths.state_db).connect()

        connection.close.assert_called_once_with()

    def test_advisory_personality_read_uses_bounded_lock_timeout(self) -> None:
        with (
            patch.dict(
                "os.environ",
                {"SPARK_INTELLIGENCE_HOME": str(self.config_manager.paths.state_db.parent)},
                clear=False,
            ),
            patch("sqlite3.connect", wraps=sqlite3.connect) as connect,
        ):
            _read_sib_active_personality_id()

        connect.assert_called_once_with(str(self.config_manager.paths.state_db), timeout=10)
