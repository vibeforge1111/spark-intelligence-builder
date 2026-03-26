from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

from spark_intelligence.channel.service import TelegramBotProfile
from spark_intelligence.config.loader import ConfigManager

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
