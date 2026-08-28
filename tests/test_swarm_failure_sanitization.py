from __future__ import annotations

import json

from spark_intelligence.swarm_bridge.sync import SwarmSyncResult, _sanitize_response_body


def test_sensitive_key_stripped():
    body = {"error": "auth_failed", "message": "token invalid", "internal_trace": "secret-data-xyz"}
    result = _sanitize_response_body(body)
    assert "internal_trace" not in result
    assert "secret-data-xyz" not in str(result)


def test_only_safe_keys_persisted():
    body = {
        "error": "unauthorized",
        "message": "invalid key",
        "code": 401,
        "status": "error",
        "workspace_tokens": ["tok-abc", "tok-def"],
        "debug_payload": {"raw_request": "sensitive"},
    }
    result = _sanitize_response_body(body)
    assert set(result.keys()) <= {"error", "message", "code", "status"}


def test_safe_fields_pass_through():
    body = {"error": "rate_limit", "message": "Too many requests", "code": 429, "status": "error"}
    result = _sanitize_response_body(body)
    assert result == body


def test_to_json_never_contains_unknown_keys():
    sync_result = SwarmSyncResult(
        ok=False,
        mode="http_error",
        message="Swarm rejected",
        payload_path=None,
        api_url="https://api.example.com",
        workspace_id="ws-123",
        accepted=False,
        response_body={
            "error": "forbidden",
            "code": 403,
            "internal_token": "tok-secret-value",
            "user_emails": ["admin@example.com"],
        },
    )
    output = json.loads(sync_result.to_json())
    rb = output["response_body"]
    assert "internal_token" not in rb
    assert "user_emails" not in rb
    assert rb.get("error") == "forbidden"
    assert rb.get("code") == 403


def test_empty_response_body_handled():
    assert _sanitize_response_body(None) is None
    assert _sanitize_response_body({}) == {}


def test_non_dict_response_body_returned_as_is():
    assert _sanitize_response_body("raw error string") == "raw error string"
    assert _sanitize_response_body(None) is None
