# Telegram Swarm Onboarding Backlog 2026-04-10

## 1. Purpose

This document turns the repo-backed Telegram and specialization-path validation work into an implementation backlog.

These are the highest-value changes to make future users and future agents connect faster with fewer hidden assumptions.

## 2. Highest-Leverage Product Gaps

### 2.1 No Canonical Specialization-Path Contract Doc

We validated a real repo-backed path, but the contract still has to be inferred from code, runbooks, and repo contents.

### 2.2 No Explicit GitHub Capability Surface

Whether an agent can push, create PRs, or merge is currently discovered late.

### 2.3 No Agent-Readable Onboarding Bundle

Human runbooks exist, but agents still need a small canonical document set they can parse reliably.

### 2.4 No One-Step Readiness Handshake

`swarm doctor` now exists, but it should become the standard handshake every client calls before autoloops or publish flows.

## 3. Recommended Build Order

### 3.1 P0: Make the Runtime Contract Explicit

- ship `SPECIALIZATION_PATH_RUNTIME_CONTRACT_V1`
- require `specialization-path.json` in repo-backed paths
- document canonical field names for scenario, mutation target, and collective payload paths

### 3.2 P0: Make Operability Machine-Readable

- expand `swarm doctor` into the canonical operability contract
- expose GitHub publish mode and readiness explicitly
- standardize blocker and recommendation keys

### 3.3 P1: Build an Agent-Readable Onboarding Pack

- add a compact onboarding doc for agents
- include command names, capability checks, and required artifacts
- keep it short enough for low-context runtimes such as Telegram-mediated agent calls

### 3.4 P1: Add GitHub Publish Adapters

- support Spark GitHub app mode
- support local `gh` fallback mode
- surface which mode is active before an agent tries to publish

### 3.5 P1: Normalize Win Publication

- define a standard "win packet" artifact
- capture benchmark result, evidence, candidate payload, and publish recommendation in one object
- let Swarm ingest that artifact directly

## 4. Concrete Builder Work Items

- add a JSON mode for `swarm doctor` that is stable enough for agent consumption
- add a specialization-path validator command
- add a `github doctor` or equivalent capability block under `swarm doctor`
- add a one-command onboarding flow that binds repo path, scenario path, mutation target, and Swarm config together

## 5. Concrete Spark Swarm Work Items

- publish the expected collective payload schema for specialization-path repos
- expose a documented ingestion contract for benchmark-win or insight packets
- make workspace auth expectations explicit for external agents and Telegram-led flows

## 6. Concrete Spark Intelligence Builder Work Items

- ship a template generator for `specialization-path.json`
- add first-class docs for repo-backed autoloop contracts
- teach Telegram help surfaces to point operators at the exact missing artifact or auth step

## 7. Success Criteria

This path is productized when a new operator or agent can:

1. attach a repo-backed specialization path
2. run one readiness command
3. see exact blockers
4. fix those blockers without reading source code
5. run the loop
6. publish a validated win through the correct GitHub and network path

## 8. Final Decision

The next step is not more experimentation.

The next step is to turn specialization-path execution, GitHub publication, and insight publication into explicit, agent-readable product contracts.
