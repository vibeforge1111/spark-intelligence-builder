# Agent Identity Execution Checklist — March 28, 2026

## End Goal

Builder and Spark Swarm converge on one canonical agent per human with:

- stable `agent_id`
- mutable `agent_name`
- Builder-first local creation support
- Swarm-first import support
- deterministic link/repair behavior
- agent-scoped base persona
- human-scoped overlay compatibility
- unit, smoke, and harness coverage

## Phase Checklist

### Phase A. Canonical Agent Storage

Status:

- shipped

Tasks:

- [x] add canonical agent link storage
- [x] add agent profile storage for mutable names and source metadata
- [x] add alias/tombstone mapping for superseded local ids
- [x] keep current pairing/session flow compatible with local derived ids
- [x] add operator-facing identity repair surfaces

Tests:

- [x] contract tests for local canonical creation
- [x] contract tests for Swarm canonicalization and aliasing
- [x] contract tests for rename history
- [x] CLI/operator smoke for identity repair workflow

### Phase B. Agent-Scoped Persona Base

Status:

- shipped

Tasks:

- [x] add agent persona profile storage
- [x] add agent persona mutation history
- [x] merge runtime profile as defaults/chip -> agent base -> human overlay
- [x] expose agent persona in operator personality inspection
- [x] migrate legacy human-only style state into agent base where appropriate

Tests:

- [x] unit coverage for agent base plus human overlay merge
- [x] bridge contract coverage for explicit agent persona authoring
- [x] CLI smoke for operator personality with agent identity attached
- [x] migration harness coverage

### Phase C. Builder-First Agent Creation

Status:

- partially shipped

Tasks:

- [x] treat local Builder identity as canonical until Swarm link exists
- [x] persist agent names separately from human display names
- [x] allow explicit conversational rename via bridge personality path
- [x] allow explicit conversational agent persona authoring via bridge personality path
- [ ] add multi-turn onboarding prompts for first-time Telegram users

Tests:

- [x] harness test for bridge-side agent persona authoring
- [ ] Telegram onboarding conversation test

### Phase D. Swarm Import And Linking

Status:

- Builder-side implementation shipped

Tasks:

- [x] import a Swarm agent id and name into Builder
- [x] canonicalize Builder-local identity to Swarm when local identity is provisional
- [x] preserve Builder-side history through alias mapping
- [x] rebind active sessions to the canonical Swarm id
- [ ] wire live Swarm identity fetch/import from the external repo path

Tests:

- [x] contract tests for Builder-first then Swarm-link
- [x] harness test that inbound DM resolution returns the Swarm id after link
- [ ] live import harness against external Swarm repo/runtime

### Phase E. Rename And Conflict Repair

Status:

- shipped

Tasks:

- [x] rename mutation log
- [x] conflict state in canonical link storage
- [x] operator repair command surfaces
- [x] latest-confirmed-write sync policy across Builder and Swarm

Tests:

- [x] contract tests for rename without id churn
- [x] repair-path smoke tests
- [ ] conflict-resolution harness tests

### Phase F. Closure

Status:

- mostly shipped in Builder

Tasks:

- [x] doctor/watchtower identity surfaces
- [x] migration command or automatic migration pass
- [x] docs closeout with shipped state
- [x] broader regression pass

Tests:

- [x] targeted phase suite
- [x] CLI smoke suite
- [x] operator pairing harness suite
- [x] broader regression suite

## Remaining Follow-Up

- [ ] multi-turn onboarding prompts for first-time Telegram users
- [ ] live Spark Swarm identity fetch/import from the external runtime path
- [ ] live import harness against the external Swarm runtime
- [ ] fuller conflict-resolution harness coverage

## Current Verification Command Set

Use these while shipping this track:

- `python -m pytest tests/test_agent_identity_contracts.py`
- `python -m pytest tests/test_memory_orchestrator.py tests/test_builder_prelaunch_contracts.py tests/test_cli_smoke.py tests/test_operator_pairing_flows.py`
- `python -m pytest tests/test_agent_identity_contracts.py tests/test_cli_smoke.py`

Run the broader combined pass after the current slice is stable:

- `python -m pytest tests/test_agent_identity_contracts.py tests/test_memory_orchestrator.py tests/test_builder_prelaunch_contracts.py tests/test_cli_smoke.py tests/test_operator_pairing_flows.py`
