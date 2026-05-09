# Codex Handoff: Agent Operating Context and Route Probes

Date: 2026-05-07  
Repo: `C:\Users\USER\Desktop\spark-intelligence-builder`  
Branch: `codex/creator-mission-status-builder`  
Primary installed runtime: `C:\Users\USER\.spark\modules\spark-intelligence-builder\source`  
Primary live Spark home: `C:\Users\USER\.spark\state\spark-intelligence`

## Current Goal

Improve Spark's conversational self-improvement and agent-operating-context layer so agents can tell what is actually possible before acting.

The current focus is not one deterministic feature like voice. The broader goal is to let users naturally ask Spark to gain or change capabilities, route that request through the correct capability proposal / Mission Control / Spawner / Codex path, and preserve a stable upgrade-safe contract so future Spark updates do not break user-added capabilities.

The recent work centered on:

- Agent Operating Context (`/aoc`, `/agent_context`, `/operating_context`, `/context`) as the agent preflight surface.
- Route probes (`/probe memory`, `/probe core`, `/probe builder`, `/probe browser`, `/probe swarm`, etc.).
- Browser-use adapter migration.
- Mission memory loop guardrails.
- Watchtower and doctor hardening so the live system distinguishes real failures from planned rollouts or managed conditions.

## Completed In This Session

### Agent Operating Context

- Added and hardened the Agent Operating Context surface so Telegram can show:
  - best route for the request,
  - Spark access level,
  - actual runner writability,
  - route health,
  - route evidence,
  - stale or contradicted context,
  - source ledger,
  - guardrails.
- Fixed command aliases so underscore forms work reliably:
  - `/operating_context`
  - `/agent_context`
  - `/aoc`
  - `/conversation_context`
- Observed that hyphenated commands like `/operating-context` did not route correctly in Telegram. Treat this as a Telegram command parsing/alias issue if it returns.
- Improved AOC task-fit selection so `/aoc fix mission memory loop` and `/aoc can you install a voice to yourself?` no longer default to plain chat when the request clearly implies local code, installation, or capability work.
- Added runner preflight evidence so AOC can report `current writable runner` when Telegram runner write/delete proof exists.

### Route Probes

- Added route probe support and live Telegram smoke tests for:
  - memory,
  - core probe batch,
  - researcher,
  - builder,
  - spawner,
  - browser,
  - swarm.
- Probe evidence now flows into AOC route status.
- Builder moved from degraded to healthy after `.env` permissions / gateway readiness were repaired.
- Spawner moved from degraded to healthy after route probe interpretation was improved.
- Memory and Researcher probes were healthy.
- Swarm is intentionally not fully online yet. AOC now labels Swarm payload/API-not-ready evidence as planned rollout state instead of a route repair.

### Browser-Use Migration

- Browser route no longer treats missing legacy `spark-browser` extension as the main truth.
- Browser probes now use the browser-use adapter status contract.
- Missing browser-use status is reported as planned migration evidence:
  - status source not ready,
  - package/CLI unavailable,
  - expected status path missing.
- Fixed browser-use status path for nested Builder homes:
  - expected shared status path: `C:\Users\USER\.spark\state\browser-use\status.json`
  - not: `C:\Users\USER\.spark\state\spark-intelligence\state\browser-use\status.json`
- AOC now labels this route as `Spark Browser: planned, browser-use adapter migration pending`.

### Mission Memory Loop

- Implemented or tested mission lesson candidate staging enough to prove the system can avoid automatic raw completion-log memory writes.
- User disliked the current Telegram prompt shape:
  - too confusing,
  - not a good memory schema yet,
  - should be hidden for now.
- Current decision: keep mission lesson candidate logic as an internal/open design surface and do not expose the user-facing prompt again until Rec/agent feedback defines a better lesson schema and approval UX.
- Shipped-project context can remain operational state; reusable memory lessons should stay approval-gated.

### Watchtower / Doctor Hardening

- Fixed stale memory lane labels before stop-ship.
- Accepted request-level proof for legacy simulator no-run intents.
- Fixed `stop_ship_runtime_state_authority` for typed Telegram mirrors.
- Fixed `stop_ship_memory_contract` / memory-role handling.
- Fixed `stop_ship_plugin_provenance` to use typed `provenance_mutation_log` instead of only recent mixed event windows.
- Treated Swarm payload/API not-ready evidence as planned rollout, not AOC repair.
- Treated watchtower scheduler freshness `degraded` as advisory when no stalled/open background runs exist.
- Treated medium `promotion_contamination` observer incidents as managed when every actionable incident has a repair-plan observer packet.
- Fixed false positive `watchtower-personality-mirrors` drift by comparing runtime mirrors against full typed-table human sets instead of recent display windows.
- Repaired live personality/identity import readiness by attaching and activating local hook providers through Builder's CLI:
  - `C:\Users\USER\Desktop\spark-personality-chip-labs`
  - `C:\Users\USER\Desktop\spark-swarm`

### Live Health State At Handoff

Latest live doctor summary:

```text
ok=True
attachments True 11 discovered (8 chips, 3 paths)
watchtower-personality-mirrors True trait_profiles=5 observation_rows=100 evolution_rows=26 mirror_drift=0
watchtower-personality-import True enabled=yes required=yes canonical_agents=58 personality_import_ready=yes available_personality_hook_chips=1 active_personality_hook_chips=1
watchtower-agent-identity-import True builder_local=58 identity_import_ready=yes available_identity_hook_chips=1 active_identity_hook_chips=1
```

## Files Touched Or Investigated

### Committed Source Files

- `src/spark_intelligence/observability/store.py`
  - Added full typed personality human-set lookup for mirror drift.
  - Updated personality panel drift calculation to use full typed sets.
- `tests/test_builder_prelaunch_contracts.py`
  - Added regression coverage for >100 mirrored observation humans with zero drift.

Recent relevant commits:

- `13825f8 Use full typed sets for personality mirror drift`
- `cf53332 Treat repaired promotion incidents as managed`
- `5f65a04 Treat watchtower freshness lag as advisory`
- `1b8d955 Mark Swarm payload readiness as planned rollout`
- `557c69d Use typed ledger for personality provenance stop ship`
- `87f1aec Polish browser-use AOC path and label`
- `6ca3b99 Use browser-use contract for browser probes`
- `0a7e440 Cover browser-use evidence promotion`
- `f54cf09 Keep inactive legacy browser in migration state`
- `7375974 Wire browser-use route evidence`
- `f72f96f Repair stale memory lane labels before stop ship`
- `037f3cd Accept request-level proof for legacy simulator intents`

### Live Runtime / Config Changed

These are live machine changes, not git commits:

- Added chip root:
  - `C:\Users\USER\Desktop\spark-personality-chip-labs`
- Added chip root:
  - `C:\Users\USER\Desktop\spark-swarm`
- Activated chip:
  - `spark-personality-chip-labs`
- Activated chip:
  - `spark-swarm`
- Resynced:
  - `C:\Users\USER\.spark\state\spark-intelligence\attachments.snapshot.json`

### Files Investigated

- `src/spark_intelligence/doctor/checks.py`
  - Watchtower checks, import checks, freshness, observer incidents.
- `src/spark_intelligence/observability/store.py`
  - Personality and agent identity watchtower panels.
- `src/spark_intelligence/attachments/snapshot.py`
  - Attachment snapshot and hook import summaries.
- `src/spark_intelligence/attachments/registry.py`
  - Chip root discovery, configured roots, manifest scanning.
- `src/spark_intelligence/browser/service.py`
  - Browser-use adapter status path and status contract.
- `src/spark_intelligence/self_awareness/operating_context.py`
  - AOC route status and planned rollout classification.
- `src/spark_intelligence/self_awareness/route_probe.py`
  - Route probe execution and evidence recording.
- `src/spark_intelligence/observability/checks.py`
  - Stop-ship plugin/personality provenance checks.
- `src/spark_intelligence/cli.py`
  - Attachment CLI, doctor/status output, agent import commands.
- `src/spark_intelligence/system_registry/registry.py`
  - Browser-use and Swarm registry status.
- `tests/test_self_awareness.py`
  - AOC, browser-use, route-probe, Swarm planned rollout tests.
- `tests/test_builder_prelaunch_contracts.py`
  - Doctor/watchtower/stop-ship tests.
- `tests/test_cli_smoke.py`
  - Doctor output and repair-hint expectations.
- `tests/test_attachment_hooks.py`
  - Hook execution/import behavior.
- `tests/test_support.py`
  - Fake chip manifests and hook behavior.
- `C:\Users\USER\Desktop\spark-personality-chip-labs\spark-chip.json`
  - Confirmed `personality` hook manifest exists.
- `C:\Users\USER\Desktop\spark-swarm\spark-chip.json`
  - Confirmed `identity` hook manifest exists.

### Session Tracker Files

The chat/session tracker is outside this repo:

- `C:\Users\USER\Documents\Codex\2026-05-06\lets-make-this-chat-about-improving\TASK.md`
- `C:\Users\USER\Documents\Codex\2026-05-06\lets-make-this-chat-about-improving\PHASES.md`

Those were updated with:

- Phase 37 - Personality Mirror Drift Window
- Phase 38 - Live Personality and Identity Hook Attachment

## Commands And Tests Already Run

### Source Tests

```powershell
python -m pytest tests/test_builder_prelaunch_contracts.py -k "personality_mirror or personality_runtime_mirror"
```

Result: `2 passed, 87 deselected`.

```powershell
python -m pytest tests/test_builder_prelaunch_contracts.py -k "personality_import or agent_identity_import or personality_mirror"
```

Result: `6 passed, 83 deselected`.

Other targeted tests run during the broader session included:

```powershell
python -m pytest tests/test_self_awareness.py::SelfAwarenessCapsuleTests::test_browser_route_probe_reports_browser_use_status_contract_when_missing tests/test_self_awareness.py::SelfAwarenessCapsuleTests::test_browser_route_probe_uses_shared_spark_root_from_nested_builder_home tests/test_self_awareness.py::SelfAwarenessCapsuleTests::test_browser_route_probe_records_browser_use_adapter_success tests/test_self_awareness.py::SelfAwarenessCapsuleTests::test_agent_operating_context_keeps_missing_browser_use_contract_as_planned_migration tests/test_self_awareness.py::SelfAwarenessCapsuleTests::test_agent_operating_context_does_not_hide_browser_use_adapter_warnings_as_planned -q
```

```powershell
python -m pytest tests/test_builder_prelaunch_contracts.py::BuilderPrelaunchContractTests::test_stop_ship_plugin_provenance_uses_typed_personality_ledger_beyond_recent_event_window tests/test_builder_prelaunch_contracts.py::BuilderPrelaunchContractTests::test_build_researcher_reply_records_personality_influence_provenance -q
```

```powershell
python -m pytest tests/test_self_awareness.py::SelfAwarenessCapsuleTests::test_recent_route_probe_failure_downgrades_aoc_route_status tests/test_self_awareness.py::SelfAwarenessCapsuleTests::test_swarm_payload_not_ready_is_planned_rollout_not_repair -q
```

```powershell
python -m pytest tests/test_builder_prelaunch_contracts.py::BuilderPrelaunchContractTests::test_doctor_treats_background_freshness_degraded_as_advisory tests/test_builder_prelaunch_contracts.py::BuilderPrelaunchContractTests::test_doctor_report_includes_watchtower_health_checks -q
```

```powershell
python -m pytest tests/test_builder_prelaunch_contracts.py::BuilderPrelaunchContractTests::test_doctor_treats_repair_planned_medium_promotion_blocks_as_managed tests/test_builder_prelaunch_contracts.py::BuilderPrelaunchContractTests::test_doctor_report_includes_observer_incident_check tests/test_builder_prelaunch_contracts.py::BuilderPrelaunchContractTests::test_doctor_ignores_info_only_observer_incidents -q
```

### Compile Checks

```powershell
python -m compileall src/spark_intelligence/observability/store.py
```

Other compile checks run earlier:

```powershell
python -m compileall src/spark_intelligence/doctor/checks.py
python -m compileall src/spark_intelligence/self_awareness/operating_context.py
python -m compileall src/spark_intelligence/observability/checks.py
```

### Installed Runtime Checks

```powershell
python -m spark_intelligence.cli doctor --home C:/Users/USER/.spark/state/spark-intelligence --json
```

Latest result: `ok=true`.

Summary command used:

```powershell
$json = python -m spark_intelligence.cli doctor --home C:/Users/USER/.spark/state/spark-intelligence --json | ConvertFrom-Json
Write-Output "ok=$($json.ok)"
$json.checks | Where-Object { $_.name -in @('attachments','watchtower-personality-mirrors','watchtower-personality-import','watchtower-agent-identity-import') } | ForEach-Object { Write-Output "$($_.name) $($_.ok) $($_.detail)" }
```

### Live Attachment Repair Commands

```powershell
python -m spark_intelligence.cli attachments add-root chips C:/Users/USER/Desktop/spark-personality-chip-labs --home C:/Users/USER/.spark/state/spark-intelligence
python -m spark_intelligence.cli attachments add-root chips C:/Users/USER/Desktop/spark-swarm --home C:/Users/USER/.spark/state/spark-intelligence
python -m spark_intelligence.cli attachments activate-chip spark-personality-chip-labs --home C:/Users/USER/.spark/state/spark-intelligence
python -m spark_intelligence.cli attachments activate-chip spark-swarm --home C:/Users/USER/.spark/state/spark-intelligence
python -m spark_intelligence.cli attachments snapshot --home C:/Users/USER/.spark/state/spark-intelligence --json
```

### Telegram Commands Tested By User

```text
/context
/conversation_context
/operating_context
/agent_context
/aoc
/aoc fix mission memory loop
/aoc can you install a voice to yourself?
/probe memory
/probe core
/probe builder
/probe spawner
/probe browser
/probe swarm
```

Important observed behavior:

- `/operating-context` and `/agent-context` did not respond correctly.
- `/operating_context`, `/agent_context`, and `/aoc` worked.
- After fixes, `/aoc fix mission memory loop` recommended `current writable runner` when runner preflight was writable.
- Browser remains planned until browser-use writes status proof.
- Swarm remains planned for full payload/API rollout even though the identity hook provider is now active.

## Known Errors, Warnings, Or Failing Checks

### Current Repo Dirty State

At handoff, `git status --short` showed many modified files and one untracked test file that appear to be from parallel/other-terminal work, not from this Codex session:

```text
 M docs/CONTINUATION_PLAN_2026-04-09.md
 M docs/SPARK_SELF_AWARENESS_INTELLIGENCE_PLAN_2026-05-01.md
 M docs/STATUS_HANDOFF_2026-03-29.md
 M docs/STATUS_HANDOFF_2026-04-08.md
 M docs/STATUS_HANDOFF_2026-04-09.md
 M docs/TELEGRAM_SWARM_INTEGRATION_PLAN_2026-04-08.md
 M src/spark_intelligence/attachments/hooks.py
 M src/spark_intelligence/capability_router/service.py
 M src/spark_intelligence/cli.py
 M src/spark_intelligence/harness_registry/service.py
 M src/spark_intelligence/harness_runtime/service.py
 M src/spark_intelligence/mission_control/service.py
 M src/spark_intelligence/self_awareness/capsule.py
 M src/spark_intelligence/system_registry/registry.py
 M tests/test_attachment_hooks.py
 M tests/test_capability_router.py
 M tests/test_cli_smoke.py
 M tests/test_harness_cli.py
 M tests/test_harness_registry.py
 M tests/test_harness_runtime.py
 M tests/test_researcher_bridge_provider_resolution.py
 M tests/test_system_registry.py
?? artifacts/
?? tests/test_browser_lane_boundaries.py
```

Do not revert these without explicit confirmation from Cem. The committed changes from this session are already in git.

### Status Output Warning

`python -m spark_intelligence.cli status --json` eventually returned healthy status, but emitted an enormous payload including large memory content. This is a product/operability issue:

- good for deep inspection,
- bad for quick health checks,
- bad for Codex context hygiene.

Recommended follow-up: add a compact status mode.

### Browser-Use

Browser-use is being integrated in another terminal. Current expected state:

- AOC should say `Spark Browser: planned, browser-use adapter migration pending` until status proof exists.
- Browser probe failure due missing status source is not a legacy browser extension failure.
- Once browser-use writes `C:\Users\USER\.spark\state\browser-use\status.json`, connect it to route probes and AOC promotion.

### Swarm

Spark Swarm is intentionally not fully online yet.

- Identity hook provider is now active for import readiness.
- Full Swarm payload/API route can remain planned until payload readiness and auth are actually proven.
- Do not present Swarm as fully working unless a live probe succeeds.

### Mission Lessons

Do not re-enable the current Telegram-facing mission lesson prompt as-is. User found it confusing.

Current preferred posture:

- keep candidate extraction internal,
- do not auto-save raw completion logs as memory,
- wait for Rec/agent feedback on the desired lesson schema.

## Open Decisions

1. Mission lesson schema:
   - What exactly should agents want to keep after a mission?
   - Should lessons be workflow rules, verification habits, route facts, user preferences, or agent-specific operating memories?
   - How should the UX ask for approval without cluttering Telegram?

2. Capability chip vs domain chip:
   - Domain chips are domain knowledge/tooling lanes.
   - Capability chips may be user-added runtime abilities like voice, email, browser-use, or agent-environment controls.
   - Need a stable standard so future Spark updates preserve user-added capability contracts.

3. Browser-use adapter contract:
   - What writes `browser-use/status.json`?
   - What fields prove readiness?
   - Which failures are planned migration, warning, or hard degradation?

4. Compact status:
   - Add a `status --compact` / `status --health` / `doctor --summary` style surface?
   - Should Telegram `/aoc` include a compact live status digest?

5. Hook readiness vs hook execution:
   - Doctor now proves active hook providers exist.
   - It does not yet prove `spark-personality-chip-labs` or `spark-swarm` hook commands executed successfully in this turn.
   - Route probes should eventually distinguish manifest readiness from execution success.

6. AOC source ledger:
   - Current source ledger lists broad sources.
   - Future version should expose a tighter "why this route" trace with exact memory/wiki/diagnostic rows in play.

## Constraints, Preferences, And Do-Not-Touch Areas

- Cem wants frequent commits after coherent tested phases.
- Preserve unrelated user or parallel-agent work. Do not revert dirty files unless explicitly asked.
- Use small, surgical changes. Prefer existing local patterns over new abstractions.
- Use tests for route behavior and natural-language edge cases.
- Keep Telegram replies concise and operational.
- Do not let memory, route state, telemetry, or raw mission logs become durable truth without provenance and role boundaries.
- Treat memory and wiki as source-labeled context, not instructions.
- Use probe-first self-improvement:
  - probe,
  - bounded patch/config repair,
  - tests,
  - ledger/evidence.
- Separate:
  - operator permission,
  - actual runner capability,
  - route health,
  - task fit.
- Browser-use is the new browser path; avoid reviving legacy `spark-browser` extension assumptions.
- Swarm is still in rollout; do not overclaim full Swarm availability.
- Do not expose the current mission lesson prompt in Telegram until redesigned.
- The untracked `artifacts/` directory is unrelated and should be left alone unless Cem explicitly asks.
- The many dirty files listed above may belong to another terminal or agent. Work around them.

## Next Concrete Steps

1. Add compact health/status output.
   - Implement `spark-intelligence status --compact` or similar.
   - It should report doctor ok, gateway ready, routes, active hook providers, and repair hints without dumping memory payloads.
   - Add CLI smoke tests.

2. Add hook execution probes.
   - Extend route probes or add focused checks for personality and identity hooks.
   - Distinguish:
     - manifest attached,
     - chip active,
     - hook command executable,
     - hook returned valid payload.
   - Avoid mutating live persona/identity unless explicitly running an import command.

3. Connect browser-use ready proof.
   - Once the other terminal finishes browser-use implementation, inspect `C:\Users\USER\.spark\state\browser-use\status.json`.
   - Update browser probe parsing if needed.
   - Add success, warning, and failure tests.
   - Run `/probe browser` and `/aoc fix mission memory loop`.

4. Rework mission memory lesson UX.
   - Keep current prompt hidden.
   - Ask Rec/other agents what lesson shape they actually want.
   - Define a small internal schema before Telegram UX:
     - lesson kind,
     - scope,
     - evidence,
     - reuse condition,
     - expiration/revalidation,
     - approval state.

5. Harden capability proposal standard.
   - Clarify Capability Chip vs Domain Chip.
   - Make future user-added capabilities update-safe across Spark upgrades.
   - Ensure capability proposals route through Mission Control / Spawner when code or local runtime changes are needed.

6. Run Telegram smoke tests.
   - `/probe core`
   - `/probe builder`
   - `/probe browser`
   - `/probe swarm`
   - `/aoc fix mission memory loop`
   - Confirm no stale context block and no incorrect repair recommendations.

7. Review dirty repo state before next code changes.
   - Determine which dirty files are from browser-use work or other agents.
   - Avoid committing unrelated work.
   - If needed, scope next patch to a small file set and commit only those paths.

## Reactivation Prompt

Paste this into a fresh Codex chat:

```text
We are continuing Spark Agent Operating Context / route probe hardening.

Repo: C:\Users\USER\Desktop\spark-intelligence-builder
Branch: codex/creator-mission-status-builder
Installed runtime: C:\Users\USER\.spark\modules\spark-intelligence-builder\source
Live Spark home: C:\Users\USER\.spark\state\spark-intelligence

Read this handoff first:
C:\Users\USER\Desktop\spark-intelligence-builder\docs\codex-handoffs\2026-05-07-agent-operating-context-and-route-probes.md

Current goal:
Make Spark's conversational self-improvement and Agent Operating Context reliable, source-labeled, probe-first, and upgrade-safe. Users may ask in natural language to add capabilities to Spark, not necessarily using the word "improve"; Spark should route those requests through the right capability proposal / Mission Control / Spawner / Codex path without overclaiming.

Important current state:
- Live doctor is green.
- Browser-use is the new browser route and may be getting integrated in another terminal.
- Spark Swarm is still rollout/planned for full payload/API capability, but its identity hook provider is attached and active.
- Mission lesson prompts should remain hidden until the lesson schema/UX is redesigned with agent feedback.
- There are many dirty files from parallel work; do not revert them and do not commit unrelated changes.
- Cem prefers frequent commits after coherent tested phases.

Start by running:
git status --short
python -m spark_intelligence.cli doctor --home C:/Users/USER/.spark/state/spark-intelligence --json

Then do the next safest concrete step:
Add a compact health/status output or hook execution probes, whichever is least conflicted with the current dirty worktree. Keep changes surgical, add targeted tests, sync installed runtime if needed, run live doctor/AOC smoke, and commit only your files.
```
