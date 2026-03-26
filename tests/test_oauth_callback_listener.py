from __future__ import annotations

import socket
import threading
import urllib.request
from time import sleep

from spark_intelligence.gateway.oauth_callback import listen_for_oauth_callback

from tests.test_support import SparkTestCase


class OAuthCallbackListenerTests(SparkTestCase):
    def test_listener_captures_loopback_callback(self) -> None:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]

        redirect_uri = f"http://127.0.0.1:{port}/auth/callback"

        def trigger_request() -> None:
            sleep(0.2)
            with urllib.request.urlopen(f"{redirect_uri}?state=test-state&code=test-code", timeout=5) as response:
                response.read()

        thread = threading.Thread(target=trigger_request, daemon=True)
        thread.start()
        capture = listen_for_oauth_callback(
            redirect_uri=redirect_uri,
            owner="auth:test",
            timeout_seconds=5,
        )
        thread.join(timeout=5)

        self.assertEqual(capture.path, "/auth/callback")
        self.assertEqual(capture.query, "state=test-state&code=test-code")
        self.assertEqual(capture.callback_url, f"{redirect_uri}?state=test-state&code=test-code")

    def test_listener_rejects_non_loopback_redirect_uri(self) -> None:
        with self.assertRaisesRegex(ValueError, "loopback"):
            listen_for_oauth_callback(
                redirect_uri="http://example.com/auth/callback",
                owner="auth:test",
                timeout_seconds=1,
            )
