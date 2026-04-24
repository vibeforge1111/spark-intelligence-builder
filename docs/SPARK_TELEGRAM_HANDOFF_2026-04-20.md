# Spark Intelligence Builder — Telegram Handoff (2026-04-20)

End-of-day snapshot so tomorrow resumes cleanly. Terse on purpose.

---

## What shipped today

Three commits, all on `main`, all pushed to GitHub.

| Commit | Scope |
|---|---|
| `50b8918` | Chip routing (task_topics/keywords/combine_with), persistent user instructions, score-truthful Telegram delivery, chunked replies, percent normalizer |
| `88e8e13` (domain-chip-xcontent) | Router metadata declared, `analysis` field emits percentages |
| `d10a863` (domain-chip-startup-yc) | Router metadata declared, `combine_with` wired |
| `cdbad11` | Bot drafts module — long replies saved as `D-xxxx`, iteration intent binds to source draft, in-place replace on re-iteration |

Repos affected:
- https://github.com/vibeforge1111/spark-intelligence-builder
- https://github.com/vibeforge1111/domain-chip-xcontent
- https://github.com/vibeforge1111/domain-chip-startup-yc

---

## System state at end of day

**Gateway**
- Home: `<workspace>\\spark-intelligence-builder\.tmp-home-live-telegram-real`
- Autostart shim: `%AppData%\...\Startup\Spark Intelligence Gateway __tmp-home-live-telegram-real_.cmd` (calls Telegram `close` API before launch to pre-empt poll races)
- Bot: `@SparkAGI_bot` (id `8667732512`), allowlist mode, 1 allowed user (`8319079055`)
- Doctor: `ok` (cleared this session by running `jobs tick` — background-freshness watchtower needs a recent `jobs_tick` row to stay green)
- Provider: MiniMax `MiniMax-M2.7` via `https://api.minimax.io/v1`
- 409 conflicts: **none since 16:00 UTC today**. Root cause earlier in the session was accidental duplicate gateway processes, not an external consumer. No token rotation needed. `Spark-Comeback/scripts/notify.ps1` shares the token but is send-only (doesn't poll), so no conflict risk.

**Chip state**
- Active: `spark-browser`, `spark-personality-chip-labs`, `spark-swarm`, `domain-chip-voice-comms`, `domain-chip-xcontent`, `startup-yc`
- Pinned: `domain-chip-xcontent`
- xcontent is primary content chip, startup-yc is primary doctrine chip, both have combine_with to each other

**DB tables used by today's features**
- `user_instructions` — persisted preferences per `(external_user_id, channel_kind)`
- `bot_drafts` — stored long bot replies with `D-xxxx` handles

---

## Architecture recap — what happens on a Telegram message

1. `@SparkAGI_bot` receives update via long-poll
2. Normalize → allowlist check → intent detection (voice transcribe, instruction capture, forget, iteration)
3. `_run_active_chip_evaluate` → `select_chips_for_message` (relevance-scored over active chips)
4. Selected chips' `evaluate` hooks run in parallel; `analysis` fields collected
5. `_build_contextual_task` assembles prompt:
   - spark self-knowledge + system registry
   - mission-control / capability-router / harness contexts
   - personality + telegram persona contract
   - recent_conversation_context
   - **GROUND TRUTH** attachment inventory (overrides conversation history)
   - persistent user instructions
   - iteration draft (if iteration intent matched)
   - chip guidance (verdict/score quoted verbatim — see `advisory.py:2328`)
   - user message
6. MiniMax call → response
7. Outbound guardrails (`gateway/guardrails.py`):
   - secret-like redaction
   - score decimals → percentages
   - paragraph-aware chunking into up to 5 parts with `(1/N)` prefix
8. Telegram delivery (`adapters/telegram/runtime.py`):
   - shape bridge reply
   - append verbatim chip metrics block if score intent
   - capture "remember this" / "forget" instructions
   - save long reply as draft (or replace source draft)
   - send chunks as separate messages

---

## Test rig for tomorrow

**Fast router test (no MiniMax call):**
```
python -m spark_intelligence.cli chips why "<message>" [--history "..."] [--recent-chip xcontent]
```

**End-to-end Telegram path (hits MiniMax):**
```
python -m spark_intelligence.cli gateway ask-telegram "<message>" --user-id 8319079055 --json
```

**Inspect state:**
```
python -m spark_intelligence.cli drafts list --user-id 8319079055 --channel telegram
python -m spark_intelligence.cli instructions list --user-id 8319079055 --channel telegram
python -m spark_intelligence.cli attachments status
python -m spark_intelligence.cli doctor
```

**Gateway control:**
```
# start (via shim)
& "$env:AppData\Microsoft\Windows\Start Menu\Programs\Startup\Spark Intelligence Gateway __tmp-home-live-telegram-real_.cmd"

# kill all gateway python processes
Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -match 'spark_intelligence' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```

---

## Top items for tomorrow (priority order)

From the "what else to improve for Telegram" discussion:

1. **Multi-user allowlist + per-user instruction isolation audit**
   Operator CLI: `spark-intelligence operator allowlist add <telegram-user-id> <role>`. Roles: `owner | trusted | readonly`. Confirm instruction scoping still holds when 2+ users active.

2. **Voice round-trip quality**
   Show transcript in reply: `"I heard: <transcript>. Here's my take: ..."`. Flag low-confidence transcription explicitly. Currently voice messages come through as opaque `🎤 Voice Message`.

3. **Operator observability surface**
   `spark-intelligence ops watch` — stream last N turns with `user → chip → verdict → reply_chunks`. Or a 1-file Streamlit at `http://localhost:8090`. Right now debugging means `tail -f gateway-trace.jsonl`.

4. **Long-reply partial delivery signals**
   Telegram `sendChatAction("typing")` every 8s while bridge runs. If >90s, send `"still working — X chips running, Y seconds so far"`. Prevents users from thinking the bot died when MiniMax is slow.

5. **Inline button quick-actions**
   When chip returns `recommended_next_step`, render as tap button (`[Run format probe]`). Native Telegram feature, turns one-shot replies into a flow.

6. **Attachment delivery for big outputs**
   When chunking would produce >3 parts, offer full article as `.md` via `sendDocument` with a 1-chunk inline summary.

7. **Per-user chip receipt log**
   Store last N `(user_id, chip_key, raw_metrics)` tuples so user can say "re-score my last tweet draft with xcontent" and the bot has actual data.

---

## Open questions and known gaps

- **Railway swarm endpoint** (`https://api-production-6ea6.up.railway.app`) is configured as swarm bridge. Untested whether it's actually reachable / needed for daily ops. Worth validating.
- **xcontent's connectors (Dub, PostHog, Airtable)** are referenced in chip docs but not wired. xcontent evaluator works fine without them — but if we want live engagement metrics rather than deterministic scoring, those need provisioning.
- **Fix 3 suppress (not shipped)**: LLM still sometimes says "I've changed my behavior" between turns — that's always a hallucination. The user has an instruction persisted saying "never tell me you've changed your behavior between turns — that's a hallucination". If we see it drift, we could add a regex suppressor in the outbound layer.

---

## Recent decisions worth remembering

- Chip router is **deterministic keyword/topic scoring**, not LLM classification. Chosen for debuggability and zero extra spend per message. `chips why` is the inspector. Deliberate tradeoff — if misrouting becomes common we'd consider Phase 2 embedding-based scoring.
- First-match selector was replaced with relevance scoring. Pinning is still supported but no longer dominates — relevance wins.
- Chip output is **verbatim-quoted** for verdict/confidence/scores (`advisory.py` directive). Prose is paraphrasable. The gateway also appends an unspoofable verbatim block when user asks for a score.
- Percent normalization runs on **every** outbound message for **every** user/channel, not just xcontent. New chips with `<label>: 0.XX` patterns get auto-converted.
- User instructions are durable. Detector is explicit ("remember this", "from now on", "always X"). Forget is also explicit ("forget X", "/forget").
- Draft store is in-place replacement on iteration. Prevents drift accumulation across multi-turn iterations.

---

## Start-of-day checklist

1. Confirm gateway is up: `Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -match 'spark_intelligence' } | Select-Object ProcessId, CreationDate`
2. If no gateway: launch shim once
3. `python -m spark_intelligence.cli doctor` — should be `ok`. If degraded on `watchtower-freshness`, run `python -m spark_intelligence.cli jobs tick`
4. Check message round-trip: `python -m spark_intelligence.cli gateway ask-telegram "healthcheck" --user-id 8319079055 --json`
5. Pick next item from priority list above

EOF
