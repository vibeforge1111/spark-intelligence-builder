# Spark Intelligence v1 Telegram Adapter Spec

## 1. Purpose

This document defines the v1 Telegram adapter for `Spark Intelligence`.

Historical note:

- this spec captures the original Builder-first Telegram adapter shape
- the newer stable production path uses a dedicated webhook-owned Telegram gateway in `spark-telegram-bot`
- use this document mainly for Builder-side adapter behavior, identity, pairing, and session rules rather than as the current production ingress ownership spec

The Telegram adapter is the first recommended channel because it is usually the fastest path to:

- one working persistent agent
- direct-message interaction
- low setup friction
- low operational complexity

## 2. Design Goals

### 2.1 Fastest First Channel

Telegram should be the fastest way to get Spark Intelligence working end to end.

### 2.2 DM-First

v1 should optimize for direct-message usage, not shared groups.

### 2.3 Thin Adapter

The adapter should be translation and delivery glue, not a second runtime.

### 2.4 Local-First Setup

the original Builder-first v1 path should not require public HTTPS or webhook hosting for local setup.

### 2.5 Secure By Default

Unknown Telegram users should not get full runtime access by accident.

## 3. Core Decision

The original Builder-first Telegram adapter should use long polling in v1 for local setup.

Why:

- simpler local setup
- works with `npx` and local installs
- avoids public webhook exposure
- keeps the first-run path much lighter

For the newer stable production path, webhook ownership now lives in `spark-telegram-bot` instead of Builder.

## 4. Ownership Boundary

The Telegram adapter owns:

- bot token usage
- Telegram API polling
- update normalization
- Telegram-specific delivery quirks
- basic adapter health

In the current split architecture, Builder-owned logic still owns session, identity, and adapter behavior, but the dedicated Telegram gateway owns live webhook ingress.

The Telegram adapter does not own:

- identity truth
- pairing truth
- runtime orchestration
- specialization logic
- memory policy

## 5. CLI Surface

Recommended commands:

- `spark-intelligence channel add telegram`
- `spark-intelligence channel status`
- `spark-intelligence channel remove telegram`

Recommended setup path:

```bash
spark-intelligence channel add telegram
spark-intelligence doctor
spark-intelligence gateway start
```

## 6. Setup Flow

### 6.1 Required Inputs

The adapter setup should request:

- Telegram bot token
- allowlist or pairing mode
- optional home user

### 6.2 Guided Instructions

The setup flow should explain:

- get bot token from `@BotFather`
- DM the bot to begin pairing
- use an explicit allowlist or first approved DM pairing

### 6.3 Stored State

Recommended minimal adapter state:

```text
channel_installation_id
bot_username
last_update_id
status
created_at
updated_at
```

## 7. Supported v1 Surfaces

### 7.1 Supported

- direct messages with the bot

### 7.2 Deferred

- group chats by default
- forum topics
- complex admin workflows
- bot-command-heavy UX

### 7.3 Group Rule

Group usage should be off by default.

If enabled later, it must use separate session semantics.

## 8. Inbound Event Model

### 8.1 Supported v1 Update Types

Recommended v1 support:

- plain text messages
- basic attachments where simple
- `/start`

### 8.2 Normalize To Canonical Event

Every Telegram update should normalize to a canonical internal event shape:

```text
channel_kind
channel_installation_id
external_user_id
external_chat_id
surface_kind
message_id
text
attachments
timestamp
raw_metadata_ref
```

### 8.3 Ignore Or Reject

Ignore or reject by default:

- unsupported update types
- channel events with no valid sender
- updates from unauthorized senders

## 9. Identity And Pairing

### 9.1 DM-First Pairing Rule

The Telegram adapter should only allow runtime execution when:

- the sender is allowlisted
- or the sender is explicitly paired

### 9.2 No Username Trust

Do not trust:

- Telegram username alone
- display name alone

The canonical binding should use Telegram numeric user identity.

### 9.3 `/start` Behavior

Recommended:

- if sender is unknown, respond with a pairing-safe message
- do not expose full runtime
- guide the user into pairing or tell them they are unauthorized

### 9.4 Pairing Modes

Recommended v1 modes:

- allowlist only
- first approved DM pairing
- operator-confirmed pairing

## 10. Session Model

### 10.1 Session Shape

For v1 Telegram DMs:

- one direct conversation surface per Telegram user per bot installation

### 10.2 Continuity Rule

Continuity should attach to the canonical session binding, not to Telegram chat names or usernames.

### 10.3 Reset Rule

Any session reset should be:

- explicit
- operator-visible
- not triggered by adapter confusion

## 11. Outbound Delivery

### 11.1 v1 Delivery Scope

Recommended v1 support:

- send text reply
- send simple media later if required

### 11.2 Delivery Rules

- reply to the correct DM surface
- preserve message order where practical
- avoid hidden retries outside the central harness

### 11.3 Rate Limits

Telegram-specific rate handling should stay inside adapter transport logic, but retry visibility should remain in the shared harness.

## 12. Runtime Model

### 12.1 Foreground First

When `gateway start` runs in the foreground, the Telegram adapter should:

- authenticate bot token
- start a visible long-poll loop
- normalize inbound updates
- hand them to the runtime orchestrator

### 12.2 No Private Scheduler

The adapter must not create:

- its own scheduler
- its own retry system
- hidden background daemons

### 12.3 Polling State

Long polling should maintain:

- last seen update id
- current health
- last successful poll
- last poll error

## 13. Security Rules

### 13.1 Unknown Sender Rule

Unknown Telegram users must not get full runtime access.

### 13.2 Token Rule

The bot token must be treated as a secret:

- no plaintext logging
- no debug dumping
- strict secret handling

### 13.3 Group Safety Rule

Do not accidentally treat a group update as a direct trusted surface.

### 13.4 Replay And Duplication Rule

The adapter should dedupe updates based on Telegram update ids and canonical message processing state.

### 13.5 Identity Rule

The adapter must not link Telegram users to humans by username heuristics.

## 14. Health And Doctor

### 14.1 Adapter Health Signals

Required health fields:

- connected
- last successful poll
- last poll error
- bot username
- last update id

### 14.2 Doctor Checks

`spark-intelligence doctor` should verify:

- bot token exists
- token is valid enough for basic API probe
- local state store writable
- adapter config structurally valid
- long-poll start prerequisites satisfied

## 15. Failure Modes To Prevent

This spec should prevent:

- public-webhook complexity blocking local setup
- unknown Telegram users reaching the runtime
- one Telegram user inheriting another user's session
- duplicate update execution from poll confusion
- adapter-local retry loops becoming invisible
- bot token leakage

## 16. Recommended v1 Happy Path

```bash
spark-intelligence channel add telegram
spark-intelligence doctor
spark-intelligence gateway start
```

Then:

- DM the bot
- complete pairing or use allowlist access
- verify one reply

## 17. Final Decision

The original Builder-first Telegram adapter should be:

- long-polling based
- DM-first
- deny-by-default
- identity-safe
- thin
- easy to run locally

It should be the simplest local Builder adapter in Spark Intelligence v1.

For stable launch Telegram ownership, prefer the dedicated `spark-telegram-bot` long-polling gateway path instead of direct Builder polling.
