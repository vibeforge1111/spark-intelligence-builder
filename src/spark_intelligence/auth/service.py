from __future__ import annotations

import base64
import hashlib
import json
import os
import urllib.parse
import urllib.request
from dataclasses import dataclass

from spark_intelligence.auth.oauth_state import consume_oauth_callback_state, issue_oauth_callback_state
from spark_intelligence.auth.providers import ProviderSpec, get_provider_spec
from spark_intelligence.auth.runtime import build_default_auth_profile_id
from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.state.db import StateDB


@dataclass(frozen=True)
class OAuthLoginStart:
    provider_id: str
    auth_profile_id: str
    authorize_url: str
    redirect_uri: str
    callback_state: str
    status: str
    default_model: str | None

    def to_json(self) -> str:
        return json.dumps(
            {
                "provider_id": self.provider_id,
                "auth_profile_id": self.auth_profile_id,
                "authorize_url": self.authorize_url,
                "redirect_uri": self.redirect_uri,
                "callback_state": self.callback_state,
                "status": self.status,
                "default_model": self.default_model,
            },
            indent=2,
        )

    def to_text(self) -> str:
        lines = [f"OAuth login started for {self.provider_id}"]
        lines.append(f"- auth_profile: {self.auth_profile_id}")
        lines.append(f"- status: {self.status}")
        lines.append(f"- redirect_uri: {self.redirect_uri}")
        if self.default_model:
            lines.append(f"- default_model: {self.default_model}")
        lines.append(f"- authorize_url: {self.authorize_url}")
        lines.append("Open the authorize URL, complete login, then rerun this command with --callback-url <full_url>.")
        return "\n".join(lines)


@dataclass(frozen=True)
class OAuthLoginResult:
    provider_id: str
    auth_profile_id: str
    status: str
    default_model: str | None
    base_url: str | None

    def to_json(self) -> str:
        return json.dumps(
            {
                "provider_id": self.provider_id,
                "auth_profile_id": self.auth_profile_id,
                "status": self.status,
                "default_model": self.default_model,
                "base_url": self.base_url,
            },
            indent=2,
        )

    def to_text(self) -> str:
        lines = [f"OAuth login completed for {self.provider_id}"]
        lines.append(f"- auth_profile: {self.auth_profile_id}")
        lines.append(f"- status: {self.status}")
        if self.default_model:
            lines.append(f"- default_model: {self.default_model}")
        if self.base_url:
            lines.append(f"- base_url: {self.base_url}")
        return "\n".join(lines)


@dataclass(frozen=True)
class AuthLogoutResult:
    provider_id: str
    auth_profile_id: str
    status: str

    def to_json(self) -> str:
        return json.dumps(
            {
                "provider_id": self.provider_id,
                "auth_profile_id": self.auth_profile_id,
                "status": self.status,
            },
            indent=2,
        )

    def to_text(self) -> str:
        return "\n".join(
            [
                f"Auth logout completed for {self.provider_id}",
                f"- auth_profile: {self.auth_profile_id}",
                f"- status: {self.status}",
            ]
        )


def connect_provider(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    provider: str,
    api_key: str | None,
    api_key_env: str | None,
    model: str | None,
    base_url: str | None,
) -> str:
    spec = get_provider_spec(provider)
    config = config_manager.load()
    if not spec.supports_api_key_connect:
        raise ValueError(f"Provider '{provider}' does not support API-key connect.")
    env_key = api_key_env or spec.default_api_key_env
    profile_id = build_default_auth_profile_id(provider)

    if api_key:
        config_manager.upsert_env_secret(env_key, api_key)

    config.setdefault("providers", {}).setdefault("records", {})
    config["providers"]["records"][provider] = {
        "provider_kind": spec.provider_kind,
        "default_model": model or spec.default_model,
        "base_url": base_url or spec.default_base_url,
        "api_key_env": env_key,
        "default_auth_profile_id": profile_id,
    }
    if not config["providers"].get("default_provider"):
        config["providers"]["default_provider"] = provider
    config_manager.save(config)

    env_map = config_manager.read_env_map()
    profile_status = "active" if env_map.get(env_key) else "pending_secret"
    with state_db.connect() as conn:
        conn.execute(
            """
            INSERT INTO provider_records(provider_id, provider_kind, default_model, base_url, api_key_env, default_auth_profile_id)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(provider_id) DO UPDATE SET
                provider_kind=excluded.provider_kind,
                default_model=excluded.default_model,
                base_url=excluded.base_url,
                api_key_env=excluded.api_key_env,
                default_auth_profile_id=excluded.default_auth_profile_id,
                updated_at=CURRENT_TIMESTAMP
            """,
            (provider, spec.provider_kind, model or spec.default_model, base_url or spec.default_base_url, env_key, profile_id),
        )
        conn.execute(
            """
            INSERT INTO auth_profiles(auth_profile_id, provider_id, auth_method, display_label, subject_hint, status, is_default)
            VALUES (?, ?, 'api_key_env', ?, NULL, ?, 1)
            ON CONFLICT(auth_profile_id) DO UPDATE SET
                provider_id=excluded.provider_id,
                auth_method=excluded.auth_method,
                display_label=excluded.display_label,
                status=excluded.status,
                is_default=excluded.is_default,
                updated_at=CURRENT_TIMESTAMP
            """,
            (profile_id, provider, f"{provider} default", profile_status),
        )
        conn.execute(
            """
            INSERT INTO auth_profile_static_refs(auth_profile_id, ref_source, ref_provider, ref_id)
            VALUES (?, 'env', 'default', ?)
            ON CONFLICT(auth_profile_id) DO UPDATE SET
                ref_source=excluded.ref_source,
                ref_provider=excluded.ref_provider,
                ref_id=excluded.ref_id,
                updated_at=CURRENT_TIMESTAMP
            """,
            (profile_id, env_key),
        )
        conn.commit()

    return f"Configured provider '{provider}' with auth profile '{profile_id}' and env ref {env_key}."


def start_oauth_login(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    provider: str,
    redirect_uri: str | None,
) -> OAuthLoginStart:
    spec = get_provider_spec(provider)
    if not spec.supports_oauth_login or not spec.oauth:
        raise ValueError(f"Provider '{provider}' does not support OAuth login.")

    oauth = spec.oauth
    resolved_redirect_uri = redirect_uri or oauth.redirect_uri
    auth_profile_id = build_default_auth_profile_id(provider)
    pkce_verifier = _generate_pkce_verifier()
    callback_state = issue_oauth_callback_state(
        state_db=state_db,
        provider_id=provider,
        auth_profile_id=auth_profile_id,
        flow_kind="oauth_login",
        redirect_uri=resolved_redirect_uri,
        expected_issuer=_issuer_from_url(oauth.authorize_url),
        pkce_verifier=pkce_verifier,
    )
    _log_provider_runtime_event(
        state_db=state_db,
        provider_id=provider,
        auth_profile_id=auth_profile_id,
        event_kind="oauth_login_started",
        detail={"redirect_uri": resolved_redirect_uri},
    )
    _upsert_oauth_provider_record(
        config_manager=config_manager,
        state_db=state_db,
        spec=spec,
        auth_profile_id=auth_profile_id,
        status="pending_oauth",
    )
    authorize_url = _build_oauth_authorize_url(
        authorize_url=oauth.authorize_url,
        client_id=oauth.client_id,
        redirect_uri=resolved_redirect_uri,
        state=callback_state.oauth_state,
        code_challenge=_pkce_challenge(pkce_verifier),
    )
    return OAuthLoginStart(
        provider_id=provider,
        auth_profile_id=auth_profile_id,
        authorize_url=authorize_url,
        redirect_uri=resolved_redirect_uri,
        callback_state=callback_state.oauth_state,
        status="pending_oauth",
        default_model=spec.default_model,
    )


def complete_oauth_login(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    provider: str,
    callback_url: str,
) -> OAuthLoginResult:
    spec = get_provider_spec(provider)
    if not spec.supports_oauth_login or not spec.oauth:
        raise ValueError(f"Provider '{provider}' does not support OAuth login.")

    parsed = urllib.parse.urlparse(callback_url)
    query = urllib.parse.parse_qs(parsed.query)
    state = _required_query_value(query, "state")
    code = _required_query_value(query, "code")
    oauth_state = consume_oauth_callback_state(
        state_db=state_db,
        provider_id=provider,
        oauth_state=state,
        redirect_uri=f"{parsed.scheme}://{parsed.netloc}{parsed.path}",
        expected_issuer=_issuer_from_url(spec.oauth.authorize_url),
    )
    token_payload = exchange_oauth_authorization_code(
        provider=provider,
        code=code,
        redirect_uri=oauth_state.redirect_uri or spec.oauth.redirect_uri,
        code_verifier=oauth_state.pkce_verifier,
    )
    _persist_oauth_tokens(
        state_db=state_db,
        auth_profile_id=oauth_state.auth_profile_id or build_default_auth_profile_id(provider),
        provider=provider,
        token_payload=token_payload,
    )
    _log_provider_runtime_event(
        state_db=state_db,
        provider_id=provider,
        auth_profile_id=oauth_state.auth_profile_id or build_default_auth_profile_id(provider),
        event_kind="oauth_login_completed",
        detail={"issuer": _issuer_from_url(spec.oauth.authorize_url)},
    )
    _upsert_oauth_provider_record(
        config_manager=config_manager,
        state_db=state_db,
        spec=spec,
        auth_profile_id=oauth_state.auth_profile_id or build_default_auth_profile_id(provider),
        status="active",
    )
    return OAuthLoginResult(
        provider_id=provider,
        auth_profile_id=oauth_state.auth_profile_id or build_default_auth_profile_id(provider),
        status="active",
        default_model=spec.default_model,
        base_url=spec.default_base_url,
    )


def logout_provider(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    provider: str,
) -> AuthLogoutResult:
    spec = get_provider_spec(provider)
    if not spec.supports_oauth_login:
        raise ValueError(f"Provider '{provider}' does not support OAuth logout.")

    auth_profile_id = build_default_auth_profile_id(provider)
    with state_db.connect() as conn:
        conn.execute(
            """
            UPDATE oauth_credentials
            SET
                access_token_ciphertext = NULL,
                refresh_token_ciphertext = NULL,
                access_expires_at = NULL,
                refresh_expires_at = NULL,
                status = 'revoked',
                updated_at = CURRENT_TIMESTAMP
            WHERE auth_profile_id = ?
            """,
            (auth_profile_id,),
        )
        conn.execute(
            """
            UPDATE auth_profiles
            SET status = 'revoked', updated_at = CURRENT_TIMESTAMP
            WHERE auth_profile_id = ?
            """,
            (auth_profile_id,),
        )
        conn.commit()

    config = config_manager.load()
    record = config.setdefault("providers", {}).setdefault("records", {}).get(provider)
    if isinstance(record, dict):
        record["status"] = "revoked"
        config_manager.save(config)

    _log_provider_runtime_event(
        state_db=state_db,
        provider_id=provider,
        auth_profile_id=auth_profile_id,
        event_kind="oauth_logout",
        detail={},
    )
    return AuthLogoutResult(
        provider_id=provider,
        auth_profile_id=auth_profile_id,
        status="revoked",
    )


def exchange_oauth_authorization_code(
    *,
    provider: str,
    code: str,
    redirect_uri: str,
    code_verifier: str | None,
) -> dict[str, object]:
    spec = get_provider_spec(provider)
    if not spec.oauth:
        raise ValueError(f"Provider '{provider}' does not support OAuth token exchange.")
    if not code_verifier:
        raise ValueError("OAuth callback state is missing PKCE verifier.")
    data = urllib.parse.urlencode(
        {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": spec.oauth.client_id,
            "code_verifier": code_verifier,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        spec.oauth.token_url,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not payload.get("access_token"):
        raise RuntimeError(f"OAuth token exchange for '{provider}' returned no access token.")
    return payload


def _upsert_oauth_provider_record(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    spec: ProviderSpec,
    auth_profile_id: str,
    status: str,
) -> None:
    config = config_manager.load()
    config.setdefault("providers", {}).setdefault("records", {})
    existing_record = config["providers"]["records"].get(spec.id) or {}
    default_model = existing_record.get("default_model") or spec.default_model
    base_url = existing_record.get("base_url") or spec.default_base_url
    config["providers"]["records"][spec.id] = {
        "provider_kind": spec.provider_kind,
        "default_model": default_model,
        "base_url": base_url,
        "api_key_env": None,
        "default_auth_profile_id": auth_profile_id,
        "status": status,
    }
    if not config["providers"].get("default_provider"):
        config["providers"]["default_provider"] = spec.id
    config_manager.save(config)

    with state_db.connect() as conn:
        conn.execute(
            """
            INSERT INTO provider_records(provider_id, provider_kind, default_model, base_url, api_key_env, default_auth_profile_id)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(provider_id) DO UPDATE SET
                provider_kind=excluded.provider_kind,
                default_model=excluded.default_model,
                base_url=excluded.base_url,
                api_key_env=excluded.api_key_env,
                default_auth_profile_id=excluded.default_auth_profile_id,
                updated_at=CURRENT_TIMESTAMP
            """,
            (spec.id, spec.provider_kind, default_model, base_url, None, auth_profile_id),
        )
        conn.execute(
            """
            INSERT INTO auth_profiles(auth_profile_id, provider_id, auth_method, display_label, subject_hint, status, is_default)
            VALUES (?, ?, 'oauth', ?, NULL, ?, 1)
            ON CONFLICT(auth_profile_id) DO UPDATE SET
                provider_id=excluded.provider_id,
                auth_method=excluded.auth_method,
                display_label=excluded.display_label,
                status=excluded.status,
                is_default=excluded.is_default,
                updated_at=CURRENT_TIMESTAMP
            """,
            (auth_profile_id, spec.id, f"{spec.id} default", status),
        )
        conn.commit()


def _persist_oauth_tokens(
    *,
    state_db: StateDB,
    auth_profile_id: str,
    provider: str,
    token_payload: dict[str, object],
) -> None:
    issuer = _issuer_from_url(get_provider_spec(provider).oauth.authorize_url)
    with state_db.connect() as conn:
        conn.execute(
            """
            INSERT INTO oauth_credentials(
                auth_profile_id,
                issuer,
                account_subject,
                scope,
                access_token_ciphertext,
                refresh_token_ciphertext,
                access_expires_at,
                refresh_expires_at,
                last_refresh_at,
                last_refresh_error,
                status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, 'active')
            ON CONFLICT(auth_profile_id) DO UPDATE SET
                issuer=excluded.issuer,
                account_subject=excluded.account_subject,
                scope=excluded.scope,
                access_token_ciphertext=excluded.access_token_ciphertext,
                refresh_token_ciphertext=excluded.refresh_token_ciphertext,
                access_expires_at=excluded.access_expires_at,
                refresh_expires_at=excluded.refresh_expires_at,
                last_refresh_error=NULL,
                status='active',
                updated_at=CURRENT_TIMESTAMP
            """,
            (
                auth_profile_id,
                issuer,
                None,
                str(token_payload.get("scope")) if token_payload.get("scope") else None,
                str(token_payload.get("access_token")),
                str(token_payload.get("refresh_token")) if token_payload.get("refresh_token") else None,
                None,
                None,
            ),
        )
        conn.execute(
            """
            UPDATE auth_profiles
            SET status = 'active', updated_at = CURRENT_TIMESTAMP
            WHERE auth_profile_id = ?
            """,
            (auth_profile_id,),
        )
        conn.commit()


def _build_oauth_authorize_url(
    *,
    authorize_url: str,
    client_id: str,
    redirect_uri: str,
    state: str,
    code_challenge: str,
) -> str:
    query = urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
    )
    return f"{authorize_url}?{query}"


def _generate_pkce_verifier() -> str:
    return base64.urlsafe_b64encode(os.urandom(48)).decode("ascii").rstrip("=")


def _pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def _issuer_from_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def _required_query_value(query: dict[str, list[str]], key: str) -> str:
    values = query.get(key) or []
    if not values or not values[0]:
        raise ValueError(f"OAuth callback URL is missing '{key}'.")
    return values[0]


def _log_provider_runtime_event(
    *,
    state_db: StateDB,
    provider_id: str,
    auth_profile_id: str,
    event_kind: str,
    detail: dict[str, object],
) -> None:
    with state_db.connect() as conn:
        conn.execute(
            """
            INSERT INTO provider_runtime_events(provider_id, auth_profile_id, event_kind, detail)
            VALUES (?, ?, ?, ?)
            """,
            (
                provider_id,
                auth_profile_id,
                event_kind,
                json.dumps(detail, sort_keys=True),
            ),
        )
        conn.commit()
