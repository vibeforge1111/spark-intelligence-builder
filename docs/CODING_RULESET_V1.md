# Spark Intelligence v1 Coding Ruleset

## 1. Purpose

This ruleset defines how Spark Intelligence should be built so the codebase stays:

- lightweight
- maintainable
- fast to install
- easy to debug
- hard to accidentally fragment

This is not a style-only document.

It is a system-shaping document.

It exists to keep Spark Intelligence aligned with:

- Spark Researcher runtime realities
- Spark Swarm ownership boundaries
- the domain chip ecosystem
- the requirement for one coherent agent system

## 2. Source Principles

This ruleset is informed by:

- Spark Researcher doctrine: local-first state, explicit runtime flow, one governing loop
- Spark domain-chip doctrine: bottleneck-driven flywheels instead of fake cron-shaped loops
- Hermes lessons: clean install path, shared runtime discipline, SQLite-backed local state
- OpenClaw lessons: strong doctor and health posture, but clear signals that adapter/runtime sprawl creates avoidable failure modes

## 3. Core Standard

Every code change should make the system feel more like one obvious machine.

If a change adds power but also adds a second way to do the same thing, the default answer is no.

## 4. Architecture Rules

### 4.1 One Runtime Path

There should be one primary execution path for user-facing agent work:

- adapter input
- session and identity resolution
- Spark Intelligence orchestration
- Spark Researcher runtime
- optional Spark Swarm delegation
- result emission

Do not create channel-specific runtime forks.

### 4.2 One Governing Loop

Spark-style evolution logic should use one governing loop with conditional stages.

Do not split core intelligence behavior into disconnected:

- cron loops
- background loops
- adapter loops
- sidecar loops

If recurring work is really part of the agent flywheel, keep it in the governing loop model.

If it is true scheduled maintenance or delivery work, put it in the job harness.

### 4.3 One Scheduler

There is one scheduler for scheduled work.

Do not create:

- adapter-owned schedulers
- per-feature schedulers
- silent retry loops
- hidden watchdog timers that act like private schedulers

### 4.4 One Source Of Truth Per Concern

Examples:

- session identity: one canonical store
- job state: one canonical job store
- runtime config: one canonical config surface
- health and repair status: one canonical health surface

If two modules can disagree about the same state, the design is wrong.

### 4.5 Thin Adapters

Adapters should:

- translate external events into canonical runtime requests
- map runtime outputs back to the platform
- expose health and pairing state

Adapters should not:

- own scheduling
- own memory policy
- own deep business logic
- invent custom state machines unless the platform truly requires it

### 4.6 Orchestrate, Do Not Rebuild

Spark Intelligence should orchestrate:

- Spark Researcher
- Spark Swarm
- domain chips
- specialization paths
- the memory chip

Do not rebuild their internals here.

### 4.7 Live Telegram Safety

When working on the configured Telegram bot, protect live bot ownership before any test or runtime change.

Current operational rule:

- one bot token
- one active receiver
- all other terminals, tests, and workers stay behind that receiver

Canonical owner:

- repo: `spark-telegram-bot`
- mode is whatever the current gateway config declares
- do not assume webhook mode or polling mode from older docs; verify the active launch config first

Hard requirements before touching Telegram runtime behavior:

1. inspect the active gateway config through the supported CLI/status commands
2. confirm whether `spark-telegram-bot` is already running
3. confirm Telegram ownership without exposing or copying the bot token
4. if a webhook is active, do not start polling
5. if polling is the active posture, do not start any second Telegram receiver

Never:

- start the old `spark_intelligence` Telegram poller against the configured Telegram bot token
- start a second Telegraf or Telegram receiver with the same token
- re-enable webhook mode, tunnels, or alternate ingress paths unless explicitly coordinated
- point Spawner directly at Telegram
- “just start polling” because the bot looks unhealthy

If Telegram looks broken:

- stop and inspect first
- diagnose the canonical `spark-telegram-bot` process, launch config, and current ingress ownership
- keep all memory tests and Builder improvements behind the existing receiver instead of competing with it

## 5. State Rules

### 5.1 Local-First By Default

Prefer local, inspectable state in v1.

Good defaults:

- SQLite for structured local state
- plain config files
- structured logs
- explicit artifact directories

Avoid remote infrastructure unless it removes a proven bottleneck.

### 5.2 File-First Debuggability

An operator should be able to inspect the local machine and answer:

- what is configured
- what is running
- what last failed
- what job is due
- what agent identity maps to what channel

If basic debugging requires opening three dashboards, the design is too heavy.

### 5.3 Explicit State Transitions

Important lifecycle state must be explicit and enumerable.

Examples:

- adapter connected or disconnected
- job pending, running, failed, succeeded
- migration discovered, validated, activated, rejected
- session paired, unpaired, disabled

Do not hide lifecycle transitions in logs only.

## 6. Job And Cron Rules

### 6.1 Not Every Repeated Action Is A Cron Job

Before creating a scheduled job, ask:

- is this truly time-based
- is this event-driven
- is this part of the Spark governing loop
- can this happen inline after an existing action

If the answer is not truly time-based, do not make it cron-shaped.

### 6.2 Jobs Must Be Boring

Every scheduled job must have:

- a clear owner
- a canonical payload
- an idempotence rule
- a retry policy
- a visible status
- a manual run path
- a path that can run to completion without a custom daemon layer

### 6.3 No Private Retry Universes

Retries must flow through the shared harness.

Do not bury retries inside:

- adapters
- gateway edge handlers
- migration scripts
- model-provider wrappers

### 6.4 Doctor Before Guesswork

When the scheduler or jobs misbehave, the first-class answer should be diagnosis and repair:

- inspect health
- inspect due backlog
- inspect stale locks
- inspect last failure
- run a manual smoke-safe replay

Do not normalize rebooting random processes until the issue disappears.

### 6.5 Native Supervision Over Homegrown Process Tricks

Do not treat detached child processes, shell watchdog loops, or bundled daemon managers as the foundation of reliability.

Prefer:

- foreground commands
- run-to-completion commands
- OS-native scheduler or service registration only when necessary

If the system needs keep-running behavior, the native wrapper should call the same Spark Intelligence commands the operator can run manually.

## 7. Module Design Rules

### 7.1 Small Modules With Real Boundaries

Split modules when they have distinct ownership, not because abstraction feels elegant.

Good splits:

- scheduler store vs scheduler runner
- adapter translation vs adapter transport
- import discovery vs import validation

Bad splits:

- generic helpers used everywhere with unclear ownership
- multiple partial orchestrators
- wrappers around wrappers around wrappers

### 7.2 Stable Interfaces

Public module interfaces should be small and hard to misuse.

Prefer:

- explicit typed payloads
- narrow function signatures
- canonical request and result shapes

Avoid:

- giant mutable objects
- dict-shaped everything
- hidden side effects on import

### 7.3 No Clever Shared Magic

Do not rely on implicit global state, magical decorators, or framework tricks to wire critical behavior.

Critical runtime behavior should be readable in the code path.

## 8. Documentation Rules

### 8.1 Every System Needs Three Views

Each important subsystem should have:

- what it owns
- how it runs
- how it fails and how to debug it

### 8.2 Happy Path And Repair Path

Docs should always include:

- normal setup path
- health check path
- repair or doctor path

### 8.3 Avoid Narrative Lies

Documentation must reflect the real runtime, not the intended future architecture.

If the implementation and docs disagree, fix one immediately.

## 9. Security And Trust Boundary Rules

### 9.1 Minimize Trust Crossings

Every adapter, import pipeline, and tool call is a trust boundary.

Validate and normalize at the edge.

Do not let foreign state become canonical without validation.

### 9.2 Least Authority For Tools

Tool access should be explicit, auditable, and scoped.

Do not create broad default tool powers because they are convenient in demos.

### 9.3 Migration Is Hostile Until Proven Safe

Imported OpenClaw or Hermes data should be treated as foreign input.

Dry-run first.
Validate next.
Activate last.

## 10. Testing And Harness Rules

### 10.1 Smoke Tests First

Spark Intelligence should prioritize high-signal smoke coverage for:

- install
- setup
- doctor
- gateway start
- adapter handshake
- scheduler start
- due job execution
- retry behavior
- migration dry-run

### 10.2 Test The Real Path

Prefer tests that hit the real production code path over tests that only validate helpers.

### 10.3 Failures Must Be Legible

Tests should fail with evidence that points to:

- ownership
- state
- last transition
- expected repair path

## 11. Product And Ecosystem Rules

### 11.1 Spark Intelligence Is The Shell, Not The Whole Brain

This repo owns the persistent-agent shell and orchestration layer.

It does not own all intelligence behavior.

### 11.2 Lego Pieces, Not Repo Sprawl

A modular ecosystem is good when each piece owns a real concern.

A modular ecosystem is bad when pieces overlap and compete.

The test is simple:

- can a new engineer say what this module owns in one sentence
- can an operator tell where a bug belongs
- can a user benefit without understanding the internals

### 11.3 Retention Beats Surface Area

Prefer changes that make the persistent agent more dependable, coherent, and useful over changes that merely add more knobs or channels.

## 12. Code Review Checklist

Before approving a change, ask:

1. Did this create a second path for an existing responsibility?
2. Did this keep ownership boundaries clear?
3. Did this make state more inspectable or less inspectable?
4. Did this add a hidden loop, timer, or retry path?
5. Did this preserve local-first debuggability?
6. Did this improve or weaken doctorability?
7. Did this add docs and smoke coverage where the operator needs them?

## 13. Anti-Patterns To Reject

- channel-specific forks in the runtime core
- multiple schedulers
- background threads with private state
- fake cron jobs for non-time-based behavior
- duplicated identity stores
- duplicated migration logic
- “temporary” sidecars that become permanent
- abstractions that make the real runtime harder to follow
- dashboard-first operations without local inspection paths
- documentation that hides failure modes

## 14. Final Rule

If a proposed change makes the codebase feel smarter but less legible, prefer the more legible design.

For Spark Intelligence v1, clarity is a feature.
