# Route Confidence Gate V1

Date: 2026-05-12
Owner: `spark-intelligence-builder`
Supporting evidence owner: `spark-cli` compiled Spark OS read model
Surface adapters: Telegram today, future messaging apps later

## Purpose

`RouteConfidenceGateV1` is the Builder-owned state machine for answering runtime route/status questions without guessing from memory, wiki, stale chat, or a single UI surface.

It exists so every ingress surface can ask the same question:

> Given this user intent and this candidate route, should Spark act, ask, explain, or refuse?

## Ownership

- Builder owns the verdict: `act`, `ask`, `explain`, or `refuse`.
- `spark-cli` owns compiled evidence such as `latest_spawner_job`.
- Spawner owns source runtime events such as mission control, PRD trace rows, and agent events.
- Telegram and future messaging apps are thin adapters. They render Builder's verdict and do not infer route confidence themselves.
- LLM wiki is supporting context only. It must not answer current runtime facts.
- Memory is supporting context only for this route. It must not fill missing live provider proof.

## Current Contract

Command:

```powershell
python -m spark_intelligence.cli self route-confidence-gate --home C:\Users\USER\.spark\state\spark-intelligence --json
```

Output schema:

```json
{
  "schema_version": "spark.route_confidence_gate.v1",
  "intent": "status",
  "candidate_route": "spawner.latest_job_provider",
  "owner_system": "spark-intelligence-builder",
  "source_owner_system": "spawner-ui",
  "decision": "explain",
  "confidence": "low",
  "source_status": "partial",
  "freshness": "current",
  "provider": null,
  "model": null,
  "joined_sources": ["agent-events", "mission-control"],
  "missing_evidence": ["spawner_prd_trace", "missing_executed_provider"],
  "safe_reply_policy": "explain_missing",
  "verification_command": "spark os trace --json"
}
```

## Edge Cases

1. No compiled Spark OS evidence
   - Decision: `ask`
   - Confidence: `blocked`
   - Safe reply: ask operator to run `spark os compile` or `spark os trace --json`.

2. Current evidence but missing provider/model
   - Decision: `explain`
   - Confidence: `low`
   - Safe reply: name missing source-owned evidence, not a guessed provider.

3. Provider/model present from source-owned execution events
   - Decision: `explain`
   - Confidence: `high` or `medium`
   - Safe reply: answer provider/model and show source classes, not raw ids.

4. Stale source evidence
   - Decision: `explain`
   - Confidence: `low`
   - Safe reply: answer only as stale/partial, with a verification command.

5. Privacy boundary violation
   - Decision: `refuse`
   - Confidence: `blocked`
   - Safe reply: do not export raw prompts, chat ids, provider output, memory bodies, transcripts, audio, env values, or secrets.

## Why This Lives In Builder

Route confidence is runtime judgment, not Telegram formatting. If Telegram owns it, every future surface would reimplement separate confidence logic and drift. Builder is the right owner because it already owns AOC, black-box evidence, memory gates, source ledgers, self-awareness, and route policy.

The clean pattern is:

```text
surface adapter -> Builder RouteConfidenceGateV1 -> source-owned evidence -> surface-safe reply
```

## Verification

```powershell
spark os compile --json
$env:PYTHONPATH='C:\Users\USER\.spark\modules\spark-intelligence-builder-release\source\src'
python -m spark_intelligence.cli self route-confidence-gate --home C:\Users\USER\.spark\state\spark-intelligence --json
```

Expected behavior after the latest run on 2026-05-12:

- Builder command is reachable.
- It reads the compiled Spark OS state.
- It does not export raw prompts, chat ids, provider output, memory bodies, transcript/audio bodies, env values, or secrets.
- It refuses to invent the latest provider if executed provider evidence is missing.
