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
    "title": "Regex operator precedence bug in _looks_like_memory_forget_request",
    "actual_behavior": "The regex alternation (|) has lower precedence than concatenation, so the word boundary \b only applies to the first alternative. This means 'profile factor' and 'memory foam' could incorrectly match the pattern.",
    "expected_behavior": "All alternatives should share proper word boundaries to prevent false positives.",
    "repro_steps": [
      "1. Send 'delete my memory foam mattress' to the bot",
      "2. _looks_like_memory_forget_request incorrectly matches 'memory foam' as a memory reference",
      "3. Bot triggers memory deletion flow instead of treating it as a regular message"
    ],
    "affected_workflow": "Memory forget detection — false positives on phrases like 'memory foam', 'profile factor'"
  },
  "evidence": {
    "safe_links_only": true,
    "before_after_proof": "Before: \b(?:saved\s+)?memor(?:y|ies)|\bprofile\s+(?:fact|memory) — \b only on first alt. After: \b(?:(?:saved\s+)?memor(?:y|ies)|profile\s+(?:fact|memory)|active\s+current\s+profile)\b — shared boundaries.",
    "links": [],
    "forbidden": ["pdf", "zip", "exe", "tokens", "raw logs", "private repo maps", "shortened links", "binaries"]
  },
  "proposed_fix": {
    "approach": "Wrap all alternatives in a non-capturing group with shared \b boundaries on both sides.",
    "files_expected": ["src/spark_intelligence/adapters/telegram/runtime.py"],
    "tests_or_smoke": "1. 'delete my memory' → should match. 2. 'delete my memory foam' → should NOT match. 3. 'forget my profile fact' → should match. 4. 'profile factory' → should NOT match."
  },
  "pr": {
    "branch": "fix-regex-precedence-forget",
    "title_prefix": "[spark-compete]",
    "author_github": "Esc1200",
    "body_must_include": ["packet", "team", "pr_author", "repo", "actual_behavior", "expected_behavior", "repro_steps", "before_after_proof", "tests_or_smoke", "duplicate_notes", "risk_notes", "review_claim"]
  },
  "review_claim": {
    "impact_claim": "medium",
    "evidence_types": ["code_diff", "repro_steps"],
    "duplicate_notes": "No existing PRs address the regex precedence issue. Checked all open PRs.",
    "risk_notes": "Low risk — single regex pattern fix with shared word boundaries. No logic changes."
  }
}
```

## Security Design Statement

This fix corrects a regex precedence bug that causes false positives in memory forget detection. Phrases like "memory foam" or "profile factor" incorrectly trigger memory deletion flow.

## Trust Boundary

**Boundary crossed:** User message → Memory operation detection
**Impact:** False positives cause unintended memory deletion flow triggers.

## PR Author
@Esc1200

## Duplicate Notes
No existing PRs address this issue.

## Risk Notes
- Single regex pattern fix
- Shared word boundaries prevent false positives
- 1 line changed

## Tests / Smoke
1. "delete my memory" → matches (correct)
2. "delete my memory foam" → no match (correct)
3. "forget my profile fact" → matches (correct)
4. "profile factory" → no match (correct)
