import json

from spark_intelligence.adapters.telegram.runtime import simulate_telegram_update
from spark_intelligence.identity.service import approve_pairing, pairing_summary, review_pairings

from tests.test_support import SparkTestCase, make_telegram_update


class OperatorPairingFlowTests(SparkTestCase):
    def test_allowlist_user_is_allowed_without_creating_pending_or_approved_pairing(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        result = simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=100,
                user_id="111",
                username="alice",
                text="hello",
            ),
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.decision, "allowed")
        pending = review_pairings(self.state_db, channel_id="telegram", status="pending")
        self.assertEqual(pending.rows, [])
        summary = pairing_summary(state_db=self.state_db, channel_id="telegram")
        self.assertEqual(summary.counts["approved"], 0)

    def test_pending_pairing_creates_reviewable_request(self) -> None:
        self.add_telegram_channel()

        result = simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=101,
                user_id="111",
                username="alice",
                text="/start",
            ),
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.decision, "pending_pairing")
        self.assertIn("operator approval", str(result.detail["response_text"]))

        report = review_pairings(self.state_db, channel_id="telegram", status="pending")
        self.assertEqual(len(report.rows), 1)
        self.assertEqual(report.rows[0]["external_user_id"], "111")
        self.assertEqual(report.rows[0]["status"], "pending")
        self.assertEqual(report.rows[0]["context"]["telegram_username"], "alice")

    def test_hold_latest_logs_exact_target_ref(self) -> None:
        self.add_telegram_channel()
        simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=102,
                user_id="111",
                username="alice",
                text="hello",
            ),
        )

        exit_code, _, stderr = self.run_cli(
            "operator",
            "hold-latest",
            "telegram",
            "--home",
            str(self.home),
            "--reason",
            "manual review",
        )

        self.assertEqual(exit_code, 0, stderr)
        held = review_pairings(self.state_db, channel_id="telegram", status="held")
        self.assertEqual(len(held.rows), 1)
        self.assertEqual(held.rows[0]["external_user_id"], "111")

        history_exit, history_stdout, history_stderr = self.run_cli(
            "operator",
            "history",
            "--home",
            str(self.home),
            "--action",
            "hold_latest_pairing",
            "--json",
        )

        self.assertEqual(history_exit, 0, history_stderr)
        payload = json.loads(history_stdout)
        self.assertEqual(payload["rows"][0]["target_ref"], "telegram:111")
        self.assertEqual(payload["rows"][0]["reason"], "manual review")

    def test_approve_latest_restores_allowed_dm(self) -> None:
        self.add_telegram_channel()
        simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=103,
                user_id="111",
                username="alice",
                text="/start",
            ),
        )

        exit_code, _, stderr = self.run_cli(
            "operator",
            "approve-latest",
            "telegram",
            "--home",
            str(self.home),
        )

        self.assertEqual(exit_code, 0, stderr)
        summary = pairing_summary(state_db=self.state_db, channel_id="telegram")
        self.assertEqual(summary.counts["approved"], 1)
        self.assertEqual(summary.latest_approved["external_user_id"], "111")

        follow_up = simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=104,
                user_id="111",
                username="alice",
                text="need help",
            ),
        )

        self.assertTrue(follow_up.ok)
        self.assertEqual(follow_up.decision, "allowed")
        self.assertIn("Pairing approved.", str(follow_up.detail["response_text"]))

    def test_revoke_latest_blocks_future_dm_with_revoked_reply(self) -> None:
        self.add_telegram_channel()
        simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=105,
                user_id="111",
                username="alice",
                text="hello",
            ),
        )

        exit_code, _, stderr = self.run_cli(
            "operator",
            "revoke-latest",
            "telegram",
            "--home",
            str(self.home),
            "--reason",
            "deny",
        )

        self.assertEqual(exit_code, 0, stderr)
        summary = pairing_summary(state_db=self.state_db, channel_id="telegram")
        self.assertEqual(summary.counts["revoked"], 1)

        follow_up = simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=106,
                user_id="111",
                username="alice",
                text="hello again",
            ),
        )

        self.assertFalse(follow_up.ok)
        self.assertEqual(follow_up.decision, "revoked")
        self.assertIn("no longer paired", str(follow_up.detail["response_text"]))

    def test_narrowing_allowlist_blocks_removed_user_even_after_prior_access(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111", "222"])

        first_access = simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=107,
                user_id="222",
                username="bob",
                text="hello",
            ),
        )
        self.assertTrue(first_access.ok)
        self.assertEqual(first_access.decision, "allowed")

        exit_code, _, stderr = self.run_cli(
            "channel",
            "add",
            "telegram",
            "--home",
            str(self.home),
            "--pairing-mode",
            "allowlist",
            "--allowed-user",
            "111",
        )
        self.assertEqual(exit_code, 0, stderr)

        follow_up = simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=108,
                user_id="222",
                username="bob",
                text="hello again",
            ),
        )

        self.assertFalse(follow_up.ok)
        self.assertEqual(follow_up.decision, "blocked")
        self.assertIn("requires explicit allowlist access", str(follow_up.detail["response_text"]))

    def test_revoke_pairing_does_not_override_configured_allowlist_access(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])
        approve_pairing(
            state_db=self.state_db,
            channel_id="telegram",
            external_user_id="111",
            display_name="alice",
        )

        exit_code, _, stderr = self.run_cli(
            "pairings",
            "revoke",
            "telegram",
            "111",
            "--home",
            str(self.home),
        )
        self.assertEqual(exit_code, 0, stderr)

        follow_up = simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=109,
                user_id="111",
                username="alice",
                text="still here",
            ),
        )

        self.assertTrue(follow_up.ok)
        self.assertEqual(follow_up.decision, "allowed")
