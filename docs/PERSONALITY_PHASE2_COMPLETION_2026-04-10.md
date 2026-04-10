# Phase 2 Completion Report — v2 Onboarding State Machine

**Status:** All 14 steps complete. Full `tests/test_operator_pairing_flows.py` suite green at 129/129 as of commit `62e15fe`.

**Scope:** This doc is the P2-14 deliverable of the Phase 2 plan. It mirrors the P2-14 row of
`docs/PERSONALITY_PHASE2_PLAN_2026-04-10.md` (on release branch `release/personality-decision-provenance-2026-04-10` pending PR #12 merge) and links every landed step to the commit that implemented it on `main`.

## Commit-by-commit status

| # | Plan row | Commit (on `main`) | Files touched | Test additions |
|---|----------|--------------------|---------------|----------------|
| P2-1 | Add `user_address TEXT` nullable column to `humans` | `c28feb4` | `state/db.py`, migration test | Schema migration test |
| P2-2 | `cancel_agent_onboarding` service function (Q-E) | `f490b47` | `identity/service.py`, unit test | Name-wipe round-trip |
| P2-3 | Per-trait anchor labels (Q-F) | `38dd034` | `personality/loader.py` | Static content; no new test |
| P2-4 | Address-aware reply formatter (Q-D) | `1500c9a` | `personality/loader.py` | Empty-address unit test + cancel deflake |
| P2-5 | State `awaiting_user_address` | `abfb0ba` | `personality/loader.py`, `identity/service.py`, tests | Address save/skip path |
| P2-6 | State `awaiting_persona_mode` | `bb9017b` | `personality/loader.py`, tests | Mode picker branches |
| P2-7 | Sub-state `awaiting_persona_guided` | `b5153cd` | `personality/loader.py`, tests | 5-trait guided walkthrough |
| P2-8 | Sub-state `awaiting_persona_express` | `08302be` | `personality/loader.py`, tests | Preset pick + unknown-preset recovery |
| P2-9 | Rename `awaiting_persona` → `awaiting_persona_freestyle` | `579405c` | `personality/loader.py`, tests | Existing freestyle flow under new step name |
| P2-10 | State `awaiting_guardrails_ack` (Q-C, Q-G) | `3b966fb` | `personality/loader.py`, tests | `change` soft-reprompt + `ok` acceptance; all 3 persona modes updated |
| P2-11 | `/cancel` handling at every step (Q-E) | `675994b` | `personality/loader.py`, tests | Mid-flow `/cancel` wipes name and state |
| P2-12 | State `awaiting_reonboard_consent` (Q-H) | `8fb1b08` | `personality/loader.py`, tests | Offer/`yes`/skip unit tests |
| P2-13 | Wire pairing and first-DM entry points | `62e15fe` | `adapters/telegram/runtime.py`, `personality/loader.py`, `personality/__init__.py`, tests | Runtime-level offer + yes + skip integration; `test_normal_chat_reply_uses_saved_agent_identity_surface_style` updated to pre-mark completion blob |
| P2-14 | This doc | (this commit) | `docs/PERSONALITY_PHASE2_COMPLETION_2026-04-10.md` | — |

## Final state machine

```
awaiting_name
  -> awaiting_user_address
  -> awaiting_persona_mode
       -> "guided"    -> awaiting_persona_guided     (5 trait questions, P2-7)
       -> "express"   -> awaiting_persona_express    (preset catalog, P2-8)
       -> "freestyle" -> awaiting_persona_freestyle  (v1 behavior, renamed P2-9)
  -> awaiting_guardrails_ack                         (P2-10)
  -> completed
```

Plus the two cross-cutting gates added in P2-11 and P2-12:

- **`/cancel`** (P2-11): honored at every step. Wipes both the in-progress onboarding state blob
  AND the saved agent name back to the empty-string sentinel, records a rename-history row with
  `source_surface="onboarding_cancel"`, and returns a confirmation reply. Persona profile is left
  untouched per Q-J default.
- **`awaiting_reonboard_consent`** (P2-12 / P2-13): entered on first DM for existing users with a
  saved persona profile who have no in-progress onboarding state blob. One-tap offer: reply `yes`
  (or `y`/`yeah`/`yep`/`yup`/`sure`/`restart`/`redo`/`re-run`/`rerun`) to re-run the short v2
  conversation; anything else marks the state `completed` and passes the message through to the
  researcher bridge. Never re-fires once the state is `completed`.

## Operator decisions honored

All eight locked decisions from §11 of `PERSONALITY_ONBOARDING_V2_DESIGN_2026-04-10.md` are implemented:

| Decision | Mapped to | Implementation notes |
|----------|-----------|----------------------|
| Q-A: Single unified path (not per-surface) | P2-5..P2-13 | One state machine in `personality/loader.py`, shared by all Telegram entry points |
| Q-B: No rate-limit cap during onboarding | P2-7..P2-9 | Guided/express/freestyle writes call `save_agent_persona_profile` directly, no cap check |
| Q-C: Show guardrails but don't gate on ack | P2-10 | Any non-`change` reply transitions to `completed`; `change` soft-reprompts without advancing |
| Q-D: Empty user_address strips the salutation entirely | P2-4 | `format_address_aware_line` collapses `{salutation}` and `{salutation_suffix}` to `""` when address is empty, and capitalizes the first alphabetic character if the template started with `{salutation}` |
| Q-E: `/cancel` wipes agent name back to empty-string sentinel | P2-2, P2-11 | `cancel_agent_onboarding` service + `_ONBOARDING_CANCEL_TOKENS` guard in the loader |
| Q-F: Per-trait anchor labels in guided mode | P2-3, P2-7 | `_GUIDED_TRAIT_ANCHORS` dict, 5 traits × 5 phrases |
| Q-G: Show guardrails uniformly across all 3 persona modes | P2-10 | All three persona completion blocks transition to `awaiting_guardrails_ack` before `completed` |
| Q-H: Run once for everyone, one-tap skip | P2-12, P2-13 | `awaiting_reonboard_consent` + `agent_has_reonboard_candidate` entry gate |

Deferred follow-ups Q-I / Q-J / Q-K are **not** implemented (per the plan's non-goals).

## Findings and deviations

Phase 2 landed cleanly with no findings that required re-scoping. A handful of implementation
details worth recording for future audits:

1. **loader.py line endings.** `src/spark_intelligence/personality/loader.py` uses all-CRLF
   line endings (3541 CRLF lines as of P2-13). Standard string-editing tools normalize line
   endings and cause massive diff bloat. P2-7 through P2-13 used byte-level Python patch scripts
   that preserve the file's convention. The test file is pure LF and was edited with the
   standard Edit tool.

2. **Concurrent agent commits on `main`.** Several non-personality agents were committing to
   `main` in parallel throughout Phase 2. Every P2-* commit explicitly staged files by path
   (never `git add -A`) to avoid pulling in unrelated in-progress work. No conflicts occurred
   because Phase 2 only touched `personality/`, `adapters/telegram/runtime.py`, and
   `tests/test_operator_pairing_flows.py`.

3. **Runtime wiring regression.** P2-13's runtime wire-up caught exactly one pre-existing
   regression in `test_normal_chat_reply_uses_saved_agent_identity_surface_style`: the test
   previously relied on existing users with a saved persona NEVER entering the onboarding
   state machine, which is no longer true post-P2-12/P2-13 by design. The fix (pre-inserting
   a `completed` onboarding state blob) is documented inline in that test.

4. **Awaiting_reonboard_consent gate for already-completed users.** The
   `agent_has_reonboard_candidate` helper only returns True when there is **no** onboarding
   state blob at all, so users who already completed v1 (blob `status="completed"`) never see
   the P2-12 offer. This matches the plan's wording "Run once for everyone" — users who already
   ran v1 count as "already run once". Users who predate the blob (or whose blob was cleared)
   DO see the offer once.

5. **`/cancel` token set.** P2-11 intentionally accepts `/cancel`, `/quit`, and `/stop` but
   nothing else, to avoid accidentally clobbering agent names when users type something like
   "cancel the reservation". The plan only mentioned `/cancel` but the broader set is a strict
   superset and none of the extras conflict with any other onboarding token set.

## Non-goals upheld

The plan's non-goals were all respected:

- No refactor of `_build_style_training_message` or existing `/style` commands.
- Spark Swarm paths still short-circuit onboarding via the
  `canonical_state.external_system == "spark_swarm"` guard at the top of
  `maybe_handle_agent_persona_onboarding_turn`.
- No new traits beyond the existing 5 (warmth, directness, playfulness, pacing, assertiveness).
- Q-I / Q-J / Q-K remain deferred.

## Test coverage summary

`tests/test_operator_pairing_flows.py` grew from the pre-Phase-2 baseline to 129 tests, all
green on `62e15fe`. Key additions per step:

- **P2-5..P2-9**: happy-path + rejection-path per state transition.
- **P2-10**: `change` soft-reprompt branch and `ok` acceptance branch.
- **P2-11**: `/cancel` wipes state and agent name at `awaiting_persona_mode` (mid-flow), with
  assertions that the state blob is deleted, the canonical agent name is `""`, and an
  `onboarding_cancel` rename-history row exists.
- **P2-12**: unit tests that call `maybe_handle_agent_persona_onboarding_turn` directly to
  exercise the yes/skip branches of `awaiting_reonboard_consent` without runtime wiring.
- **P2-13**: runtime-level integration tests that drive the reonboard flow end-to-end via
  `simulate_telegram_update` after `consume_pairing_welcome`, including a follow-up DM
  assertion that the offer never re-fires once the state is `completed`.

No tests were disabled or skipped. No flakes observed across the ~20 full-suite runs during
Phase 2.

## Follow-ups (out of scope for Phase 2)

The plan explicitly defers these to future phases:

1. **Q-I**: surfacing persona provenance to users (e.g. "set via guided mode on 2026-04-11").
2. **Q-J non-default**: letting `/cancel` also wipe the persona profile.
3. **Q-K**: exposing the v2 state machine to surfaces other than Telegram.

The release-branch plan doc (`docs/PERSONALITY_PHASE2_PLAN_2026-04-10.md` on
`release/personality-decision-provenance-2026-04-10`, PR #12) should be updated to mark
P2-7..P2-14 complete with the SHAs listed in the table above as part of that PR's merge flow.
This completion report will live on `main` regardless of when PR #12 merges.
