# Spark Intelligence Gateway And Provider Auth Readiness Review 2026-03-26

## 1. Purpose

This review records the next architectural lock for Spark Intelligence after the Telegram/operator hardening pass.

It answers:

- what is already good enough in the current repo
- what is still structurally missing before broader adapter work
- what OpenClaw should influence
- what Hermes should influence
- what neither repo should be copied on purpose
- the secure implementation order for gateway, provider auth, OAuth, and agent runtime routing

This is a design-and-execution document, not a generic landscape memo.

## 2. Bottom-Line Verdict

The repo is ready for a broader readiness and architecture pass.

It is not ready to expand Discord or WhatsApp deeply yet.

The highest-value next slice is:

1. lock the gateway responsibility boundary
2. lock the provider/auth registry shape
3. add secure OAuth support alongside API-key support
4. unify runtime provider resolution across CLI, gateway, and future agent entrypoints
5. only then widen adapter breadth

The reason is simple:

- Telegram/operator safety is now materially hardened
- the remaining risk is architectural drift, not missing Telegram features
- adding more adapters before provider/auth and callback routing are settled would multiply cleanup later

## 3. Current Spark Intelligence Read

The current builder is strong in these areas:

- one canonical repo boundary
- one canonical SQLite state file
- one operator-first control surface
- one real Telegram runtime and live-validated onboarding path
- one repeatable local regression suite for the current vertical slice

The current builder is still structurally thin in these areas:

- provider auth is API-key-only
- provider state is too flat for mixed API-key and OAuth realities
- gateway runtime is transport-first, not callback/auth-route aware
- doctor can verify env refs, but not OAuth session health, callback readiness, or token rotation state
- there is no shared runtime-provider resolver that all execution paths must use

In concrete repo terms, the current state is:

- `src/spark_intelligence/auth/service.py` stores provider records as `provider_id`, `provider_kind`, `default_model`, `base_url`, and `api_key_env`
- `src/spark_intelligence/state/db.py` has `provider_records`, but no auth-profile table, no OAuth credential table, and no callback-state table
- `src/spark_intelligence/state/db.py` stores one `auth_ref` string on `channel_installations`, which is not enough for profile selection, rotation metadata, or auth method typing
- `src/spark_intelligence/gateway/runtime.py` is focused on inbound polling and runtime processing, not HTTP callback ownership or shared auth route management
- `src/spark_intelligence/doctor/checks.py` can now verify env-backed provider refs plus expired OAuth auth states
- Since this review started, `src/spark_intelligence/cli.py` has grown from `auth connect <provider> --api-key --model --base-url` into `auth providers`, `auth status`, `auth refresh`, `auth logout`, `gateway oauth-callback`, and the first `auth login <provider>` OAuth flow, with loopback callback completion now routed through the gateway surface plus fail-closed expiry handling. `operator inbox` and `operator security` now also surface reconnect/revoke guidance for provider auth failures. The bridge now also uses provider-aware runtime selection for real API-key-backed LLM execution, and the provider registry now makes execution transport explicit: API-key-backed providers use `direct_http`, while Codex/OAuth stays on `external_cli_wrapper` until a direct OAuth runtime can meet the same security bar. The repo now also has a built-in OAuth maintenance job behind `jobs tick`, `expiring_soon` status before tokens lapse, a doctor check that flags stale manual maintenance, and gateway/status surfaces that expose auth state plus runtime-provider readiness plus execution transport together. Doctor now also fails `provider-runtime` when the selected provider cannot be resolved and separately fails `provider-execution` when a wrapper-backed provider is configured without a usable researcher bridge. `gateway start` now follows both rules instead of polling into a known-bad runtime path, the loopback callback listener now rejects malformed callback requests before they can collapse into ambiguous auth failures, the shared route registry now enforces method plus content-type contracts for future adapter webhooks, and the first Discord webhook skeleton now verifies official Discord signatures via interaction public key before JSON parsing while reducing the older static-secret message path to explicit compatibility mode with operator-visible readiness surfaces for signed-vs-legacy ingress plus trace-visible and operator-visible auth rejection reasons, including higher-severity escalation once the same rejection family repeats in recent traces plus explicit operator snoozes for known noisy sources that are now listable and clearable through dedicated operator commands. WhatsApp now also exposes webhook-ingress readiness explicitly so a configured-but-unprotected adapter degrades before broader runtime work expands, and the first WhatsApp webhook skeleton now reuses the same fail-closed route validation discipline while adding the Meta-style GET verification handshake, `X-Hub-Signature-256` POST auth, an exact one-event text-message contract that rejects stub-shaped or batched webhook payloads before handing off to the existing simulated bridge, and trace-visible plus operator-visible ignore/auth/verification rejection reasons so operators can inspect rejected Meta payloads through the same local gateway and operator surfaces. Signed `PING` requests now work end to end, and DM-only chat-input `APPLICATION_COMMAND` interactions now require the explicit `/spark message:<text>` contract plus guarded callback replies before they route through the existing DM bridge while guild interaction behavior remains intentionally deferred. The current decision is to keep maintenance operator-driven until a persistent scheduler can meet the same auditability bar.

## 4. External Research Read

This review used the current local snapshots of:

- `https://github.com/openclaw/openclaw`
- `https://github.com/NousResearch/hermes-agent`

Representative source files and docs inspected:

- OpenClaw:
  - `docs/concepts/oauth.md`
  - `docs/concepts/model-providers.md`
  - `docs/gateway/authentication.md`
  - `docs/gateway/secrets.md`
  - `docs/gateway/security/index.md`
  - `src/plugins/provider-runtime.ts`
  - `src/plugins/http-registry.ts`
  - `src/plugins/types.ts`
- Hermes:
  - `website/docs/developer-guide/provider-runtime.md`
  - `website/docs/developer-guide/gateway-internals.md`
  - `hermes_cli/auth.py`
  - `hermes_cli/runtime_provider.py`
  - `agent/anthropic_adapter.py`
  - `gateway/run.py`

## 5. What OpenClaw Gets Right

OpenClaw is strongest where Spark now needs more structure.

The most useful patterns to borrow are:

- provider auth is not treated as one flat env-var problem
- provider plugins can own onboarding, OAuth, API-key setup, and runtime auth translation
- HTTP callback and webhook routes are explicitly registered and owned
- secrets resolve into an eager runtime snapshot instead of being fetched lazily on every hot path
- startup and reload are fail-fast when active secrets cannot resolve
- doctor and security surfaces are treated as first-class operator tooling, not an afterthought

The most important OpenClaw lesson for Spark is this:

- core should own generic persistence, routing, and policy boundaries
- provider-specific login, refresh, and runtime auth translation should live behind a provider contract

That separation matters because OAuth, setup-token, API-key, and provider-specific quota or usage calls do not age at the same rate.

## 6. What Hermes Gets Right

Hermes is strongest where Spark needs a smaller and cleaner runtime contract.

The most useful patterns to borrow are:

- one shared runtime-provider resolver used by CLI, gateway, cron, and helpers
- explicit precedence between explicit request, persisted config, environment, and provider defaults
- refreshable OAuth credentials are preferred over stale copied env tokens when both exist
- provider and base-url scoping prevents sending the wrong API key to the wrong endpoint
- auth refresh logic is aware of expiry and refresh skew
- provider runtime resolution returns one normalized runtime object: provider, base_url, api_key, api_mode, source, and expiry metadata

The big Hermes lesson for Spark is this:

- every execution surface should call the same provider resolver before model traffic happens

Without that, CLI, gateway, background jobs, and auxiliary flows drift into different auth behaviors.

## 7. What Not To Copy

Spark should not copy everything from either repo.

Do not copy from OpenClaw:

- plugin breadth as a v1 goal
- giant provider and channel surface before the core contract is stable
- multiple overlapping operational planes before one simple path is solid

Do not copy from Hermes:

- config spread across too many partially overlapping files and env bridges
- gateway ownership of too many unrelated runtime concerns
- auth behaviors that depend on ambient environment mutation instead of explicit persisted state

Do not copy from either:

- silent provider remapping
- hidden fallback credential precedence
- callback logic that is not tied to exact state, provider, and redirect expectations
- "best effort" security around mixed-trust users on one gateway

## 8. Security Findings For Spark

No critical code bug was found in the current builder during this review.

The architectural security findings are:

### 8.1 Medium: provider auth model is too weak for OAuth

Current provider storage assumes one env-backed API secret.

That is not enough for:

- OAuth access token plus refresh token
- multiple accounts for one provider
- token expiry and refresh metadata
- explicit profile selection
- secure revocation and reconnect flows

### 8.2 Medium: no callback-state boundary exists yet

There is currently no state model for:

- OAuth `state`
- PKCE verifier and challenge tracking
- callback route ownership
- one-time exchange expiration
- replay prevention

If OAuth is added without this boundary first, it will be fragile.

### 8.3 Medium: runtime auth resolution is not centralized

Right now the repo has auth setup logic and provider config, but not one shared runtime resolver that every model execution path must use.

That creates future drift risk across:

- CLI model calls
- gateway-triggered replies
- researcher-bridge model calls
- future auxiliary or cron flows

### 8.4 Low: doctor and status surfaces are not yet auth-lifecycle aware

Doctor can say "provider env ref exists."

That is weaker than what operators will need once OAuth exists:

- token valid vs expired
- refreshable vs non-refreshable
- callback listener ready vs unavailable
- account subject bound vs ambiguous
- last refresh failure and repair hint

## 9. Secure Target Architecture

Spark should implement this target shape.

### 9.1 One provider registry

Add a canonical provider registry contract with:

- provider id
- provider kind
- supported auth methods
- default model metadata
- base URL rules
- runtime transport family
- optional usage and quota hooks

This registry can start built-in and static.

Do not start with a public plugin marketplace.

### 9.2 One auth profile model

Add a first-class auth-profile layer.

Minimum concepts:

- `auth_profile_id`
- `provider_id`
- `auth_method`
- `display_label`
- `subject_hint`
- `status`
- `is_default`
- `created_at`
- `updated_at`

Profiles should support both:

- static secret refs for API keys and non-refreshable tokens
- locally stored OAuth and session credentials for refreshable flows

### 9.3 Separate static secrets from refreshable OAuth credentials

Spark should not treat API keys and OAuth refresh tokens as the same storage problem.

Recommended split:

- static API keys and static tokens:
  - stored as env, file, or exec refs in config
  - never copied into SQLite in plaintext
- refreshable OAuth credentials:
  - stored in local state with owner-only file permissions
  - encrypted at rest when supported by the host platform
  - fail closed if encryption support is required but unavailable

The DB shape should distinguish:

- profile metadata
- credential material
- token expiry and refresh metadata
- last refresh failure
- account subject, issuer, and scope metadata

### 9.4 One callback-state store

Add a dedicated callback-state table for OAuth and future device or webhook flows.

Minimum fields:

- `callback_id`
- `provider_id`
- `auth_profile_id` or pending profile slot
- `flow_kind`
- `state`
- `pkce_verifier`
- `redirect_uri`
- `expected_issuer`
- `status`
- `expires_at`
- `consumed_at`
- `created_at`

Rules:

- state must be random and single-use
- expired state must be rejected
- consumed state must be rejected
- redirect URI and provider must match exactly
- callback completion must be atomic with profile and token write

### 9.5 One gateway route registry

The gateway should own one explicit HTTP route registry even before many routes exist.

Route classes should be explicit:

- internal operator-only routes
- OAuth callback routes
- provider-owned helper routes
- adapter webhook routes

Each route registration should declare:

- path
- auth mode
- ownership
- method set
- collision policy

That prevents ad hoc callback or webhook handlers from accumulating in random files.

### 9.6 One runtime provider resolver

Add one resolver that returns a normalized runtime auth object for every model call.

Input precedence should be explicit:

1. exact request override
2. channel or agent explicit profile selection
3. persisted workspace default
4. environment-backed live override if intentionally supported
5. provider fallback

Output should include:

- `provider_id`
- `auth_profile_id`
- `base_url`
- `auth_scheme`
- `api_mode`
- secret material or short-lived runtime token
- `source`
- `expires_at`
- `subject_hint`

Every execution surface should use it:

- CLI
- gateway reply path
- researcher bridge if it starts selecting providers here
- future background or auxiliary work

### 9.7 One operator-visible health model

Doctor, status, and operator security should report:

- active provider profiles
- default profile selection
- missing static secret refs
- OAuth profiles requiring login
- profiles expiring soon
- last refresh failure
- callback listener availability
- provider-specific repair hint

This should stay local-first.

Do not build a dashboard first.

## 10. Proposed Schema Additions

The current schema should grow in a narrow, explicit way.

Recommended new tables:

- `auth_profiles`
- `auth_profile_static_refs`
- `oauth_credentials`
- `oauth_callback_states`
- `provider_runtime_events`

Recommended `auth_profiles` fields:

- `auth_profile_id TEXT PRIMARY KEY`
- `provider_id TEXT NOT NULL`
- `auth_method TEXT NOT NULL`
- `display_label TEXT NOT NULL`
- `subject_hint TEXT`
- `status TEXT NOT NULL`
- `is_default INTEGER NOT NULL DEFAULT 0`
- `created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP`
- `updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP`

Recommended `oauth_credentials` fields:

- `auth_profile_id TEXT PRIMARY KEY`
- `issuer TEXT`
- `account_subject TEXT`
- `scope TEXT`
- `access_token_ciphertext TEXT`
- `refresh_token_ciphertext TEXT`
- `access_expires_at TEXT`
- `refresh_expires_at TEXT`
- `last_refresh_at TEXT`
- `last_refresh_error TEXT`
- `status TEXT NOT NULL`
- `created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP`
- `updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP`

Recommended channel change:

- keep `channel_installations.auth_ref`, but redefine it to reference a real auth profile id
- do not let channels own raw secret material directly

## 11. CLI And Operator Surface To Add

Spark should move from `auth connect` only to a fuller auth surface.

Recommended command family:

- `spark-intelligence auth providers`
- `spark-intelligence auth status`
- `spark-intelligence auth connect <provider> --api-key-env <ENV>`
- `spark-intelligence auth login <provider> [--method oauth|device|api-key]`
- `spark-intelligence auth logout <provider> [--profile <id>]`
- `spark-intelligence auth set-default <provider> <profile>`
- `spark-intelligence auth profiles [--provider <id>]`
- `spark-intelligence auth doctor [--provider <id>]`

The operator should also gain:

- callback and status visibility
- last auth failure visibility
- explicit revoke and reconnect guidance

## 12. OAuth Rules For Spark

OAuth support should ship only with these rules:

- PKCE for browser-based flows
- one-time `state`
- short callback expiry
- strict callback consumption
- refresh under a lock
- explicit refresh skew before expiry
- no silent fallback from failed OAuth to unrelated env API key
- no provider remapping behind the operator's back
- exact base-url scoping so one provider key cannot leak to another endpoint
- owner-only local state permissions

If a secure callback listener cannot start, the flow should fall back to a manual copy-paste path, not to an insecure listener.

## 13. API-Key Rules For Spark

API-key support should also mature.

Required rules:

- env, file, and exec secret refs, not only `.env`
- exact base-url scoping
- optional live override env vars only when declared explicitly
- explicit provider ownership of required headers or request auth translation
- no copying plaintext secrets into logs, traces, or SQLite

## 14. Today's Execution Plan

This is the recommended execution order from here.

### Phase 1: architecture lock

Write and approve:

- provider registry contract
- auth-profile schema
- OAuth callback-state schema
- runtime-provider resolution contract
- route registry contract

### Phase 2: state and config migration

Implement:

- schema migration for auth profiles and OAuth state
- config shape for static secret refs
- migration path from current `provider_records` and channel `auth_ref`

### Phase 3: runtime provider resolver

Implement one resolver used by:

- CLI
- gateway
- future bridge and background entrypoints

Do this before any live OAuth provider support lands.

### Phase 4: OAuth infrastructure

Implement:

- callback route registration
- state issuance and validation
- PKCE support
- secure token exchange
- refresh path
- doctor and status surfaces

Start with one provider only.

### Phase 5: first real OAuth provider

Recommended first target:

- OpenAI Codex-style OAuth, because OpenClaw shows a clean operator value case and the flow is well-defined

Do not start with the hardest provider first.

### Phase 6: provider-specific usage and health

After login and runtime auth work:

- provider status
- profile selection
- usage and quota hooks where justified
- repair hints and reconnect flow

### Phase 7: broader adapter and gateway expansion

Only after phases 1 through 6 are stable:

- Discord runtime breadth
- WhatsApp runtime breadth
- webhook-heavy surfaces
- more complex remote gateway modes

## 15. Commit Checkpoints

Commit often, but keep the changes narrow.

Recommended checkpoints:

1. docs: readiness review and new execution direction
2. spec: auth profile and callback-state schema updates
3. feat: runtime provider resolver skeleton
4. feat: secret-ref and auth-profile persistence
5. feat: OAuth callback service and first provider login
6. test: auth resolver, callback-state, and refresh regression coverage
7. docs: operator runbook for provider login, rotation, and recovery

## 16. Non-Goals For The Next Slice

Do not spend the next slice on:

- finishing full Discord runtime breadth
- finishing full WhatsApp runtime breadth
- adding many providers at once
- a plugin marketplace
- remote multi-tenant gateway claims
- copying OpenClaw or Hermes whole-cloth

## 17. Definition Of Done For This Architecture Slice

This slice is done when all of this is true:

- one shared runtime-provider resolver exists and is used by every model call path
- Spark supports both static API-key auth and at least one secure OAuth flow
- callback state is single-use, auditable, and fail-closed
- doctor and status can explain missing, expiring, failed, and healthy auth states
- channel config points at auth profiles, not raw token assumptions
- the next adapter can reuse the same gateway and auth contracts without inventing new auth glue

## 18. Final Recommendation

The next work should optimize for architectural integrity, not breadth.

Spark now has enough Telegram and operator evidence to stop polishing the same slice and instead lock the next-level contracts:

- gateway route ownership
- provider and auth registry
- OAuth plus API-key coexistence
- one runtime provider resolver

That is the safest path to making Spark Intelligence feel more like a serious gatewayed agent runtime and less like a Telegram-only shell with future auth debt.
