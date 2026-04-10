# Phase 2 Implementation Plan — v2 Onboarding State Machine

**Status:** Locked 2026-04-10, immediately after operator answered Q-A through Q-H. Dependencies: Phase 1 complete (commits `6955ad5`, `e7e8afa`, `50ceafc`, `15ab9b1`) and operator decisions locked (`3ca8ee1`).

This plan translates the conceptual spec in `PERSONALITY_ONBOARDING_V2_DESIGN_2026-04-10.md` into commit-sized implementation steps. Each step is self-contained and testable.

## Starting point (facts from the codebase, 2026-04-10)

- **v1 onboarding state is a JSON blob** in `runtime_state` (keyed by `_agent_onboarding_state_key(human_id)`). No schema migration needed for state transitions — the JSON shape is free-form.
- **v1 state machine** in `personality/loader.py:maybe_handle_agent_persona_onboarding_turn` has only two steps: `awaiting_name` → `awaiting_persona` → `completed`. About 200 lines.
- **`humans.display_name`** is the platform-captured human label. There is no `user_address` column anywhere — that's a Phase 2 addition.
- **`agent_profiles.agent_name`** is already empty-string-sentinel under Phase 1.
- **`agent_rename_history`** can record the `/cancel` wipe with `new_name=""` (same way Finding G fix already exercises it).
- **No rate-limit gating** for onboarding currently — trait updates during v1 onboarding go through `save_agent_persona_profile` directly. Q-B's "no cap during onboarding" is therefore mostly a non-change; we just need to make sure that stays true as we add new paths.

## Commit-sized steps

| # | Step | Files touched | Scope |
|---|------|---------------|-------|
| P2-1 | Add `user_address TEXT` nullable column to `humans` | `state/db.py`, migration test | Schema only. Idempotent ALTER TABLE. |
| P2-2 | `cancel_agent_onboarding` service function (Q-E) | `identity/service.py`, unit test | Wipes name + onboarding state; keeps persona profile (Q-J default). Records rename history with `new_name=""`, `source_ref="onboarding-cancel"`. |
| P2-3 | Per-trait anchor labels (Q-F) | `personality/loader.py` | New `_GUIDED_TRAIT_ANCHORS` dict, 5 traits × 5 phrases each = 25 lines of static content. No behavior change. |
| P2-4 | Address-aware reply formatter (Q-D) | `personality/loader.py` | New `format_address_aware_line(template, user_address)` helper. Empty address → strip the salutation placeholder entirely. |
| P2-5 | State: `awaiting_user_address` | `personality/loader.py`, test | New state between `awaiting_name` and the persona mode choice. "How should your agent address you?" Accepts a name or "(blank)" / "skip". Stores on `humans.user_address` via new setter in `identity/service.py`. |
| P2-6 | State: `awaiting_persona_mode` | `personality/loader.py`, test | New state. "Guided (answer 5 quick questions), Express (pick a preset), or Freestyle (describe in your words)?" Branches to one of three sub-states. |
| P2-7 | Sub-state: `awaiting_persona_guided` | `personality/loader.py`, test | Iterates through 5 traits, each with anchor labels from P2-3. Collects 1-5 ratings, maps to trait deltas. No rate-limit cap (Q-B). |
| P2-8 | Sub-state: `awaiting_persona_express` | `personality/loader.py`, test | Shows preset catalog (same presets as `/style preset` currently). User picks by name or number. |
| P2-9 | Sub-state: `awaiting_persona_freestyle` (rename of v1 `awaiting_persona`) | `personality/loader.py`, test | Keep existing behavior. Rename the state string for consistency. |
| P2-10 | State: `awaiting_guardrails_ack` (Q-C, Q-G) | `personality/loader.py`, test | New state entered after any of the three persona sub-states. Shows the guardrails card once. Silence = acceptance — any next message (except an empty one) transitions to `completed`. Uniform across all 3 persona modes. |
| P2-11 | `/cancel` handling at every step (Q-E) | `personality/loader.py`, test | Detect `/cancel` on every incoming turn while onboarding is active. Call `cancel_agent_onboarding` (P2-2). Reply: "Cancelled. Your agent name was cleared. Say `/start` to set up again." |
| P2-12 | State: `awaiting_reonboard_consent` (Q-H) | `personality/loader.py`, test | Entered on first DM for users with a saved persona but `status != 'completed'`. One-tap offer. Only `yes`/`y`/`restart` starts v2 onboarding; any other reply transitions to `completed` silently. |
| P2-13 | Wire pairing and first-DM entry points | `adapters/telegram/runtime.py` | Update `_handle_post_pairing_welcome` and the main DM turn handler to call `maybe_handle_agent_persona_onboarding_turn(start_if_eligible=True)`. May also need a tiny entry-gate check for the Q-H reonboard path. |
| P2-14 | Docs and audit update | `docs/PERSONALITY_PHASE2_PLAN_2026-04-10.md` | Mark complete, link commits. Update audit doc with any Phase 2 findings. |

## Non-goals (stay out of Phase 2)

- Do NOT refactor `_build_style_training_message` or the existing `/style` commands.
- Do NOT touch the Spark Swarm bridge. Spark Swarm paths already short-circuit onboarding (`canonical_state.external_system == "spark_swarm"`).
- Do NOT add new traits beyond the existing 5.
- Do NOT implement Q-I / Q-J / Q-K — those are deferred follow-ups, not blockers.

## Test strategy

- Extend `tests/test_operator_pairing_flows.py` and `tests/test_agent_identity_contracts.py` incrementally as each step lands.
- Each state transition gets at least one happy-path and one rejection-path test.
- `/cancel` gets a test at 3 points: `awaiting_name`, mid-guided, `awaiting_guardrails_ack`.
- Q-H reonboard consent gets 2 tests: "yes" → enters onboarding, anything else → silently skips.
- Q-D empty-address formatting gets a small unit test on the helper alone.

## Rollback plan

If any step breaks the suite or introduces regressions, revert that step's commit with `git revert <hash>` (NOT `--amend`, per safety rules). The state machine is additive — reverting a later step should not require touching earlier ones.

## Execution order

Steps are listed in dependency order. P2-1 through P2-4 are independent preludes that unblock the state machine work (P2-5+). P2-13 is the final wire-up and should land last to avoid exposing a half-built state machine to live pairing.
