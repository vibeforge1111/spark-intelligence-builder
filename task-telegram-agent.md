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

## Phase 2 - /chip create from Telegram

- [ ] spawner-ui: new `POST /api/chip/create` endpoint wrapping `chip_labs.chip_factory.scaffold_chip`
- [ ] Brief parser: LLM converts free-text prompt -> structured brief JSON
- [ ] Scaffolder enhancements:
  - [ ] auto-populate `task_topics` / `task_keywords` / `combine_with` from brief
  - [ ] call `vibeship-skills-lab/tools/validate-h70-cplus.js` after scaffold
  - [ ] run `attachments pin-chip` + `snapshot` so chip becomes router-invokable immediately
- [ ] spark-telegram-bot: `/chip create <prompt>` handler -> POSTs to spawner-ui
- [ ] Mission relay posts chip-creation progress into Telegram chat
- [ ] Green signal: Telegram `/chip create a chip for supply-chain-risk` produces a valid, router-invokable chip end-to-end

## Phase 3 - Autoloops (Scheduled tab)

- [ ] spawner-ui: cron backend. Persistent schedule store in `.spawner/schedules.json`
- [ ] spawner-ui: `POST /api/scheduled/{create,list,delete}` endpoints
- [ ] spawner-ui: kanban Scheduled tab UI - replace "Coming in next pass" placeholder with real list + create form
- [ ] Scheduler worker: wakes on cron match, POSTs `/api/spark/run`, relays mission outcome to telegram
- [ ] spark-telegram-bot: `/schedule "<cron>" <goal>` and `/schedules list|delete <id>` handlers
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
