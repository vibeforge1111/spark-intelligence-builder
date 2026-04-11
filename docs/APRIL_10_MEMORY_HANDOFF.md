# April 10 Memory Handoff

Date: 2026-04-10
Scope: `spark-intelligence-builder` plus its active `domain-chip-memory` dependency
Status: canonical cross-repo handoff for the April 10 memory push

## Executive Summary

The April 10 memory work did not rebuild the memory architecture from scratch.

The real achievement was productizing an already-working memory substrate into a
Builder-owned Telegram flow with:

- direct Telegram profile-fact writes into Domain Chip Memory
- direct fact-query and explanation-query reads from memory
- Builder `state.db` intake into the chip KB/wiki compiler
- a repeatable Telegram regression runner
- KB bundles that carry runtime evidence plus repo-source context

The practical split was:

- `domain-chip-memory` carried the memory substrate, Builder `state.db` intake,
  and the founder/startup/hack/rebuild fact lane
- `spark-intelligence-builder` carried the live Telegram integration, KB compile
  command, regression runner, explanation routing, and architecture benchmark

## What Shipped In `domain-chip-memory`

Last committed stop on `main`:

- `fd5cfae` on 2026-04-10 18:29 +0400
- subject: `Add probes to Builder state Telegram intake`

That repo also had coherent uncommitted April 10 work adding the founder/startup/
hack/rebuild fact lane:

- `startup_name`
- `founder_of`
- `hack_actor`
- `current_mission`
- `spark_role`

The key chip-side handoff docs are:

- `docs/NEXT_PHASE_SPARK_MEMORY_KB_BENCHMARK_PROGRAM_2026-04-10.md`
- `docs/CURRENT_STATUS_BENCHMARKS_AND_KB_2026-04-09.md`

The chip-side recommendation was:

1. connect real Spark exports into governed memory and the KB
2. make the KB materially useful
3. preserve benchmark lanes as regression gates
4. avoid drifting into architecture brainstorming

## What Shipped In `spark-intelligence-builder`

Builder continued the memory push after the chip repo stopped committing.

Key commits on 2026-04-10:

### 18:15 +0400

- `1186433` `Add Builder Telegram KB compile command`

This added the Builder-owned KB compile path that invokes the chip CLI through
`run-spark-builder-state-telegram-intake`.

Primary files:

- `src/spark_intelligence/memory/knowledge_base.py`
- `tests/test_memory_knowledge_base.py`

### 18:35 to 19:02 +0400

- `8716d31` `Add Telegram memory regression runner`
- `0103bbf` `Expand Telegram memory regression coverage`
- `8e7d982` `Add default repo sources to memory KB compiles`
- `7ddb581` `Expand Telegram regression coverage and KB notes`
- `6aa922d` `Merge regression notes with default KB sources`
- `542d51d` `Expand country overwrite regression coverage`
- `9bf9ea1` `Add filterable Telegram memory regression packs`

This established:

- `spark-intelligence memory run-telegram-regression`
- targeted `--category` and `--case-id` runs
- default repo-source manifests in KB compiles
- overwrite, abstention, explanation, identity, and staleness coverage

Primary files:

- `src/spark_intelligence/memory/regression.py`
- `src/spark_intelligence/memory/knowledge_base.py`
- `src/spark_intelligence/cli.py`

### 22:18 to 23:44 +0400

- `e1e7fca` `Add profile fact explanation routing`
- `84220cf` `Enrich Telegram regression KB notebook sources`
- `672265a` `Expand Telegram regression coverage lanes`
- `43a81f3` `Restore Spark self-knowledge bridge context helper`
- `911a6b2` `Harden profile fact memory query fallbacks`
- `3daca59` `Normalize memory subjects and isolate regression probes`
- `2f9c996` `Add memory architecture benchmark and clean KB regression artifacts`

This extended the rollout from a simple regression harness into:

- explanation-aware answer routing
- richer KB notebook/source pages
- broader regression coverage
- normalized memory subject handling
- memory architecture benchmarking

## Runtime Evidence From April 10

The runtime artifacts show that the Builder integration was exercised for real.

### Pre-regression KB compile

By 18:26 +0400, this Builder home existed:

- `.tmp-home-live-telegram-kb-vault-20260410-03`

Its generated KB already contained current-state pages for:

- preferred name
- occupation
- city
- startup name
- current mission

Its run summary reported:

- 1 conversation run
- 5 accepted writes
- 0 rejected writes
- 0 skipped turns
- probe coverage `10/10` current-state
- probe coverage `10/10` evidence

### Regression runner proving the paired-user path

At 18:45 +0400:

- `.tmp-home-live-telegram-regression-20260410-06-blocked`

This captured the expected blocked case:

- status `blocked_precondition`
- reason: unauthorized DM pending pairing approval

At 18:46 +0400:

- `.tmp-home-live-telegram-regression-20260410-06-authorized`

This rerun went green:

- `20/20` matched
- KB probe coverage enabled

### Full green matrix

By 18:56 +0400:

- `.tmp-home-live-telegram-regression-20260410-10`

This run reported:

- `26/26` matched
- categories:
  - `profile_write`
  - `profile_query`
  - `explanation`
  - `identity`
  - `overwrite`
  - `staleness`
  - `abstention`

The covered fact lane included:

- Sarah
- entrepreneur
- Dubai
- Seedify
- Spark Swarm
- survive the hack and revive the companies
- UAE
- Asia/Dubai
- Abu Dhabi
- Canada

### Overwrite-focused slice with KB bundle

By 19:02 +0400:

- `.tmp-home-live-telegram-regression-overwrite-20260410-11`

This overwrite slice reported:

- `4/4` matched
- `city_overwrite`
- `country_overwrite`
- `country_query_after_overwrite`
- `city_query_after_overwrite`

It also produced a KB bundle with current-state and evidence pages for:

- founder
- startup
- mission
- city
- country
- timezone
- preferred name

### Late-night live homes

Two late-night Builder homes were refreshed again:

- `.tmp-home-live-telegram-recovered`
- `.tmp-home-live-telegram-started-supported-20260410164848`

The started-supported home shows real memory write/read traffic in `state.db`
plus Telegram traces. The recovered home appears to be a recovery attempt and
its `state.db` is currently malformed.

## Best Source Documents

If one person needs to restart this work without re-investigating the whole
session, these are the main docs to read in order:

1. `spark-intelligence-builder/docs/MEMORY_REALTIME_BENCHMARK_PROGRAM_2026-04-11.md`
2. `spark-intelligence-builder/docs/SPARK_MEMORY_KB_ROLLOUT_PLAN_2026-04-10.md`
3. `spark-intelligence-builder/docs/MEMORY_EXECUTION_PLAN_2026-04-10.md`
4. `domain-chip-memory/docs/NEXT_PHASE_SPARK_MEMORY_KB_BENCHMARK_PROGRAM_2026-04-10.md`

What they agree on:

- the memory architecture is already working
- the current gap is coverage, calibration, and product integration
- the KB/wiki path is live and should be enriched, not replaced
- new behavior should only be promoted after live Telegram validation

Current default comparison structure:

1. `summary_synthesis_memory`
2. `dual_store_event_calendar_hybrid`

Those two contenders should now be treated as the default race across offline ProductMemory scorecards plus live Telegram regression and soak validation.

## Honest Status At Stop Point

By the end of April 10:

- the Builder memory write/read path was live
- the Builder KB compile path was live
- the Telegram regression runner was live
- the regression matrix had a green `26/26` run
- overwrite slices had a green focused run and KB bundle
- explanation routing and fallback hardening were in flight and landed
- architecture benchmarking had been added to the Builder side

What was still open:

- broader non-profile memory lanes
- stronger synthesis pages in the KB/wiki
- tighter memory quality gates against noisy promotions
- continued validation on fresh real Builder homes

## Recommended Restart

Do not rebuild the memory system.

Restart from the Builder-owned path:

1. treat the `26/26` regression matrix as the current baseline
2. keep targeted category runs for overwrite, abstention, and explanation lanes
3. keep the execution plan and regression outputs flowing into KB repo sources
4. promote new memory behavior only after live Telegram validation
5. continue from the Builder integration layer, not from a fresh chip redesign
