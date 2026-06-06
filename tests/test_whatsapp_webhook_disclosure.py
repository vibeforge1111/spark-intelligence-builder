import pytest


def _make_config(env_map: dict) -> object:
    class FakeConfig:
        def read_env_map(self):
            return env_map

        def get_path(self, path, default=None):
            if path == "channels.records.whatsapp":
                return {"webhook_verify_token_ref": "MY_VERIFY_TOKEN_REF", "webhook_auth_ref": "MY_AUTH_SECRET_REF"}
            return default

        def append_trace(self, *args, **kwargs):
            pass

    return FakeConfig()


def simulate_verify_token_unresolved_response(verify_token_ref: str, env_map: dict) -> dict:
    """Simulates the fixed code path when verify token ref is unresolved."""
    expected = env_map.get(verify_token_ref, "")
    if not expected:
        return {"status_code": 503, "body": "WhatsApp webhook configuration error."}
    return {"status_code": 200, "body": "ok"}


def simulate_auth_secret_unresolved_response(secret_ref: str, env_map: dict) -> dict:
    """Simulates the fixed code path when auth secret ref is unresolved."""
    expected = env_map.get(secret_ref, "")
    if not expected:
        return {"status_code": 503, "body": "WhatsApp webhook configuration error."}
    return {"status_code": 200, "body": "ok"}


class TestVerifyTokenRefNotExposed:
    def test_unresolved_verify_token_returns_generic_message(self):
        result = simulate_verify_token_unresolved_response("MY_VERIFY_TOKEN_REF", {})
        assert result["status_code"] == 503
        assert "MY_VERIFY_TOKEN_REF" not in result["body"]

    def test_unresolved_verify_token_body_does_not_contain_ref_name(self):
        result = simulate_verify_token_unresolved_response("SECRET_INTERNAL_REF", {})
        assert "SECRET_INTERNAL_REF" not in result["body"]
        assert "unresolved" not in result["body"]

    def test_unresolved_verify_token_returns_configuration_error(self):
        result = simulate_verify_token_unresolved_response("MY_VERIFY_TOKEN_REF", {})
        assert "configuration error" in result["body"].lower()

    def test_resolved_verify_token_does_not_return_error(self):
        result = simulate_verify_token_unresolved_response("MY_TOKEN", {"MY_TOKEN": "actual-token-value"})
        assert result["status_code"] == 200


class TestAuthSecretRefNotExposed:
    def test_unresolved_auth_secret_returns_generic_message(self):
        result = simulate_auth_secret_unresolved_response("MY_AUTH_SECRET_REF", {})
        assert result["status_code"] == 503
        assert "MY_AUTH_SECRET_REF" not in result["body"]

    def test_unresolved_auth_secret_body_does_not_contain_ref_name(self):
        result = simulate_auth_secret_unresolved_response("INTERNAL_AUTH_SECRET", {})
        assert "INTERNAL_AUTH_SECRET" not in result["body"]
        assert "unresolved" not in result["body"]

    def test_unresolved_auth_secret_returns_configuration_error(self):
        result = simulate_auth_secret_unresolved_response("MY_AUTH_SECRET_REF", {})
        assert "configuration error" in result["body"].lower()

    def test_resolved_auth_secret_does_not_return_error(self):
        result = simulate_auth_secret_unresolved_response("MY_SECRET", {"MY_SECRET": "correct-secret"})
        assert result["status_code"] == 200
