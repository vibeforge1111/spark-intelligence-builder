# Chip creation: configured Codex executable

Windows desktop launchers supply CODEX_BIN for a standalone Codex executable. Chip creation searched only PATH, so it could return codex_cli_missing while training worked through its configured executable.

Creation now resolves CODEX_BIN when supplied, retaining PATH discovery otherwise. A missing explicit executable fails closed; there is no fallback to another executable. Governed dispatch, prompt screening, model choice and the read-only sandbox are unchanged.

Regression coverage checks explicit paths with spaces, PATH fallback and invalid explicit paths without dispatch. No real provider calls are needed. The separate desktop error message incorrectly claiming benchmark readiness on failed authoring remains a follow-up.

The repository's fleet helper was absent at both documented local locations and in the local Spark/workspace file search. Work was isolated in a dedicated Git worktree and branch; existing runtime edits were preserved.
