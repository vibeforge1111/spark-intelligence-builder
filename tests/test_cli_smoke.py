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
        self.assertIn(
            "- provider=openai-codex method=oauth status=expiring_soon transport=external_cli_wrapper exec_ready=no dependency=researcher_disabled",
            stdout,
        )

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
