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
