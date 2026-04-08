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
- `spark-intelligence attachments add-root chips C:\Users\USER\Desktop\spark-browser-extension --home C:\Users\USER\Desktop\spark-intelligence-builder\.tmp-home-browser-extension`
- `spark-intelligence attachments activate-chip spark-browser --home C:\Users\USER\Desktop\spark-intelligence-builder\.tmp-home-browser-extension`
- `spark-intelligence browser status --chip-key spark-browser --home C:\Users\USER\Desktop\spark-intelligence-builder\.tmp-home-browser-extension --json`
- `spark-intelligence browser page-snapshot --origin https://example.com/ --chip-key spark-browser --home C:\Users\USER\Desktop\spark-intelligence-builder\.tmp-home-browser-extension --json`

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

## 6. Telegram Ingress Ownership Addendum

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

Why this matters:

- it keeps the production bot on the only implemented responder path
- it avoids pretending `spark-swarm` owns a Telegram ingress path that is not actually implemented in this repo
- it preserves the governed browser integration while keeping the one-poller-per-bot-token rule explicit for future work

## 7. Release State

As of 2026-04-08:

- `spark-browser-extension` is pushed at `be01fc3`
- `spark-intelligence-builder` is pushed at `d84b895`
- the real cross-repo governed browser path is working for the current Builder CLI surface
- the canonical current production Telegram owner remains Builder until a real `spark-swarm` Telegram ingress exists
