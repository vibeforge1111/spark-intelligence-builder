from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from spark_intelligence.security.https_endpoint import ResolvedHTTPSEndpoint
from spark_intelligence.swarm_bridge.sync import (
    SwarmSession,
    _refresh_swarm_access_token,
)

from tests.test_support import SparkTestCase


class SwarmRefreshEndpointSecurityTests(SparkTestCase):
    def _session(self, *, supabase_url: str) -> SwarmSession:
        return SwarmSession(
            access_token_env="SPARK_SWARM_ACCESS_TOKEN",
            access_token="expired-access-token",
            refresh_token_env="SPARK_SWARM_REFRESH_TOKEN",
            refresh_token="refresh-secret-value",
            auth_client_key_env="SPARK_SWARM_AUTH_CLIENT_KEY",
            auth_client_key="client-secret-value",
            supabase_url=supabase_url,
            access_token_expires_at=None,
            auth_state="refreshable",
        )

    def test_refresh_rejects_unsafe_endpoint_before_credential_post(self) -> None:
        unsafe_urls = [
            "http://203.0.113.10",
            "https://127.0.0.1",
            "https://[::1]",
            "https://client-secret-value@example.com",
            "https://example.com?redirect=https://127.0.0.1",
            "https://example.com#fragment",
        ]
        for unsafe_url in unsafe_urls:
            with self.subTest(unsafe_url=unsafe_url), patch(
                "spark_intelligence.swarm_bridge.sync.post_https_bytes",
                side_effect=AssertionError("credential POST must not run"),
                create=True,
            ), patch(
                "spark_intelligence.swarm_bridge.sync.urllib.request.urlopen",
                side_effect=AssertionError("legacy credential POST must not run"),
            ):
                with pytest.raises(RuntimeError) as raised:
                    _refresh_swarm_access_token(
                        config_manager=self.config_manager,
                        state_db=self.state_db,
                        session=self._session(supabase_url=unsafe_url),
                    )

                message = str(raised.value)
                assert message == (
                    "Swarm session refresh failed safely. Check the Swarm auth endpoint "
                    "configuration and network connectivity, then retry."
                )
                assert unsafe_url not in message
                assert "refresh-secret-value" not in message
                assert "client-secret-value" not in message

    def test_refresh_uses_pinned_public_https_transport_with_fixed_query(self) -> None:
        endpoint = ResolvedHTTPSEndpoint(
            hostname="auth.example.com",
            port=443,
            request_target="/auth/v1/token",
            addresses=("93.184.216.34",),
        )
        response = json.dumps(
            {
                "access_token": "fresh-access-token",
                "refresh_token": "rotated-refresh-token",
            }
        ).encode("utf-8")

        with patch(
            "spark_intelligence.swarm_bridge.sync.resolve_public_https_endpoint",
            return_value=endpoint,
            create=True,
        ) as resolve_mock, patch(
            "spark_intelligence.swarm_bridge.sync.post_https_bytes",
            return_value=response,
            create=True,
        ) as post_mock:
            refreshed = _refresh_swarm_access_token(
                config_manager=self.config_manager,
                state_db=self.state_db,
                session=self._session(supabase_url="https://auth.example.com"),
            )

        resolve_mock.assert_called_once_with("https://auth.example.com/auth/v1/token")
        post_mock.assert_called_once()
        assert post_mock.call_args.kwargs["query"] == {"grant_type": "refresh_token"}
        assert post_mock.call_args.kwargs["timeout_seconds"] == 15
        assert post_mock.call_args.kwargs["max_response_bytes"] == 1024 * 1024
        assert refreshed.access_token == "fresh-access-token"
        assert refreshed.refresh_token == "rotated-refresh-token"

    def test_refresh_normalizes_malformed_responses_without_payload_lineage(self) -> None:
        responses = [
            b"not-json",
            b"[]",
            b'\xff',
        ]
        endpoint = ResolvedHTTPSEndpoint(
            hostname="auth.example.com",
            port=443,
            request_target="/auth/v1/token",
            addresses=("93.184.216.34",),
        )
        for response in responses:
            with self.subTest(response=response), patch(
                "spark_intelligence.swarm_bridge.sync.resolve_public_https_endpoint",
                return_value=endpoint,
                create=True,
            ), patch(
                "spark_intelligence.swarm_bridge.sync.post_https_bytes",
                return_value=response,
                create=True,
            ):
                with pytest.raises(RuntimeError) as raised:
                    _refresh_swarm_access_token(
                        config_manager=self.config_manager,
                        state_db=self.state_db,
                        session=self._session(supabase_url="https://auth.example.com"),
                    )

                assert str(raised.value) == "Swarm session refresh returned an invalid response."
                assert raised.value.__cause__ is None
