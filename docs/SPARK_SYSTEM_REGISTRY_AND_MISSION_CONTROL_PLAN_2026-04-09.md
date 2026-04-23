# Spark System Registry And Mission Control Plan

## Purpose

This document defines the next major Spark subsystem after the recent Telegram, voice, and self-knowledge fixes.

The problem is no longer just "answer quality." Spark needs a durable runtime model of:

- what systems exist around it
- what chips, tools, adapters, and bridges are attached
- what is active versus merely installed
- what it can do right now
- which subsystem should handle a task
- what is unavailable, degraded, or gated

Without that, Spark stays partially blind. It may answer honestly, but it cannot operate like a serious modular system.

## Spark's Design Constraint

Spark is not supposed to become one giant repo that does everything badly.

Spark's differentiator is:

- a thin runtime owner in Builder
- strong attachment and bridge contracts
- modular domain chips
- separate specialization systems that can improve independently
- one coherent operator-facing intelligence surface over that modular graph

That means the right move is not to turn Builder into Paperclip, Hermes, or Spawner UI.

The right move is:

- Builder owns the runtime registry, mission summary, routing, and operator contract
- chips own specialized capabilities
- external systems can be bridged in without collapsing boundaries
- orchestration layers can stay optional and composable

## What We Can Learn Without Copying

### OpenClaw

Useful patterns:

- explicit plugin inventory and enable/disable surfaces
- clear distinction between discovered, enabled, and applied config
- strong channel-specific contracts
- practical gateway/session discipline

What Spark should take:

- inventory truthfulness
- explicit state categories
- channel-specific media/runtime contracts
- plugin and extension discovery that does not depend on memory or folklore

What Spark should not copy:

- a plugin-first identity where everything important becomes an extension before the runtime has a strong world model

### Hermes

Useful patterns:

- toolsets as composable capability bundles
- many platform adapters under one gateway
- multiple execution backends
- memory, cron, delegation, and cross-platform surfaces treated as first-class capabilities

What Spark should take:

- capability grouping
- platform-aware tool exposure
- backend abstraction for terminals and models
- clear distinction between tool inventory and tool availability

What Spark should not copy:

- a flat "all tools everywhere" mental model in Builder

Spark should expose capability packs and routing guidance, not just a long list of raw tools.

### Paperclip

Useful patterns:

- control-plane thinking
- explicit governance and approval concepts
- task hierarchy tied back to mission
- orchestration and heartbeat logic separated from the agent runtime

What Spark should take:

- mission-control concepts as a separate plane
- task-to-goal ancestry
- resumable execution state
- auditability and governance around automation

What Spark should not copy:

- making Builder into a company-orchestration product

Spark needs mission control for one intelligent modular runtime, not a whole org-chart operating system.

### Spawner UI

Useful patterns:

- visual orchestration and progressive disclosure
- explicit active-mission state
- resumability and checkpointing
- orchestration surfaces separated from execution bridges

What Spark should take:

- active mission snapshots
- resumable state
- explicit execution packs
- event-sourced progress surfaces

What Spark should not copy:

- pushing Builder into a canvas-first orchestration app

Spawner UI can remain a strong optional mission-control or orchestration surface if needed later.

## Core Spark Thesis

Spark should know itself through a real registry, not by improvising from names in prompt context.

The runtime must be able to answer:

- What systems are part of Spark?
- What chips are attached?
- Which chips are active right now?
- What tools are available right now?
- Which tasks should stay in Builder?
- Which tasks should escalate to Researcher or Swarm?
- Which tools require approval, pairing, or stronger trust?
- What is broken or degraded?
- What can I automate?
- What execution backends exist for this machine?

## The Three New Core Subsystems

## 1. System Registry

This is the canonical source of truth.

Builder should own a real registry module, not scattered prompt strings.

Suggested records:

- `systems`
  - Builder
  - Researcher
  - Swarm
  - Browser
  - Voice
  - Memory
  - Automation
  - Mission Control
- `chips`
  - every attached chip
  - every path attachment
- `tools`
  - tool families, not only individual tool names
- `bridges`
  - Telegram bridge
  - Researcher bridge
  - voice bridge
  - browser bridge
  - future terminal/model bridges
- `backends`
  - provider backends
  - terminal backends
  - STT/TTS backends
  - orchestration backends

Each registry record should carry fields like:

- `id`
- `kind`
- `label`
- `role`
- `description`
- `status`
- `attached`
- `active`
- `pinned`
- `available`
- `degraded`
- `requires_restart`
- `capabilities`
- `limitations`
- `repo_root`
- `hook_names`
- `trust_class`
- `last_used_at`
- `health`
- `operator_notes`

This registry should power both:

- Spark's internal self-knowledge
- operator introspection surfaces

## 2. Mission Control

Mission control is not "everything orchestration."

For Spark, mission control should mean:

- what Spark is doing now
- what systems are available
- what tasks are active
- what automations exist
- what execution loops are running
- what needs approval
- what failed and why
- what should handle the next request

Mission control should have two views:

- `runtime mission summary`
  - compact, safe to inject into normal replies when relevant
- `operator mission report`
  - fuller detail for explicit introspection and management

Mission control should answer:

- `What can you do right now?`
- `What are you connected to?`
- `What systems are active?`
- `What is degraded?`
- `What should handle this task?`
- `What loops are running?`
- `What automations are configured?`

## 3. Harness Layer

Harnesses are critical.

The failure mode the user called out is real: an agent can have tools and still fail to actually get work done.

Spark needs execution harnesses that translate intent into reliable work.

A harness should define:

- how a task starts
- how context is prepared
- what capability pack is available
- what backend executes the task
- what progress events exist
- what completion looks like
- what artifacts are expected
- how retry/resume works
- what safety gates apply

Spark should treat harnesses as explicit execution contracts, not hidden glue.

Suggested harness families:

- `chat_harness`
  - default conversational work
- `research_harness`
  - evidence-backed retrieval/research tasks
- `automation_harness`
  - scheduled or event-driven tasks
- `swarm_harness`
  - multi-node or multi-agent escalation
- `tool_harness`
  - bounded tool-driven execution
- `terminal_harness`
  - shell/workspace operations
- `browser_harness`
  - browser automation and QA
- `voice_harness`
  - STT/TTS and speech interactions

This keeps Spark from becoming vague about "using tools." It should know which harness governs the task.

## Capability Routing

Builder should add a real task-to-system router.

This router should classify requests into something like:

- stay in Builder only
- use Researcher through the bridge
- escalate to Swarm
- invoke browser tools
- invoke voice tools
- invoke terminal backend
- invoke automation path
- invoke a chip-specific path

Routing should depend on:

- task shape
- required outputs
- available tools
- attached chips
- trust/safety class
- current system health
- operator preferences

The router should return:

- chosen subsystem
- chosen harness
- chosen capability pack
- reason
- fallback if unavailable

## Capability Packs

Hermes is right about grouped tools. Spark should do this in its own language.

Spark should prefer capability packs over giant raw tool lists.

Examples:

- `research_pack`
- `builder_pack`
- `browser_pack`
- `voice_pack`
- `automation_pack`
- `terminal_pack`
- `swarm_pack`
- `memory_pack`
- `ops_pack`

These packs can map to:

- tool families
- chips
- bridges
- backends
- approval rules

This keeps Builder readable and prevents prompt/context overload.

## Automation And Task Layer

Spark needs more than introspection. It needs a clean automation/task model.

But this should not immediately become a Paperclip-scale orchestration platform.

Spark should start with:

- task records
- active run records
- resumable runs
- scheduled automations
- event-triggered automations
- checkpoint/savepoint semantics
- execution artifacts
- run outcome summaries

Suggested entities:

- `task_definition`
- `task_run`
- `automation_rule`
- `active_loop`
- `checkpoint`
- `artifact`
- `approval_gate`

This is enough to support:

- autoloops
- normal task management
- reporting
- resumability
- future mission-control UIs

## Self-Evolution, But Governed

Spark should improve itself, but not by mutating blindly.

Self-improvement should operate through bounded mutation surfaces:

- style/persona rules
- routing heuristics
- tool preference order
- capability recommendations
- task templates
- automation recipes

Governance rules:

- proposed change
- before/after preview
- validation or replay
- operator approval where needed
- rollback
- provenance log

Spark should learn:

- what systems solved which tasks
- where failures happen
- which harnesses stall
- which chips are actually useful
- where tools are missing

That is how it becomes more intelligent operationally without turning into ungoverned prompt drift.

## Tool And Chip Onboarding Contract

New modular systems should be easy to add.

Every new chip, bridge, adapter, or tool integration should declare:

- what it is
- what it can do
- what surfaces can call it
- required permissions
- trust class
- health checks
- expected artifacts
- example intents
- whether it is interactive, batch, or autonomous

This contract is what will let Spark understand:

- a new browser system
- a new Playwright-style surface
- a terminal backend
- a new memory chip
- a custom local tool on a user's machine

without requiring hand-authored prompt folklore every time.

## What Must Stay Out Of Builder Core

To avoid spaghetti:

- full visual orchestration UI should stay optional
- company/org-chart management should stay outside Builder
- heavy multi-agent planning surfaces should stay in Swarm or external orchestration layers
- chip-specific business logic should stay in chips
- raw backend-specific hacks should stay behind adapter boundaries

Builder core should own:

- registry
- routing
- mission summary
- harness contracts
- approval and state model
- operator introspection

## Recommended Implementation Order

### Phase 1: Registry Foundation

Ship first:

- `system_registry.py`
- canonical registry record schemas
- chip/system/tool/backend discovery adapters
- runtime health snapshot
- explicit `pinned` vs `active` vs `attached` vs `available`

Operator questions unlocked:

- `What are you connected to?`
- `What systems are active?`
- `What can you do right now?`

### Phase 2: Mission Summary

Ship next:

- `mission_control.py`
- compact runtime summary
- operator report surface
- Telegram/NL introspection for mission state

Operator questions unlocked:

- `What are you doing now?`
- `What is degraded?`
- `What loops are running?`

### Phase 3: Capability Router

Ship next:

- task classification
- subsystem selection
- harness selection
- capability pack selection
- reasoned route summaries

Operator questions unlocked:

- `What system should handle this?`
- `Should this stay in Builder or go to Swarm?`

### Phase 4: Harness Contracts

Ship next:

- explicit harness definitions
- resumability and progress events
- approval and failure semantics
- artifact expectations

Operator outcome:

- better execution reliability
- less tool flailing
- clearer automation behavior

### Phase 5: Automation And Loops

Ship next:

- scheduled tasks
- event-triggered tasks
- checkpointing
- active loop status
- bounded autoloops

### Phase 6: Governed Self-Evolution

Ship after the above:

- mutation proposals
- replay/validation
- rollback
- confidence and provenance

## Immediate Repo-Level Recommendation

The next implementation tranche should start in Builder with:

1. `src/spark_intelligence/system_registry/`
2. `src/spark_intelligence/mission_control/`
3. registry-backed Telegram/NL introspection
4. routing helpers that consume registry facts instead of ad hoc prompt text

Do not start by building:

- a canvas UI
- a giant task manager
- full Swarm orchestration inside Builder
- generic plugin complexity for its own sake

## Success Criteria

Spark is in a good state when it can answer, truthfully and specifically:

- what it is
- what systems it has
- what chips are attached
- what tools and backends exist
- what it can do right now
- what it cannot do right now
- which system should handle the task
- what automation paths are available
- how to recover from a degraded system

And operationally:

- new chips can be onboarded through a clean contract
- Builder does not become crowded with chip-specific logic
- orchestration can grow without collapsing modularity
- harnesses make execution more reliable than simple prompt-and-tool improvisation

## Current Recommendation

Proceed with a Builder-native system registry and mission-control foundation first.

That is the right Spark move:

- closer to OpenClaw's explicit inventory discipline
- informed by Hermes' toolset and backend structure
- aware of Paperclip's control-plane strengths
- informed by Spawner UI's active-mission and resumability ideas
- still unmistakably Spark: modular, bridged, chip-centric, and evolvable without becoming one giant monolith
