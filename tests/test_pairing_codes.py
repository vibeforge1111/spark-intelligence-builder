from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from spark_intelligence.identity.service import consume_pairing_code, issue_pairing_code

from tests.test_support import SparkTestCase


class PairingCodeTests(SparkTestCase):
    def test_issue_pairing_code_uses_unambiguous_short_code_without_plaintext_storage(self) -> None:
        self.add_telegram_channel()
        issued = issue_pairing_code(
            state_db=self.state_db,
            channel_id="telegram",
            external_user_id="111",
            now=datetime(2026, 4, 26, 12, 0, tzinfo=timezone.utc),
        )

        self.assertEqual(len(issued.code), 8)
        self.assertRegex(issued.code, r"^[23456789ABCDEFGHJKLMNPQRSTUVWXYZ]{8}$")
        with self.state_db.connect() as conn:
            rows = conn.execute(
                "SELECT state_key, value FROM runtime_state WHERE state_key LIKE 'pairing_code:telegram:%'"
            ).fetchall()
        self.assertEqual(len(rows), 1)
        self.assertNotIn(issued.code, str(rows[0]["state_key"]))
        self.assertNotIn(issued.code, str(rows[0]["value"]))
        payload = json.loads(str(rows[0]["value"]))
        self.assertEqual(payload["external_user_id"], "111")
        self.assertEqual(payload["status"], "pending")
        self.assertIn("code_hash", payload)

    def test_issue_pairing_code_rate_limits_generation_per_user(self) -> None:
        self.add_telegram_channel()
        start = datetime(2026, 4, 26, 12, 0, tzinfo=timezone.utc)
        issue_pairing_code(state_db=self.state_db, channel_id="telegram", external_user_id="111", now=start)

        with self.assertRaises(RuntimeError) as error:
            issue_pairing_code(
                state_db=self.state_db,
                channel_id="telegram",
                external_user_id="111",
                now=start + timedelta(minutes=2),
            )

        self.assertIn("rate limited", str(error.exception))

    def test_issue_pairing_code_limits_pending_codes_per_channel_user(self) -> None:
        self.add_telegram_channel()
        start = datetime(2026, 4, 26, 12, 0, tzinfo=timezone.utc)
        for offset in (0, 11, 22):
            issue_pairing_code(
                state_db=self.state_db,
                channel_id="telegram",
                external_user_id="111",
                now=start + timedelta(minutes=offset),
            )

        with self.assertRaises(RuntimeError) as error:
            issue_pairing_code(
                state_db=self.state_db,
                channel_id="telegram",
                external_user_id="111",
                now=start + timedelta(minutes=33),
            )

        self.assertIn("Too many pending", str(error.exception))

    def test_consume_pairing_code_approves_pairing_and_marks_code_used(self) -> None:
        self.add_telegram_channel()
        start = datetime(2026, 4, 26, 12, 0, tzinfo=timezone.utc)
        issued = issue_pairing_code(state_db=self.state_db, channel_id="telegram", external_user_id="111", now=start)

        result = consume_pairing_code(
            state_db=self.state_db,
            channel_id="telegram",
            external_user_id="111",
            code=f"{issued.code[:4]} {issued.code[4:]}",
            display_name="Alice",
            now=start + timedelta(minutes=5),
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.decision, "approved")
        with self.state_db.connect() as conn:
            pairing = conn.execute(
                "SELECT status FROM pairing_records WHERE pairing_id = 'pairing:telegram:111'"
            ).fetchone()
            self.assertIsNotNone(pairing)
            self.assertEqual(pairing["status"], "approved")
            values = [
                json.loads(str(row["value"]))
                for row in conn.execute("SELECT value FROM runtime_state WHERE state_key LIKE 'pairing_code:telegram:%'")
            ]
        self.assertEqual(values[0]["status"], "used")

    def test_consume_pairing_code_locks_after_repeated_bad_attempts(self) -> None:
        self.add_telegram_channel()
        start = datetime(2026, 4, 26, 12, 0, tzinfo=timezone.utc)
        issue_pairing_code(state_db=self.state_db, channel_id="telegram", external_user_id="111", now=start)

        for attempt in range(5):
            result = consume_pairing_code(
                state_db=self.state_db,
                channel_id="telegram",
                external_user_id="111",
                code="AAAAAAAA",
                now=start + timedelta(minutes=attempt),
            )
            self.assertFalse(result.ok)
            self.assertEqual(result.decision, "invalid_code")

        locked = consume_pairing_code(
            state_db=self.state_db,
            channel_id="telegram",
            external_user_id="111",
            code="AAAAAAAA",
            now=start + timedelta(minutes=6),
        )
        self.assertFalse(locked.ok)
        self.assertEqual(locked.decision, "locked")

    def test_operator_cli_can_issue_and_approve_pairing_code_without_logging_code(self) -> None:
        self.add_telegram_channel()
        issue_exit, issue_stdout, issue_stderr = self.run_cli(
            "operator",
            "issue-pairing-code",
            "telegram",
            "111",
            "--home",
            str(self.home),
        )
        self.assertEqual(issue_exit, 0, issue_stderr)
        code = issue_stdout.splitlines()[0].rsplit(": ", 1)[1]

        approve_exit, approve_stdout, approve_stderr = self.run_cli(
            "operator",
            "approve-pairing-code",
            "telegram",
            "111",
            code,
            "--home",
            str(self.home),
            "--display-name",
            "Alice",
        )
        self.assertEqual(approve_exit, 0, approve_stderr)
        self.assertIn("Approved pairing", approve_stdout)
        with self.state_db.connect() as conn:
            action_rows = conn.execute(
                "SELECT details_json FROM operator_events WHERE action IN ('issue_pairing_code', 'approve_pairing_code')"
            ).fetchall()
        self.assertEqual(len(action_rows), 2)
        self.assertNotIn(code, "\n".join(str(row["details_json"]) for row in action_rows))
