from __future__ import annotations

import unittest

from spark_intelligence.auth.providers import PROVIDER_REGISTRY, get_provider_spec


class AuthProviderErrorTests(unittest.TestCase):
    def test_unknown_provider_error_lists_known_providers(self) -> None:
        with self.assertRaises(ValueError) as exc:
            get_provider_spec("antropic")

        message = str(exc.exception)
        self.assertIn("Unknown provider 'antropic'.", message)
        for provider_id in PROVIDER_REGISTRY:
            self.assertIn(provider_id, message)


if __name__ == "__main__":
    unittest.main()
