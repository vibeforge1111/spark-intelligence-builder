# Builder Post-Tranche1 Roadmap

Prepared: 2026-03-27

Purpose:

- preserve the next tranche surfaces after `573d729`
- keep the roadmap anchored to the source docs rather than memory

## Tranche 1 Landed

Shipped in Builder core:

- typed ledgers for events, runs, delivery, config mutations, and provenance mutations
- config mutation audit with rollback metadata and no-op suppression
- run lifecycle truth for gateway/job paths
- typed delivery truth instead of generated-output ambiguity
- bridge status derived from typed truth before `runtime_state`
- doctor and operator surfaces reading typed truth first

Primary commit:

- `573d729` `Implement tranche 1 truth-surface ledgers`

## Next Tranche Family

Source anchor:

- [BUILDER_PACKET_AND_EVENT_SCHEMA_SPEC.md](<workspace>/spark-domain-chip-labs/docs/BUILDER_PACKET_AND_EVENT_SCHEMA_SPEC.md)

Explicit `Second tranche` items:

- provenance and policy events
- quarantine and contradiction events

## Tranche 2 Progress

Already shipped in Builder core:

- `ffa415c` `Add tranche 2 watchtower health surfaces`
- `f872d83` `Add tranche 2 contradiction registry surfaces`
- `130e7c9` `Add tranche 2 policy gate block surfaces`
- `5b5e985` `Add tranche 2 memory lane promotion records`

What those commits cover:

- typed Watchtower health dimensions and panel snapshots
- typed contradiction accumulation and resolution
- typed `policy_gate_blocked` storage and operator or Watchtower surfacing
- typed memory-lane records for classified bridge and influence artifacts

What is still tranche 2 and intentionally left for later:

- reset-sensitive state registry
- resume richness guard
- broader promotion-gate enforcement on more persistence surfaces
- observer contamination detection beyond current lane and residue checks

## Provenance And Policy Expansion

Source anchor:

- [SPARK_PROVENANCE_AND_MUTATION_LEDGER_DOCTRINE.md](<workspace>/spark-domain-chip-labs/docs/SPARK_PROVENANCE_AND_MUTATION_LEDGER_DOCTRINE.md)

Carry forward:

- provenance tags on delivery surfaces
- provenance tags on Watchtower/operator surfaces
- quarantine path for unsafe sources
- observer classification on repeated provenance incidents
- fail closed when provenance is missing on high-risk mutation
- preserve raw and mutated refs separately when safe

Shipped so far:

- typed policy gate records for secret-boundary and missing-provenance blocks
- Watchtower/operator counts for policy blocks
- contradiction tracking for repeated stop-ship failures

Still open:

- broader policy-gate coverage beyond the current secret-boundary and provenance-missing paths
- clearer raw-vs-mutated references on more high-risk mutation paths

## Memory-Lane Enforcement

Source anchor:

- [MEMORY_LANE_SEPARATION_AND_PROMOTION_POLICY.md](<workspace>/spark-domain-chip-labs/docs/MEMORY_LANE_SEPARATION_AND_PROMOTION_POLICY.md)

Carry forward:

- lane labels on persisted artifacts
- promotion gates:
  - provenance check
  - keepability check
  - contradiction check
  - secrecy check
  - residue check
- reset-sensitive state registry
- resume richness guard
- observer contamination detection

Shipped so far:

- typed memory-lane records for classified bridge outputs and operational influence
- stop-ship failure when classified artifacts lack typed lane records
- stop-ship failure when classified artifacts land in the wrong lane

Still open:

- reset-sensitive state registry
- resume richness guard
- observer contamination detection
- broader lane labeling outside the currently classified bridge and influence surfaces

Core rule to preserve:

- `user_history`, `ops_transcripts`, `execution_evidence`, and `durable_intelligence_memory` stay separate

## Silent-Failure And Watchtower Expansion

Source anchor:

- [SILENT_FAILURE_HEALTH_MODEL_FOR_BUILDER_AND_WATCHTOWER.md](<workspace>/spark-domain-chip-labs/docs/SILENT_FAILURE_HEALTH_MODEL_FOR_BUILDER_AND_WATCHTOWER.md)
- [BUILDER_WATCHTOWER_PANEL_SPEC.md](<workspace>/spark-domain-chip-labs/docs/BUILDER_WATCHTOWER_PANEL_SPEC.md)

Carry forward:

- health fact emission for:
  - ingress
  - execution
  - delivery
  - scheduler freshness
  - environment parity
- explicit health states instead of process-alive heuristics
- Watchtower/operator panels for:
  - delivery truth
  - execution truth
  - background freshness
  - environment parity
- stall and freshness thresholds
- parity comparison surfaces
- observer incident classification

Shipped so far:

- typed Watchtower health dimensions for ingress, execution, delivery, scheduler freshness, and environment parity
- typed Watchtower contradiction and policy-block visibility

Still open:

- richer observer incident classification beyond the current Builder-core fact panels

## De Facto Tranche 3: Self-Observer Packet Layer

Source anchor:

- [SELF_OBSERVER_PACKET_CONTRACT.md](<workspace>/spark-domain-chip-labs/docs/SELF_OBSERVER_PACKET_CONTRACT.md)
- [SELF_OBSERVER_HYBRID_BOUNDARY.md](<workspace>/spark-domain-chip-labs/docs/SELF_OBSERVER_HYBRID_BOUNDARY.md)

Carry forward:

- Builder core keeps emitting normalized fact events
- self-observer chip emits separate packets:
  - `self_observation`
  - `incident_report`
  - `repair_plan`
  - `security_advisory`
  - `reflection_digest`
  - contradiction objects
- Swarm and Researcher consume normalized packets, not raw reflections
- contradiction lineage stays first-class

Status:

- historical status on 2026-03-27: not started in Builder core
- superseded on 2026-03-28: Builder core now emits the bounded packet family over typed observer incidents
- remaining external follow-on is a separately replaceable self-observer chip runtime, not a missing Builder-core packet surface

## Stop-Ship Items Still Relevant After Tranche 1

Source anchor:

- [STOP_SHIP_REGISTRY_SPARK_PRELAUNCH.md](<workspace>/spark-domain-chip-labs/docs/STOP_SHIP_REGISTRY_SPARK_PRELAUNCH.md)

Still important for later tranches:

- STOP-004 session integrity and resume safety
- STOP-006 secret boundary safety beyond the current heuristic detector
- STOP-007 plugin and chip provenance on all high-risk mutation paths
- STOP-008 ops residue memory contamination
- STOP-010 watchdog dependency

## Working Rule

Do not treat this note as a new doctrine.

It is only a source-anchored reminder of what remains after tranche 1 so future prompts can pick up the next tranche cleanly.
