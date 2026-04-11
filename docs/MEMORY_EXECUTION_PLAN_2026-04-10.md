# Memory Execution Plan 2026-04-10

## Architecture Read

The current memory architecture is working.

We are already using:
- Builder writes of structured Telegram profile facts into the memory SDK.
- Builder reads for direct fact queries and explanation-style queries.
- Builder KB/wiki compilation from `state.db` into a Karpathy-style vault.
- Live Telegram regression replay against real Builder homes.

We are not starting over. The current gap is that the richer benchmarked memory behavior is only partially promoted into Builder, and the regression loop still needs wider runtime coverage.

## Validated Baseline

Latest recovered-home Telegram regression:
- `26/26` matched
- `kb_has_probe_coverage = true`
- `kb_current_state_hits = 84/84`
- `kb_evidence_hits = 84/84`

Latest broad coverage now includes:
- profile writes
- direct fact queries
- explanation queries
- identity summary
- staleness behavior
- overwrite behavior
- abstention behavior

## What Is Already Solid

- The write/read substrate is live in Builder.
- Explanation retrieval is live in Builder.
- The KB/wiki compile path is live and valid.
- Repo-source manifests let the wiki carry runtime artifacts plus checked-in design context.

## What Still Needs Promotion

- More benchmark-style runtime packs beyond the main profile-memory lane.
- Broader non-profile memory questions and evidence-heavy queries.
- Stronger synthesis pages so the wiki reads like an operator notebook, not only a contract artifact.
- Tighter quality gates that reject noisy or low-durability promotions.

## Active Architecture Program

The current default serious comparison loop is:

1. `summary_synthesis_memory`
2. `dual_store_event_calendar_hybrid`

Those two contenders must move together through:

- ProductMemory scorecards from `domain-chip-memory`
- live Telegram regression in Builder
- live Telegram soak packs in Builder

`observational_temporal_memory` remains a useful control baseline, but not the default second contender.

## Execution Tracks

### Track 1: Broaden the regression surface

Ship benchmark-style slices instead of only one monolithic run.

Immediate work:
- keep the full default regression matrix as the always-on baseline
- support targeted `--category` and `--case-id` runs for focused validation
- support targeted `--baseline` runs while keeping the default contender pair stable
- add more non-profile and evidence-sensitive cases after each live green run

Acceptance:
- we can run both the full matrix and narrow packs without editing code
- summary JSON exposes machine-readable lane coverage

### Track 2: Enrich the KB/wiki system

Make the wiki useful as an LLM-readable and operator-readable notebook.

Immediate work:
- keep the default repo-source manifest attached to every compile
- include the current execution plan in the manifest
- keep promoting run summaries and future benchmark artifacts into `wiki/sources`

Acceptance:
- a fresh KB compile includes both runtime evidence and the current operating plan

### Track 3: Promote richer runtime retrieval

Use more of the benchmark-proven read surface where it materially improves answers.

Immediate work:
- keep explanation queries on `explain_answer`
- identify the next narrow non-profile read lane to promote
- validate every promotion against Telegram before widening defaults

Acceptance:
- Builder answers broader memory questions without regressing direct fact stability

### Track 4: Add memory quality gates

Separate durable facts from conversational residue.

Immediate work:
- define stop-ship checks for noisy promotions
- keep overwrite and abstention lanes mandatory in the regression pack
- add contradiction-oriented cases before any wider rollout
- require both offline scorecards and live Telegram packs to remain green before promoting a memory change

Acceptance:
- memory quality improves without inflating long-lived clutter

## Current Recommendation

Continue small, benchmark-backed promotions.

Do not rebuild the memory stack. Keep expanding coverage, feed the execution plan into the wiki, and only promote new runtime behavior after it stays green on both offline ProductMemory scorecards and live Telegram validation on real Builder homes.
