# Spark Intelligence v1 Spark Swarm Escalation Contract

## 1. Purpose

This document defines how `Spark Intelligence` escalates work to `Spark Swarm`.

The goal is to let one persistent user-facing agent invoke deeper multi-agent work without:

- exposing multi-agent complexity to the user by default
- duplicating Spark Swarm logic inside Spark Intelligence
- breaking identity, security, or tool boundaries

## 2. Core Rule

Spark Swarm is not the default runtime for every message.

Spark Swarm is the deep-work escalation layer.

The user should still feel like they are talking to one agent.

## 3. Design Goals

### 3.1 One-Agent UX

Escalation should happen behind one persistent agent identity.

### 3.2 Explicit Escalation Criteria

Swarm should be invoked because the task needs it, not because it sounds impressive.

### 3.3 Security Preservation

Escalation must not widen authority beyond the approvals and tool boundaries already granted.

### 3.4 Clear Provenance

Spark Intelligence should always know:

- why escalation happened
- which workstreams ran
- how results were merged

## 4. What Swarm Is For

Good v1 escalation candidates:

- multi-step research with distinct sub-questions
- parallel specialist synthesis
- repo-spanning analysis or change planning
- deeper work that benefits from explicit lineage

Bad v1 escalation candidates:

- every ordinary chat message
- simple direct answers
- identity or pairing logic
- adapter-specific transport work

## 5. Escalation Triggers

Recommended v1 triggers:

- explicit `needs_specialist_depth`
- explicit `parallelizable_subtasks`
- explicit `cross-domain_or_cross-repo_work`
- explicit `long-running_deep_research`

Do not trigger on vague ambition alone.

## 6. Request Contract

Spark Intelligence should pass a narrow escalation request envelope.

Recommended fields:

```text
request_id
agent_id
human_id
session_binding_id
trace_id
user_goal
researcher_context_summary
active_domain_chips
active_specialization_path
security_mode
allowed_tools
approval_scope
requested_work_mode
```

### 6.1 Do Not Pass

Do not pass by default:

- raw adapter tokens
- broad host authority not already approved
- unrelated user or operator secrets

## 7. Response Contract

Recommended normalized response shape:

```text
request_id
swarm_run_id
summary
worker_outputs
merged_recommendation
artifact_refs
followup_actions
trace_ref
```

Spark Intelligence should turn that into the final user-facing response.

## 8. Ownership Boundaries

### 8.1 Spark Intelligence Owns

- identity
- pairing
- session continuity
- user-facing messaging
- transport auth
- approval enforcement
- final delivery

### 8.2 Spark Swarm Owns

- multi-agent fanout
- specialist work coordination
- merge lineage
- collective intelligence operations

### 8.3 Spark Researcher Still Matters

Spark Researcher remains the main intelligence core.

Swarm is invoked as an escalation layer, not as a replacement for Spark Researcher.

## 9. Security Rules

### 9.1 No New Authority By Escalation

Swarm must not silently gain tools or authority that the parent request did not have.

### 9.2 Lineage Rule

Every escalation should preserve:

- parent request id
- parent trace id
- requesting agent id

### 9.3 Identity Rule

Swarm does not resolve external user identity.

That stays in Spark Intelligence.

### 9.4 Fail-Closed Rule

If escalation context is incomplete or invalid:

- do not partially escalate unsafely
- return a controlled failure or fallback

## 10. Interaction With Collective Sync

Spark Swarm already consumes `SparkResearcherCollectiveSyncPayload` as the bridge from Spark Researcher.

Spark Intelligence should respect that existing shape rather than inventing a competing collective schema.

## 11. v1 Behavioral Rule

For v1:

- keep Spark Swarm escalation explicit and relatively rare
- use it for clear deep-work cases
- keep the normal message path single-agent first

## 12. Final Decision

Spark Swarm is the deep execution layer for Spark Intelligence, not the default chat loop.

Spark Intelligence should escalate into it carefully, preserve one-agent UX, and never let escalation weaken security or ownership boundaries.
