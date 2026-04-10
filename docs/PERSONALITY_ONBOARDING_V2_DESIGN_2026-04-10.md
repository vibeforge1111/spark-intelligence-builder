# Personality Onboarding v2 — Design

Author: Claude (Opus 4.6), 2026-04-10
Status: **DRAFT — awaiting operator review.** No code has been written for this doc yet.
Companions:
- `docs/PERSONALITY_TESTING_METHODOLOGY.md` (§5 default chip, §A.11 values questions)
- `docs/PERSONALITY_A11_PROPOSALS_2026-04-10.md` (Q1/Q2/Q3 locked, §G guardrails)
- `docs/PERSONALITY_GAPS_14_15_DESIGN_2026-04-10.md` (trait floors + rate limits)
- `PERSONALITY_HANDOFF_2026-03-26.md` (earlier onboarding v1 design)

---

## 0. Why v2

### What exists today (v1)

`maybe_handle_agent_persona_onboarding_turn` at `src/spark_intelligence/personality/loader.py:1378` implements a **two-step** onboarding:

1. `awaiting_name` — asks "What should I call your agent? Right now it's `{current_name}`." Accepts a name or `skip`.
2. `awaiting_persona` — asks for a comma-separated descriptor string (e.g. `calm, strategic, very direct, low-fluff`) and runs `_extract_trait_deltas` + `_extract_onboarding_descriptor_deltas` to shift traits off the 0.50 baseline.

### Problems with v1

| # | Problem | Evidence |
|---|---------|----------|
| 1 | The current-name fallback leaks `"Spark Agent"` because `CanonicalAgentState.agent_name` is never `None` | `identity/service.py` 5 fallback sites (lines 379, 531, 557, 612, 860) |
| 2 | The operator is explicitly asking for **no default agent name** — the user must name the agent | Operator message: *"agents should have a name always that the user defines"* |
| 3 | `skip` at `awaiting_name` (line 1430) keeps the default name, which violates problem #2 | Code |
| 4 | The persona step is a free-text descriptor — there's no sub-step for *how the agent should address the user* | v1 has no `user_address` concept at all |
| 5 | Personality capture is one-shot. The user has no structured way to choose traits they care about (warmth vs directness vs playfulness vs pacing vs assertiveness); it's all buried in descriptor extraction | Code |
| 6 | There's no "feedback anytime" moment — the closing line mentions `be more direct` but doesn't frame the whole NL preference loop as an ongoing contract | Line 1554-1556 |
| 7 | Onboarding does not surface the new `§G` guardrails (anti-glazing, better-way surfacing, honest failure) so the user never learns what Spark's commitments are | — |
| 8 | The OpenClaw 3-question reference pattern (name / personality / how-to-address-user) is not honored | — |

### v2 scope

Introduce a **structured, required-name, multi-step** onboarding that:
- Requires an agent name (no skip, no default)
- Captures personality in a guided way (trait-by-trait with optional express mode)
- Captures how the user wants to be addressed
- Explains the feedback loop ("say `be more direct` anytime") as a first-class contract
- Optionally surfaces the §G guardrails so the user knows what Spark will and won't do

---

## 1. High-level shape

```
 ┌──────────────────────────────────────────┐
 │  0. kickoff  (eligibility + welcome)     │
 └─────────────────┬────────────────────────┘
                   ▼
 ┌──────────────────────────────────────────┐
 │  1. awaiting_name      (required)        │
 │      "What should I call your agent?"    │
 └─────────────────┬────────────────────────┘
                   ▼
 ┌──────────────────────────────────────────┐
 │  2. awaiting_user_address   (required)   │
 │      "And what should I call you?"       │
 └─────────────────┬────────────────────────┘
                   ▼
 ┌──────────────────────────────────────────┐
 │  3. awaiting_persona_mode   (choice)     │
 │      express | guided | skip             │
 └─────────┬────────────┬────────────┬──────┘
           │            │            │
           ▼            ▼            ▼
  ┌─────────────┐  ┌────────────┐  ┌─────────────────┐
  │ 3a express  │  │ 3b guided  │  │ 3c skip/default │
  │ single free-│  │ 5 trait    │  │ flat 0.50 base  │
  │ text line   │  │ sub-steps  │  │                 │
  └──────┬──────┘  └──────┬─────┘  └────────┬────────┘
         └────────────────┼─────────────────┘
                          ▼
 ┌──────────────────────────────────────────┐
 │  4. awaiting_guardrails_ack  (optional)  │
 │      Show §G1/G2/G3 commitments          │
 │      Default: auto-accept if silent      │
 └─────────────────┬────────────────────────┘
                   ▼
 ┌──────────────────────────────────────────┐
 │  5. completed                            │
 │      Recap + feedback-loop contract      │
 └──────────────────────────────────────────┘
```

All required steps (1, 2) must be answered. Optional steps (3, 4) have a "skip" path, but step 1 has **no skip**.

---

## 2. Step-by-step spec

### Step 0 — kickoff

**Trigger:** same as v1 (`start_if_eligible=True`, no existing persona, `preferred_source == "builder_local"`, not bridged to Spark Swarm).

**Reply template:**
```
Let's set up your agent. This is a short conversation — name, how you want me to call you, and the personality you want.

You can say `/cancel` at any time to stop onboarding.
```
Then fall through into Step 1 in the *same* message (no extra round trip for the greeting).

### Step 1 — awaiting_name (REQUIRED)

**Prompt:**
```
What should I call your agent?

Reply with a name like `Atlas`, `Nova`, or `Lyra`.
```

Changes from v1:
- Drop the line `"Right now it's \`{canonical_state.agent_name}\`"` — there is no default to anchor on
- Drop the `skip` lowered-keyword branch (lines 1430-1446)
- Drop the `Operator Zero` example (privacy)

**Validation:**
- Empty / whitespace → re-prompt with "I need a name to continue. Try something like `Atlas` or `Nova`."
- Length > 48 chars → truncate and confirm: "That's long — I'll use `{truncated}`. OK?" (new `awaiting_name_confirm` sub-state? *Open question Q-A.*) — or simpler: reject and re-prompt.
- Reserved names (future-proofing): `/cancel`, `skip`, `default`, `spark agent`, empty strings → re-prompt.

**Persist:** `rename_agent_identity` as today.

### Step 2 — awaiting_user_address (REQUIRED, NEW)

**Prompt:**
```
Got it — `{agent_name}` it is.

And what should `{agent_name}` call you? (Your first name, a nickname, a callsign, whatever fits.)
```

**New schema field:** `agent_profiles.user_address TEXT` (nullable; populated on completion). *See §3 for the schema migration.*

**Validation:**
- Empty / whitespace → re-prompt
- Reserved: `/cancel`, empty
- Length cap: 48 chars

**Persist:** write directly to `agent_profiles.user_address` via a new helper `save_agent_user_address`.

### Step 3 — awaiting_persona_mode (choice)

**Prompt:**
```
Now the personality. Three ways to go:

- `express` — one line, e.g. `calm, strategic, very direct, low-fluff`
- `guided`  — I'll ask you about 5 traits one at a time (takes ~30 seconds)
- `skip`    — keep the balanced default, shape it later with `be more direct`, `be warmer`, etc.

Which one?
```

#### 3a — express

Same as v1 `awaiting_persona` descriptor extraction. Reuses `_extract_trait_deltas` and `_extract_onboarding_descriptor_deltas`. This is the existing code path, minimally touched.

#### 3b — guided (NEW)

Five sub-steps, one per trait, in this order:
1. `awaiting_trait_warmth`
2. `awaiting_trait_directness`
3. `awaiting_trait_playfulness`
4. `awaiting_trait_pacing`
5. `awaiting_trait_assertiveness`

Each sub-step prompts with the trait's human name, a 1-line plain-English description, and a 1-5 scale:

```
Warmth — how personable vs businesslike should `{agent_name}` be?

1 — strictly businesslike
2 — professional, light warmth
3 — balanced  (default)
4 — warm and friendly
5 — very warm

Reply with 1-5 (or `skip` to keep 3).
```

**Mapping 1-5 → trait value:**
| Input | Trait value | Delta from 0.50 |
|-------|-------------|-----------------|
| 1 | 0.15 | -0.35 |
| 2 | 0.35 | -0.15 |
| 3 | 0.50 | 0.00 |
| 4 | 0.65 | +0.15 |
| 5 | 0.85 | +0.35 |

**Note:** These deltas must respect the **A.11 Q3 protected-trait floors** (see `PERSONALITY_GAPS_14_15_DESIGN_2026-04-10.md`):
- `directness` and `assertiveness` have a floor of 0.35. A user pick of `1` would want 0.15 for assertiveness but the floor would clamp it to 0.35 and surface a `personality_floor_hit` event explaining why.
- `warmth` is rate-limited (±0.25 per turn). Since 0.50±0.35 exceeds ±0.25, a user pick of `1` or `5` for warmth during onboarding would clamp to 0.25 or 0.75 respectively. *This is actually a design question — onboarding is a one-time event, should the rate limit apply?* **Q-B: does the rate limit apply during onboarding, or only after?** Recommendation: **only after onboarding completes** — the initial persona capture shouldn't be artificially narrowed. Otherwise a user asking for "very warm" gets only halfway.

**Proposed resolution for Q-B (for operator review):** Protected-trait *floors* (Q3) apply always. *Rate limits* don't apply during onboarding — they kick in post-completion. The floor still prevents the user from asking for `1` on directness/assertiveness; it clamps to 0.35 with a notification.

#### 3c — skip

Persist an empty persona profile (all traits at 0.50). Same as v1 `skip`.

### Step 4 — awaiting_guardrails_ack (OPTIONAL)

**Prompt:**
```
One more thing. Here's what `{agent_name}` commits to:

  1. No glazing. `{agent_name}` won't say "great idea!" unless it's actually a great idea — and will say so when it isn't.
  2. Better-way surfacing. If there's a clearly better approach, `{agent_name}` will say so, not just go along.
  3. Honest failure reporting. If something isn't working, `{agent_name}` will tell you, not pretend.

Reply `ok` to accept, or `change` to adjust. You can always say `be gentler` or `be more direct` later.
```

**Behavior:**
- `ok` / `yes` / `sounds good` / empty → proceed to completion
- `change` / `adjust` → **this is out of scope for v2 step 4**; route to a follow-up prompt "What should I change?" which falls into the existing NL preference path. Explicitly does NOT let the user turn off G1/G2/G3 — they're locked by §A.11. The follow-up can only adjust *how firmly* the guardrails are phrased (e.g. "be nicer when pointing out problems" — that's a delivery style tweak, not a guardrail bypass).

**Q-C: should step 4 be mandatory?** Recommendation: **optional by default, skippable in silence**. Showing it is good UX but gating onboarding on it feels preachy. If silence counts as acknowledgment, the guardrails are still active — the user just didn't read them.

### Step 5 — completed

**Reply template:**
```
Locked in. Here's the recap:

  Agent:        {agent_name}
  Calls you:    {user_address}
  Personality:  {persona_summary or "balanced"}
  Commitments:  anti-glazing, better-way surfacing, honest failure reporting

You can shape `{agent_name}`'s personality any time:
  - `be more direct`, `be warmer`, `slow down`, `be playful`
  - `what's my personality` to see current traits
  - `reset personality` to go back to balanced

Ready when you are.
```

---

## 3. Schema migration

New column:
```sql
ALTER TABLE agent_profiles
  ADD COLUMN user_address TEXT;  -- how the agent should address the user
```

Also — **this design depends on** the Phase 1 change: `agent_profiles.agent_name` becoming nullable (or verified-nullable if it already is). Phase 1 Step 1 (Task #5) will audit this.

**Default-value behavior for existing rows:** `user_address` starts as `NULL` for all existing users. A read path would need to fall back to something — **Q-D: what's the fallback when `user_address` is NULL?**

Options:
- (a) Fall back to `human_display_name` from the human profile if present
- (b) Generic address like `"friend"` — **rejected, too cute**
- (c) Fall back to nothing (agent just doesn't use a name tag for the user)
- (d) Use the telegram `first_name` field from the paired chat

Recommendation: **(c) default, with (a) as a preference if human_display_name exists.** Never use a generic fallback.

---

## 4. Onboarding-state payload changes

The existing `_agent_onboarding_state` payload (serialized to `runtime_state`) needs:

**New fields:**
- `user_address` (str, set after step 2)
- `persona_mode` (`"express" | "guided" | "skip"`, set after step 3 choice)
- `guided_trait_step` (one of `warmth/directness/playfulness/pacing/assertiveness/null`, only used in guided mode)
- `guided_trait_values` (dict[str, float], accumulated as user answers each sub-step)
- `guardrails_ack` (`"pending" | "accepted" | "adjusted"`)

**New step values:**
- `awaiting_user_address`
- `awaiting_persona_mode`
- `awaiting_trait_warmth`, `awaiting_trait_directness`, `awaiting_trait_playfulness`, `awaiting_trait_pacing`, `awaiting_trait_assertiveness`
- `awaiting_guardrails_ack`

**Removed behavior:**
- `skip` keyword at `awaiting_name` step

---

## 5. Touchpoints in code (no edits in this doc, just mapping)

Files that would need changes when implementation begins (deferred to later tasks):

| File | Change | Risk |
|------|--------|------|
| `src/spark_intelligence/personality/loader.py:1378-1560` | Rewrite `maybe_handle_agent_persona_onboarding_turn` as a state machine dispatcher | High — this is the onboarding engine, touched by many tests |
| `src/spark_intelligence/personality/loader.py:1456` | Drop "Operator Zero" example | Trivial |
| `src/spark_intelligence/personality/loader.py:1430` | Drop skip-keyword branch at awaiting_name | Trivial but touches tests |
| `src/spark_intelligence/identity/service.py` | (Phase 1) Remove 5 `"Spark Agent"` fallbacks; make `agent_name` nullable | Medium — 30 call sites |
| `src/spark_intelligence/identity/service.py` new helper | `save_agent_user_address(human_id, address)` | Low |
| `src/spark_intelligence/personality/loader.py` new helpers | `_format_trait_prompt`, `_parse_trait_scale`, `_format_guardrail_ack` | Low |
| Migration script | Add `user_address` column to `agent_profiles` | Low (ALTER TABLE is idempotent with `PRAGMA table_info` check) |
| Tests | `tests/test_agent_persona_onboarding.py` (if it exists; otherwise create) | Medium |

**Intentionally NOT changed by Phase 2:**
- `build_telegram_surface_identity_preamble` — Phase 1 Step 4 handles the no-name branch
- `detect_and_persist_nl_preferences` — unchanged; this is the post-onboarding NL loop
- `load_personality_profile` — unchanged

---

## 6. Open questions for the operator

| ID | Question | Recommendation |
|----|----------|----------------|
| Q-A | Long names (>48 chars) — re-prompt or truncate-and-confirm? | Re-prompt. Simpler, no extra state. |
| Q-B | Do Q3 trait-rate-limits apply during onboarding? | **No.** Floors always apply; rate limits only kick in post-completion. |
| Q-C | Is the guardrails-ack step mandatory? | **No.** Show it but treat silence as acceptance. |
| Q-D | `user_address` fallback when NULL | Prefer `human_display_name`; never generic. |
| Q-E | Should `/cancel` be supported mid-onboarding? | **Yes.** Clean UX. Deletes onboarding state but keeps whatever name was already saved. |
| Q-F | Does the guided-mode 1-5 scale need plain-language anchor labels per trait, or is one generic label set fine? | Per-trait. Warmth's "1" ≠ directness's "1" semantically. |
| Q-G | If the user picks `express` mode, do we still run the guardrails-ack step? | **Yes**, uniformly across all 3 persona modes. |
| Q-H | How do we surface this onboarding on existing test homes that already have a saved persona? | **Do not.** v2 onboarding only runs for users with no existing persona. Existing users use the NL preference path (`be more direct`, etc.). |

---

## 7. Non-goals

v2 explicitly does **not**:
- Change the post-onboarding NL preference loop (still `be more direct`, etc.)
- Touch Spark Swarm integration (the swarm-bridge path still shortcircuits onboarding)
- Add a "voice" trait beyond the 5 existing ones (warmth/directness/playfulness/pacing/assertiveness)
- Add an "expertise domain" question (maybe v3)
- Add a "chip selection" question (maybe v3)
- Implement any UI — this is Telegram-surface text only
- Touch the default chip at `src/spark_intelligence/personality/chips/default.py`

---

## 8. Rollout strategy

1. **Phase 1 lands first.** `agent_name` becomes nullable; fallbacks removed. This is a prerequisite for v2 because v2 can't emit "Right now it's `None`" — the new Step 1 prompt has no default-name anchor.
2. **v2 is feature-flagged** via a config entry `personality.onboarding.version` = `"v1" | "v2"`. Default `v1` during development; flip to `v2` after the operator accepts the design.
3. **Existing homes with saved personas are untouched** (Q-H).
4. **Test home:** the operator's `.tmp-home-live-telegram-real` has `"Operator"` saved. Because it *has* a saved persona, v2 will not re-run onboarding. The `"Operator"` data can stay as-is unless the operator wants it cleared.
5. **Migration:** the `user_address` column is added via an idempotent `ALTER TABLE IF NOT EXISTS`-equivalent (SQLite needs a `PRAGMA table_info` guard).

---

## 9. What this doc does NOT commit to

- **Exact prompt wording is a first draft.** The operator should edit any prompt that reads wrong.
- **The 1-5 scale is a first draft.** An alternative is a 1-10 scale or a pair of phrases ("warm / businesslike") with a slider.
- **Step ordering is a first draft.** An alternative is to put `user_address` *after* persona mode — rationale being that personality is more engaging to set than a name-the-user question.
- **The guardrails-ack step is the most opinionated piece.** It can be dropped entirely if the operator feels it's preachy.

---

## 10. Summary

v2 turns v1's two-step free-text onboarding into a **required-name, 4-to-8-step** state machine that:
- Requires a user-defined agent name (no default)
- Captures how the agent addresses the user (new field)
- Offers express / guided / skip modes for personality capture
- Surfaces the §G guardrails as a first-class contract
- Ends with a recap and a feedback-loop explainer

Dependencies: Phase 1 (remove `"Spark Agent"` fallback, make `agent_name` nullable) must ship first.

Next step: operator reviews this doc, answers Q-A through Q-H, and green-lights or redlines the direction. Until then, no code changes.

---

## 11. Operator decisions — Q-A through Q-H (locked 2026-04-10)

| ID | Question | Decision | Notes |
|----|----------|----------|-------|
| Q-A | Long names (>48 chars) during `awaiting_name` | **Re-prompt** | Simpler; no `awaiting_name_confirm` sub-state. Matches recommendation. |
| Q-B | Do trait rate limits (±0.25/turn) apply during onboarding? | **No** | Onboarding is a one-shot explicit capture. Q3 floors still apply; rate limits kick in post-completion. Matches recommendation. |
| Q-C | Guardrails-ack step mandatory? | **Show but don't gate** | Display guardrails once; silence = acceptance. Matches recommendation. |
| Q-D | `user_address` fallback when NULL | **Empty / no address** | Do not personalize when `user_address` is NULL. Messages omit the salutation entirely rather than falling back to `human_display_name`. *Diverges from recommendation.* |
| Q-E | Support `/cancel` during onboarding? | **Yes — and `/cancel` fully resets the agent** | Typing `/cancel` wipes in-progress onboarding state AND the agent's saved name. User starts over from pairing next time. *Diverges from recommendation, which kept the name.* |
| Q-F | Per-trait anchor labels for the 1-5 guided scale? | **Per-trait** | Each of the 5 traits gets its own 5-anchor label set (25 phrases total). Matches recommendation. |
| Q-G | Guardrails step for `express` / `freestyle` modes? | **Show uniformly** | All 3 persona modes hit the same guardrails step. Matches recommendation. |
| Q-H | Surface v2 onboarding to existing users with saved personas? | **Run once for everyone, with a one-tap skip** | Existing users see a short offer ("Want to re-set up your agent? (yes/skip)"). Only `yes` starts v2 onboarding; `skip` or any other reply keeps the existing persona untouched. *Diverges from the "do not re-run" recommendation.* |

### Implementation notes derived from decisions

- **Q-D (empty user_address):** The read path for reply templates needs an "address-aware" formatting helper that produces two variants — with salutation (when `user_address` is set) and without (when NULL). Do NOT silently substitute `human_display_name`; treat NULL as "user explicitly wants no address" OR "user hasn't set one yet" — both produce the same no-address rendering. A follow-up UX question (not blocking Phase 2): should the onboarding's `awaiting_user_address` step offer an explicit "don't address me" choice, or is leaving it blank enough?

- **Q-E (/cancel wipes name):** `rename_agent_identity` currently sets `agent_name` and writes to `agent_rename_history`. A `/cancel` path needs a new service function that:
  1. Deletes the in-progress `agent_persona_onboarding_state` row
  2. Clears `agent_profiles.agent_name` back to empty string (the Phase 1 sentinel)
  3. Records the wipe in `agent_rename_history` with `source_ref="onboarding-cancel"` and `new_name=""`
  4. Leaves `agent_profiles.agent_id` and the pairing intact (so the user can restart without re-pairing)

- **Q-H (existing users get a skip offer):** New state `awaiting_reonboard_consent` is entered on first DM for any user whose `agent_persona_onboarding_state.status != 'completed'` AND who has a saved persona. The state presents a short offer; any reply other than `yes` / `y` / `restart` transitions to `completed` without changes (silence = skip, not nag). Users who already have `status='completed'` but no persona profile are treated as brand-new.

### Questions opened by the decisions (deferred, not blocking Phase 2)

- **Q-I (new):** Should the `awaiting_user_address` step offer an explicit "don't address me by name" option, or does leaving it blank cover that? — follow-up on Q-D.
- **Q-J (new):** When `/cancel` wipes the agent name, do we also wipe `agent_persona_profiles` (rules, traits, summary)? Or just the name and onboarding state? — follow-up on Q-E. Default assumption: wipe only the name and onboarding state, keep the profile. The user can `/cancel` to rename but keep the vibe.
- **Q-K (new):** For Q-H existing-user offers, how is "has a saved persona" detected? Candidate: `agent_persona_profiles` row exists for the agent with non-empty `behavioral_rules_json` or `persona_summary`. Needs confirmation once Phase 2 implementation starts.
