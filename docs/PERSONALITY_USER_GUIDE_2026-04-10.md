# Personality System — User Guide and Gaps

**Status:** Written 2026-04-10 after Phase 2 + Phase 3 closed out. Reflects the state of
`origin/main` at `fe457af` (and the concurrent memory-code commit `7f51f39` layered on
top, which does not touch personality). Every factual claim in this doc has a
`file:line` reference so it can be re-verified without guessing.

Companions:
- `docs/PERSONALITY_ONBOARDING_V2_DESIGN_2026-04-10.md` — design doc, §11 holds the locked Q-A..Q-H decisions
- `docs/PERSONALITY_PHASE2_PLAN_2026-04-10.md` — commit-sized implementation plan
- `docs/PERSONALITY_PHASE2_COMPLETION_2026-04-10.md` — commit-by-commit delivery receipt

---

## Part 1 — How to use it

### 1.1 What the personality system does

Each agent (currently Telegram-only) gets:
- A user-defined **name** (no default; the user must pick one)
- A **user-address** string (how the agent calls the user, or NULL = no salutation)
- Five **trait values** on a 0.0–1.0 scale, default 0.50: `warmth`, `directness`, `playfulness`, `pacing`, `assertiveness` (`loader.py:64-70`)
- Up to 8 **behavioral rules** (short style directives the user authored)
- Three **guardrail commitments** shown during onboarding (no glazing, better-way surfacing, honest failure reporting)
- A **per-user trait delta profile** that stacks on top of the base traits, clamped to ±0.5 cumulative

The v2 state machine captures all of that in a short conversation, then the post-onboarding natural-language loop keeps tuning it over time.

---

### 1.2 Entry points

Personality onboarding is triggered on the Telegram surface in two places:
- `src/spark_intelligence/adapters/telegram/runtime.py:698` — DM handler
- `src/spark_intelligence/adapters/telegram/runtime.py:1286` — async polling handler

Both call `maybe_handle_agent_persona_onboarding_turn` with `start_if_eligible=pairing_welcome_pending(...) or agent_has_reonboard_candidate(...)`. The second gate was added in P2-13 so existing users with a saved persona get offered a one-tap re-setup.

**Telegram is the only surface right now.** There is no CLI entry point. See §Gaps.

---

### 1.3 Onboarding flow (new users)

```
awaiting_name
  → awaiting_user_address
  → awaiting_persona_mode
       → guided    → awaiting_persona_guided    (5 trait questions)
       → express   → awaiting_persona_express   (4 presets)
       → freestyle → awaiting_persona_freestyle (comma-separated descriptor)
  → awaiting_guardrails_ack
  → completed
```

Any reply matching `/cancel`, `/quit`, or `/stop` at any state wipes the in-progress onboarding blob AND clears the saved agent name back to the empty-string sentinel (`loader.py:1531-1553`, `_ONBOARDING_CANCEL_TOKENS` at `loader.py:2821-2827`). The persona profile itself is **not** wiped by `/cancel` — that was Q-J's default resolution.

---

### 1.4 State-by-state reference

#### `awaiting_name` (`loader.py:1606-1674`)

**Prompt:** *"Let's set up your agent. Short conversation: name first, then personality. You can say `/cancel` at any time to stop and reset. What should I call your agent? Reply with a name like `Atlas`, `Nova`, or `Lyra`."*

**Accepted inputs:**
- A bare name (1–2 tokens, no embedded sentences)
- Explicit forms like `your name is Atlas`, `i want to call you Nova`, `rename yourself to Lyra` (`loader.py:160-164`, case-insensitive, 2–41 chars)
- `keep` / `keep it` / `keep current name` / `keep the current name` — only if there's already a saved name

**Rejection:** Empty/whitespace or no extractable name re-prompts with *"I need a short agent name first. Reply with something like `Atlas`, `Nova`, or `Lyra`."*

---

#### `awaiting_user_address` (`loader.py:1676-1709`)

**Prompt:** *"How should your agent address you? Reply with a name or salutation like `Alice`, `Boss`, or `Captain`. Say `skip` to keep replies neutral."*

**Accepted inputs:**
- Any free-text string (stored up to 48 chars via `set_human_user_address`)
- Skip tokens (`_ONBOARDING_ADDRESS_SKIP_TOKENS`, `loader.py:2513-2529`): `skip`, `none`, `blank`, `no`, `nope`, `(blank)`, `(skip)`, `(none)`, `no address`, `no salutation`, `don't`, `dont`, `nothing`
- An empty message also counts as skip

**NULL behavior:** If you skip, `user_address` stays NULL and the agent will omit salutations entirely (`format_address_aware_line` at `loader.py:2475-2510` collapses `{salutation}` and `{salutation_suffix}` to empty string; if a template starts with `{salutation}`, it capitalizes the first alphabetic character).

---

#### `awaiting_persona_mode` (`loader.py:1711-1768`)

**Prompt:** *"How should we shape your personality? 1. Guided — I ask 5 quick questions. 2. Express — pick a preset style. 3. Freestyle — describe it in your own words. Reply with `guided`, `express`, `freestyle`, or the number `1`, `2`, or `3`."*

**Accepted inputs** (case-insensitive, `loader.py:2557` via `_parse_onboarding_persona_mode`):
- Guided: `guided`, `guide`, `questions`, `question`, `1`, `one`
- Express: `express`, `preset`, `presets`, `2`, `two`
- Freestyle: `freestyle`, `free`, `freeform`, `describe`, `3`, `three`

**Rejection:** Anything unparseable re-prompts with *"I didn't catch that. Reply with `guided`, `express`, `freestyle`, or the number `1`, `2`, or `3`."*

---

#### `awaiting_persona_guided` (`loader.py:1770-1877`)

Five sub-questions asked in this order (`loader.py:2571-2577`): warmth → directness → playfulness → pacing → assertiveness.

**Each prompt format:**
```
Question {n} of 5. {trait_prompt}
  1. {anchor 1}
  2. {anchor 2}
  3. {anchor 3}
  4. {anchor 4}
  5. {anchor 5}

Reply with a number from 1 to 5.
```

**Anchor labels** (`_GUIDED_TRAIT_ANCHORS`, `loader.py:86-122`):

| Trait | 1 | 2 | 3 | 4 | 5 |
|-------|---|---|---|---|---|
| warmth | reserved and formal | mostly reserved | balanced warmth | warm and friendly | very warm, uses your name often |
| directness | gentle and exploratory | mostly gentle | balanced directness | direct and concise | very direct, skips preamble |
| playfulness | serious and measured | mostly serious | balanced playfulness | playful and witty | very playful, cracks jokes |
| pacing | deliberate, explains thoroughly | thoughtful and thorough | balanced pacing | brisk and efficient | rapid, one-line answers |
| assertiveness | gentle, hedges opinions | measured and polite | balanced assertiveness | assertive and confident | very assertive, states views plainly |

**Accepted inputs** per sub-step (`loader.py:2604-2626`):
- Plain digits `1`–`5`
- Embedded digits like `rating 3`, `3 please`
- English words `one`, `two`, `three`, `four`, `five` (case-insensitive)

**Rating → trait value** (`loader.py:2579-2585`):

| Rating | Trait value |
|--------|-------------|
| 1 | 0.10 |
| 2 | 0.30 |
| 3 | 0.50 (default) |
| 4 | 0.70 |
| 5 | 0.90 |

**Rejection:** Any non-numeric answer re-prompts with *"I need a number from 1 to 5 (or the word `one`, `two`, `three`, `four`, or `five`)."* and re-shows the same question.

---

#### `awaiting_persona_express` (`loader.py:1878-1936`)

**Prompt shows the four presets:**
```
Pick a preset by number or name.
  1. `operator` — Operator: Direct, grounded, and concrete. Prefers next steps over filler.
  2. `claude-like` — Claude-like: Strong continuity, grounded follow-ups, low canned phrasing.
  3. `concise` — Concise: Short answers, brisk pacing, low filler.
  4. `warm` — Warm: Friendly and human, calm without losing structure.

Reply with the name (for example `warm`) or the number.
```

**Preset trait vectors** (`loader.py:2662-2707`):

| Preset | warmth | directness | playfulness | pacing | assertiveness |
|--------|--------|------------|-------------|--------|---------------|
| `operator` | 0.40 | 0.85 | 0.25 | 0.70 | 0.80 |
| `claude-like` | 0.65 | 0.55 | 0.45 | 0.50 | 0.55 |
| `concise` | 0.45 | 0.80 | 0.30 | 0.75 | 0.65 |
| `warm` | 0.85 | 0.50 | 0.60 | 0.40 | 0.45 |

**Accepted inputs** (`loader.py:2730-2765` via `_parse_onboarding_persona_express_choice`):
- Canonical keys (case-insensitive): `operator`, `claude-like`, `concise`, `warm`
- Labels: `Operator`, `Claude-like`, `Concise`, `Warm`
- Digits `1`–`4`, words `one`..`four`
- Partial token matching (e.g. a message containing the word "warm" matches the warm preset)

---

#### `awaiting_persona_freestyle` (`loader.py:1988-2058`)

**Prompt:** *"Now describe the personality you want. You can say something like `calm, strategic, very direct, low-fluff`."*

**How it's parsed:**
- `_extract_trait_deltas()` matches ~27 NL patterns (`loader.py:127-158`)
- `_extract_onboarding_descriptor_deltas()` keyword-matches a fixed vocabulary: `direct`, `concise`, `low-fluff`, `warm`, `friendly`, `playful`, `serious`, `calm`, `assertive`, `confident`, `gentle`, `cautious`, `strategic` (`loader.py:2303-2332`)
- Skip tokens: `skip`, `skip for now`, `later`, `use default` → falls back to all-0.50 balanced defaults

**Rejection:** None — any non-skip input is accepted; anything that doesn't match extraction patterns just produces no deltas.

---

#### `awaiting_guardrails_ack` (`loader.py:1937-1960`)

**Prompt:**
```
One more thing. Here's what `{agent_name}` commits to:

  1. No glazing. {agent_name} won't say 'great idea!' unless it's actually a great idea
     — and will say so when it isn't.
  2. Better-way surfacing. If there's a clearly better approach, {agent_name} will say
     so, not just go along.
  3. Honest failure reporting. If something isn't working, {agent_name} will tell you,
     not pretend.

Reply `ok` to accept, or `change` to adjust. You can always say `be gentler` or
`be more direct` later.
```

**Accepted inputs:**
- `change` / `adjust` / `edit` / `modify` (case-insensitive) → soft-reprompt: *"No problem. Reply `ok` to keep the commitments as-is, or say things like `be gentler` / `be more direct` any time after onboarding to shape tone."* Stays in `awaiting_guardrails_ack`.
- Anything else (including empty) → accepted; transitions to `completed`.

Per Q-C, silence is treated as acceptance. The guardrails are always active regardless of acknowledgment — the ack step is about visibility, not gating.

---

#### `awaiting_reonboard_consent` (`loader.py:1555-1604`)

**When it fires:** Existing users with a saved persona profile AND no in-progress onboarding state blob, on their first DM. Gate is `agent_has_reonboard_candidate()` at `runtime.py:693-696` / `loader.py`.

**Prompt:**
```
Want to re-run setup for `{agent_name}`? Your current personality stays put unless
you say `yes`.

Reply `yes` to start the short setup conversation, or anything else to keep things
as they are.
```

**"Yes" tokens** (`_REONBOARD_CONSENT_YES_TOKENS`, `loader.py:2846-2859`):
`yes`, `y`, `yeah`, `yep`, `yup`, `sure`, `restart`, `redo`, `re-run`, `rerun` (case-insensitive)

**"No" behavior:** Any other reply — including the original message the user was trying to send — closes the offer by writing `status="completed"` to the onboarding blob and returns `None` so the original message passes through to the researcher bridge unchanged. The offer **never re-fires** once closed.

---

### 1.5 After onboarding — shaping the personality

Once `completed`, the system keeps watching for NL preferences on every subsequent message via `detect_and_persist_nl_preferences()` (`loader.py:2064-2110`).

**Recognized phrases** (pattern list at `loader.py:127-158`, case-insensitive, ~27 patterns):

| Phrase family | Effect |
|---------------|--------|
| `be direct` / `more direct` | directness +0.4 |
| `be concise` / `keep replies short` | directness +0.2, pacing +0.3 |
| `skip preamble` / `get to the point` | directness +0.4, pacing +0.3 |
| `more detail` / `more thorough` | directness −0.3, pacing −0.3 |
| `be casual` / `more human` | warmth +0.2, playfulness +0.1 |
| `be warm` / `warmer` | warmth +0.4 |
| `be playful` | playfulness +0.4 |
| `tone it down` | assertiveness −0.3, playfulness −0.2 |
| `slow down` | pacing −0.4, directness −0.2 |
| `speed it up` | pacing +0.3 |
| `be serious` | playfulness −0.3 |
| `stop hedging` | assertiveness +0.4, directness +0.3 |
| `stop apologizing` | assertiveness +0.3, directness +0.2 |

Deltas stack additively on a per-user profile and are clamped to **±0.5 cumulative** per trait (`loader.py:2091`). There is no per-turn rate limit in the code right now — see §Gaps for the discrepancy with the design doc.

---

### 1.6 Personality query commands

`detect_personality_query()` at `loader.py:2909-2998` recognizes two intent families:

**Status ("what am I configured as") — `loader.py:2884-2890`:**
- `what's my personality` / `what is my personality`
- `how am I/are you configured`
- `show my/your personality`
- `current personality/style`
- `my style/personality settings/preferences`

Effect: Formats the profile as context and injects it into the LLM reply so the agent describes its current state.

**Reset — `loader.py:2892-2898`:**
- `reset my/personality/style/preferences`
- `go back to default/normal/original`
- `clear my personality/style settings/changes`
- `undo personality/style changes`
- `remove my personality/style preferences/customization`

Effect: Clears all user trait deltas (`loader.py:2932`), deletes persistent memory if the config manager is wired, records a `personality_reset` event, and injects a confirmation context *"[Personality action: RESET]..."* so the LLM acknowledges the reset.

**Note:** "Reset" clears the **per-user NL deltas**. It does **not** wipe the base `agent_persona_profiles` row (the name, traits, and rules set during onboarding). To get a clean slate including the base profile, use `/cancel` (wipes name + onboarding state, keeps profile) followed by a re-onboard.

---

### 1.7 Authoring mode (explicit persona edits)

`detect_and_persist_agent_persona_preferences()` at `loader.py:2113-2206` recognizes "agent persona authoring" messages that do more than shift trait deltas — they can rename the agent or add behavioral rules.

**Triggered by** (`_is_agent_persona_authoring_message` at `loader.py:427-439`):
- An explicit rename phrase (e.g. `your name is X`, `rename yourself to X`)
- 2+ extractable behavioral rules in one message
- Personality markers like `your personality`, `your style`, `agent persona`, `let's define your personality` combined with a trait signal or at least one extractable behavioral rule
- Messages starting with `/agent persona `

**Behavioral rules** (`_extract_behavioral_rules` at `loader.py:375-388`):
- 12–180 characters each
- Must start with one of 22 prefixes: `be `, `keep `, `sound `, `avoid `, `give `, `skip `, etc. (`loader.py:176-212`)
- Kept to the last 8 rules, deduplicated

**Persistence:** Writes to `agent_persona_profiles` with `mutation_kind="explicit_authoring"` (`loader.py:2169`).

### 1.7.1 Telegram interaction-preference sync

The Telegram gateway also has a launch-safe path for explicit durable interaction preferences, for example:

- `when you talk to me, use short paragraphs with blank lines`
- `I want my agent to be more conversational`
- `do not give chatbot-like generic answers`

Telegram first stores the preference in its per-user local hot context so the next reply can adapt immediately. It then sends Builder a canonical authoring message:

```text
Your style should follow this saved agent interaction preference.
When you talk to me, use short paragraphs with blank lines.
```

Builder treats that as explicit agent persona authoring and persists the extracted behavioral rule into `agent_persona_profiles` / `agent_persona_mutations`.

Boundary rules:

- This is agent-style state, not a user fact.
- It does not write to `domain-chip-memory`.
- It does not create `personality_trait_profiles` rows.
- It does not mutate global `spark-character` chip files.
- If Builder sync is disabled or unavailable, Telegram keeps only the local hot preference and logs the miss.

---

### 1.8 Storage

Seven tables hold personality state (all in the main state SQLite DB, schema in `src/spark_intelligence/state/db.py`):

| Table | Purpose | Key columns |
|-------|---------|-------------|
| `agent_persona_profiles` | Base persona (name, traits, rules, summary, provenance) | `agent_id` PK |
| `personality_trait_profiles` | Per-user NL delta overlay | `human_id` PK, `deltas_json` |
| `agent_profiles` | Agent identity metadata | `agent_id` PK |
| `humans` | Human identity; stores `user_address` (P2-1 column) | `human_id` PK |
| `agent_rename_history` | Audit trail of name changes including `/cancel` wipes | `rename_id` PK, `source_surface` |
| `agent_persona_mutations` | Audit trail of persona changes (kind, delta, source) | `mutation_id` PK, `mutation_kind` |
| `runtime_state` | Onboarding state blob (`agent_onboarding:...` keys) + NL delta compatibility mirror | `state_key` PK |

The onboarding state machine writes its JSON blob to `runtime_state` under `agent_onboarding:human:{channel}:{external_user_id}` — see the test fixture at `tests/test_operator_pairing_flows.py` for the exact key pattern.

---

### 1.8.1 Future path

Safe next steps:

- Show users which saved interaction preferences are active, including last updated time and source surface.
- Add natural-language edit/remove flows for individual behavioral rules.
- Keep tests in natural language: one user updates spacing, another asks for directness, a third asks for a one-off style that must not persist.
- Add conflict handling when a new rule contradicts an older one in the same dimension.
- Add a reply-quality evaluator that checks whether saved rules actually change the next response without making it stiff or generic.

Riskier phases that need a design review:

- Passive personality learning from repeated conversation patterns.
- Cross-agent inheritance of style preferences.
- Global evolution back into `spark-character`.
- Memory-side calibration between user facts, agent style rules, and procedural lessons.
- Automatic promotion of conversational observations into durable persona doctrine.

Those phases should require provenance, undo, per-user/per-agent scoping, and memory movement traceability before they ship.

---

### 1.9 Trait floors and rate limits — design vs code reality

The design doc §11 (Q-B, Q3) says:
- **Trait floors:** `directness` and `assertiveness` should have a hard floor of 0.35 (protected traits)
- **Rate limits:** ±0.25 per turn post-onboarding; no limit during onboarding

**What the code actually does:**
- The 0.35 number appears at `loader.py:825-836` as a threshold for LLM context injection (the agent gets different prompt context when a trait falls below 0.35), but there is **no hard clamp** in the trait-setting functions. If you picked guided rating `1` (→ 0.10) for directness, the stored value is 0.10, not floored to 0.35.
- The ±0.5 cumulative cap on NL deltas (`loader.py:2091`) is the only observable rate limit. There is no per-turn ±0.25 enforcement.

This is tracked in §Gaps as **GAP-1** and **GAP-2**.

---

## Part 2 — Known gaps and future work

These are things I noticed during the Phase 2/3 work that are not bugs per se but are real limitations, stale pieces, or open questions I'd want to look at on another day. Ordered rough-priority descending.

### GAP-1 — Trait floors aren't actually enforced

The design doc's Q3 "protected-trait floors" (directness/assertiveness minimum 0.35) is referenced in context-injection logic but never enforced as a clamp when traits are written. A user picking guided rating `1` for directness stores `0.10`, below the stated floor. Either the design was aspirational and should be relaxed in the doc, or the clamp needs to land in `save_agent_persona_profile` / the guided rating-to-value mapper.

**Impact:** Low today — the 0.35 context threshold still catches low-directness in prompt generation. But it means the design doc over-promises.

**Files:** `loader.py:825-836` (where 0.35 is referenced), `loader.py:2579-2585` (rating → value mapping that should clamp but doesn't), `loader.py:2091` (NL delta clamp, uses ±0.5 not 0.35 floor).

---

### GAP-2 — Per-turn rate limits don't exist in code

Design Q-B says rate limits (±0.25/turn) apply post-onboarding. The code has a ±0.5 **cumulative** cap (`loader.py:2091`) but no per-turn limit at all. This means a user can type `be more direct be more direct be more direct` in one message and if any patterns match multiple times, the full delta applies (subject only to the cumulative cap).

**Impact:** Low-medium. Users can't grief this because the cumulative cap still bounds the total, but the "one turn shouldn't swing you more than ±0.25" guarantee from the design isn't real.

**Files:** `loader.py:2091` (the clamp that should be split into per-turn + cumulative).

---

### GAP-3 — Personality system is Telegram-only

`maybe_handle_agent_persona_onboarding_turn` is only called from `src/spark_intelligence/adapters/telegram/runtime.py` (lines 698 and 1286). There is **no CLI entry point** for personality onboarding. If you wanted to drive the flow from the builder-local CLI or any non-Telegram surface, you'd need to wire new call sites.

**Impact:** Medium. Any future CLI-based user would have no way to set their agent's personality.

**What it would take:** Add a `spark_intelligence personality onboard` CLI command that runs the state machine interactively (reads user input, calls the handler, prints the reply), or a one-shot form (`--name Atlas --mode express --preset warm`).

---

### GAP-4 — `PERSONALITY_HANDOFF_2026-03-26.md` is stale v1 content

The top-level handoff doc was written for the v1 two-step onboarding and never updated for v2. It still describes the old flow and doesn't mention any of the new states (`awaiting_user_address`, `awaiting_persona_mode`, etc.). Anyone reading it fresh would get a wrong mental model.

**Impact:** Medium — documentation drift is the #1 trap for future agents coming into this codebase. I almost repeated this mistake in the P2-14 fabrication.

**Fix:** Either add a `SUPERSEDED BY:` header pointing at this guide + the design doc, or archive the file entirely. Low-risk either way.

---

### GAP-5 — No persona provenance surfacing

`agent_persona_profiles.provenance_json` stores metadata about how and when a persona was set (e.g. `"source": "onboarding_guided"`, `"updated_at": "2026-04-10T..."`), and `agent_persona_mutations` has a full audit trail with `mutation_kind`. None of this is exposed to users. The status query (`what's my personality`) shows the current trait values but not "you set this via guided mode on 2026-04-10" or "your last change was 2 days ago".

**Impact:** Low — nice-to-have, not a bug. Would give users context when they're trying to remember what they chose.

**What it would take:** Extend `_format_profile_status()` at `loader.py` to include the most recent `agent_persona_mutations` row's `mutation_kind` and `created_at`. Small change, one new test.

---

### GAP-6 — No `/style preset` command post-onboarding

The four express presets (`operator`, `claude-like`, `concise`, `warm`) are only reachable during onboarding. After `completed`, if you want to switch from your custom persona back to the `warm` preset, you have to either `/cancel` (which wipes the name) then re-onboard, or manually type out every trait change via NL phrases.

**Impact:** Low-medium. Users who want to experiment with presets post-setup have no clean path.

**What it would take:** A new `/style preset <name>` command that writes the preset trait vector to `agent_persona_profiles` without touching name or user_address. Or a new intent in `detect_personality_query` that catches `switch to the warm preset` etc.

---

### GAP-7 — `reset personality` only clears NL deltas, not the base profile

From §1.6: "reset personality" clears the per-user NL delta overlay but does not touch `agent_persona_profiles`. A user who says `reset my personality` probably expects everything to go back to baseline — including their onboarding choices — but the onboarding-chosen traits are preserved under the cleared delta layer.

**Impact:** Medium — user expectation vs implementation mismatch. Not a bug in either layer alone, but the combination is surprising.

**Fix options:**
- Rename the detected intent to something narrower like `reset my style preferences` and add a separate `reset my agent` that wipes both layers
- Or make `reset personality` wipe both layers and document it clearly

---

### GAP-8 — Guardrails can't be structurally softened

The `change` token in `awaiting_guardrails_ack` soft-reprompts the user to "say things like `be gentler` later". There is no way to turn off G1/G2/G3 individually — by design (they're locked) — but there's also no structured way to record "user wants these phrased gently". The `change` branch is effectively a UI acknowledgment with no persistent effect.

**Impact:** Low. Design Q-C explicitly said the guardrails shouldn't gate completion, and this matches that. But the "adjust how firmly they're phrased" path from the design doc §2 step 4 isn't actually implemented.

---

### GAP-9 — Test coverage is Telegram-only

`tests/test_operator_pairing_flows.py` covers the entire state machine via the `simulate_telegram_update` harness. If GAP-3 is fixed and a CLI entry point is added, the tests would need to be duplicated/parameterized to cover both surfaces. Right now there's no `tests/test_personality_cli.py` scaffold.

**Impact:** Low today (no CLI exists). Would become a gap the moment GAP-3 is addressed.

---

### GAP-10 — `loader.py` line-ending inconsistency

`src/spark_intelligence/personality/loader.py` has mixed CRLF/LF line endings (3541 lines, mostly CRLF). Any tool that naively rewrites the file will normalize to LF and cause a massive diff. Phase 2 coped by using byte-level Python patch scripts for every edit. There's no `.gitattributes` normalization rule either.

**Impact:** Medium for anyone editing the file — it's a tripwire for concurrent agents and for standard editing tools. The correct long-term fix is a one-shot normalization commit plus a `.gitattributes` rule, but that commit will look gigantic in diff views.

**Files:** `src/spark_intelligence/personality/loader.py`, `.gitattributes` (currently absent).

---

### GAP-11 — Concurrent-agent working-tree pollution

Throughout this session the working tree had ~15 dirty files from parallel agent sessions (memory code, CLI code, README edits). I never `git add -A`-ed them, but anyone reviewing the repo state sees a noisy `git status`. This is an environmental quirk of the multi-agent setup, not a code gap, but it's worth naming because it creates real risk of accidentally committing someone else's in-flight work.

**Impact:** Process / hygiene. Not fixable in code — mitigated by the "always stage files by explicit path" rule which is now in my memory.

---

## Part 3 — Quick-reference cheat sheet

### For the user

| I want to... | Say this |
|--------------|----------|
| Start over during onboarding | `/cancel` (or `/quit` / `/stop`) |
| Skip addressing | `skip` (or just reply empty) at the `awaiting_user_address` step |
| Pick guided mode | `guided`, `1`, or `one` |
| Pick express mode | `express`, `2`, or `two` |
| Pick freestyle mode | `freestyle`, `3`, or `three` |
| Make the agent more direct | `be more direct` |
| Make the agent warmer | `be warmer` / `warmer` |
| Slow down | `slow down` |
| Check current personality | `what's my personality` |
| Wipe NL adjustments | `reset my personality` |
| Accept onboarding guardrails | `ok` (or send anything / silence) |
| Re-run onboarding as an existing user | Wait for the one-tap offer on your first DM, then reply `yes` |

### For the developer

| I want to... | Look here |
|--------------|-----------|
| Add a new trait | `loader.py:64-70` (defaults), `_GUIDED_TRAIT_ANCHORS` at `loader.py:86-122`, `_NL_TRAIT_PATTERNS` at `loader.py:127-158` |
| Add a new preset | `loader.py:2662-2707` (catalog), `loader.py:2730-2765` (parser) |
| Add a new cancel token | `_ONBOARDING_CANCEL_TOKENS` at `loader.py:2821-2827` |
| Add a new NL preference phrase | `_NL_TRAIT_PATTERNS` at `loader.py:127-158` |
| Add a new state to the machine | `maybe_handle_agent_persona_onboarding_turn` dispatcher around `loader.py:1438-1604` |
| Wire a new surface | Copy the call pattern from `src/spark_intelligence/adapters/telegram/runtime.py:698, 1286` |
| Add a test | `tests/test_operator_pairing_flows.py` (look at the P2-12/P2-13 tests for the reonboard/consent pattern) |

---

## Verification notes

This doc was written against `origin/main` at `fe457af` (Phase 3 correction commit), with the concurrent commit `7f51f39 Harden memory benchmark trust scoring` layered on top locally. Test state: `tests/test_operator_pairing_flows.py` 129/129 passing.

Every `file:line` reference in this guide was cross-checked against a full-file scan of `src/spark_intelligence/personality/loader.py` and the Telegram runtime. If you find a discrepancy, the loader line numbers may have shifted due to concurrent edits — re-grep for the function name (`_GUIDED_TRAIT_ANCHORS`, `_parse_onboarding_persona_mode`, etc.) to get the current location.

If something in this doc is wrong, it's a bug in the doc, not the code. Correct the doc rather than making the code match it.
