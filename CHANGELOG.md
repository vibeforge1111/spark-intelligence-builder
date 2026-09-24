# Changelog

## [Unreleased]

### Fixed

- Phase 1.3 (PR pending): Windows chip creation forwards the minimal operating-system environment needed by the account-authenticated Codex CLI, while keeping credentials and unrelated variables excluded. Mac/Linux behavior is unchanged.

### Session status — 2026-09-24

- Prepared in an isolated worktree; five focused tests pass and the original Windows test fails before the fix. Compilation and whitespace checks pass. Broader creation/bridge tests have 44 passes and six failures that also occur with the original environment function. Three involve Windows path/python3 assumptions; three assume no available Codex provider and fail differently with the original function. These are not a full-suite pass. Not published or merged; Desktop prerequisites land first.
