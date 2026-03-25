---
name: agent-landscape-analysis
description: Analyze OpenClaw, Claude Cowork, Hermes Agent, and adjacent agent products to learn what to borrow, what to directly copy as patterns, and what Spark Intelligence should keep uniquely its own. Use when comparing architectures, onboarding, adapters, reliability harnesses, migration paths, or competitive product shape.
---

# Agent Landscape Analysis

Use this skill when work touches:

- OpenClaw comparisons
- Hermes Agent comparisons
- Claude Cowork comparisons
- onboarding design
- adapter design
- harness and reliability comparisons
- migration and import decisions
- competitive product or architecture benchmarking

## Read First

- `docs/ARCHITECTURE_SPARK_INTELLIGENCE_V1.md`
- `docs/CRON_JOB_HARNESS_SPEC_V1.md`
- `docs/IMPORT_AND_MIGRATION_SPEC_V1.md`
- `docs/SPARK_INTELLIGENCE_PROMPT_BIBLE.md`

Then read:

- `references/comparison-playbook.md`

## External Research Rule

Use primary sources whenever possible:

- official docs
- official GitHub repos
- official issue trackers

Treat Claude Cowork as a product and UX benchmark unless trustworthy technical internals are publicly available. Do not invent proprietary implementation details.

## Comparison Output Format

Always classify findings into:

- borrow
- yoink
- reject
- keep uniquely Spark

## Workflow

1. Identify the exact subsystem being compared.
2. Read the relevant Spark docs first so Spark's intended shape is clear.
3. Research official sources for OpenClaw, Hermes, or Claude Cowork.
4. Compare architecture, onboarding, adapters, diagnostics, harnesses, and install shape.
5. Separate good product shape from bad maintenance cost.
6. Recommend what Spark should borrow, copy as a pattern, or reject.

Use the comparison buckets and output template in `references/comparison-playbook.md`.

## Required Outputs

Return these explicitly:

- what they do better
- what they do worse
- what Spark should borrow
- what Spark should yoink as a pattern
- what Spark should reject
- what must remain authentically Spark-native

## Review Rules

- Do not optimize for imitation.
- Do not reward breadth when it increases maintenance cost.
- Prefer stronger harnesses, cleaner setup, and clearer ownership.
- Keep a running eye on migration compatibility and operator trust.

## Default Deliverable

The result should end with:

- borrow
- yoink
- reject
- keep uniquely Spark
