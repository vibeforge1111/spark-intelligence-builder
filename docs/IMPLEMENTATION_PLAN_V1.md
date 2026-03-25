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

Exit criteria:

- config validates
- state initializes idempotently
- doctor can verify state readiness
- no secondary adapter-owned truth store exists

### Phase 2: Identity And Pairing Core

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

Goal:

- implement the narrow Spark Researcher integration bridge

Modules:

- `researcher_bridge/`

Key outputs:

- request envelope
- bridge to advisory and packet/memory surfaces
- normalized response envelope

Exit criteria:

- Spark Intelligence can produce a Spark Researcher-backed reply for Telegram
- no adapter secrets pass through the bridge by default

### Phase 6: Doctor, Health, And Harness

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

### Phase 7: Security Hardening Pass

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
