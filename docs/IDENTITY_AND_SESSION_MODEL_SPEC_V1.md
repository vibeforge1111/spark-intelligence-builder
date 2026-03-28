# Spark Intelligence v1 Identity And Session Model Spec

Superseded in part for Builder-plus-Spark-Swarm canonical agent linking by:

- `docs/AGENT_IDENTITY_SYSTEM_SPEC_2026-03-28.md`
- `docs/AGENT_IDENTITY_IMPLEMENTATION_PLAN_2026-03-28.md`

This v1 document still describes the foundational workspace, human, session, and pairing model.

## 1. Purpose

This document defines the v1 identity and session model for `Spark Intelligence`.

This is a core system, not an implementation detail.

It decides:

- who is allowed to talk to an agent
- how one human maps across Telegram, Discord, WhatsApp, and future channels
- how one persistent Spark agent stays continuous across surfaces
- how to prevent cross-user and cross-thread leakage
- how to keep pairing, allowlists, and migration behavior understandable

## 2. Design Goals

### 2.1 One Human, One Agent Identity

Within one Spark Intelligence workspace, one human should map to one persistent Spark agent identity.

Different channels should not create different agents by default.

### 2.2 Strict Isolation By Default

Users should not inherit each other's context because they happen to be in the same server, group, or thread.

### 2.3 Canonical Identity Store

Identity and session state should live in one canonical store.

Adapters should resolve against it.

Adapters should not invent their own identity systems.

### 2.4 Lightweight But Secure

The model should be simple enough for SQLite and local inspection, while still being hard to misuse.

### 2.5 Migration-Friendly

The model should support safe import of:

- allowlists
- pairings
- safe session mappings

from OpenClaw and Hermes where structurally compatible.

## 3. Core Principles

### 3.1 Identity Is A Security Boundary

Identity resolution is not just UX.

It is one of the main trust boundaries of the system.

### 3.2 Session Continuity Must Be Explicit

Continuity should come from canonical mappings, not heuristics hidden inside adapters.

### 3.3 Deny By Default

Unknown senders should not get full runtime access by accident.

### 3.4 Per-User By Default

Shared-room behavior must be explicitly enabled.

It should never be the accidental default.

## 4. Canonical Entities

### 4.1 Workspace

The local Spark Intelligence installation boundary.

This is the top-level owner of:

- config
- state
- agents
- channels
- pairings

### 4.2 Human Identity

A canonical person record inside one workspace.

Recommended fields:

```text
id
display_name
status
created_at
updated_at
```

### 4.3 Agent Identity

The persistent Spark agent assigned to a human inside one workspace.

Recommended fields:

```text
id
workspace_id
human_id
spark_profile
home_channel
status
created_at
updated_at
```

v1 default:

- one active agent identity per human per workspace

### 4.4 Channel Installation

Represents one configured adapter instance.

Examples:

- Telegram bot installation
- Discord bot installation
- WhatsApp linked session

Recommended fields:

```text
id
channel_kind
status
config_ref
created_at
updated_at
```

### 4.5 Channel Account

Represents an external sender identity on a channel.

Examples:

- Telegram user id
- Discord user id
- WhatsApp phone number identity

Recommended fields:

```text
id
channel_installation_id
external_user_id
external_username
status
created_at
updated_at
```

### 4.6 Conversation Surface

Represents where the interaction happens.

Examples:

- Telegram DM
- Discord DM
- Discord guild channel thread
- WhatsApp direct chat

Recommended fields:

```text
id
channel_installation_id
surface_kind
external_surface_id
status
created_at
updated_at
```

### 4.7 Identity Binding

The canonical mapping between:

- one human identity
- one external channel account

Recommended fields:

```text
id
human_id
channel_account_id
binding_kind
verified
created_at
updated_at
```

### 4.8 Session Binding

A runtime continuity mapping between:

- one agent identity
- one conversation surface

This tracks where continuity is allowed to continue.

Recommended fields:

```text
id
agent_id
surface_id
session_mode
status
last_active_at
created_at
updated_at
```

### 4.9 Pairing Record

Tracks the approval flow for a new sender or surface.

Recommended fields:

```text
id
channel_account_id
surface_id
status
approval_mode
approved_by
created_at
updated_at
```

### 4.10 Allowlist Entry

Tracks explicit authorization.

Recommended fields:

```text
id
channel_kind
external_user_id
scope
created_at
updated_at
```

## 5. Default Trust Model

### 5.1 Unknown Sender

Default behavior:

- do not attach to an existing agent identity
- do not expose full runtime behavior
- require pairing or allowlist approval

### 5.2 Known Sender On Known Surface

Default behavior:

- resolve human identity
- resolve agent identity
- resolve or create valid session binding
- continue runtime conversation

### 5.3 Known Sender On New Surface

Default behavior:

- do not assume linking automatically
- require explicit verification or operator approval unless a safe linking rule is present

## 6. Resolution Flow

### 6.1 Inbound Message Resolution

Every inbound event should flow like this:

1. identify channel installation
2. normalize external sender id
3. normalize conversation surface id
4. look up channel account
5. check allowlist or pairing state
6. resolve identity binding
7. resolve agent identity
8. resolve session binding
9. create runtime request

If any trust-critical step fails, stop before runtime execution.

### 6.2 Outbound Resolution

Outbound events should flow like this:

1. runtime emits result for agent identity
2. session binding identifies destination surface
3. channel installation identifies adapter transport
4. adapter sends output

## 7. Pairing Model

### 7.1 Safe Defaults

v1 should support:

- explicit allowlist-only mode
- first-pairing approval mode
- operator-confirmed linking mode

Recommended default:

- Telegram: allowlist or first approved DM pairing
- Discord: allowlist or DM pairing, not public server auto-pairing
- WhatsApp: allowlist or explicit first pairing

### 7.2 Pairing States

Recommended states:

- `pending`
- `approved`
- `rejected`
- `revoked`

### 7.3 Pairing Rule

One new channel account should never silently inherit another human's agent.

## 8. Cross-Channel Linking

### 8.1 Principle

Cross-channel continuity is valuable, but it is also risky.

So linking should be explicit.

### 8.2 Recommended v1 Linking Methods

Safe options:

- operator confirms link in CLI
- one-time linking code
- trusted existing surface confirms new surface

### 8.3 Unsafe Linking Methods

Reject:

- matching usernames only
- matching display names only
- automatic linking from shared group presence
- heuristic linking with no explicit approval trail

## 9. Group And Shared Surface Rules

### 9.1 Default Group Posture

Group and guild surfaces should be treated as untrusted shared environments.

### 9.2 v1 Default

Recommended default:

- DMs first
- explicit shared-room mode only later

### 9.3 Shared Mode Rule

If shared mode exists later, it must have:

- separate session semantics
- explicit operator opt-in
- explicit identity scope

It should not reuse direct-message assumptions.

## 10. Session Model

### 10.1 Session Purpose

Sessions define continuity boundaries for runtime context.

They should answer:

- which surface this interaction belongs to
- which agent identity owns it
- whether continuity should continue here

### 10.2 Session Modes

Recommended v1:

- `direct_continuous`
- `linked_surface_continuous`
- `operator_review_only`

### 10.3 Session Lifetime

Sessions should be long-lived enough for continuity, but explicit enough to revoke.

### 10.4 Revocation

Revoking a session binding should:

- stop future continuity on that surface
- not delete the human identity
- not necessarily delete the underlying agent identity

## 11. Storage Model

### 11.1 Recommended Store

Use SQLite in v1.

Reasons:

- local-first
- inspectable
- good enough for v1 scale
- works with the rest of the lightweight runtime doctrine

### 11.2 Canonical Tables

Recommended initial tables:

- `humans`
- `agents`
- `channel_installations`
- `channel_accounts`
- `conversation_surfaces`
- `identity_bindings`
- `session_bindings`
- `pairing_records`
- `allowlist_entries`

## 12. Security Rules

### 12.1 No Adapter-Owned Identity Truth

Adapters can cache transport state, but they must not own identity truth.

### 12.2 No Cross-User Leakage

A session must never be reused across humans.

### 12.3 No Cross-Surface Auto-Linking Without Proof

New surfaces require explicit verification.

### 12.4 Audit Trail

Pairing, linking, approval, and revocation events should be logged with:

- who acted
- what changed
- when it changed

### 12.5 Principle Of Least Continuity

Only preserve continuity where the binding is explicit and safe.

## 13. CLI Surface

Recommended identity commands:

- `spark-intelligence identity list`
- `spark-intelligence identity inspect <human-or-agent-id>`
- `spark-intelligence pairing list`
- `spark-intelligence pairing approve <pairing-id>`
- `spark-intelligence pairing reject <pairing-id>`
- `spark-intelligence session list`
- `spark-intelligence session revoke <session-id>`

The CLI should make identity state visible enough to debug locally.

## 14. Migration Rules

### 14.1 Safe Import Targets

Safe imports from OpenClaw and Hermes:

- explicit allowlists
- explicit paired user ids
- channel installation metadata
- clear one-to-one session mappings

### 14.2 Unsafe Import Targets

Reject:

- inferred identity links with no proof trail
- room-level mappings with ambiguous ownership
- opaque cached sender state

## 15. Failure Modes To Prevent

This model should prevent:

- one user inheriting another user's context
- one Discord server thread accidentally binding to the wrong human
- a new Telegram account hijacking an existing agent identity
- WhatsApp or Discord group activity auto-linking identities
- adapter-local hacks becoming the real identity system

## 16. Recommended v1 Rule

The simplest correct rule for v1 is:

- one human
- one agent
- DMs first
- explicit pairing
- explicit cross-channel linking
- one canonical identity store

## 17. Final Decision

Identity and session state should be treated as a first-class secure core of Spark Intelligence.

It should stay:

- lightweight
- SQLite-backed
- explicit
- visible
- revocable
- deny-by-default

Continuity is a feature.

Uncontrolled continuity is a security bug.
