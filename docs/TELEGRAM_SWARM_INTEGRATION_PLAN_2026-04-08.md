# Telegram To Spark Swarm Integration Plan 2026-04-08

## 1. Purpose

This note defines the correct integration shape between:

- the production Telegram bot
- `spark-intelligence-builder`
- `spark-swarm`
- `spark-browser-extension`
- `spark-researcher`

The goal is to expand Telegram-accessible Swarm functionality without creating ownership conflicts, duplicate runtimes, or blurred trust boundaries.

## 2. Current Truth

As of 2026-04-08:

- Builder is the production Telegram ingress owner.
- Builder already exposes bounded Telegram runtime commands for Swarm:
  - `/swarm`
  - `/swarm status`
  - `/swarm evaluate <task>`
  - `/swarm sync`
- Builder is already connected to `spark-swarm` as a downstream system.
- `spark-swarm` does not currently implement Telegram bot ingress in this repo.
- `spark-browser-extension` is the governed browser runtime used downstream through Builder.
- `spark-researcher` remains the heavy local runtime behind Builder and Swarm.

That means the right architecture is:

`Telegram -> Builder -> Swarm / Browser / Researcher`

not:

`Telegram -> Swarm`

and not:

`Telegram -> Builder` plus a second Telegram poller in Swarm.

## 3. Ownership Model

### Telegram / Builder

Builder owns:

- bot token polling
- pairing and identity resolution
- operator approvals and runtime controls
- delivery formatting and channel-specific responses
- natural-language intent interpretation

### Spark Swarm

Swarm owns:

- hosted collective intelligence state
- workspace observatory and control-plane state
- specialization-path and collective governance state
- upgrade, delivery, contradiction, mastery, and inbox records

### Spark Browser Extension

The browser extension owns:

- governed Brave/browser execution
- host permission posture
- bounded page evidence collection

### Spark Researcher

Spark Researcher owns:

- heavy runtime execution
- autoloop and local artifact generation
- local memory and run-state emission

## 4. What Telegram Should Be Able To Do With Swarm

There are four clean classes of Telegram-to-Swarm integration.

### A. Read Swarm Hosted State

Telegram should answer natural requests such as:

- show me the swarm status
- what changed in the swarm
- what path is active right now
- what upgrades are pending
- what contradictions are blocking progress
- what operator issues are open
- summarize the collective for this workspace

These should map to hosted Swarm read surfaces, not local bridge execution.

Primary Swarm sources:

- overview
- live session
- runtime pulse
- collective snapshot
- evolution inbox
- specializations
- evolution paths
- upgrades
- operator issues

### B. Trigger Bounded Hosted Swarm Actions

Telegram should be able to invoke safe hosted governance actions such as:

- sync the latest collective state
- absorb an insight
- review a mastery
- change evolution mode
- deliver an upgrade
- refresh upgrade delivery status

These should go through Builder as explicit action intents and then into the hosted Swarm API with normal operator safeguards.

### C. Trigger Governed Local Swarm Bridge Actions

Telegram should eventually support explicit, operator-governed local bridge actions such as:

- run a specialization path
- start an autoloop plan
- execute the latest rerun request
- bind or refresh the local bridge session

These are not simple hosted actions. They cross into trusted local execution and should be treated as higher-risk operator actions.

They should only be exposed with:

- explicit intent
- clear reply copy describing what will run
- approval or confirmation when the action may mutate local state or consume meaningful time

### D. Deep-Link To The Swarm Web UI

Telegram should frequently respond with:

- a concise answer
- the current state summary
- a link to the exact Swarm page for deeper inspection

Useful targets include:

- `/collective`
- `/live`
- `/runtime`
- `/runs`
- `/connect`

Telegram should not try to replicate the entire web control plane in chat.

## 5. What Telegram Should Not Do

The following would create architectural drift or operational soup:

- moving production Telegram ingress ownership into Swarm before Swarm implements a real bot runtime
- allowing Builder and Swarm to poll the same Telegram bot token
- bypassing the Swarm bridge for local specialization-path execution
- storing raw runtime logs or browser traces as chat-derived Swarm intelligence
- letting vague mentions of "swarm" hijack normal conversation into command mode
- duplicating Swarm's hosted governance logic inside Builder memory or prompt residue

## 6. Recommended Phased Rollout

### Phase 1. Natural-Language Swarm Read Intents

Goal:

- make Telegram feel more natural for Swarm reads without adding risky execution

Add Builder-side natural-language intents for:

- swarm status
- swarm overview
- swarm live state
- runtime pulse
- active specialization
- pending upgrades
- operator issues
- evolution inbox summary

Implementation rule:

- these should stay read-only
- these should use Swarm hosted API/state
- these should produce concise summaries plus optional deep links

### Phase 2. Natural-Language Swarm Hosted Actions

Goal:

- support practical operator actions that remain inside hosted governance boundaries

Add intents such as:

- sync this to swarm
- absorb this insight
- review this mastery as accepted/rejected
- set this specialization to review required / auto / hold
- check delivery status for this upgrade

Implementation rule:

- each action must map to one concrete API call
- ambiguous language should be rejected or clarified
- responses should confirm the state transition, not just say "done"

### Phase 3. Governed Local Bridge Actions

Goal:

- let Telegram start real Swarm local execution without silently escalating privileges

Add intents such as:

- run startup-operator
- start autoloop for startup-operator
- execute the latest rerun request

Implementation rule:

- treat these as operator-grade actions
- default to approval-required when the action starts local runtime work or may mutate local repo state
- return structured execution summaries and artifact references, not raw logs

### Phase 4. Rich Linked Telegram Operator Experience

Goal:

- make Telegram the lightweight command surface while Swarm web stays the deep inspection surface

Add replies that include:

- state summary
- next recommended action
- exact Swarm page link
- exact Builder command or Swarm bridge command when operator follow-up is needed

## 7. Intent Mapping Model

Builder should implement Swarm Telegram intents in three tiers.

### Tier 1. Runtime Commands

These are explicit and deterministic.

Examples:

- `/swarm status`
- `show me the swarm status`
- `please sync with swarm`

These should bypass the normal provider bridge and route straight into Builder-owned runtime handlers.

### Tier 2. Structured Swarm Intents

These are natural-language requests that still map to one clear action or read surface.

Examples:

- what upgrades are waiting in swarm
- show the current specialization paths
- summarize the operator issues in swarm

These should route through explicit Builder intent handlers, not generic chat generation.

### Tier 3. Advisory Questions About Swarm

These are open-ended.

Examples:

- should this go to swarm
- what would swarm be best used for here
- is this worth escalating to a specialization path

These should remain in Builder reasoning mode, with Swarm-aware guidance layered in rather than forced command execution.

## 8. Safeguards

### One Poller Rule

- only one Telegram long-poller per bot token
- production owner remains Builder

### Explicit Local Execution Rule

- no silent specialization-path or autoloop execution from vague natural language

### Hosted-State First Rule

- prefer hosted Swarm read surfaces before introducing local bridge execution

### Evidence Compression Rule

- return thin summaries, decisions, and links
- do not dump raw bridge logs or raw researcher artifacts into Telegram

## 9. Best Next Implementation Order

If we continue immediately, the best order is:

1. Add natural-language Swarm read intents in Builder.
2. Add hosted Swarm summary responses and page links.
3. Add hosted Swarm action intents for safe governance actions.
4. Add approval-gated local bridge execution intents for specialization-path and rerun flows.

This keeps risk low while making Telegram materially more useful as a front door to Swarm.

## 10. Bottom Line

Telegram should become:

- a natural-language front door to Swarm state
- a lightweight operator action surface for hosted governance
- a governed trigger surface for local Swarm bridge work

It should not become:

- a second Swarm runtime
- a replacement for the Swarm web control plane
- a second Telegram ingress owner

The clean product shape is:

`Telegram -> Builder -> Swarm hosted state / Swarm bridge / Browser / Researcher`
