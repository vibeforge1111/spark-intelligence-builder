from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OAuthProviderConfig:
    authorize_url: str
    token_url: str
    client_id: str
    redirect_uri: str


@dataclass(frozen=True)
class ProviderSpec:
    id: str
    provider_kind: str
    display_name: str
    auth_methods: tuple[str, ...]
    default_model: str | None = None
    default_base_url: str | None = None
    default_api_key_env: str | None = None
    api_mode: str = "chat_completions"
    execution_transport: str = "direct_http"
    oauth: OAuthProviderConfig | None = None

    @property
    def supports_api_key_connect(self) -> bool:
        return self.default_api_key_env is not None and "api_key_env" in self.auth_methods

    @property
    def supports_oauth_login(self) -> bool:
        return self.oauth is not None and "oauth" in self.auth_methods


PROVIDER_REGISTRY: dict[str, ProviderSpec] = {
    "openai": ProviderSpec(
        id="openai",
        provider_kind="openai",
        display_name="OpenAI",
        auth_methods=("api_key_env",),
        default_model="gpt-5.4",
        default_base_url="https://api.openai.com/v1",
        default_api_key_env="OPENAI_API_KEY",
        api_mode="chat_completions",
    ),
    "openai-codex": ProviderSpec(
        id="openai-codex",
        provider_kind="openai-codex",
        display_name="OpenAI Codex",
        auth_methods=("oauth",),
        default_model="gpt-5.4",
        default_base_url="https://chatgpt.com/backend-api/codex",
        api_mode="codex_responses",
        execution_transport="external_cli_wrapper",
        oauth=OAuthProviderConfig(
            authorize_url="https://auth.openai.com/oauth/authorize",
            token_url="https://auth.openai.com/oauth/token",
            client_id="app_EMoamEEZ73f0CkXaXp7hrann",
            redirect_uri="http://127.0.0.1:1455/auth/callback",
        ),
    ),
    "anthropic": ProviderSpec(
        id="anthropic",
        provider_kind="anthropic",
        display_name="Anthropic",
        auth_methods=("api_key_env",),
        default_model="claude-opus-4-6",
        default_base_url="https://api.anthropic.com",
        default_api_key_env="ANTHROPIC_API_KEY",
        api_mode="anthropic_messages",
    ),
    "openrouter": ProviderSpec(
        id="openrouter",
        provider_kind="openrouter",
        display_name="OpenRouter",
        auth_methods=("api_key_env",),
        default_model="anthropic/claude-3.7-sonnet",
        default_base_url="https://openrouter.ai/api/v1",
        default_api_key_env="OPENROUTER_API_KEY",
        api_mode="chat_completions",
    ),
    "minimax": ProviderSpec(
        id="minimax",
        provider_kind="minimax",
        display_name="MiniMax",
        auth_methods=("api_key_env",),
        default_model="MiniMax-M2.7",
        default_base_url="https://api.minimax.io/v1",
        default_api_key_env="MINIMAX_API_KEY",
        api_mode="chat_completions",
    ),
    "custom": ProviderSpec(
        id="custom",
        provider_kind="custom",
        display_name="Custom OpenAI-Compatible",
        auth_methods=("api_key_env",),
        default_model=None,
        default_base_url=None,
        default_api_key_env="CUSTOM_API_KEY",
        api_mode="chat_completions",
    ),
}


def get_provider_spec(provider_id: str) -> ProviderSpec:
    try:
        return PROVIDER_REGISTRY[provider_id]
    except KeyError as exc:
        raise ValueError(f"Unknown provider '{provider_id}'.") from exc


def list_provider_specs() -> list[ProviderSpec]:
    return [PROVIDER_REGISTRY[key] for key in sorted(PROVIDER_REGISTRY)]


def list_api_key_provider_ids() -> list[str]:
    return [spec.id for spec in list_provider_specs() if spec.supports_api_key_connect]


def list_oauth_provider_ids() -> list[str]:
    return [spec.id for spec in list_provider_specs() if spec.supports_oauth_login]
