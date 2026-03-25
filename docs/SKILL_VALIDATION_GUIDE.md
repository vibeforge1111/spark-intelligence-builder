# Skill Validation Guide

## Purpose

This guide explains the lightweight validation layer for repo skills.

The goal is not heavyweight tooling.

The goal is to catch obvious drift before a skill becomes vague, incomplete, or structurally inconsistent.

## Validator

Run:

```bash
python scripts/validate_skills.py
```

The validator checks that each skill:

- has key structural sections
- references actual files
- keeps those references resolvable from the skill directory or repo root

## Scenario Packs

Scenario packs are lightweight manual pressure tests.

They are useful when a skill passes structural validation but you still want to test whether it produces the right judgment.

Current pack:

- `scenario-packs/reliable-job-harnesses/`

## Recommended Workflow

1. Run the validator.
2. Pick one scenario from the relevant pack.
3. Answer it using the skill and its references.
4. Check whether the answer preserves Spark subsystem boundaries.
5. Update the skill or reference docs if the answer drifts into vague or heavy-handed architecture.

## Why This Stays Lightweight

This validation layer intentionally avoids:

- a framework dependency
- opaque scoring systems
- large test harnesses
- synthetic automation that hides the actual doctrine

It is a small guardrail, not a platform.
