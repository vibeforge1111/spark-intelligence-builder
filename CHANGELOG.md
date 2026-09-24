# Changelog

## [Unreleased]

### Added

- None.

### Changed

- None.

### Fixed

- Phase 1.3 (PR #1010): Windows chip creation forwards the minimal operating-system environment needed by the account-authenticated Codex CLI, while keeping credentials and unrelated variables excluded. Mac/Linux behavior is unchanged.

### Scoring

- Phase 2.1 (PR #1011): new starter chips get one explicitly authored draft-or-clarify development case. Generic starters retain 14 cases; realistic packs retain every original case plus the new case, with matching manifests. Scores before and after this change are not comparable as one trend.


- None: this environment repair does not change benchmark or judging rules.

### Session status — 2026-09-24

- Windows environment PR #1010 merged after all required CI. This isolated outcome-window counterpart follows desktop PR #31 and must pass all exact-head Builder checks before merge.
- Seven new regressions failed before and pass after; twelve focused starter tests pass. Existing realistic-pack count assertions reflect the additional case, with all evidence/protection assertions preserved. No live chip, provider call or running checkout changed.
