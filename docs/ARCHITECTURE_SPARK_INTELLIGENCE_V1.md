# Spark Intelligence v1 Architecture

## 1. Purpose

This document defines the v1 architecture for `Spark Intelligence`.

The goal is not to build a generic chat assistant. The goal is to build a Spark-native persistent agent system where:

- one user has one durable agent identity
- that agent can be reached across channels
- the runtime core stays inside the Spark ecosystem
- specialization comes from domain chips and specialization paths
- hard tasks escalate through Spark Swarm
- memory is integrated, but owned by the separate memory domain chip

This document also captures which patterns we should borrow from `OpenClaw` and `Hermes Agent`, and which parts should remain uniquely Spark-native.

## 2. Design Goals

### 2.1 Persistent Agent Identity

Every user should feel they are talking to the same agent across:

- Telegram
- WhatsApp
- Discord
- future surfaces

### 2.2 Spark-Native Runtime

The intelligence core must be `Spark Researcher`, not a separate generic assistant runtime.

### 2.3 Modular Specialization

The agent should gain skill through:

- domain chips
- specialization paths
- autoloop flywheels

### 2.4 Multi-Channel Delivery

Messaging surfaces should be treated as transport adapters, not independent products.

### 2.5 Operator Trust

The system should expose enough state, routing, and execution visibility for the operator to trust it.

### 2.6 Lightweight Core

The v1 system should stay as lightweight as possible.

That means:

- one main runtime process where possible
- thin channel adapters
- minimal persistence surface
- minimal always-on background jobs
- no extra service unless it solves a real v1 problem

### 2.7 Maintainability First

The system should be easy to reason about, easy to debug, and cheap to evolve.

That means:

- clear subsystem boundaries
- reuse Spark systems instead of rebuilding them here
- prefer stable interfaces over deep coupling
- keep platform-specific logic isolated
- delay complexity until real load proves it is needed

## 3. Architectural Thesis

Spark Intelligence should combine:

- the `single gateway control plane` shape of OpenClaw
- the `clean CLI + setup + gateway separation` shape of Hermes
- the `runtime core + swarm + chip ecosystem` that is unique to Spark

So the architecture should be:

`channels -> Spark Intelligence Gateway -> Spark Runtime Core -> Spark Swarm / Domain Chips / Specialization Paths`

not:

`channels -> generic bot -> some plugins`

And not:

`channels -> too many services -> too many sync points -> too much maintenance`

## 4. High-Level System

```text
Telegram / WhatsApp / Discord / Web
                |
                v
      Spark Intelligence Gateway
                |
                +--------------------+
                |                    |
                v                    v
    Session / Identity Plane     Operator Control Plane
                |                    |
                +----------+---------+
                           |
                           v
                Spark Runtime Orchestrator
                           |
          +----------------+----------------+
          |                                 |
          v                                 v
  Spark Researcher Core               Spark Swarm
          |                                 |
          +----------------+----------------+
                           |
                           v
               Specialization Router
                           |
       +-------------------+-------------------+
       |                   |                   |
       v                   v                   v
  Domain Chips      Specialization Paths   Autoloop Hooks
                           |
                           v
                    Memory Chip Boundary
```

## 5. Primary Subsystems

### 5.1 Spark Intelligence Gateway

This is the always-on entrypoint for external surfaces.

Responsibilities:

- receive inbound messages from channels
- normalize channel events into one internal message format
- maintain connection state with adapters
- route messages to the correct persistent agent identity
- send outbound replies, notifications, and media
- enforce pairing, allowlists, and delivery policy

Why this exists:

- OpenClaw is right that one gateway should be the control plane for channels, sessions, and routing
- Hermes is right that messaging should be isolated as a gateway subsystem

Maintainability rule:

the gateway should own transport and routing, not domain intelligence.

### 5.2 Session and Identity Plane

This subsystem maps:

- user
- channel account
- peer/thread/chat
- Spark agent identity

Core rule:

one human should map to one persistent Spark agent identity, even if they message through different surfaces.

Responsibilities:

- cross-channel identity resolution
- session continuity rules
- per-channel chat mapping
- sender authorization and pairing
- home channel selection

Maintainability rule:

identity mapping should be stored in one canonical place, not reimplemented in each adapter.

### 5.3 Spark Runtime Orchestrator

This is the main internal coordinator.

Responsibilities:

- accept normalized user requests
- construct runtime context
- invoke Spark Researcher
- escalate to Spark Swarm when needed
- route to domain chips and specialization paths
- return structured outputs back to the gateway

This is the central system-level layer that turns "a message came in" into "the Spark ecosystem did the right work."

Lightweight rule:

this layer should orchestrate existing Spark systems, not duplicate their internal logic.

### 5.4 Spark Researcher Core

This is the main agent runtime.

Responsibilities:

- reasoning loop
- task planning
- execution coordination
- tool calls
- output synthesis
- user-facing continuity

Spark Intelligence should not replace this. It should package and expose it.

### 5.5 Spark Swarm Integration Layer

This layer escalates the main agent into coordinated multi-agent work.

Responsibilities:

- spawn or route specialist sub-agents
- parallelize deep tasks
- merge results back into the main session
- maintain lineage between user request and swarm output

Design rule:

the user still experiences one persistent agent, even when swarm work happens underneath.

### 5.6 Specialization Router

This layer decides which intelligence surfaces are active.

Responsibilities:

- attach or prioritize domain chips
- map user profile to specialization path
- choose whether a request stays general or enters a specialist mode
- expose the active specialization set to the runtime

### 5.7 Domain Chip Integration Layer

Domain chips should not be hardcoded into the base runtime.

Responsibilities:

- load specialization surfaces
- expose chip metadata and capability boundaries
- contribute heuristics, workflows, and constraints
- support attachment, detachment, and versioning

Maintainability rule:

chips should integrate through a narrow contract so chip repos can evolve independently.

### 5.8 Specialization Path Engine

This engine governs long-term agent growth.

Responsibilities:

- track the user's chosen growth path
- decide which chips or capabilities deepen next
- prevent random capability sprawl
- produce a coherent progression model

### 5.9 Autoloop Flywheel Hooks

This layer captures what repeated use should improve.

Responsibilities:

- observe recurrent tasks
- trigger improvement workflows
- generate future specialization proposals
- route learnings into allowed Spark surfaces

### 5.10 Memory Chip Boundary

This is an integration boundary, not an ownership surface for this repo.

Spark Intelligence depends on the memory system but should not own:

- memory promotion rules
- retrieval doctrine
- memory hygiene policy
- contradiction handling logic

This repo should define:

- where memory is called
- what interfaces it needs
- how runtime sessions request memory services

## 6. Internal Data Flow

### 6.1 Standard Request Flow

1. User sends a message on Telegram, WhatsApp, Discord, or another adapter.
2. Channel adapter converts it into a normalized event.
3. Gateway resolves the sender to a Spark agent identity.
4. Session plane loads the current session and channel context.
5. Runtime orchestrator builds the execution request.
6. Spark Researcher handles the base reasoning loop.
7. If needed, Spark Swarm is invoked for deeper or parallel work.
8. Specialization router attaches relevant domain chips and path context.
9. Memory chip interfaces are called where needed.
10. Final output is returned to the gateway.
11. Gateway formats and delivers the reply back through the original channel.

### 6.2 Escalation Flow

If the main agent detects the task requires specialist depth:

1. Runtime orchestrator calls Spark Swarm.
2. Swarm fans out work to specialist agents or execution lanes.
3. Results are merged back into the primary session.
4. The user still receives a unified answer from their persistent agent.

### 6.3 Evolution Flow

Over time:

1. Autoloop hooks observe repeated work.
2. Specialization path engine updates growth direction.
3. New chips or deeper variants become active.
4. The user's agent gets sharper without becoming fragmented.

## 7. Channel Adapter Architecture

### 7.1 Adapter Model

Each adapter should implement a shared interface:

- connect
- authenticate
- receive inbound event
- send outbound message
- send media
- report health
- expose sender and thread metadata

### 7.2 Supported v1 Adapters

Recommended priority:

1. Telegram
2. Discord
3. WhatsApp

Rationale:

- Telegram is usually the fastest bot setup
- Discord is useful for power users and teams
- WhatsApp is high-value but pairing and session handling are more operationally sensitive

### 7.3 Adapter Responsibilities

Each adapter should be thin.

Adapters should own:

- platform auth tokens
- event translation
- media normalization
- channel-specific delivery quirks

Adapters should not own:

- business logic
- specialization logic
- runtime orchestration
- memory policy

Lightweight rule:

every new adapter should be mostly translation glue, not a new runtime.

## 8. Onboarding Architecture

### 8.1 Recommended Path

The primary onboarding experience should be CLI-first.

Recommended commands:

- `spark-intelligence setup`
- `spark-intelligence channel add telegram`
- `spark-intelligence channel add discord`
- `spark-intelligence channel add whatsapp`
- `spark-intelligence gateway start`
- `spark-intelligence doctor`

Keep the initial CLI small.

v1 should avoid a huge command surface. The first commands should cover:

- setup
- channel add and remove
- gateway start and status
- doctor
- pairing and auth
- basic runtime inspection

### 8.2 Why CLI-First

This is one of the strongest patterns to borrow from Hermes and OpenClaw.

Reasons:

- works locally, remotely, and on servers
- works well for power users
- supports non-interactive automation later
- keeps the setup flow explicit and debuggable

### 8.3 What Onboarding Should Configure

The onboarding wizard should configure:

- model/provider selection
- Spark runtime linkage
- workspace location
- gateway port and auth
- initial persistent agent identity
- initial domain chip set
- initial specialization path
- channel adapters
- daemon/service install

### 8.4 Spark-Specific Onboarding Step

This is where Spark must differ.

The user should also choose:

- which Spark agent core they are instantiating
- which specialization path they want first
- which domain chips are active at day one

OpenClaw and Hermes onboard channels and runtime.
Spark Intelligence must onboard evolution.

## 9. Operator Control Plane

Spark Intelligence should have an operator surface, but not as the primary user product.

Responsibilities:

- view active agents
- inspect current session and channel mappings
- inspect active chips and specialization path
- inspect swarm escalations
- inspect gateway health
- manage pairing and allowlists
- restart or reconfigure adapters

This can begin as CLI plus minimal local web UI.

Maintainability rule:

the operator plane should be generated from the same underlying runtime state, not from a separate shadow state model.

## 10. Security Model

### 10.1 Default Posture

All inbound messaging surfaces should be treated as untrusted.

### 10.2 Pairing and Allowlists

We should borrow this heavily from OpenClaw and Hermes.

Defaults:

- unknown inbound users do not get full runtime access
- pairing or explicit allowlist approval is required
- platform-specific allowlists override broader defaults

### 10.3 Dangerous Actions

Destructive or sensitive actions should require approval policies or scoped execution rules.

This should integrate with Spark runtime governance rather than living only in the adapter layer.

### 10.4 Identity Boundaries

One user should not accidentally inherit another user's context simply because they are in the same Discord server or group thread.

The architecture should default to per-user isolation unless a room-style shared mode is explicitly enabled.

## 11. Storage Model

### 11.1 What This Repo Owns

Spark Intelligence should own storage for:

- gateway config
- adapter credentials references
- session and identity mappings
- channel routing state
- specialization attachment state
- operator-facing runtime metadata

Recommended v1 default:

- local-first config files for static config
- a lightweight relational store for runtime metadata and mappings

For v1, prefer `SQLite` unless a real deployment mode requires something heavier.

### 11.2 What This Repo Does Not Own

This repo should not become the canonical store for all memory intelligence.

That belongs to the memory chip and related Spark systems.

## 12. Runtime Boundaries

### 12.1 What Lives Inside Spark Intelligence

- gateway
- channel adapters
- identity/session routing
- Spark runtime orchestration
- chip/path attachment logic
- operator control surfaces

### 12.2 What Lives Outside Spark Intelligence

- core memory doctrine
- independent domain chip repos
- standalone Spark Researcher internals outside exposed interfaces
- standalone Spark Swarm internals outside exposed interfaces

## 13. Recommended v1 Technical Shape

### 13.1 Services

v1 should start with one main process plus clean internal modules:

- gateway runtime
- adapter modules
- orchestrator
- Spark integration clients
- operator UI or CLI layer

If complexity grows, later split into:

- gateway service
- runtime orchestrator service
- operator UI service

Do not start there.

The default posture should be:

- one deployable runtime
- one clear module tree
- one canonical config model
- one canonical session and identity model

### 13.2 Control Plane

We should adopt the OpenClaw instinct of one control plane for:

- sessions
- routing
- channel connections
- operator tools

But it should remain Spark-shaped rather than Gateway-first product branding.

### 13.3 Core Reuse Policy

Before building a new subsystem in this repo, ask:

1. Can Spark Researcher already do this?
2. Can Spark Swarm already do this?
3. Can a domain chip or specialization path own this?
4. Is this only transport glue and therefore belongs in an adapter?

If the answer is yes to any of those, do not rebuild it inside Spark Intelligence.

### 13.4 Internal Protocol

We should likely use a structured event/request model internally for:

- inbound messages
- runtime requests
- swarm escalations
- operator events
- adapter health

This is a pattern worth borrowing directly from modern gateway systems.

### 13.5 Adapter Budget

To keep the system maintainable, v1 should support only a small number of first-class adapters.

Recommended sequence:

1. Telegram
2. Discord
3. WhatsApp

Do not start by supporting every surface that OpenClaw supports.

### 13.6 Complexity Budget

The architecture should explicitly reject:

- microservices by default
- multiple sources of truth for session state
- channel-specific business logic forks
- runtime duplication between Spark Intelligence and Spark Researcher
- memory logic duplicated outside the memory chip
- feature sprawl in the CLI before the runtime is stable

## 14. Borrow / Yoink / Build Ourselves

### 14.1 Borrow From OpenClaw

- one always-on gateway as the control plane
- onboarding wizard as the main setup path
- daemon/service install as part of onboarding
- multi-channel architecture with one source of truth
- pairing and per-channel DM safety defaults
- optional dashboard for operator visibility

### 14.2 Borrow From Hermes

- clean CLI command surface
- separate gateway subsystem
- explicit config and doctor flows
- platform-specific setup guides and env/config separation
- session isolation thinking for Discord and group contexts
- channel-specific operational docs

### 14.3 Yoink Directly As Patterns

These are strong enough that we should intentionally copy the shape:

- `setup -> configure -> start gateway -> doctor`
- adapter-per-platform architecture
- explicit allowlists and pairing flow
- service installation for always-on messaging runtime
- one shared identity/session mapping layer

What not to yoink:

- OpenClaw's full channel surface area
- any architecture that makes the gateway more important than the Spark runtime
- any pattern that increases maintenance cost without improving the v1 wedge

### 14.4 Keep Uniquely Spark

- Spark Researcher as the core runtime
- Spark Swarm as the deep execution layer
- domain chips as the specialization surface
- specialization paths as progression logic
- autoloop flywheels as improvement logic
- memory chip as a separate integrated intelligence layer

This is the actual moat. If we lose this, we are only rebuilding a messaging wrapper.

## 15. Recommended v1 Package Layout

```text
spark-intelligence-builder/
|- docs/
|  |- PRD_SPARK_INTELLIGENCE_V1.md
|  |- ARCHITECTURE_SPARK_INTELLIGENCE_V1.md
|  `- CHANNEL_ADAPTER_SPEC_V1.md
|- src/
|  `- spark_intelligence/
|     |- cli/
|     |- gateway/
|     |- adapters/
|     |- identity/
|     |- orchestrator/
|     |- runtime/
|     |- swarm/
|     |- chips/
|     |- paths/
|     |- flywheels/
|     |- control_plane/
|     `- config/
`- tests/
```

## 16. Immediate Next Specs

The next docs after this should be:

1. channel adapter spec
2. onboarding CLI spec
3. identity and session model spec
4. Spark Researcher integration contract
5. Spark Swarm escalation contract
6. domain chip attachment contract

## 17. Final Architectural Decision

Spark Intelligence should be:

- one persistent agent product
- one gateway/control plane
- one Spark-native runtime identity
- many attached specialization systems
- as lightweight as possible in v1
- as maintainable as possible over time

The user talks to one agent.
The Spark ecosystem is what makes that agent powerful.
