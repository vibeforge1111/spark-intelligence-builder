from __future__ import annotations

import unittest
import urllib.error
from unittest.mock import patch

from spark_intelligence.auth.service import exchange_oauth_refresh_token


class AuthServiceNetworkErrorTests(unittest.TestCase):
    def test_exchange_oauth_refresh_token_surfaces_actionable_network_error(self) -> None:
        with patch(
            "urllib.request.urlopen",
            side_effect=urllib.error.URLError("connection refused"),
        ):
            with self.assertRaises(RuntimeError) as ctx:
                exchange_oauth_refresh_token(provider="openai-codex", refresh_token="rt-test")

        message = str(ctx.exception)
        self.assertIn("OAuth refresh for 'openai-codex' failed", message)
        self.assertIn("token", message.lower())
        self.assertIn("network", message.lower())

