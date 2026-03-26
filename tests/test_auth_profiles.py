from __future__ import annotations

import json
from unittest.mock import patch

from spark_intelligence.auth.runtime import build_auth_status_report, resolve_runtime_provider
from spark_intelligence.gateway.oauth_callback import OAuthCallbackCapture

from tests.test_support import SparkTestCase


class AuthProfileTests(SparkTestCase):
    def test_auth_connect_creates_default_auth_profile_and_static_env_ref(self) -> None:
        exit_code, stdout, stderr = self.run_cli(
            "auth",
            "connect",
            "openai",
            "--home",
            str(self.home),
            "--api-key",
            "sk-test-openai",
            "--model",
            "gpt-5.4",
        )

        self.assertEqual(exit_code, 0, stderr)
        self.assertIn("auth profile 'openai:default'", stdout)

        config = self.config_manager.load()
        self.assertEqual(config["providers"]["default_provider"], "openai")
        self.assertEqual(config["providers"]["records"]["openai"]["default_auth_profile_id"], "openai:default")
        self.assertEqual(config["providers"]["records"]["openai"]["api_key_env"], "OPENAI_API_KEY")

        with self.state_db.connect() as conn:
            provider_row = conn.execute(
                """
                SELECT provider_id, default_auth_profile_id, api_key_env
                FROM provider_records
                WHERE provider_id = 'openai'
                LIMIT 1
                """
            ).fetchone()
            profile_row = conn.execute(
                """
                SELECT provider_id, auth_method, status, is_default
                FROM auth_profiles
                WHERE auth_profile_id = 'openai:default'
                LIMIT 1
                """
            ).fetchone()
            ref_row = conn.execute(
                """
                SELECT ref_source, ref_provider, ref_id
                FROM auth_profile_static_refs
                WHERE auth_profile_id = 'openai:default'
                LIMIT 1
                """
            ).fetchone()

        self.assertEqual(provider_row["default_auth_profile_id"], "openai:default")
        self.assertEqual(provider_row["api_key_env"], "OPENAI_API_KEY")
        self.assertEqual(profile_row["provider_id"], "openai")
        self.assertEqual(profile_row["auth_method"], "api_key_env")
        self.assertEqual(profile_row["status"], "active")
        self.assertEqual(profile_row["is_default"], 1)
        self.assertEqual(ref_row["ref_source"], "env")
        self.assertEqual(ref_row["ref_provider"], "default")
        self.assertEqual(ref_row["ref_id"], "OPENAI_API_KEY")

    def test_auth_status_reports_secret_readiness_in_json(self) -> None:
        connect_exit, _, connect_stderr = self.run_cli(
            "auth",
            "connect",
            "openrouter",
            "--home",
            str(self.home),
            "--api-key-env",
            "WORK_OPENROUTER_KEY",
            "--model",
            "anthropic/claude-3.7-sonnet",
        )
        self.assertEqual(connect_exit, 0, connect_stderr)

        status_exit, status_stdout, status_stderr = self.run_cli(
            "auth",
            "status",
            "--home",
            str(self.home),
            "--json",
        )

        self.assertEqual(status_exit, 1, status_stderr)
        payload = json.loads(status_stdout)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["default_provider"], "openrouter")
        self.assertEqual(payload["providers"][0]["provider_id"], "openrouter")
        self.assertEqual(payload["providers"][0]["auth_profile_id"], "openrouter:default")
        self.assertEqual(payload["providers"][0]["secret_ref"]["source"], "env")
        self.assertEqual(payload["providers"][0]["secret_ref"]["id"], "WORK_OPENROUTER_KEY")
        self.assertFalse(payload["providers"][0]["secret_present"])
        self.assertEqual(payload["providers"][0]["status"], "pending_secret")

    def test_auth_providers_lists_api_key_and_oauth_options(self) -> None:
        exit_code, stdout, stderr = self.run_cli(
            "auth",
            "providers",
            "--json",
        )

        self.assertEqual(exit_code, 0, stderr)
        payload = json.loads(stdout)
        provider_ids = [provider["id"] for provider in payload["providers"]]
        self.assertIn("openai", provider_ids)
        self.assertIn("openai-codex", provider_ids)
        openai_codex = next(provider for provider in payload["providers"] if provider["id"] == "openai-codex")
        self.assertEqual(openai_codex["auth_methods"], ["oauth"])
        self.assertEqual(openai_codex["oauth_redirect_uri"], "http://127.0.0.1:1455/auth/callback")

    def test_resolve_runtime_provider_uses_default_profile_and_env_secret(self) -> None:
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
            "--base-url",
            "https://api.anthropic.com",
        )
        self.assertEqual(connect_exit, 0, connect_stderr)

        resolution = resolve_runtime_provider(
            config_manager=self.config_manager,
            state_db=self.state_db,
        )

        self.assertEqual(resolution.provider_id, "anthropic")
        self.assertEqual(resolution.provider_kind, "anthropic")
        self.assertEqual(resolution.auth_profile_id, "anthropic:default")
        self.assertEqual(resolution.auth_method, "api_key_env")
        self.assertEqual(resolution.api_mode, "anthropic_messages")
        self.assertEqual(resolution.base_url, "https://api.anthropic.com")
        self.assertEqual(resolution.default_model, "claude-opus-4-6")
        self.assertEqual(resolution.secret_ref.source, "env")
        self.assertEqual(resolution.secret_ref.ref_id, "ANTHROPIC_API_KEY")
        self.assertEqual(resolution.secret_value, "anthropic-secret")
        self.assertEqual(resolution.source, "config+env")

    def test_auth_login_start_creates_pending_oauth_profile_and_callback_state(self) -> None:
        exit_code, stdout, stderr = self.run_cli(
            "auth",
            "login",
            "openai-codex",
            "--home",
            str(self.home),
            "--json",
        )

        self.assertEqual(exit_code, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["provider_id"], "openai-codex")
        self.assertEqual(payload["auth_profile_id"], "openai-codex:default")
        self.assertEqual(payload["status"], "pending_oauth")
        self.assertIn("code_challenge=", payload["authorize_url"])
        self.assertIn("state=", payload["authorize_url"])

        config = self.config_manager.load()
        self.assertEqual(config["providers"]["records"]["openai-codex"]["default_auth_profile_id"], "openai-codex:default")
        self.assertEqual(config["providers"]["records"]["openai-codex"]["status"], "pending_oauth")

        with self.state_db.connect() as conn:
            profile_row = conn.execute(
                """
                SELECT provider_id, auth_method, status, is_default
                FROM auth_profiles
                WHERE auth_profile_id = 'openai-codex:default'
                LIMIT 1
                """
            ).fetchone()
            callback_row = conn.execute(
                """
                SELECT provider_id, auth_profile_id, flow_kind, status, redirect_uri, pkce_verifier
                FROM oauth_callback_states
                WHERE auth_profile_id = 'openai-codex:default'
                LIMIT 1
                """
            ).fetchone()

        self.assertEqual(profile_row["provider_id"], "openai-codex")
        self.assertEqual(profile_row["auth_method"], "oauth")
        self.assertEqual(profile_row["status"], "pending_oauth")
        self.assertEqual(profile_row["is_default"], 1)
        self.assertEqual(callback_row["provider_id"], "openai-codex")
        self.assertEqual(callback_row["flow_kind"], "oauth_login")
        self.assertEqual(callback_row["status"], "pending")
        self.assertEqual(callback_row["redirect_uri"], "http://127.0.0.1:1455/auth/callback")
        self.assertTrue(callback_row["pkce_verifier"])

    def test_auth_login_complete_persists_oauth_credentials_and_runtime_resolution(self) -> None:
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
                "scope": "openid profile",
            },
        ):
            complete_exit, complete_stdout, complete_stderr = self.run_cli(
                "auth",
                "login",
                "openai-codex",
                "--home",
                str(self.home),
                "--callback-url",
                callback_url,
                "--json",
            )

        self.assertEqual(complete_exit, 0, complete_stderr)
        payload = json.loads(complete_stdout)
        self.assertEqual(payload["provider_id"], "openai-codex")
        self.assertEqual(payload["auth_profile_id"], "openai-codex:default")
        self.assertEqual(payload["status"], "active")

        with self.state_db.connect() as conn:
            oauth_row = conn.execute(
                """
                SELECT issuer, scope, access_token_ciphertext, refresh_token_ciphertext, status
                FROM oauth_credentials
                WHERE auth_profile_id = 'openai-codex:default'
                LIMIT 1
                """
            ).fetchone()
            callback_row = conn.execute(
                """
                SELECT status, consumed_at
                FROM oauth_callback_states
                WHERE oauth_state = ?
                LIMIT 1
                """,
                (start_payload["callback_state"],),
            ).fetchone()

        self.assertEqual(oauth_row["issuer"], "https://auth.openai.com")
        self.assertEqual(oauth_row["scope"], "openid profile")
        self.assertEqual(oauth_row["access_token_ciphertext"], "oauth-access-token")
        self.assertEqual(oauth_row["refresh_token_ciphertext"], "oauth-refresh-token")
        self.assertEqual(oauth_row["status"], "active")
        self.assertEqual(callback_row["status"], "consumed")
        self.assertTrue(callback_row["consumed_at"])

        report = build_auth_status_report(
            config_manager=self.config_manager,
            state_db=self.state_db,
        )
        self.assertTrue(report.ok)
        self.assertEqual(report.providers[0].provider_id, "openai-codex")
        self.assertEqual(report.providers[0].auth_method, "oauth")
        self.assertEqual(report.providers[0].secret_ref.source, "oauth_store")
        self.assertEqual(report.providers[0].secret_ref.ref_id, "openai-codex:default")
        self.assertTrue(report.providers[0].secret_present)
        self.assertEqual(report.providers[0].status, "active")

        resolution = resolve_runtime_provider(
            config_manager=self.config_manager,
            state_db=self.state_db,
        )
        self.assertEqual(resolution.provider_id, "openai-codex")
        self.assertEqual(resolution.auth_method, "oauth")
        self.assertEqual(resolution.api_mode, "codex_responses")
        self.assertEqual(resolution.secret_ref.source, "oauth_store")
        self.assertEqual(resolution.secret_ref.ref_id, "openai-codex:default")
        self.assertEqual(resolution.secret_value, "oauth-access-token")
        self.assertEqual(resolution.source, "oauth_store")

    def test_auth_login_listen_completes_flow_via_callback_listener(self) -> None:
        def callback_capture(*, redirect_uri: str, owner: str, timeout_seconds: int = 120):
            with self.state_db.connect() as conn:
                row = conn.execute(
                    """
                    SELECT oauth_state
                    FROM oauth_callback_states
                    WHERE provider_id = 'openai-codex' AND status = 'pending'
                    ORDER BY created_at DESC
                    LIMIT 1
                    """
                ).fetchone()
            return OAuthCallbackCapture(
                callback_url=f"{redirect_uri}?state={row['oauth_state']}&code=listener-code",
                path="/auth/callback",
                query=f"state={row['oauth_state']}&code=listener-code",
            )

        with patch(
            "spark_intelligence.cli.listen_for_oauth_callback",
            side_effect=callback_capture,
        ), patch(
            "spark_intelligence.auth.service.exchange_oauth_authorization_code",
            return_value={
                "access_token": "oauth-access-token",
                "refresh_token": "oauth-refresh-token",
            },
        ):
            exit_code, stdout, stderr = self.run_cli(
                "auth",
                "login",
                "openai-codex",
                "--home",
                str(self.home),
                "--listen",
                "--json",
            )

        self.assertEqual(exit_code, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["start"]["provider_id"], "openai-codex")
        self.assertEqual(payload["callback"]["path"], "/auth/callback")
        self.assertEqual(payload["result"]["status"], "active")

    def test_auth_logout_revokes_oauth_credentials(self) -> None:
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

        logout_exit, logout_stdout, logout_stderr = self.run_cli(
            "auth",
            "logout",
            "openai-codex",
            "--home",
            str(self.home),
            "--json",
        )

        self.assertEqual(logout_exit, 0, logout_stderr)
        payload = json.loads(logout_stdout)
        self.assertEqual(payload["provider_id"], "openai-codex")
        self.assertEqual(payload["status"], "revoked")

        status_exit, status_stdout, status_stderr = self.run_cli(
            "auth",
            "status",
            "--home",
            str(self.home),
            "--json",
        )
        self.assertEqual(status_exit, 1, status_stderr)
        status_payload = json.loads(status_stdout)
        self.assertEqual(status_payload["providers"][0]["status"], "revoked")
        self.assertFalse(status_payload["providers"][0]["secret_present"])

        with self.assertRaisesRegex(RuntimeError, "no active OAuth access token"):
            resolve_runtime_provider(
                config_manager=self.config_manager,
                state_db=self.state_db,
            )

    def test_build_auth_status_report_handles_no_providers(self) -> None:
        report = build_auth_status_report(
            config_manager=self.config_manager,
            state_db=self.state_db,
        )

        self.assertTrue(report.ok)
        self.assertEqual(report.default_provider, None)
        self.assertEqual(report.providers, [])

    def test_doctor_degrades_when_provider_auth_profile_secret_is_missing(self) -> None:
        connect_exit, _, connect_stderr = self.run_cli(
            "auth",
            "connect",
            "openrouter",
            "--home",
            str(self.home),
            "--api-key-env",
            "MISSING_OPENROUTER_KEY",
            "--model",
            "anthropic/claude-3.7-sonnet",
        )
        self.assertEqual(connect_exit, 0, connect_stderr)

        doctor_exit, doctor_stdout, doctor_stderr = self.run_cli(
            "doctor",
            "--home",
            str(self.home),
        )

        self.assertEqual(doctor_exit, 1, doctor_stderr)
        self.assertIn("Doctor status: degraded", doctor_stdout)
        self.assertIn("[fail] provider-auth: openrouter:MISSING_OPENROUTER_KEY", doctor_stdout)
