# Spark Intelligence Builder

Spark Intelligence Builder is the product repo for `Spark Intelligence`: a Spark-native, persistent 1:1 agent system.

It is intentionally not the repo that should absorb the whole Spark ecosystem.

The core idea is simple:

- `Spark Researcher` is the runtime core.
- `Spark Swarm` handles delegation and multi-agent coordination.
- `Domain chips` provide specialization.
- `Specialization paths` shape long-term growth.
- `Autoloop flywheels` improve the agent from repeated use.
- `Telegram`, `WhatsApp`, and `Discord` act as delivery adapters.

This repo should stay focused on:

- runtime shell
- gateway and adapters
- identity, pairing, and operator control
- contracts and bridges into other Spark systems

It should avoid becoming a giant everything-repo by copying the internals of:

- `Spark Researcher`
- `Spark Swarm`
- `domain chip` repos
- `specialization path` repos

This repo currently includes:

- [docs/PRD_SPARK_INTELLIGENCE_V1.md](./docs/PRD_SPARK_INTELLIGENCE_V1.md)
- [docs/ARCHITECTURE_SPARK_INTELLIGENCE_V1.md](./docs/ARCHITECTURE_SPARK_INTELLIGENCE_V1.md)
- [docs/SPARK_INTELLIGENCE_PROMPT_BIBLE.md](./docs/SPARK_INTELLIGENCE_PROMPT_BIBLE.md)
- [docs/CRON_JOB_HARNESS_SPEC_V1.md](./docs/CRON_JOB_HARNESS_SPEC_V1.md)
- [docs/IMPORT_AND_MIGRATION_SPEC_V1.md](./docs/IMPORT_AND_MIGRATION_SPEC_V1.md)
- [docs/CODING_RULESET_V1.md](./docs/CODING_RULESET_V1.md)
- [docs/SKILL_VALIDATION_GUIDE.md](./docs/SKILL_VALIDATION_GUIDE.md)
- [docs/ONBOARDING_CLI_SPEC_V1.md](./docs/ONBOARDING_CLI_SPEC_V1.md)
- [docs/IDENTITY_AND_SESSION_MODEL_SPEC_V1.md](./docs/IDENTITY_AND_SESSION_MODEL_SPEC_V1.md)
- [docs/CONFIG_AND_STATE_SCHEMA_SPEC_V1.md](./docs/CONFIG_AND_STATE_SCHEMA_SPEC_V1.md)
- [docs/OPERATOR_CONTROL_SURFACE_SPEC_V1.md](./docs/OPERATOR_CONTROL_SURFACE_SPEC_V1.md)
- [docs/PROVIDER_AND_AUTH_CONFIG_SPEC_V1.md](./docs/PROVIDER_AND_AUTH_CONFIG_SPEC_V1.md)
- [docs/SECURITY_RESEARCH_PLAN_HERMES_OPENCLAW.md](./docs/SECURITY_RESEARCH_PLAN_HERMES_OPENCLAW.md)
- [docs/SECURITY_DOCTRINE_V1.md](./docs/SECURITY_DOCTRINE_V1.md)
- [docs/OPENCLAW_HERMES_SECURITY_HISTORY_ANALYSIS_2026-03-25.md](./docs/OPENCLAW_HERMES_SECURITY_HISTORY_ANALYSIS_2026-03-25.md)
- [docs/OPENCLAW_HERMES_DEEP_COMPARATIVE_ANALYSIS_2026-03-25.md](./docs/OPENCLAW_HERMES_DEEP_COMPARATIVE_ANALYSIS_2026-03-25.md)
- [docs/SECURITY_HISTORY_THEME_APPENDIX_2026-03-25.md](./docs/SECURITY_HISTORY_THEME_APPENDIX_2026-03-25.md)
- [docs/TELEGRAM_ADAPTER_SPEC_V1.md](./docs/TELEGRAM_ADAPTER_SPEC_V1.md)
- [docs/SPARK_RESEARCHER_INTEGRATION_CONTRACT_V1.md](./docs/SPARK_RESEARCHER_INTEGRATION_CONTRACT_V1.md)
- [docs/SPARK_SWARM_ESCALATION_CONTRACT_V1.md](./docs/SPARK_SWARM_ESCALATION_CONTRACT_V1.md)
- [docs/DOMAIN_CHIP_ATTACHMENT_CONTRACT_V1.md](./docs/DOMAIN_CHIP_ATTACHMENT_CONTRACT_V1.md)
- [docs/IMPLEMENTATION_READINESS_AUDIT_2026-03-25.md](./docs/IMPLEMENTATION_READINESS_AUDIT_2026-03-25.md)
- [docs/IMPLEMENTATION_PLAN_V1.md](./docs/IMPLEMENTATION_PLAN_V1.md)

Key repo skills:

- [skills/reliable-job-harnesses/SKILL.md](./skills/reliable-job-harnesses/SKILL.md)
- [skills/maintainable-engineering/SKILL.md](./skills/maintainable-engineering/SKILL.md)
- [skills/spark-ecosystem-product/SKILL.md](./skills/spark-ecosystem-product/SKILL.md)
- [skills/agent-landscape-analysis/SKILL.md](./skills/agent-landscape-analysis/SKILL.md)
- [skills/security-systems/SKILL.md](./skills/security-systems/SKILL.md)
- [skills/security-auditor/SKILL.md](./skills/security-auditor/SKILL.md)

Validation support:

- `python scripts/validate_skills.py`
- [scenario-packs/reliable-job-harnesses/README.md](./scenario-packs/reliable-job-harnesses/README.md)

Current Phase 0 runtime shell:

```bash
pip install -e .
spark-intelligence setup
spark-intelligence status
spark-intelligence operator set-bridge researcher disabled
spark-intelligence operator review-pairings
spark-intelligence auth connect openai --api-key <key> --model <model>
spark-intelligence channel telegram-onboard
spark-intelligence channel add discord --bot-token <token> --allowed-user <id>
spark-intelligence channel add whatsapp --bot-token <token> --allowed-user <id>
spark-intelligence config set spark.researcher.runtime_root "C:/Users/USER/Desktop/spark-researcher"
spark-intelligence swarm configure --api-url https://your-swarm-host --workspace-id <workspace_id> --access-token <token>
spark-intelligence doctor
spark-intelligence agent inspect
spark-intelligence pairings list
spark-intelligence sessions list
spark-intelligence gateway start
```

`setup` now auto-detects local `spark-researcher`, `spark-swarm`, domain-chip, and specialization-path repos on the Desktop when they are present. It can also wire hosted Swarm access in one command:

```bash
spark-intelligence setup \
  --swarm-api-url https://your-swarm-host \
  --swarm-workspace-id <workspace_id> \
  --swarm-access-token <token>
```

Telegram setup is BotFather-first and DM-first. The guided path is:

```bash
spark-intelligence channel telegram-onboard
spark-intelligence channel telegram-onboard --bot-token <token> --allowed-user <telegram_user_id>
spark-intelligence channel add telegram --bot-token <token> --allowed-user <telegram_user_id>
spark-intelligence channel test telegram
```

`channel telegram-onboard` prints the BotFather steps when no token is provided, and validates the token before storing it when a token is provided. `channel add telegram` stays scriptable, but now validates the token by default unless `--skip-validate` is explicitly used.
`channel test telegram` rechecks the stored token, refreshes Telegram auth health, and shows the configured bot identity and pairing posture without starting the gateway.

Telegram runtime verification is available in two forms:

```bash
spark-intelligence gateway simulate-telegram-update ./sample-update.json
spark-intelligence gateway simulate-discord-message ./sample-discord-message.json
spark-intelligence gateway simulate-whatsapp-message ./sample-whatsapp-message.json
spark-intelligence gateway start --once --poll-timeout-seconds 0
spark-intelligence gateway start --continuous
spark-intelligence gateway traces --limit 20
spark-intelligence gateway outbound --limit 20
```

Config can be inspected and updated without editing `config.yaml` manually:

```bash
spark-intelligence config show
spark-intelligence config show --path spark.researcher --json
spark-intelligence config set spark.researcher.config_path "C:/Users/USER/Desktop/spark-researcher/spark-researcher.project.json"
spark-intelligence config unset spark.researcher.config_path
```

Spark Swarm stays manual-first in v1. The builder can export the latest real collective payload from `spark-researcher` and sync it without absorbing Swarm internals:

```bash
spark-intelligence swarm status
spark-intelligence swarm configure --api-url https://your-swarm-host --workspace-id <workspace_id> --access-token <token>
spark-intelligence swarm sync --dry-run
spark-intelligence swarm sync
spark-intelligence swarm evaluate "Break this into a multi-step parallel research workflow"
```

Chip and specialization-path attachments stay external as well. Spark Intelligence scans configured roots first, then falls back to Desktop auto-discovery for repos such as `domain-chip-*` and `specialization-path-*`:

```bash
spark-intelligence attachments status
spark-intelligence attachments list --kind chip
spark-intelligence attachments list --kind path --json
spark-intelligence attachments add-root chips "C:/Users/USER/Desktop/domain-chip-content"
spark-intelligence attachments add-root paths "C:/Users/USER/Desktop/specialization-path-startup-operator"
spark-intelligence attachments activate-chip content
spark-intelligence attachments pin-chip startup-yc
spark-intelligence attachments set-path startup-operator
spark-intelligence attachments snapshot --json
spark-intelligence agent inspect
```

The attachment snapshot is written to `SPARK_INTELLIGENCE_HOME/attachments.snapshot.json` and mirrored into SQLite runtime state so external Spark repos can consume the current attachment set without importing this repo's internals.

The Spark Researcher bridge now also includes a compact attachment context envelope derived from that snapshot, so advisory requests can stay aware of active chips and the active specialization path without this repo importing chip logic.

```bash
spark-intelligence researcher status
spark-intelligence researcher status --json
```

```bash
spark-intelligence operator set-bridge researcher enabled
spark-intelligence operator set-bridge swarm disabled
spark-intelligence operator set-channel telegram paused
spark-intelligence operator review-pairings
spark-intelligence operator hold-pairing telegram 123456
spark-intelligence operator approve-pairing telegram 123456
spark-intelligence operator approve-latest telegram
spark-intelligence operator hold-latest telegram
spark-intelligence operator inbox
spark-intelligence operator security
spark-intelligence operator history
```

`operator inbox` now emits direct recommended commands for each actionable item so the operator surface stays lightweight and local-first without a separate ticketing subsystem.
`operator security` also reads durable bridge failure counters and last-failure metadata from local state, not just recent logs.
Telegram pending pairings now also carry lightweight local context, so `operator review-pairings` can show the most recent Telegram username, chat id, and last inbound message preview.
After approval, the first successful Telegram reply also carries a one-time “pairing approved” welcome so the user gets a cleaner handoff into the active agent.

Telegram ingress now also applies lightweight runtime guardrails:
- duplicate update suppression
- per-user rate limiting
- outbound reply truncation and secret-like reply blocking

Those guardrails now live in shared gateway helpers so future adapters can inherit the same local-first safety behavior instead of re-implementing it.

Telegram runtime health is also persisted locally now:
- last auth check state
- last poll failure type/message
- consecutive poll failures with bounded backoff
- cleaner `/start` pairing response for first-contact users

That health flows into `gateway status`, `operator inbox`, and `operator security` so Telegram problems stay visible without another background subsystem.
