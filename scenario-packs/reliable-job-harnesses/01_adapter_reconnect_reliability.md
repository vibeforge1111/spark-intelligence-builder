# Scenario 01: Adapter Reconnect Reliability

## Prompt

`Design the reconnect and heartbeat behavior for Telegram after temporary network loss. Keep it reliable, visible, and lightweight.`

## What This Is Testing

- correct classification as scheduled work vs event-driven work
- reconnect retry discipline
- stale-lock and flap-loop prevention
- operator trust and repair path

## Strong Answer Signals

- treats adapter transport as event-driven where possible
- uses the shared harness only for bounded reconnect retries or health probes
- defines a flap-loop limit
- defines what `doctor` should detect
- avoids adapter-owned private timers

## Failure Signals

- creates a second scheduler inside the adapter
- treats every reconnect condition as cron
- hides retries in the adapter transport layer
- has no visible operator repair path

## Minimum Output Shape

```text
Job Owner:
Work Classification:
Trigger Type:
Retry Policy:
Doctor Path:
Smoke Tests:
Why This Stays Lightweight:
```
