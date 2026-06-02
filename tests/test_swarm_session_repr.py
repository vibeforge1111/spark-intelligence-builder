from __future__ import annotations

from spark_intelligence.swarm_bridge.sync import SwarmSession


def _make_session(
    *,
    access_token: str = "eyJhbGciOiJIUzI1NiJ9.secret-access-token",
    refresh_token: str = "rt-secret-refresh-value",
    auth_client_key: str = "anon-secret-client-key",
) -> SwarmSession:
    return SwarmSession(
        access_token_env="SPARK_SWARM_ACCESS_TOKEN",
        access_token=access_token,
        refresh_token_env="SPARK_SWARM_REFRESH_TOKEN",
        refresh_token=refresh_token,
        auth_client_key_env="SPARK_SWARM_AUTH_CLIENT_KEY",
        auth_client_key=auth_client_key,
        supabase_url="https://myproject.supabase.co",
        access_token_expires_at="2026-12-31T00:00:00+00:00",
        auth_state="configured",
    )


def test_repr_does_not_contain_access_token():
    session = _make_session(access_token="eyJ.secret-access-token")
    assert "eyJ.secret-access-token" not in repr(session)


def test_repr_does_not_contain_refresh_token():
    session = _make_session(refresh_token="rt-secret-refresh-value")
    assert "rt-secret-refresh-value" not in repr(session)


def test_repr_does_not_contain_auth_client_key():
    session = _make_session(auth_client_key="anon-secret-client-key")
    assert "anon-secret-client-key" not in repr(session)


def test_str_is_also_safe():
    session = _make_session(
        access_token="eyJ.my-token",
        refresh_token="rt-my-refresh",
        auth_client_key="anon-my-key",
    )
    s = str(session)
    assert "eyJ.my-token" not in s
    assert "rt-my-refresh" not in s
    assert "anon-my-key" not in s


def test_non_sensitive_fields_visible_in_repr():
    session = _make_session()
    r = repr(session)
    assert "access_token_env" in r
    assert "SPARK_SWARM_ACCESS_TOKEN" in r
    assert "supabase_url" in r
    assert "https://myproject.supabase.co" in r
    assert "auth_state" in r
    assert "configured" in r


def test_sensitive_field_values_are_accessible():
    token = "eyJ.actual-secret"
    session = _make_session(access_token=token)
    assert session.access_token == token
    assert session.refresh_token is not None
    assert session.auth_client_key is not None
