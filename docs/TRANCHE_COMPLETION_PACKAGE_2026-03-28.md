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
- stop-ship `stop_ship_reset_integrity`

The panel now shows:

- registered reset-sensitive keys
- active vs cleared reset-sensitive keys
- resume richness guard interventions
- recent reset events

The observer panel now classifies:

- provenance contamination
- promotion contamination
- session integrity incidents
- residue contamination
- resume risk intercepted by guardrails

These classifications are also surfaced through operator security and doctor output.

## Verification

Validated in repo with:

- `python -m pytest tests/test_memory_orchestrator.py tests/test_operator_pairing_flows.py tests/test_builder_prelaunch_contracts.py`

Result:

- `69 passed`

## Still Deferred

This package does not claim to finish all remaining doctrine items.

Still intentionally deferred:

- broader raw-vs-mutated reference preservation on more high-risk mutation paths
- downstream memory-domain contract enforcement outside this repo
- self-observer packet layer / de facto tranche 3

## Working Rule

Treat this as the shipped package note for the 2026-03-28 tranche followup pass.

It is not a replacement for the doctrine source docs.
