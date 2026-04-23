# Telegram-Native Recursive Agent

Track: feature work toward making the Telegram bot the full control plane for recursive sessions, chip creation, and autoloops. Items move **Next** -> **In Progress** -> **Green-lit** only after live Telegram testing.

Companion to `task.md` (memory methodology plan). Different scope - do not merge.

Started: 2026-04-23

---

## Phase 0 - Boot ritual + tracking  [GREEN 2026-04-23]

- [x] `scripts/boot-spark.sh` - detect port conflicts, launch spawner-ui (:4174), telegram-bot (polling). Health-gated.
- [x] `scripts/kill-spark.sh` - clean teardown
- [x] `task-telegram-agent.md` committed (0f516b5)
- [x] Green signal: spawner-ui REST verified, mission ran end-to-end Z.AI+MiniMax in <25s, Telegram round-trip confirmed (msg 680 sent, user reply received on bot side)
- KNOWN GAP: Spark Builder gateway not in boot script yet - will add before Phase 1 text tests

## Phase 1 - Urgent defects  [GREEN 2026-04-23]

- [x] P9 fix: short-circuit in `adapters/telegram/runtime.py` - detect_instruction_intent runs BEFORE bridge; matched messages skip chip routing, produce instruction ack directly
- [x] P7 fix: wrap `evaluate_swarm_escalation` in `capability_router/service.py` try/except - any failure (timeout, URLError, None api_url) degrades to `swarm_decision(mode="unavailable", escalate=False)` instead of killing the turn
- [x] Green signal: Live Telegram tests pass. P9 routes to `user_instruction_shortcircuit` with ack. P7 routes to `provider_fallback_chat+manual_recommended` without `bridge_error`.

## Phase 2 - /chip create from Telegram  [GREEN 2026-04-23]

- [x] Builder CLI: `spark-intelligence chips create --prompt ...` (commit 19008ec)
- [x] Brief parser: LLM (Z.AI GLM 5.1) turns prompt -> strict JSON brief with router fields
- [x] Scaffolder delegation: chip_labs.chip_factory.scaffold_chip
- [x] Manifest patched with chip_name + task_topics + task_keywords + combine_with
- [x] add_attachment_root -> snapshot -> pin_chip -> snapshot -> router_invokable verified
- [x] spark-telegram-bot /chip create handler (commit 4030ee2) shells out to builder CLI
- [x] Green signal: live Telegram `/chip create a chip for brand-sentiment-tracking ...` produced `domain-chip-brand-sentiment-tracking` at Desktop, router_invokable=yes, in ~45s
- DEFERRED: spawner-ui REST endpoint (bot shells Python directly; simpler and works)
- DEFERRED: H70-C+ validator integration (chip contract is different from skills contract; not blocking)
- DEFERRED: mission-relay progress events for chip-creation (single-shot is fine for now)

## Phase 3A - Autoloops (recursive self-improving loops)  [GREEN 2026-04-23]

Plumbing: Telegram /loop -> builder CLI -> run_chip_hook (suggest then
evaluate per round) -> status JSON -> Telegram reply.

- [x] spark-researcher `autoloop` CLI investigated - broken against its
      own committed chips (schema drift); pivoted to lightweight
      in-builder runner.
- [x] Builder loops/ module with run_chip_autoloop()
- [x] `spark-intelligence loops run --chip <key> --rounds N`
- [x] spark-telegram-bot `/loop <chip_key> [rounds]` command
- [x] Green signal: live Telegram `/loop startup-yc 2` returned "Rounds
      2/2" with per-round summary; status file written.
- FOLLOW-UP: scaffolded chips (from /chip create) have a lab_hooks
  relative-import bug; fix so new chips can loop immediately.
- FOLLOW-UP: seed history / priming for suggest hooks that return 0
  candidates on cold state.

## Phase 3B - Scheduler (cron-style recurring triggers)

Distinct from autoloops. Cron-driven recurring missions or autoloops.

- [ ] spawner-ui: persistent schedule store in `.spawner/schedules.json`
- [ ] spawner-ui: `POST /api/scheduled/{create,list,delete}` endpoints
- [ ] spawner-ui: Kanban Scheduled tab - replace "Coming in next pass" placeholder
- [ ] Scheduler worker: wakes on cron match, calls `/api/spark/run` OR spawns autoloop
- [ ] spark-telegram-bot: `/schedule "<cron>" <action>` and `/schedules list|delete <id>`
- [ ] Green signal: Telegram `/schedule "*/2 * * * *" echo hello` fires twice, reports both in chat

## Phase 4 - Stretch (only if time remains)

- [ ] chips can declare `recursion_contract`, missions reference chip via `chip_ref`
- [ ] Telegram `/loop <goal> --iterations N` sugar on top of `/run`
- [ ] Plain-text mission-intent auto-route (builder suggests `/run` when user describes a recursive task conversationally)

---

## Test discipline (every green signal)

1. One live Telegram send/receive test via `sendMessage` + bot reply
2. One direct REST probe of the underlying API
3. A committed change with descriptive message
