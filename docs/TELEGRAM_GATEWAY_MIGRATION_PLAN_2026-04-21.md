# Telegram Gateway Migration Plan

Date: 2026-04-21
Status: planned

## Purpose

Define the path for eventually moving Telegram ingress ownership from `spark-telegram-bot` into `spark-intelligence-builder`.

This is a migration plan, not a current-state doc.

## Current Live State

Today the stable live path is:

`Telegram -> spark-telegram-bot webhook gateway -> Builder/Spark logic -> Spawner UI when execution is needed`

That split exists for a good reason:

- `spark-telegram-bot` is the proven single-owner ingress process
- `spark-intelligence-builder` remains the richer Spark runtime
- `spawner-ui` remains the execution backend

The current live system should not be destabilized by an immediate merge.

## Why Migrate Later

Long term, Builder is the more natural home for:

- Telegram ingress
- persistent memory
- personality and voice state
- runtime routing
- operator and identity control

That would reduce split-brain between:

- a thin standalone Telegram gateway
- the richer Builder runtime that already owns more of Spark’s durable logic

## Non-Goal

Do not collapse everything into one undifferentiated runtime.

Even after migration, these boundaries should remain:

- Builder owns Telegram ingress and outbound sending
- Spawner remains a separate execution plane
- mission state still comes from Spawner
- Telegram remains a control and summary surface, not a second workflow model

## Migration Principles

1. One Telegram token owner at all times.
2. One webhook receiver at all times.
3. Builder must match the current gateway contract before cutover.
4. Spawner relay behavior must stay the same through cutover.
5. Migration must be reversible.
6. `spark-telegram-bot` remains the production ingress until Builder proves parity.

## Required Parity Before Cutover

Builder must support all of these before it becomes the Telegram ingress owner:

- webhook secret validation
- `update_id` dedupe
- strict single-owner startup enforcement
- admin-only mission control commands
- `/run`, `/mission`, and `/board`
- Spawner relay receiver with shared-secret protection
- mission correlation:
  - `missionId`
  - `requestId`
  - `chatId`
  - `userId`
  - `update_id`
- concise terminal mission updates back to Telegram

## Proposed Phases

### Phase 1. Freeze The Gateway Contract

Treat `spark-telegram-bot` as the reference implementation for:

- webhook behavior
- command routing
- relay behavior
- startup ownership rules

Verify:

- Builder docs and gateway docs describe the same contract

### Phase 2. Port The Contract Into Builder

Add Builder-native equivalents for:

- webhook ingress
- secret validation
- dedupe
- startup mode enforcement
- relay receiver

Verify:

- Builder can handle synthetic webhook updates without owning the production token

### Phase 3. Port Command Behavior

Builder must match the current user-facing behavior for:

- `/start`
- `/myid`
- `/run`
- `/board`
- `/mission status|pause|resume|kill`

Verify:

- command outputs are compatible enough that operator behavior does not need to change

### Phase 4. Shadow Validation

Run Builder’s Telegram ingress in a non-owning validation mode:

- same command handling
- same relay logic
- no production Telegram ownership

Verify:

- live updates mirrored into Builder produce the same routing and mission-control behavior as the standalone gateway

### Phase 5. Controlled Cutover

Switch Telegram webhook ownership from `spark-telegram-bot` to Builder only when:

- parity is verified
- recovery path is documented
- rollback is tested

Verify:

- `/start`, `/run`, `/board`, and mission completion updates all work after the switch

### Phase 6. Retirement

Once Builder has owned Telegram ingress stably:

- freeze `spark-telegram-bot`
- keep it as an archive/reference repo or minimal fallback
- do not keep dual active ingress paths

## Cutover Criteria

Do not cut over until:

1. Builder matches the gateway contract.
2. Builder survives restart and recovery cleanly.
3. Builder handles Spawner relay correctly.
4. Telegram ownership cannot be stolen by a fallback poller.
5. Rollback to `spark-telegram-bot` is documented and tested.

## Recommendation

Short term:

- keep `spark-telegram-bot` as the production ingress owner

Medium term:

- build Builder-native webhook parity behind the current gateway

Long term:

- move Telegram ingress into Builder only after deliberate parity and cutover validation
