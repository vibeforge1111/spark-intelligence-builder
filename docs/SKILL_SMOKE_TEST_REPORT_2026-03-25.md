# Skill Smoke Test Report 2026-03-25

## Purpose

This report records a lightweight smoke-test pass for the four core repo skills.

The goal was not to prove perfect performance. The goal was to catch obvious gaps in:

- trigger clarity
- workflow depth
- output specificity
- missing references
- ambiguity about subsystem ownership

## Test Cases

### 1. Reliable Job Harnesses

Prompt:

`Design the retry and smoke-test strategy for adapter reconnect jobs without creating competing schedulers.`

Observed gap before improvement:

- trigger was fine
- workflow was too short
- output shape was not detailed enough for repeated use

Improvement applied:

- added `references/workflow.md`
- added anti-patterns
- added output template
- added explicit default deliverable

Result after improvement:

- stronger
- clearer
- reusable for future harness work

### 2. Maintainable Engineering

Prompt:

`Review this architecture diff for maintainability, trust boundaries, and missing docs.`

Observed gap before improvement:

- it had principles but not enough review structure
- findings format was underspecified

Improvement applied:

- added `references/review-framework.md`
- added severity and output structure guidance
- added explicit actionable deliverable requirements

Result after improvement:

- better for repeated code review and architecture review use

### 3. Spark Ecosystem Product

Prompt:

`Should persistent reminders live in Spark Intelligence, Spark Researcher, or a domain chip?`

Observed gap before improvement:

- product logic was sound
- subsystem ownership map was too implicit

Improvement applied:

- added `references/system-map.md`
- added explicit ownership map
- added output structure for value and ownership

Result after improvement:

- much easier to answer modular ownership questions consistently

### 4. Agent Landscape Analysis

Prompt:

`Compare Hermes and OpenClaw on adapters, cron harnesses, and setup flow. What should Spark borrow and reject?`

Observed gap before improvement:

- comparison idea was good
- evidence and output format needed stronger structure

Improvement applied:

- added `references/comparison-playbook.md`
- added explicit evidence rules
- added comparison dimensions
- added fixed result buckets

Result after improvement:

- more likely to produce grounded competitive analysis instead of vague benchmarking

## Summary

Main improvements from the smoke-test pass:

- every skill now has a reference file
- every skill now has a more explicit output shape
- subsystem ownership and anti-patterns are clearer
- repeated use should be more consistent

## Second Pass

The second pass focused on three questions:

- do the skills encode enough Spark-system doctrine on their own
- do they distinguish cron from governing-loop work clearly enough
- do they point to an explicit coding ruleset rather than relying on ambient repo context

### New Gaps Found

#### 1. Repo-Level Coding Rules Were Still Implicit

Gap:

- maintainability doctrine existed across architecture and prompts
- there was no dedicated coding ruleset document that future reviews could anchor to

Fix applied:

- added `docs/CODING_RULESET_V1.md`
- added `skills/maintainable-engineering/references/coding-rules.md`
- updated `maintainable-engineering` to read the new coding-rules reference

#### 2. Reliable Job Harnesses Needed A Stronger "Not Everything Is Cron" Rule

Gap:

- the skill already covered scheduler discipline
- it did not strongly enough force the operator to classify work as scheduled, event-driven, governing-loop, or repair work

Fix applied:

- updated `reliable-job-harnesses` to read the coding ruleset
- added explicit governing-loop distinction
- added doctor-path requirements
- expanded anti-patterns and output template in `references/workflow.md`

#### 3. Spark Ecosystem Product Needed Tighter Ownership For Scheduled Work

Gap:

- system ownership was clear at a high level
- it was still possible to drift into putting Spark evolution behavior inside cron just because the feature recurred

Fix applied:

- updated `spark-ecosystem-product` to read the coding ruleset
- added explicit governing-loop vs scheduled-work guidance
- updated the system map to assign true scheduled work to Spark Intelligence and governing-loop execution shape to Spark Researcher

## Result After Second Pass

The skills are now more opinionated in the right places:

- coding rules are explicit instead of ambient
- cron discipline is harder to misuse
- Spark ownership boundaries are clearer under repeated product pressure

The repo now has a stronger ladder:

- architecture
- coding rules
- harness spec
- skill references
- smoke-test report

## Validation Layer Follow-Up

After the second pass, the repo gained a lightweight validation layer:

- `python scripts/validate_skills.py`
- `scenario-packs/reliable-job-harnesses/`

First validation run found one real structural gap:

- `agent-landscape-analysis` was missing a `## Core Doctrine` section

That gap was fixed, and the validator now passes across all four skills.

## Remaining Improvement Opportunities

- add `agents/openai.yaml` for skill discovery metadata
- add example transcripts for one or two skills
- add a simple local validation script for skill completeness checks
- add a small simulated harness scenario pack for skill-level dry runs
