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
spark-intelligence auth connect openai --api-key <key> --model <model>
spark-intelligence channel add telegram --bot-token <token> --allowed-user <id>
spark-intelligence doctor
spark-intelligence agent inspect
spark-intelligence pairings list
spark-intelligence sessions list
spark-intelligence gateway start
```

Telegram runtime verification is available in two forms:

```bash
spark-intelligence gateway simulate-telegram-update ./sample-update.json
spark-intelligence gateway start --once --poll-timeout-seconds 0
```
