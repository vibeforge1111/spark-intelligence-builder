# Spark Intelligence Continuation Plan 2026-04-09

## 1. Purpose

This note turns the April 8 to April 9, 2026 Builder handoff into the concrete next execution order after the benchmark-causality work resumed.

## 2. What Changed Today

The main architecture blocker from April 9, 2026 was:

- `startup-operator` autoloop mutated a prompt file
- `thestartupbench run-baseline` only evaluated fixed heuristic baselines
- so the mutation target could not causally affect the measured score

That blocker is now reduced at the bridge and path-repo layers:

- `spark-swarm` now supports benchmark-coupled Startup Bench autoloops when the repo-owned mutation target is a tool-script JSON executed through `thestartupbench run-script`
- `specialization-path-startup-operator` now declares `benchmarks/startup-operator.tool_calls.json` as the mutation target instead of a prompt document
- the startup-operator path repo now ships a real executable tool script and a hook that emits JSON tool-script candidates instead of pretending a doctrine prompt affects the benchmark

Verification completed locally:

- `spark-swarm`: focused bridge tests passed for autoloop, startup-bench adapter, and runtime-context updates
- `specialization-path-startup-operator`: local hook tests passed
- real smoke check: `python -m thestartupbench run-script ... benchmarks/startup-operator.tool_calls.json ...` executed successfully against `minimal_0to1_scenario`
- real Spark Swarm autoloop check: `spark-swarm specialization-path autoloop startup-operator C:\Users\USER\Desktop\specialization-path-startup-operator --plan-only`
- real Spark Swarm forced round: `spark-swarm specialization-path autoloop startup-operator C:\Users\USER\Desktop\specialization-path-startup-operator --rounds 1 --force`
- Builder Telegram autoloop/session reply tests now surface script-backed runner details and plain-language stop reasons

## 3. Current Honest Status

### True Now

- a real benchmark-consumed mutation seam exists for `startup-operator`
- the current repo-owned target is executable by Startup Bench
- the real Spark Swarm autoloop path has now been exercised against the canonical startup-operator repo
- round summaries now record script-backed execution with `benchmarkRunnerType: script`
- Builder's Telegram autoloop replies now surface the benchmark runner, mutation target, and clearer stop language
- prompt-only startup-bench mutation paths remain blocked

### Not Yet Proved End To End

- live Builder-home Telegram and browser health on the canonical operator surface after these changes
- a real Telegram DM pass that observes and explains the new runner shape cleanly
- improvement visibility through Builder operator UX

## 4. Next Execution Order

### Phase 1. Re-run The Proven Seam On Demand

This is no longer speculative. The seam has already been proven once on April 9, 2026.

When re-running on the real path repo, use:

- `spark-swarm specialization-path autoloop startup-operator C:\Users\USER\Desktop\specialization-path-startup-operator --plan-only`
- `spark-swarm specialization-path autoloop startup-operator C:\Users\USER\Desktop\specialization-path-startup-operator --rounds 1 --force`
- if needed later, one manual round with `--candidate-file` against the tool-script target

Confirm:

- preflight passes
- the round summary reports script-backed execution instead of heuristic-baseline-only execution
- keep vs revert decisions are driven by the executable tool script score

### Phase 2. Re-verify Builder As The Live Operator Surface

Run on the canonical Builder home:

- Telegram channel health
- Swarm status
- browser status
- real Telegram prompts for the latest startup-operator autoloop session

Confirm:

- Builder still owns Telegram ingress
- Swarm downstream reads/actions still work
- startup-operator autoloop replies stay truthful with the new benchmark-coupled target

### Phase 3. Add Builder Reply Composition

Implement the reply-composition layer already scoped in `docs/TELEGRAM_COMMUNICATION_AND_EVOLUTION_PLAN_2026-04-09.md`.

Priority reply shapes:

- autoloop paused
- autoloop finished
- autoloop session inspection
- blocked / unavailable / auth-failed
- lane insight summary
- browser-backed answer

Rule:

- lead with verdict
- then reason
- then compressed details
- then next useful action

### Phase 4. Make Improvement Visibility Explicit

Builder should be able to say:

- what changed
- whether it was proposed or kept
- whether it was delivered or only observed
- whether the active behavior is now using the new artifact

This should be driven by structured state, not by asking the operator to inspect raw files.

## 5. Guardrails

- do not claim prompt-driven Startup Bench improvement; that path is still invalid
- do not call a round successful unless the executable tool-script candidate beats the current tool script on the benchmark
- do not widen natural-language coverage before the reply-composition layer is in place

## 6. Bottom Line

The correct place to continue from here is no longer "find a causal seam."

That seam now exists as a repo-owned Startup Bench tool script.

The next work is:

1. re-run it when needed through the real Spark Swarm path
2. verify it live through Builder and Telegram
3. improve reply quality so the operator can understand what changed and what to do next
