# Route Confidence Gate V1

Status: design checkpoint for the first release slice.

Owner: `spark-intelligence-builder`

## Purpose

`RouteConfidenceGateV1` is the Builder-owned evaluator that turns current evidence into one of four outcomes:

- `act`
- `ask`
- `explain`
- `refuse`

It should answer route/status questions only when source-owned evidence is strong enough. It is not a mission launcher, memory store, or UI panel.

## First Proof

The first proof question is:

```text
Which LLM took the latest Spawner job?
```

Builder should answer only from current compiled evidence about the latest Spawner job. If the evidence is incomplete, Builder should name the missing proof instead of guessing.

## Source Hierarchy

For this question, use this order:

1. current user message and explicit status intent
2. Telegram route firewall / read-only classification
3. `LatestSpawnerJobEvidenceV1` from `spark-cli`
4. Spawner mission-control row for the latest job
5. Spawner PRD trace row for the same job
6. Spawner `agent-events` executed provider/model metadata
7. Builder AOC/source ledger freshness
8. runtime source freshness
9. approved memory
10. LLM wiki
11. inference

Memory and LLM wiki can explain policy, but they cannot answer "latest" questions.

## Proposed Contract

```json
{
  "schema_version": "spark.route_confidence_gate.v1",
  "intent": "status",
  "candidate_route": "spawner.latest_job_provider",
  "owner_system": "spawner-ui",
  "decision": "explain",
  "confidence": "high",
  "required_sources": ["mission-control", "spawner-prd-trace", "agent-events"],
  "missing_evidence": [],
  "source_refs_redacted": ["trace:redacted"],
  "authority_required": false,
  "safe_reply_policy": "answer_live",
  "data_boundary": {
    "exports_raw_prompt": false,
    "exports_chat_id": false,
    "exports_provider_output": false,
    "exports_memory_body": false,
    "exports_transcript_body": false,
    "exports_audio": false,
    "exports_env_value": false,
    "exports_secret": false
  }
}
```

## Edge Cases

| Edge case | Builder decision |
| --- | --- |
| Latest mission id is absent | `ask` with `missing_latest_mission_id` |
| Mission exists but provider/model execution metadata is absent | `explain` with `missing_executed_provider_model` |
| Configured provider exists but no execution evidence exists | `explain`; do not answer from config |
| PRD planned provider conflicts with agent-event executed provider | prefer executed provider and include conflict risk |
| Telegram classified a read-only question as a build/run | `refuse` the action path and return read-only route guidance |
| Current live state conflicts with memory | current live state wins |
| LLM wiki has a stale note | ignore for current answer; record stale-support risk if useful |
| Runtime source drift is detected | `explain` or `ask`; require `/diagnose` or source freshness proof |
| Evidence is from fixture/test roots | block live answer unless explicitly in test mode |
| Evidence contains forbidden payload keys | `refuse` and emit privacy blocker metadata |
| Repair action lacks repair target, repair scope, or fresh health evidence | `ask` with route evidence blockers |
| Local supervised repair has authority, runner capability, and fresh degraded health evidence | `act` |
| Credential, secret, destructive, external, publication, or broad-mutation repair lacks confirmation | `ask` |
| Memory action lacks source-owned `MemoryActionVerdictV1` | `ask` |
| Memory action verdict is blocked or denied | `refuse` |
| Publishing action lacks publication target or external-risk classification | `ask` |
| Publishing action lacks confirmation | `ask` |

## Action Route Adoption

Action routes are stricter than status routes. Builder accepts metadata-only route context from adapters, but the adapter does not own the verdict.

Required action evidence:

- latest instruction
- intent clarity
- route fit
- consequence risk
- confirmation state
- structured `spark.authority_verdict.v1` or explicit `not_required`
- runner/capability state
- clean privacy boundary

Additional route-family evidence:

- repair: repair target, repair scope, and fresh health evidence
- memory action: source-owned `spark.memory_action_verdict.v1`
- publishing: publication target and publication/external risk classification

This keeps `spawner.build`, `spark.repair`, memory correction/forget/decay/promote/deepen, and publishing routes on the same Builder-owned `act | ask | explain | refuse` spine without making Telegram, CLI, or Cockpit a second authority engine.

## LLM Wiki Boundary

Builder's LLM wiki may store a candidate note that says:

```text
Latest route/status answers require source-owned live evidence.
```

That note is supporting project knowledge only. It does not override:

- current user message
- live traces
- compiled Spark OS artifacts
- tests
- runtime source freshness

Do not promote this note to verified until the first route gate implementation and live proof pass.

## Minimal Implementation Path

1. Add a small pure evaluator in Builder self-awareness code.
2. Accept `LatestSpawnerJobEvidenceV1` from `spark-cli` artifacts or AOC source ledger input.
3. Return a metadata-only gate card with decision, missing evidence, source refs redacted, and safe reply policy.
4. Expose it through the existing AOC/Operating Panel path.
5. Add tests for stale memory, config-vs-executed provider, missing provider metadata, route hijack, and privacy keys.

Do not add a new database or background worker for v1.
