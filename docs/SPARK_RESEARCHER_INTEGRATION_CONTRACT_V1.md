# Spark Intelligence v1 Spark Researcher Integration Contract

## 1. Purpose

This document defines how `Spark Intelligence` integrates with `Spark Researcher`.

The goal is to use Spark Researcher as the runtime intelligence core without:

- rebuilding it in Spark Intelligence
- giving it channel or identity responsibilities
- blurring ownership between the products

## 2. Core Rule

Spark Intelligence owns:

- channels
- identity
- pairing
- session continuity
- provider and adapter config
- operator-facing runtime shell

Spark Researcher owns:

- core intelligence loop
- evidence-backed advisory logic
- packet and memory retrieval
- Spark-native reasoning and runtime artifacts

## 3. Design Goals

### 3.1 One Clear Boundary

Spark Intelligence should call Spark Researcher through a small stable bridge.

### 3.2 No Duplicate Runtime Logic

Do not reimplement:

- packet retrieval
- memory retrieval
- advisory assembly
- Spark runtime artifact logic

inside Spark Intelligence.

### 3.3 Secure Context Passing

Only pass runtime context Spark Researcher actually needs.

Do not pass adapter secrets or broad host authority by default.

### 3.4 Local-First

The contract should work against a local Spark Researcher workspace and `runtime_root`.

## 4. Spark Researcher Realities

Current Spark Researcher is built around:

```text
config -> run -> score -> ledger -> memory -> next decision
```

Important existing surfaces include:

- `run_once()`
- `build_advisory()`
- `search_memory()`
- `sync_memory()`
- `search_packets()`
- `packet_status()`
- collective payload generation for Spark Swarm sync

The contract should respect those realities instead of pretending Spark Researcher is a blank SDK.

## 5. Recommended Integration Modes

### 5.1 Conversational Advisory Mode

This should be the main v1 mode for Spark Intelligence.

Use Spark Researcher to:

- build evidence-backed context
- retrieve packets and memory
- produce Spark-shaped guidance for the active session

### 5.2 Packet And Memory Query Mode

Spark Intelligence may call Spark Researcher for:

- packet lookup
- memory lookup
- packet status
- memory sync triggers

### 5.3 Collective Sync Mode

Spark Intelligence should consume or respect Spark Researcher collective payloads when integrating with Spark Swarm, but not own their schema.

### 5.4 Deferred Experimental Mode

Do not make per-message `run_once()` experimentation the default chat path in v1.

That is too heavy and blurs conversational runtime with research loop execution.

## 6. Recommended v1 Request Contract

Spark Intelligence should call a narrow bridge with a normalized request envelope.

Recommended fields:

```text
request_id
agent_id
human_id
session_binding_id
surface_kind
channel_kind
user_message
active_spark_profile
active_domain_chips
active_specialization_path
config_path
runtime_root
trace_id
security_mode
```

### 6.1 Do Not Pass

Do not pass to Spark Researcher by default:

- raw adapter tokens
- broad channel transport metadata with no intelligence value
- operator secrets unrelated to the task

## 7. Recommended v1 Response Contract

The bridge should return a normalized result shape.

Recommended fields:

```text
request_id
reply_text
evidence_summary
packet_refs
memory_refs
escalation_hint
followup_actions
trace_ref
```

### 7.1 Response Rule

Spark Researcher returns intelligence output.

Spark Intelligence decides:

- how to deliver it
- how to present it on the channel
- whether to trigger pairing or approval flows

## 8. Ownership Boundaries

### 8.1 Spark Intelligence Responsibilities

- resolve identity
- enforce pairing and allowlists
- construct the normalized request
- manage sessions
- deliver the final response

### 8.2 Spark Researcher Responsibilities

- evidence-backed reasoning
- packet retrieval
- memory retrieval and sync surfaces
- Spark-native runtime context shaping
- collective payload production where applicable

### 8.3 Shared But Distinct

Both care about continuity, but in different ways:

- Spark Intelligence owns user/session continuity
- Spark Researcher owns intelligence continuity

Do not merge these into one blurry subsystem.

## 9. Security Rules

### 9.1 Identity Rule

Spark Researcher should not be trusted to resolve external user identity.

That stays in Spark Intelligence.

### 9.2 Secret Rule

Spark Researcher should not receive adapter secrets unless there is an explicit, reviewed reason.

### 9.3 Tool Rule

Spark Researcher should only act within the tool and approval boundaries Spark Intelligence exposes.

### 9.4 Fail-Closed Rule

If config, runtime root, or bridge invocation is invalid:

- return a controlled failure
- do not silently downgrade to an unsafe mode

## 10. Filesystem And Runtime Binding

### 10.1 Required Inputs

The bridge should know:

- `config_path`
- `runtime_root`

These are already first-class concepts in Spark Researcher.

### 10.2 Artifact Respect

Spark Intelligence should treat Spark Researcher artifacts as canonical where Spark Researcher owns them:

- ledger
- memory artifacts
- packet documents
- collective payloads

It should not mirror them into competing stores.

## 11. Suggested Bridge Shape

Recommended future module:

```text
spark_intelligence/
`- researcher_bridge/
   |- request.py
   |- response.py
   |- advisory.py
   |- packets.py
   |- memory.py
   `- errors.py
```

### 11.1 Bridge Responsibilities

- validate request envelope
- call Spark Researcher surfaces
- normalize results into Spark Intelligence response shapes
- expose clear failure types

## 12. v1 Behavioral Rules

### 12.1 Message Handling

For a normal user message:

1. Spark Intelligence resolves identity and session
2. Spark Intelligence builds a normalized request
3. Spark Researcher bridge assembles Spark-native context
4. Spark Researcher returns normalized intelligence output
5. Spark Intelligence formats and sends the reply

### 12.2 Memory Handling

Spark Intelligence may trigger memory sync or memory lookup through Spark Researcher surfaces, but does not redefine memory doctrine.

### 12.3 Escalation Handling

If the response indicates deeper work is needed:

- Spark Intelligence may call Spark Swarm
- Spark Researcher remains the primary intelligence core

## 13. Failure Modes To Prevent

This contract should prevent:

- Spark Intelligence rebuilding Spark Researcher logic locally
- adapter tokens leaking into Spark Researcher by default
- identity resolution leaking into Spark Researcher
- two competing stores for packets or memory
- per-message heavy experiment execution becoming the default chat path

## 14. Recommended v1 Implementation Rule

Start with one narrow advisory-style bridge.

Do not begin with:

- a huge SDK abstraction
- per-message self-edit loops
- direct mutation execution from every chat request

## 15. Final Decision

Spark Intelligence should integrate Spark Researcher through a small, explicit bridge that:

- passes only the right context
- keeps ownership boundaries clean
- preserves Spark Researcher as the runtime intelligence core
- avoids rebuilding Spark inside Spark Intelligence

Spark Intelligence is the shell.
Spark Researcher is the core.
