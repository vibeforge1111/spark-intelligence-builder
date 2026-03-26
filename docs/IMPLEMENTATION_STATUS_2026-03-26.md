# Spark Intelligence Implementation Status 2026-03-26

## 1. Purpose

This note records what is already real in the repo, what shipped on 2026-03-26, and what the team should do next.

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

The practical result is that Telegram onboarding and moderation are now much easier to operate locally without adding any heavy dashboard or background subsystem.

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

The next slice should stay focused on gateway and provider-auth architecture, not more adapter breadth.

Start here in this exact order:

1. Lock the provider registry, auth-profile shape, OAuth callback-state shape, and gateway route-registry contract.
2. Add one shared runtime-provider resolver used by CLI, gateway, and future bridge execution paths.
3. Add secure OAuth support alongside static API-key support without weakening existing Telegram/operator safety.
4. Only after that, widen Discord, WhatsApp, or webhook-heavy surfaces.

The detailed execution direction is recorded in `GATEWAY_PROVIDER_AUTH_READINESS_REVIEW_2026-03-26.md`.

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

- one shared runtime-provider resolver exists and is used by every model-call path
- Spark supports both static API-key auth and at least one secure OAuth flow
- callback state is single-use, auditable, and fail-closed
- doctor and status can explain missing, expiring, failed, and healthy auth states
- channel config points at auth profiles instead of raw token assumptions
- expansion to another adapter happens only after those contracts are stable
