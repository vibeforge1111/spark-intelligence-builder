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
- [docs/TELEGRAM_COMMAND_REFERENCE_2026-04-09.md](./docs/TELEGRAM_COMMAND_REFERENCE_2026-04-09.md)
- [docs/PERSONALITY_VOICE_SYSTEM_BOUNDARY_2026-04-09.md](./docs/PERSONALITY_VOICE_SYSTEM_BOUNDARY_2026-04-09.md)
- [docs/DISCORD_OPERATOR_RUNBOOK_2026-03-26.md](./docs/DISCORD_OPERATOR_RUNBOOK_2026-03-26.md)
- [docs/GATEWAY_PROVIDER_AUTH_READINESS_REVIEW_2026-03-26.md](./docs/GATEWAY_PROVIDER_AUTH_READINESS_REVIEW_2026-03-26.md)
- [docs/NEXT_EXECUTION_PLAN_2026-03-26.md](./docs/NEXT_EXECUTION_PLAN_2026-03-26.md)
- [docs/GATEWAY_RUNTIME_OPERATOR_RECOVERY_REVIEW_2026-03-26.md](./docs/GATEWAY_RUNTIME_OPERATOR_RECOVERY_REVIEW_2026-03-26.md)
- [docs/SYSTEM_CONNECTION_AND_PRODUCTIZATION_PLAN_2026-03-26.md](./docs/SYSTEM_CONNECTION_AND_PRODUCTIZATION_PLAN_2026-03-26.md)
- [docs/SPARK_RESEARCHER_INTEGRATION_CONTRACT_V1.md](./docs/SPARK_RESEARCHER_INTEGRATION_CONTRACT_V1.md)
- [docs/SPARK_SWARM_ESCALATION_CONTRACT_V1.md](./docs/SPARK_SWARM_ESCALATION_CONTRACT_V1.md)
- [docs/DOMAIN_CHIP_ATTACHMENT_CONTRACT_V1.md](./docs/DOMAIN_CHIP_ATTACHMENT_CONTRACT_V1.md)
- [docs/IMPLEMENTATION_READINESS_AUDIT_2026-03-25.md](./docs/IMPLEMENTATION_READINESS_AUDIT_2026-03-25.md)
- [docs/IMPLEMENTATION_PLAN_V1.md](./docs/IMPLEMENTATION_PLAN_V1.md)
- [docs/IMPLEMENTATION_STATUS_2026-03-26.md](./docs/IMPLEMENTATION_STATUS_2026-03-26.md)
- [docs/STATUS_HANDOFF_2026-03-29.md](./docs/STATUS_HANDOFF_2026-03-29.md)
- [docs/STATUS_HANDOFF_2026-04-08.md](./docs/STATUS_HANDOFF_2026-04-08.md)
- [docs/STATUS_HANDOFF_2026-04-09.md](./docs/STATUS_HANDOFF_2026-04-09.md)
- [docs/TELEGRAM_COMMUNICATION_AND_EVOLUTION_PLAN_2026-04-09.md](./docs/TELEGRAM_COMMUNICATION_AND_EVOLUTION_PLAN_2026-04-09.md)
- [docs/CONTINUATION_PLAN_2026-04-09.md](./docs/CONTINUATION_PLAN_2026-04-09.md)
- [docs/SPARK_SYSTEM_REGISTRY_AND_MISSION_CONTROL_PLAN_2026-04-09.md](./docs/SPARK_SYSTEM_REGISTRY_AND_MISSION_CONTROL_PLAN_2026-04-09.md)
- [docs/MEMORY_REALTIME_BENCHMARK_PROGRAM_2026-04-11.md](./docs/MEMORY_REALTIME_BENCHMARK_PROGRAM_2026-04-11.md)
- [docs/MEMORY_BENCHMARK_HANDOFF_2026-04-11.md](./docs/MEMORY_BENCHMARK_HANDOFF_2026-04-11.md)
- [docs/NEXT_48H_MEMORY_EXECUTION_PLAN_2026-04-11.md](./docs/NEXT_48H_MEMORY_EXECUTION_PLAN_2026-04-11.md)
- [docs/MEMORY_FAILURE_LEDGER_2026-04-11.md](./docs/MEMORY_FAILURE_LEDGER_2026-04-11.md)
- [docs/MEMORY_EXECUTION_PLAN_2026-04-10.md](./docs/MEMORY_EXECUTION_PLAN_2026-04-10.md)
- [docs/SPARK_MEMORY_KB_ROLLOUT_PLAN_2026-04-10.md](./docs/SPARK_MEMORY_KB_ROLLOUT_PLAN_2026-04-10.md)

Current memory benchmarking program:

- default contenders are `summary_synthesis_memory` and `dual_store_event_calendar_hybrid`
- current pinned runtime selector is `summary_synthesis_memory`
- latest clean live `14/14` soak favors `summary_synthesis_memory` at `92/92` overall and `64/64` on selector packs
- the latest offline ProductMemory benchmark is tied between `summary_synthesis_memory` and `dual_store_event_calendar_hybrid` at `1156/1266`
- the runtime is now pinned to `summary_synthesis_memory` because it leads live Telegram and no longer trails offline ProductMemory on accuracy
- soak runs now enforce a per-pack timeout so one hung Telegram regression cannot freeze the full benchmark suite
- benchmark upgrades are not promoted on offline scorecards alone
- the same contenders must also stay green on live Telegram regression and soak runs

Operator shortcut:

- `powershell -ExecutionPolicy Bypass -File .\scripts\run_memory_two_contender_validation.ps1`
- `powershell -ExecutionPolicy Bypass -File .\scripts\run_memory_automation_tests.ps1`
- `powershell -ExecutionPolicy Bypass -File .\scripts\run_memory_validated_full_cycle.ps1`
- by default it writes each full run into a timestamped artifact root under `.spark-intelligence\artifacts\memory-validation-runs\`
- use `run_memory_automation_tests.ps1` for fast wrapper/renderer/harness regression checks without running the live Telegram soak
- use `run_memory_validated_full_cycle.ps1` when you want the fast automation preflight and the real full validation in one command
- it also refreshes `.spark-intelligence\artifacts\memory-validation-runs\latest-run.json` to point at the newest run manifest
- it refreshes `.spark-intelligence\artifacts\memory-validation-runs\latest-full-run.json` only when a full benchmark + regression + soak run exists
- use `-SkipBaselinePublish` on ad hoc smoke runs when you do not want a short or experimental full run to replace the canonical published baseline
- `run_memory_validated_full_cycle.ps1` now auto-skips baseline publishing on non-default runs such as custom output roots or shortened soaks; use `-PublishBaseline` only when you intentionally want that run to replace the canonical published baseline
- on full runs it also preserves `.spark-intelligence\artifacts\memory-validation-runs\previous-full-run.json` and writes a per-run `validation-delta.md`
- it also auto-refreshes [docs/MEMORY_FAILURE_LEDGER_2026-04-11.md](./docs/MEMORY_FAILURE_LEDGER_2026-04-11.md) from the newest run
- it also auto-refreshes the baseline sections in [README.md](./README.md), [docs/MEMORY_LIVE_VALIDATION_RESULTS_2026-04-11.md](./docs/MEMORY_LIVE_VALIDATION_RESULTS_2026-04-11.md), and [docs/MEMORY_BENCHMARK_HANDOFF_2026-04-11.md](./docs/MEMORY_BENCHMARK_HANDOFF_2026-04-11.md) from the newest full run
- if `domain-chip-memory` is available next to this repo, full runs also auto-refresh its top-level Builder-alignment docs from the same `latest-full-run.json` pointer
<!-- AUTO_MEMORY_BASELINE_README_START -->
- current clean full-run baseline:
  `.spark-intelligence\artifacts\memory-validation-runs\20260412-023241`
- current canonical full-run pointer:
  `.spark-intelligence\artifacts\memory-validation-runs\latest-full-run.json`
- expected full validation cost from the latest clean run:
  - benchmark: `13.543s`
  - regression: `23.724s`
  - soak: `339.130s`
  - total: `376.594s`
<!-- AUTO_MEMORY_BASELINE_README_END -->

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
spark-intelligence connect status
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
spark-intelligence swarm configure --api-url https://your-swarm-host --workspace-id <workspace_id> --access-token <token> --refresh-token <refresh_token> --auth-client-key-env SPARK_SWARM_AUTH_CLIENT_KEY
spark-intelligence doctor
spark-intelligence agent inspect
spark-intelligence pairings list
spark-intelligence sessions list
spark-intelligence gateway start
# Telegram DM controls:
# /think
# /think on
# /think off
# /style
# /style status
# /style history
# /style savepoints
# /style savepoint <name>
# /style diff <name>
# /style restore <name>
# /style presets
# /style preset <name>
# /style undo
# /style score
# /style examples
# /style compare
# /style before-after <instruction>
# /style test
# /style train <instruction>
# /style feedback <note>
# /style good <note>
# /style bad <note>
# /voice
# /voice plan
# /voice reply
# /voice reply on
# /voice reply off
# /voice speak <text>
# /chip
# /chip status [chip_key]
# /chip evaluate <chip_key> [text|key=value ...|json]
# /chip suggest <chip_key> [text|key=value ...|json]
# /chip autoloop <chip_key>
# /swarm
# /swarm status
# /swarm overview
# /swarm live
# /swarm runtime
# /swarm upgrades
# /swarm issues
# /swarm inbox
# /swarm collective
# /swarm sync
# /swarm evaluate <task>
```

The first supported phase-E bootstrap profile is now:

```bash
spark-intelligence bootstrap telegram-agent \
  --provider custom \
  --api-key-env CUSTOM_API_KEY \
  --model MiniMax-M2.7 \
  --base-url https://api.minimax.io/v1 \
  --bot-token-env TELEGRAM_BOT_TOKEN
```

That command reuses the proven pieces already in this repo: config/state bootstrap, local Spark repo autodetection, API-key provider connect, Telegram channel setup, and the supported continuous run command. It also records the supported install profile in local config so the productization phase can see whether a home has actually been bootstrapped or only assembled manually.

The matching supported always-on run wrapper on Windows is now:

```bash
spark-intelligence install-autostart --home .tmp-home-live-telegram-real
spark-intelligence uninstall-autostart --home .tmp-home-live-telegram-real
```

That path uses native Windows Task Scheduler entries instead of a custom daemon layer and persists the installed wrapper metadata in local config so `status` and the productization phase can see whether the canonical home is still running in foreground-only mode or has a supported always-on wrapper installed.

`setup` now auto-detects local `spark-researcher`, `spark-swarm`, domain-chip, and specialization-path repos on the Desktop when they are present. It can also wire hosted Swarm access in one command:

```bash
spark-intelligence setup \
  --swarm-api-url https://your-swarm-host \
  --swarm-workspace-id <workspace_id> \
  --swarm-access-token <token>
```

After bootstrap, `spark-intelligence connect status` is the quickest way to see the current connection phase, the live blocker for the next phase, and the next command to run.

Model-provider auth now has a first-class provider registry plus default auth-profile layer. `auth providers` shows the supported auth methods and execution transport, `auth connect` writes a canonical API-key-backed profile such as `openai:default` or `anthropic:default`, `auth login openai-codex --listen` now completes through the same gateway-owned callback surface exposed by `gateway oauth-callback`, `auth refresh openai-codex` rotates the locally stored OAuth access token when a refresh token is present, `auth logout openai-codex` revokes the locally stored OAuth profile, and `auth status` now surfaces expiry and refresh state so the configured provider auth can be inspected before runtime use. Tokens that are close to expiry are now marked `expiring_soon`, `doctor` flags stale OAuth maintenance explicitly, `jobs list` shows the last maintenance result, and `jobs tick` runs the built-in OAuth maintenance job before tokens fail closed.

OAuth-backed runtime resolution now fails closed on expired access tokens. If a stored OAuth token has expired, `auth status` marks it as `expired`, `doctor` degrades with the provider id and failing auth state, and runtime provider selection refuses to silently continue with stale credentials.

`gateway status` and unified `status` now also surface provider auth method, runtime-provider readiness, execution transport, and OAuth-maintenance health in one place. That keeps the current architecture decision operationally visible: API-key-backed providers stay on `direct_http`, while Codex/OAuth stays on `external_cli_wrapper`.

`doctor` now also distinguishes between `provider-runtime` and `provider-execution`. If the selected runtime provider cannot actually be resolved, Spark degrades before message handling instead of deferring the failure to the first inbound request. If `openai-codex` is configured while the researcher bridge is disabled or unavailable, Spark also degrades with a separate `provider-execution` failure instead of pretending the wrapper-backed path is usable.

`gateway start` now follows those same rules and fails closed before polling when runtime-provider readiness or provider-execution readiness is degraded. That keeps missing secrets, expired default OAuth profiles, no-default-provider config drift, and wrapper-backed Codex auth from looking healthy until the first inbound message proves otherwise.

The gateway-owned OAuth callback listener now also rejects malformed callback requests at the HTTP edge and only captures OAuth-shaped requests with one `state` plus exactly one outcome (`code` or `error`). Provider-denied callbacks are consumed once and surfaced as explicit auth failures instead of collapsing into a vague missing-`code` error.

The shared route registry now also carries ingress contracts for future adapter webhooks. `oauth_callback` routes are GET-only, and `adapter_webhook` routes must be POST-only and declare allowed request content types so future HTTP handlers can fail closed before adapter-specific parsing runs.

The first real webhook skeleton now uses that contract for Discord. `/webhooks/discord` is registered as POST `application/json`, request validation happens before JSON decoding, and Discord ingress now prefers the real signed-request model with `X-Signature-Ed25519` plus `X-Signature-Timestamp` verified against the configured Discord interaction public key. The older static `X-Spark-Webhook-Secret` message-shaped path is now treated as explicit compatibility mode and must be enabled with `channel add discord --allow-legacy-message-webhook`; otherwise it fails closed. `doctor`, `gateway status`, and unified `status` now surface that ingress mode explicitly so operators can tell whether Discord is running on signed interactions, legacy compatibility, or a broken no-ingress config. Signed Discord `PING` requests now complete end to end, and the Discord v1 DM command contract is now explicit: chat-input `APPLICATION_COMMAND` requests must use `/spark message:<text>` before they route through the existing DM bridge and return a guarded interaction callback. Guild interaction handling is still intentionally rejected in v1, and rejected Discord auth attempts now land in `gateway traces` with explicit reasons plus operator-visible webhook alerts in `operator inbox` and `operator security`.

WhatsApp now follows the same readiness principle at a simpler boundary: `doctor` and `gateway status` surface whether the adapter has both pieces of the Meta-style webhook contract configured, the POST app secret and the GET verify token, or is currently in a broken partial/no-ingress state.

The first WhatsApp webhook skeleton is now also in place on `/webhooks/whatsapp`. It now supports the Meta-style GET verification handshake with `hub.challenge`, verifies POST payload authenticity using `X-Hub-Signature-256`, fails closed on malformed JSON, and only accepts one explicit Meta text-message event at a time before routing it through the existing simulated WhatsApp bridge. Stub-shaped payloads, mixed batch payloads, and unsupported change families are ignored instead of being normalized opportunistically, and both ignored ingress decisions and rejected auth/verification attempts now land in `gateway traces` with explicit reasons plus operator-visible webhook alerts in `operator inbox` and `operator security`.

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
spark-intelligence gateway ask-telegram "What are you connected to right now?"
spark-intelligence gateway simulate-discord-message ./sample-discord-message.json
spark-intelligence gateway simulate-whatsapp-message ./sample-whatsapp-message.json
spark-intelligence gateway start --once --poll-timeout-seconds 0
spark-intelligence gateway start --continuous
spark-intelligence gateway traces --limit 20
spark-intelligence gateway traces --channel-id telegram --event telegram_pending_pairing
spark-intelligence gateway outbound --limit 20
spark-intelligence gateway outbound --channel-id telegram --delivery failed
```

There is also an internal operator-only terminal-to-Telegram bridge for probing the Telegram runtime directly from the CLI, but it should remain disabled by default and should not be treated as a normal end-user surface.
`gateway ask-telegram` sends one synthetic private Telegram message through Builder's real Telegram runtime path and prints Spark's reply locally. It remains an internal operator-only bridge and should stay disabled by default. Pass `--user-id` when the home has multiple Telegram users; otherwise the command will reuse the most recent Telegram user or the single configured allowlisted user when it can infer one safely.

For local recovery on Windows, use [`start-telegram.ps1`](C:/Users/USER/Desktop/spark-intelligence-builder/start-telegram.ps1). The default invocation tests Telegram auth against `.tmp-home-live-telegram-real` and then starts the continuous gateway:

```powershell
.\start-telegram.ps1
```

Production ingress ownership rule:

- only one runtime may long-poll one Telegram bot token at a time
- today, the implemented production Telegram ingress is still `spark-intelligence` Builder, not `spark-swarm`
- do not run a second Telegram poller against the same bot token from another runtime, browser automation surface, or test harness
- if `spark-swarm` later gains real Telegram ingress ownership, Builder must stop polling that same bot token and remain downstream for reasoning, chips, and governed browser execution
- if direct Builder Telegram testing is needed alongside another ingress owner later, use a separate staging bot token instead of dual-polling the production bot

Live Telegram system shape:

- Telegram DMs land in Builder's Telegram runtime first
- Builder owns pairing, identity, operator controls, channel delivery, and bot-token polling
- Builder then decides whether the message should stay local as a runtime command, go through provider-backed reasoning, use chip-guided browser work, or trigger a Swarm recommendation
- `Spark Swarm` stays downstream from Builder for evaluation and collective sync, not as the current Telegram ingress owner
- `spark-browser-extension` stays downstream from Builder for governed Brave/browser execution

In practice, the current live stack is:

1. Telegram user sends a DM to the production bot.
2. Builder receives the update and applies pairing and operator policy.
3. If the message is a runtime command such as `/swarm status`, `/swarm evaluate <task>`, or `/swarm sync`, Builder handles it directly and returns the result to Telegram.
4. If the message needs reasoning or browsing, Builder routes into the active chips and provider bridge.
5. If browser evidence is needed, Builder calls the `spark-browser-extension` attachment and uses the governed browser hooks against the dedicated Brave profile.
6. If Swarm escalation or collective export is needed, Builder calls the Swarm bridge and returns the downstream result back to Telegram.

Live commands verified on the production-shaped home:

- `/style status`
- `/style history`
- `/style savepoints`
- `/style savepoint <name>`
- `/style diff <name>`
- `/style restore <name>`
- `/style presets`
- `/style preset <name>`
- `/style undo`
- `/style score`
- `/style examples`
- `/style compare`
- `/style before-after <instruction>`
- `/style train <instruction>`
- `/style feedback <note>`
- `/voice`
- `/voice plan`
- `/voice reply`
- `/voice speak <text>`
- `/swarm status`
- `/swarm evaluate <task>`
- `/swarm sync`
- `/swarm overview`
- `/swarm live`
- `/swarm runtime`
- `/swarm specializations`
- `/swarm insights`
- `/swarm masteries`
- `/swarm upgrades`
- `/swarm inbox`
- `/swarm collective`

Current operator checkpoint:

- Telegram natural-language Swarm reads, hosted actions, and local autoloop/session control are live through Builder
- `spark-browser-extension` remains the downstream governed browser runtime for Telegram browse/search tasks
- `startup-operator` autoloop control is live, but real score-improving Startup Bench autoloops are intentionally blocked until the benchmark can consume repo-owned mutations
- the current tomorrow handoff is documented in [docs/STATUS_HANDOFF_2026-04-09.md](./docs/STATUS_HANDOFF_2026-04-09.md)
- the focused next-step plan for communication quality and visible agent evolution is documented in [docs/TELEGRAM_COMMUNICATION_AND_EVOLUTION_PLAN_2026-04-09.md](./docs/TELEGRAM_COMMUNICATION_AND_EVOLUTION_PLAN_2026-04-09.md)
- the Telegram plus specialization-path onboarding and troubleshooting runbook is documented in [docs/TELEGRAM_SWARM_SPECIALIZATION_PATH_RUNBOOK_2026-04-10.md](./docs/TELEGRAM_SWARM_SPECIALIZATION_PATH_RUNBOOK_2026-04-10.md)

Local Swarm bridge commands now available through Builder Telegram:

- `/swarm paths`
- `/swarm run <path_key>`
- `/swarm autoloop <path_key> [rounds <n>]`
- `/swarm continue <path_key> [session <id>] [rounds <n>]`
- `/swarm sessions <path_key>`
- `/swarm session <path_key> [latest|<session_id>]`
- `/swarm rerun [path_key]`

Those local-bridge commands map onto the existing `spark-swarm` specialization-path system rather than inventing a second loop kernel in Builder. The current Telegram surface now covers:

- attached path discovery
- specialization-path benchmark runs
- bounded autoloop start
- autoloop continuation through saved session ids
- session and round-history inspection through local session summaries
- latest rerun-request execution

The current command surface still returns bounded summaries, not raw bridge logs. Long-running path execution remains explicit and operator-shaped rather than being silently inferred from vague natural language.

Lane-scoped Swarm reads are also available now for attached specialization labels and keys:

- `/swarm insights <specialization>`
- `/swarm masteries <specialization>`
- `/swarm upgrades <specialization>`

That means the bot can inspect one lane directly instead of only returning the workspace-wide summary list before you act on a specific insight, mastery, or upgrade.

The Telegram runtime also accepts bounded natural-language equivalents for those commands when the message makes the intent explicit, for example:

- `Can you show me my current style?`
- `What style changes have you saved?`
- `What style savepoints do I have?`
- `Save style savepoint named checkpoint one`
- `Compare my style to savepoint checkpoint one`
- `Restore style savepoint named checkpoint one`
- `What style presets are available?`
- `Set style preset to claude-like`
- `Undo the last style change`
- `Score my style`
- `Show me my style examples`
- `Compare my style`
- `Show me style before and after for be more direct and keep replies short`
- `Train your style to be more direct and keep replies short`
- `Be more Claude-like in conversation continuity`
- `That was too verbose`
- `Less canned and more grounded follow-up questions`
- `What is the voice status?`
- `Turn voice replies on`
- `Please speak this out loud: <text>`
- `What is the thinking status?`
- `Turn thinking on`
- `Turn thinking off`
- `Can you show me the swarm status?`
- `Show me swarm overview`
- `Show me the swarm runtime pulse`
- `What upgrades are pending in swarm?`
- `What is in the swarm inbox?`
- `Summarize the collective in swarm`
- `Please sync with swarm`
- `Can you evaluate this for swarm: <task>`
- `Show me swarm paths`
- `Show me Startup Operator insights in swarm`
- `/swarm masteries Startup Operator`
- `Show me pending Startup Operator upgrades in swarm`
- `Run the Startup Operator path in swarm`
- `Start autoloop for Startup Operator in swarm for 2 rounds`
- `Continue the Startup Operator autoloop in swarm for 1 more round`
- `Show me the latest Startup Operator autoloop session`
- `Execute the latest Startup Operator rerun request in swarm`

Recommended live operator loop:

- use the agent normally before adding more style instructions
- save style feedback from real replies that feel off, not synthetic memory-probe loops
- prefer concrete feedback like `too polished`, `too generic`, `be more grounded`, or `ask fewer follow-up questions`
- use `/style history`, `/style score`, `/style examples`, and `/style compare` to inspect drift only after a few real exchanges
- treat voice quality separately from text personality: Builder owns the live persona, and `domain-chip-voice-comms` handles STT/TTS around it

Current Telegram voice behavior:

- voice and audio messages are transcribed through `domain-chip-voice-comms`
- voice-origin turns auto-reply with audio when TTS succeeds
- `/voice reply on` enables audio replies for later text turns in that DM
- Builder keeps the normal Telegram caption text but sends a voice-shaped spoken variant into `voice.speak` so audio replies sound more natural than raw text read aloud
- Telegram voice replies should use Telegram voice-note delivery, not generic MP3/document delivery
- the live path now requests Telegram-targeted Opus audio from `domain-chip-voice-comms` and delivers it with Telegram `sendVoice`
- keep this contract when building future voice systems, because the older MP3 path caused degraded playback behavior and did not match the old Openclaw Telegram setup

`status` now also surfaces the last bridge routing decision plus the last active chip route, and the text forms of `gateway traces` and `gateway outbound` now include `route=...` and `chip=...` when that metadata is available. That makes it possible to see whether a Telegram reply came from researcher advisory, direct provider fallback, or another bridge path without dropping to raw JSON.

The current Telegram command reference now lives in [docs/TELEGRAM_COMMAND_REFERENCE_2026-04-09.md](./docs/TELEGRAM_COMMAND_REFERENCE_2026-04-09.md). When new Telegram runtime commands are added, the repo rule is:

- add the slash command first
- add bounded natural-language equivalents only when the intent is reliably recognizable
- map both forms onto the same internal command handler
- add simulation coverage for both forms
- update the command reference in the same change

`spark-intelligence connect route-policy` is the matching operator-facing summary for that behavior. It explains when Spark Intelligence stays on external Researcher advisory, when it falls back to direct provider chat, when it executes directly through the configured provider, and when Spark Swarm escalation should be considered.

Those same surfaces now also distinguish "Swarm token is configured" from "hosted Swarm is actually accepting the current session", and now also distinguish an expired one-hour access token from a refreshable session. `connect status`, `connect route-policy`, and `swarm status` surface `configured`, `expired`, `refreshable`, or `auth_rejected` instead of treating any token-shaped value as effectively ready.

`spark-intelligence connect set-route-policy` is the matching operator control surface. It currently lets the operator tune:

- whether short under-supported conversational traffic can use direct provider fallback
- the maximum message length that still qualifies for that fallback
- whether automatic Spark Swarm recommendation is enabled
- the long-task word-count threshold used by the Swarm evaluator

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
spark-intelligence swarm configure --api-url https://your-swarm-host --workspace-id <workspace_id> --access-token <token> --refresh-token <refresh_token> --auth-client-key-env SPARK_SWARM_AUTH_CLIENT_KEY
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
spark-intelligence attachments run-hook evaluate --chip-key startup-yc --payload-json "{\"situation\":\"How should we improve retention?\"}" --json
spark-intelligence agent inspect
```

The attachment snapshot is written to `SPARK_INTELLIGENCE_HOME/attachments.snapshot.json` and mirrored into SQLite runtime state so external Spark repos can consume the current attachment set without importing this repo's internals.

Chip manifests that expose the standard `spark-hook-io.v1` contract now also surface their hook commands directly into Spark Intelligence. That gives the builder one generic DOP-style path for future domains instead of one-off prompt wiring: a chip can declare `evaluate`, `suggest`, `packets`, and `watchtower` in `spark-chip.json`, Spark can run those hooks through `attachments run-hook`, and the Researcher bridge can inject active-chip `evaluate` output into live reply construction without giving chips ownership over identity, channels, or provider auth.

The Spark Researcher bridge now also includes a compact attachment context envelope derived from that snapshot, and active-chip `evaluate` output can now be folded into the advisory/fallback prompt path so live requests can be shaped by the currently active chip and specialization path without this repo importing chip internals.

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
Those same operator surfaces now also aggregate repeated Discord/WhatsApp webhook auth and verification rejections into direct trace commands, sustained repeats now escalate into higher-severity operator alerts instead of staying flat one-off warnings, and `operator snooze-webhook-alert <event>`, `operator webhook-alert-snoozes`, and `operator clear-webhook-alert-snooze <event>` now provide temporary suppression plus explicit visibility and reversal for a known noisy webhook-alert family. Active snoozes remain visible in `operator inbox`, `operator security`, and the dedicated snooze list with the original operator reason when one was supplied, the time the snooze was set, and the current suppressed recent rejection count plus latest reason and latest suppressed timestamp, so suppression never becomes fully hidden state. The inbox/security summary counts now also include `active_suppressed_webhook_snoozes` so masked pressure shows up immediately in the top-line totals, separate from the total snooze count. Quiet snoozes still point directly at a clear command, while snoozes that are still masking sustained recent traffic are promoted out of plain `info` status and point first at trace inspection before clearing, and the dedicated snooze list now sorts those sustained masked issues ahead of quieter snoozes even if they expire later. Clear actions now also retain the prior snooze metadata in operator history, and expired snoozes are pruned from local runtime state when those surfaces are read.
For `expiring_soon` OAuth states, the recommended first repair path is `jobs tick`, because OAuth maintenance is intentionally operator-driven and auditable in the current design.
Telegram pending pairings now also carry lightweight local context, so `operator review-pairings` can show the most recent Telegram username, chat id, and last inbound message preview.
`operator review-pairings` now also supports lightweight `--channel-id`, `--status`, and `--limit` filters so the queue stays usable once there are multiple channels or repeated onboarding attempts.
`operator pairing-summary telegram` provides a compact channel-level view of pending, held, approved, and revoked pairing state in one command.
`operator revoke-latest telegram` provides the same fast-path ergonomics as approve/hold for denying the newest pending or held Telegram request without touching approved pairings.
The fast-path pairing commands now also write exact `channel:user` targets into `operator history`, so local audit trails stay specific instead of logging only the channel name.
`operator history` now also supports lightweight `--action`, `--target-kind`, and `--contains` filters so local audit review stays usable as the event log grows.
After approval, the first successful Telegram reply also carries a one-time "pairing approved" welcome so the user gets a cleaner handoff into the active agent.
Held, revoked, paused, disabled, and generic blocked Telegram DMs now also return explicit user-facing replies instead of failing silently.
Telegram DMs now also hide `<think>...</think>` blocks by default, and the paired user can control that visibility per Telegram DM with `/think`, `/think on`, and `/think off`.
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

The current execution refocus is recorded in [docs/EXECUTION_REFOCUS_TELEGRAM_LLM_2026-03-26.md](./docs/EXECUTION_REFOCUS_TELEGRAM_LLM_2026-03-26.md).

The current system-connection and productization plan is recorded in [docs/SYSTEM_CONNECTION_AND_PRODUCTIZATION_PLAN_2026-03-26.md](./docs/SYSTEM_CONNECTION_AND_PRODUCTIZATION_PLAN_2026-03-26.md).

The latest cross-repo end-of-day state, including the browser-extension integration track, is recorded in [docs/STATUS_HANDOFF_2026-03-29.md](./docs/STATUS_HANDOFF_2026-03-29.md).

## Current Start

Current work should continue with gateway/runtime and operator-recovery locking, not more adapter breadth.

The exact first move is:

1. phase 1: lock the gateway runtime and operator recovery boundary
2. phase 2: prove one narrow live webhook/runtime path beyond Telegram
3. phase 3: harden the agent/provider execution contract before major breadth expansion

Do not start broad live Discord or WhatsApp runtime work before that.

The first phase-1 follow-up is already in: `status` and `gateway status` now surface explicit repair hints for degraded OAuth maintenance, provider runtime, and provider execution state so those summaries align with the operator recovery path instead of only reporting a degraded state.

The next phase-1 decision is also in: `doctor` stays diagnostic and fail-closed, but degraded doctor output now points at `status` and `operator security` instead of trying to become a second command-selection surface.

The next phase-1 operator-alignment patch is also in: configured Discord or WhatsApp channels with broken ingress contracts now surface as operator channel alerts with explicit secure repair commands, so operator surfaces match the degraded runtime surfaces instead of staying silent.

The next phase-1 runtime-summary patch is also in: paused or disabled channels now surface explicit repair hints in `gateway status` and top-level `status`, so those runtime summaries no longer leave channel-state blockage entirely implicit.

The next phase-1 start-path patch is also in: `gateway start` now echoes the same channel repair hint when Telegram is paused or disabled, so the foreground runtime path matches the already-improved runtime summaries.

Phase 2 prep is now in: [docs/DISCORD_OPERATOR_RUNBOOK_2026-03-26.md](./docs/DISCORD_OPERATOR_RUNBOOK_2026-03-26.md) defines the exact narrow live-validation target for Discord v1 signed interactions: DM-only `/spark message:<text>`, with setup, failure checks, and recovery commands.

The current refocus is: keep the canonical live home `.tmp-home-live-telegram-real` healthy on the proven Telegram plus MiniMax path first, then tighten Telegram recovery and rotation behavior, then decide whether to retry Codex auth, and only after that return to Discord live validation and later WhatsApp.

That Telegram plus LLM path is now proven end to end on the canonical live home:

- `auth status`: `custom` active via `env:CUSTOM_API_KEY`
- model: `MiniMax-M2.7`
- base URL: `https://api.minimax.io/v1`
- `gateway status`: ready
- `status`: doctor ok, gateway ready
- `gateway start --once`: real Telegram DM processed and real outbound reply sent successfully
