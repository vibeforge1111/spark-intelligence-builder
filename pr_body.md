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
    "severity": "high",
    "title": "Hardcoded debug log path writes user message content to disk",
    "actual_behavior": "The _maybe_save_reply_as_draft() function in runtime.py contains a debug logging block that writes to a hardcoded path 'C:/Users/USER/Desktop/spark-intelligence-builder/.tmp-home-live-telegram-real/logs/draft_capture_probe.log'. This creates directories and writes user message content (first 120 chars) to disk on every call in production. The debug block silently swallows exceptions if the path doesn't exist, but creates the directory tree if it does.",
    "expected_behavior": "No debug logging to hardcoded filesystem paths in production code.",
    "repro_steps": [
      "1. Deploy the intelligence builder with a user whose home directory contains 'USER'",
      "2. Send any message through the Telegram adapter",
      "3. Check C:/Users/USER/Desktop/spark-intelligence-builder/.tmp-home-live-telegram-real/logs/",
      "4. Find draft_capture_probe.log containing user message content"
    ],
    "affected_workflow": "All Telegram adapter message processing — user message content leaked to filesystem"
  },
  "evidence": {
    "safe_links_only": true,
    "before_after_proof": "Before: Lines 343-354 contain try/except block that creates directories and writes user messages to hardcoded path. After: Debug block removed entirely.",
    "links": [],
    "forbidden": ["pdf", "zip", "exe", "tokens", "raw logs", "private repo maps", "shortened links", "binaries"]
  },
  "proposed_fix": {
    "approach": "Remove the entire debug logging block (lines 343-354). This is clearly leftover development debug code that should not be in production.",
    "files_expected": ["src/spark_intelligence/adapters/telegram/runtime.py"],
    "tests_or_smoke": "1. Send message through Telegram adapter. 2. Verify no draft_capture_probe.log is created. 3. Verify normal message processing still works."
  },
  "pr": {
    "branch": "fix-hardcoded-debug-log-path",
    "title_prefix": "[spark-compete]",
    "author_github": "Esc1200",
    "body_must_include": ["packet", "team", "pr_author", "repo", "actual_behavior", "expected_behavior", "repro_steps", "before_after_proof", "tests_or_smoke", "duplicate_notes", "risk_notes", "review_claim"]
  },
  "review_claim": {
    "impact_claim": "high",
    "evidence_types": ["code_diff", "repro_steps"],
    "duplicate_notes": "No existing PRs address the hardcoded debug log path. Checked all open PRs.",
    "risk_notes": "Low risk — removes 12 lines of debug-only code. No functional behavior changed."
  }
}
```

## Security Design Statement

This fix removes a debug logging block that writes user message content to a hardcoded filesystem path. In production, this creates unnecessary files containing user data and creates directory trees without user consent. The hardcoded path also leaks the development environment structure.

## Trust Boundary

**Boundary crossed:** User message processing → Filesystem write
**Impact:** User message content (first 120 chars) is written to disk on every message, creating an unintended data persistence path.

## PR Author
@Esc1200

## Duplicate Notes
No existing PRs address this issue. PRs #343 and #344 address subprocess timeouts in different files.

## Risk Notes
- Removes 12 lines of debug-only code
- No functional behavior changed
- The debug block was clearly leftover from local development

## Tests / Smoke
1. Send message through Telegram adapter → no log file created
2. Normal message processing unaffected
3. No directory creation at hardcoded path
