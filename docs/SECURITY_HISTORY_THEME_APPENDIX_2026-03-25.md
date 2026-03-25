# Security History Theme Appendix 2026-03-25

## Purpose

This appendix records the broader commit-history theme mining used in the security analysis.

It is not a line-by-line diff archive.

It is a compact research aid for recurring hardening classes.

## OpenClaw

Approximate full-history theme counts from commit-subject mining:

- auth and webhook related: `~1600`
- runtime and session related: `~1581`
- host and tool boundary related: `~1070`
- identity and pairing related: `~1053`
- leakage and logging related: `~314`

Representative recurring subjects:

- WhatsApp identity handling
- pairing replies and codes
- default daemon identity
- auth profile ids and JWT fallback
- session key determinism
- admin-only session reset
- exec wrapper trust
- media root bypass closure
- env sanitization
- SecretRef handling

## Hermes

Approximate full-history theme counts from commit-subject mining:

- runtime and session related: `~243`
- auth and webhook related: `~116`
- host and tool boundary related: `~114`
- identity and pairing related: `~58`
- leakage and logging related: `~37`

Representative recurring subjects:

- unauthorized DM behavior
- DM session isolation
- group session isolation
- file permission hardening
- dangerous command approval clarity
- custom provider auth correctness
- MCP OAuth path traversal and shared-state fixes
- token leakage prevention

## Practical Read

OpenClaw’s security churn surface is broader.

Hermes’ security churn surface is narrower but still recurring in the places that matter for local agents:

- sessions
- DMs
- file permissions
- approvals
- auth edge cases

## Spark Rule

If a theme recurs across both systems, treat it as a first-order design requirement, not a later polish pass.
