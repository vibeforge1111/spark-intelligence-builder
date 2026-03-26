# Gateway Runtime And Operator Recovery Review 2026-03-26

## 1. Purpose

This note starts phase 1 from `NEXT_EXECUTION_PLAN_2026-03-26.md`.

Its job is to make the current gateway/runtime and operator-recovery contract explicit enough that future changes can be judged against a written boundary instead of local habit.

## 2. What The Gateway Owns Right Now

`gateway start` is currently responsible for:

- refusing to start when configured channel/provider execution is already known-bad
- owning ingress for:
  - Telegram long-poll updates
  - gateway OAuth callback completion
  - Discord webhook handling
  - WhatsApp webhook handling
- writing shared gateway trace and outbound audit records
- applying shared outbound guardrails and duplicate/rate-limit handling
- exposing runtime summaries through `gateway status`, `status`, and `doctor`

This is already a real runtime boundary, not just a transport shim.

## 3. What The Gateway Does Not Yet Own

The gateway does not yet own:

- a persistent built-in scheduler for OAuth maintenance
- broad live Discord runtime behavior
- broad live WhatsApp runtime behavior
- multi-tenant remote callback/webhook service behavior
- a direct Codex/OAuth runtime path

Those remain intentionally outside the v1 contract.

## 4. Operator Recovery Surfaces That Already Exist

Current repair surfaces are spread across:

- `doctor`
- `status`
- `gateway status`
- `operator inbox`
- `operator security`
- `gateway traces`
- `gateway outbound`
- `auth status`
- `jobs list`
- `jobs tick`

That is acceptable only if each surface has a clear role.

## 5. Intended Role Of Each Surface

The clean contract should be:

- `doctor`
  - fail-closed readiness summary
  - answer: "is this safe to operate right now?"
- `status`
  - compact combined runtime snapshot
  - answer: "what mode is the system in right now?"
- `gateway status`
  - gateway-specific ingress/provider/runtime detail
  - answer: "what will `gateway start` or ingress do right now?"
- `operator inbox`
  - actionable mixed queue
  - answer: "what should the operator do next?"
- `operator security`
  - higher-signal risk and recovery view
  - answer: "what could cause unsafe or degraded operation?"
- `gateway traces`
  - rawish investigative evidence
  - answer: "what actually happened?"
- `gateway outbound`
  - outbound delivery evidence
  - answer: "what did Spark try to send?"
- `auth status`
  - provider/auth detail
  - answer: "which provider credentials are healthy, expiring, or broken?"
- `jobs list`
  - maintenance visibility
  - answer: "what recurring local jobs exist and when did they last run?"
- `jobs tick`
  - explicit operator-driven maintenance execution
  - answer: "run the current local maintenance contract now"

## 6. Current Contract That Should Be Preserved

The repo already has a few strong rules that should not be weakened:

- fail closed before polling or accepting ingress when readiness is already broken
- keep callback and webhook ingress narrow and explicit
- keep auth state single-use, auditable, and revocable
- keep operator repair commands concrete instead of hand-wavy
- keep quiet compatibility paths explicitly marked as compatibility paths
- keep adapter breadth behind the same guardrail and trace model

## 7. Current Gaps

These are the main remaining phase-1 gaps.

### Gap A: surface overlap is real

The same issue can appear across `doctor`, `status`, `gateway status`, `operator inbox`, and `operator security`.

That is not automatically wrong, but the wording and role discipline need to stay tight or the operator gets five similar surfaces with slightly different implications.

### Gap B: scheduler policy is implemented but not yet a durable contract

The repo currently supports operator-driven OAuth maintenance through `jobs tick`, with stale-maintenance detection.

That is good enough for v1, but it should be treated as an explicit product decision rather than a temporary implementation detail.

### Gap C: non-Telegram live validation is still weaker

Discord and WhatsApp have secure narrow ingress contracts, but they do not yet have the same level of live operational confidence that Telegram now has.

### Gap D: execution-path policy is still partly implicit

The provider transport split is explicit, but the broader rule for wrapper-backed versus direct-runtime execution should be written in one place and treated as a conscious policy.

## 8. Immediate Recommendations

Recommended next actions inside phase 1:

1. keep the current `jobs tick` maintenance model as the supported v1 scheduler contract
2. do one pass that removes or tightens any duplicated recovery wording across `doctor`, `status`, `gateway status`, and operator surfaces
3. only after that, do a live validation pass for one narrow Discord or WhatsApp path

The first pass on step 2 is now landed:

- `status` and `gateway status` now surface explicit repair hints when OAuth maintenance, provider runtime, or provider execution are degraded
- those hints now reuse the same first actions already implied by operator surfaces:
  - `spark-intelligence jobs tick`
  - `spark-intelligence auth refresh <provider>`
  - `spark-intelligence auth login <provider> --listen`
  - `spark-intelligence auth connect <provider> --api-key <key>`
  - `spark-intelligence researcher status`

The next surface-alignment decision is also now landed:

- `doctor` remains diagnostic and fail-closed
- degraded `doctor` output now points operators at:
  - `spark-intelligence status`
  - `spark-intelligence operator security`
- `doctor` does not try to become a second repair-command surface

## 9. Proposed Decision For Now

The repo should explicitly treat the following as the v1 contract:

- gateway owns ingress, readiness gating, and audit trails
- operator owns repair actions
- doctor owns diagnosis, not command selection
- maintenance is operator-driven, not daemon-driven
- Discord and WhatsApp remain narrow live surfaces until one of them is proven end to end
- Codex/OAuth remains wrapper-backed until a direct runtime can match current security and recovery guarantees

## 10. Definition Of Done For Phase 1

Phase 1 is done when all of this is true:

- one written gateway/runtime contract exists
- one written operator-recovery contract exists
- the scheduler model is an explicit v1 decision
- `doctor`, `status`, `gateway status`, and operator surfaces no longer imply conflicting next actions
- the repo is ready to prove one narrow non-Telegram live path without changing the boundary again
