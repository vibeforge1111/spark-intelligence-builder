# Spark Intelligence v1 Provider And Auth Config Spec

## 1. Purpose

This document defines the v1 model/provider and auth configuration layer for `Spark Intelligence`.

This includes:

- LLM provider configuration
- OpenAI-compatible endpoint support
- token and secret handling
- auth-profile storage and runtime resolution
- OAuth callback-state posture

## 2. Design Goals

- fast setup
- one canonical provider config surface
- lightweight local storage
- secure secret handling
- explicit validation
- no provider-specific runtime forks in the core

## 3. Supported v1 Provider Shapes

Recommended v1 support:

- OpenAI
- OpenAI Codex via OAuth
- Anthropic
- OpenRouter
- custom OpenAI-compatible endpoint

## 4. Canonical Provider Record

Recommended fields:

```text
id
provider_kind
base_url
default_model
execution_transport
default_auth_profile_id
created_at
updated_at
```

The canonical provider record points at a default auth profile instead of owning raw secret material directly.
`execution_transport` makes runtime delivery explicit so the gateway and bridge do not infer transport from model names or ad hoc provider checks.

## 4.1 Canonical Auth Profile Record

Recommended fields:

```text
auth_profile_id
provider_id
auth_method
display_label
subject_hint
status
is_default
created_at
updated_at
```

For static API-key-backed providers, the auth profile should point at a secret ref instead of copying plaintext into SQLite.

## 4.2 OAuth Callback-State Record

Recommended fields:

```text
callback_id
provider_id
auth_profile_id
flow_kind
oauth_state
pkce_verifier
redirect_uri
expected_issuer
status
expires_at
consumed_at
created_at
```

Rules:

- `oauth_state` must be random and single-use
- expired states must fail closed
- consumed states must fail closed
- callback completion should be atomic with credential persistence

## 5. Secret Handling

Secrets should not be spread across many files.

Recommended v1:

- provider metadata in canonical config
- static secrets in `.env` or env/file/exec secret refs
- clear secret references from config into runtime
- refreshable OAuth material in a dedicated local auth store

Do not:

- print raw secrets in logs
- duplicate secrets across adapter-specific files
- bury secrets inside undocumented JSON blobs

## 6. Validation

Every provider setup flow should validate:

- required credentials present
- base URL sane if custom
- selected model reachable enough for a lightweight probe
- response shape compatible enough for runtime use
- auth-profile resolution deterministic enough for CLI and gateway parity

## 7. CLI Surface

Recommended commands:

- `spark-intelligence auth connect openai`
- `spark-intelligence auth connect anthropic`
- `spark-intelligence auth connect openrouter`
- `spark-intelligence auth connect custom`
- `spark-intelligence auth providers`
- `spark-intelligence auth connect <provider> --api-key-env <ENV>`
- `spark-intelligence auth status`
- `spark-intelligence auth login <provider>`
- `spark-intelligence auth login <provider> --listen`
- `spark-intelligence auth login <provider> --callback-url <full_url>`
- `spark-intelligence gateway oauth-callback`
- `spark-intelligence auth refresh <provider>`
- `spark-intelligence auth logout <provider>`
- `spark-intelligence jobs tick`

## 8. OAuth Boundaries

OAuth-backed model auth and external-tool OAuth should not be blurred.

Recommended rule:

- provider auth = model and runtime auth
- connector OAuth = external tool access

Both may use OAuth, but they should not share storage or callback handling casually.

Model-provider OAuth should use:

- PKCE where applicable
- one-time callback state
- short expiry
- locked refresh
- explicit expiry tracking in local state
- operator-visible refresh failures
- explicit provider and redirect matching

For v1, Spark should keep API-key-backed providers on a direct HTTP execution path and keep Codex/OAuth on an explicit external-wrapper path until a first-class direct OAuth runtime can match the same callback-state, expiry, and revoke guarantees.
Short-horizon expiry should be surfaced before failure, and scheduled maintenance should remain explicit and auditable. A built-in `jobs tick` maintenance pass is acceptable in v1; hidden background refresh is not. `doctor` and `jobs list` should make stale or never-run maintenance obvious to the operator.

## 9. Security Rules

- deny missing provider config at startup
- refuse invalid custom endpoints
- never downgrade to insecure defaults silently
- log validation failures without leaking secrets
- require explicit operator action to rotate or replace auth
- never silently remap one provider to another
- never let one provider key leak to another base URL

## 10. Final Decision

Provider and auth config in v1 should stay:

- simple
- explicit
- locally inspectable
- secure by default
- separate from channel auth

One provider layer.
One auth truth surface.
