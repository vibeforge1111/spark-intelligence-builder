# Spark Intelligence Telegram Operator Runbook 2026-03-26

## 1. Purpose

This runbook captures the practical Telegram operator flow that was validated live on 2026-03-26.

Use it for:

- first BotFather setup
- first pairing approval
- token rotation
- provider-auth recovery when gateway execution is blocked by OAuth state
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
- existing channel status, pairing mode, and auth linkage are preserved on reruns unless you explicitly change them

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

## 5. Allowlist-Only Access

If you want explicit access without creating a pending pairing queue, configure Telegram in allowlist mode:

```bash
spark-intelligence channel telegram-onboard --bot-token <token> --pairing-mode allowlist --allowed-user <telegram_user_id>
```

Expected result:

- the listed Telegram user can DM immediately
- the user does not appear in `operator review-pairings` unless the operator later approves an explicit pairing
- rerunning the command with a narrower `--allowed-user` set removes stale config-driven access on later messages
- rerunning the command without `--pairing-mode` or `--allowed-user` preserves the current posture instead of silently widening access

If you want to intentionally clear the configured allowlist without changing the rest of the Telegram posture, run:

```bash
spark-intelligence channel telegram-onboard --bot-token <token> --clear-allowed-users
```

For generic scripted updates, the equivalent is:

```bash
spark-intelligence channel add telegram --clear-allowed-users
```

Do not combine `--clear-allowed-users` with `--allowed-user` in the same command.

## 6. Token Rotation

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
- the existing Telegram status and pairing posture remain unchanged unless you explicitly change them

## 7. Fast Recovery Checks

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

If Telegram auth is healthy but the gateway still fails closed on model execution, check provider auth next:

```bash
spark-intelligence auth status
spark-intelligence jobs list
spark-intelligence operator inbox
spark-intelligence operator security
spark-intelligence jobs tick
```

Interpretation:

- `auth status`:
  - `expired` means refresh or login is required now
  - `expiring_soon` means the token is close to expiry and should be repaired before it blocks runtime use
- `jobs list`:
  - shows whether the built-in OAuth maintenance job has run recently and what it last did
- `operator inbox` and `operator security`:
  - surface the exact repair command Spark currently recommends
- `jobs tick`:
  - runs the operator-driven OAuth maintenance pass; use this before escalating to a persistent scheduler design

## 8. Common Recovery Commands

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

Repair provider auth manually if the bridge is blocked:

```bash
spark-intelligence jobs tick
spark-intelligence auth refresh openai-codex
spark-intelligence auth login openai-codex --listen
```

Use them in this order:

1. `jobs tick` when `auth status` shows `expiring_soon` or `doctor` reports stale OAuth maintenance.
2. `auth refresh` when the token is already expired or the maintenance job could not repair it.
3. `auth login --listen` when credentials were revoked or refresh is no longer possible.

## 9. Security Rules

- do not commit the Telegram token
- keep the token only in the local Spark Intelligence `.env`
- rotate the token after accidental exposure
- keep Telegram in DM-first mode unless group behavior is intentionally designed later
- use operator commands for pairing decisions; do not treat normal chat access as control-plane authority
- treat configured allowlists and operator-approved pairings as different tools: allowlists are for explicit static access, pairings are for operator-reviewed DM identity approval

## 10. Proven Live Path

The following path was validated live on 2026-03-26:

1. BotFather token validated successfully
2. `/start` created a pending pairing
3. pending-pairing reply delivered successfully
4. operator approval succeeded
5. next DM was routed through the approved canonical Telegram session
6. reply delivered successfully through the Telegram gateway
7. rotated token was revalidated successfully

That means this runbook reflects an operator path that has already been proven in a real Telegram bot session, not only in simulation tests.
