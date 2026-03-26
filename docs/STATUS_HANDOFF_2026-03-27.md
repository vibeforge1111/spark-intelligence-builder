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

- `@SparkAGI_bot`

Current live provider path:

- provider: `custom`
- base URL: `https://api.minimax.io/v1`
- model: `MiniMax-M2.7`
- auth ref: `CUSTOM_API_KEY`

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

## 4. What Is Proven

The following is no longer theoretical:

- Telegram DM ingress works live
- real provider-backed replies work live
- small-talk fallback no longer dead-ends at the old researcher placeholder
- `/think`, `/think on`, `/think off` work in Telegram
- provider failure and recovery paths were tested earlier and recover correctly
- the supported bootstrap path works on a clean home in tests
- the supported Windows always-on wrapper path works locally through the Startup-folder fallback

Current live autostart wrapper:

- [Spark Intelligence Gateway __tmp-home-live-telegram-real_.cmd](C:/Users/USER/AppData/Roaming/Microsoft/Windows/Start%20Menu/Programs/Startup/Spark%20Intelligence%20Gateway%20__tmp-home-live-telegram-real_.cmd)

## 5. Current Phase Map

Connection phases right now:

- phase A: ready
- phase B: ready
- phase C: ready on the builder side
- phase D: still current
- phase E: materially real

Interpretation:

- phase A is real because Telegram + provider + Researcher are working together on the canonical home
- phase B is real because specialization is active in live runtime state
- phase C is locally ready because Swarm auth/config is present and `swarm status` is healthy
- phase D is still current because routing quality and escalation behavior still need refinement
- phase E is materially real because there is now one supported bootstrap path and one supported always-on wrapper path

## 6. Main Remaining Blockers

### 6.1 Upstream Swarm Sync

The main non-local blocker is still the hosted Spark Swarm sync endpoint.

Current state:

- local Swarm auth/config is present
- local Swarm readiness is good
- real `swarm sync` still returns hosted API `500`

Known request id:

- `88c83e64-395d-4f87-857f-73aebe282ece`

This is now an upstream/server-side issue, not a local Spark Intelligence auth/config issue.

### 6.2 Runtime Quality

The Telegram path is working, but the next important quality step is not infrastructure. It is behavior quality:

- when to stay researcher-first
- when to use direct conversational fallback
- when to recommend Swarm
- how specialized replies should feel for operator/startup use instead of sounding like internal memos

### 6.3 Reproducibility Polish

The first supported product path now exists, but there is still polish left for "another operator can install this with no tribal knowledge":

- tighter runbook wording
- cleaner first-run docs
- possibly one more validation pass from a truly fresh second home

## 7. Best Starting Points For Tomorrow

There are two strong next options.

### Option A: Swarm Backend Debugging

Use tomorrow to inspect why hosted `swarm sync` is failing with `collective_sync_failed`.

Best if the goal is to unblock:

- real Spark Swarm upload
- real collective sync
- later real delegation loops

### Option B: Telegram Runtime Quality

Use tomorrow to improve live routing and response quality on the already-working Telegram path.

Best if the goal is to improve:

- operator-facing usefulness
- specialization feel
- routing clarity
- live conversational quality before adding more surfaces

## 8. Recommended Tomorrow Order

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
   - start from the hosted `500` request id
4. If Telegram quality:
   - inspect live traces
   - tighten route policy defaults
   - tighten response style and specialization behavior

## 9. Repo State

Latest pushed commit at handoff:

- `60a5910` `fix: fall back to windows startup folder autostart`

Current test status at handoff:

- `162` tests passing

Verification command:

```bash
python -m unittest discover -s tests -p "test_*.py"
```
