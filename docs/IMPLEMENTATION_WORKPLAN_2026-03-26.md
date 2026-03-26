# Spark Intelligence Implementation Workplan 2026-03-26

## 1. Handoff Baseline

This workplan starts from the latest prior-session handoff already committed in this repo:

- commit `b801f85`
- timestamp: 2026-03-26 03:05 +04
- summary: `docs: update implementation status and next steps`

That handoff is directionally correct.

The repo is not blocked on missing doctrine.

It is blocked on missing repeatable regression coverage for the current Telegram/operator vertical slice.

## 2. Confirmed Completed Work

Code inspection confirms these plan phases are already materially done for the first vertical slice:

- package and CLI entrypoint
- canonical config bootstrap and SQLite state bootstrap
- operator identity, pairing, allowlist, and session state transitions
- Telegram DM runtime and simulation path
- Spark Researcher bridge with external-or-stub boundary handling
- Spark Swarm bridge status and sync surface
- gateway trace and outbound audit logs
- operator review, summary, and history surfaces

This means the implementation plan should now be read as:

- phases 0 through 5: complete for the narrow v1 slice
- phase 6: partially complete
- phase 7: still pending at the implementation-harness level

## 3. Confirmed Remaining Work

The remaining work from the previous handoff is still real and still ordered correctly.

What remains highest priority:

- create a real `tests/` directory
- choose the lightest viable local runner
- add regression coverage for Telegram pairing and operator-control flows
- add regression coverage for filtered trace, outbound, review, and history surfaces
- add failure-path tests for Telegram auth, polling, duplicates, and rate limits
- run the implementation through the security review gates after the harness exists

## 4. Maintainability And Risk Read

Maintainability risks:

- the repo still depends too much on manual verification
- operator-critical behavior can drift without a fast local regression loop
- observability surfaces exist, but their filters are not yet protected by tests

Simplification opportunities:

- use built-in `unittest` first instead of adding a new dependency just to start coverage
- test state-transition functions directly where CLI subprocess coverage adds little value
- share one temp-home bootstrap helper so every test runs against the real config and SQLite layout

Ownership boundary mistakes to avoid:

- do not copy Spark Researcher logic into local test helpers
- do not add adapter-specific side stores outside the canonical SQLite state and log files
- do not start Discord or WhatsApp runtime work before the Telegram slice is stable

Security issues still open:

- authorization and denied-reply behavior are not yet locked down by regression coverage
- duplicate and rate-limit protections exist but are not yet covered by automated tests
- ship readiness should still be considered incomplete until phase-7 review runs on the implementation diff

Documentation gaps:

- the README still needed a same-day execution pointer instead of only a prior-session status note
- today’s work order was not yet recorded as an execution plan tied to a specific commit baseline

Tests needed next:

- pending pairing
- hold latest
- approve latest
- revoke latest
- revoked user reply
- operator history exact target logging
- filtered `gateway traces`
- filtered `gateway outbound`
- filtered `operator review-pairings`
- filtered `operator history`
- Telegram auth failure persistence
- Telegram poll failure persistence
- duplicate suppression
- rate limiting

## 5. Today Execution Order

Today should proceed in this order:

1. Add a lightweight `unittest` harness and shared temp-home bootstrap helper.
2. Lock down Telegram pairing/operator regressions first:
   - pending pairing
   - hold latest
   - approve latest
   - revoke latest
   - revoked user reply
   - operator history exact target logging
3. Lock down observability filters:
   - `gateway traces`
   - `gateway outbound`
   - `operator review-pairings`
   - `operator history`
4. Add Telegram failure-path tests:
   - auth failure persistence
   - poll failure persistence
   - duplicate suppression
   - rate limiting
5. Reassess whether the next slice should be doctor polish or runtime hardening only after the above is green.

## 6. Commit Checkpoints

Commit often, but keep each commit narrow:

1. docs handoff and same-day workplan alignment
2. test harness plus Telegram pairing/operator regressions
3. observability filter regressions
4. Telegram failure-path regressions
5. any follow-up cleanup required by test results

## 7. Non-Goals For Today

Do not spend today on:

- live Discord runtime
- live WhatsApp runtime
- webhook-first Telegram
- copied Spark Researcher or Spark Swarm internals
- new memory ownership inside this repo
