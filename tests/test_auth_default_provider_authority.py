from __future__ import annotations

from spark_intelligence.auth.service import connect_provider, start_oauth_login

from tests.test_support import SparkTestCase


class AuthDefaultProviderAuthorityTests(SparkTestCase):
    def test_pending_static_profile_does_not_become_default_before_active_provider(self) -> None:
        connect_provider(
            config_manager=self.config_manager,
            state_db=self.state_db,
            provider="openrouter",
            api_key=None,
            api_key_env="MISSING_OPENROUTER_KEY",
            model=None,
            base_url=None,
        )

        pending_config = self.config_manager.load()
        self.assertIsNone(pending_config["providers"]["default_provider"])

        connect_provider(
            config_manager=self.config_manager,
            state_db=self.state_db,
            provider="openai",
            api_key="active-openai-secret",
            api_key_env=None,
            model=None,
            base_url=None,
        )

        active_config = self.config_manager.load()
        self.assertEqual(active_config["providers"]["default_provider"], "openai")
        self.assertIn("openrouter", active_config["providers"]["records"])

    def test_pending_oauth_profile_does_not_become_default(self) -> None:
        result = start_oauth_login(
            config_manager=self.config_manager,
            state_db=self.state_db,
            provider="openai-codex",
            redirect_uri=None,
        )

        self.assertEqual(result.status, "pending_oauth")
        config = self.config_manager.load()
        self.assertIsNone(config["providers"]["default_provider"])
        self.assertEqual(config["providers"]["records"]["openai-codex"]["status"], "pending_oauth")

    def test_active_static_profile_becomes_default_when_none_exists(self) -> None:
        connect_provider(
            config_manager=self.config_manager,
            state_db=self.state_db,
            provider="anthropic",
            api_key="active-anthropic-secret",
            api_key_env=None,
            model=None,
            base_url=None,
        )

        config = self.config_manager.load()
        self.assertEqual(config["providers"]["default_provider"], "anthropic")
