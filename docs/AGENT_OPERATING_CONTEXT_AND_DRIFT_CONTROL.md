# Agent Operating Context and Drift Control

This document captures the AOC v2 foundation shipped for Spark's agent drift bugs.

## Problem

Spark drifted when retrieved context, route templates, or mission-control status became louder than the latest user turn. The fix treats drift as a state-routing bug:

- classify the latest user intent before action
- make source hierarchy executable
- gate route-changing actions
- record why the agent chose a path
- expose the shared state in AOC instead of hiding it in agent prose

## Source Hierarchy

Use this order when sources conflict:

1. current user message
2. operator override
3. operator-supplied access
4. current diagnostics
5. runner preflight
6. live probe
7. approved memory
8. raw memory
9. wiki doctrine
10. mission trace
11. raw chat history
12. inference

Wiki doctrine guides behavior, but it does not override live state. Mission trace is evidence, not instruction.

## Runtime Pieces

- `conversation_frame.py`: task intent, current mode, allowed actions, disallowed actions, option-reference resolution, action gate, final-answer drift check.
- `agent_events.py`: shared event model and black-box report over `builder_events`.
- `turn_recorder.py`: records a meaningful turn as frame, action gate, drift check, and optional memory candidate events.
- `source_hierarchy.py`: resolves conflicting claims by authority and freshness.
- `stale_context_sweeper.py`: turns source conflicts into stale/contradicted review items with typed review actions.
- `approval_inbox.py`: shows approval-gated memory candidates; it does not write memory by itself.
- `route_confidence.py`: explains recommended route confidence and evidence.
- `agent_scratchpad.py`: structured operational scratchpad derived from AOC; not hidden chain-of-thought.
- `operating_strip.py`: compact top-strip renderer for always-visible AOC status.
- `operating_source_ledger.py`: unified source freshness ledger for panel consumers.
- `operating_panel_sections.py`: drilldown section contract for permissions, runner capability, route health, task fit, sources, contradictions, and agent instruction.
- `operating_panel.py`: shared read-model joining AOC, scratchpad, black box, memory inbox, and stale sweeper.
- `drift_evals.py`: regression suite for the drift cases from the AOC design thread.

## Design Rules

- AOC is a shared read-model, not a new brain.
- The scratchpad is derived from AOC, not separately authored.
- Stale context is shown and typed; do not silently reconcile it.
- Source freshness is rendered explicitly; do not make agents infer freshness from prose.
- Memory candidates go to the approval inbox; do not auto-save raw logs.
- Route-changing actions must pass the conversation action gate.
- Final answers should be checked against the latest user turn before sending.

## Key Drift Evals

The default eval suite covers:

- concept question must not become diagnostics
- "you drifted" resets to latest chat context
- Mission Control mentions do not authorize opening Mission Control
- "option 2" resolves against the latest active list
- old access memory loses to current diagnostics
- Level 4 access plus read-only runner recommends writable Spawner/Codex without saying access failed

Run the focused checks:

```powershell
python -m pytest tests\test_agent_drift_evals.py tests\test_agent_scratchpad.py tests\test_agent_operating_panel.py tests\test_stale_context_sweeper.py -q
```

Run the broader AOC foundation checks:

```powershell
python -m pytest tests\test_conversation_operating_frame.py tests\test_agent_events.py tests\test_memory_approval_inbox.py tests\test_route_confidence.py tests\test_source_hierarchy.py tests\test_agent_operating_summary.py tests\test_stale_context_sweeper.py tests\test_agent_operating_panel.py tests\test_agent_operating_panel_cli.py tests\test_agent_turn_recorder.py tests\test_agent_drift_evals.py tests\test_agent_scratchpad.py -q
```

## Extension Path

Next integrations should consume the existing read-models:

- Telegram should render a compact AOC strip from `operating_panel.py`.
- Mission Control should drill into the same panel payload, not rebuild route state.
- Memory Dashboard should read typed stale actions and approval inbox items.
- Diagnostics should emit `capability_probed` and `contradiction_found` events.
- Spawner/Codex should record `route_selected`, `mission_changed_state`, and turn-trace events.

Rollback is simple: callers can stop rendering the panel while the underlying event and source contracts remain append-only evidence.
