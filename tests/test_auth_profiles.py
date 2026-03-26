from __future__ import annotations

import json

from spark_intelligence.auth.runtime import build_auth_status_report, resolve_runtime_provider

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

    def test_build_auth_status_report_handles_no_providers(self) -> None:
        report = build_auth_status_report(
            config_manager=self.config_manager,
            state_db=self.state_db,
        )

        self.assertTrue(report.ok)
        self.assertEqual(report.default_provider, None)
        self.assertEqual(report.providers, [])
