# Spark Intelligence Discord Operator Runbook 2026-03-26

## 1. Purpose

This runbook captures the narrow Discord v1 operator flow that phase 2 should validate live.

Use it for:

- Discord app setup for signed interaction ingress
- local Spark configuration for Discord DM slash-command handling
- narrow live validation of `/spark message:<text>`
- ingress failure diagnosis and recovery

This is intentionally a DM-only runbook.

It does not cover guild command breadth, components, or modals.

## 2. Assumptions

- `spark-intelligence setup` has already been run
- the operator can edit local Spark Intelligence secrets
- Discord v1 remains signed-interaction-first
- the supported v1 command contract is:
  - DM-only
  - chat-input slash command
  - `/spark message:<text>`

Optional:

- set `SPARK_INTELLIGENCE_HOME` first, or pass `--home <path>` on every command

## 3. Discord App Setup

1. Open the Discord Developer Portal.
2. Create or choose the Discord application for Spark Intelligence.
3. Enable interactions for the application.
4. Copy the application interaction public key.
5. Point the Discord interaction endpoint at your Spark gateway webhook path:

```text
https://<your-host-or-tunnel>/webhooks/discord
```

6. Register the slash command shape:
   - command name: `spark`
   - one required string option: `message`

Do not broaden the command shape for v1.

## 4. Local Spark Configuration

Configure Discord for signed interactions:

```bash
spark-intelligence channel add discord --interaction-public-key <public-key>
```

Expected result:

- Discord channel config is written locally
- signed interaction ingress becomes the active Discord mode
- `doctor` and `gateway status` report Discord ingress as ready

Optional verification:

```bash
spark-intelligence doctor
spark-intelligence gateway status
spark-intelligence status
```

Expected indicators:

- `discord-runtime` is healthy
- Discord ingress mode is `signed_interactions`

## 5. Narrow Live Validation Target

The live phase-2 validation target is exactly this:

1. DM the Discord app user.
2. Run the slash command:

```text
/spark message:hello from discord live test
```

Expected result:

- Discord sends a signed interaction request to `/webhooks/discord`
- Spark accepts the request only if the signature verifies
- Spark rejects guild usage and malformed command shapes
- Spark returns a guarded interaction callback
- gateway traces record the interaction outcome

For v1, this is enough.

Do not widen into guild commands or component flows before this path is proven.

## 6. Failure Checks

If Discord fails, run these in order:

```bash
spark-intelligence doctor
spark-intelligence gateway status
spark-intelligence status
spark-intelligence operator security
spark-intelligence gateway traces --event discord_webhook_auth_failed --limit 20
spark-intelligence gateway traces --limit 20
```

Interpretation:

- `doctor`
  - `discord-runtime` failing means the ingress contract is broken
- `gateway status`
  - shows whether gateway readiness is blocked by provider/runtime issues
- `status`
  - shows first repair hints for runtime/provider/channel blockage
- `operator security`
  - surfaces Discord ingress failures as operator alerts
- `gateway traces`
  - shows bad-signature, malformed, and rejected-ingress events directly

## 7. Common Recovery Commands

If the Discord interaction public key changed or was never set:

```bash
spark-intelligence channel add discord --interaction-public-key <public-key>
```

If you intentionally need the old compatibility path for a controlled local scenario only:

```bash
spark-intelligence channel add discord --allow-legacy-message-webhook --webhook-secret <secret>
```

That is not the normal v1 path.

If Discord is paused:

```bash
spark-intelligence operator set-channel discord enabled
```

If provider execution is the actual blocker rather than Discord ingress:

```bash
spark-intelligence status
spark-intelligence operator security
spark-intelligence jobs tick
spark-intelligence researcher status
```

## 8. Phase-2 Exit Criteria

Discord phase 2 is good enough when all of this is true:

- signed interaction ingress is proven against a real Discord app
- the DM slash command contract works exactly as documented
- bad signatures and malformed requests are trace-visible
- operator/security surfaces point to the right repair commands
- the validation can be repeated from this runbook without rediscovering the setup
