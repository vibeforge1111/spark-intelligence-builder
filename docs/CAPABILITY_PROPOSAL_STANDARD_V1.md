# Spark Capability Proposal Standard v1

## Purpose

This standard defines how Spark should handle natural-language requests that try to give Spark a new ability.

A capability proposal is not the same thing as a normal app build, even when the user says "build." It is a request to change what Spark can do, what systems it can reach, or how a Spark-owned workflow behaves.

## Core Distinction

Spark should classify the user's request into two layers:

1. Intent layer: what capability the user wants Spark to gain.
2. Implementation route: how that capability should be built, tested, approved, and activated.

Examples:

- "Build a dashboard for Spark memory reports" is an artifact build unless it says the dashboard changes Spark's runtime behavior.
- "Build this for you, Spark: read my emails and summarize them" is a capability proposal.
- "Create a capability for Spark to read my calendar" is a capability proposal.
- "Build a skill that lets you browse my project files" is a capability proposal.
- "Build a tool for Spark users to manage reminders" is an app/tool build.

## Implementation Routes

Capability proposals should choose one of these routes:

| Route | Use When | Owner |
| --- | --- | --- |
| `domain_chip` | The capability is a reusable specialist module with hookable behavior, domain policy, doctrine, watchtower logic, or repeatable evaluation/suggestion logic. | Domain chip repo + Builder attachment runtime |
| `runtime_patch` | The capability changes Builder, Telegram, memory, routing, identity, permissions, or provider behavior directly. | Spark Intelligence Builder or gateway owner |
| `capability_connector` | The capability connects Spark to an external service such as email, calendar, browser, voice, files, notifications, or another API. Usually starts as a chip or hook-backed connector. | Builder + connector chip/harness |
| `mission_artifact` | The user wants a visible app/dashboard/tool that supports Spark or the user but does not itself become Spark runtime authority. | Spawner UI / Mission Control |
| `workflow_automation` | The capability is scheduled, recurring, event-driven, or report-like. | Builder/Spawner scheduler with explicit capability ledger entries |

## Domain Chip vs Capability Chip

Do not introduce a separate "capability chip" repo type in v1.

Use `domain chip` as the concrete attachment package. A chip may expose capabilities through its manifest:

- `capabilities`
- `commands`
- `task_topics`
- `task_keywords`
- `onboarding.surfaces`
- `onboarding.permissions`
- `onboarding.harnesses`
- `onboarding.health_checks`
- `onboarding.limitations`

The phrase "capability chip" can be used conversationally, but technically it means:

> a domain chip whose primary purpose is to add an executable Spark capability.

This avoids a second plugin standard while still letting Spark build email, calendar, voice, file, browser, memory, or workflow capabilities as attached modules.

## Required Capability Proposal Packet

Before Spark claims a capability is live, the plan must name:

- `capability_goal`
- `recipient` (`spark`, `user`, `spark_users`, or `external_system`)
- `implementation_route`
- `owner_system`
- `permissions_required`
- `safe_probe`
- `human_approval_boundary`
- `rollback_path`
- `activation_path`
- `eval_or_smoke_test`
- `capability_ledger_key`
- `claim_boundary`

Canonical JSON shape:

```json
{
  "schema_version": "spark.capability_proposal.v1",
  "status": "proposal_plan_only",
  "capability_goal": "",
  "recipient": "spark",
  "implementation_route": "domain_chip",
  "owner_system": "Spark Intelligence Builder + domain chip attachment runtime",
  "permissions_required": ["operator_approval_to_activate"],
  "safe_probe": "",
  "human_approval_boundary": "",
  "rollback_path": "",
  "activation_path": "",
  "eval_or_smoke_test": "",
  "capability_ledger_key": "domain_chip:example-capability",
  "claim_boundary": "",
  "source_intent": ""
}
```

The initial packet is always `proposal_plan_only`. Later systems may add activation/evidence records, but they must not rewrite this packet into proof.

## Build Flow

Capability proposals may still use Spawner UI and Mission Control.

The build flow should be:

1. Classify the request as a capability proposal.
2. Produce the capability proposal packet.
3. Choose the implementation route.
4. If route is `domain_chip` or `capability_connector`, generate a domain-chip PRD and build it through Spawner/Mission Control.
5. If route is `runtime_patch`, create a bounded implementation mission with tests and rollback.
6. If route is `mission_artifact`, keep it as a normal app build.
7. If route is `workflow_automation`, require schedule, permissions, dry run, and observability.
8. Run the safe probe and eval before raising capability confidence.

## Spawner Bridge Contract

When a capability proposal enters Spawner, callers should attach the packet to `/api/prd-bridge/write` as `capabilityProposalPacket`.

Spawner stores the packet as inert, versioned metadata:

- `pending-request.json.capabilityProposalPacket`
- `pending-request.json.capabilityProposalSummary`
- `pending-load.json.capabilityProposalPacket`
- `pending-load.json.metadata.capabilityProposalPacket`
- Mission Control relay events may include the summary as `data.capabilityProposal`.

This metadata is for traceability and future upgrade compatibility. It must not be treated as activation proof, permission grant, or executable authority.

## Claim Boundary

Spark may say:

- "I can plan that capability."
- "I can build the chip/mission that would add that capability."
- "The route is attached/configured."
- "The route recently passed a probe."

Spark must not say:

- "I can read your email now" before auth, permission, execution, and eval pass.
- "The chip works" just because a repo exists.
- "The capability is installed" when only a PRD or mission exists.

## Routing Rule

When a request contains a Spark recipient plus an ability/integration/access surface, route it as a capability proposal before normal build intent.

Spark recipient examples:

- "for you"
- "for Spark"
- "build you"
- "so you can"
- "so Spark can"
- "lets you"
- "lets Spark"
- "make Spark able to"

Capability surface examples:

- email, inbox, calendar
- voice, browser, files, filesystem
- memory reports, notifications, reminders
- tools, routes, systems, workflow
- capability, ability, skill, integration, access, permission

Normal app-build examples should continue to build:

- "Build a Spark memory dashboard."
- "Build a dashboard for Spark memory reports."
- "Build a tool for Spark users to manage reminders."

## Anti-Drift Gates

A capability proposal cannot be promoted unless:

- The implementation route is explicit.
- The owner system is explicit.
- The permission boundary is explicit.
- The safe probe is executable.
- The eval or smoke test is named.
- The rollback path is named.
- The capability ledger key is stable.

If any gate is missing, keep the proposal in `plan_only_probe_first` mode.
