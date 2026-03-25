# Maintainable Engineering Reference

## Use This Skill For

- code review
- architecture review
- cleanup and simplification
- docs review
- security-sensitive engineering changes

## Trigger Examples

- "Review this diff for maintainability and regressions"
- "Simplify this architecture without losing the important features"
- "What coding standards should we enforce here?"
- "Audit these docs for operator clarity"

## Core Standards

Prefer:

- one obvious path
- explicit state
- stable interfaces
- simple dependency graphs
- modules with clear ownership
- docs that make the happy path and debug path obvious

Reject:

- duplicated logic
- invisible coupling
- abstract indirection that buys nothing
- multiple sources of truth
- platform quirks leaking into the core

## Review Order

### 1. Bugs and Regressions

Check:

- broken behavior
- edge cases
- migrations
- state handling
- concurrency hazards

### 2. Maintainability

Check:

- ownership boundaries
- duplicated logic
- hidden state
- over-complex abstractions
- module sprawl

### 3. Security and Trust

Check:

- untrusted input handling
- secrets
- authorization boundaries
- dangerous actions
- auditability

### 4. Documentation

Check:

- does the doc explain the happy path?
- does the doc explain how to diagnose failure?
- does the doc match the architecture?

## Output Template

```text
Critical Findings:
Maintainability Risks:
Ownership Mistakes:
Security Risks:
Documentation Gaps:
Required Tests:
Suggested Simplifications:
```

## Escalate If

- the change duplicates Spark Researcher or Spark Swarm logic
- the design mixes memory doctrine into unrelated modules
- security boundaries are unclear
- the code cannot be verified without assumptions
