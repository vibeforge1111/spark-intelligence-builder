# Spark Intelligence Prompt Bible

## 1. Purpose

This document collects the core prompts and operating doctrines that should shape how Spark Intelligence is designed, reviewed, built, and improved.

The aim is not to create prompt clutter. The aim is to create a small set of high-signal prompts that keep the system:

- useful
- maintainable
- lightweight
- secure
- product-driven
- startup-rational

This prompt set should push the repo toward product efficiency, quality, and usefulness while preserving a small and coherent codebase.

## 2. Core Doctrine

### 2.1 System Doctrine Prompt

Use this when defining product and architecture direction.

```text
You are designing Spark Intelligence.

Your job is to create a Spark-native persistent agent system that is:
- highly useful in real life
- lightweight in implementation
- maintainable over time
- integrated with Spark Researcher, Spark Swarm, domain chips, specialization paths, and autoloop flywheels
- strict about not rebuilding what already exists elsewhere in the Spark ecosystem

Every design decision must answer:
1. Does this deepen the core wedge?
2. Does this reduce or increase maintenance burden?
3. Can this be owned by an existing Spark subsystem instead?
4. Is this the simplest version that preserves real value?
5. Does this create one obvious path or multiple competing systems?

Favor:
- clear boundaries
- one source of truth
- one scheduler model
- thin adapters
- strong harnesses
- fast installation
- reliable behavior

Reject:
- duplicated runtime logic
- architecture vanity
- channel-specific forks
- too many background systems
- product ideas that are impressive but not sticky
```

### 2.2 Gstack Product Discipline Prompt

Use this whenever making product decisions.

```text
Use gstack-style product discipline on every decision.

Assume that the goal is not to ship more features. The goal is to ship the smallest system that creates real product pull.

Evaluate every feature using:
1. Desperate user need
2. Frequency of use
3. Retention impact
4. Distribution impact
5. Whether the feature makes the product feel more coherent or more fragmented

Ask:
- What painful recurring job does this solve?
- What is the narrowest wedge?
- What is the status quo today?
- Why will a user keep using this instead of bouncing?
- Does this feature improve product-market fit or just improve the demo?

Do not approve a feature if it mostly improves surface area without improving stickiness, trust, or leverage.
```

## 3. Product and Startup Prompts

### 3.1 Product-Market Fit Prompt

```text
Review this feature like a startup founder who cares about product-market fit.

Answer:
1. Who is the exact user?
2. What recurring pain are they trying to remove?
3. Why is this problem painful enough to matter weekly or daily?
4. What is the current workaround?
5. Why is Spark Intelligence meaningfully better?
6. Will this feature increase retention, usage frequency, or willingness to recommend?
7. Is this the wedge, or is it a distraction from the wedge?

Return:
- core user
- recurring pain
- PMF strength: strong / medium / weak
- keep / cut / defer recommendation
```

### 3.2 Great Startup Prompt

```text
Review this decision like a great startup operator.

Prefer:
- small wedges
- speed to useful product
- sticky behavior
- leverage
- compounding learning loops

Reject:
- broad feature fantasies
- unowned complexity
- expensive architecture without a user reason
- features that create maintenance without creating pull

Explain whether this helps Spark Intelligence become:
- more useful
- more retainable
- easier to operate
- more defensible
```

## 4. Engineering Prompts

### 4.1 System Architecture Prompt

```text
Review the proposed architecture for Spark Intelligence.

Judge it by:
- simplicity
- maintainability
- runtime coherence
- subsystem ownership
- install complexity
- operational reliability
- ability to evolve without fragmentation

Force these questions:
1. Is there one obvious execution path?
2. Are there multiple systems solving the same problem?
3. Is this rebuilding Spark Researcher or Spark Swarm instead of integrating them?
4. Are adapters thin or are they leaking product logic?
5. Is there one scheduler and one job harness?
6. Is the control plane lightweight and understandable?
7. Will this be easy to debug six months from now?

Return:
- architecture risks
- simplification opportunities
- duplicated responsibilities
- recommended final shape
```

### 4.2 Maintainability Prompt

```text
Review this code or design for maintainability first.

Look for:
- duplicated logic
- hidden coupling
- unclear ownership
- state spread across too many places
- abstraction that hides simple behavior
- platform-specific hacks that leak into the core
- jobs or daemons that can conflict with each other

Prefer:
- one source of truth
- small modules
- stable interfaces
- explicit state transitions
- boring code that will age well

Return:
- maintainability risks
- what to simplify
- what to move behind interfaces
- what to delete entirely
```

### 4.3 Code Review Prompt

```text
Review this change like a strict senior engineer.

Focus first on:
- bugs
- regressions
- missing tests
- unsafe assumptions
- concurrency or state hazards
- migration hazards
- operator trust risks

Then evaluate:
- readability
- maintainability
- whether the feature is worth the complexity it adds

Do not praise code by default.
Return findings ordered by severity with exact fixes.
```

### 4.4 Security Prompt

```text
Review this design for security and abuse resistance.

Assume:
- every inbound channel is untrusted
- every user message can be adversarial
- adapters can leak unsafe assumptions
- migrations can import bad state

Check:
- auth and allowlists
- pairing flow
- secret handling
- channel isolation
- group vs DM context boundaries
- dangerous tool execution
- import validation
- replay and auditability

Return:
- critical risks
- privilege boundary mistakes
- missing validation
- safe defaults to enforce
```

## 5. Harness and Reliability Prompts

### 5.1 Cron and Job Harness Prompt

```text
Review this scheduled job design for Spark Intelligence.

The goal is one clean job harness, not many competing background systems.

Check:
- is the job idempotent?
- can it be retried safely?
- does it have one owner?
- is status observable?
- is failure visible?
- does it mutate shared state safely?
- can it be smoke-tested locally?

Reject:
- silent jobs
- overlapping schedulers
- hidden retries
- side effects without audit trails
- jobs that fork their own state model

Return:
- harness risks
- observability gaps
- retry policy issues
- smoke tests required
```

### 5.2 Smoke Test Prompt

```text
Design the minimum smoke tests for this subsystem.

The smoke suite should prove:
- install works
- setup works
- doctor works
- gateway starts
- one adapter can receive and send
- one persistent session survives restart
- one scheduled job runs correctly
- one migration import completes safely

Prefer short, stable, high-signal tests over giant flaky integration suites.
```

## 6. Competitive Learning Prompts

### 6.1 Hermes vs OpenClaw Comparative Prompt

```text
Compare Hermes and OpenClaw for this subsystem.

Answer:
1. Where is Hermes cleaner?
2. Where is OpenClaw stronger?
3. Which parts are good enough to borrow?
4. Which parts should be copied as patterns?
5. Which parts should Spark reject to stay lightweight?

Do not optimize for imitation.
Optimize for:
- clearer ownership
- simpler operations
- stronger harnesses
- faster install
- lower maintenance burden
```

### 6.2 Import and Migration Prompt

```text
Review this migration design from OpenClaw or Hermes into Spark Intelligence.

Rules:
- import only state that is deterministic and auditable
- never import mystery state
- preserve user continuity where possible
- reject foreign memory semantics unless they map cleanly
- validate everything before activation

Return:
- safe to import
- unsafe to import
- transform rules
- rollback plan
```

## 7. How To Use This Bible

Use these prompts at the right decision points:

- product changes: product-market fit prompt + great startup prompt
- architecture changes: system architecture prompt + maintainability prompt
- risky implementation: code review prompt + security prompt
- scheduler work: cron and job harness prompt + smoke test prompt
- competitive learning: Hermes vs OpenClaw comparative prompt
- migrations: import and migration prompt

The goal is not prompt maximalism.
The goal is disciplined thinking with a small number of reusable lenses.
