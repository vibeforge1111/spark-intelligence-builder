# Changelog

## [Unreleased]

### Added

- None.

### Changed

- None.

### Fixed

- Phase 1.3 (PR pending): Windows chip creation forwards the minimal operating-system environment needed by the account-authenticated Codex CLI, while keeping credentials and unrelated variables excluded. Mac/Linux behavior is unchanged.

### Scoring

- None: this environment repair does not change benchmark or judging rules.

### Session status — 2026-09-24

- Prepared in an isolated worktree. Five focused environment tests pass; the Windows regression fails before the fix. Broader local tests have 44 passes and six pre-existing Windows/provider-availability failures, so this is not a full-suite pass.
- Desktop prerequisite PRs #19–#21 and Windows prompt repair #18 are merged after fully passing CI. Desktop JSON repair #22 is checking its corrected revision; Builder publication follows it in Phase 1 order.
- Builder main remains 19afe6cf and its latest CI run 35945719937 passed. The environment repair itself is not published or CI-verified yet. No training data or running checkout has changed.
