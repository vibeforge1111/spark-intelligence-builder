# Spark Intelligence v1 Domain Chip Attachment Contract

## 1. Purpose

This document defines how `Spark Intelligence` attaches and activates domain chips.

The goal is to use domain chips as real specialization modules without:

- hardcoding domain logic into the base runtime
- turning chips into branding-only presets
- giving chips ownership over identity, channels, or security boundaries

## 2. Core Rule

Domain chips should attach through a narrow, explicit contract.

They are specialization modules, not alternate runtimes.

## 3. Design Goals

### 3.1 Real Modularity

Chips should be attachable, inspectable, and removable without rewriting the runtime shell.

### 3.2 Spark-Native Integration

Chips should integrate through Spark Researcher and related Spark surfaces, not through random adapter hooks.

### 3.3 Security Boundaries

Chips must not own:

- identity
- pairing
- provider auth
- channel auth

### 3.4 Lightweight Runtime

Attaching a chip should add capability, not a new subsystem zoo.

## 4. What A Chip Owns

A domain chip may own:

- domain-specific evaluation logic
- domain-specific suggestion logic
- domain packets and doctrine
- bounded watchtower or review logic
- scoped workflows and heuristics

## 5. What A Chip Does Not Own

A domain chip does not own:

- human identity
- session continuity
- adapter transport
- global memory doctrine
- tool approval policy

## 6. Existing Spark Hook Model

Current Spark chip contracts already expose hooks such as:

- `evaluate`
- `suggest`
- `packets`
- `watchtower`

Spark Intelligence should reuse those shapes where possible instead of inventing a parallel chip protocol.

## 7. Attachment Record

Recommended v1 attachment record:

```text
id
agent_id
chip_key
chip_manifest_path
status
attachment_mode
priority
created_at
updated_at
```

## 8. Activation Model

Recommended v1 activation modes:

- `inactive`
- `available`
- `active`
- `pinned`

### 8.1 Rule

Not every attached chip needs to be active on every request.

## 9. Selection Logic

Chip selection should consider:

- active Spark profile
- active specialization path
- current task domain
- explicit user or operator preference

Do not activate chips just because they exist.

## 10. Runtime Boundary

Spark Intelligence may decide which chips are in scope for a request.

Spark Researcher and related Spark surfaces should do the domain-intelligence work once those chips are in scope.

## 11. Security Rules

### 11.1 No Identity Leakage

Chips must not become a second identity system.

### 11.2 No Secret Ownership

Chips must not silently own provider or adapter secrets.

### 11.3 No Hidden Tool Escalation

Attaching a chip must not silently widen tool authority.

### 11.4 Bounded Contracts

Chip invocation should remain contract-driven and auditable.

## 12. v1 Practical Rule

For v1:

- support a small starter chip set
- expose chip attachment visibly in operator state
- keep chip activation narrow and explicit

## 13. Final Decision

Domain chips in Spark Intelligence should behave like real specialization modules attached through a narrow Spark-native contract.

They deepen the agent.

They do not replace the runtime shell or the security model.
