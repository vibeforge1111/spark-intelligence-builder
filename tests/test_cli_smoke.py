from __future__ import annotations

import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

from spark_intelligence.channel.service import TelegramBotProfile
from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.gateway.discord_webhook import DISCORD_WEBHOOK_PATH, handle_discord_webhook
from spark_intelligence.gateway.whatsapp_webhook import WHATSAPP_WEBHOOK_PATH, handle_whatsapp_webhook

from tests.test_support import SparkTestCase


class CliSmokeTests(SparkTestCase):
    def test_setup_creates_bootstrap_and_doctor_and_status_report_clean_temp_home(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            home = Path(tempdir)

            setup_exit, setup_stdout, setup_stderr = self.run_cli("setup", "--home", str(home))
            self.assertEqual(setup_exit, 0, setup_stderr)
            self.assertIn("Spark Intelligence home:", setup_stdout)
            self.assertTrue((home / "config.yaml").exists())
            self.assertTrue((home / ".env").exists())
            self.assertTrue((home / "state.db").exists())

            doctor_exit, doctor_stdout, doctor_stderr = self.run_cli("doctor", "--home", str(home))
            self.assertEqual(doctor_exit, 0, doctor_stderr)
            self.assertIn("Doctor status: ok", doctor_stdout)

            status_exit, status_stdout, status_stderr = self.run_cli("status", "--home", str(home))
            self.assertEqual(status_exit, 0, status_stderr)
            self.assertIn("Spark Intelligence status", status_stdout)
            self.assertIn("- doctor: ok", status_stdout)

    def test_operator_security_surfaces_telegram_poll_failure_in_json(self) -> None:
        self.add_telegram_channel(bot_token="good-token")

        class PollFailingClient:
            def __init__(self, token: str):
                self.token = token

            def get_me(self) -> dict[str, object]:
                return {"result": {"username": "sparkbot"}}

            def get_updates(self, *, offset: int | None = None, timeout_seconds: int = 5) -> list[dict[str, object]]:
                raise URLError("offline")

            def send_message(self, *, chat_id: str, text: str) -> dict[str, object]:
                return {"ok": True}

        with patch("spark_intelligence.gateway.runtime.TelegramBotApiClient", PollFailingClient):
            gateway_exit, gateway_stdout, gateway_stderr = self.run_cli(
                "gateway",
                "start",
                "--home",
                str(self.home),
                "--once",
            )

        self.assertEqual(gateway_exit, 1, gateway_stderr)
        self.assertIn("Telegram polling failure", gateway_stdout)

        security_exit, security_stdout, security_stderr = self.run_cli(
            "operator",
            "security",
            "--home",
            str(self.home),
            "--json",
        )

        self.assertEqual(security_exit, 0, security_stderr)
        payload = json.loads(security_stdout)
        self.assertEqual(payload["counts"]["channel_alerts"], 1)
        self.assertEqual(payload["counts"]["bridge_alerts"], 1)
        self.assertEqual(payload["channel_alerts"][0]["status"], "poll_failure")
        self.assertEqual(payload["recent"]["duplicates"], [])
        self.assertEqual(payload["recent"]["rate_limited"], [])

    def test_operator_security_surfaces_discord_webhook_auth_rejections(self) -> None:
        setup_exit, _, setup_stderr = self.run_cli(
            "channel",
            "add",
            "discord",
            "--home",
            str(self.home),
            "--allow-legacy-message-webhook",
            "--webhook-secret",
            "discord-webhook-secret",
        )
        self.assertEqual(setup_exit, 0, setup_stderr)

        response = handle_discord_webhook(
            config_manager=self.config_manager,
            state_db=self.state_db,
            path=DISCORD_WEBHOOK_PATH,
            method="POST",
            content_type="application/json",
            headers={},
            body=b"{}",
        )
        self.assertEqual(response.status_code, 401)

        security_exit, security_stdout, security_stderr = self.run_cli(
            "operator",
            "security",
            "--home",
            str(self.home),
            "--json",
        )

        self.assertEqual(security_exit, 0, security_stderr)
        payload = json.loads(security_stdout)
        self.assertEqual(payload["counts"]["webhook_alerts"], 1)
        self.assertEqual(payload["webhook_alerts"][0]["event"], "discord_webhook_auth_failed")
        self.assertIn("Discord webhook auth rejected 1 time(s)", payload["webhook_alerts"][0]["summary"])
        self.assertEqual(
            payload["webhook_alerts"][0]["recommended_command"],
            "spark-intelligence gateway traces --event discord_webhook_auth_failed --limit 20",
        )

    def test_operator_security_surfaces_discord_ingress_missing(self) -> None:
        setup_exit, _, setup_stderr = self.run_cli(
            "channel",
            "add",
            "discord",
            "--home",
            str(self.home),
        )
        self.assertEqual(setup_exit, 0, setup_stderr)

        security_exit, security_stdout, security_stderr = self.run_cli(
            "operator",
            "security",
            "--home",
            str(self.home),
            "--json",
        )

        self.assertEqual(security_exit, 0, security_stderr)
        payload = json.loads(security_stdout)
        self.assertEqual(payload["counts"]["channel_alerts"], 1)
        self.assertEqual(payload["channel_alerts"][0]["channel_id"], "discord")
        self.assertEqual(payload["channel_alerts"][0]["status"], "ingress_missing")
        self.assertIn("Discord ingress is not ready", payload["channel_alerts"][0]["summary"])
        self.assertEqual(
            payload["channel_alerts"][0]["recommended_command"],
            "spark-intelligence channel add discord --interaction-public-key <public-key>",
        )

    def test_operator_security_escalates_sustained_discord_webhook_auth_rejections(self) -> None:
        setup_exit, _, setup_stderr = self.run_cli(
            "channel",
            "add",
            "discord",
            "--home",
            str(self.home),
            "--allow-legacy-message-webhook",
            "--webhook-secret",
            "discord-webhook-secret",
        )
        self.assertEqual(setup_exit, 0, setup_stderr)

        for _ in range(3):
            response = handle_discord_webhook(
                config_manager=self.config_manager,
                state_db=self.state_db,
                path=DISCORD_WEBHOOK_PATH,
                method="POST",
                content_type="application/json",
                headers={},
                body=b"{}",
            )
            self.assertEqual(response.status_code, 401)

        security_exit, security_stdout, security_stderr = self.run_cli(
            "operator",
            "security",
            "--home",
            str(self.home),
            "--json",
        )

        self.assertEqual(security_exit, 0, security_stderr)
        payload = json.loads(security_stdout)
        self.assertEqual(payload["counts"]["webhook_alerts"], 1)
        alert = payload["webhook_alerts"][0]
        self.assertEqual(alert["event"], "discord_webhook_auth_failed")
        self.assertEqual(alert["status"], "sustained_rejections")
        self.assertEqual(alert["severity"], "critical")
        self.assertEqual(alert["count"], 3)
        self.assertIn("sustained discord webhook auth rejected detected", alert["summary"].lower())
        webhook_items = [
            item
            for item in payload["items"]
            if item["recommended_command"] == "spark-intelligence gateway traces --event discord_webhook_auth_failed --limit 20"
        ]
        self.assertEqual(len(webhook_items), 1)
        self.assertEqual(webhook_items[0]["priority"], "high")

    def test_operator_security_cools_down_old_discord_webhook_auth_rejections(self) -> None:
        setup_exit, _, setup_stderr = self.run_cli(
            "channel",
            "add",
            "discord",
            "--home",
            str(self.home),
            "--allow-legacy-message-webhook",
            "--webhook-secret",
            "discord-webhook-secret",
        )
        self.assertEqual(setup_exit, 0, setup_stderr)

        for _ in range(3):
            response = handle_discord_webhook(
                config_manager=self.config_manager,
                state_db=self.state_db,
                path=DISCORD_WEBHOOK_PATH,
                method="POST",
                content_type="application/json",
                headers={},
                body=b"{}",
            )
            self.assertEqual(response.status_code, 401)

        with patch(
            "spark_intelligence.ops.service._utc_now",
            return_value=datetime.now(timezone.utc) + timedelta(minutes=16),
        ):
            security_exit, security_stdout, security_stderr = self.run_cli(
                "operator",
                "security",
                "--home",
                str(self.home),
                "--json",
            )

        self.assertEqual(security_exit, 0, security_stderr)
        payload = json.loads(security_stdout)
        self.assertEqual(payload["counts"]["webhook_alerts"], 0)
        self.assertEqual(payload["webhook_alerts"], [])

    def test_operator_can_snooze_discord_webhook_alert(self) -> None:
        setup_exit, _, setup_stderr = self.run_cli(
            "channel",
            "add",
            "discord",
            "--home",
            str(self.home),
            "--allow-legacy-message-webhook",
            "--webhook-secret",
            "discord-webhook-secret",
        )
        self.assertEqual(setup_exit, 0, setup_stderr)

        response = handle_discord_webhook(
            config_manager=self.config_manager,
            state_db=self.state_db,
            path=DISCORD_WEBHOOK_PATH,
            method="POST",
            content_type="application/json",
            headers={},
            body=b"{}",
        )
        self.assertEqual(response.status_code, 401)

        snooze_exit, snooze_stdout, snooze_stderr = self.run_cli(
            "operator",
            "snooze-webhook-alert",
            "discord_webhook_auth_failed",
            "--minutes",
            "30",
            "--home",
            str(self.home),
            "--reason",
            "known noisy source",
        )
        self.assertEqual(snooze_exit, 0, snooze_stderr)
        self.assertIn("Snoozed webhook alert 'discord_webhook_auth_failed'", snooze_stdout)

        security_exit, security_stdout, security_stderr = self.run_cli(
            "operator",
            "security",
            "--home",
            str(self.home),
            "--json",
        )
        self.assertEqual(security_exit, 0, security_stderr)
        payload = json.loads(security_stdout)
        self.assertEqual(payload["counts"]["webhook_alerts"], 0)
        self.assertEqual(payload["counts"]["webhook_snoozes"], 1)
        self.assertEqual(payload["counts"]["active_suppressed_webhook_snoozes"], 1)
        self.assertEqual(payload["webhook_alerts"], [])
        self.assertEqual(len(payload["webhook_snoozes"]), 1)
        self.assertEqual(payload["webhook_snoozes"][0]["event"], "discord_webhook_auth_failed")
        self.assertIsInstance(payload["webhook_snoozes"][0]["snoozed_at"], str)
        self.assertEqual(payload["webhook_snoozes"][0]["reason"], "known noisy source")
        self.assertEqual(payload["webhook_snoozes"][0]["suppressed_recent_count"], 1)
        self.assertEqual(payload["webhook_snoozes"][0]["latest_suppressed_reason"], "Discord webhook secret header is missing.")
        self.assertIsInstance(payload["webhook_snoozes"][0]["latest_suppressed_at"], str)
        snooze_items = [
            item
            for item in payload["items"]
            if item["recommended_command"] == "spark-intelligence operator clear-webhook-alert-snooze discord_webhook_auth_failed"
        ]
        self.assertEqual(len(snooze_items), 1)
        self.assertEqual(snooze_items[0]["priority"], "info")
        self.assertEqual(
            snooze_items[0]["clear_command"],
            "spark-intelligence operator clear-webhook-alert-snooze discord_webhook_auth_failed",
        )
        self.assertIn("was snoozed at", snooze_items[0]["summary"])
        self.assertIn("Reason: known noisy source.", snooze_items[0]["summary"])
        self.assertIn(
            "Suppressed 1 recent rejection(s); latest reason: Discord webhook secret header is missing.",
            snooze_items[0]["summary"],
        )
        self.assertIn("Last suppressed at:", snooze_items[0]["summary"])

        history_exit, history_stdout, history_stderr = self.run_cli(
            "operator",
            "history",
            "--home",
            str(self.home),
            "--action",
            "snooze_webhook_alert",
            "--json",
        )
        self.assertEqual(history_exit, 0, history_stderr)
        history_payload = json.loads(history_stdout)
        self.assertEqual(len(history_payload["rows"]), 1)
        self.assertEqual(history_payload["rows"][0]["target_ref"], "discord_webhook_auth_failed")
        self.assertEqual(history_payload["rows"][0]["reason"], "known noisy source")

    def test_operator_security_escalates_sustained_suppressed_discord_webhook_traffic(self) -> None:
        setup_exit, _, setup_stderr = self.run_cli(
            "channel",
            "add",
            "discord",
            "--home",
            str(self.home),
            "--allow-legacy-message-webhook",
            "--webhook-secret",
            "discord-webhook-secret",
        )
        self.assertEqual(setup_exit, 0, setup_stderr)

        for _ in range(3):
            response = handle_discord_webhook(
                config_manager=self.config_manager,
                state_db=self.state_db,
                path=DISCORD_WEBHOOK_PATH,
                method="POST",
                content_type="application/json",
                headers={},
                body=b"{}",
            )
            self.assertEqual(response.status_code, 401)

        snooze_exit, _, snooze_stderr = self.run_cli(
            "operator",
            "snooze-webhook-alert",
            "discord_webhook_auth_failed",
            "--minutes",
            "30",
            "--home",
            str(self.home),
        )
        self.assertEqual(snooze_exit, 0, snooze_stderr)

        security_exit, security_stdout, security_stderr = self.run_cli(
            "operator",
            "security",
            "--home",
            str(self.home),
            "--json",
        )
        self.assertEqual(security_exit, 0, security_stderr)
        payload = json.loads(security_stdout)
        self.assertEqual(payload["counts"]["webhook_alerts"], 0)
        self.assertEqual(payload["counts"]["webhook_snoozes"], 1)
        self.assertEqual(payload["counts"]["active_suppressed_webhook_snoozes"], 1)
        self.assertEqual(payload["webhook_snoozes"][0]["status"], "sustained_rejections_suppressed")
        self.assertEqual(payload["webhook_snoozes"][0]["severity"], "warning")
        self.assertEqual(payload["webhook_snoozes"][0]["suppressed_recent_count"], 3)
        self.assertIsInstance(payload["webhook_snoozes"][0]["latest_suppressed_at"], str)
        snooze_items = [
            item
            for item in payload["items"]
            if item.get("clear_command") == "spark-intelligence operator clear-webhook-alert-snooze discord_webhook_auth_failed"
        ]
        self.assertEqual(len(snooze_items), 1)
        self.assertEqual(snooze_items[0]["priority"], "medium")
        self.assertEqual(
            snooze_items[0]["recommended_command"],
            "spark-intelligence gateway traces --event discord_webhook_auth_failed --limit 20",
        )
        self.assertIn("Snooze is still masking sustained ingress traffic.", snooze_items[0]["summary"])

        list_exit, list_stdout, list_stderr = self.run_cli(
            "operator",
            "webhook-alert-snoozes",
            "--home",
            str(self.home),
            "--json",
        )
        self.assertEqual(list_exit, 0, list_stderr)
        list_payload = json.loads(list_stdout)
        self.assertEqual(len(list_payload["rows"]), 1)
        self.assertEqual(list_payload["rows"][0]["status"], "sustained_rejections_suppressed")
        self.assertEqual(list_payload["rows"][0]["severity"], "warning")
        self.assertIsInstance(list_payload["rows"][0]["latest_suppressed_at"], str)
        self.assertEqual(
            list_payload["rows"][0]["recommended_command"],
            "spark-intelligence gateway traces --event discord_webhook_auth_failed --limit 20",
        )
        self.assertEqual(
            list_payload["rows"][0]["clear_command"],
            "spark-intelligence operator clear-webhook-alert-snooze discord_webhook_auth_failed",
        )

    def test_operator_can_list_and_clear_discord_webhook_alert_snooze(self) -> None:
        setup_exit, _, setup_stderr = self.run_cli(
            "channel",
            "add",
            "discord",
            "--home",
            str(self.home),
            "--allow-legacy-message-webhook",
            "--webhook-secret",
            "discord-webhook-secret",
        )
        self.assertEqual(setup_exit, 0, setup_stderr)

        response = handle_discord_webhook(
            config_manager=self.config_manager,
            state_db=self.state_db,
            path=DISCORD_WEBHOOK_PATH,
            method="POST",
            content_type="application/json",
            headers={},
            body=b"{}",
        )
        self.assertEqual(response.status_code, 401)

        snooze_exit, _, snooze_stderr = self.run_cli(
            "operator",
            "snooze-webhook-alert",
            "discord_webhook_auth_failed",
            "--minutes",
            "30",
            "--home",
            str(self.home),
        )
        self.assertEqual(snooze_exit, 0, snooze_stderr)

        list_exit, list_stdout, list_stderr = self.run_cli(
            "operator",
            "webhook-alert-snoozes",
            "--home",
            str(self.home),
            "--json",
        )
        self.assertEqual(list_exit, 0, list_stderr)
        list_payload = json.loads(list_stdout)
        self.assertEqual(len(list_payload["rows"]), 1)
        self.assertEqual(list_payload["rows"][0]["event"], "discord_webhook_auth_failed")
        self.assertIsInstance(list_payload["rows"][0]["snoozed_at"], str)
        self.assertGreaterEqual(list_payload["rows"][0]["remaining_minutes"], 1)
        self.assertIsNone(list_payload["rows"][0]["reason"])
        self.assertEqual(list_payload["rows"][0]["suppressed_recent_count"], 1)
        self.assertEqual(list_payload["rows"][0]["latest_suppressed_reason"], "Discord webhook secret header is missing.")
        self.assertIsInstance(list_payload["rows"][0]["latest_suppressed_at"], str)
        self.assertEqual(list_payload["rows"][0]["status"], "snoozed")
        self.assertEqual(list_payload["rows"][0]["severity"], "info")
        self.assertEqual(
            list_payload["rows"][0]["recommended_command"],
            "spark-intelligence operator clear-webhook-alert-snooze discord_webhook_auth_failed",
        )
        self.assertEqual(
            list_payload["rows"][0]["clear_command"],
            "spark-intelligence operator clear-webhook-alert-snooze discord_webhook_auth_failed",
        )

        clear_exit, clear_stdout, clear_stderr = self.run_cli(
            "operator",
            "clear-webhook-alert-snooze",
            "discord_webhook_auth_failed",
            "--home",
            str(self.home),
            "--reason",
            "noise resolved",
        )
        self.assertEqual(clear_exit, 0, clear_stderr)
        self.assertIn("Cleared webhook alert snooze for 'discord_webhook_auth_failed'", clear_stdout)
        self.assertIn("set at", clear_stdout)
        self.assertIn("until", clear_stdout)

        security_exit, security_stdout, security_stderr = self.run_cli(
            "operator",
            "security",
            "--home",
            str(self.home),
            "--json",
        )
        self.assertEqual(security_exit, 0, security_stderr)
        payload = json.loads(security_stdout)
        self.assertEqual(payload["counts"]["webhook_alerts"], 1)
        self.assertEqual(payload["webhook_alerts"][0]["event"], "discord_webhook_auth_failed")

        history_exit, history_stdout, history_stderr = self.run_cli(
            "operator",
            "history",
            "--home",
            str(self.home),
            "--action",
            "clear_webhook_alert_snooze",
            "--json",
        )
        self.assertEqual(history_exit, 0, history_stderr)
        history_payload = json.loads(history_stdout)
        self.assertEqual(len(history_payload["rows"]), 1)
        self.assertEqual(history_payload["rows"][0]["target_ref"], "discord_webhook_auth_failed")
        self.assertEqual(history_payload["rows"][0]["reason"], "noise resolved")
        self.assertEqual(history_payload["rows"][0]["details"]["removed"], True)
        self.assertEqual(history_payload["rows"][0]["details"]["cleared_snooze"]["event"], "discord_webhook_auth_failed")
        self.assertIsInstance(history_payload["rows"][0]["details"]["cleared_snooze"]["snoozed_at"], str)
        self.assertIsInstance(history_payload["rows"][0]["details"]["cleared_snooze"]["snooze_until"], str)
        self.assertIsNone(history_payload["rows"][0]["details"]["cleared_snooze"]["reason"])

    def test_operator_webhook_alert_snoozes_prioritize_sustained_masked_traffic(self) -> None:
        discord_exit, _, discord_stderr = self.run_cli(
            "channel",
            "add",
            "discord",
            "--home",
            str(self.home),
            "--allow-legacy-message-webhook",
            "--webhook-secret",
            "discord-webhook-secret",
        )
        self.assertEqual(discord_exit, 0, discord_stderr)

        whatsapp_exit, _, whatsapp_stderr = self.run_cli(
            "channel",
            "add",
            "whatsapp",
            "--home",
            str(self.home),
            "--bot-token",
            "whatsapp-token",
            "--webhook-secret",
            "whatsapp-webhook-secret",
            "--webhook-verify-token",
            "whatsapp-verify-token",
        )
        self.assertEqual(whatsapp_exit, 0, whatsapp_stderr)

        discord_response = handle_discord_webhook(
            config_manager=self.config_manager,
            state_db=self.state_db,
            path=DISCORD_WEBHOOK_PATH,
            method="POST",
            content_type="application/json",
            headers={},
            body=b"{}",
        )
        self.assertEqual(discord_response.status_code, 401)

        for _ in range(3):
            whatsapp_response = handle_whatsapp_webhook(
                config_manager=self.config_manager,
                state_db=self.state_db,
                path=WHATSAPP_WEBHOOK_PATH,
                method="GET",
                content_type=None,
                headers={},
                body=b"",
                query_params={
                    "hub.mode": "subscribe",
                    "hub.verify_token": "wrong-token",
                    "hub.challenge": "challenge-code",
                },
            )
            self.assertEqual(whatsapp_response.status_code, 401)

        discord_snooze_exit, _, discord_snooze_stderr = self.run_cli(
            "operator",
            "snooze-webhook-alert",
            "discord_webhook_auth_failed",
            "--minutes",
            "5",
            "--home",
            str(self.home),
        )
        self.assertEqual(discord_snooze_exit, 0, discord_snooze_stderr)

        whatsapp_snooze_exit, _, whatsapp_snooze_stderr = self.run_cli(
            "operator",
            "snooze-webhook-alert",
            "whatsapp_webhook_verification_failed",
            "--minutes",
            "30",
            "--home",
            str(self.home),
        )
        self.assertEqual(whatsapp_snooze_exit, 0, whatsapp_snooze_stderr)

        list_exit, list_stdout, list_stderr = self.run_cli(
            "operator",
            "webhook-alert-snoozes",
            "--home",
            str(self.home),
            "--json",
        )
        self.assertEqual(list_exit, 0, list_stderr)
        list_payload = json.loads(list_stdout)
        self.assertEqual(
            [row["event"] for row in list_payload["rows"]],
            ["whatsapp_webhook_verification_failed", "discord_webhook_auth_failed"],
        )
        self.assertEqual(list_payload["rows"][0]["status"], "sustained_rejections_suppressed")
        self.assertEqual(list_payload["rows"][0]["severity"], "warning")
        self.assertEqual(list_payload["rows"][1]["status"], "snoozed")
        self.assertEqual(list_payload["rows"][1]["severity"], "info")

    def test_operator_webhook_alert_snoozes_prunes_expired_runtime_state(self) -> None:
        setup_exit, _, setup_stderr = self.run_cli(
            "channel",
            "add",
            "discord",
            "--home",
            str(self.home),
            "--allow-legacy-message-webhook",
            "--webhook-secret",
            "discord-webhook-secret",
        )
        self.assertEqual(setup_exit, 0, setup_stderr)

        response = handle_discord_webhook(
            config_manager=self.config_manager,
            state_db=self.state_db,
            path=DISCORD_WEBHOOK_PATH,
            method="POST",
            content_type="application/json",
            headers={},
            body=b"{}",
        )
        self.assertEqual(response.status_code, 401)

        snooze_exit, _, snooze_stderr = self.run_cli(
            "operator",
            "snooze-webhook-alert",
            "discord_webhook_auth_failed",
            "--minutes",
            "30",
            "--home",
            str(self.home),
        )
        self.assertEqual(snooze_exit, 0, snooze_stderr)

        with patch(
            "spark_intelligence.ops.service._utc_now",
            return_value=datetime.now(timezone.utc) + timedelta(minutes=31),
        ):
            list_exit, list_stdout, list_stderr = self.run_cli(
                "operator",
                "webhook-alert-snoozes",
                "--home",
                str(self.home),
                "--json",
            )

        self.assertEqual(list_exit, 0, list_stderr)
        list_payload = json.loads(list_stdout)
        self.assertEqual(list_payload["rows"], [])
        with self.state_db.connect() as conn:
            row = conn.execute(
                "SELECT value FROM runtime_state WHERE state_key = ? LIMIT 1",
                ("ops:webhook_alert_snooze:discord_webhook_auth_failed",),
            ).fetchone()
        self.assertIsNone(row)

    def test_operator_inbox_surfaces_whatsapp_verification_rejections(self) -> None:
        setup_exit, _, setup_stderr = self.run_cli(
            "channel",
            "add",
            "whatsapp",
            "--home",
            str(self.home),
            "--bot-token",
            "whatsapp-token",
            "--webhook-secret",
            "whatsapp-webhook-secret",
            "--webhook-verify-token",
            "whatsapp-verify-token",
        )
        self.assertEqual(setup_exit, 0, setup_stderr)

        response = handle_whatsapp_webhook(
            config_manager=self.config_manager,
            state_db=self.state_db,
            path=WHATSAPP_WEBHOOK_PATH,
            method="GET",
            content_type=None,
            headers={},
            body=b"",
            query_params={
                "hub.mode": "subscribe",
                "hub.verify_token": "wrong-token",
                "hub.challenge": "challenge-code",
            },
        )
        self.assertEqual(response.status_code, 401)

        inbox_exit, inbox_stdout, inbox_stderr = self.run_cli(
            "operator",
            "inbox",
            "--home",
            str(self.home),
            "--json",
        )

        self.assertEqual(inbox_exit, 0, inbox_stderr)
        payload = json.loads(inbox_stdout)
        self.assertEqual(payload["counts"]["webhook_alerts"], 1)
        self.assertEqual(payload["webhooks"][0]["event"], "whatsapp_webhook_verification_failed")
        webhook_items = [item for item in payload["items"] if item["kind"] == "webhook"]
        self.assertEqual(len(webhook_items), 1)
        self.assertIn("WhatsApp webhook verification rejected 1 time(s)", webhook_items[0]["summary"])
        self.assertEqual(
            webhook_items[0]["recommended_command"],
            "spark-intelligence gateway traces --event whatsapp_webhook_verification_failed --limit 20",
        )

    def test_operator_inbox_surfaces_whatsapp_ingress_missing(self) -> None:
        setup_exit, _, setup_stderr = self.run_cli(
            "channel",
            "add",
            "whatsapp",
            "--home",
            str(self.home),
            "--bot-token",
            "whatsapp-token",
        )
        self.assertEqual(setup_exit, 0, setup_stderr)

        inbox_exit, inbox_stdout, inbox_stderr = self.run_cli(
            "operator",
            "inbox",
            "--home",
            str(self.home),
            "--json",
        )

        self.assertEqual(inbox_exit, 0, inbox_stderr)
        payload = json.loads(inbox_stdout)
        self.assertEqual(payload["counts"]["channel_alerts"], 1)
        self.assertEqual(payload["channels"][0]["channel_id"], "whatsapp")
        self.assertEqual(payload["channels"][0]["status"], "ingress_missing")
        self.assertIn("WhatsApp ingress is not ready", payload["channels"][0]["summary"])
        channel_items = [item for item in payload["items"] if item["kind"] == "channel"]
        self.assertEqual(len(channel_items), 1)
        self.assertEqual(
            channel_items[0]["recommended_command"],
            "spark-intelligence channel add whatsapp --webhook-secret <secret> --webhook-verify-token <token>",
        )

    def test_operator_inbox_escalates_sustained_whatsapp_verification_rejections(self) -> None:
        setup_exit, _, setup_stderr = self.run_cli(
            "channel",
            "add",
            "whatsapp",
            "--home",
            str(self.home),
            "--bot-token",
            "whatsapp-token",
            "--webhook-secret",
            "whatsapp-webhook-secret",
            "--webhook-verify-token",
            "whatsapp-verify-token",
        )
        self.assertEqual(setup_exit, 0, setup_stderr)

        for _ in range(3):
            response = handle_whatsapp_webhook(
                config_manager=self.config_manager,
                state_db=self.state_db,
                path=WHATSAPP_WEBHOOK_PATH,
                method="GET",
                content_type=None,
                headers={},
                body=b"",
                query_params={
                    "hub.mode": "subscribe",
                    "hub.verify_token": "wrong-token",
                    "hub.challenge": "challenge-code",
                },
            )
            self.assertEqual(response.status_code, 401)

        inbox_exit, inbox_stdout, inbox_stderr = self.run_cli(
            "operator",
            "inbox",
            "--home",
            str(self.home),
            "--json",
        )

        self.assertEqual(inbox_exit, 0, inbox_stderr)
        payload = json.loads(inbox_stdout)
        self.assertEqual(payload["counts"]["webhook_alerts"], 1)
        alert = payload["webhooks"][0]
        self.assertEqual(alert["event"], "whatsapp_webhook_verification_failed")
        self.assertEqual(alert["status"], "sustained_rejections")
        self.assertEqual(alert["severity"], "critical")
        self.assertEqual(alert["count"], 3)
        webhook_items = [item for item in payload["items"] if item["kind"] == "webhook"]
        self.assertEqual(len(webhook_items), 1)
        self.assertEqual(webhook_items[0]["priority"], "high")

    def test_operator_inbox_cools_down_old_whatsapp_verification_rejections(self) -> None:
        setup_exit, _, setup_stderr = self.run_cli(
            "channel",
            "add",
            "whatsapp",
            "--home",
            str(self.home),
            "--bot-token",
            "whatsapp-token",
            "--webhook-secret",
            "whatsapp-webhook-secret",
            "--webhook-verify-token",
            "whatsapp-verify-token",
        )
        self.assertEqual(setup_exit, 0, setup_stderr)

        for _ in range(3):
            response = handle_whatsapp_webhook(
                config_manager=self.config_manager,
                state_db=self.state_db,
                path=WHATSAPP_WEBHOOK_PATH,
                method="GET",
                content_type=None,
                headers={},
                body=b"",
                query_params={
                    "hub.mode": "subscribe",
                    "hub.verify_token": "wrong-token",
                    "hub.challenge": "challenge-code",
                },
            )
            self.assertEqual(response.status_code, 401)

        with patch(
            "spark_intelligence.ops.service._utc_now",
            return_value=datetime.now(timezone.utc) + timedelta(minutes=16),
        ):
            inbox_exit, inbox_stdout, inbox_stderr = self.run_cli(
                "operator",
                "inbox",
                "--home",
                str(self.home),
                "--json",
            )

        self.assertEqual(inbox_exit, 0, inbox_stderr)
        payload = json.loads(inbox_stdout)
        self.assertEqual(payload["counts"]["webhook_alerts"], 0)
        self.assertEqual(payload["webhooks"], [])

    def test_operator_can_snooze_whatsapp_verification_alert(self) -> None:
        setup_exit, _, setup_stderr = self.run_cli(
            "channel",
            "add",
            "whatsapp",
            "--home",
            str(self.home),
            "--bot-token",
            "whatsapp-token",
            "--webhook-secret",
            "whatsapp-webhook-secret",
            "--webhook-verify-token",
            "whatsapp-verify-token",
        )
        self.assertEqual(setup_exit, 0, setup_stderr)

        response = handle_whatsapp_webhook(
            config_manager=self.config_manager,
            state_db=self.state_db,
            path=WHATSAPP_WEBHOOK_PATH,
            method="GET",
            content_type=None,
            headers={},
            body=b"",
            query_params={
                "hub.mode": "subscribe",
                "hub.verify_token": "wrong-token",
                "hub.challenge": "challenge-code",
            },
        )
        self.assertEqual(response.status_code, 401)

        snooze_exit, snooze_stdout, snooze_stderr = self.run_cli(
            "operator",
            "snooze-webhook-alert",
            "whatsapp_webhook_verification_failed",
            "--minutes",
            "30",
            "--home",
            str(self.home),
        )
        self.assertEqual(snooze_exit, 0, snooze_stderr)
        self.assertIn("Snoozed webhook alert 'whatsapp_webhook_verification_failed'", snooze_stdout)

        inbox_exit, inbox_stdout, inbox_stderr = self.run_cli(
            "operator",
            "inbox",
            "--home",
            str(self.home),
            "--json",
        )
        self.assertEqual(inbox_exit, 0, inbox_stderr)
        payload = json.loads(inbox_stdout)
        self.assertEqual(payload["counts"]["webhook_alerts"], 0)
        self.assertEqual(payload["counts"]["webhook_snoozes"], 1)
        self.assertEqual(payload["counts"]["active_suppressed_webhook_snoozes"], 1)
        self.assertEqual(payload["webhooks"], [])
        self.assertEqual(len(payload["webhook_snoozes"]), 1)
        self.assertEqual(payload["webhook_snoozes"][0]["event"], "whatsapp_webhook_verification_failed")
        self.assertIsInstance(payload["webhook_snoozes"][0]["snoozed_at"], str)
        self.assertIsNone(payload["webhook_snoozes"][0]["reason"])
        self.assertEqual(payload["webhook_snoozes"][0]["suppressed_recent_count"], 1)
        self.assertEqual(payload["webhook_snoozes"][0]["latest_suppressed_reason"], "WhatsApp webhook verify token is invalid.")
        self.assertIsInstance(payload["webhook_snoozes"][0]["latest_suppressed_at"], str)
        snooze_items = [item for item in payload["items"] if item["kind"] == "webhook_snooze"]
        self.assertEqual(len(snooze_items), 1)
        self.assertIn("was snoozed at", snooze_items[0]["summary"])
        self.assertEqual(
            snooze_items[0]["clear_command"],
            "spark-intelligence operator clear-webhook-alert-snooze whatsapp_webhook_verification_failed",
        )
        self.assertIn(
            "Suppressed 1 recent rejection(s); latest reason: WhatsApp webhook verify token is invalid.",
            snooze_items[0]["summary"],
        )
        self.assertIn("Last suppressed at:", snooze_items[0]["summary"])
        self.assertEqual(
            snooze_items[0]["recommended_command"],
            "spark-intelligence operator clear-webhook-alert-snooze whatsapp_webhook_verification_failed",
        )

    def test_doctor_degrades_after_telegram_poll_failure(self) -> None:
        self.add_telegram_channel(bot_token="good-token")

        class PollFailingClient:
            def __init__(self, token: str):
                self.token = token

            def get_me(self) -> dict[str, object]:
                return {"result": {"username": "sparkbot"}}

            def get_updates(self, *, offset: int | None = None, timeout_seconds: int = 5) -> list[dict[str, object]]:
                raise URLError("offline")

            def send_message(self, *, chat_id: str, text: str) -> dict[str, object]:
                return {"ok": True}

        with patch("spark_intelligence.gateway.runtime.TelegramBotApiClient", PollFailingClient):
            self.run_cli(
                "gateway",
                "start",
                "--home",
                str(self.home),
                "--once",
            )

        doctor_exit, doctor_stdout, doctor_stderr = self.run_cli(
            "doctor",
            "--home",
            str(self.home),
        )

        self.assertEqual(doctor_exit, 1, doctor_stderr)
        self.assertIn("Doctor status: degraded", doctor_stdout)
        self.assertIn("[fail] telegram-runtime: poll_failures=1", doctor_stdout)

    def test_gateway_status_is_not_ready_when_telegram_is_configured_without_provider(self) -> None:
        self.add_telegram_channel()

        exit_code, stdout, stderr = self.run_cli(
            "gateway",
            "status",
            "--home",
            str(self.home),
        )

        self.assertEqual(exit_code, 1, stderr)
        self.assertIn("Gateway ready: no", stdout)
        self.assertIn("- channels: telegram", stdout)
        self.assertIn("- providers: none", stdout)

    def test_gateway_status_lists_auth_profile_backed_provider_when_secret_is_missing(self) -> None:
        self.add_telegram_channel()
        connect_exit, _, connect_stderr = self.run_cli(
            "auth",
            "connect",
            "openai",
            "--home",
            str(self.home),
            "--api-key-env",
            "MISSING_OPENAI_KEY",
            "--model",
            "gpt-5.4",
        )
        self.assertEqual(connect_exit, 0, connect_stderr)

        exit_code, stdout, stderr = self.run_cli(
            "gateway",
            "status",
            "--home",
            str(self.home),
        )

        self.assertEqual(exit_code, 1, stderr)
        self.assertIn("Gateway ready: no", stdout)
        self.assertIn("- channels: telegram", stdout)
        self.assertIn("- providers: openai", stdout)
        self.assertIn(
            "- provider-runtime: degraded Provider 'openai' is missing secret value for env ref 'MISSING_OPENAI_KEY'.",
            stdout,
        )
        self.assertIn("- provider-execution: ok openai:direct_http", stdout)
        self.assertIn("- oauth-maintenance: ok no oauth providers configured", stdout)
        self.assertIn(
            "- provider=openai method=api_key_env status=pending_secret transport=direct_http exec_ready=yes dependency=direct_http",
            stdout,
        )

    def test_gateway_status_surfaces_repair_hint_for_paused_channel(self) -> None:
        self.add_telegram_channel(bot_token="good-token")
        connect_exit, _, connect_stderr = self.run_cli(
            "auth",
            "connect",
            "openai",
            "--home",
            str(self.home),
            "--api-key",
            "openai-secret",
            "--model",
            "gpt-5.4",
        )
        self.assertEqual(connect_exit, 0, connect_stderr)

        pause_exit, _, pause_stderr = self.run_cli(
            "operator",
            "set-channel",
            "telegram",
            "paused",
            "--home",
            str(self.home),
        )
        self.assertEqual(pause_exit, 0, pause_stderr)

        gateway_exit, gateway_stdout, gateway_stderr = self.run_cli(
            "gateway",
            "status",
            "--home",
            str(self.home),
        )
        self.assertEqual(gateway_exit, 1, gateway_stderr)
        self.assertIn("- repair-hint: spark-intelligence operator set-channel telegram enabled", gateway_stdout)

        status_exit, status_stdout, status_stderr = self.run_cli(
            "status",
            "--home",
            str(self.home),
        )
        self.assertEqual(status_exit, 0, status_stderr)
        self.assertIn("- repair hint: spark-intelligence operator set-channel telegram enabled", status_stdout)

    def test_status_json_includes_auth_report(self) -> None:
        connect_exit, _, connect_stderr = self.run_cli(
            "auth",
            "connect",
            "anthropic",
            "--home",
            str(self.home),
            "--api-key",
            "anthropic-secret",
            "--model",
            "claude-opus-4-6",
        )
        self.assertEqual(connect_exit, 0, connect_stderr)

        exit_code, stdout, stderr = self.run_cli(
            "status",
            "--home",
            str(self.home),
            "--json",
        )

        self.assertEqual(exit_code, 0, stderr)
        payload = json.loads(stdout)
        self.assertIn("auth", payload)
        self.assertTrue(payload["auth"]["ok"])
        self.assertEqual(payload["auth"]["default_provider"], "anthropic")
        self.assertEqual(payload["auth"]["providers"][0]["auth_profile_id"], "anthropic:default")
        self.assertIn("gateway", payload)
        self.assertTrue(payload["gateway"]["oauth_maintenance_ok"])
        self.assertEqual(payload["gateway"]["oauth_maintenance_detail"], "no oauth providers configured")
        self.assertTrue(payload["gateway"]["provider_runtime_ok"])
        self.assertEqual(payload["gateway"]["provider_runtime_detail"], "anthropic:anthropic:default:direct_http")
        self.assertTrue(payload["gateway"]["provider_execution_ok"])
        self.assertEqual(payload["gateway"]["provider_execution_detail"], "anthropic:direct_http")
        self.assertIn(
            "provider=anthropic method=api_key_env status=active transport=direct_http exec_ready=yes dependency=direct_http",
            payload["gateway"]["provider_lines"],
        )

    def test_gateway_status_surfaces_codex_transport_and_oauth_maintenance_degradation(self) -> None:
        self.add_telegram_channel()
        start_exit, start_stdout, start_stderr = self.run_cli(
            "auth",
            "login",
            "openai-codex",
            "--home",
            str(self.home),
            "--json",
        )
        self.assertEqual(start_exit, 0, start_stderr)
        start_payload = json.loads(start_stdout)
        callback_url = (
            "http://127.0.0.1:1455/auth/callback"
            f"?state={start_payload['callback_state']}&code=test-oauth-code"
        )

        with patch(
            "spark_intelligence.auth.service.exchange_oauth_authorization_code",
            return_value={
                "access_token": "oauth-access-token",
                "refresh_token": "oauth-refresh-token",
                "expires_in": 60,
            },
        ):
            complete_exit, _, complete_stderr = self.run_cli(
                "auth",
                "login",
                "openai-codex",
                "--home",
                str(self.home),
                "--callback-url",
                callback_url,
            )
        self.assertEqual(complete_exit, 0, complete_stderr)

        exit_code, stdout, stderr = self.run_cli(
            "gateway",
            "status",
            "--home",
            str(self.home),
        )

        self.assertEqual(exit_code, 1, stderr)
        self.assertIn("- provider-runtime: ok openai-codex:openai-codex:default:external_cli_wrapper", stdout)
        self.assertIn("- provider-execution: degraded openai-codex:external_cli_wrapper:researcher_disabled", stdout)
        self.assertIn("- oauth-maintenance: degraded oauth maintenance has never run; expiring_soon=openai-codex", stdout)
        self.assertIn("- repair-hint: spark-intelligence jobs tick", stdout)
        self.assertIn("- repair-hint: spark-intelligence researcher status", stdout)
        self.assertIn(
            "- provider=openai-codex method=oauth status=expiring_soon transport=external_cli_wrapper exec_ready=no dependency=researcher_disabled",
            stdout,
        )

        status_exit, status_stdout, status_stderr = self.run_cli(
            "status",
            "--home",
            str(self.home),
        )
        self.assertEqual(status_exit, 1, status_stderr)
        self.assertIn("- oauth maintenance detail: oauth maintenance has never run; expiring_soon=openai-codex", status_stdout)
        self.assertIn("- repair hint: spark-intelligence jobs tick", status_stdout)
        self.assertIn("- repair hint: spark-intelligence researcher status", status_stdout)

    def test_gateway_start_fails_closed_when_runtime_provider_is_unresolved(self) -> None:
        self.add_telegram_channel(bot_token="good-token")
        connect_exit, _, connect_stderr = self.run_cli(
            "auth",
            "connect",
            "openai",
            "--home",
            str(self.home),
            "--api-key-env",
            "MISSING_OPENAI_KEY",
            "--model",
            "gpt-5.4",
        )
        self.assertEqual(connect_exit, 0, connect_stderr)

        exit_code, stdout, stderr = self.run_cli(
            "gateway",
            "start",
            "--home",
            str(self.home),
            "--once",
        )

        self.assertEqual(exit_code, 1, stderr)
        self.assertIn("Provider runtime readiness is degraded. Gateway did not start polling.", stdout)
        self.assertNotIn("Telegram bot authenticated:", stdout)

    def test_gateway_start_fails_closed_when_provider_execution_is_unavailable(self) -> None:
        self.add_telegram_channel(bot_token="good-token")
        start_exit, start_stdout, start_stderr = self.run_cli(
            "auth",
            "login",
            "openai-codex",
            "--home",
            str(self.home),
            "--json",
        )
        self.assertEqual(start_exit, 0, start_stderr)
        start_payload = json.loads(start_stdout)
        callback_url = (
            "http://127.0.0.1:1455/auth/callback"
            f"?state={start_payload['callback_state']}&code=test-oauth-code"
        )

        with patch(
            "spark_intelligence.auth.service.exchange_oauth_authorization_code",
            return_value={
                "access_token": "oauth-access-token",
                "refresh_token": "oauth-refresh-token",
                "expires_in": 3600,
            },
        ):
            complete_exit, _, complete_stderr = self.run_cli(
                "auth",
                "login",
                "openai-codex",
                "--home",
                str(self.home),
                "--callback-url",
                callback_url,
            )
        self.assertEqual(complete_exit, 0, complete_stderr)

        exit_code, stdout, stderr = self.run_cli(
            "gateway",
            "start",
            "--home",
            str(self.home),
            "--once",
        )

        self.assertEqual(exit_code, 1, stderr)
        self.assertIn("Provider execution readiness is degraded. Gateway did not start polling.", stdout)
        self.assertNotIn("Telegram bot authenticated:", stdout)

    def test_gateway_start_surfaces_repair_hint_for_paused_channel(self) -> None:
        self.add_telegram_channel(bot_token="good-token")
        connect_exit, _, connect_stderr = self.run_cli(
            "auth",
            "connect",
            "openai",
            "--home",
            str(self.home),
            "--api-key",
            "openai-secret",
            "--model",
            "gpt-5.4",
        )
        self.assertEqual(connect_exit, 0, connect_stderr)

        pause_exit, _, pause_stderr = self.run_cli(
            "operator",
            "set-channel",
            "telegram",
            "paused",
            "--home",
            str(self.home),
        )
        self.assertEqual(pause_exit, 0, pause_stderr)

        exit_code, stdout, stderr = self.run_cli(
            "gateway",
            "start",
            "--home",
            str(self.home),
            "--once",
        )

        self.assertEqual(exit_code, 0, stderr)
        self.assertIn("Telegram adapter is paused by operator. Gateway did not start polling.", stdout)
        self.assertIn("- repair-hint: spark-intelligence operator set-channel telegram enabled", stdout)
        self.assertNotIn("Telegram bot authenticated:", stdout)

    def test_doctor_degrades_when_codex_wrapper_transport_lacks_researcher_bridge(self) -> None:
        start_exit, start_stdout, start_stderr = self.run_cli(
            "auth",
            "login",
            "openai-codex",
            "--home",
            str(self.home),
            "--json",
        )
        self.assertEqual(start_exit, 0, start_stderr)
        start_payload = json.loads(start_stdout)
        callback_url = (
            "http://127.0.0.1:1455/auth/callback"
            f"?state={start_payload['callback_state']}&code=test-oauth-code"
        )

        with patch(
            "spark_intelligence.auth.service.exchange_oauth_authorization_code",
            return_value={
                "access_token": "oauth-access-token",
                "refresh_token": "oauth-refresh-token",
                "expires_in": 3600,
            },
        ):
            complete_exit, _, complete_stderr = self.run_cli(
                "auth",
                "login",
                "openai-codex",
                "--home",
                str(self.home),
                "--callback-url",
                callback_url,
            )
        self.assertEqual(complete_exit, 0, complete_stderr)

        doctor_exit, doctor_stdout, doctor_stderr = self.run_cli(
            "doctor",
            "--home",
            str(self.home),
        )

        self.assertEqual(doctor_exit, 1, doctor_stderr)
        self.assertIn("[fail] provider-execution: openai-codex:external_cli_wrapper:researcher_disabled", doctor_stdout)
        self.assertIn("spark-intelligence status", doctor_stdout)
        self.assertIn("spark-intelligence operator security", doctor_stdout)

    def test_setup_with_swarm_access_token_persists_named_env_ref(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            home = Path(tempdir)
            env_key = "CUSTOM_SWARM_TOKEN"

            exit_code, stdout, stderr = self.run_cli(
                "setup",
                "--home",
                str(home),
                "--swarm-api-url",
                "https://swarm.example",
                "--swarm-workspace-id",
                "ws-test",
                "--swarm-access-token",
                "secret-token",
                "--swarm-access-token-env",
                env_key,
            )

            self.assertEqual(exit_code, 0, stderr)
            self.assertIn("Setup integrations:", stdout)
            config_manager = ConfigManager.from_home(str(home))
            self.assertEqual(config_manager.read_env_map()[env_key], "secret-token")
            self.assertEqual(config_manager.get_path("spark.swarm.api_url"), "https://swarm.example")
            self.assertEqual(config_manager.get_path("spark.swarm.workspace_id"), "ws-test")
            self.assertEqual(config_manager.get_path("spark.swarm.access_token_env"), env_key)

    def test_telegram_onboard_preserves_existing_allowlist_and_pairing_mode_on_token_rotation(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"], bot_token="old-token")

        with patch(
            "spark_intelligence.cli.inspect_telegram_bot_token",
            return_value=TelegramBotProfile(
                bot_id="42",
                username="sparkbot",
                first_name="Spark Bot",
                can_join_groups=True,
                can_read_all_group_messages=False,
                supports_inline_queries=False,
            ),
        ):
            exit_code, stdout, stderr = self.run_cli(
                "channel",
                "telegram-onboard",
                "--home",
                str(self.home),
                "--bot-token",
                "new-token",
            )

        self.assertEqual(exit_code, 0, stderr)
        config_manager = ConfigManager.from_home(str(self.home))
        record = config_manager.get_path("channels.records.telegram")
        self.assertEqual(record["pairing_mode"], "allowlist")
        self.assertEqual(record["allowed_users"], ["111"])
        self.assertEqual(config_manager.read_env_map()["TELEGRAM_BOT_TOKEN"], "new-token")
        self.assertEqual(record["status"], "enabled")
        self.assertIn("Configured channel 'telegram' with pairing mode 'allowlist' status 'enabled'.", stdout)

    def test_telegram_onboard_preserves_existing_channel_status_on_token_rotation(self) -> None:
        self.add_telegram_channel(pairing_mode="pairing", bot_token="old-token")
        status_exit, _, status_stderr = self.run_cli(
            "operator",
            "set-channel",
            "telegram",
            "paused",
            "--home",
            str(self.home),
        )
        self.assertEqual(status_exit, 0, status_stderr)

        with patch(
            "spark_intelligence.cli.inspect_telegram_bot_token",
            return_value=TelegramBotProfile(
                bot_id="42",
                username="sparkbot",
                first_name="Spark Bot",
                can_join_groups=True,
                can_read_all_group_messages=False,
                supports_inline_queries=False,
            ),
        ):
            exit_code, stdout, stderr = self.run_cli(
                "channel",
                "telegram-onboard",
                "--home",
                str(self.home),
                "--bot-token",
                "new-token",
            )

        self.assertEqual(exit_code, 0, stderr)
        config_manager = ConfigManager.from_home(str(self.home))
        record = config_manager.get_path("channels.records.telegram")
        self.assertEqual(record["status"], "paused")
        self.assertEqual(config_manager.read_env_map()["TELEGRAM_BOT_TOKEN"], "new-token")
        self.assertIn("Configured channel 'telegram' with pairing mode 'pairing' status 'paused'.", stdout)

    def test_channel_add_preserves_existing_auth_ref_and_status_without_new_token(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"], bot_token="old-token")
        status_exit, _, status_stderr = self.run_cli(
            "operator",
            "set-channel",
            "telegram",
            "paused",
            "--home",
            str(self.home),
        )
        self.assertEqual(status_exit, 0, status_stderr)

        exit_code, stdout, stderr = self.run_cli(
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
        config_manager = ConfigManager.from_home(str(self.home))
        record = config_manager.get_path("channels.records.telegram")
        self.assertEqual(record["auth_ref"], "TELEGRAM_BOT_TOKEN")
        self.assertEqual(record["status"], "paused")
        self.assertEqual(config_manager.read_env_map()["TELEGRAM_BOT_TOKEN"], "old-token")
        with self.state_db.connect() as conn:
            row = conn.execute(
                "SELECT status, auth_ref FROM channel_installations WHERE channel_id = 'telegram' LIMIT 1"
            ).fetchone()
        self.assertEqual(row["status"], "paused")
        self.assertEqual(row["auth_ref"], "TELEGRAM_BOT_TOKEN")
        self.assertIn("Configured channel 'telegram' with pairing mode 'allowlist' status 'paused'.", stdout)

    def test_channel_add_preserves_existing_allowlist_on_token_rotation(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"], bot_token="old-token")

        with patch(
            "spark_intelligence.cli.inspect_telegram_bot_token",
            return_value=TelegramBotProfile(
                bot_id="42",
                username="sparkbot",
                first_name="Spark Bot",
                can_join_groups=True,
                can_read_all_group_messages=False,
                supports_inline_queries=False,
            ),
        ):
            exit_code, stdout, stderr = self.run_cli(
                "channel",
                "add",
                "telegram",
                "--home",
                str(self.home),
                "--bot-token",
                "new-token",
            )

        self.assertEqual(exit_code, 0, stderr)
        config_manager = ConfigManager.from_home(str(self.home))
        record = config_manager.get_path("channels.records.telegram")
        self.assertEqual(record["pairing_mode"], "allowlist")
        self.assertEqual(record["allowed_users"], ["111"])
        self.assertEqual(record["status"], "enabled")
        self.assertEqual(config_manager.read_env_map()["TELEGRAM_BOT_TOKEN"], "new-token")
        self.assertIn("Configured channel 'telegram' with pairing mode 'allowlist' status 'enabled'.", stdout)

    def test_channel_add_can_clear_existing_allowlist_explicitly(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111", "222"], bot_token="old-token")

        exit_code, stdout, stderr = self.run_cli(
            "channel",
            "add",
            "telegram",
            "--home",
            str(self.home),
            "--clear-allowed-users",
            "--pairing-mode",
            "allowlist",
        )

        self.assertEqual(exit_code, 0, stderr)
        config_manager = ConfigManager.from_home(str(self.home))
        record = config_manager.get_path("channels.records.telegram")
        self.assertEqual(record["allowed_users"], [])
        self.assertEqual(record["pairing_mode"], "allowlist")
        self.assertIn("Configured channel 'telegram' with pairing mode 'allowlist' status 'enabled'.", stdout)

    def test_telegram_onboard_can_clear_existing_allowlist_explicitly(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111", "222"], bot_token="old-token")

        with patch(
            "spark_intelligence.cli.inspect_telegram_bot_token",
            return_value=TelegramBotProfile(
                bot_id="42",
                username="sparkbot",
                first_name="Spark Bot",
                can_join_groups=True,
                can_read_all_group_messages=False,
                supports_inline_queries=False,
            ),
        ):
            exit_code, stdout, stderr = self.run_cli(
                "channel",
                "telegram-onboard",
                "--home",
                str(self.home),
                "--bot-token",
                "new-token",
                "--clear-allowed-users",
            )

        self.assertEqual(exit_code, 0, stderr)
        config_manager = ConfigManager.from_home(str(self.home))
        record = config_manager.get_path("channels.records.telegram")
        self.assertEqual(record["allowed_users"], [])
        self.assertEqual(config_manager.read_env_map()["TELEGRAM_BOT_TOKEN"], "new-token")
        self.assertIn("Configured channel 'telegram' with pairing mode 'allowlist' status 'enabled'.", stdout)

    def test_channel_add_preserves_existing_status_and_auth_for_discord(self) -> None:
        setup_exit, _, setup_stderr = self.run_cli(
            "channel",
            "add",
            "discord",
            "--home",
            str(self.home),
            "--bot-token",
            "discord-old-token",
            "--allowed-user",
            "111",
            "--pairing-mode",
            "allowlist",
        )
        self.assertEqual(setup_exit, 0, setup_stderr)

        status_exit, _, status_stderr = self.run_cli(
            "operator",
            "set-channel",
            "discord",
            "paused",
            "--home",
            str(self.home),
        )
        self.assertEqual(status_exit, 0, status_stderr)

        exit_code, stdout, stderr = self.run_cli(
            "channel",
            "add",
            "discord",
            "--home",
            str(self.home),
        )

        self.assertEqual(exit_code, 0, stderr)
        config_manager = ConfigManager.from_home(str(self.home))
        record = config_manager.get_path("channels.records.discord")
        self.assertEqual(record["pairing_mode"], "allowlist")
        self.assertEqual(record["allowed_users"], ["111"])
        self.assertEqual(record["status"], "paused")
        self.assertEqual(record["auth_ref"], "DISCORD_BOT_TOKEN")
        self.assertEqual(config_manager.read_env_map()["DISCORD_BOT_TOKEN"], "discord-old-token")
        self.assertIn("Configured channel 'discord' with pairing mode 'allowlist' status 'paused'.", stdout)

    def test_channel_add_can_clear_existing_allowlist_explicitly_for_discord(self) -> None:
        setup_exit, _, setup_stderr = self.run_cli(
            "channel",
            "add",
            "discord",
            "--home",
            str(self.home),
            "--bot-token",
            "discord-old-token",
            "--allowed-user",
            "111",
            "--allowed-user",
            "222",
            "--pairing-mode",
            "allowlist",
        )
        self.assertEqual(setup_exit, 0, setup_stderr)

        exit_code, stdout, stderr = self.run_cli(
            "channel",
            "add",
            "discord",
            "--home",
            str(self.home),
            "--clear-allowed-users",
            "--pairing-mode",
            "allowlist",
        )

        self.assertEqual(exit_code, 0, stderr)
        config_manager = ConfigManager.from_home(str(self.home))
        record = config_manager.get_path("channels.records.discord")
        self.assertEqual(record["allowed_users"], [])
        self.assertEqual(record["pairing_mode"], "allowlist")
        self.assertEqual(record["auth_ref"], "DISCORD_BOT_TOKEN")
        self.assertIn("Configured channel 'discord' with pairing mode 'allowlist' status 'enabled'.", stdout)

    def test_channel_add_persists_discord_webhook_secret_ref(self) -> None:
        exit_code, stdout, stderr = self.run_cli(
            "channel",
            "add",
            "discord",
            "--home",
            str(self.home),
            "--allow-legacy-message-webhook",
            "--webhook-secret",
            "discord-webhook-secret",
        )

        self.assertEqual(exit_code, 0, stderr)
        config_manager = ConfigManager.from_home(str(self.home))
        record = config_manager.get_path("channels.records.discord")
        self.assertEqual(record["webhook_auth_ref"], "DISCORD_WEBHOOK_SECRET")
        self.assertTrue(record["allow_legacy_message_webhook"])
        self.assertEqual(config_manager.read_env_map()["DISCORD_WEBHOOK_SECRET"], "discord-webhook-secret")
        self.assertIn("Configured channel 'discord' with pairing mode 'pairing' status 'enabled'.", stdout)

    def test_channel_add_rejects_discord_webhook_secret_without_explicit_legacy_opt_in(self) -> None:
        exit_code, stdout, stderr = self.run_cli(
            "channel",
            "add",
            "discord",
            "--home",
            str(self.home),
            "--webhook-secret",
            "discord-webhook-secret",
        )

        self.assertEqual(exit_code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("Discord legacy message webhooks require --allow-legacy-message-webhook.", stderr)

    def test_channel_add_preserves_existing_discord_webhook_secret_ref(self) -> None:
        setup_exit, _, setup_stderr = self.run_cli(
            "channel",
            "add",
            "discord",
            "--home",
            str(self.home),
            "--allow-legacy-message-webhook",
            "--webhook-secret",
            "discord-webhook-secret",
            "--allowed-user",
            "111",
            "--pairing-mode",
            "allowlist",
        )
        self.assertEqual(setup_exit, 0, setup_stderr)

        exit_code, stdout, stderr = self.run_cli(
            "channel",
            "add",
            "discord",
            "--home",
            str(self.home),
        )

        self.assertEqual(exit_code, 0, stderr)
        config_manager = ConfigManager.from_home(str(self.home))
        record = config_manager.get_path("channels.records.discord")
        self.assertEqual(record["webhook_auth_ref"], "DISCORD_WEBHOOK_SECRET")
        self.assertTrue(record["allow_legacy_message_webhook"])
        self.assertEqual(config_manager.read_env_map()["DISCORD_WEBHOOK_SECRET"], "discord-webhook-secret")
        self.assertIn("Configured channel 'discord' with pairing mode 'allowlist' status 'enabled'.", stdout)

    def test_channel_add_can_disable_existing_discord_legacy_message_webhook(self) -> None:
        setup_exit, _, setup_stderr = self.run_cli(
            "channel",
            "add",
            "discord",
            "--home",
            str(self.home),
            "--allow-legacy-message-webhook",
            "--webhook-secret",
            "discord-webhook-secret",
        )
        self.assertEqual(setup_exit, 0, setup_stderr)

        exit_code, stdout, stderr = self.run_cli(
            "channel",
            "add",
            "discord",
            "--home",
            str(self.home),
            "--disable-legacy-message-webhook",
        )

        self.assertEqual(exit_code, 0, stderr)
        config_manager = ConfigManager.from_home(str(self.home))
        record = config_manager.get_path("channels.records.discord")
        self.assertFalse(record["allow_legacy_message_webhook"])
        self.assertIsNone(record["webhook_auth_ref"])
        self.assertIn("Configured channel 'discord' with pairing mode 'pairing' status 'enabled'.", stdout)

    def test_channel_add_persists_discord_interaction_public_key(self) -> None:
        exit_code, stdout, stderr = self.run_cli(
            "channel",
            "add",
            "discord",
            "--home",
            str(self.home),
            "--interaction-public-key",
            "abcdef123456",
        )

        self.assertEqual(exit_code, 0, stderr)
        config_manager = ConfigManager.from_home(str(self.home))
        record = config_manager.get_path("channels.records.discord")
        self.assertEqual(record["interaction_public_key"], "abcdef123456")
        self.assertIn("Configured channel 'discord' with pairing mode 'pairing' status 'enabled'.", stdout)

    def test_doctor_degrades_when_discord_has_no_ingress_mode(self) -> None:
        setup_exit, _, setup_stderr = self.run_cli(
            "channel",
            "add",
            "discord",
            "--home",
            str(self.home),
            "--bot-token",
            "discord-token",
        )
        self.assertEqual(setup_exit, 0, setup_stderr)

        doctor_exit, doctor_stdout, doctor_stderr = self.run_cli(
            "doctor",
            "--home",
            str(self.home),
        )

        self.assertEqual(doctor_exit, 1, doctor_stderr)
        self.assertIn(
            "[fail] discord-runtime: no signed interaction public key configured and legacy message webhook compatibility is disabled",
            doctor_stdout,
        )

    def test_doctor_reports_discord_legacy_ingress_mode(self) -> None:
        setup_exit, _, setup_stderr = self.run_cli(
            "channel",
            "add",
            "discord",
            "--home",
            str(self.home),
            "--allow-legacy-message-webhook",
            "--webhook-secret",
            "discord-webhook-secret",
        )
        self.assertEqual(setup_exit, 0, setup_stderr)

        doctor_exit, doctor_stdout, doctor_stderr = self.run_cli(
            "doctor",
            "--home",
            str(self.home),
        )

        self.assertEqual(doctor_exit, 0, doctor_stderr)
        self.assertIn(
            "[ok] discord-runtime: status=enabled pairing_mode=pairing auth_ref=missing allowed_users=0 ingress=legacy_message_webhook webhook_auth_ref=DISCORD_WEBHOOK_SECRET",
            doctor_stdout,
        )

    def test_gateway_status_reports_signed_discord_ingress_mode(self) -> None:
        setup_exit, _, setup_stderr = self.run_cli(
            "channel",
            "add",
            "discord",
            "--home",
            str(self.home),
            "--interaction-public-key",
            "abcdef123456",
        )
        self.assertEqual(setup_exit, 0, setup_stderr)

        exit_code, stdout, stderr = self.run_cli(
            "gateway",
            "status",
            "--home",
            str(self.home),
        )

        self.assertEqual(exit_code, 1, stderr)
        self.assertIn(
            "- discord: status=enabled pairing_mode=pairing auth_ref=missing allowed_users=0 ingress=signed_interactions",
            stdout,
        )

    def test_doctor_degrades_when_whatsapp_has_no_webhook_contract(self) -> None:
        setup_exit, _, setup_stderr = self.run_cli(
            "channel",
            "add",
            "whatsapp",
            "--home",
            str(self.home),
            "--bot-token",
            "whatsapp-token",
        )
        self.assertEqual(setup_exit, 0, setup_stderr)

        doctor_exit, doctor_stdout, doctor_stderr = self.run_cli(
            "doctor",
            "--home",
            str(self.home),
        )

        self.assertEqual(doctor_exit, 1, doctor_stderr)
        self.assertIn(
            "[fail] whatsapp-runtime: status=enabled pairing_mode=pairing auth_ref=WHATSAPP_BOT_TOKEN allowed_users=0 ingress=missing",
            doctor_stdout,
        )

    def test_doctor_reports_whatsapp_meta_webhook_ingress_mode(self) -> None:
        setup_exit, _, setup_stderr = self.run_cli(
            "channel",
            "add",
            "whatsapp",
            "--home",
            str(self.home),
            "--bot-token",
            "whatsapp-token",
            "--webhook-secret",
            "whatsapp-webhook-secret",
            "--webhook-verify-token",
            "whatsapp-verify-token",
        )
        self.assertEqual(setup_exit, 0, setup_stderr)

        doctor_exit, doctor_stdout, doctor_stderr = self.run_cli(
            "doctor",
            "--home",
            str(self.home),
        )

        self.assertEqual(doctor_exit, 0, doctor_stderr)
        self.assertIn(
            "[ok] whatsapp-runtime: status=enabled pairing_mode=pairing auth_ref=WHATSAPP_BOT_TOKEN allowed_users=0 ingress=meta_webhook webhook_auth_ref=WHATSAPP_WEBHOOK_SECRET webhook_verify_token_ref=WHATSAPP_WEBHOOK_VERIFY_TOKEN",
            doctor_stdout,
        )

    def test_gateway_status_reports_whatsapp_ingress_mode(self) -> None:
        setup_exit, _, setup_stderr = self.run_cli(
            "channel",
            "add",
            "whatsapp",
            "--home",
            str(self.home),
            "--bot-token",
            "whatsapp-token",
            "--webhook-secret",
            "whatsapp-webhook-secret",
            "--webhook-verify-token",
            "whatsapp-verify-token",
        )
        self.assertEqual(setup_exit, 0, setup_stderr)

        exit_code, stdout, stderr = self.run_cli(
            "gateway",
            "status",
            "--home",
            str(self.home),
        )

        self.assertEqual(exit_code, 1, stderr)
        self.assertIn(
            "- whatsapp: status=enabled pairing_mode=pairing auth_ref=WHATSAPP_BOT_TOKEN allowed_users=0 ingress=meta_webhook",
            stdout,
        )

    def test_channel_add_persists_whatsapp_verify_token_ref(self) -> None:
        exit_code, stdout, stderr = self.run_cli(
            "channel",
            "add",
            "whatsapp",
            "--home",
            str(self.home),
            "--bot-token",
            "whatsapp-token",
            "--webhook-secret",
            "whatsapp-webhook-secret",
            "--webhook-verify-token",
            "whatsapp-verify-token",
        )

        self.assertEqual(exit_code, 0, stderr)
        config_manager = ConfigManager.from_home(str(self.home))
        record = config_manager.get_path("channels.records.whatsapp")
        self.assertEqual(record["webhook_auth_ref"], "WHATSAPP_WEBHOOK_SECRET")
        self.assertEqual(record["webhook_verify_token_ref"], "WHATSAPP_WEBHOOK_VERIFY_TOKEN")
        env_map = config_manager.read_env_map()
        self.assertEqual(env_map["WHATSAPP_WEBHOOK_SECRET"], "whatsapp-webhook-secret")
        self.assertEqual(env_map["WHATSAPP_WEBHOOK_VERIFY_TOKEN"], "whatsapp-verify-token")
        self.assertIn("Configured channel 'whatsapp' with pairing mode 'pairing' status 'enabled'.", stdout)
