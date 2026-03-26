from __future__ import annotations

import json
from unittest.mock import patch

from spark_intelligence.auth.runtime import build_auth_status_report, resolve_runtime_provider
from spark_intelligence.gateway.oauth_callback import GatewayOAuthCallbackResult, OAuthCallbackCapture

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
        self.assertEqual(openai_codex["execution_transport"], "external_cli_wrapper")
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
        self.assertEqual(resolution.execution_transport, "direct_http")
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
                "expires_in": 3600,
                "refresh_token_expires_in": 7200,
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
                SELECT issuer, scope, access_token_ciphertext, refresh_token_ciphertext, status, access_expires_at, refresh_expires_at
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
        self.assertTrue(oauth_row["access_expires_at"])
        self.assertTrue(oauth_row["refresh_expires_at"])
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
        self.assertTrue(report.providers[0].access_expires_at)
        self.assertTrue(report.providers[0].refresh_expires_at)

        resolution = resolve_runtime_provider(
            config_manager=self.config_manager,
            state_db=self.state_db,
        )
        self.assertEqual(resolution.provider_id, "openai-codex")
        self.assertEqual(resolution.auth_method, "oauth")
        self.assertEqual(resolution.api_mode, "codex_responses")
        self.assertEqual(resolution.execution_transport, "external_cli_wrapper")
        self.assertEqual(resolution.secret_ref.source, "oauth_store")
        self.assertEqual(resolution.secret_ref.ref_id, "openai-codex:default")
        self.assertEqual(resolution.secret_value, "oauth-access-token")
        self.assertEqual(resolution.source, "oauth_store")

    def test_auth_login_listen_completes_flow_via_callback_listener(self) -> None:
        with patch(
            "spark_intelligence.cli.serve_gateway_oauth_callback",
            return_value=GatewayOAuthCallbackResult(
                callback_url="http://127.0.0.1:1455/auth/callback?state=test-state&code=listener-code",
                path="/auth/callback",
                query="state=test-state&code=listener-code",
                provider_id="openai-codex",
                auth_profile_id="openai-codex:default",
                status="active",
                default_model="gpt-5-codex",
                base_url="https://api.openai.com/v1",
            ),
        ), patch(
            "spark_intelligence.auth.service.exchange_oauth_authorization_code",
            return_value={
                "access_token": "oauth-access-token",
                "refresh_token": "oauth-refresh-token",
                "expires_in": 3600,
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

    def test_gateway_oauth_callback_completes_pending_login_with_inferred_redirect(self) -> None:
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
            f"{start_payload['redirect_uri']}?state={start_payload['callback_state']}&code=gateway-code"
        )

        with patch(
            "spark_intelligence.gateway.oauth_callback._capture_oauth_callback",
            return_value=OAuthCallbackCapture(
                callback_url=callback_url,
                path="/auth/callback",
                query=f"state={start_payload['callback_state']}&code=gateway-code",
            ),
        ), patch(
            "spark_intelligence.auth.service.exchange_oauth_authorization_code",
            return_value={
                "access_token": "oauth-access-token",
                "refresh_token": "oauth-refresh-token",
                "expires_in": 3600,
            },
        ):
            exit_code, stdout, stderr = self.run_cli(
                "gateway",
                "oauth-callback",
                "--home",
                str(self.home),
                "--provider",
                "openai-codex",
                "--json",
            )

        self.assertEqual(exit_code, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["provider_id"], "openai-codex")
        self.assertEqual(payload["auth_profile_id"], "openai-codex:default")
        self.assertEqual(payload["status"], "active")

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

    def test_auth_refresh_updates_expiry_and_clears_refresh_error(self) -> None:
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
                "expires_in": 1,
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

        with patch(
            "spark_intelligence.auth.service.exchange_oauth_refresh_token",
            return_value={
                "access_token": "oauth-access-token-refreshed",
                "refresh_token": "oauth-refresh-token-rotated",
                "expires_in": 3600,
                "refresh_token_expires_in": 7200,
            },
        ):
            refresh_exit, refresh_stdout, refresh_stderr = self.run_cli(
                "auth",
                "refresh",
                "openai-codex",
                "--home",
                str(self.home),
                "--json",
            )

        self.assertEqual(refresh_exit, 0, refresh_stderr)
        payload = json.loads(refresh_stdout)
        self.assertEqual(payload["provider_id"], "openai-codex")
        self.assertEqual(payload["status"], "active")
        self.assertTrue(payload["access_expires_at"])
        self.assertTrue(payload["refresh_expires_at"])

        with self.state_db.connect() as conn:
            oauth_row = conn.execute(
                """
                SELECT access_token_ciphertext, refresh_token_ciphertext, last_refresh_at, last_refresh_error
                FROM oauth_credentials
                WHERE auth_profile_id = 'openai-codex:default'
                LIMIT 1
                """
            ).fetchone()

        self.assertEqual(oauth_row["access_token_ciphertext"], "oauth-access-token-refreshed")
        self.assertEqual(oauth_row["refresh_token_ciphertext"], "oauth-refresh-token-rotated")
        self.assertTrue(oauth_row["last_refresh_at"])
        self.assertEqual(oauth_row["last_refresh_error"], None)

    def test_auth_status_and_doctor_degrade_when_oauth_token_is_expired(self) -> None:
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
                "expires_in": 0,
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

        status_exit, status_stdout, status_stderr = self.run_cli(
            "auth",
            "status",
            "--home",
            str(self.home),
            "--json",
        )
        self.assertEqual(status_exit, 1, status_stderr)
        status_payload = json.loads(status_stdout)
        self.assertEqual(status_payload["providers"][0]["status"], "expired")
        self.assertTrue(status_payload["providers"][0]["token_expired"])
        self.assertFalse(status_payload["providers"][0]["secret_present"])

        doctor_exit, doctor_stdout, doctor_stderr = self.run_cli(
            "doctor",
            "--home",
            str(self.home),
        )
        self.assertEqual(doctor_exit, 1, doctor_stderr)
        self.assertIn("[fail] provider-auth: openai-codex:expired", doctor_stdout)
        self.assertIn("[fail] provider-runtime: Provider 'openai-codex' has an expired OAuth access token.", doctor_stdout)

        with self.assertRaisesRegex(RuntimeError, "expired OAuth access token"):
            resolve_runtime_provider(
                config_manager=self.config_manager,
                state_db=self.state_db,
            )

        inbox_exit, inbox_stdout, inbox_stderr = self.run_cli(
            "operator",
            "inbox",
            "--home",
            str(self.home),
            "--json",
        )
        self.assertEqual(inbox_exit, 0, inbox_stderr)
        inbox_payload = json.loads(inbox_stdout)
        self.assertEqual(inbox_payload["counts"]["auth_alerts"], 1)
        self.assertEqual(inbox_payload["auth"][0]["status"], "expired")
        self.assertEqual(
            inbox_payload["auth"][0]["recommended_command"],
            "spark-intelligence auth refresh openai-codex",
        )

    def test_auth_refresh_failure_records_error_and_returns_nonzero(self) -> None:
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

        with patch(
            "spark_intelligence.auth.service.exchange_oauth_refresh_token",
            side_effect=RuntimeError("refresh failed"),
        ):
            refresh_exit, refresh_stdout, refresh_stderr = self.run_cli(
                "auth",
                "refresh",
                "openai-codex",
                "--home",
                str(self.home),
            )

        self.assertEqual(refresh_exit, 1)
        self.assertEqual(refresh_stdout, "")
        self.assertIn("refresh failed", refresh_stderr)

        status_exit, status_stdout, status_stderr = self.run_cli(
            "auth",
            "status",
            "--home",
            str(self.home),
            "--json",
        )
        self.assertEqual(status_exit, 0, status_stderr)
        status_payload = json.loads(status_stdout)
        self.assertEqual(status_payload["providers"][0]["status"], "refresh_error")
        self.assertTrue(status_payload["providers"][0]["secret_present"])
        self.assertEqual(status_payload["providers"][0]["last_refresh_error"], "refresh failed")

        with self.state_db.connect() as conn:
            oauth_row = conn.execute(
                """
                SELECT last_refresh_at, last_refresh_error
                FROM oauth_credentials
                WHERE auth_profile_id = 'openai-codex:default'
                LIMIT 1
                """
            ).fetchone()
            event_row = conn.execute(
                """
                SELECT event_kind, detail
                FROM provider_runtime_events
                WHERE auth_profile_id = 'openai-codex:default'
                ORDER BY event_id DESC
                LIMIT 1
                """
            ).fetchone()

        self.assertTrue(oauth_row["last_refresh_at"])
        self.assertEqual(oauth_row["last_refresh_error"], "refresh failed")
        self.assertEqual(event_row["event_kind"], "oauth_refresh_failed")
        self.assertIn("refresh failed", event_row["detail"])

        security_exit, security_stdout, security_stderr = self.run_cli(
            "operator",
            "security",
            "--home",
            str(self.home),
            "--json",
        )
        self.assertEqual(security_exit, 0, security_stderr)
        security_payload = json.loads(security_stdout)
        self.assertEqual(security_payload["counts"]["auth_alerts"], 1)
        self.assertEqual(security_payload["auth_alerts"][0]["status"], "refresh_error")
        self.assertEqual(
            security_payload["auth_alerts"][0]["recommended_command"],
            "spark-intelligence auth refresh openai-codex",
        )

    def test_auth_status_surfaces_expiring_soon_and_operator_guidance(self) -> None:
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

        status_exit, status_stdout, status_stderr = self.run_cli(
            "auth",
            "status",
            "--home",
            str(self.home),
            "--json",
        )
        self.assertEqual(status_exit, 0, status_stderr)
        status_payload = json.loads(status_stdout)
        self.assertEqual(status_payload["providers"][0]["status"], "expiring_soon")
        self.assertTrue(status_payload["providers"][0]["token_expiring_soon"])

        inbox_exit, inbox_stdout, inbox_stderr = self.run_cli(
            "operator",
            "inbox",
            "--home",
            str(self.home),
            "--json",
        )
        self.assertEqual(inbox_exit, 0, inbox_stderr)
        inbox_payload = json.loads(inbox_stdout)
        self.assertEqual(inbox_payload["counts"]["auth_alerts"], 1)
        self.assertEqual(inbox_payload["auth"][0]["status"], "expiring_soon")
        self.assertEqual(
            inbox_payload["auth"][0]["recommended_command"],
            "spark-intelligence jobs tick",
        )

    def test_doctor_flags_stale_oauth_maintenance_when_token_is_expiring_soon(self) -> None:
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

        doctor_exit, doctor_stdout, doctor_stderr = self.run_cli(
            "doctor",
            "--home",
            str(self.home),
        )
        self.assertEqual(doctor_exit, 1, doctor_stderr)
        self.assertIn("[fail] oauth-maintenance:", doctor_stdout)
        self.assertIn("oauth maintenance has never run", doctor_stdout)
        self.assertIn("expiring_soon=openai-codex", doctor_stdout)

    def test_jobs_tick_refreshes_due_oauth_profile(self) -> None:
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

        with patch(
            "spark_intelligence.auth.service.exchange_oauth_refresh_token",
            return_value={
                "access_token": "oauth-access-token-refreshed",
                "refresh_token": "oauth-refresh-token-rotated",
                "expires_in": 3600,
                "refresh_token_expires_in": 7200,
            },
        ):
            tick_exit, tick_stdout, tick_stderr = self.run_cli(
                "jobs",
                "tick",
                "--home",
                str(self.home),
            )

        self.assertEqual(tick_exit, 0, tick_stderr)
        self.assertIn("auth:oauth-refresh-maintenance", tick_stdout)
        self.assertIn("refreshed=1", tick_stdout)

        list_exit, list_stdout, list_stderr = self.run_cli(
            "jobs",
            "list",
            "--home",
            str(self.home),
        )
        self.assertEqual(list_exit, 0, list_stderr)
        self.assertIn("auth:oauth-refresh-maintenance", list_stdout)
        self.assertIn("last_result=scanned=1 due=1 refreshed=1 failed=0 skipped=0", list_stdout)

        with self.state_db.connect() as conn:
            oauth_row = conn.execute(
                """
                SELECT access_token_ciphertext, refresh_token_ciphertext
                FROM oauth_credentials
                WHERE auth_profile_id = 'openai-codex:default'
                LIMIT 1
                """
            ).fetchone()
            event_row = conn.execute(
                """
                SELECT event_kind, detail
                FROM provider_runtime_events
                WHERE auth_profile_id = 'openai-codex:default'
                ORDER BY event_id DESC
                LIMIT 1
                """
            ).fetchone()
            job_row = conn.execute(
                """
                SELECT last_run_at, last_result
                FROM job_records
                WHERE job_id = 'auth:oauth-refresh-maintenance'
                LIMIT 1
                """
            ).fetchone()

        self.assertEqual(oauth_row["access_token_ciphertext"], "oauth-access-token-refreshed")
        self.assertEqual(oauth_row["refresh_token_ciphertext"], "oauth-refresh-token-rotated")
        self.assertEqual(event_row["event_kind"], "oauth_refresh_completed")
        self.assertIn('"trigger": "job"', event_row["detail"])
        self.assertTrue(job_row["last_run_at"])
        self.assertIn("refreshed=1", job_row["last_result"])

    def test_operator_security_surfaces_revoked_oauth_reconnect_guidance(self) -> None:
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

        logout_exit, _, logout_stderr = self.run_cli(
            "auth",
            "logout",
            "openai-codex",
            "--home",
            str(self.home),
        )
        self.assertEqual(logout_exit, 0, logout_stderr)

        security_exit, security_stdout, security_stderr = self.run_cli(
            "operator",
            "security",
            "--home",
            str(self.home),
            "--json",
        )
        self.assertEqual(security_exit, 0, security_stderr)
        payload = json.loads(security_stdout)
        self.assertEqual(payload["counts"]["auth_alerts"], 1)
        self.assertEqual(payload["auth_alerts"][0]["status"], "revoked")
        self.assertEqual(
            payload["auth_alerts"][0]["recommended_command"],
            "spark-intelligence auth login openai-codex --listen",
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
        self.assertIn(
            "[fail] provider-runtime: Provider 'openrouter' is missing secret value for env ref 'MISSING_OPENROUTER_KEY'.",
            doctor_stdout,
        )
