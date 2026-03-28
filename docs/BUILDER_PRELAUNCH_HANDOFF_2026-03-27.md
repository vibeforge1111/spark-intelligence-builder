# Builder Prelaunch Handoff 2026-03-27

## Current State

This repo has been hardened against the main prelaunch doctrine gaps without changing the existing Builder posture:

- no custom daemon manager
- native autostart posture remains intact
- operator-driven maintenance remains on `jobs tick`
- Builder core owns ingress, delivery, identity, runtime gating, recovery, and typed truth surfaces
- deeper self-observer reasoning remains an invokeable chip, not a monolithic always-on core

## Major Landed Batches

Recently landed commits on `main`:

- `5b5e985` `Add tranche 2 memory lane promotion records`
- `130e7c9` `Add tranche 2 policy gate block surfaces`
- `f872d83` `Add tranche 2 contradiction registry surfaces`
- `ffa415c` `Add tranche 2 watchtower health surfaces`
- `f04376e` `Classify bridge outputs for keepability governance`
- `94f23b7` `Harden local bridge persistence boundaries`
- `d01ddf0` `Record governed webhook delivery lineage`
- `2570a9d` `Track webhook request runs through bridge execution`

Earlier prelaunch hardening in this stream also landed:

- config mutation audit and rollback metadata
- execution lineage ledger and typed run registry
- jobs/background closure tracking
- typed attachment/personality mirrors instead of hidden generic state
- governed direct-provider and subprocess execution paths
- source-scan stop-ship checks for unguided external execution
- stronger structural secret-boundary heuristics
- operational residue quarantine and reply cleanup

## What Is Structurally Safer Now

- Canonical config writes are audited with actor, reason code, before/after summary, and rollback reference.
- Researcher bridge outputs now carry explicit `output_keepability` and `promotion_disposition`.
- Telegram, Discord, and WhatsApp delivery paths now emit typed delivery evidence instead of relying only on traces or outbound JSON bodies.
- Discord and WhatsApp webhook requests now open and close typed runs, and the shared simulated bridge path threads the same `run_id` through intent, dispatch, result, delivery, and closure.
- Local persistence no longer stores raw bridge failure reply text as durable runtime residue; it stores sanitized operator-status summaries instead.
- Stop-ship checks now fail on:
  - intent without dispatch/result proof
  - missing background closure
  - hidden critical truth in generic `runtime_state`
  - missing provenance or keepability
  - promotable ephemeral/debug bridge outputs
  - classified artifacts missing typed memory-lane records
  - classified artifacts stored in the wrong memory lane
  - raw bridge residue persisted locally
  - unguided external execution call sites
  - raw bridge reply consumption outside immediate delivery surfaces

## What Is Still Compensating Only

- Secret detection is still heuristic, even though it now includes structural patterns beyond simple token matching.
- Raw JSONL traces still exist as investigative evidence beside the typed ledger.
- Some compatibility mirrors still exist in `runtime_state` for older callers.

## What Remains Outside This Repo's Proof Boundary

- Builder still does not own the downstream memory chip.
- Builder now labels and locally guards non-promotable material, but it cannot prove a separate memory domain will honor those same classes until that domain exposes a compatible contract.

## Best Next Work Items

If continuing inside this repo, the next highest-value items are:

1. Add a reset-sensitive state registry so Builder can prove reset actually clears the right session-scoped state.
2. Add a resume richness guard so sparse or degraded state cannot overwrite richer preserved history.
3. Expand promotion-gate coverage beyond the currently classified bridge and influence surfaces.
4. If a downstream memory surface is added later, require an explicit Builder-facing contract that rejects `ephemeral_context` and `operator_debug_only` material by default.

## Tranche Framing

- Tranche 1: shipped core truth surfaces and ledgers
- Tranche 2: partially shipped provenance, policy, Watchtower, contradiction, and memory-lane work
- De facto Tranche 3:
  historical status on 2026-03-27 was a deferred self-observer packet layer over normalized Builder facts
  superseded on 2026-03-28 by the shipped Builder-core packet family documented in `TRANCHE_COMPLETION_PACKAGE_2026-03-28.md`

## Working Tree Note

At the time of this handoff, there is an unrelated untracked file in the repo root:

- `PERSONALITY_HANDOFF_2026-03-26.md`

It was intentionally left untouched.
