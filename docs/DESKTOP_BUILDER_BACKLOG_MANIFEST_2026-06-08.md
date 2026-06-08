# Desktop Builder Backlog Manifest - 2026-06-08

This manifest records read-only evidence for the stale Desktop Builder checkout.
It does not promote, merge, archive, move, or delete any Desktop files.

## Classification

| Field | Value |
| --- | --- |
| Desktop path | `C:\Users\USER\Desktop\spark-intelligence-builder` |
| Live runtime path | `C:\Users\USER\.spark\modules\spark-intelligence-builder\source` |
| Desktop branch | `codex/browser-use-receipts` |
| Desktop upstream | `origin/codex/browser-use-receipts` is gone |
| Desktop HEAD | `bc8dfd3e39df6f74a2829e00bef8726b3aa7ed7a` |
| Runtime code-bearing HEAD | `b34549e8d6327b8d3faf6b92536d90df66230152` |
| Verdict | backlog/historical evidence, not live runtime truth |

## Dirty Surface Summary

Read-only `git status --short --branch` showed 33 modified tracked files plus
untracked `LICENSE` and `docs/creator/`.

Read-only `git diff --stat` showed:

```text
33 files changed, 2411 insertions(+), 1083 deletions(-)
```

Notable touched areas:

- CI and packaging: `.github/workflows/ci.yml`, `pyproject.toml`, `spark.toml`.
- Runtime and gateway: auth, gateway, diagnostics, observability, user
  instructions.
- Memory and workflow recovery: memory orchestrator, procedural lessons,
  shadow replay.
- Self-awareness: capability ledger, capsule, self-awareness tests.
- Tests: attachment hooks, harness CLI, memory, procedural lessons, runtime path
  normalization, self-awareness, swarm sync.

## Manifest And License Signals

Desktop `spark.toml` currently declares:

- `license = "AGPL-3.0-only"`
- `[needs].modules = []`

Desktop `pyproject.toml` currently declares:

- `license = "AGPL-3.0-only"`
- dependency on `spark-character`

Desktop `LICENSE` is an untracked AGPL-3.0 license file. Because it is
untracked in the Desktop checkout, it is evidence of intended cleanup only, not
a committed source-truth fix.

## Handling Policy

Do not use this Desktop tree to contradict installed-runtime evidence.

Allowed path:

1. Pick one named behavior from the Desktop diff.
2. Write a short porting note with exact source files and expected behavior.
3. Port the minimum behavior onto the live runtime tree.
4. Run focused tests in the live runtime tree.
5. Commit only the curated live-runtime change.

Blocked path:

- wholesale merge;
- copying dirty Desktop files over the installed runtime;
- treating Desktop AGPL/needs metadata as fixed until committed on the live
  runtime line;
- publishing or pinning Desktop HEAD as runtime truth.

## Next Check

Before any future Desktop cleanup, rerun:

```powershell
git -C C:\Users\USER\Desktop\spark-intelligence-builder status --short --branch
git -C C:\Users\USER\Desktop\spark-intelligence-builder rev-parse HEAD
git -C C:\Users\USER\.spark\modules\spark-intelligence-builder\source status --short --branch
git -C C:\Users\USER\.spark\modules\spark-intelligence-builder\source rev-parse HEAD
```
