---
name: maintainable-engineering
description: Keep Spark Intelligence code, architecture, security, and documentation highly maintainable, scalable enough, and clean without adding unnecessary complexity. Use when reviewing code, planning architecture, writing docs, simplifying modules, improving quality, or enforcing long-term engineering standards.
---

# Maintainable Engineering

Use this skill when work touches:

- code review
- architecture review
- documentation quality
- module boundaries
- security-sensitive changes
- maintainability improvements
- simplification or cleanup work

## Read First

- `docs/ARCHITECTURE_SPARK_INTELLIGENCE_V1.md`
- `docs/SPARK_INTELLIGENCE_PROMPT_BIBLE.md`
- `docs/PRD_SPARK_INTELLIGENCE_V1.md`

Then read:

- `references/review-framework.md`

## Core Doctrine

Prefer:

- one obvious path
- one source of truth
- stable interfaces
- thin adapters
- small modules
- explicit state transitions
- boring code that ages well

Reject:

- architecture vanity
- indirection without pressure
- duplicated logic
- hidden coupling
- channel-specific forks in the core
- setup or runtime complexity that does not deepen the wedge

## Workflow

1. Identify the real ownership boundary.
2. Check whether the proposed change duplicates an existing Spark subsystem.
3. Reduce the number of moving parts.
4. Make failure and state transitions explicit.
5. Check documentation burden and operator burden.
6. Review security defaults and trust boundaries.
7. Require tests or smoke coverage where failure would be expensive.

If the request is a real review, use the severity-first order and output template from `references/review-framework.md`.

## Required Outputs

Return these explicitly:

- maintainability risks
- simplification opportunities
- ownership boundary mistakes
- security issues
- documentation gaps
- tests or smoke tests needed

## Review Rules

When reviewing code:

- findings first
- bugs before style
- regressions before theory
- missing tests before optional ideas

When reviewing architecture:

- remove layers before adding layers
- prefer integration over rebuilding
- prefer local clarity over abstract cleverness

When reviewing docs:

- optimize for operator clarity
- make the happy path obvious
- make debugging steps explicit

## Spark-Specific Rules

- Spark Intelligence should orchestrate Spark systems, not reimplement them.
- Memory doctrine belongs to the memory chip, not random modules.
- The gateway should not become the real product.
- The runtime should remain lightweight enough to install quickly and debug locally.

## Default Deliverable

The default output should be actionable:

- findings
- simplifications
- test gaps
- ownership corrections
