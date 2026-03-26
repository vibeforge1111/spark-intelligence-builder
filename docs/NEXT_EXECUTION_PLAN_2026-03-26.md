# Spark Intelligence Next Execution Plan 2026-03-26

## 1. Why This Plan Exists

The repo is no longer blocked on basic architecture.

It now has:

- a real Telegram/operator vertical slice
- shared provider/auth contracts
- OAuth plus API-key coexistence
- fail-closed gateway readiness checks
- narrow but secure Discord and WhatsApp ingress contracts

That changes the next job.

The next work should not be "add more surfaces quickly."

The next work should be "lock the runtime and operator model tightly enough that broader live adapter work and deeper agent/provider execution do not create security or recovery debt."

## 2. Current Recommendation

Do the next three phases in this order:

1. lock the gateway runtime and operator recovery boundary
2. validate and tighten one narrow live webhook/runtime path beyond Telegram
3. harden the agent/provider execution contract before adding major breadth

This is the safest path to stay aligned with the good parts of OpenClaw and Hermes-style auth/runtime design without copying their whole shape blindly.

## 3. Phase 1: Gateway Runtime Boundary Lock

Goal:

Make the gateway's long-running responsibilities, repair surfaces, and service boundaries explicit before more runtime breadth lands.

Implement:

- one written runtime contract for what `gateway start` is responsible for:
  - ingress ownership
  - readiness gating
  - trace and outbound audit ownership
  - retry and fail-closed behavior
  - maintenance expectations
- one written operator recovery contract for:
  - provider auth failure
  - webhook auth failure
  - bridge unavailable state
  - stale OAuth maintenance
- one scheduler decision:
  - keep `jobs tick` as the supported v1 path
  - or design a persistent scheduler only if it preserves the same auditability
- one review of `doctor`, `status`, and operator output for overlap and conflicting repair guidance

Do not do in this phase:

- finish Discord breadth
- finish WhatsApp breadth
- add more providers
- add a direct Codex/OAuth runtime just because it feels cleaner

Definition of done:

- the repo has a single written source of truth for gateway runtime ownership and operator repair flow
- the current manual OAuth maintenance decision is either reaffirmed cleanly or replaced deliberately
- the next runtime bugs can be judged against a documented contract instead of local convention

## 4. Phase 2: Narrow Live Runtime Validation

Goal:

Prove one webhook-driven adapter path with the same confidence level already reached for Telegram.

Recommended target:

- Discord first, because the ingress/auth boundary is already the most explicit after Telegram

Implement and validate:

- one real signed interaction test path against an actual Discord app if practical
- narrow DM-only command validation for `/spark message:<text>`
- operator and trace review of denied, malformed, and signature-failure paths
- runbook steps for onboarding, rotation, failure diagnosis, and recovery

If Discord live validation is blocked, do the equivalent narrow pass for WhatsApp instead.

Do not do in this phase:

- guild command breadth
- component or modal expansion
- broad WhatsApp message-type support
- best-effort webhook parsing

Definition of done:

- one non-Telegram live ingress path is proven end to end
- the operator can diagnose failure from `doctor`, `status`, `operator security`, and `gateway traces`
- the runbook for that path is good enough for repeatable revalidation

## 5. Phase 3: Agent and Provider Execution Hardening

Goal:

Move from "provider-aware bridge execution exists" to "agent execution contract is explicit, secure, and extensible."

Implement:

- one documented precedence model for provider selection:
  - explicit invocation override
  - agent or profile default
  - global default
- one documented contract for how OAuth/API-backed credentials are injected into execution paths
- one documented rule for when wrapper-backed execution is acceptable versus when a direct runtime is required
- usage, failure, and reconnect telemetry that stays operator-visible
- a direct-Codex decision:
  - either keep wrapper-backed as the supported v1 path
  - or design a direct runtime only if it matches the current auth and revocation guarantees

Do not do in this phase:

- a multi-provider marketplace
- broad agent-plugin architecture
- expanding adapters first and hoping the execution contract settles later

Definition of done:

- model/provider selection precedence is explicit
- execution paths use shared auth/runtime resolution instead of ad hoc provider assumptions
- wrapper-backed versus direct-runtime policy is a conscious product decision, not a hidden implementation accident

## 6. Phase Order Rationale

This order is deliberate.

If the repo expands adapters before the gateway/runtime boundary is locked, config and operator-path drift comes back.

If the repo expands provider execution before a live webhook path beyond Telegram is proven, the operator story gets more complex faster than the gateway is validated.

If the repo adds a direct OAuth runtime before the current wrapper-backed path is judged explicitly, it risks inventing a second auth/execution path without a better security story.

## 7. Immediate Start

Start phase 1 now with:

1. a gateway-runtime and operator-recovery review document that turns the current behavior into an explicit contract
2. a gap list for duplicated or conflicting `doctor`/`status`/`operator security` guidance
3. one narrow follow-up patch only if the review finds a real inconsistency
