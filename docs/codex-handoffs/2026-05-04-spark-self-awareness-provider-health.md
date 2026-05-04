# Codex Handoff: Spark Self-Awareness, LLM Wiki, and Provider Health

Date: 2026-05-04

## Repo And Branch

- Repo path: `C:\Users\USER\AppData\Local\Temp\spark-builder-live-wiki-answer-clean-0fb48574`
- Remote: `https://github.com/vibeforge1111/spark-intelligence-builder.git`
- Branch: `main`
- Current git state at handoff creation: clean and aligned with `origin/main`
- Latest pushed commit: `2c2a1fb Record direct provider health evidence`

Recent relevant commits:

- `2c2a1fb Record direct provider health evidence`
- `74a8550 Support ignored attachment roots for local health`
- `597db8d Clarify aggregate Builder readiness in self status`
- `f255ec3 Add live Telegram verifier cutoff`
- `4441d8f Polish live Telegram cadence text output`
- `a9f9dcb Add live Telegram prompt runbook artifact`
- `625385c Harden live Telegram evidence diagnostics`
- `7aa6be4 Add self-awareness handoff freshness check`

## Current Goal

Make Spark agents genuinely self-aware and introspective without turning the system into deterministic chatbot scripts. The goal is for Spark, especially through Telegram and Builder, to know and explain:

- what systems, tools, chips, routes, memory layers, and wiki pages are available
- what is verified versus merely configured
- where it is strong, weak, stale, blocked, or missing evidence
- how to improve weak spots safely, with natural-language invocability
- how user memory, Spark doctrine, project context, and wiki knowledge stay separate
- how LLM wiki knowledge supports answers without outranking live runtime truth

The immediate slice just completed was provider and health evidence hardening: ZAI auth is present, Builder direct provider execution works, and self-status can now see that provider as a fresh observed success.

## Completed So Far

Provider and auth health:

- Synced `ZAI_API_KEY` from `C:\Users\USER\.env.zai` into Spark home `C:\Users\USER\.spark-intelligence\.env` without printing the secret value.
- Confirmed provider runtime resolves to:
  - provider: `custom`
  - auth method: `api_key_env`
  - API mode: `chat_completions`
  - transport: `direct_http`
  - model: `glm-5.1`
  - secret ref: `ZAI_API_KEY`
- Verified direct provider execution by sending a minimal governed health probe and receiving `OK`.
- Fixed stale auth status derivation so a static env profile moves from `pending_secret` to `active` after the env secret exists.
- Added redacted direct-provider observability events for governed success/failure, so self-status can see provider `custom` as recently successful with latency.

Self-awareness and wiki health:

- `self status` now reports `degraded_or_missing` as empty for the fast core snapshot.
- `custom` provider appears in capability evidence as `recent_success`, `fresh`, `can_claim_confidently=true`, with route latency recorded.
- Real wiki vault at `C:\Users\USER\.spark-intelligence\wiki` was bootstrapped/refreshed.
- `wiki status` reports:
  - `healthy=true`
  - `valid=true`
  - `markdown_page_count=20`
  - no missing bootstrap/system compile files
  - authority remains `supporting_not_authoritative`
- The LLM wiki metadata contract is visible:
  - `wiki_family`
  - `owner_system`
  - `authority`
  - `scope_kind`
  - `source_of_truth`
  - `freshness`
  - `status`
  - `generated_at`
  - `last_verified_at`
  - `source_path`

Attachment/local health:

- Added ignored attachment roots support so known irrelevant/broken roots no longer make local health noisy.
- Real Spark home config now ignores:
  - `C:\Users\USER\Desktop\domain-chip-crypto-trading-master-compare`
  - `C:\Users\USER\Desktop\_tmp_spark_domain_chip_labs`
  - `C:\Users\USER\Desktop\domain-chip-spark-private-main`
- `attachments status` had `warnings=0` after this slice.

Canonical docs and gates:

- `self handoff-check --json` passes.
- Canonical docs already present:
  - `docs/SPARK_SELF_AWARENESS_HARDENING_TASKS_2026-05-01.md`
  - `docs/SPARK_SELF_AWARENESS_LLM_WIKI_HANDOFF_2026-05-01.md`
  - `docs/SPARK_LLM_WIKI_ARCHITECTURE_PLAN_2026-05-01.md`
- This archival Codex handoff is separate from those canonical docs and is intended for fresh-chat reactivation.

## Files Touched

Recent provider/auth commit:

- `src/spark_intelligence/auth/runtime.py`
  - Treats a `pending_secret` static profile as `active` once the referenced env secret is present.
- `src/spark_intelligence/llm/direct_provider.py`
  - Records redacted `tool_result_received` and `dispatch_failed` events for governed direct-provider execution.
  - Captures capability key, provider id/kind, API mode, auth method, model, latency, and eval ref.
  - Does not record prompts, raw responses, or secret values.
- `tests/test_auth_profiles.py`
  - Regression for pending static profile becoming active after env secret appears.
- `tests/test_direct_provider_execution.py`
  - Regression for governed direct provider success telemetry and secret redaction.

Previous local-health commit:

- `src/spark_intelligence/config/loader.py`
- `src/spark_intelligence/attachments/registry.py`
- `tests/test_attachment_hooks.py`
- `docs/SPARK_SELF_AWARENESS_HARDENING_TASKS_2026-05-01.md`
- `docs/SPARK_SELF_AWARENESS_LLM_WIKI_HANDOFF_2026-05-01.md`

Important files investigated:

- `src/spark_intelligence/auth/runtime.py`
- `src/spark_intelligence/auth/service.py`
- `src/spark_intelligence/llm/direct_provider.py`
- `src/spark_intelligence/llm/provider_wrapper.py`
- `src/spark_intelligence/researcher_bridge/advisory.py`
- `src/spark_intelligence/observability/store.py`
- `src/spark_intelligence/self_awareness/capsule.py`
- `tests/test_auth_profiles.py`
- `tests/test_direct_provider_execution.py`
- `tests/test_self_awareness.py`
- `docs/SPARK_SELF_AWARENESS_LLM_WIKI_HANDOFF_2026-05-01.md`
- `docs/SPARK_LLM_WIKI_ARCHITECTURE_PLAN_2026-05-01.md`
- `docs/SPARK_SELF_AWARENESS_HARDENING_TASKS_2026-05-01.md`

## Commands And Checks Already Run

Repo and handoff checks:

```powershell
git status --short --branch
git remote -v
git log --oneline -8
$env:PYTHONPATH='src'; python -m spark_intelligence.cli self handoff-check --json
```

Latest `self handoff-check --json` result:

- `status=pass`
- `healthy=true`
- `doc_update_required=false`
- `warnings=[]`
- report path: `C:\Users\USER\.spark-intelligence\artifacts\handoff-freshness\2026-05-04T062520Z0000.json`

Auth/provider checks:

```powershell
$env:PYTHONPATH='src'; python -m spark_intelligence.cli auth status --home C:\Users\USER\.spark-intelligence --json
```

Latest auth result:

- `ok=true`
- `default_provider=custom`
- provider status: `active`
- model: `glm-5.1`
- base URL present: `https://api.z.ai/api/coding/paas/v4/`
- secret ref: `ZAI_API_KEY`
- secret present: `true`

Direct provider probe:

- Used `ConfigManager.from_home("C:\Users\USER\.spark-intelligence")`
- Resolved provider via `resolve_runtime_provider`
- Called `execute_direct_provider_prompt` with a governed health prompt
- Result: `ok=true`, provider `custom`, model `glm-5.1`, reply preview `OK`
- After telemetry patch, self-status showed provider `custom` with fresh `recent_success`

Wiki/local health:

```powershell
$env:PYTHONPATH='src'; python -m spark_intelligence.cli wiki bootstrap --home C:\Users\USER\.spark-intelligence --json
$env:PYTHONPATH='src'; python -m spark_intelligence.cli wiki compile-system --home C:\Users\USER\.spark-intelligence --json
$env:PYTHONPATH='src'; python -m spark_intelligence.cli wiki status --home C:\Users\USER\.spark-intelligence --json
$env:PYTHONPATH='src'; python -m spark_intelligence.cli attachments status --home C:\Users\USER\.spark-intelligence --json
```

Focused tests:

```powershell
$env:PYTHONPATH='src'; python -m pytest tests\test_auth_profiles.py -q
$env:PYTHONPATH='src'; python -m pytest tests\test_direct_provider_execution.py tests\test_auth_profiles.py -q
$env:PYTHONPATH='src'; python -m pytest tests\test_direct_provider_execution.py tests\test_auth_profiles.py tests\test_self_awareness.py -q
git diff --check
git diff --cached --check
```

Latest focused test result:

- `55 passed, 1 warning in 423.98s`
- Warning was a pre-existing Telegram runtime `datetime.utcnow()` deprecation warning.

Git:

```powershell
git add src\spark_intelligence\auth\runtime.py src\spark_intelligence\llm\direct_provider.py tests\test_auth_profiles.py tests\test_direct_provider_execution.py
git commit -m "Record direct provider health evidence"
git push origin main
```

Push succeeded to `main`. A GitHub CLI credential helper warning appeared, but the push completed:

```text
/mnt/c/Program\ Files/GitHub\ CLI/gh.exe auth git-credential store: line 1: /mnt/c/Program Files/GitHub CLI/gh.exe: No such file or directory
```

## Known Errors, Warnings, Or Failing Checks

Current hard failures:

- None in the focused test surface.
- Current repo state was clean after pushing `2c2a1fb`.
- `self handoff-check --json` passes.

Known warnings/noise:

- `tests/test_self_awareness.py::SelfAwarenessCapsuleTests::test_normal_telegram_message_uses_self_awareness_with_wiki_context` emits a pre-existing `datetime.utcnow()` deprecation warning from `src/spark_intelligence/adapters/telegram/runtime.py`.
- `git push` prints a GitHub CLI credential helper path warning, but push succeeds.
- `git diff --check` prints CRLF warnings on Windows working-copy files, but exits successfully.
- `python -m spark_intelligence.cli self status --home ... --json --compact` failed because `--compact` is not a supported flag for `self status`; use JSON plus external parsing instead.
- `rg ... AGENTS.md` reported missing `AGENTS.md` in this repo; no repo-local AGENTS file was found in this checkout.
- The `document-release` skill was not found under `C:\Users\USER\.codex\skills`; the installed copy exists under `C:\Users\USER\Desktop\BuildRoyale\document-release\SKILL.md`.

Known system weak spots from self-status:

- Researcher bridge generic adapter is disabled by policy:
  - error says set `SPARK_RESEARCHER_ENABLE_GENERIC_ADAPTER=1` and `SPARK_RESEARCHER_ADAPTER_ALLOWED_EXECUTABLES` to an explicit allowlist.
  - This is not a provider auth failure.
- Old browser evidence still includes `browser_permission_required` and `browser_unavailable` failures.
- Old `startup-yc` evidence includes a prior provider HTTP 401 from before ZAI auth was corrected; direct provider health is now green, but route-specific startup-yc should be reprobed before claiming it is healthy.
- Self-awareness still correctly reports the design boundary: configured/available is not proof of a successful invocation.

## Open Decisions

1. Whether to enable the Researcher generic adapter.
   - Recommended only with a tight executable allowlist.
   - Do not broadly enable generic execution just to remove a warning.

2. Whether browser capability needs to be live for the next Telegram test.
   - If yes, repair/authorize browser session and rerun a browser route probe.
   - If no, keep browser as an honest weak spot and continue provider/wiki/Telegram testing.

3. Whether to re-run startup-yc route after provider auth has been fixed.
   - This would clear or update the old 401 evidence for that route.

4. How much direct-provider telemetry should be treated as eval coverage.
   - Current patch marks it as `observed`, not `covered`.
   - This is intentionally conservative.

5. Whether to add route-selection evals now or after live Telegram manual tests.
   - Recommended order: live manual tests first, then add only the evals that reflect real failure modes.

6. Whether to add this Codex handoff path to `docs/manifests/spark_memory_kb_repo_sources.json`.
   - The canonical handoff mentions adding handoff docs to the manifest when they should become project context.
   - This archival handoff may be useful for Codex restart only; decide before ingesting it into KB.

## Constraints And Preferences

User preferences and operating mode:

- Commit often and push completed slices to `main`.
- Keep changes surgical and avoid overengineering.
- Prefer LLM-powered, source-aware cognition over deterministic chatbot-like responses.
- Spark should answer naturally, but every claim about capability/health should know its evidence boundary.
- Natural-language invocability matters: users should be able to ask Spark to test, improve, or explain itself.
- The self-awareness system should include Spark, the user context, projects, environment, Builder abilities, memory, wiki, tools, routes, and self-improvement loops.

Hard boundaries:

- Do not print or store secret values.
- Do not store prompts, raw provider responses, or conversational residue as durable truth.
- Do not promote user memory into global Spark doctrine.
- Do not let wiki outrank current-state memory for mutable user facts.
- Wiki remains `supporting_not_authoritative`.
- Memory KB pages are downstream of governed memory snapshots, not independent truth.
- Graphiti/sidecar memory remains advisory until evals pass.
- Current-state memory outranks wiki for mutable user facts.
- User memory stays separate from global Spark doctrine.
- Do not enable broad generic adapter execution without explicit policy and allowlist.
- Preserve provenance, source-aware recall, human/agent scoping, memory movement traceability, concise Telegram replies, and anti-residue memory discipline.

Repo/tool constraints:

- Use `$env:PYTHONPATH='src'; python -m spark_intelligence.cli ...` when running local CLI from this checkout.
- Use `rg` for searches.
- Use `apply_patch` for manual file edits.
- Do not revert unrelated changes.
- Be careful with real home state under `C:\Users\USER\.spark-intelligence`; it is live local Spark state, not just repo fixtures.

## Next Concrete Steps

1. Pull/confirm latest `main`, then run:

   ```powershell
   git status --short --branch
   $env:PYTHONPATH='src'; python -m spark_intelligence.cli self handoff-check --json
   ```

2. Run a compact real-health snapshot:

   ```powershell
   $env:PYTHONPATH='src'; python -m spark_intelligence.cli auth status --home C:\Users\USER\.spark-intelligence --json
   $env:PYTHONPATH='src'; python -m spark_intelligence.cli wiki status --home C:\Users\USER\.spark-intelligence --json
   $env:PYTHONPATH='src'; python -m spark_intelligence.cli attachments status --home C:\Users\USER\.spark-intelligence --json
   ```

3. Run live Telegram natural-language tests against the self-awareness goal:

   - "What systems can you see right now?"
   - "Where are you strong, weak, stale, or missing evidence?"
   - "Can you test the provider route now?"
   - "What can you improve next, and what evidence do you need first?"
   - "What do you know about me versus global Spark doctrine?"

4. Use `self live-telegram-cadence` / live verifier artifacts to decide whether the Telegram route is genuinely green enough to claim.

5. Decide on Researcher generic adapter policy:

   - If needed, configure only a narrow explicit allowlist.
   - Rerun the same bridge path that previously returned `bridge_error`.
   - Keep direct provider health separate from bridge execution health.

6. Reprobe route-specific stale failures if they matter:

   - startup-yc after ZAI fix
   - browser route after browser permission/session fix
   - any chip the user wants Spark to claim confidently

7. Add only the evals that the live tests justify:

   - stale status trap
   - "configured is not invoked" overclaim trap
   - natural-language "improve this weak spot" route
   - user-memory versus global-doctrine boundary
   - wiki supporting-not-authoritative boundary

## Reactivation Prompt

Paste this into a fresh Codex chat:

```text
Continue Spark self-awareness / LLM wiki / provider health hardening in:
C:\Users\USER\AppData\Local\Temp\spark-builder-live-wiki-answer-clean-0fb48574

Branch: main
Remote: https://github.com/vibeforge1111/spark-intelligence-builder.git
Latest known pushed commit: 2c2a1fb Record direct provider health evidence

Read this handoff first:
C:\Users\USER\AppData\Local\Temp\spark-builder-live-wiki-answer-clean-0fb48574\docs\codex-handoffs\2026-05-04-spark-self-awareness-provider-health.md

Then read the canonical docs:
C:\Users\USER\AppData\Local\Temp\spark-builder-live-wiki-answer-clean-0fb48574\docs\SPARK_SELF_AWARENESS_LLM_WIKI_HANDOFF_2026-05-01.md
C:\Users\USER\AppData\Local\Temp\spark-builder-live-wiki-answer-clean-0fb48574\docs\SPARK_LLM_WIKI_ARCHITECTURE_PLAN_2026-05-01.md
C:\Users\USER\AppData\Local\Temp\spark-builder-live-wiki-answer-clean-0fb48574\docs\SPARK_SELF_AWARENESS_HARDENING_TASKS_2026-05-01.md

Current goal:
Make Spark agents genuinely self-aware and introspective across Telegram and Builder. Spark should know what it can do, where it lacks evidence, what systems/routes/tools/memory/wiki it can access, how the user/project/environment context fits, and how to improve safely via natural language. Keep this LLM/source-aware and evidence-grounded, not deterministic chatbot scripting.

Before making changes, run:
git status --short --branch
$env:PYTHONPATH='src'; python -m spark_intelligence.cli self handoff-check --json

Known current health:
- ZAI provider secret is present in C:\Users\USER\.spark-intelligence\.env via env ref ZAI_API_KEY.
- auth status reports provider custom active, model glm-5.1, direct_http chat_completions.
- Direct provider probe returned OK and now records redacted capability evidence.
- wiki status is healthy with 20 pages and supporting_not_authoritative authority.
- self status has no degraded core systems in the fast snapshot.

Guardrails:
- Do not print or store secret values.
- Do not store prompts or raw model responses in provider telemetry.
- Wiki is supporting_not_authoritative and never outranks current-state memory for mutable user facts.
- User memory stays separate from global Spark doctrine.
- Graphiti/sidecar hits remain advisory until evals pass.
- Do not enable generic adapter execution broadly; if needed, use an explicit narrow allowlist.
- Avoid overengineering. Probe first, then add only the smallest eval/code/doc change that real evidence justifies.

Next recommended steps:
1. Confirm repo is clean and run self handoff-check.
2. Run auth/wiki/attachments status against C:\Users\USER\.spark-intelligence.
3. Run live Telegram natural-language self-awareness tests.
4. Decide whether Researcher generic adapter should remain blocked or be enabled with a tight allowlist.
5. Reprobe startup-yc and browser only if those routes are needed for the user's next goal.
6. Add route-selection/self-awareness evals only for failures observed in live testing.
7. Commit and push small slices to main.
```
