# Spark Intelligence Implementation Status 2026-03-26

## 1. Purpose

This note records what is already real in the repo, what shipped on 2026-03-26, what landed in the first gateway/provider-auth architecture pass, and what the team should do next.

## 2. Current Build State

The first serious Spark Intelligence vertical slice now exists.

The repo already has:

- a working package and CLI
- canonical config and SQLite state bootstrap
- identity, pairing, session, and operator control surfaces
- a real Telegram long-poll runtime
- a real Spark Researcher bridge
- a manual-first Spark Swarm bridge
- external attachment discovery and snapshot wiring for chips and specialization paths
- shared gateway guardrails for duplicate suppression, rate limiting, and outbound safety
- Discord and WhatsApp scaffolds that stay simulation-first for now

This means the repo is no longer in documentation-only mode.

It is in early implementation plus operator-hardening mode.

## 3. What Shipped Today

Today focused on Telegram operator flow, auditability, and local review speed.

Shipped today:

- `operator pairing-summary <channel>` for compact channel-level pairing state
- filtered `gateway traces` and `gateway outbound` views
- filtered `operator review-pairings` by channel, status, and limit
- `operator revoke-latest <channel>` for fast deny flow on pending or held requests
- exact `channel:user` targets in `operator history` for fast-path pairing actions
- filtered `operator history` by action, target kind, and substring match
- a repeatable `tests/` suite for CLI smoke, operator flows, observability, and Telegram failure paths
- fail-closed Telegram startup on auth and poll errors
- owner-only `.env` permission hardening with doctor visibility
- token rotation paths that preserve existing Telegram status, pairing mode, allowlists, and auth linkage by default
- safe separation between configured allowlists and operator-approved pairings so narrowing `allowed_users` actually removes stale config-driven access
- default auth-profile persistence for API-key-backed providers
- a canonical provider registry with typed API-key vs OAuth auth methods
- `auth providers` for local provider/auth capability discovery
- explicit provider execution-transport metadata in the provider registry and `auth providers`
- `auth status` for local provider secret-readiness inspection
- OAuth callback-state persistence and single-use consumption rules
- first OAuth-backed provider login flow via `auth login openai-codex`
- loopback OAuth callback capture via `auth login <provider> --listen`
- gateway-owned loopback OAuth callback completion via `gateway oauth-callback`
- local OAuth refresh via `auth refresh <provider>`
- local OAuth logout via `auth logout <provider>`
- expiry-aware `auth status`, `doctor`, and runtime-provider resolution for OAuth-backed providers
- `expiring_soon` auth-status visibility plus operator guidance before OAuth tokens fail closed
- built-in `jobs tick` OAuth maintenance that proactively refreshes due OAuth profiles
- explicit doctor coverage for stale OAuth maintenance so the current manual scheduler model is visible and auditable
- unified `status` and `gateway status` visibility for provider auth state, runtime-provider readiness, execution transport, and OAuth-maintenance health
- explicit `provider-runtime` doctor coverage so the selected default provider fails readiness when its secret, expiry state, or default-selection config is not actually resolvable
- explicit `provider-execution` doctor coverage so wrapper-backed Codex auth fails readiness when the researcher bridge is disabled or unavailable
- fail-closed `gateway start` behavior when configured runtime-provider or provider-execution readiness is not actually usable
- stricter gateway OAuth callback capture so malformed callback requests are rejected at the HTTP edge and provider-denied callbacks become explicit auth failures with single-use state consumption
- stricter route-registry ingress contracts so OAuth callback routes stay GET-only and future adapter webhooks must declare POST-only request content types
- first Discord webhook skeleton on `/webhooks/discord` with route validation, Discord signature verification via interaction public key, legacy static-secret message ingress reduced to explicit compatibility mode, operator-visible readiness for signed-vs-legacy Discord ingress, signed `PING` handling, an explicit DM-only chat-input `/spark message:<text>` `APPLICATION_COMMAND` contract routed through the existing DM bridge with guarded callback replies, and trace-visible plus operator-visible auth rejection reasons
- operator-visible WhatsApp ingress readiness so missing verify-token/app-secret configuration degrades `doctor` and `gateway status` instead of looking silently configured
- first WhatsApp webhook skeleton on `/webhooks/whatsapp` with route validation, Meta-style GET verification, `X-Hub-Signature-256` POST auth, fail-closed JSON parsing, one explicit Meta text-message event contract routed through the existing simulated WhatsApp bridge instead of opportunistic stub or batch normalization, and trace-visible plus operator-visible ignore/auth/verification rejection reasons
- operator-visible reconnect and revoke guidance in `operator inbox` and `operator security` for expired, revoked, and refresh-error provider auth states
- sustained Discord/WhatsApp webhook auth or verification rejections now escalate to higher-severity operator alerts instead of staying flat warnings
- operators can now explicitly snooze, inspect, and clear one webhook-alert family through `operator snooze-webhook-alert <event>`, `operator webhook-alert-snoozes`, and `operator clear-webhook-alert-snooze <event>`, while active snoozes remain visible in `operator inbox`, `operator security`, and the dedicated snooze list with their operator reason, snoozed time, suppressed recent rejection count, latest reason, and latest suppressed timestamp when present; inbox/security counts now also expose `active_suppressed_webhook_snoozes` separately from the total snooze count, quiet snoozes still point directly at clearing, snoozes that are still masking sustained recent traffic are promoted and point first at trace inspection, the dedicated snooze list now sorts those sustained masked issues ahead of quieter snoozes even if they expire later, clear actions retain the prior snooze metadata in operator history, and expired snoozes self-prune from local runtime state
- provider-aware Spark Researcher bridge routing instead of hardcoded `generic` advisory model selection
- direct provider-backed LLM execution for API-key-backed bridge traffic via provider-aware HTTP wrapper commands
- explicit runtime transport selection so API-key-backed providers use `direct_http` while Codex/OAuth stays `external_cli_wrapper`
- a gateway route-registry contract that now also owns OAuth callback serving
- a direct-provider conversational fallback so under-supported Telegram small-talk does not dead-end at the generic "no concrete guidance" placeholder when a real provider is configured
- Telegram think-block visibility now defaults to hidden, with per-DM runtime controls available through `/think`, `/think on`, and `/think off`

The practical result is that Telegram onboarding and moderation are much easier to operate locally, and the repo now has the first real foundations for secure provider auth growth without inventing ad hoc OAuth glue later.

## 4. What Is Stable Enough

These areas are now stable enough to keep and build on:

- federated repo boundaries
- Telegram-first runtime shape
- operator-owned pairing and control-plane decisions
- local auditability
- bridge-driven Spark integration instead of copied internals

## 5. Main Remaining Risk

The biggest risk is no longer missing architecture or a total lack of tests.

The biggest risk is config and operator-path drift as the repo expands into more adapters and more recovery flows.

Right now we have:

- a real `tests/` directory
- repeatable regression coverage for Telegram pairing/operator flows
- CLI smoke coverage for the current vertical slice
- failure-path coverage for Telegram auth, polling, duplicates, and rate limits
- live Telegram validation against a real BotFather bot

The remaining risk is that more adapters or more config mutation paths could reintroduce silent authorization drift if they are not held to the same standard.

## 6. Next Start Exactly

The next slice should stay focused on runtime and operator integrity, not more breadth.

Start here in this exact order:

1. Phase 1: lock the gateway runtime and operator recovery boundary.
2. Phase 2: prove one narrow live webhook/runtime path beyond Telegram.
3. Phase 3: harden the agent/provider execution contract before major breadth expansion.

The concrete phase plan is recorded in `NEXT_EXECUTION_PLAN_2026-03-26.md`.

Phase 1 has now started with `GATEWAY_RUNTIME_OPERATOR_RECOVERY_REVIEW_2026-03-26.md`.

The first phase-1 repair-guidance patch is also in: `status` and `gateway status` now surface explicit repair hints for degraded OAuth maintenance, provider runtime, and provider execution state so those surfaces no longer rely on operator views alone to reveal the first fix command.

The next phase-1 role split is also now explicit: `doctor` remains a diagnostic and fail-closed readiness surface, but degraded doctor output now points operators at `status` and `operator security` for repair guidance instead of trying to duplicate command selection itself.

The next phase-1 operator-alignment patch is also in: configured Discord or WhatsApp channels with broken ingress contracts now surface as operator channel alerts with explicit secure repair commands, so operator surfaces no longer stay quiet while `doctor` and `gateway status` are already degraded.

The next phase-1 runtime-summary patch is also in: paused or disabled channels now surface explicit repair hints in `gateway status` and top-level `status`, so those runtime summaries no longer leave channel-state blockage entirely implicit.

The next phase-1 start-path patch is also in: `gateway start` now echoes the same channel repair hint when Telegram is paused or disabled, so the foreground runtime path matches the already-improved runtime summaries.

Phase 2 prep has now started with `DISCORD_OPERATOR_RUNBOOK_2026-03-26.md`, which locks the narrow live-validation target for Discord v1 to signed DM slash-command ingress only: `/spark message:<text>`.

Execution is now explicitly refocused through `EXECUTION_REFOCUS_TELEGRAM_LLM_2026-03-26.md`: before live Discord or broader WhatsApp work, Spark should first prove and stabilize the real Telegram plus LLM path end to end on the live home.

That proof is now in place on the canonical home `.tmp-home-live-telegram-real`:

- Telegram auth is healthy against the live BotFather bot `@SparkAGI_bot`
- the live home is narrowed to one working provider path, `custom`, backed by MiniMax
- `auth status`, `gateway status`, and top-level `status` are now all consistent on that same home
- `gateway start --once` now exits cleanly on that same home with provider runtime and execution both healthy
- one real inbound Telegram DM has been processed and one real outbound provider-backed reply has been sent successfully on that same home

Codex OAuth is still not proven on the live home and is no longer the gating item for the Telegram vertical slice. The current remaining work is operational hardening around the already-proven Telegram plus MiniMax path, followed by a deliberate decision on whether to retry Codex auth or defer it behind the stable API-key-backed path.

The next broader connection and productization map is now recorded in `SYSTEM_CONNECTION_AND_PRODUCTIZATION_PLAN_2026-03-26.md`. That plan separates what is truly connected today from what is only wired, and defines the next step-by-step order for activating chips/path specialization, connecting real Swarm API access, and productizing the install/setup/run story for another operator.

## 7. Current Non-Goals

Do not start these next:

- full live Discord runtime breadth
- full live WhatsApp runtime breadth
- webhook-first Telegram
- a provider plugin marketplace
- new memory logic in this repo
- copied Spark Researcher or Spark Swarm internals

## 8. Next Definition Of Done

The next slice is successful if all of this is true:

- the canonical Telegram live home stays on one working provider path and remains green after token, provider, or pairing changes
- one controlled provider-failure drill is run on that same home and all recovery surfaces point at the correct repair flow
- one real Telegram reply is re-proven after the recovery drill
- Codex auth is either deliberately retried from a stable baseline or explicitly deferred behind the proven MiniMax path
- expansion to another adapter happens only after those contracts are stable
