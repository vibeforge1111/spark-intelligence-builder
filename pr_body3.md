## Spark Compete Packet

```json
{
  "schema": "spark-compete-hotfix-v1",
  "event": "spark-compete-first-event",
  "submission_mode": "public_repo_pr",
  "submission_target_url": "https://github.com/vibeforge1111/spark-intelligence-builder/pull/PLACEHOLDER",
  "team": {
    "name": "Bug Hunters",
    "members": ["Esc1200", "ZakJan777", "dara917"],
    "llm_device_holder": "Esc1200",
    "device_holder_github": "Esc1200",
    "github_accounts": ["Esc1200", "ZakJan777", "dara917"]
  },
  "target_repo": {
    "id": "vibeforge1111/spark-intelligence-builder",
    "source": "https://github.com/vibeforge1111/spark-intelligence-builder",
    "owner_surface": "spark-intelligence-builder"
  },
  "issue": {
    "type": "bug",
    "severity": "medium",
    "title": "run_governed_command default timeout is None — subprocesses can hang forever",
    "actual_behavior": "run_governed_command() has timeout_seconds defaulting to None. Callers that don't explicitly pass a timeout (e.g., execute_chip_hook_record in hooks.py) will block indefinitely if the child process hangs.",
    "expected_behavior": "A reasonable default timeout should prevent indefinite hangs while still allowing callers to override.",
    "repro_steps": [
      "1. Run a chip hook that invokes a hanging process",
      "2. execute_chip_hook_record() calls run_governed_command() without timeout",
      "3. The subprocess hangs forever, blocking the caller"
    ],
    "affected_workflow": "Chip hook execution — any hanging child process blocks the entire pipeline"
  },
  "evidence": {
    "safe_links_only": true,
    "before_after_proof": "Before: timeout_seconds: float | None = None. After: timeout_seconds: float | None = 120. Callers can still override with explicit timeout.",
    "links": [],
    "forbidden": ["pdf", "zip", "exe", "tokens", "raw logs", "private repo maps", "shortened links", "binaries"]
  },
  "proposed_fix": {
    "approach": "Change default timeout from None to 120 seconds. This is a reasonable default for most command executions while preventing indefinite hangs.",
    "files_expected": ["src/spark_intelligence/execution/governed.py"],
    "tests_or_smoke": "1. Run chip hook with normal command → completes within 120s. 2. Run chip hook with hanging command → times out after 120s. 3. Caller with explicit timeout still uses their value."
  },
  "pr": {
    "branch": "fix-governed-default-timeout",
    "title_prefix": "[spark-compete]",
    "author_github": "Esc1200",
    "body_must_include": ["packet", "team", "pr_author", "repo", "actual_behavior", "expected_behavior", "repro_steps", "before_after_proof", "tests_or_smoke", "duplicate_notes", "risk_notes", "review_claim"]
  },
  "review_claim": {
    "impact_claim": "medium",
    "evidence_types": ["code_diff", "repro_steps"],
    "duplicate_notes": "PRs #343 and #344 address subprocess timeouts in different files (runtime.py). This fix addresses the governed.py default timeout.",
    "risk_notes": "Low risk — changes one default parameter. Callers with explicit timeout are unaffected. 120s is generous enough for normal operations."
  }
}
```

## Security Design Statement

This fix prevents indefinite process hangs by setting a reasonable default timeout for governed command execution. Without a timeout, a hanging child process can block the entire pipeline indefinitely.

## Trust Boundary

**Boundary crossed:** Command execution → Subprocess management
**Impact:** A hanging child process blocks the caller indefinitely, potentially causing resource exhaustion.

## PR Author
@Esc1200

## Duplicate Notes
PRs #343 and #344 address subprocess timeouts in runtime.py. This fix addresses the governed.py default timeout which is a separate call site.

## Risk Notes
- Changes one default parameter value
- 120s is generous for normal operations
- Callers with explicit timeout unaffected

## Tests / Smoke
1. Normal command execution completes within 120s
2. Hanging command times out after 120s
3. Explicit timeout override still works
