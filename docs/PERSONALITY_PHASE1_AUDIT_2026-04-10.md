# Phase 1 Step 1 — Audit findings

Author: Claude (Opus 4.6), 2026-04-10
Status: **Audit complete — operator decision needed before Phase 1 Step 2.**
Companion docs:
- `docs/PERSONALITY_ONBOARDING_V2_DESIGN_2026-04-10.md`
- `docs/PERSONALITY_A11_PROPOSALS_2026-04-10.md`

---

## 1. What this audit answers

Before changing `CanonicalAgentState.agent_name` from `str` to `str | None` (Phase 1 Step 2), I need to verify:
- (a) The `agent_profiles.agent_name` column's declared nullability
- (b) Every call site that reads `canonical_state.agent_name` (so I don't break a downstream path that assumes it's always a non-empty string)
- (c) Every hardcoded `"Spark Agent"` fallback location (so they can be removed atomically)
- (d) Every write path that stores an `agent_name` (so I can require it to be non-empty before write)

---

## 2. Finding A — schema **is `NOT NULL`**

`src/spark_intelligence/state/db.py:413`:

```sql
CREATE TABLE IF NOT EXISTS agent_profiles (
    agent_id TEXT PRIMARY KEY,
    human_id TEXT NOT NULL,
    agent_name TEXT NOT NULL,      -- ← HERE
    origin TEXT NOT NULL,
    status TEXT NOT NULL,
    ...
)
```

**Implication:** SQLite does not support `ALTER TABLE ... DROP NOT NULL` directly. To make `agent_name` nullable we'd need to:

1. Create `agent_profiles_new` with the new schema
2. Copy rows: `INSERT INTO agent_profiles_new SELECT * FROM agent_profiles`
3. Drop the old table: `DROP TABLE agent_profiles`
4. Rename: `ALTER TABLE agent_profiles_new RENAME TO agent_profiles`
5. Recreate the two indexes (`idx_agent_profiles_human_id`, `idx_agent_profiles_external_ref`)
6. Wrap the whole thing in a `PRAGMA foreign_keys=OFF` + transaction dance

This is a **non-trivial migration** that touches the operator's live state.db. Given the history of concurrent-writer corruption in `.tmp-home-live-telegram-real`, this migration is **not something to run blind.**

### Alternative: keep `NOT NULL`, use empty-string sentinel for "unset"

Instead of nullability, we could treat `agent_name = ""` as "no name set yet" and gate user-facing reply paths on `agent_name != ""`. Downsides:
- Empty-string sentinel is semantically fragile (easy to forget a check → silent "no-name" replies)
- `"".strip() or "Spark Agent"` is the exact pattern we're trying to remove — an empty-string sentinel invites the pattern to come back

### Alternative: keep `NOT NULL`, require name-before-create

A third path: don't even **insert** a row into `agent_profiles` until the user has named the agent. The onboarding flow creates a row only after `awaiting_name` succeeds. Upstream code that assumes a row always exists for a paired human would need to handle the "not yet" case explicitly (likely by routing to onboarding).

**My recommendation:** path 3 (require name-before-create) is the cleanest. It doesn't touch the schema, doesn't need a migration, and semantically matches the operator's requirement ("agents should have a name always that the user defines"). The row simply doesn't exist until the user has defined the name.

**Operator decision needed — Q-1:** pick one of:
- **(a)** Table-rebuild migration to make `agent_name` nullable (highest cost, most invasive, schema cleanest)
- **(b)** Empty-string sentinel (lowest cost, semantic drift)
- **(c)** Require name-before-create: no row inserted until user provides name (my recommendation) — schema unchanged, call sites handle "not yet created" case

---

## 3. Finding B — 5 `"Spark Agent"` fallback sites (+1 in tests)

Grep for `"Spark Agent"` in `src/`:

| File | Line | Context | Risk to remove |
|------|------|---------|----------------|
| `src/spark_intelligence/identity/service.py` | 379 | `resolved_name = (display_name or "").strip() or "Spark Agent"` | Medium — this is inside `rename_agent_identity` or a similar write path; empty input currently becomes "Spark Agent" |
| `src/spark_intelligence/identity/service.py` | 531 | `agent_name=str(profile_row["agent_name"] or "Spark Agent")` | High — this is the `read_canonical_agent_state` row-to-dataclass mapping. The `profile_row["agent_name"]` is already `NOT NULL` so the fallback only fires if someone stored an empty string. |
| `src/spark_intelligence/identity/service.py` | 557 | `incoming_name = agent_name.strip() or "Spark Agent"` | Medium — likely another write path taking user input |
| `src/spark_intelligence/identity/service.py` | 612 | `chosen_name or "Spark Agent"` | Medium |
| `src/spark_intelligence/identity/service.py` | 860 | `agent_name = str(result.get("agent_name") or result.get("display_name") or "").strip() or "Spark Agent"` | Medium — payload normalization |
| `tests/test_support.py` | 129 | `current_name = str(current_identity.get("agent_name") or "Spark Agent").strip() or "Spark Agent"` | Low — this is a test helper; it just needs to stop using the literal |

**Total:** 5 src sites + 1 test helper = 6 references to remove.

I have **not read lines 370-390, 520-540, 545-570, 600-620, 850-870 of `identity/service.py`** yet — that's the next step before I actually edit. This audit confirms they exist and need removal; Phase 1 Step 2 will read each one in full context before editing.

---

## 4. Finding C — `read_canonical_agent_state` call sites

Grep count: **31 total occurrences across 9 files.**

| File | Occurrences | Notes |
|------|-------------|-------|
| `src/spark_intelligence/identity/service.py` | 6 | Definition + internal callers |
| `src/spark_intelligence/personality/loader.py` | 4 | Onboarding + preamble + NL preference flows |
| `src/spark_intelligence/adapters/telegram/runtime.py` | 2 | Telegram surface reply path |
| `src/spark_intelligence/cli.py` | 2 | CLI commands |
| `src/spark_intelligence/ops/service.py` | 2 | Ops surface |
| `src/spark_intelligence/identity/__init__.py` | 2 | Re-export shim |
| `tests/test_agent_identity_contracts.py` | 9 | Contract tests |
| `tests/test_operator_pairing_flows.py` | 2 | Pairing flow tests |
| `tests/test_cli_smoke.py` | 2 | CLI smoke tests |

**Implication for Phase 1 Step 2:** if I change `CanonicalAgentState.agent_name` from `str` to `str | None`, every read site that does `canonical_state.agent_name` without a None check becomes a latent bug. I need to read each src call site (17 occurrences across 6 files) to map the required changes.

**Under "require name-before-create" (Q-1 option c) this becomes simpler:** `read_canonical_agent_state` would need to handle the case where the row doesn't exist — likely by returning `None` for the whole `CanonicalAgentState`, not just the `agent_name` field. Existing callers mostly already assume the row exists, so they'd need defensive handling.

Either way, the 17 src call sites need individual review before any change lands.

---

## 5. Finding D — write paths that store `agent_name`

I haven't completed this yet in the audit, but the main ones I can see from the Spark Agent fallback sweep:
- `rename_agent_identity` (line 379 area — pulls `display_name` from input)
- Whatever writes `profile_row["agent_name"]` initially — probably in `create_canonical_agent_record` or similar
- `line 557` — "incoming_name" suggests a pair/link path from an external system (Spark Swarm?)
- `line 612` — "chosen_name" suggests a conflict-resolution or merge path
- `line 860` — payload normalization (probably a webhook or ingest path)

**These 5 write paths all currently accept empty input and silently replace with "Spark Agent".** Under the new rule, each must either:
- Reject the write (raise or return a validation error), or
- Route to onboarding / ask the user for a name first

**Phase 1 Step 2 task:** read the full context of each of these 5 sites, decide reject-vs-route-to-onboarding per site, and implement.

---

## 6. Finding E — in-flight preamble fix (uncommitted)

`src/spark_intelligence/personality/loader.py:833-862` currently has the Gap #1 fix in the working tree but not committed. The fix reads:

```python
playfulness = float(traits.get("playfulness", 0.5))
...
# runtime_command surface: short, trait-shaped name tag...
if directness >= 0.65 or pacing >= 0.6 or warmth <= 0.4:
    return f"{visible_name}:"
if warmth >= 0.65 or playfulness >= 0.6:
    return f"{visible_name} here."
return f"{visible_name}:"
```

This fix currently has **no branch for the "no saved agent name" case.** Under the new rule, if `visible_name` is `None` (or the row doesn't exist yet), the function must return `""` (empty preamble, no name tag) rather than `"None:"`. Phase 1 Step 4 handles this.

---

## 7. Finding F — test home data question

`.tmp-home-live-telegram-real/state.db` has `"Operator"` saved as `agent_name`. That's user-defined data (the operator chose it), not a code default. Under the new rule:

- The row should stay — `"Operator"` is a valid user-chosen name
- v2 onboarding will **not** re-run because the home already has a saved persona (see v2 design §5.4, Q-H)
- Nothing needs to change in the test home data for Phase 1 to land

**Unless the operator wants to clear it as a test vehicle for the new flow** — that's their call.

---

## 8. Phase 1 revised plan (contingent on Q-1)

### If Q-1 = (a) Table-rebuild migration
1. Write SQLite migration helper `_migrate_agent_profiles_drop_not_null_agent_name` in `state/db.py`
2. Change schema constant to `agent_name TEXT`
3. Change `CanonicalAgentState.agent_name` to `str | None`
4. Remove 5 "Spark Agent" fallbacks
5. Read and patch 17 src call sites for None-handling
6. Update tests

### If Q-1 = (c, recommended) Require name-before-create
1. Schema unchanged
2. Change the agent-creation code path to **not insert** into `agent_profiles` until a name is provided
3. Change `read_canonical_agent_state` to return `None` (or a sentinel `NoAgentYet` state) when the row doesn't exist
4. Remove 5 "Spark Agent" fallbacks
5. Update every read site to handle the "no agent yet" branch — likely routing to onboarding
6. Update tests

### If Q-1 = (b) Empty-string sentinel
1. Schema unchanged
2. Change agent-creation to use `""` instead of `"Spark Agent"`
3. Change every read site to check `if canonical_state.agent_name:` before using it
4. Remove 5 "Spark Agent" fallbacks
5. Update tests

**All three paths require the same ~17 src call-site review.** The difference is the size of the defensive branch each call site has to add.

---

## 9. What I still need before Phase 1 Step 2 starts

1. **Operator answers Q-1** — pick (a), (b), or (c)
2. **Operator reviews the v2 onboarding design** (`PERSONALITY_ONBOARDING_V2_DESIGN_2026-04-10.md`) and confirms the agent-creation flow should gate on a user-provided name
3. **Operator confirms `.tmp-home-live-telegram-real`'s `"Operator"` data stays as-is** (no test-home mutations required)
4. **Q-1 decision logged** — I'll add it as a new locked question in `PERSONALITY_A11_PROPOSALS_2026-04-10.md` under a new §H "implementation-level questions" section, or treat it as scoped to this Phase 1 doc

---

## 10. Summary

Schema finding is the headline: **`agent_profiles.agent_name` is `NOT NULL`**, so the original plan ("change `CanonicalAgentState.agent_name` from `str` to `str | None`") needs to either include a table-rebuild migration (option a) or abandon nullability in favor of a different approach (option b empty-string sentinel, or option c require name-before-create).

My recommendation is **option c**: schema unchanged, no migration, agent-creation gated on a user-provided name, `read_canonical_agent_state` handles the "no row yet" case, 5 fallbacks removed. This is the cleanest semantically and the safest operationally (no live schema change on a database that's had recent corruption issues).

Audit status: **complete.** Phase 1 Step 2 is blocked on Q-1.

---

## Addendum 2026-04-10 — Q-1 revision after full code read

**Original recommendation:** option (c) — require name-before-create, `read_canonical_agent_state` returns `CanonicalAgentState | None`, 17 call sites updated.

**Revised recommendation after reading the full context:** **option (b)** — empty-string sentinel, `CanonicalAgentState.agent_name` stays `str`, new `has_user_defined_name` property as the user-facing gate.

**Why the revision:**
1. `read_canonical_agent_state` is entangled with `resolve_canonical_agent_identity` — lines 504 and 517 of the original call it when the link or profile row is missing, acting as a create-if-missing shortcut. `rename_agent_identity` also depends on this shortcut (line 718 calls resolve with no display_name during onboarding). Decoupling read from create would cascade into `rename_agent_identity` and every onboarding touchpoint, a much larger surgery than the audit estimated.
2. Empty-string sentinel is **already half-present** in the codebase — `resolve_canonical_agent_identity` line 453 (original) has: `elif ... and not str(profile_row["agent_name"] or "").strip() and resolved_name:` — meaning the code already treats empty-string as "unset" in at least one repair path. This precedent reduces the semantic-drift risk I cited in the original audit.
3. `build_telegram_surface_identity_preamble` **already has an empty-name guard** at lines 829-830 (`if not visible_name: return ""`) and a no-saved-persona guard at line 851. Both mean the preamble is already safe under the new empty-name regime without any preamble-side edits.
4. The user-facing contract — *"agents should have a name always that the user defines"* — is satisfied by gating all user-facing output on `has_user_defined_name`. The DB-level representation ("" vs NULL) is an implementation detail the operator doesn't see.

**What changed:**
- `CanonicalAgentState.agent_name` stays `str` (not `str | None`)
- `read_canonical_agent_state` still auto-creates (still calls `resolve_canonical_agent_identity` on missing rows)
- New rows are created with `agent_name = ""` when no `display_name` is provided
- New property `CanonicalAgentState.has_user_defined_name` returns True iff the name is a real, non-whitespace string
- `to_payload()` exposes `has_user_defined_name` so JSON-surface callers can gate too
- 5 `"Spark Agent"` literals removed:
  - line 379 (resolve create path): empty-string passthrough
  - line 531 (read_canonical_agent_state): empty-string passthrough
  - line 557 (link_spark_swarm_agent): **strict raise** — external system must propagate a user-defined name
  - line 612 (link_spark_swarm_agent INSERT): coalesce to `incoming_name` (which is guaranteed non-empty by the raise above)
  - line 860 (normalize_spark_swarm_identity_import): **strict raise** — import boundary must propagate a user-defined name
- `tests/test_support.py:129` fake swarm helper: empty-string passthrough (matches real hook contract)

**What did NOT change:**
- `read_canonical_agent_state` signature
- `resolve_canonical_agent_identity` signature
- `rename_agent_identity` — still works because it UPDATEs the agent_name field regardless of prior value
- The 17 call sites of `read_canonical_agent_state` — none needed updates because `agent_name` is still `str`, just can be empty
- Schema — no migration required

**Trade-off honestly stated:**
- Option (b) is marginally more fragile than option (c) because an empty-string sentinel is easier to forget to check than a `None`. The `has_user_defined_name` property is the mitigation — future readers can grep for it to find every user-facing gate.
- Phase 1 Steps 3 (onboarding flow) and 5 (tests) still need to land for the "user must define a name before the agent speaks" contract to be fully enforced. Right now, a user with no saved persona would see no name tag (preamble returns ""), which is better than seeing "Spark Agent" — but not as guided as a first-run onboarding nudge. Step 3 handles that.

**What this addendum supersedes:** the "Phase 1 revised plan" in §8 above (all three Q-1 branches). The actual shipped plan is option (b) as described here. §8 is kept for historical context.

---

## 11. Finding G (discovered during Phase 1 Step 5) — 6th default-name leak in `approve_pairing`

While sweeping tests for broken assertions after the Phase 1 Step 2 edits, I grepped for other places that might fall back to a machine-generated agent name and found a **sixth leak** that the original audit missed:

`src/spark_intelligence/identity/service.py:1001`:

```python
def approve_pairing(
    *,
    state_db: StateDB,
    channel_id: str,
    external_user_id: str,
    display_name: str | None = None,
    ...
) -> str:
    ...
    resolved_name = display_name or f"{channel_id} user {external_user_id}"
    human_id, _, _ = _activate_channel_access(
        state_db=state_db,
        channel_id=channel_id,
        external_user_id=external_user_id,
        display_name=resolved_name,
    )
```

**The chain:**

1. `approve_pairing(..., display_name=None)` — called by CLI / Telegram operator pairing approval when no explicit display_name is provided
2. Line 1001 synthesises `resolved_name = "telegram user 1234567890"` (machine-generated)
3. Line 1006 passes this as `display_name` to `_activate_channel_access`
4. `_activate_channel_access` line 972 passes the same `display_name` to `resolve_canonical_agent_identity(display_name=display_name)`
5. Inside `resolve_canonical_agent_identity`, the Phase 1 Step 2 edit at line 379 now does `resolved_name = (display_name or "").strip()` — so `"telegram user 1234567890"` survives as the agent_name and is inserted into `agent_profiles.agent_name`
6. Result: the new agent has `agent_name = "telegram user 1234567890"`, which satisfies the `has_user_defined_name` property (it is a non-whitespace string), so all user-facing gates would treat it as a legitimate user-defined name even though it is a machine-synthesised fallback

**Why this is a smell but not a crisis:**

- It only fires when `approve_pairing` is called without an explicit `display_name`. Every test in `test_agent_identity_contracts.py` and `test_operator_pairing_flows.py` passes `display_name` explicitly, so no tests break.
- The Telegram `/approve` operator path typically resolves a display name from the Telegram user profile before calling `approve_pairing`, so in practice the synthesised fallback rarely fires.
- The deeper architectural issue is that `approve_pairing` overloads **the human's display_name** as **the agent's display_name**. These are conceptually different: one is "what we call the human" and the other is "what the user names their agent". Fixing this properly means splitting them into two parameters, which is a surgical refactor across the pairing path.

**Scope decision:** I am **not fixing Finding G in Phase 1.** Reasons:

1. The operator's "go" signal was on the audit's original Phase 1 plan, which listed 5 src fallback sites. Finding G is a 6th site discovered after the plan was approved. Fixing it is scope-creep without explicit authorisation.
2. The fix requires an architectural split (human display_name vs agent display_name) that touches the pairing flow, the CLI, the Telegram approval path, and likely several tests. This is a Phase 2 or Phase 3 concern, not a Phase 1 hotfix.
3. The current Phase 1 regime (5 fallbacks removed, `has_user_defined_name` gate, empty-string sentinel) **reduces** the blast radius of Finding G: previously, if no display_name was propagated, the agent would silently become `"Spark Agent"` (totally indistinguishable from other agents). Now, the fallback is the machine-generated `"telegram user 1234567890"` form, which is at least uniquely identifiable per-pair and visibly wrong in operator logs.

**Recommended follow-up (Phase 1.5 or Phase 2):**

- Either (i) remove the f-string fallback at line 1001 and raise a `ValueError` when `approve_pairing` is called without a display_name, requiring callers to resolve a display_name from the platform first, OR
- (ii) split `approve_pairing`'s `display_name` parameter into `human_display_name` (goes into `humans.display_name` only) and `agent_display_name` (goes into `resolve_canonical_agent_identity` only, defaulting to empty-string for the new Phase 1 regime), with the agent_display_name flowing through onboarding as an empty canonical state that triggers the name prompt on first DM.

Option (ii) is the more principled fix and aligns with the v2 onboarding design's "name-required" philosophy. Option (i) is a faster stopgap.

**Operator decision needed — Q-2:** should Finding G be addressed next, or deferred behind v2 onboarding work?

**Q-2 resolution (2026-04-10, commit `6955ad5`):** Closed via option (i) variant. Rather than raise `ValueError`, `_activate_channel_access` now simply does not forward `display_name` to `resolve_canonical_agent_identity`. The f-string fallback at `approve_pairing:1001` is retained — it is still the correct label for `humans.display_name` and `channel_accounts` (the human's identity surface), but no longer bleeds into `agent_profiles.agent_name`. The architectural split between human and agent display name is effectively achieved for the pairing path: the human name flows into `humans`/`channel_accounts` as before, and the agent starts with an empty `agent_name` that onboarding fills in.

---

## 12. Phase 1 completion status (2026-04-10)

| Step | Status | Commit |
|------|--------|--------|
| 1 — Audit | Complete | `e030dc4` |
| 2 — Remove 5 fallbacks + `has_user_defined_name` | Complete | `d243d1a` |
| 3 — Onboarding prompt gating | Complete | `84f3bb4` |
| 4 — Commit in-flight preamble fix | Superseded (see §13) | `d243d1a` → reverted in `6955ad5` |
| 5 — Update / verify tests | Complete | `6955ad5` |
| 6 — Finding G fix | Complete | `6955ad5` |

**Known follow-ups logged:**
- Q-A through Q-H from `PERSONALITY_ONBOARDING_V2_DESIGN_2026-04-10.md` — still awaiting operator decisions for Phase 2 scope
- `.tmp-home-live-telegram-real` "Operator" seed data disposition (Finding F)

---

## 13. Addendum — `d243d1a` preamble regression and revert (2026-04-10)

**Discovered while running `tests/test_agent_identity_contracts` after Finding G fix.**

The Phase 1 plan step 4 listed an "in-flight preamble fix" which I assumed was a forward-direction patch. In practice it was the **reverse**: the working-tree hunk I bundled into `d243d1a` re-added a runtime_command name-tag path to `build_telegram_surface_identity_preamble` (`return f"{visible_name}:"` etc.) that had been **deliberately removed** in an earlier operator commit:

- `a603026` "Drop Telegram name preambles" (2026-04-09) explicitly set the runtime_command surface to `return ""` and updated the three matching tests (`test_apply_telegram_surface_persona_*`, `test_build_telegram_surface_identity_preamble_uses_agent_name_for_runtime_and_welcome`) to assert the empty-string preamble.
- `d243d1a` silently reverted that decision by bundling the stale working-tree hunk.

The Finding G commit (`6955ad5`) restores `a603026`'s design: `build_telegram_surface_identity_preamble` returns `""` for all non-`approval_welcome` surfaces, with an explanatory comment referencing both `a603026` and `d243d1a`. The three `test_agent_identity_contracts` tests that asserted the empty preamble now pass again.

**Seventh default-name leak discovered:** while chasing down `test_first_post_approval_dm_runs_multi_turn_agent_onboarding`, I found that `_apply_post_approval_welcome` in `adapters/telegram/runtime.py` had `agent_name=agent_name or "Spark Intelligence"` as a fallback when calling the preamble builder. This was not in the audit's original 5-site list. It has been fixed in `6955ad5` by passing `agent_name` through as-is; the preamble builder handles the empty-name case with a name-free "Pairing approved. Let's set up your agent." welcome.

**Lesson:** When Phase 1 lists an "in-flight fix" to bundle, verify it against `git log` for the affected function before committing. A working-tree diff is not automatically a forward patch; it may be a half-finished revert of a recent operator decision.

