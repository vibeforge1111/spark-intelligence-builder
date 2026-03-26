from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.state.db import StateDB


API_MODE_BY_PROVIDER = {
    "anthropic": "anthropic_messages",
    "custom": "chat_completions",
    "openai": "chat_completions",
    "openrouter": "chat_completions",
}


@dataclass(frozen=True)
class StaticSecretRef:
    source: str
    provider: str | None
    ref_id: str


@dataclass(frozen=True)
class RuntimeProviderResolution:
    provider_id: str
    provider_kind: str
    auth_profile_id: str
    auth_method: str
    api_mode: str
    base_url: str | None
    default_model: str | None
    secret_ref: StaticSecretRef
    secret_value: str
    source: str


@dataclass(frozen=True)
class ProviderAuthStatus:
    provider_id: str
    provider_kind: str
    auth_profile_id: str
    auth_method: str
    status: str
    is_default_provider: bool
    is_default_profile: bool
    default_model: str | None
    base_url: str | None
    secret_ref: StaticSecretRef | None
    secret_present: bool


@dataclass(frozen=True)
class AuthStatusReport:
    default_provider: str | None
    providers: list[ProviderAuthStatus]

    @property
    def ok(self) -> bool:
        return all(provider.secret_present for provider in self.providers)

    def to_json(self) -> str:
        payload = {
            "ok": self.ok,
            "default_provider": self.default_provider,
            "providers": [
                {
                    **asdict(provider),
                    "secret_ref": (
                        {
                            "source": provider.secret_ref.source,
                            "provider": provider.secret_ref.provider,
                            "id": provider.secret_ref.ref_id,
                        }
                        if provider.secret_ref
                        else None
                    ),
                }
                for provider in self.providers
            ],
        }
        return json.dumps(payload, indent=2)

    def to_text(self) -> str:
        lines = ["Auth status"]
        lines.append(f"- default_provider: {self.default_provider or 'none'}")
        if not self.providers:
            lines.append("- providers: none")
            lines.append("- result: no providers configured yet")
            return "\n".join(lines)
        for provider in self.providers:
            marker = "ok" if provider.secret_present else "fail"
            ref_text = (
                f"{provider.secret_ref.source}:{provider.secret_ref.ref_id}"
                if provider.secret_ref
                else "missing"
            )
            default_tags: list[str] = []
            if provider.is_default_provider:
                default_tags.append("default-provider")
            if provider.is_default_profile:
                default_tags.append("default-profile")
            tags = f" [{' '.join(default_tags)}]" if default_tags else ""
            lines.append(
                (
                    f"- [{marker}] {provider.provider_id}{tags}: "
                    f"profile={provider.auth_profile_id} method={provider.auth_method} "
                    f"status={provider.status} ref={ref_text} secret={'yes' if provider.secret_present else 'no'}"
                )
            )
            if provider.default_model:
                lines.append(f"  model={provider.default_model}")
            if provider.base_url:
                lines.append(f"  base_url={provider.base_url}")
        return "\n".join(lines)


def build_default_auth_profile_id(provider_id: str) -> str:
    return f"{provider_id}:default"


def build_auth_status_report(*, config_manager: ConfigManager, state_db: StateDB) -> AuthStatusReport:
    config = config_manager.load()
    provider_records = config.get("providers", {}).get("records", {}) or {}
    default_provider = config.get("providers", {}).get("default_provider")
    env_map = config_manager.read_env_map()

    providers: list[ProviderAuthStatus] = []
    with state_db.connect() as conn:
        for provider_id in sorted(provider_records):
            record = provider_records.get(provider_id) or {}
            profile_id = str(record.get("default_auth_profile_id") or build_default_auth_profile_id(provider_id))
            profile_row = conn.execute(
                """
                SELECT auth_profile_id, provider_id, auth_method, status, is_default
                FROM auth_profiles
                WHERE auth_profile_id = ?
                LIMIT 1
                """,
                (profile_id,),
            ).fetchone()
            ref_row = conn.execute(
                """
                SELECT ref_source, ref_provider, ref_id
                FROM auth_profile_static_refs
                WHERE auth_profile_id = ?
                LIMIT 1
                """,
                (profile_id,),
            ).fetchone()
            secret_ref = _resolve_secret_ref(record=record, ref_row=ref_row)
            secret_present = False
            if secret_ref and secret_ref.source == "env":
                secret_present = secret_ref.ref_id in env_map and bool(env_map[secret_ref.ref_id])
            providers.append(
                ProviderAuthStatus(
                    provider_id=provider_id,
                    provider_kind=str(record.get("provider_kind") or provider_id),
                    auth_profile_id=profile_id,
                    auth_method=str(profile_row["auth_method"]) if profile_row else "api_key_env",
                    status=_derive_profile_status(profile_row=profile_row, secret_present=secret_present),
                    is_default_provider=provider_id == default_provider,
                    is_default_profile=bool(profile_row["is_default"]) if profile_row else True,
                    default_model=_optional_string(record.get("default_model")),
                    base_url=_optional_string(record.get("base_url")),
                    secret_ref=secret_ref,
                    secret_present=secret_present,
                )
            )
    return AuthStatusReport(
        default_provider=str(default_provider) if default_provider else None,
        providers=providers,
    )


def resolve_runtime_provider(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    requested_provider: str | None = None,
    requested_profile: str | None = None,
) -> RuntimeProviderResolution:
    config = config_manager.load()
    provider_records = config.get("providers", {}).get("records", {}) or {}
    provider_id = _select_provider_id(provider_records=provider_records, default_provider=config.get("providers", {}).get("default_provider"), requested_provider=requested_provider)
    record = provider_records.get(provider_id)
    if not isinstance(record, dict):
        raise RuntimeError(f"Provider '{provider_id}' is not configured.")

    auth_profile_id = str(requested_profile or record.get("default_auth_profile_id") or build_default_auth_profile_id(provider_id))
    env_map = config_manager.read_env_map()
    with state_db.connect() as conn:
        profile_row = conn.execute(
            """
            SELECT auth_method
            FROM auth_profiles
            WHERE auth_profile_id = ?
            LIMIT 1
            """,
            (auth_profile_id,),
        ).fetchone()
        ref_row = conn.execute(
            """
            SELECT ref_source, ref_provider, ref_id
            FROM auth_profile_static_refs
            WHERE auth_profile_id = ?
            LIMIT 1
            """,
            (auth_profile_id,),
        ).fetchone()
    secret_ref = _resolve_secret_ref(record=record, ref_row=ref_row)
    if not secret_ref:
        raise RuntimeError(f"Provider '{provider_id}' has no secret reference configured.")
    if secret_ref.source != "env":
        raise RuntimeError(f"Provider '{provider_id}' uses unsupported secret source '{secret_ref.source}'.")
    secret_value = env_map.get(secret_ref.ref_id)
    if not secret_value:
        raise RuntimeError(
            f"Provider '{provider_id}' is missing secret value for env ref '{secret_ref.ref_id}'."
        )
    return RuntimeProviderResolution(
        provider_id=provider_id,
        provider_kind=str(record.get("provider_kind") or provider_id),
        auth_profile_id=auth_profile_id,
        auth_method=str(profile_row["auth_method"]) if profile_row else "api_key_env",
        api_mode=API_MODE_BY_PROVIDER.get(str(record.get("provider_kind") or provider_id), "chat_completions"),
        base_url=_optional_string(record.get("base_url")),
        default_model=_optional_string(record.get("default_model")),
        secret_ref=secret_ref,
        secret_value=secret_value,
        source="config+env",
    )


def _resolve_secret_ref(*, record: dict[str, object], ref_row: object) -> StaticSecretRef | None:
    if ref_row:
        return StaticSecretRef(
            source=str(ref_row["ref_source"]),
            provider=str(ref_row["ref_provider"]) if ref_row["ref_provider"] else None,
            ref_id=str(ref_row["ref_id"]),
        )
    env_key = record.get("api_key_env")
    if env_key:
        return StaticSecretRef(source="env", provider="default", ref_id=str(env_key))
    return None


def _select_provider_id(
    *,
    provider_records: dict[str, object],
    default_provider: object,
    requested_provider: str | None,
) -> str:
    if requested_provider:
        return requested_provider
    if isinstance(default_provider, str) and default_provider:
        return default_provider
    if len(provider_records) == 1:
        return next(iter(provider_records))
    if not provider_records:
        raise RuntimeError("No providers are configured.")
    raise RuntimeError("No default provider is configured.")


def _derive_profile_status(*, profile_row: object, secret_present: bool) -> str:
    if profile_row and profile_row["status"]:
        if not secret_present and str(profile_row["status"]) == "active":
            return "pending_secret"
        return str(profile_row["status"])
    return "active" if secret_present else "pending_secret"


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
