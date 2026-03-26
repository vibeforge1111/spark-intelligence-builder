# Spark Intelligence Builder

Spark Intelligence Builder is the product repo for `Spark Intelligence`: a Spark-native, persistent 1:1 agent system.

It is intentionally not the repo that should absorb the whole Spark ecosystem.

The core idea is simple:

- `Spark Researcher` is the runtime core.
- `Spark Swarm` handles delegation and multi-agent coordination.
- `Domain chips` provide specialization.
- `Specialization paths` shape long-term growth.
- `Autoloop flywheels` improve the agent from repeated use.
- `Telegram`, `WhatsApp`, and `Discord` act as delivery adapters.

This repo should stay focused on:

- runtime shell
- gateway and adapters
- identity, pairing, and operator control
- contracts and bridges into other Spark systems

It should avoid becoming a giant everything-repo by copying the internals of:

- `Spark Researcher`
- `Spark Swarm`
- `domain chip` repos
- `specialization path` repos

This repo currently includes:

- [docs/PRD_SPARK_INTELLIGENCE_V1.md](./docs/PRD_SPARK_INTELLIGENCE_V1.md)
- [docs/ARCHITECTURE_SPARK_INTELLIGENCE_V1.md](./docs/ARCHITECTURE_SPARK_INTELLIGENCE_V1.md)
- [docs/SPARK_INTELLIGENCE_PROMPT_BIBLE.md](./docs/SPARK_INTELLIGENCE_PROMPT_BIBLE.md)
- [docs/CRON_JOB_HARNESS_SPEC_V1.md](./docs/CRON_JOB_HARNESS_SPEC_V1.md)
- [docs/IMPORT_AND_MIGRATION_SPEC_V1.md](./docs/IMPORT_AND_MIGRATION_SPEC_V1.md)
- [docs/CODING_RULESET_V1.md](./docs/CODING_RULESET_V1.md)
- [docs/SKILL_VALIDATION_GUIDE.md](./docs/SKILL_VALIDATION_GUIDE.md)
- [docs/ONBOARDING_CLI_SPEC_V1.md](./docs/ONBOARDING_CLI_SPEC_V1.md)
- [docs/IDENTITY_AND_SESSION_MODEL_SPEC_V1.md](./docs/IDENTITY_AND_SESSION_MODEL_SPEC_V1.md)
- [docs/CONFIG_AND_STATE_SCHEMA_SPEC_V1.md](./docs/CONFIG_AND_STATE_SCHEMA_SPEC_V1.md)
- [docs/OPERATOR_CONTROL_SURFACE_SPEC_V1.md](./docs/OPERATOR_CONTROL_SURFACE_SPEC_V1.md)
- [docs/PROVIDER_AND_AUTH_CONFIG_SPEC_V1.md](./docs/PROVIDER_AND_AUTH_CONFIG_SPEC_V1.md)
- [docs/SECURITY_RESEARCH_PLAN_HERMES_OPENCLAW.md](./docs/SECURITY_RESEARCH_PLAN_HERMES_OPENCLAW.md)
- [docs/SECURITY_DOCTRINE_V1.md](./docs/SECURITY_DOCTRINE_V1.md)
- [docs/OPENCLAW_HERMES_SECURITY_HISTORY_ANALYSIS_2026-03-25.md](./docs/OPENCLAW_HERMES_SECURITY_HISTORY_ANALYSIS_2026-03-25.md)
- [docs/OPENCLAW_HERMES_DEEP_COMPARATIVE_ANALYSIS_2026-03-25.md](./docs/OPENCLAW_HERMES_DEEP_COMPARATIVE_ANALYSIS_2026-03-25.md)
- [docs/SECURITY_HISTORY_THEME_APPENDIX_2026-03-25.md](./docs/SECURITY_HISTORY_THEME_APPENDIX_2026-03-25.md)
- [docs/TELEGRAM_ADAPTER_SPEC_V1.md](./docs/TELEGRAM_ADAPTER_SPEC_V1.md)
- [docs/TELEGRAM_OPERATOR_RUNBOOK_2026-03-26.md](./docs/TELEGRAM_OPERATOR_RUNBOOK_2026-03-26.md)
- [docs/GATEWAY_PROVIDER_AUTH_READINESS_REVIEW_2026-03-26.md](./docs/GATEWAY_PROVIDER_AUTH_READINESS_REVIEW_2026-03-26.md)
- [docs/SPARK_RESEARCHER_INTEGRATION_CONTRACT_V1.md](./docs/SPARK_RESEARCHER_INTEGRATION_CONTRACT_V1.md)
- [docs/SPARK_SWARM_ESCALATION_CONTRACT_V1.md](./docs/SPARK_SWARM_ESCALATION_CONTRACT_V1.md)
- [docs/DOMAIN_CHIP_ATTACHMENT_CONTRACT_V1.md](./docs/DOMAIN_CHIP_ATTACHMENT_CONTRACT_V1.md)
- [docs/IMPLEMENTATION_READINESS_AUDIT_2026-03-25.md](./docs/IMPLEMENTATION_READINESS_AUDIT_2026-03-25.md)
- [docs/IMPLEMENTATION_PLAN_V1.md](./docs/IMPLEMENTATION_PLAN_V1.md)
- [docs/IMPLEMENTATION_STATUS_2026-03-26.md](./docs/IMPLEMENTATION_STATUS_2026-03-26.md)

Key repo skills:

- [skills/reliable-job-harnesses/SKILL.md](./skills/reliable-job-harnesses/SKILL.md)
- [skills/maintainable-engineering/SKILL.md](./skills/maintainable-engineering/SKILL.md)
- [skills/spark-ecosystem-product/SKILL.md](./skills/spark-ecosystem-product/SKILL.md)
- [skills/agent-landscape-analysis/SKILL.md](./skills/agent-landscape-analysis/SKILL.md)
- [skills/security-systems/SKILL.md](./skills/security-systems/SKILL.md)
- [skills/security-auditor/SKILL.md](./skills/security-auditor/SKILL.md)

Validation support:

- `python scripts/validate_skills.py`
- [scenario-packs/reliable-job-harnesses/README.md](./scenario-packs/reliable-job-harnesses/README.md)

Current runtime shell:

```bash
pip install -e .
spark-intelligence setup
spark-intelligence status
spark-intelligence operator set-bridge researcher disabled
spark-intelligence operator review-pairings
spark-intelligence auth providers
spark-intelligence auth connect openai --api-key <key> --model <model>
spark-intelligence auth connect openrouter --api-key-env WORK_OPENROUTER_KEY --model anthropic/claude-3.7-sonnet
spark-intelligence auth login openai-codex --listen
spark-intelligence gateway oauth-callback --provider openai-codex
spark-intelligence auth refresh openai-codex
spark-intelligence auth logout openai-codex
spark-intelligence auth status
spark-intelligence jobs list
spark-intelligence jobs tick
spark-intelligence channel telegram-onboard
spark-intelligence channel add discord --bot-token <token> --allowed-user <id>
spark-intelligence channel add whatsapp --bot-token <token> --allowed-user <id>
spark-intelligence config set spark.researcher.runtime_root "C:/Users/USER/Desktop/spark-researcher"
spark-intelligence swarm configure --api-url https://your-swarm-host --workspace-id <workspace_id> --access-token <token>
spark-intelligence doctor
spark-intelligence agent inspect
spark-intelligence pairings list
spark-intelligence sessions list
spark-intelligence gateway start
```

`setup` now auto-detects local `spark-researcher`, `spark-swarm`, domain-chip, and specialization-path repos on the Desktop when they are present. It can also wire hosted Swarm access in one command:

```bash
spark-intelligence setup \
  --swarm-api-url https://your-swarm-host \
  --swarm-workspace-id <workspace_id> \
  --swarm-access-token <token>
```

Model-provider auth now has a first-class provider registry plus default auth-profile layer. `auth providers` shows the supported auth methods and execution transport, `auth connect` writes a canonical API-key-backed profile such as `openai:default` or `anthropic:default`, `auth login openai-codex --listen` now completes through the same gateway-owned callback surface exposed by `gateway oauth-callback`, `auth refresh openai-codex` rotates the locally stored OAuth access token when a refresh token is present, `auth logout openai-codex` revokes the locally stored OAuth profile, and `auth status` now surfaces expiry and refresh state so the configured provider auth can be inspected before runtime use. Tokens that are close to expiry are now marked `expiring_soon`, `doctor` flags stale OAuth maintenance explicitly, `jobs list` shows the last maintenance result, and `jobs tick` runs the built-in OAuth maintenance job before tokens fail closed.

OAuth-backed runtime resolution now fails closed on expired access tokens. If a stored OAuth token has expired, `auth status` marks it as `expired`, `doctor` degrades with the provider id and failing auth state, and runtime provider selection refuses to silently continue with stale credentials.

`gateway status` and unified `status` now also surface provider auth method, auth state, execution transport, and OAuth-maintenance health in one place. That keeps the current architecture decision operationally visible: API-key-backed providers stay on `direct_http`, while Codex/OAuth stays on `external_cli_wrapper`.

The Spark Researcher bridge is now provider-aware on the live path. When a provider is configured and resolvable, Spark uses that runtime selection to choose the advisory model family and run real provider execution instead of always falling back to `generic`. API-key-backed providers now execute through Spark's direct HTTP wrapper path, while the Codex/OAuth branch stays on the external CLI-wrapper transport until there is a first-class direct OAuth runtime with the same security guarantees. If provider auth is configured but unresolved, the bridge fails closed.

Telegram setup is BotFather-first and DM-first. The guided path is:

```bash
spark-intelligence channel telegram-onboard
spark-intelligence channel telegram-onboard --bot-token <token> --allowed-user <telegram_user_id>
spark-intelligence channel add telegram --bot-token <token> --allowed-user <telegram_user_id>
spark-intelligence channel test telegram
```

`channel telegram-onboard` prints the BotFather steps when no token is provided, and validates the token before storing it when a token is provided. `channel add telegram` stays scriptable, but now validates the token by default unless `--skip-validate` is explicitly used.
`channel test telegram` rechecks the stored token, refreshes Telegram auth health, and shows the configured bot identity and pairing posture without starting the gateway.
Configured `--allowed-user` entries are explicit allowlist access, not implicit operator-approved pairings. Allowlisted users can DM immediately, but they do not appear in pairing review unless the operator explicitly approves them.
Re-running `channel telegram-onboard` or `channel add telegram` now preserves existing status, pairing mode, and bot auth linkage by default, so token rotation does not silently widen access or re-enable a paused channel.
Narrowing the configured allowlist also removes stale config-driven access on later messages instead of leaving old users authorized in local state.
If you need to intentionally clear all configured allowlist entries while preserving the rest of the channel posture, use `--clear-allowed-users`.

The practical live-ops flow for Telegram onboarding, pairing approval, token rotation, and recovery is documented in [docs/TELEGRAM_OPERATOR_RUNBOOK_2026-03-26.md](./docs/TELEGRAM_OPERATOR_RUNBOOK_2026-03-26.md).

Telegram runtime verification is available in two forms:

```bash
spark-intelligence gateway simulate-telegram-update ./sample-update.json
spark-intelligence gateway simulate-discord-message ./sample-discord-message.json
spark-intelligence gateway simulate-whatsapp-message ./sample-whatsapp-message.json
spark-intelligence gateway start --once --poll-timeout-seconds 0
spark-intelligence gateway start --continuous
spark-intelligence gateway traces --limit 20
spark-intelligence gateway traces --channel-id telegram --event telegram_pending_pairing
spark-intelligence gateway outbound --limit 20
spark-intelligence gateway outbound --channel-id telegram --delivery failed
```

Config can be inspected and updated without editing `config.yaml` manually:

```bash
spark-intelligence config show
spark-intelligence config show --path spark.researcher --json
spark-intelligence config set spark.researcher.config_path "C:/Users/USER/Desktop/spark-researcher/spark-researcher.project.json"
spark-intelligence config unset spark.researcher.config_path
```

Spark Swarm stays manual-first in v1. The builder can export the latest real collective payload from `spark-researcher` and sync it without absorbing Swarm internals:

```bash
spark-intelligence swarm status
spark-intelligence swarm configure --api-url https://your-swarm-host --workspace-id <workspace_id> --access-token <token>
spark-intelligence swarm sync --dry-run
spark-intelligence swarm sync
spark-intelligence swarm evaluate "Break this into a multi-step parallel research workflow"
```

Chip and specialization-path attachments stay external as well. Spark Intelligence scans configured roots first, then falls back to Desktop auto-discovery for repos such as `domain-chip-*` and `specialization-path-*`:

```bash
spark-intelligence attachments status
spark-intelligence attachments list --kind chip
spark-intelligence attachments list --kind path --json
spark-intelligence attachments add-root chips "C:/Users/USER/Desktop/domain-chip-content"
spark-intelligence attachments add-root paths "C:/Users/USER/Desktop/specialization-path-startup-operator"
spark-intelligence attachments activate-chip content
spark-intelligence attachments pin-chip startup-yc
spark-intelligence attachments set-path startup-operator
spark-intelligence attachments snapshot --json
spark-intelligence agent inspect
```

The attachment snapshot is written to `SPARK_INTELLIGENCE_HOME/attachments.snapshot.json` and mirrored into SQLite runtime state so external Spark repos can consume the current attachment set without importing this repo's internals.

The Spark Researcher bridge now also includes a compact attachment context envelope derived from that snapshot, so advisory requests can stay aware of active chips and the active specialization path without this repo importing chip logic.

```bash
spark-intelligence researcher status
spark-intelligence researcher status --json
```

```bash
spark-intelligence operator set-bridge researcher enabled
spark-intelligence operator set-bridge swarm disabled
spark-intelligence operator set-channel telegram paused
spark-intelligence operator review-pairings
spark-intelligence operator review-pairings --channel-id telegram --status pending
spark-intelligence operator pairing-summary telegram
spark-intelligence operator hold-pairing telegram 123456
spark-intelligence operator approve-pairing telegram 123456
spark-intelligence operator approve-latest telegram
spark-intelligence operator hold-latest telegram
spark-intelligence operator revoke-latest telegram
spark-intelligence operator inbox
spark-intelligence operator security
spark-intelligence operator history
spark-intelligence operator history --action approve_latest_pairing --target-kind pairing
```

`operator inbox` now emits direct recommended commands for each actionable item so the operator surface stays lightweight and local-first without a separate ticketing subsystem.
`operator security` also reads durable bridge failure counters and last-failure metadata from local state, not just recent logs.
Both `operator inbox` and `operator security` now also surface provider-auth reconnect actions, so expired, revoked, or refresh-error OAuth states point directly at `auth refresh` or `auth login --listen` instead of requiring manual diagnosis from `auth status`.
For `expiring_soon` OAuth states, the recommended first repair path is `jobs tick`, because OAuth maintenance is intentionally operator-driven and auditable in the current design.
Telegram pending pairings now also carry lightweight local context, so `operator review-pairings` can show the most recent Telegram username, chat id, and last inbound message preview.
`operator review-pairings` now also supports lightweight `--channel-id`, `--status`, and `--limit` filters so the queue stays usable once there are multiple channels or repeated onboarding attempts.
`operator pairing-summary telegram` provides a compact channel-level view of pending, held, approved, and revoked pairing state in one command.
`operator revoke-latest telegram` provides the same fast-path ergonomics as approve/hold for denying the newest pending or held Telegram request without touching approved pairings.
The fast-path pairing commands now also write exact `channel:user` targets into `operator history`, so local audit trails stay specific instead of logging only the channel name.
`operator history` now also supports lightweight `--action`, `--target-kind`, and `--contains` filters so local audit review stays usable as the event log grows.
After approval, the first successful Telegram reply also carries a one-time "pairing approved" welcome so the user gets a cleaner handoff into the active agent.
Held, revoked, paused, disabled, and generic blocked Telegram DMs now also return explicit user-facing replies instead of failing silently.
`gateway traces` and `gateway outbound` now support lightweight filters like `--channel-id`, `--event`, `--user`, and `--delivery` so Telegram onboarding failures can be narrowed quickly without another dashboard.

Telegram ingress now also applies lightweight runtime guardrails:
- duplicate update suppression
- per-user rate limiting
- outbound reply truncation and secret-like reply blocking

Those guardrails now live in shared gateway helpers so future adapters can inherit the same local-first safety behavior instead of re-implementing it.

Telegram runtime health is also persisted locally now:
- last auth check state
- last poll failure type/message
- consecutive poll failures with bounded backoff
- cleaner `/start` pairing response for first-contact users

That health flows into `gateway status`, `operator inbox`, and `operator security` so Telegram problems stay visible without another background subsystem.

## Current Status

The repo is now beyond pure planning.

The current build already includes:

- working package and CLI
- canonical config and SQLite state
- Telegram live runtime
- Spark Researcher bridge
- Spark Swarm sync and evaluation bridge
- attachment snapshot support for chips and specialization paths
- operator pairing, audit, and review controls
- a repeatable `tests/` suite for CLI smoke, operator flows, observability, and Telegram failure paths

Today’s implementation summary is recorded in [docs/IMPLEMENTATION_STATUS_2026-03-26.md](./docs/IMPLEMENTATION_STATUS_2026-03-26.md).

Today’s execution order is recorded in [docs/IMPLEMENTATION_WORKPLAN_2026-03-26.md](./docs/IMPLEMENTATION_WORKPLAN_2026-03-26.md).

The next architecture pass for gateway, provider auth, OAuth, and runtime model routing is recorded in [docs/GATEWAY_PROVIDER_AUTH_READINESS_REVIEW_2026-03-26.md](./docs/GATEWAY_PROVIDER_AUTH_READINESS_REVIEW_2026-03-26.md).

## Current Start

Current work should continue with gateway and provider-auth architecture locking, not more adapter breadth.

The exact first move is:

1. lock the provider registry, auth-profile model, callback-state model, and route-registry contract
2. add one shared runtime-provider resolver used by CLI, gateway, and future bridge execution
3. implement secure OAuth alongside static API-key auth without weakening existing Telegram/operator safety
4. expand Discord or WhatsApp only after those contracts are real and regression-covered

Do not start with live Discord or WhatsApp runtime work before that.
