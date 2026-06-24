# Spark Intelligence Builder Telegram Bridge

Last updated: 2026-04-26

This document describes the current Telegram split between `spark-telegram-bot` and Builder.

## Current Production Split

`spark-telegram-bot` owns:

- the live Telegram bot token;
- long-polling ingress;
- Telegram API delivery;
- gateway-specific rate limits, launch mode, and local mission relay;
- process lifecycle for the Telegram gateway.

Builder owns:

- identity and session meaning behind Telegram events;
- pairing and allowlist state;
- provider/runtime context;
- memory, character, researcher, and swarm bridge decisions;
- operator-visible runtime health.

The gateway is the transport. Builder is the runtime control plane behind it.

## Message Flow

```text
Telegram update
  -> spark-telegram-bot transport validation
  -> normalized gateway request
  -> Builder identity and pairing checks
  -> Builder runtime/provider/memory/research decision
  -> normalized response
  -> spark-telegram-bot delivery
```

## Do Not Double-Own The Token

Only one production process should receive updates for the live Telegram bot token.

Safe modes:

- `spark-telegram-bot` owns live production ingress.
- Builder local adapter tests use a test token or offline simulation.
- Builder bootstrap stores token references for runtime contracts without becoming a second receiver.

Unsafe modes:

- Builder and `spark-telegram-bot` both long-poll the same live token.
- A webhook and a long-polling worker are active against the same bot.
- Local tests reuse production secrets.

## Builder-Side Contract

Builder should receive enough information to make runtime decisions:

```text
channel_kind
external_user_id
external_chat_id
surface_kind
message_id
text
timestamp
trace_id
```

Builder should not require:

- the raw Telegram bot token;
- unrelated Telegram update payloads;
- gateway process secrets;
- full transport retry state.

## Identity Rules

- Use Telegram numeric ids for external identity.
- Treat usernames and display names as labels only.
- Keep DM and group/session semantics separate.
- Fail closed when external id type validation fails.

## Security Rules

- Unknown senders do not reach runtime execution.
- Pairing and allowlist checks happen before expensive runtime work.
- Public denial text should not reveal whether the issue was unknown device, missing scope, bad pairing, or held approval.
- Logs may include trace ids and normalized ids, but not bot tokens or provider keys.

## Gateway Trace Proof Continuity

Every Builder gateway trace row is redacted before it is written. Processed Telegram rows with request and trace continuity also carry proof-continuity metadata.

- If Telegram supplies a valid `harnessProofRef`, Builder preserves it.
- If no fresh Harness proof is available, Builder writes a compact `spark.harness_proof.v1` gap capsule with `proofStatus: missing_harness_authority`, `proofStorage: source_gap_capsule`, `authority.contract: none`, and `governor.verified: false`.
- Historical gateway traces can be repaired with `spark-intelligence gateway repair-proof --home <spark-intelligence-state-home> --json`. The repair keeps redaction intact, writes `proofStorage: legacy_gap_capsule`, and creates a `.proof-backup`.

Gap capsules are traceability, not authorization. They make Builder gateway rows inspectable while keeping missing authority visible to the control-proof audit.

## Local Verification

Use offline or controlled checks where possible:

```powershell
spark-intelligence doctor
spark-intelligence operator review-pairings
spark-intelligence gateway simulate-telegram-update --help
```

For gateway-owned behavior, run the `spark-telegram-bot` build and test suite in that repo.

## Change Rules

Any change to the Telegram bridge should update this document when it:

- changes which repo owns ingress;
- changes the normalized request shape;
- changes pairing or allowlist behavior;
- changes secret handling;
- changes runtime fallback behavior.
