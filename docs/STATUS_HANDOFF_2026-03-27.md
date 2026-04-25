# Spark Intelligence Handoff 2026-03-27

## 1. Purpose

This note captures where Spark Intelligence Builder stands at the end of 2026-03-27, what was built today, what is already working on the real canonical home, and what the next starting points should be tomorrow.

## 2. Current Real State

Spark Intelligence Builder now has one fully supported live path on this machine:

- Telegram as the active delivery surface
- `Spark Researcher` connected as the runtime core
- `custom` provider configured against MiniMax
- active model aligned to `MiniMax-M2.7`
- active chip `startup-yc`
- active path `startup-operator`
- supported bootstrap profile installed
- supported always-on Windows wrapper installed

Canonical live home:

- [`.tmp-home-live-telegram-real`](../.tmp-home-live-telegram-real)

Current live status on that home:

- `doctor: ok`
- `gateway: ready`
- `provider runtime: ok`
- `provider execution: ok`
- `install profile: telegram-agent`
- `default gateway mode: continuous`
- `autostart: enabled windows_startup_folder`

Current Telegram bot identity:

- configured locally in the operator environment

Current live provider path:

- provider: configured locally
- auth ref: local secret store

Current live specialization:

- active chips: `startup-yc`
- active path: `startup-operator`

## 3. What Was Built Today

Today moved the repo from "working pieces" into a materially productized first path.

Shipped today:

- runtime route visibility
  - `status` now shows last bridge route and active chip route
  - `gateway traces` and `gateway outbound` now carry route/chip metadata
- runtime route explanation
  - `spark-intelligence connect route-policy`
- runtime route tuning
  - `spark-intelligence connect set-route-policy`
  - operator-tunable conversational fallback and Swarm recommendation thresholds
- DOP/domain-chip standardization
  - generic manifest-backed `spark-hook-io.v1` execution
  - active-chip `evaluate` output now shapes live bridge behavior
- supported Telegram bootstrap profile
  - `spark-intelligence bootstrap telegram-agent`
- supported Windows always-on wrapper
  - `spark-intelligence install-autostart`
  - `spark-intelligence uninstall-autostart`
  - Task Scheduler first, Startup-folder fallback when Task Scheduler is denied
- MiniMax support path corrected and aligned
  - supported path is now `MiniMax-M2.7`
  - canonical live home updated to match
- Telegram runtime-quality hardening
  - active-chip doctrine is now passed to provider chat as hidden background guidance instead of raw memo scaffolding
  - Telegram reply cleanup now strips common internal memo headings plus `Confidence` / `Evidence gap` residue before delivery
  - memo-shaped chip output is now rewritten into cleaner Telegram-style replies with the recommendation or primary answer first and a short `Next:` line when useful
  - common internal prefixes like `Based on the research notes provided, ...` are now stripped before Telegram delivery
- Swarm phase/operator surfacing hardening
  - `connect status` now treats hosted Swarm auth rejection as a real phase C blocker instead of counting any token-shaped value as effectively ready
  - `connect route-policy` and `swarm status` now surface the latest auth rejection/failure mode directly
- Swarm session lifecycle hardening
  - Builder now distinguishes `configured`, `expired`, `refreshable`, and `auth_rejected` Swarm session states instead of collapsing all token-shaped values into one state
  - `swarm sync` can now refresh an expired session once and retry the hosted upload when a refresh token and auth client key are configured
  - `swarm configure` now accepts `--refresh-token`, `--refresh-token-env`, `--auth-client-key`, `--auth-client-key-env`, and `--supabase-url`
- Swarm live sync recovery
  - the canonical home now carries a refreshable Builder-side Swarm session with access token, refresh token, and auth client key material present
  - Builder now normalizes missing contradiction `status` values to `open` before upload so live Researcher payloads conform to the hosted Swarm contract
  - successful Swarm uploads now clear stale `last_failure` state so `swarm status` reflects the current live outcome instead of the last historical 500

## 4. What Is Proven

The following is no longer theoretical:

- Telegram DM ingress works live
- real provider-backed replies work live
- small-talk fallback no longer dead-ends at the old researcher placeholder
- `/think`, `/think on`, `/think off` work in Telegram
- `/swarm`, `/swarm status`, `/swarm sync`, and `/swarm evaluate <task>` now work as Telegram runtime commands
- provider failure and recovery paths were tested earlier and recover correctly
- the supported bootstrap path works on a clean home in tests
- the supported Windows always-on wrapper path works locally through the Startup-folder fallback
- live hosted Spark Swarm sync now works from the canonical Builder home

Current live autostart wrapper:

- [Spark Intelligence Gateway __tmp-home-live-telegram-real_.cmd](<user-home>/AppData/Roaming/Microsoft/Windows/Start%20Menu/Programs/Startup/Spark%20Intelligence%20Gateway%20__tmp-home-live-telegram-real_.cmd)

## 5. Current Phase Map

Connection phases right now:

- phase A: ready
- phase B: ready
- phase C: ready
- phase D: locally hardened again
- phase E: materially real

Interpretation:

- phase A is real because Telegram + provider + Researcher are working together on the canonical home
- phase B is real because specialization is active in live runtime state
- phase C is real because the canonical home can now authenticate to hosted Swarm and complete a real collective sync upload
- phase D is more mature now because routing visibility, route tuning, and Telegram delivery cleanup are all in place, but escalation behavior still needs refinement
- phase E is materially real because there is now one supported bootstrap path and one supported always-on wrapper path

## 6. Main Remaining Blockers

### 6.1 Runtime Quality

The next important work is now behavior quality, not connection repair.

Current live state on the canonical home:

- hosted Swarm sync succeeds from the canonical home
- `swarm status` now reports:
  - `auth_state: configured`
  - `last_sync_mode: uploaded`
  - no active `last_failure`
- `connect status` now marks phase C as `ready`

Current behavior focus:

- when to stay researcher-first
- when to use direct conversational fallback
- when to recommend or escalate into Swarm
- how specialized replies should feel for operator/startup use instead of sounding like internal memos

### 6.2 Reproducibility Polish

The first supported product path now exists, but there is still polish left for "another operator can install this with no tribal knowledge":

- tighter runbook wording
- cleaner first-run docs
- possibly one more validation pass from a truly fresh second home

### 6.3 Hosted Swarm Follow-Through

The Builder-side Swarm blocker is gone, but there is still one important follow-through task on the actual product behavior:

- `swarm status` now reports:
- prove one intentional Telegram-originated Swarm escalation path instead of only direct `swarm sync`
- decide which route-policy defaults should become the recommended production defaults now that hosted Swarm is actually reachable

## 7. Exact Remaining Work

What is still left from here, grouped by practical priority:

### 7.1 Must-Finish Core Work

- prove one intentional Telegram-originated Swarm escalation path instead of only local payload readiness
- tighten live Telegram response quality further so specialized replies feel operator-useful and not just technically correct
- decide the recommended production route-policy defaults now that hosted Swarm sync is live

### 7.2 Runtime Quality Work

- refine the decision rule for:
  - `researcher_advisory`
  - `provider_execution`
  - `provider_fallback_chat`
  - Swarm recommendation or escalation
- improve how startup/operator specialization shows up in normal Telegram replies
- decide which route-policy defaults should become the recommended production defaults on the canonical home
- review more live traces from the canonical home and remove any remaining memo-like or internal-looking answer patterns

### 7.3 Productization Polish

- add one cleaner runbook for boot, restart, stop, logs, and health checks for the always-on Telegram path
- tighten the fresh-operator install story so it is not only smoke-tested but documented as the primary supported setup
- make the canonical `.env` and home contract more explicit in docs
- validate the supported bootstrap and autostart path one more time from a truly fresh operator-style home

### 7.4 Deferred Expansion Work

- Discord remains deferred until the Telegram plus Researcher plus Swarm path is complete
- WhatsApp remains deferred for the same reason
- Codex OAuth remains optional/deferred unless it becomes necessary for the primary production provider story

## 8. Best Starting Points For Tomorrow

There are two strong next options.

### Option A: Swarm Session Recovery

Use tomorrow to turn the now-working hosted Swarm connection into a real runtime behavior.

Best if the goal is to unblock:

- intentional Swarm recommendation/escalation from Telegram
- route-policy finalization
- later real delegation loops

### Option B: Telegram Runtime Quality

Use tomorrow to improve live routing and response quality on the already-working Telegram path.

Best if the goal is to improve:

- operator-facing usefulness
- specialization feel
- routing clarity
- live conversational quality before adding more surfaces

## 9. Recommended Tomorrow Order

Recommended order:

1. Confirm live canonical home still shows:
   - `install profile: telegram-agent`
   - `autostart: enabled windows_startup_folder`
   - `provider: custom`
   - `model: MiniMax-M2.7`
2. Decide whether tomorrow is:
   - Swarm backend day
   - Telegram runtime-quality day
3. If Swarm:
   - start from `spark-intelligence swarm status --home .tmp-home-live-telegram-real`
   - confirm `last_sync_mode: uploaded`
   - trigger one intentional Swarm recommendation or escalation path
4. If Telegram quality:
   - inspect live traces
   - tighten route policy defaults
   - tighten response style and specialization behavior

## 10. Repo State

Latest pushed commit:

- `ecf317d` `fix: clean telegram memo-style replies`

Current test status at handoff:

- full suite is green: `174` tests passing

Verification command:

```bash
python -m unittest discover -s tests -p "test_*.py"
```
