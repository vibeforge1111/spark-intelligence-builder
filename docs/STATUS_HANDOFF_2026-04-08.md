# Spark Intelligence Browser Integration Handoff 2026-04-08

## 1. What This Checkpoint Represents

This note captures the browser-specific Builder state after the governed browser V1 repo was finalized and the real cross-repo Builder path was re-tested.

The important shift is that Builder is no longer only structurally compatible with the browser-extension repo.

It now completes a real governed `browser status` and `browser page-snapshot` flow against the live `spark-browser-extension` runtime on this machine.

## 2. What Changed In Builder

Builder shipped the missing live browser snapshot fallback in commit:

- `0e7d433` `Fix Builder live browser snapshot fallback`

What changed:

- `spark-intelligence browser page-snapshot` no longer stops at a direct `browser.page.snapshot` attempt when the extension lacks live page context
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
- `spark-intelligence attachments add-root chips <workspace>\\spark-browser-extension --home <workspace>\\spark-intelligence-builder\.tmp-home-browser-extension`
- `spark-intelligence attachments activate-chip spark-browser --home <workspace>\\spark-intelligence-builder\.tmp-home-browser-extension`
- `spark-intelligence browser status --chip-key spark-browser --home <workspace>\\spark-intelligence-builder\.tmp-home-browser-extension --json`
- `spark-intelligence browser page-snapshot --origin https://example.com/ --chip-key spark-browser --home <workspace>\\spark-intelligence-builder\.tmp-home-browser-extension --json`

Results:

- browser-adjacent tests pass in Builder
- the real manifest-backed attachment path is active
- live `browser status` returns `completed`
- live `browser page-snapshot` now returns `completed`
- the returned snapshot includes real bounded page data plus a real `tab_id`

## 4. Honest Readiness Status

For the current Builder-owned browser CLI surface, this is ready.

That means:

- `browser status` works against the real extension runtime
- `browser page-snapshot` works against the real extension runtime
- Builder now behaves like a real downstream consumer of the governed browser adapter rather than only a fixture-mode contract checker

This does not mean Builder owns the full browser product.

What still remains outside this specific Builder checkpoint:

- richer Builder-side approval authority and persistence
- broader browser-specific operator surfaces in Builder
- any future Builder browser commands beyond the current `status` and `page-snapshot` slice

## 5. Current Risks And Caveats

- the live path still depends on the dedicated browser profile being launched and connected
- the extension remains `active_tab_only`, so Builder must keep using explicit page-context establishment instead of assuming ambient page access
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
- `spark-browser-extension` remains the downstream governed browser runtime for Brave execution

The current production-shaped runtime is:

1. Telegram ingress is owned by Builder.
2. Builder enforces pairing, operator policy, and delivery.
3. Builder handles runtime commands such as `/swarm status`, `/swarm evaluate <task>`, and `/swarm sync` locally.
4. Builder uses Spark Researcher and the configured provider path for normal conversational reasoning.
5. Builder uses the `spark-browser-extension` attachment when a Telegram request needs governed browser evidence.
6. Builder uses the Swarm bridge when the operator asks for evaluation or collective sync.

This keeps one clear chain of responsibility:

- Telegram channel ownership: Builder
- multi-agent escalation and collective sync: Swarm
- governed browser execution: `spark-browser-extension`

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
