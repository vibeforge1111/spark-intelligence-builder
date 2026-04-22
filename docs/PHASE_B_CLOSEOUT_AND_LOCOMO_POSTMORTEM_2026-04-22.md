# Phase B Closeout And LoCoMo Postmortem 2026-04-22

## Phase B Status

Phase B is closed on the live Spark side.

- Belief-recall regression slice:
  - artifact: `C:\Users\USER\.spark-intelligence\artifacts\phase-b-targeted\regression-belief-sequence-v4`
  - result: `7/7`
- Abstention guardrail packs:
  - artifact: `C:\Users\USER\.spark-intelligence\artifacts\phase-b-targeted\regression-abstention-packs-v1`
  - result: `14/14`
- Builder checkpoint:
  - commit: `195eb28`
  - message: `Close Phase B belief recall and abstention routing gaps`

## LoCoMo Headline

The external `LoCoMo` result from Phase A looked like a generic architecture weakness:

- `summary_synthesis_memory`: `173/1986`
- `dual_store_event_calendar_hybrid`: `163/1986`

That raw read is incomplete.

The real picture is:

1. The public `locomo10.json` surface is partially contaminated for full-lane scoring.
2. The current substrate is heavily overfit to the old closed `conv-26` lane.
3. On unseen conversations, retrieval falls back to aggregate chatter instead of exact evidence.

## Root Causes

### 1. Category-5 Contamination In The Public `locomo10` Surface

The raw dataset at:

- `C:\Users\USER\Desktop\domain-chip-memory\benchmark_data\official\LoCoMo\data\locomo10.json`

contains `444` QA rows with no `answer` field at all.

Measured breakdown:

- total LoCoMo questions: `1986`
- missing-answer questions: `444`
- category `5`: `446` total, `444` missing-answer

Our current adapter normalizes those rows into `expected_answers: []`, and the scorecard still counts them in `overall`.

That means the reported `173/1986` includes a large block of guaranteed false rows that are not honest substrate misses.

Adjusted scored-only view:

- scored subset: `1542`
- correct: `173`
- adjusted accuracy: `11.22%`

This is still weak, but it is materially different from `8.71%`.

### 2. The Existing LoCoMo Lane Does Not Generalize Beyond `conv-26`

The repo historically closed:

- `conv-26 q1-150`
- `conv-30 q1-25`

and a lot of the current tests and heuristics were built around that lane.

Adjusted by-sample accuracy on answer-present rows:

- `conv-26`: `119/154` (`77.27%`)
- `conv-30`: `31/81` (`38.27%`)
- `conv-41`: `5/152` (`3.29%`)
- `conv-42`: `3/199` (`1.51%`)
- `conv-43`: `4/178` (`2.25%`)
- `conv-44`: `2/123` (`1.63%`)
- `conv-47`: `1/150` (`0.67%`)
- `conv-48`: `3/191` (`1.57%`)
- `conv-49`: `1/156` (`0.64%`)
- `conv-50`: `4/158` (`2.53%`)

So this is not a mild generalization drop. It is a lane collapse outside the previously hand-closed slice.

### 3. Retrieval Is Aggregate-First Instead Of Evidence-First

In the full `summary_synthesis_memory` LoCoMo artifact:

- almost every scored prediction uses `primary_retrieved_memory_role = aggregate`
- most scored predictions use `primary_answer_candidate_source = aggregate_memory`

That is the wrong default for many LoCoMo questions, especially:

- category `1` identity/fact questions
- category `2` temporal questions
- category `3` inference questions that still need exact supporting spans

On failed unseen-conversation examples like `conv-41`, answers drift into semantically related but wrong conversational residue:

- dinner question answered with gym-friends chatter
- martial-arts question answered with sunset chatter
- volunteering question answered with a generic shelter/faith snippet

That is not a provider polish issue. It is a retrieval and answer-candidate selection problem.

### 4. The Current LoCoMo Support Is Benchmark-Shaped

The substrate has many LoCoMo-specific phrase rules and answer rescues concentrated around the old closed lane.

The strongest evidence is in:

- `C:\Users\USER\Desktop\domain-chip-memory\src\domain_chip_memory\memory_observation_scoring_rules.py`
- `C:\Users\USER\Desktop\domain-chip-memory\tests\test_providers.py`
- `C:\Users\USER\Desktop\domain-chip-memory\tests\test_memory_systems.py`

These files contain many question- or phrase-shaped boosts around themes like:

- pottery workshop
- camping in June/July
- books, symbols, instruments, artists
- specific Caroline/Melanie answer spans

That helped close the old lane, but it did not produce a general LoCoMo retrieval substrate for unseen speaker pairs and unseen topic clusters.

## What To Improve Next

### 1. Split Honest Scoring From Audit-Only Rows

Do not keep treating the current full `1986` as one clean score.

Add one of these:

- a scored `LoCoMo` lane that excludes rows with missing gold answers
- or a broader benchmark-issue registry for missing-answer rows so audited reporting is honest

Keep the raw full lane as an audit artifact, not the primary optimization target.

### 2. Add Unseen-Conversation LoCoMo Gates

Stop using `conv-26` as the effective main training loop.

Create explicit regression packs from answer-present slices in:

- `conv-41`
- `conv-42`
- `conv-47`
- `conv-50`

The next mutation should only count as a real LoCoMo improvement if it raises at least one unseen conversation without breaking the closed `conv-26` lane.

### 3. Make LoCoMo Retrieval Evidence-First For Categories `1/2/3`

For LoCoMo:

- prefer exact evidence and current-state spans before aggregate synthesis
- demote `aggregate_memory` to support unless evidence recall is genuinely empty
- track evidence-turn hit rate per question, not only final correctness

If the primary retrieved role is still aggregate on most unseen-conversation fact questions, the substrate is still pointed the wrong way.

### 4. Add Relation Expansion Across Two-Speaker Conversation Graphs

The benchmark registry already points in this direction:

- `src/domain_chip_memory/benchmark_registry.py`
- mutation: `mem-004`
- rationale: `relation_expansion_combo`

That is the right next substrate move for LoCoMo:

- speaker-to-speaker linkage
- entity-to-activity linkage
- event-to-follow-up linkage across sessions
- multi-hop retrieval that can join two evidence spans before answer selection

### 5. Replace Phrase Rescue With Typed Cross-Conversation Extraction

The current question scoring still depends too much on benchmark-shaped lexical boosts.

The next wave should favor:

- typed entity facts
- typed event relations
- typed family/social-role links
- generic temporal anchors
- generic cross-session recurrence links

The goal is not to add more `conv-26` phrases. The goal is to make `conv-41` through `conv-50` retrievable without custom per-question rescue.

### 6. Add Better LoCoMo Diagnostics

Before another mutation loop, add artifact-side slices for:

- per-sample accuracy on answer-present rows
- missing-gold count
- primary retrieved role distribution
- primary answer-candidate source distribution
- evidence-hit vs aggregate-hit counts

Right now the raw top-line `173/1986` hides too much.

## Recommended Next Execution Order

1. Make audited LoCoMo reporting honest by separating missing-gold rows from scored rows.
2. Build an unseen-conversation regression pack from answer-present `conv-41+` slices.
3. Change LoCoMo packet assembly so category `1/2/3` questions prefer evidence-first answer candidates over aggregate-first synthesis.
4. Only after that, start a relation-expansion mutation for multi-hop LoCoMo retrieval.

## Bottom Line

LoCoMo is weak for two different reasons at once:

- the public full lane is partially contaminated
- the current substrate does not generalize beyond the hand-closed `conv-26` path

So the next honest goal is not "make `173/1986` look bigger."

It is:

- clean the benchmark accounting
- prove transfer on unseen conversations
- move LoCoMo from aggregate-first chatter retrieval to evidence-first relational retrieval
