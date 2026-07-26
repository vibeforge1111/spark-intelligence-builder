from __future__ import annotations

from spark_intelligence.swarm_bridge.sync import SwarmSession


def _session() -> SwarmSession:
    return SwarmSession(
        access_token_env="SPARK_SWARM_ACCESS_TOKEN",
        access_token="access-secret-marker",
        refresh_token_env="SPARK_SWARM_REFRESH_TOKEN",
        refresh_token="refresh-secret-marker",
        auth_client_key_env="SPARK_SWARM_AUTH_CLIENT_KEY",
        auth_client_key="client-key-secret-marker",
        supabase_url="https://project.supabase.co",
        access_token_expires_at="2026-12-31T00:00:00+00:00",
        auth_state="configured",
    )


def test_swarm_session_repr_and_str_hide_credential_values() -> None:
    session = _session()

    for rendered in (repr(session), str(session)):
        assert "access-secret-marker" not in rendered
        assert "refresh-secret-marker" not in rendered
        assert "client-key-secret-marker" not in rendered


def test_swarm_session_repr_preserves_non_secret_diagnostics() -> None:
    rendered = repr(_session())

    assert "SPARK_SWARM_ACCESS_TOKEN" in rendered
    assert "SPARK_SWARM_REFRESH_TOKEN" in rendered
    assert "SPARK_SWARM_AUTH_CLIENT_KEY" in rendered
    assert "https://project.supabase.co" in rendered
    assert "configured" in rendered


def test_swarm_session_credentials_remain_available_to_governed_callers() -> None:
    session = _session()

    assert session.access_token == "access-secret-marker"
    assert session.refresh_token == "refresh-secret-marker"
    assert session.auth_client_key == "client-key-secret-marker"
