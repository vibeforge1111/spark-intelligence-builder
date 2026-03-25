# Spark Intelligence v1 Provider And Auth Config Spec

## 1. Purpose

This document defines the v1 model/provider and auth configuration layer for `Spark Intelligence`.

This includes:

- LLM provider configuration
- OpenAI-compatible endpoint support
- token and secret handling
- future OAuth-backed connector posture

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
- Anthropic
- OpenRouter
- custom OpenAI-compatible endpoint

## 4. Canonical Provider Record

Recommended fields:

```text
id
provider_kind
display_name
base_url
default_model
status
created_at
updated_at
```

## 5. Secret Handling

Secrets should not be spread across many files.

Recommended v1:

- provider metadata in canonical config
- secrets in `.env` or OS-native secret store where practical
- clear secret references from config into runtime

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

## 7. CLI Surface

Recommended commands:

- `spark-intelligence auth connect openai`
- `spark-intelligence auth connect anthropic`
- `spark-intelligence auth connect openrouter`
- `spark-intelligence auth connect custom`
- `spark-intelligence auth status`
- `spark-intelligence auth test`

## 8. Future OAuth Connectors

OAuth-backed external tools should not be mixed into base provider config.

Keep them as a separate connector layer later.

Recommended rule:

- provider auth = model/runtime auth
- connector OAuth = external tool access

Do not blur them.

## 9. Security Rules

- deny missing provider config at startup
- refuse invalid custom endpoints
- never downgrade to insecure defaults silently
- log validation failures without leaking secrets
- require explicit operator action to rotate or replace auth

## 10. Final Decision

Provider and auth config in v1 should stay:

- simple
- explicit
- locally inspectable
- secure by default
- separate from channel auth

One provider layer.
One auth truth surface.
