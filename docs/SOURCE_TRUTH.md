# Builder Source Truth

Last updated: 2026-06-08

This document is the operator rule for choosing Builder runtime truth during
Spark remediation work. It exists because stale Desktop and installed-looking
trees have repeatedly produced false audit conclusions.

## Current Classification

Canonical Builder runtime truth for the current Spark OS line is:

```text
C:\Users\USER\.spark\modules\spark-intelligence-builder\source
```

That tree is the live installed runtime inspected by `spark-cli`, Builder
doctor, the canonical `state.db`, and the 2026-06-08 deep-audit remediation
commits.

The Desktop checkout at:

```text
C:\Users\USER\Desktop\spark-intelligence-builder
```

is backlog/historical evidence until curated. On 2026-06-08 it was dirty, on a
gone branch, and at a different HEAD from the installed runtime. It must not be
used as proof of live runtime behavior unless the document explicitly says it is
auditing that stale tree.

The older Desktop `SOURCE_TRUTH.md` named a
`spark-intelligence-builder-release` path. That path is not the current live
runtime path for this remediation line.

## Ownership

Builder owns:

- runtime identity, sessions, pairings, and provider policy;
- memory, character, researcher, and swarm bridge contracts;
- canonical governed tool-call persistence in `state.db:tool_call_ledger`;
- supervised self-evolution evidence and change-manifest runner records.

Builder does not own:

- Telegram token ownership or live long-polling ingress;
- Spawner mission execution state or provider result bodies;
- Spark CLI installer, registry, provenance, or OS compiler implementation;
- private strategy repos or unpublished module internals.

## Backlog Policy

Dirty Builder worktrees are feature backlog, not release truth.

Allowed path:

```text
classify backlog slice
  -> name the exact behavior to port
  -> port minimum behavior onto the canonical installed line
  -> run focused Builder tests
  -> update docs and runtime evidence
  -> commit only the curated change
```

Forbidden path:

```text
dirty checkout
  -> wholesale merge or copy into installed runtime
  -> publish installer metadata
  -> claim live runtime behavior
```

## Verification

Before claiming a Builder source-truth decision, record:

```powershell
git -C C:\Users\USER\.spark\modules\spark-intelligence-builder\source status --short --branch
git -C C:\Users\USER\.spark\modules\spark-intelligence-builder\source rev-parse HEAD
git -C C:\Users\USER\Desktop\spark-intelligence-builder status --short --branch
git -C C:\Users\USER\Desktop\spark-intelligence-builder rev-parse HEAD
```

Current 2026-06-08 evidence, refreshed after the Builder harness-ledger
remediation commits. The code-bearing remediation line is:

- installed Builder code-bearing HEAD: `69276c51e7e657095631a299ff97589dae1f50f2`
- later docs-only commits may sit on top of this without changing runtime
  behavior
- Desktop Builder HEAD: `bc8dfd3e39df6f74a2829e00bef8726b3aa7ed7a`
- installed Builder status: clean detached checkout
- Desktop Builder status: dirty `codex/browser-use-receipts` branch whose
  remote ref is gone
- installed Builder manifest: AGPL-3.0-only and `needs.modules =
  ["spark-harness-core"]`
- Desktop Builder manifest: AGPL metadata is visible only in the dirty working
  tree, `LICENSE` is untracked, and `spark-harness-core` is still not declared
  in `needs.modules`

## Current Next Safe Action

Keep the installed Builder source canonical. Re-derive selected Desktop backlog
slices only when they have a named behavior, focused tests, and a rollback path.
Do not use the Desktop tree to contradict installed-runtime evidence without
first stating that the Desktop tree is stale/backlog evidence. If the Desktop
tree is curated later, create a backlog manifest first: branch, HEAD, changed
files, behavior to port, tests to run, legal/provenance state, and rollback
path.
