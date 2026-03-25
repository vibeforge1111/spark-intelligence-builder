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

## Remaining Improvement Opportunities

- add `agents/openai.yaml` for skill discovery metadata
- add example transcripts for one or two skills
- add a simple local validation script for skill completeness checks
