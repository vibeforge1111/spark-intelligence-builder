# Security Systems Guardrails

## Focus

Use this reference when designing:

- identity and pairing systems
- provider and channel auth
- dangerous command approvals
- webhook and adapter trust boundaries
- local runtime hardening
- tool execution boundaries

## Standard Security Rules

- deny by default
- least authority
- one security truth surface
- fail closed
- local inspectability

## Local-Agent Guardrails

- no adapter-owned identity truth
- no username-only linking
- no cross-user session reuse
- no broad tool powers by demo default
- no full host env inheritance without sanitization
- no hidden daemon or watchdog layers
- no secret leakage in logs

## Spark-Specific Guardrails

- keep identity canonical in one store
- keep pairing and allowlists explicit
- keep dangerous-action approvals explicit
- keep cross-channel linking explicit
- keep native autostart wrappers thin and auditable

## Output Template

```text
Security Boundary:
Authority Added:
Default-Deny Rule:
Approval Or Pairing Rule:
Secret Handling Rule:
Session Rule:
Failure Mode:
Required Smoke Tests:
Do-Not-Build Guardrail:
```
