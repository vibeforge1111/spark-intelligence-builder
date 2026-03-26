# Spark Intelligence Telegram Operator Runbook 2026-03-26

## 1. Purpose

This runbook captures the practical Telegram operator flow that was validated live on 2026-03-26.

Use it for:

- first BotFather setup
- first pairing approval
- token rotation
- gateway health checks
- local recovery when Telegram auth or polling breaks

This is a practical operator document, not a full architecture spec.

## 2. Assumptions

- the repo is installed and runnable
- `spark-intelligence setup` has already been run
- the operator can edit the local Spark Intelligence `.env`
- Telegram is configured in DM-first mode

Optional:

- set `SPARK_INTELLIGENCE_HOME` first, or pass `--home <path>` on every command

## 3. First BotFather Setup

1. Open Telegram and start a chat with `@BotFather`.
2. Run `/newbot`.
3. Choose a display name.
4. Choose a username ending in `bot`.
5. Copy the API token BotFather returns.
6. Run:

```bash
spark-intelligence channel telegram-onboard --bot-token <token>
```

Expected result:

- the token is validated live
- Telegram channel config is written locally
- `TELEGRAM_BOT_TOKEN` is stored in the Spark Intelligence `.env`

Optional verification:

```bash
spark-intelligence channel test telegram
spark-intelligence doctor
```

## 4. First User Pairing

1. Ask the user to send `/start` to the Telegram bot.
2. Pull the update through the gateway:

```bash
spark-intelligence gateway start --once
```

Expected result:

- one update fetched
- `pending_pairing: 1`
- one pending-pairing reply sent back to the user

Inspect the queue:

```bash
spark-intelligence operator review-pairings
spark-intelligence operator review-pairings --channel-id telegram --status pending --json
spark-intelligence operator pairing-summary telegram
```

Approve the latest Telegram request:

```bash
spark-intelligence operator approve-latest telegram --reason "initial live pairing"
```

Verify local auditability:

```bash
spark-intelligence operator history --action approve_latest_pairing --target-kind pairing
```

Then ask the user to send one more DM and run:

```bash
spark-intelligence gateway start --once
spark-intelligence gateway traces --channel-id telegram --limit 20
spark-intelligence gateway outbound --channel-id telegram --limit 20
```

Expected result:

- `processed: 1`
- one `telegram_update_processed` trace
- one `telegram_bridge_outbound` audit row
- first successful reply includes the one-time pairing-approved welcome

## 5. Token Rotation

When rotating the Telegram bot token:

1. Generate the new token in `@BotFather`.
2. Replace the local secret in the Spark Intelligence `.env`:

```env
TELEGRAM_BOT_TOKEN=<new_token>
```

3. Revalidate:

```bash
spark-intelligence channel test telegram
spark-intelligence doctor
spark-intelligence gateway start --once
```

Expected result:

- `channel test telegram` passes
- `doctor` reports `telegram-runtime` as healthy
- `doctor` also reports `.env-permissions` as healthy
- `gateway start --once` authenticates the bot cleanly

## 6. Fast Recovery Checks

If Telegram stops working, run these in order:

```bash
spark-intelligence doctor
spark-intelligence channel test telegram
spark-intelligence operator security
spark-intelligence gateway traces --channel-id telegram --limit 20
spark-intelligence gateway outbound --channel-id telegram --limit 20
spark-intelligence gateway start --once
```

Interpretation:

- `doctor`:
  - `telegram-runtime` failing means auth or poll health is degraded
  - `.env-permissions` failing means local secret-file hardening drifted
- `channel test telegram`:
  - checks the stored token directly against Telegram
- `operator security`:
  - summarizes poll failures, auth failures, duplicates, rate limits, and delivery failures
- `gateway traces`:
  - shows inbound decisions and poll-failure records
- `gateway outbound`:
  - shows what replies were attempted and whether delivery succeeded

## 7. Common Recovery Commands

Pause Telegram ingress without deleting config:

```bash
spark-intelligence operator set-channel telegram paused --reason "temporary maintenance"
```

Resume by setting it back to enabled:

```bash
spark-intelligence operator set-channel telegram enabled --reason "maintenance complete"
```

Hold the latest pending request:

```bash
spark-intelligence operator hold-latest telegram --reason "needs manual review"
```

Revoke the latest pending or held request:

```bash
spark-intelligence operator revoke-latest telegram --reason "denied"
```

## 8. Security Rules

- do not commit the Telegram token
- keep the token only in the local Spark Intelligence `.env`
- rotate the token after accidental exposure
- keep Telegram in DM-first mode unless group behavior is intentionally designed later
- use operator commands for pairing decisions; do not treat normal chat access as control-plane authority

## 9. Proven Live Path

The following path was validated live on 2026-03-26:

1. BotFather token validated successfully
2. `/start` created a pending pairing
3. pending-pairing reply delivered successfully
4. operator approval succeeded
5. next DM was routed through the approved canonical Telegram session
6. reply delivered successfully through the Telegram gateway
7. rotated token was revalidated successfully

That means this runbook reflects an operator path that has already been proven in a real Telegram bot session, not only in simulation tests.
