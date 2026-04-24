# Spark Telegram Bot + Intelligence Builder - Operator Reference

How to run every command the Spark system exposes today, across Telegram, the
Python CLI, and the spawner-ui REST surface. Covers the wiring shipped
through the 2026-04-23 session that added `/chip create`, `/loop`, and
`/schedule` to the Telegram bot.

---

## 1. Boot the stack

Three services must be up for the full feature set.

```bash
# One command, from any cwd
/c/Users/USER/Desktop/spark-intelligence-builder/scripts/boot-spark.sh

# Check what's up
scripts/boot-spark.sh --status

# Clean teardown
scripts/kill-spark.sh
```

Ports the script manages:

| Port | Service | Notes |
| --- | --- | --- |
| 4174 | spawner-ui | SvelteKit dev server, mission API |
| 8788 | mission relay | Inside telegram-bot; listens for spawner events |
| 8907 | telegram webhook | Optional; polling mode doesn't use it |

If the bot refuses to start:

1. `taskkill /IM node.exe /F` or PowerShell `Stop-Process` the orphan.
2. Remove stale lock: `rm spark-telegram-bot/.spark-telegram-owner-lock-*.json`
3. Clear DB row:
   ```bash
   python -c "import sqlite3; c=sqlite3.connect('spark-telegram-bot/.spark-gateway-state.db'); c.execute(\"DELETE FROM gateway_state WHERE state_key LIKE '%owner-lock%'\"); c.commit()"
   ```
4. Re-run the boot script.

Launch config boundaries that matter:

- `spark-telegram-bot` owns the Telegram bot token and polling mode.
- `spawner-ui` owns mission execution config and receives only the relay URL plus `TELEGRAM_RELAY_SECRET`; it should not receive the Telegram bot token.
- Builder owns runtime/provider config and should read provider secrets from the supported local secret/config layer, not from public docs or command arguments.

---

## 2. Telegram bot - slash commands

Bot handle: `@SparkAGI_bot`. Commands marked `admin-only` require the user
id allowlist in the bot env. All return structured text replies.

### 2.1 System

| Command | What it does |
| --- | --- |
| `/start` | Welcome message, admin check |
| `/status` | Bot connectivity status (Mind/Spark/Ollama) |
| `/myid` | Echo your Telegram user id |

### 2.2 Memory (plain text also works via "remember this:" short-circuit)

| Command | Example | What it does |
| --- | --- | --- |
| `/remember <fact>` | `/remember I prefer 3-bullet summaries` | Store instruction |
| `/recall [topic]` | `/recall preferences` | Retrieve stored instructions |
| `/forget <needle>` | `/forget bullet summaries` | Archive matching instruction |
| `/about` | `/about` | Show what the bot remembers about you |

### 2.3 Missions - one-shot multi-LLM runs

| Command | Example | What it does |
| --- | --- | --- |
| `/run <goal>` | `/run draft 3 tweet hooks for Seedify Phoenix Raise` | POST `/api/spark/run`; dispatches to all configured providers in parallel |
| `/board` | `/board` | GET `/api/mission-control/board`; shows running/paused/completed |
| `/mission status \| pause \| resume \| kill <id>` | `/mission status spark-1776939248147` | POST `/api/mission-control/command` |

### 2.4 Chip creation (from this session, admin-only)

```
/chip create <natural language brief>
```

Example:

```
/chip create a chip for supply-chain-risk that evaluates supplier
financial health, geopolitical exposure, single-source dependencies,
and logistics resilience
```

Pipeline: bot shells to `python -m spark_intelligence.cli chips create
--prompt "..."`. LLM parses brief, `chip_labs.chip_factory.scaffold_chip`
generates the chip directory, manifest is patched with router fields,
chip is pinned + snapshot refreshed. Reply includes `chip_key`, path,
and `router_invokable` flag.

### 2.5 Autoloops - recursive self-improving loops (admin-only)

```
/loop <chip_key> [rounds]
```

Example:

```
/loop startup-yc 3
/loop domain-chip-brand-sentiment-tracking 2
```

Each round: chip's `suggest` hook returns candidates, then `evaluate`
runs on each. Loop persists state at
`~/.spark-intelligence/loops/<chip>.status.json`. Bot acks immediately
and posts the summary asynchronously (bypasses Telegraf's 90s handler
timeout).

### 2.6 Scheduler - cron-style recurring triggers (admin-only)

```
/schedule "<cron>" mission <goal>
/schedule "<cron>" loop <chipKey> [rounds]
/schedules
/schedules delete <id>
```

Examples:

```
/schedule "0 9 * * *" loop startup-yc 3
/schedule "*/30 * * * *" mission check for new seedify news
```

Stored at `spawner-ui/.spawner/schedules.json`. Worker ticks every 30s
and fires any schedule whose `nextFireAt` has passed. Each fire DMs
the chatId that created the schedule with
`[sched <id>] <action> ok/fail: <summary>`.

### 2.7 Plain-text chat (no slash)

Goes through `spark-intelligence-builder/gateway ask-telegram` -> the
`researcher_bridge`. The bridge:

1. Detects instruction intent (`remember this:`, `from now on:`, etc.)
   and short-circuits to `user_instructions` (no chip routing).
2. Runs chip router (`select_chips_for_message`).
3. If the chip is unavailable (e.g. browser stale), falls back to the
   LLM with Z.AI's `web_search` tool for web queries.
4. Escalation to Spark Swarm is best-effort: a timeout won't kill the
   turn, it degrades to local researcher.

---

## 3. Direct CLI - `spark-intelligence`

Run from inside `spark-intelligence-builder`. Pass `--home
.tmp-home-live-telegram-real` to use the live production config.

### 3.1 Chips

```bash
# Inspect routing for a test message
python -m spark_intelligence.cli chips why "research seedify news" \
  --home .tmp-home-live-telegram-real --json

# Create a chip from a natural-language brief
python -m spark_intelligence.cli chips create \
  --home .tmp-home-live-telegram-real \
  --prompt "a chip for competitive-intel tracking 5 rivals across pricing and launches" \
  --output-dir "<user-home>/Desktop" \
  --json
```

### 3.2 Attachments - chip registry

```bash
python -m spark_intelligence.cli attachments list --home .tmp-home-live-telegram-real
python -m spark_intelligence.cli attachments snapshot --home .tmp-home-live-telegram-real --json
python -m spark_intelligence.cli attachments pin-chip <chip_key> --home .tmp-home-live-telegram-real
python -m spark_intelligence.cli attachments unpin-chip <chip_key> --home .tmp-home-live-telegram-real
python -m spark_intelligence.cli attachments activate-chip <chip_key> --home .tmp-home-live-telegram-real
python -m spark_intelligence.cli attachments deactivate-chip <chip_key> --home .tmp-home-live-telegram-real
python -m spark_intelligence.cli attachments run-hook <chip_key> <hook_name> --home .tmp-home-live-telegram-real
```

### 3.3 Autoloops

```bash
python -m spark_intelligence.cli loops run \
  --home .tmp-home-live-telegram-real \
  --chip startup-yc \
  --rounds 3 \
  --suggest-limit 3 \
  --json
```

Per-round status lands in `~/.spark-intelligence/loops/<chip>.status.json`.

### 3.4 Gateway - simulate a Telegram turn in-process

```bash
python -m spark_intelligence.cli gateway ask-telegram \
  "what can you do?" \
  --user-id 8319079055 \
  --json
```

Useful for testing chat routing without sending a real Telegram message.

### 3.5 Observability

```bash
python -m spark_intelligence.cli doctor
python -m spark_intelligence.cli auth status --home .tmp-home-live-telegram-real
python -m spark_intelligence.cli memory status --home .tmp-home-live-telegram-real
python -m spark_intelligence.cli gateway traces --home .tmp-home-live-telegram-real
```

---

## 4. Direct REST - `spawner-ui` on :4174

All responses JSON unless noted.

### 4.1 Providers

```bash
curl http://127.0.0.1:4174/api/providers
```

### 4.2 Missions

```bash
# Create
curl -X POST http://127.0.0.1:4174/api/spark/run \
  -H "Content-Type: application/json" \
  -d '{"goal":"summarize top gamefi launchpads","chatId":"smoke","userId":"smoke","requestId":"smoke-1","projectPath":"<user-home>/Desktop"}'

# Status
curl -X POST http://127.0.0.1:4174/api/mission-control/command \
  -H "Content-Type: application/json" \
  -d '{"action":"status","missionId":"spark-1776...","source":"cli"}'

# Kanban board
curl http://127.0.0.1:4174/api/mission-control/board
```

### 4.3 Schedules

```bash
# List
curl http://127.0.0.1:4174/api/scheduled

# Create (mission)
curl -X POST http://127.0.0.1:4174/api/scheduled \
  -H "Content-Type: application/json" \
  -d '{"cron":"0 9 * * *","action":"mission","payload":{"goal":"daily seedify news scan"},"chatId":"8319079055"}'

# Create (loop)
curl -X POST http://127.0.0.1:4174/api/scheduled \
  -H "Content-Type: application/json" \
  -d '{"cron":"0 */6 * * *","action":"loop","payload":{"chipKey":"startup-yc","rounds":2},"chatId":"8319079055"}'

# Delete
curl -X DELETE "http://127.0.0.1:4174/api/scheduled?id=sched-abc123"
```

---

## 5. Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| Bot replies "Something went wrong" instantly | Telegraf 90s handler timeout on a long command | The detach pattern is shipped for `/loop`; if it reappears, detach the handler body with `void (async () => { ... })()` and post results via `ctx.telegram.sendMessage` |
| Bot fails to start, "Gateway ownership already held" | Stale lock file or orphan process | See boot-stack steps 1-3 above |
| Bot fails to start, `EADDRINUSE :8788` | Previous bot process didn't release the relay port | `powershell Get-NetTCPConnection -LocalPort 8788 -State Listen \| % { Stop-Process -Id $_.OwningProcess -Force }` |
| Scheduled fires but no Telegram message | Mission relay config is missing or rejected | Confirm the generated relay URL and `TELEGRAM_RELAY_SECRET` are present in the Spawner runtime config, then restart Spawner fully |
| `/chip create` result says `router_invokable: false` | `attachment_mode` not `active`/`pinned` OR no `task_topics`/`task_keywords` in manifest | Inspect with `attachments snapshot --json`; re-pin with `attachments pin-chip <key>` |
| `/loop` says "Supported hooks: none" | `commands` in spark-chip.json is in string form or missing | Manifest commands must be an array per hook (e.g. `["python","-m","<module>.cli","evaluate"]`); the chip_create pipeline normalizes this automatically |
| `/loop` returns candidates=0 | Chip's `suggest` hook returns empty on cold history (not a bug, chip-specific) | Either (a) seed history via chip-specific bootstrap, or (b) pick a chip with non-empty cold-start behavior |
| `/loop` on a scaffolded chip fails with `ImportError: attempted relative import beyond top-level package` | Scaffolder emits `from ..lab_hooks import`; needs absolute import | The chip_create pipeline patches this automatically; for older chips, run the patch manually via `python -c "from spark_intelligence.chip_create.pipeline import _patch_generated_cli; _patch_generated_cli(Path('<chip_dir>'), Path('<workspace>/spark-domain-chip-labs'))"` |
| "Researcher is unavailable" on swarm-escalation messages | Spark Swarm API unreachable (no URL set) | Already graceful-degrades to local; if you want real swarm, configure `SPARK_SWARM_API_URL` through the supported local config layer and start the swarm service |

---

## 6. What's NOT yet wired

- **Kanban Scheduled tab UI** - backend is live, UI is still a "Coming in next pass" placeholder in `spawner-ui/src/routes/kanban/+page.svelte`. Frontend work needed to list + create + delete schedules visually.
- **Scaffolder source fix** - `chip_labs.chip_factory.scaffold_chip` emits command strings and broken relative imports. The `/chip create` pipeline post-patches both. Upstream scaffolder should be fixed so other callers don't need to patch.
- **Suggest-hook cold-start seeding** - several chips return 0 candidates when `history: []`. Need per-chip seed prompt or generic priming strategy.
- **Spark Researcher `autoloop` CLI integration** - the committed `spark-researcher autoloop` is broken against its own chips due to schema drift. Our `loops run` bypasses it; pick this up when the researcher package catches up.

---

## 7. Live verification - Phase checklist

Every phase was green-signaled by a real Telegram round-trip. To re-verify
after an upgrade:

1. `/start` - bot responds.
2. `/run hello` - mission spawns, `/board` shows it.
3. `/chip create a chip for <anything>` - chip appears at Desktop and is
   router-invokable (confirmed in `attachments snapshot --json`).
4. `/loop startup-yc 1` - returns "Rounds 1/1" with non-zero candidates and
   a real metric (e.g. `best_metric=0.773`).
5. `/schedule "*/1 * * * *" loop startup-yc 1` - wait 2 minutes, see
   `[sched sched-xxxx] loop ok` arrive on its own. `/schedules delete` to clean up.
6. Plain text: `remember this: I prefer short replies` - returns the
   user-instruction-shortcircuit ack, not a web search.

If any of these regress, the phase commits in `task-telegram-agent.md`
bisect the likely culprit.
