from __future__ import annotations

import socket
import threading
import urllib.request
from urllib.error import HTTPError
from time import sleep
from unittest.mock import patch

from spark_intelligence.auth.service import start_oauth_login
from spark_intelligence.gateway.oauth_callback import listen_for_oauth_callback, serve_gateway_oauth_callback

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

    def test_listener_ignores_malformed_callback_until_valid_one_arrives(self) -> None:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]

        redirect_uri = f"http://127.0.0.1:{port}/auth/callback"

        def trigger_requests() -> None:
            sleep(0.2)
            with self.assertRaises(HTTPError):
                urllib.request.urlopen(f"{redirect_uri}?code=missing-state", timeout=5)
            with urllib.request.urlopen(f"{redirect_uri}?state=test-state&code=test-code", timeout=5) as response:
                response.read()

        thread = threading.Thread(target=trigger_requests, daemon=True)
        thread.start()
        capture = listen_for_oauth_callback(
            redirect_uri=redirect_uri,
            owner="auth:test",
            timeout_seconds=5,
        )
        thread.join(timeout=5)

        self.assertEqual(capture.query, "state=test-state&code=test-code")

    def test_listener_rejects_non_loopback_redirect_uri(self) -> None:
        with self.assertRaisesRegex(ValueError, "loopback"):
            listen_for_oauth_callback(
                redirect_uri="http://example.com/auth/callback",
                owner="auth:test",
                timeout_seconds=1,
            )

    def test_gateway_callback_completes_pending_login(self) -> None:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]

        redirect_uri = f"http://127.0.0.1:{port}/auth/callback"
        start = start_oauth_login(
            config_manager=self.config_manager,
            state_db=self.state_db,
            provider="openai-codex",
            redirect_uri=redirect_uri,
        )

        def trigger_request() -> None:
            sleep(0.2)
            with urllib.request.urlopen(f"{redirect_uri}?state={start.callback_state}&code=test-code", timeout=5) as response:
                response.read()

        thread = threading.Thread(target=trigger_request, daemon=True)
        thread.start()
        with patch(
            "spark_intelligence.auth.service.exchange_oauth_authorization_code",
            return_value={
                "access_token": "oauth-access-token",
                "refresh_token": "oauth-refresh-token",
                "expires_in": 3600,
            },
        ):
            result = serve_gateway_oauth_callback(
                config_manager=self.config_manager,
                state_db=self.state_db,
                redirect_uri=redirect_uri,
                timeout_seconds=5,
                expected_provider="openai-codex",
            )
        thread.join(timeout=5)

        self.assertEqual(result.provider_id, "openai-codex")
        self.assertEqual(result.auth_profile_id, "openai-codex:default")
        self.assertEqual(result.status, "active")
        self.assertEqual(result.path, "/auth/callback")

    def test_gateway_callback_surfaces_oauth_error_cleanly(self) -> None:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]

        redirect_uri = f"http://127.0.0.1:{port}/auth/callback"
        start = start_oauth_login(
            config_manager=self.config_manager,
            state_db=self.state_db,
            provider="openai-codex",
            redirect_uri=redirect_uri,
        )

        def trigger_request() -> None:
            sleep(0.2)
            with urllib.request.urlopen(
                f"{redirect_uri}?state={start.callback_state}&error=access_denied&error_description=user+denied",
                timeout=5,
            ) as response:
                response.read()

        thread = threading.Thread(target=trigger_request, daemon=True)
        thread.start()
        with self.assertRaisesRegex(ValueError, "OAuth callback returned error 'access_denied': user denied"):
            serve_gateway_oauth_callback(
                config_manager=self.config_manager,
                state_db=self.state_db,
                redirect_uri=redirect_uri,
                timeout_seconds=5,
                expected_provider="openai-codex",
            )
        thread.join(timeout=5)

        with self.state_db.connect() as conn:
            callback_row = conn.execute(
                "SELECT status FROM oauth_callback_states WHERE oauth_state = ? LIMIT 1",
                (start.callback_state,),
            ).fetchone()
            event_row = conn.execute(
                """
                SELECT event_kind, detail
                FROM provider_runtime_events
                WHERE auth_profile_id = ?
                ORDER BY rowid DESC
                LIMIT 1
                """,
                ("openai-codex:default",),
            ).fetchone()

        self.assertEqual(callback_row["status"], "consumed")
        self.assertEqual(event_row["event_kind"], "oauth_login_failed")
        self.assertIn("access_denied", event_row["detail"])
