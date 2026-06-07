"""Tests: whatsapp_webhook 503 does not disclose verify_token_ref in response body."""
from __future__ import annotations

from unittest.mock import MagicMock

from spark_intelligence.gateway.whatsapp_webhook import (
    WhatsAppWebhookResponse,
    WHATSAPP_WEBHOOK_PATH,
    handle_whatsapp_webhook,
)


def _make_config(record: dict, env_map: dict | None = None) -> MagicMock:
    cm = MagicMock()
    cm.read_env_map.return_value = env_map or {}
    cm.get_path.return_value = record
    return cm


def _get(config_manager: MagicMock, query: dict) -> WhatsAppWebhookResponse:
    return handle_whatsapp_webhook(
        config_manager=config_manager,
        state_db=MagicMock(),
        path=WHATSAPP_WEBHOOK_PATH,
        method="GET",
        content_type=None,
        headers=None,
        body=b"",
        query_params=query,
    )


class TestVerifyTokenRefNotDisclosed:
    def test_unresolved_ref_body_does_not_contain_ref_name(self):
        """503 when token ref is unresolved must not echo the ref name in body."""
        cm = _make_config(
            record={"webhook_verify_token_ref": "WHATSAPP_WEBHOOK_TOKEN"},
            env_map={},  # ref present but unresolved
        )
        resp = _get(cm, {"hub.mode": "subscribe", "hub.verify_token": "x", "hub.challenge": "c"})
        assert resp.status_code == 503
        assert "WHATSAPP_WEBHOOK_TOKEN" not in (resp.body or "")

    def test_unresolved_ref_returns_generic_message(self):
        cm = _make_config(
            record={"webhook_verify_token_ref": "MY_SECRET_TOKEN_VAR"},
            env_map={},
        )
        resp = _get(cm, {"hub.mode": "subscribe", "hub.verify_token": "x", "hub.challenge": "c"})
        assert "not configured" in (resp.body or "").lower()

    def test_missing_ref_returns_503(self):
        cm = _make_config(record={})
        resp = _get(cm, {"hub.mode": "subscribe", "hub.verify_token": "x", "hub.challenge": "c"})
        assert resp.status_code == 503

    def test_valid_token_returns_200(self):
        cm = _make_config(
            record={"webhook_verify_token_ref": "TOKEN_VAR"},
            env_map={"TOKEN_VAR": "mysecret"},
        )
        resp = _get(cm, {"hub.mode": "subscribe", "hub.verify_token": "mysecret", "hub.challenge": "abc123"})
        assert resp.status_code == 200

    def test_wrong_token_body_does_not_contain_secret(self):
        cm = _make_config(
            record={"webhook_verify_token_ref": "TOKEN_VAR"},
            env_map={"TOKEN_VAR": "mysecret"},
        )
        resp = _get(cm, {"hub.mode": "subscribe", "hub.verify_token": "wrongtoken", "hub.challenge": "c"})
        assert "mysecret" not in (resp.body or "")
        assert resp.status_code in (400, 401, 403)
