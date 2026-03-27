# Builder Prelaunch Truth Surfaces Note 2026-03-27

## Purpose

This note maps the prelaunch doctrine into the Builder code that now exists in this repo.

It does not replace the wider architecture docs.

It explains what Builder core now owns, what remains delegated to the self-observer chip, and which launch blockers are still real.

## What Builder Core Owns

Builder core keeps owning:

- ingress and delivery through the gateway/runtime boundary
- identity, pairing, session continuity, and operator recovery
- provider/runtime gating and fail-closed startup
- typed fact capture for config mutations, run lineage, delivery attempts, environment snapshots, and quarantine events
- stop-ship checks that fail on missing proof, closure drift, environment drift, secret-boundary mishandling, and daemon-like recovery re-entry

Builder core does **not** own diagnosis or repair reasoning.

## What The Self-Observer Chip Owns

The self-observer chip should stay an invokeable chip.

It should own:

- incident diagnosis
- contradiction analysis
- repair-plan drafting
- unsafe-repair classification
- reflection and retrospective reasoning

The chip should reason over Builder fact packets.
It should not become the always-on runtime authority that decides or mutates canonical truth by itself.

## Structural Changes Landed

The repo now has typed Builder-core truth surfaces for:

- `config_mutation_requested`, `config_mutation_applied`, `config_mutation_rejected`
- `runtime_environment_snapshot`
- `run_opened`, `run_closed`, `run_stalled`
- `intent_committed`
- `dispatch_started`, `dispatch_failed`
- `tool_result_received`
- `delivery_attempted`, `delivery_succeeded`, `delivery_failed`
- `plugin_or_chip_influence_recorded`
- `secret_boundary_violation`
- `quarantine_recorded`
- `contradiction_recorded`

These live in SQLite, not only JSONL logs.

The new typed storage is:

- `builder_runs`
- `builder_events`
- `config_mutation_audit`
- `runtime_environment_snapshots`
- `attachment_state_snapshots`
- `personality_trait_profiles`
- `personality_observations`
- `personality_evolution_events`
- `quarantine_records`

## Structural Versus Compensating

Structural now:

- config mutations emit audit rows with actor, reason code, target path, before/after summaries, and rollback references
- Telegram live-path processing now opens and closes typed runs and records intent, dispatch, result, and delivery stages
- `jobs tick` now records real run identity and closure instead of only last-run hints
- chip and attachment influence now emits provenance-bearing events with keepability classification
- personality-driven reply shaping now emits provenance-bearing influence events instead of mutating tone silently
- Swarm sync, decision, and auth-refresh outcomes now emit typed event mirrors instead of depending only on `runtime_state`
- attachment snapshot truth now lands in `attachment_state_snapshots`; `runtime_state` is only a compatibility mirror for snapshot hints
- personality preference, observation, and evolution truth now lands in dedicated personality tables instead of generic `runtime_state` keys
- secret-like chip/output material can be quarantined before model-visible prompt assembly
- the final assembled bridge prompt is now screened before any Researcher or direct-provider execution
- direct-provider execution now supports a lower-layer governed prompt contract, and the external provider wrapper uses it by default when Builder passes run/request context
- external subprocess-style tool ingress now has shared governed execution helpers for typed result recording and secret-boundary screening instead of caller-specific glue only
- stop-ship checks now scan source call sites and fail if new raw `subprocess.run(...)` or direct-provider execution entry points appear outside the governed modules
- secret-boundary detection now layers structural heuristics on top of token-family matching, including auth headers, suspicious-key assignments, embedded URL credentials, and credential-shaped high-entropy values
- operator-triggered `attachments run-hook` executions now emit typed provenance and block secret-like hook output before terminal display
- operator-triggered `attachments run-hook` executions now open and close typed runs instead of existing only as CLI residue
- chip-hook provenance and screening now live in shared hook-layer helpers instead of only in caller-specific glue
- outbound secret-like replies now emit typed violation and quarantine records instead of only trace-side evidence
- doctor and operator security now expose explicit stop-ship failures

Still compensating:

- raw JSONL traces remain useful investigative evidence, but they are no longer the only lineage surface
- some legacy `runtime_state` keys still exist as cache or compatibility state, including attachment and personality mirrors kept for older callers
- outbound secret blocking still relies partly on pattern detection; it is stronger than before, but not a full content-security proof system

## What Remains Blocked Before Launch

Still unsafe or incomplete before launch:

- pre-model trust-boundary policy is still narrower than the doctrine wants; the repo now screens chip guidance, but not every future tool-result or attachment pathway
- JSONL traces are still kept for ops visibility, so operators can still confuse raw evidence with canonical lineage if docs and surfaces drift
- no part of this change authorizes broad auto-repair; repair remains bounded and operator-mediated

## Repo-Specific Phase Order

1. Builder core fact surfaces first: config audit, run registry, event ledger, quarantine, environment snapshots.
2. Gateway and bridge lineage second: interactive path proof from intent through delivery.
3. Background integrity third: `jobs tick` closure, stalled-run checks, parity checks.
4. Self-observer integration fourth: chip consumes fact packets and emits diagnosis, contradiction, and repair packets.
5. Optional compensating controls last: rollback automation or watchdog-style recovery only after the core truth surfaces stay clean.

## Final Rule

If a capability must always exist for launch safety, it belongs in Builder core as a typed fact surface.

If a capability depends on interpretation, contradiction management, or repair reasoning, it belongs in the self-observer chip.
