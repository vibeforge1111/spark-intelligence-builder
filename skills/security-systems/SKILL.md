---
name: security-systems
description: Design secure, lightweight local-agent systems for Spark Intelligence and adjacent agent products. Use when defining identity and pairing rules, provider or channel auth, dangerous command approvals, tool and host boundaries, webhook trust checks, local secret handling, or security guardrails for new features.
---

# Security Systems

Use this skill when work touches:

- identity and pairing architecture
- session isolation
- provider and secret handling
- channel or webhook auth
- dangerous command approval design
- tool and host authority boundaries
- security rules for new features

## Read First

- `docs/SECURITY_DOCTRINE_V1.md`
- `docs/IDENTITY_AND_SESSION_MODEL_SPEC_V1.md`
- `docs/PROVIDER_AND_AUTH_CONFIG_SPEC_V1.md`
- `docs/CODING_RULESET_V1.md`
- `docs/SECURITY_RESEARCH_PLAN_HERMES_OPENCLAW.md`
- `docs/OPENCLAW_HERMES_SECURITY_HISTORY_ANALYSIS_2026-03-25.md`

Then read:

- `references/system-guardrails.md`
- `references/competitor-lessons.md`

## Core Doctrine

Prefer:

- deny-by-default
- least authority
- one canonical identity and security truth surface
- explicit approvals and revocation
- strict file and secret handling
- visible, auditable runtime behavior

Reject:

- silent authority expansion
- username-only or heuristic identity linking
- vague dangerous-command approvals
- daemon or watchdog tricks that hide active authority
- adapter-specific security truth

## Workflow

1. Identify the exact trust boundary.
2. Define what new authority the feature adds.
3. Reduce the authority to the minimum needed.
4. Define the explicit approval, pairing, or auth rule.
5. Define secret, session, and logging behavior.
6. Check OpenClaw and Hermes lesson classes for known failure shapes.
7. Define fail-closed behavior, inspection surfaces, and smoke tests.

## Required Outputs

Return these explicitly:

- security boundary
- authority added
- default-deny rule
- approval or pairing rule
- secret-handling rule
- session rule
- failure mode
- required smoke tests
- do-not-build guardrail

## Spark-Specific Rules

- identity is a security boundary, not a convenience feature
- DMs-first is safer than shared-room-first
- cross-channel linking must be explicit
- dangerous local actions must be approval-gated
- native autostart wrappers must not become hidden control planes

## Default Deliverable

The result should usually end in a compact secure-design brief with:

- boundary map
- secure defaults
- hardening rules
- tests to add
