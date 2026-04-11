# Memory Live Validation Results 2026-04-11

## Scope

This run validated the active two-contender program:

1. `summary_synthesis_memory`
2. `dual_store_event_calendar_hybrid`

The goal was to keep offline ProductMemory comparison and live Telegram validation tied together so promotions do not happen on scorecards alone.

## Confirmed Results

- Live Telegram regression: `31/31` matched
- KB compile: valid
- KB probe coverage: `38/38` current-state and `38/38` evidence hits
- Telegram soak status: `27/27` completed, `0` failed
- Live overall leader: `summary_synthesis_memory`
- Offline ProductMemory leader: `dual_store_event_calendar_hybrid`

## Soak Aggregate

- `summary_synthesis_memory`: `27/138` matched, `19.57%` aggregate accuracy
- `dual_store_event_calendar_hybrid`: `21/138` matched, `15.22%` aggregate accuracy

## Interpretation

The current decision should hold:

1. Keep `summary_synthesis_memory` as the live runtime favorite.
2. Keep `dual_store_event_calendar_hybrid` as the active offline challenger.
3. Do not promote on offline scorecards alone.
4. Keep live Telegram regression and soak as mandatory gates.

## Current Gap

The comparison harness still reports the Builder runtime selector as effectively unresolved rather than explicitly pinned to a named memory architecture. The next implementation step is to wire a controlled runtime selector so Builder can intentionally run the live winner instead of only benchmarking around it.

## Artifacts

- `.spark-intelligence/artifacts/telegram-memory-regression/telegram-memory-regression.json`
- `.spark-intelligence/artifacts/telegram-memory-regression/regression-summary.md`
- `.spark-intelligence/artifacts/telegram-memory-regression/architecture-live-comparison/telegram-memory-architecture-live-comparison.json`
- `.spark-intelligence/artifacts/telegram-memory-architecture-soak/telegram-memory-architecture-soak.json`
