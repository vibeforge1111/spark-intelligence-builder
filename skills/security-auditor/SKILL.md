---
name: security-auditor
description: Audit local-agent systems, specs, and code for security weaknesses and hardening priorities. Use when reviewing Spark Intelligence features, security-sensitive diffs, identity or pairing flows, host-execution boundaries, webhook adapters, auth and secret handling, or when producing a security grade and remediation list that others can reuse later.
---

# Security Auditor

Use this skill when work touches:

- security review
- pre-landing diff review
- trust-boundary grading
- hardening backlog creation
- local-agent security posture checks
- reusable audit reports for other builders

## Read First

- `docs/SECURITY_DOCTRINE_V1.md`
- `docs/IDENTITY_AND_SESSION_MODEL_SPEC_V1.md`
- `docs/PROVIDER_AND_AUTH_CONFIG_SPEC_V1.md`
- `docs/CODING_RULESET_V1.md`
- `docs/SECURITY_RESEARCH_PLAN_HERMES_OPENCLAW.md`
- `docs/OPENCLAW_HERMES_SECURITY_HISTORY_ANALYSIS_2026-03-25.md`

Then read:

- `references/audit-framework.md`
- `references/competitor-findings.md`

## Core Doctrine

Audit for real security failures first:

- identity confusion
- host takeover paths
- dangerous defaults
- weak approvals
- secret leakage
- cross-session leakage

Do not get distracted by style before those are clear.

## Workflow

1. Identify the artifact being reviewed.
2. Map the relevant trust boundaries.
3. Check identity, session, auth, host, webhook, secret, and logging behavior.
4. Compare the design against known OpenClaw and Hermes hardening classes.
5. Grade the system.
6. Produce severity-first findings and a hardening order.

## Required Outputs

Return these explicitly:

- security grade
- critical findings
- high findings
- medium findings
- low findings
- open questions
- hardening priorities
- required tests

## Review Rules

- findings first
- exploit paths before theory
- boundary mistakes before optional improvements
- remediation order before broad advice

## Default Deliverable

The result should usually be a reusable security audit with:

- grade
- severity-ordered findings
- concrete hardening steps
- tests to add before shipping
