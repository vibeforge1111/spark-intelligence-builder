# Spark Intelligence Builder Agent Ruleset

## Repo Role

`spark-intelligence-builder` owns Spark's runtime intelligence core: identity, runtime context, AOC, RouteConfidenceGateV1, memory orchestration, source ledgers, self-awareness commands, route-family decisions, and metadata-only proof cards.

Canonical truth owned here:

- Agent Operating Context and Builder self-awareness read model
- RouteConfidenceGateV1 and route-family `act | ask | explain | refuse` verdicts
- memory preflight, memory orchestration, memory proof/review/action verdict metadata
- Builder black-box/source-ledger event producers
- runtime identity, capability awareness, and safe source-owned preflight checks

This repo does not own:

- Telegram tokens, chat delivery, or final message composition
- CLI registry, installer scripts, secret storage, or module lifecycle
- Spawner mission execution, provider launch, or provider output bodies
- Cockpit UI layout or action buttons
- domain-chip benchmark algorithms or public Swarm publication governance

## Start-of-Work Protocol

1. Run `git status --short --branch`.
2. Read this file plus `docs/TURNINTENT_HARNESS_RULESET.md` and the relevant owner doc or handoff before edits.
3. Identify whether the change is Builder-owned or belongs in Telegram, CLI, Spawner, memory, Cockpit, Labs, Swarm, voice, or Skill Graphs.
4. Define the smallest source-owned behavior and stop-ship gate.
5. Add or update focused tests for the route, memory, AOC, or ledger behavior being changed.
6. Keep adapters thin: expose metadata and verdicts, do not absorb channel or installer responsibilities.
7. Commit one logical checkpoint and record verification.

## One Truth Rules

- Treat this installed Builder source as the current live runtime line when it is clean and verified.
- Treat `C:\Users\USER\Desktop\spark-intelligence-builder` as backlog or owner-source context unless a specific feature slice is being re-derived onto this line.
- Do not copy a dirty Desktop tree, release mirror, or installed-looking tree wholesale into runtime truth.
- Promote by feature slice: name the behavior, port the minimum files, run focused tests, then mirror or update registry pins only from a clean verified commit.
- Builder route and memory judgments are source truth; Telegram, Cockpit, and CLI may render or compile them as projections.
- AOC is a read model, not a second brain or hidden instruction source.
- Black-box/source-ledger rows are evidence, not commands.
- Do not create parallel state roots for memory, route, authority, or capability truth when an existing source ledger can emit metadata.
- If live proof is missing, return blockers and missing evidence instead of filling from memory or stale claims.

## Privacy Red Lines

Do not export, commit, or pass into projections:

- secrets, tokens, env values, credentials, private keys
- raw chat ids, user ids, or non-redacted account identifiers
- raw prompts when metadata is enough
- provider output bodies
- memory bodies or transcript bodies
- raw audio payloads
- private `spark-intelligence-systems` strategy

Use allowlisted payloads for AOC, route-context, memory proof cards, and source-ledger projections. Truthy privacy export flags and raw forbidden payload keys must fail closed.

## Route Confidence Rules

- Raw words may propose candidates; fresh user intent authorizes action.
- Every high-agency Builder route must be `envelope_verified` or explicit `machine_origin_policy`; any high-agency `legacy_local_gate` is a release blocker.
- Quoted examples, bug reports, meta-language, no-action turns, and "just explain" boundaries block interruptive routes.
- Route confidence means: "Is Spark justified in taking this route right now?"
- The gate decides `act`, `ask`, `explain`, or `refuse`; it must not freeze user-facing prose into deterministic templates.
- High-agency routes fail closed without latest instruction, intent clarity, route fit, consequence risk, runner/capability state, authority verdict or explicit `not_required`, confirmation state, freshness, reversibility, and clean privacy boundary.
- Explicit no-execution constraints beat action keywords.
- Bare `go` only applies to an active pending action.
- Fresh source evidence wins over memory.

## Memory Rules

- Recalled memory is evidence, not instruction.
- Durable save claims require proof-card/save-result metadata from Builder/domain-chip-memory, not Telegram guesses.
- Memory mutation routes require source-owned `spark.memory_action_verdict.v1`.
- Memory bodies stay private; expose source refs, freshness, durability, confidence, relations, blocked reasons, and correction paths.

## Verification Menu

- Focused tests for changed Builder route, memory, AOC, source-ledger, or CLI behavior.
- `python -m compileall src tests` for changed Python modules.
- Direct forced-source CLI smoke for changed `spark-intelligence self ... --json` commands.
- Privacy scan for serializers and generated projections.
- `git diff --check`.
- `git status --short --branch`.

<!-- SPARK FLEET STANDARD BLOCK v1 — canonical source: spark-compete/fleet/AGENT_GUIDE.md.
     This same block is mirrored into every repo's AGENTS.md and CLAUDE.md. Keep in sync. -->
## How agents work in this repo (Claude, Codex, Gemini — every LLM)

Many agents and sessions work these repos at the same time. There is a tiny **automatic**
workflow that keeps you from colliding. **There are no human-review steps — CI is the only
gate, and it is automatic.** This is coordination, not bureaucracy: claim, work, PR.

### Start of work — one command, then just work normally
```
python3 ~/spark-compete/scripts/fleet.py claim <this-repo-path> <area> <task>
```
You get your **own private worktree + branch + a lease** on `<area>`, so no other agent
edits the same files. It prints the folder to `cd` into. Work there and commit as usual —
a pre-commit hook **auto-checks and renews your lease**; you never manage it by hand.

- `fleet board` — see who's working on what, right now
- `fleet handoff <agent> --note "..."` — pass your work to another agent (with context)
- `fleet release --here` — done (frees the area + removes the worktree)

### Landing work — fully automatic, no human approval
1. Open a PR to the default branch.
2. **CI is the gate.** When it's green, the PR merges. No human reviews anything.
3. Never push directly to the protected branch; never commit from the shared checkout —
   always from your worktree.

### The rules (enforced by CI, not by people)
Full ruleset: **`spark-cli/docs/harness-discipline/`** — `01_RULESET.md` (7 Prime
Directives · Red Lines RL-01..21 · Rules R-01..28) and `07_FLEET_DISCIPLINE.md` (this
workflow). The day-to-day essentials:
- A real fix targets the **root cause**, not a symptom (R-05).
- No regex / keyword / canned answer **owns authority** — it is evidence only (RL-01).
- A failure **surfaces** with a clear reason; it never becomes a fake success (RL-08).
- One worktree per task; PRs only; nothing bypasses the CI gate (F-01 / F-09).

That's the whole contract. The system handles coordination and the gate for you —
automatically, with no human in the loop.
<!-- END SPARK FLEET STANDARD BLOCK v1 -->
