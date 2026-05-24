# Spark Intelligence Browser Integration Handoff 2026-04-08

## 1. What This Checkpoint Represents

This note is historical. The legacy browser extension path described below has since been disabled; use the guarded Spark CLI browser-use MCP lane instead.

The important shift is that Builder is no longer only structurally compatible with the browser-extension repo.

The old checkpoint completed a governed browser-extension status/snapshot flow. That flow is no longer an active runtime path.

## 2. What Changed In Builder

Builder shipped the missing live browser snapshot fallback in commit:

- `0e7d433` `Fix Builder live browser snapshot fallback`

What changed:

- Historical: the old extension snapshot command no longer stopped at a direct `browser.page.snapshot` attempt when the extension lacked live page context.
- Builder now falls back to:
  - `browser.navigate`
  - `browser.tab.wait`
  - `browser.page.snapshot` with the returned `tab_id`
- the browser payload builder now supports explicit `tab_id` targeting for page snapshots
- regression coverage now includes the missing live-context recovery path

Files touched by that fix:

- `src/spark_intelligence/browser/service.py`
- `src/spark_intelligence/browser/__init__.py`
- `src/spark_intelligence/cli.py`
- `tests/test_cli_smoke.py`

## 3. What Was Verified

Builder verification completed:

- `python -m pytest tests/test_cli_smoke.py tests/test_attachment_hooks.py -k browser -q`
- `python -m pytest tests/test_cli_smoke.py tests/test_attachment_hooks.py -q`
- `spark browser-use status --json`
- `spark browser-use mcp-config --client codex`

Results:

- browser-adjacent tests pass in Builder
- the guarded browser-use status/config commands are the supported path now
- the legacy extension attachment path is disabled

## 4. Honest Readiness Status

For the current Builder-owned browser CLI surface, this historical note is superseded.

That means:

- use `spark browser-use status --json` for browser-use readiness
- use `spark browser-use mcp-config --client codex` for Codex MCP wiring
- Builder should not call the legacy extension adapter

This does not mean Builder owns the full browser product.

What still remains outside this specific Builder checkpoint:

- richer Builder-side approval authority and persistence
- broader browser-specific operator surfaces in Builder
- any future Builder browser commands beyond the current `status` and `page-snapshot` slice

## 5. Current Risks And Caveats

- the legacy extension live path is disabled
- browser automation should go through the guarded browser-use MCP lane
- this repo still has unrelated local untracked files not touched by this checkpoint

Those unrelated local files should not be confused with the shipped browser integration state.

## 6. Telegram Ownership And System Shape

Later on 2026-04-08, the production-shape runtime was clarified:

- only one runtime may own one Telegram bot token through long polling at a time
- Builder should not run a second long-poll gateway against a bot token already owned elsewhere
- `spark-swarm` was inspected directly and does not currently implement Telegram bot ingress in this repo

An attempted handoff of production Telegram ownership away from Builder was rolled back the same day because it removed the only implemented live responder path.

Current live truth:

- `.tmp-home-live-telegram-real` now keeps `channels.records.telegram.status: enabled`
- the live Builder gateway was restarted and verified on the Telegram socket
- real Telegram traffic processed again after the rollback
- `spark-swarm` remains a downstream or adjacent system here, not the active Telegram ingress owner for this bot
- browser automation now belongs to the guarded Spark CLI browser-use MCP lane, not `spark-browser-extension`

The current production-shaped runtime is:

1. Telegram ingress is owned by Builder.
2. Builder enforces pairing, operator policy, and delivery.
3. Builder handles runtime commands such as `/swarm status`, `/swarm evaluate <task>`, and `/swarm sync` locally.
4. Builder uses Spark Researcher and the configured provider path for normal conversational reasoning.
5. Builder should not use the `spark-browser-extension` attachment for governed browser evidence.
6. Builder uses the Swarm bridge when the operator asks for evaluation or collective sync.

This keeps one clear chain of responsibility:

- Telegram channel ownership: Builder
- multi-agent escalation and collective sync: Swarm
- governed browser execution: guarded Spark CLI browser-use MCP lane

Live verification completed on the production-shaped Telegram home:

- `/swarm status` returned ready/auth/sync state
- `/swarm evaluate delegate this as parallel swarm work` returned `manual_recommended`
- `/swarm sync` returned `ok`, `uploaded`, and `accepted: yes`

Why this matters:

- it keeps the production bot on the only implemented responder path
- it avoids pretending `spark-swarm` owns a Telegram ingress path that is not actually implemented in this repo
- it preserves the governed browser integration while keeping the one-poller-per-bot-token rule explicit for future work

## 7. Release State

As of 2026-04-08:

- `spark-browser-extension` is pushed at `be01fc3`
- `spark-intelligence-builder` is pushed at `7796397`
- the real cross-repo governed browser path is working for the current Builder CLI surface
- the canonical current production Telegram owner remains Builder until a real `spark-swarm` Telegram ingress exists
- the live Telegram -> Builder -> Swarm command path is verified
