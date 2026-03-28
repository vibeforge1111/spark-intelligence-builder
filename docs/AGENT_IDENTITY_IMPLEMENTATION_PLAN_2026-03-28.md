# Agent Identity Implementation Plan - March 28, 2026

## 1. Purpose

This document now serves as the shipped-state implementation map for canonical agent identity in Builder.

It keeps the same opinionated product rule:

- recommended path: Spark Swarm first
- supported fallback: Builder first, link later
- repair path: canonicalize to Spark Swarm and import Builder state

## 2. User-Facing Product Copy

### 2.1 Recommended Path

Use:

- `Recommended: connect your existing Spark Swarm agent first so your identity, name, and personality stay unified from the start.`

### 2.2 Supported Fallback

Use:

- `No Spark Swarm agent yet? Create your agent here now and link it later.`

### 2.3 Repair Path

Use:

- `We found both a Builder-created agent and a Spark Swarm agent for you.`
- `Recommended: keep the Spark Swarm identity as the main one and import your Builder setup into it.`

## 3. Shipped Builder Phases

### Phase A. First-Class Canonical Agent Storage

Status:

- shipped

Delivered:

- canonical Builder-side agent identity storage in `canonical_agent_links`
- mutable name-bearing profile storage in `agent_profiles`
- alias mapping in `agent_identity_aliases`
- rename history in `agent_rename_history`

Definition of done reached:

- Builder can represent local-first agents, Swarm-backed agents, linked agents, and conflict-bearing records

### Phase B. Agent-Scoped Persona Base

Status:

- shipped

Delivered:

- agent-scoped persona base in `agent_persona_profiles`
- persona mutation history in `agent_persona_mutations`
- runtime merge order:
  - defaults
  - chip/evolver base
  - agent persona base
  - human overlay
- legacy human-only overlay migration into canonical agent persona base

Definition of done reached:

- persona base survives rename and link operations

### Phase C. Builder-First Agent Creation Flow

Status:

- shipped

Delivered:

- local canonical agent creation on first use
- explicit conversational rename through the bridge
- explicit conversational agent persona authoring through the bridge
- multi-turn Telegram onboarding prompts for first-time Builder-local users
- persisted separation between human display name and agent display name

### Phase D. Swarm Import And Linking

Status:

- shipped in Builder

Delivered:

- import and canonicalization onto a Spark Swarm `agent_id`
- alias preservation for superseded local Builder ids
- session rebinding to the canonical Swarm id
- Builder-side history preservation through link
- hook-backed `agent import-swarm` flow for live external identity fetches

Still open:

- the external Spark Swarm runtime exposing the `identity` hook contract

### Phase E. Rename And Sync Contract

Status:

- shipped

Delivered:

- rename mutation logging
- latest-confirmed-write name precedence across Builder and Swarm
- visible provenance of the winning name through `name_source` and `name_updated_at`

Definition of done reached:

- rename is metadata-only and never creates a new agent

### Phase F. Conflict Handling And Operator Closure

Status:

- shipped in Builder

Delivered:

- operator repair surfaces
- doctor and Watchtower identity visibility
- persisted Watchtower and doctor readiness for the external `identity` hook
- broader regression coverage
- legacy personality migration command
- explicit conflict-repair harness coverage

Still open:

- final external Swarm import harness once the other side is ready

## 4. Runtime Resolution Rules

### 4.1 Agent Identity Resolution

When an inbound message arrives:

1. resolve human
2. resolve canonical agent for that human
3. if linked to Spark Swarm, use the linked canonical identity
4. if not linked, use the Builder-local canonical identity
5. if conflict exists, avoid silent canonical persona mutation until repaired

### 4.2 Persona Resolution

Current effective persona is built in this order:

1. default trait baseline
2. chip/evolver base profile if available
3. agent-scoped persona base
4. human-scoped overlay
5. temporary session-scoped tone hints outside canonical storage

## 5. Current Operator Surfaces

Builder currently exposes:

- `spark-intelligence agent inspect`
- `spark-intelligence agent rename --human-id <human_id> --name <name>`
- `spark-intelligence agent link-swarm --human-id <human_id> --swarm-agent-id <id> --agent-name <name> [--confirmed-at <timestamp>]`
- `spark-intelligence agent import-swarm --human-id <human_id> [--chip-key <chip-key>]`
- `spark-intelligence agent migrate-legacy-personality --human-id <human_id> [--keep-overlay] [--force]`

Operators can inspect:

- canonical identity state
- local-versus-Swarm link state
- current agent name and name provenance
- rename history
- alias state
- canonical persona base

## 6. Migration Notes

### 6.1 Former State

Builder previously stored effective personality shaping primarily on `human_id`.

### 6.2 Current Migration Path

Builder now supports migration that:

- snapshots the currently effective trait vector
- writes it into the canonical agent persona base
- preserves migration provenance
- optionally clears only the legacy human overlay
- avoids deleting broader observation or evolution history

## 7. Remaining Follow-Up

Builder-local follow-up:
- none

External dependency:

- the external Spark Swarm runtime implementing the `identity` hook contract Builder now expects

## 8. Recommended Near-Term Build Order

The next practical order is:

1. implement the `identity` hook in the external Spark Swarm runtime

That keeps Builder productized locally while leaving the cross-repo dependency explicit.
