# Tranche Completion Package

Date: 2026-03-28

Purpose:

- capture the concrete tranche-followup work shipped in this pass
- separate newly closed items from still-deferred doctrine work

## Shipped In This Package

### Reset-sensitive state registry

Added typed registry surfaces for reset-sensitive runtime-state keys.

What now exists:

- `reset_sensitive_state_registry`
- `session_reset_performed` fact events for personality resets
- personality reset clears all registered personality mirrors, not just the trait-delta key

This closes the immediate reset-integrity gap where personality reset could leave observation or evolution mirrors behind.

### Resume richness guard

Added typed guard records for sparse-overwrite protection on resume-like runtime-state writes.

What now exists:

- `resume_richness_guard_records`
- guarded JSON-object runtime-state writes that preserve richer existing fields when a sparser write arrives later
- guard coverage on:
  - pairing context
  - Telegram auth and poll state
  - Researcher attachment and failure state mirrors
  - Swarm sync, decision, and failure state mirrors

This implements the current Builder-side version of the `preserve richer artifact` rule for the existing runtime-state mirrors in this repo.

### Promotion-gate expansion

Promotion candidates no longer rely only on lane labels.

Follow-on policy-gate blocks now emit for promotion candidates when they fail:

- provenance check
- keepability check
- residue/lane check
- contradiction check

This widens promotion-gate enforcement beyond the prior lane-record-only posture.

### Watchtower and stop-ship surfaces

Added explicit Builder-core visibility for the new tranche surfaces:

- Watchtower `session_integrity` panel
- Watchtower `observer_incidents` panel
- Watchtower `observer_packets` panel
- Watchtower `memory_shadow` contract counters
- stop-ship `stop_ship_reset_integrity`
- stop-ship `stop_ship_memory_contract`
- raw-vs-mutated text refs on guarded bridge and delivery mutation paths

The panel now shows:

- registered reset-sensitive keys
- active vs cleared reset-sensitive keys
- resume richness guard interventions
- recent reset events

The observer panel now classifies:

- provenance contamination
- promotion contamination
- memory contract drift
- session integrity incidents
- residue contamination
- resume risk intercepted by guardrails

These classifications are also surfaced through operator security and doctor output.

High-risk bridge and delivery rewrites now also preserve raw-vs-mutated text refs when Builder changes output before delivery. Stop-ship now fails if a mutated classified event omits those refs.

Builder core now also emits a bounded observer packet layer from typed observer incidents. The packet family stays on the core side of the hybrid boundary, remains evidence-backed and proposal-oriented, and is consumable by Watchtower, operator summaries, or a later observer chip without treating free-form diagnosis as settled truth.

### Observer packet family completion

Expanded the observer packet layer from `self_observation` only into the broader packet family named in the tranche roadmap.

What now exists:

- `self_observation`
- `incident_report`
- `repair_plan`
- `security_advisory`
- `reflection_digest`

What the Builder-side packet layer now does:

- derives packet-family objects from typed observer incidents without depending on raw reflection text
- keeps diagnosis and recommendation content bounded, evidence-backed, and explicitly proposal-oriented
- surfaces packet kind mix in Watchtower and doctor output
- exposes richer operator summaries for packet kinds beyond `self_observation`

This closes the remaining repo-local tranche item around the self-observer packet family. The packet content remains bounded by Builder-core evidence rather than free-form diagnosis.

### Observer packet persistence and handoff export

Persisted the Builder-side observer packet family as first-class typed records and added direct operator access to the packet ledger.

What now exists:

- `observer_packet_records`
- active vs archived packet tracking keyed by stable `packet_id`
- persisted source refs for incident class, item ref, and source ref
- `spark-intelligence operator observer-packets`
- `spark-intelligence operator export-observer-packets`
- JSON handoff bundles written for external self-observer consumption
- operator audit history entries for packet exports

What the Builder-side packet ledger now does:

- records the current bounded packet family as stable typed rows instead of requiring packet reconstruction for every operator read
- keeps packet content, evidence refs, related event ids, related packet ids, and contradiction ids in the typed ledger
- archives no-longer-active packet rows instead of silently dropping packet history
- gives operators a direct packet inspection surface outside Watchtower and summary text
- emits a portable bundle that an external self-observer runtime can ingest without scraping Builder internals

This closes the remaining repo-local follow-on work around packet persistence, direct packet inspection, and Builder-side handoff packaging for a replaceable external observer runtime.

### Observer chip handoff runtime

Added a Builder-side runtime path that hands bounded observer packets to an invokeable chip `packets` hook and records the exchange as typed Builder evidence.

What now exists:

- `observer_handoff_records`
- `spark-intelligence operator handoff-observer`
- `spark-intelligence operator observer-handoffs`
- per-handoff bundle and result artifacts under `artifacts/observer-handoffs/`
- typed operator audit history for chip handoff attempts
- secret-boundary screening on observer chip output before operator display

What the Builder-side handoff runtime now does:

- builds a bounded `spark-observer-handoff.v1` payload over the persisted observer packet bundle
- includes focused Watchtower and attachment state context for the chip without turning the chip into runtime authority
- invokes the active chip or an explicit chip key through the existing `spark-hook-io.v1` `packets` hook contract
- records completed, failed, blocked, or stalled handoff attempts in a typed ledger instead of leaving them only in generic traces
- writes a result artifact only when the chip output passes secret-boundary screening
- surfaces problematic handoff attempts through Watchtower, operator security/inbox counts, and doctor checks

This closes the Builder-side runtime half of the self-observer handoff doctrine. The external chip implementation still lives outside this repo, but Builder now has a bounded, typed, operator-visible way to hand observer packets to it.

### Builder memory contract enforcement

Added a Builder-local contract layer around downstream memory read and write roles.

What now exists:

- shared allowed-role contract helpers for `current_state` and `event`
- fail-closed normalization for explicit invalid downstream memory roles on Builder reads and writes
- Watchtower and doctor visibility for memory-contract violations
- stop-ship failure when memory events violate the Builder role contract
- shadow replay filtering that omits accepted observations carrying invalid memory-role or operation-role combinations

This closes the Builder-side piece of the previously deferred memory-domain contract gap. Builder now refuses to normalize explicit downstream role drift into usable current-state or event memory.

## Verification

Validated in repo with:

- `python -m pytest tests/test_builder_prelaunch_contracts.py tests/test_memory_orchestrator.py tests/test_operator_pairing_flows.py tests/test_cli_smoke.py tests/test_attachment_hooks.py tests/test_gateway_discord_webhook.py tests/test_gateway_whatsapp_webhook.py`

Result:

- `185 passed`

## Still Deferred

This package does not claim to finish all possible observer doctrine work.

No additional tranche followup items remain open inside this repo. A separately replaceable self-observer chip runtime would still be an external follow-on, not a missing Builder-core tranche surface.

## Working Rule

Treat this as the shipped package note for the 2026-03-28 tranche followup pass.

It is not a replacement for the doctrine source docs.
