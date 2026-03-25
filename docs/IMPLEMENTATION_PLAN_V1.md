# Spark Intelligence v1 Implementation Plan

## 1. Purpose

This document defines the implementation plan for the first real build of `Spark Intelligence`.

The goal is to materialize:

- one persistent local agent
- one lightweight runtime shell
- one secure identity and pairing model
- one Telegram-first delivery path
- one Spark Researcher core integration

without losing the maintainability and security doctrine already written in this repo.

## 1.1 Current Status As Of 2026-03-26

The repo has already moved well past pure scaffolding.

What is now implemented:

- package and CLI entrypoint
- canonical config and SQLite state bootstrap
- identity, pairing, session, and operator control layers
- Telegram long-poll runtime
- Spark Researcher bridge
- Spark Swarm sync/evaluation bridge
- external chip/path attachment snapshot wiring
- local audit, inbox, security, trace, and outbound surfaces

So the implementation path is no longer "start the first slice from scratch."

The implementation path is now "stabilize and harden the first slice before expanding breadth."

## 2. Build Rule

Build one narrow, production-shaped slice first.

Do not begin with a giant framework.

## 3. Pre-Implementation Non-Negotiables

The first slice must preserve these boundaries from the beginning:

- one workspace trust boundary
- one canonical config model
- one canonical SQLite state model
- one role distinction between paired user and operator authority
- one operator-owned runtime control path
- one adapter-containment rule
- one federated repo boundary between Spark Intelligence and the rest of Spark

If a shortcut breaks one of these, it should not land as "temporary v1 glue".

## 4. Target v1 Slice

The first serious slice should prove:

- install
- setup
- provider auth
- identity and pairing
- Telegram DM interaction
- Spark Researcher-backed response path
- doctor

That is enough to validate the product shape.

It should also prove the architecture shape:

- Spark Intelligence can call Spark Researcher cleanly without copying its internals
- later Spark Swarm and chip/path integrations can stay contract-driven
- this repo can remain the orchestrator instead of turning into a giant everything-repo

## 5. Proposed Codebase Layout

Recommended initial layout:

```text
src/
`- spark_intelligence/
   |- cli/
   |- config/
   |- state/
   |- identity/
   |- gateway/
   |- adapters/
   |  `- telegram/
   |- researcher_bridge/
   |- jobs/
   |- doctor/
   `- security/
```

## 6. Phase Plan

### Phase 0: Package And CLI Skeleton

Status: completed

Goal:

- create the executable package and minimal command tree

Target commands:

- `setup`
- `doctor`
- `gateway start`
- `channel add telegram`
- `auth connect`
- `jobs tick`

Exit criteria:

- commands parse cleanly
- config root created
- no heavy dependencies required
- runtime control remains operator-only

### Phase 1: Config And State Layer

Status: completed

Goal:

- build the canonical config and SQLite state layer

Modules:

- `config/`
- `state/`

Key outputs:

- config loader and validator
- SQLite schema for identity, sessions, channels, pairings
- exact role and authority records
- strict local paths
- repo-local integration config for external Spark systems

Exit criteria:

- config validates
- state initializes idempotently
- doctor can verify state readiness
- no secondary adapter-owned truth store exists

### Phase 2: Identity And Pairing Core

Status: completed

Goal:

- implement the canonical identity and session model

Modules:

- `identity/`

Key outputs:

- human identity records
- agent identity records
- session bindings
- pairing records
- allowlists
- paired-user vs operator-authority checks

Exit criteria:

- unknown senders fail closed
- explicit pairing works
- sessions are inspectable and revocable
- paired access cannot mutate control-plane state

### Phase 3: Provider And Auth Layer

Status: completed for the first provider path

Goal:

- implement model-provider configuration and validation

Modules:

- `config/`
- `security/`

Key outputs:

- provider config records
- secret handling
- auth validation probes

Exit criteria:

- provider setup works for one provider
- invalid auth fails clearly
- no secret leakage in logs

### Phase 4: Telegram Adapter

Status: completed for the first live runtime path

Goal:

- implement the first real delivery adapter

Modules:

- `adapters/telegram/`
- `gateway/`

Key outputs:

- long-poll loop
- Telegram update normalization
- DM-first behavior
- pairing-safe `/start`
- outbound text delivery
- adapter-local failure containment

Exit criteria:

- one allowlisted or paired DM user can message the bot
- one reply returns through the full path
- Telegram failure does not corrupt identity or job state

### Phase 5: Spark Researcher Bridge

Status: completed for the current vertical slice

Goal:

- implement the narrow Spark Researcher integration bridge

Modules:

- `researcher_bridge/`

Key outputs:

- request envelope
- bridge to advisory and packet/memory surfaces
- normalized response envelope
- external-runtime contract wiring without local logic duplication

Exit criteria:

- Spark Intelligence can produce a Spark Researcher-backed reply for Telegram
- no adapter secrets pass through the bridge by default
- Spark Researcher remains an external system boundary, not copied logic

### Phase 6: Doctor, Health, And Harness

Status: partially completed

Goal:

- make the vertical slice operator-trustworthy

Modules:

- `doctor/`
- `jobs/`
- `gateway/`

Key outputs:

- doctor checks
- adapter health
- provider health
- `jobs tick`
- runtime ownership checks
- config/state consistency checks

Exit criteria:

- install -> setup -> doctor -> gateway start is reliable
- failures are legible
- hidden background-runtime drift is detectable

Remaining work inside this phase:

- replace mostly manual verification with repeatable regression coverage
- add a lightweight `tests/` suite for the current Telegram/operator slice
- cover failure-path persistence and observability surfaces with automated checks

### Phase 7: Security Hardening Pass

Status: partially completed at the doctrine level, not yet complete at the implementation-test level

Goal:

- run the new security doctrine and audit skills against the first slice

Artifacts:

- security audit findings
- remediation patches
- required smoke tests

Exit criteria:

- no critical findings
- high findings resolved or explicitly blocked from ship

### Phase 8: Optional Expansion

Only after the first slice is stable:

- Discord adapter
- WhatsApp adapter
- Spark Swarm escalation path
- richer operator control surface

Expansion rule:

- add bridges and contracts before adding copied internals
- keep domain chips and specialization paths out of this repo unless there is a very strong boundary reason

## 7. Required Smoke Tests

The first slice should have short smoke tests for:

- install
- setup
- provider validation
- identity and pairing
- Telegram adapter startup
- Telegram inbound message
- Telegram outbound reply
- doctor
- jobs tick
- paired user cannot access operator-only mutations
- exact session binding survives adapter restart
- Telegram adapter failure is visible without taking down unrelated state
- Spark Researcher bridge works without local fallback duplication

As of 2026-03-26, these smoke tests should become the next actual implementation work, not a later aspiration.

## 8. Security Review Gates

Before shipping the first slice, run:

- `$security-systems` on the architecture and feature boundaries
- `$security-auditor` on the actual implementation diff

The first slice should especially check:

- pairing defaults
- Telegram DM authorization
- token handling
- host env inheritance
- dangerous action boundaries

## 8. Suggested Build Order By Files

Suggested first code files:

1. `src/spark_intelligence/cli/__init__.py`
2. `src/spark_intelligence/config/loader.py`
3. `src/spark_intelligence/state/db.py`
4. `src/spark_intelligence/identity/models.py`
5. `src/spark_intelligence/identity/service.py`
6. `src/spark_intelligence/adapters/telegram/client.py`
7. `src/spark_intelligence/adapters/telegram/normalize.py`
8. `src/spark_intelligence/gateway/runtime.py`
9. `src/spark_intelligence/researcher_bridge/advisory.py`
10. `src/spark_intelligence/doctor/checks.py`

## 9. What Not To Do In The First Implementation

- do not start with Discord and WhatsApp too
- do not build webhook-first Telegram
- do not build a custom daemon manager
- do not rebuild Spark Researcher logic locally
- do not pull chip or specialization-path logic into this repo just because integration is inconvenient
- do not add broad dangerous tool execution before approval boundaries are ready
- do not skip doctor and health surfaces

## 10. Final Implementation Decision

The correct first implementation is:

- one package
- one SQLite-backed local state model
- one secure identity core
- one Telegram DM adapter
- one Spark Researcher bridge
- one doctorable runtime shell

That is the smallest serious version of Spark Intelligence.

## 11. Tomorrow Start Order

Tomorrow should begin from the current build, not from documentation assumptions.

The exact order should be:

1. create `tests/` and choose the lightest viable local test runner
2. add Telegram pairing/operator regression tests:
   - pending pairing
   - hold latest
   - approve latest
   - revoke latest
   - revoked user reply
3. add observability regression tests:
   - filtered `gateway traces`
   - filtered `gateway outbound`
   - filtered `operator review-pairings`
   - filtered `operator history`
4. add Telegram failure-path tests:
   - auth failure persistence
   - poll failure persistence
   - duplicate suppression
   - rate limiting
5. only after those land, decide whether the next slice is:
   - richer doctor output
   - Telegram runtime polish
   - or live runtime work for another adapter

## 12. Tomorrow Non-Goals

Do not start tomorrow with:

- live Discord runtime
- live WhatsApp runtime
- webhook-first Telegram
- new memory ownership inside this repo
- copied Spark Researcher or Spark Swarm internals
