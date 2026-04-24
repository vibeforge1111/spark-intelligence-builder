# Spark Intelligence System Connection And Productization Plan 2026-03-26

## 1. Purpose

This plan answers two practical questions:

1. How do we connect the real Spark systems step by step from the current working Telegram agent shell?
2. What still has to exist in Spark Intelligence Builder before another operator can install it, connect it, and use it without local tribal knowledge?

This is a connection and productization plan, not only a code roadmap.

The first supporting runtime surface for this plan is `spark-intelligence connect status`, which reports the current phase, the current blocker, and the next command to run from the live home.

## 2. Current Reality

These things were real on the canonical live home `.tmp-home-live-telegram-real` during the March validation pass. This is historical status, not a requirement to use one specific LLM provider:

- Telegram DM ingress is live against the configured BotFather bot
- the gateway can run continuously in foreground mode
- the Spark Researcher bridge is connected to the local `spark-researcher` runtime
- one OpenAI-compatible provider is connected through the `custom` direct provider path
- provider runtime and provider execution readiness are both green
- pairing, operator controls, traces, outbound audit, and recovery surfaces are real
- Telegram conversational fallback is live for under-supported small-talk
- Telegram think-block visibility is controlled per DM with `/think`, `/think on`, and `/think off`

These things are wired but not yet truly in the Telegram reply loop:

- Spark Swarm is payload-ready but not API-connected
- domain chips are discovered but none are active
- specialization paths are discovered but none are active
- Codex OAuth exists as an auth architecture path, but it is not the current stable live provider
- Discord and WhatsApp have narrow secure ingress contracts, but they are not the current priority path

## 3. What “Connected To Everything” Should Mean

Spark Intelligence should eventually be able to do all of this from one installed workspace:

- receive user input through a real gateway surface
- route it through identity, pairing, and operator policy
- use Spark Researcher as the first runtime core
- activate domain chips and specialization paths that shape the behavior
- escalate selected tasks into Spark Swarm when multi-agent coordination is justified
- use one stable provider/auth layer for model execution
- expose clear operator recovery paths when any of those dependencies fail

That means “connected” is not just “a token exists.”

It means the bot can actually use the system boundaries intentionally and safely.

## 4. Step-By-Step Connection Plan

Do this in order.

### Phase A. Lock The Current Telegram Core

Goal:

- keep the current Telegram plus Researcher plus provider path stable while everything else is connected around it

Required outcomes:

- the canonical Telegram home stays green
- the continuous gateway remains the default operating mode
- token rotation and provider-failure drills remain recoverable
- `/think` behavior stays stable and documented

Do not expand adapter breadth during this phase.

### Phase B. Activate Real Spark Specialization

Goal:

- move from “generic Telegram agent shell” to “Telegram agent shaped by our actual Spark chips and paths”

Required work:

1. choose the first real active chip set
2. choose one active specialization path
3. verify `attachments snapshot`, `agent inspect`, and `researcher status` reflect that active state
4. prove one Telegram message that is materially different because chips/path are active

Recommended first activation:

- one startup/operator-oriented path
- one content or strategy chip set

Definition of done:

- `status` no longer shows `active chips: none`
- `status` no longer shows `active path: none`
- the Telegram bot behaves differently because of those active assets, not only because of the provider

### Phase C. Connect Real Spark Swarm

Goal:

- make Swarm actually reachable instead of only payload-ready

Required work:

1. configure:
   - `swarm-api-url`
   - `workspace-id`
   - `access-token`
2. verify `swarm status` shows:
   - `enabled: yes`
   - `payload_ready: yes`
   - `api_ready: yes`
3. run:
   - `spark-intelligence swarm sync`
   - `spark-intelligence swarm evaluate <task>`
4. define the escalation policy from Telegram/Researcher into Swarm

Important design rule:

- Telegram should not blindly invoke Swarm on every message
- escalation should be explicit and auditable

Definition of done:

- Swarm is not only configured, but proven reachable
- one Telegram-originated task can be escalated to Swarm intentionally

### Phase D. Lock The Runtime Routing Contract

Goal:

- define exactly when a Telegram request stays in Researcher, when it uses direct provider fallback, and when it escalates to Swarm

Required contract:

- casual or low-evidence conversational traffic:
  - may use direct provider fallback
- evidence-backed or domain-guided traffic:
  - should stay in Researcher-first mode
- multi-agent or orchestration-worthy traffic:
  - may escalate to Swarm
- failures:
  - must remain fail-closed and operator-visible

Definition of done:

- the runtime path is explainable from one decision table
- traces and operator surfaces make the decision observable

### Phase E. Productize Setup And Installer

Goal:

- make another operator able to install and run Spark Intelligence without custom manual assembly

Required work:

1. one canonical install flow
2. one canonical home layout
3. one canonical `.env` contract
4. one setup command that detects or asks for:
   - researcher runtime
   - swarm runtime or hosted Swarm settings
   - provider/auth choice
   - Telegram onboarding
5. one “first successful message” checklist

Definition of done:

- a new operator can clone, install, run setup, connect one provider, connect one channel, and get one reply without hidden steps

## 5. Remaining Gaps By Area

### Gateway

What exists:

- real Telegram long-poll runtime
- secure narrow Discord and WhatsApp ingress boundaries
- traces, outbound audit, repair hints, and fail-closed startup

What is still missing:

- a first-class long-running service story instead of “run the foreground CLI”
- startup management for Windows and later Linux/macOS service mode
- one canonical production run command and service wrapper
- cleaner runtime lifecycle docs for restart, stop, log tail, and health checks
- a unified ingress policy table across Telegram, Discord, and WhatsApp

### Installer And Setup

What exists:

- `setup`
- repo autodiscovery for local Spark repos
- local home bootstrap

What is still missing:

- a true “anyone can install this” installer flow
- setup that clearly distinguishes:
  - local-only mode
  - Telegram-first mode
  - hosted-Swarm-connected mode
- setup verification that proves each dependency, not only writes config
- a one-shot bootstrap that can finish the first working channel/provider path
- docs that treat one canonical home as the supported default

### Provider And Auth

What exists:

- provider registry
- API-key and OAuth support
- refresh/logout/callback flows
- fail-closed readiness

What is still missing:

- clearer first-class provider support beyond `custom`
- one supported default production provider story
- a decision on whether Codex OAuth is production-critical or optional
- operator-facing provider rotation drills documented for the canonical home
- more live validation for non-MiniMax provider paths

### Researcher Integration

What exists:

- real Researcher bridge
- provider-backed reply path
- conversational fallback for under-supported Telegram small-talk

What is still missing:

- a cleaner distinction between:
  - conversational assistant behavior
  - evidence-backed advisory behavior
  - research-needed behavior
- explicit Researcher policy for when to ask clarifying questions vs answer directly
- a documented runtime decision table shared with Swarm escalation

### Swarm Integration

What exists:

- payload generation
- status/configure/sync/evaluate surfaces

What is still missing:

- real API connection
- live sync proof
- live escalation proof from Spark Intelligence
- task-selection policy for escalation
- operator visibility for Swarm participation in Telegram-originated tasks

### Chips And Paths

What exists:

- attachment discovery
- attachment snapshot
- active/pinned/path state plumbing

What is still missing:

- one supported default chip profile
- one supported default specialization path
- proof that those assets materially shape Telegram behavior
- docs for “recommended activation sets” by use case

### Telegram Product Surface

What exists:

- live bot
- pairing and moderation
- continuous gateway option
- `/think` controls

What is still missing:

- a clean production runbook for always-on operation
- one operator-friendly command to boot the full Telegram stack
- persistent-service guidance
- clearer user-facing behavior for conversational vs research-style replies

### Discord And WhatsApp

What exists:

- secure ingress skeletons
- readiness surfaces

What is still missing:

- they are not in the product-critical path yet
- no broad live runtime validation comparable to Telegram
- no reason to prioritize them before the Spark core systems are actually connected

## 6. What Must Exist Before “Anyone Can Use Spark Intelligence”

This is the real productization bar:

1. Install
   - one clear install command
   - one supported Python/runtime version story

2. Setup
   - one setup path that gets a real provider and one real channel connected

3. Run
   - one command or service wrapper that starts the gateway continuously

4. Recover
   - one operator can diagnose auth, provider, channel, and bridge failures from built-in surfaces

5. Specialize
   - one supported way to activate chips and one specialization path

6. Escalate
   - one supported way to connect Swarm and verify it

7. Validate
   - one end-to-end acceptance checklist for:
     - provider
     - Telegram
     - Researcher
     - chips/path
     - Swarm

## 7. Concrete Next Execution Order

Use this order next:

1. Activate one real chip set and one real specialization path on the canonical Telegram home.
2. Prove one Telegram reply that is meaningfully shaped by those active assets.
3. Configure real Swarm API settings and get `swarm status` to `api_ready: yes`.
4. Prove one live `swarm sync`.
5. Define and implement the Telegram-to-Swarm escalation rule.
6. Productize the install/setup/run story so a second operator can reproduce the stack.
7. Only after that return to Discord and WhatsApp as expansion surfaces.

## 8. Recommended Deliverables

The next plan should produce these concrete artifacts:

- one “canonical install and setup” doc
- one “always-on gateway run” doc
- one “Telegram plus Researcher plus Swarm” runtime decision contract
- one “recommended chips and path profiles” doc
- one acceptance checklist for a fresh operator machine

## 9. Definition Of Done

We should say Spark Intelligence is really connected and usable when all of this is true:

- Telegram is live by default through the continuous gateway
- one real provider path is stable
- Spark Researcher is live and clearly in the runtime loop
- at least one real chip profile and one specialization path are active
- Spark Swarm is API-connected and proven reachable
- at least one Telegram-originated task can escalate into Swarm intentionally
- setup and run are reproducible by another operator without hidden steps
- recovery is operator-visible and documented
