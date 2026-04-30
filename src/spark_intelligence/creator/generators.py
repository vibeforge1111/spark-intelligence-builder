from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from spark_intelligence.creator.contracts import ArtifactManifest, ValidationIssue, validate_artifact_manifest
from spark_intelligence.creator.intent import CreatorIntentPacket


@dataclass(frozen=True)
class CreatorArtifactBundle:
    intent_packet: CreatorIntentPacket
    artifact_manifests: list[ArtifactManifest]
    validation_issues: list[ValidationIssue]

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent_packet": self.intent_packet.to_dict(),
            "artifact_manifests": [manifest.to_dict() for manifest in self.artifact_manifests],
            "validation_issues": [issue.to_dict() for issue in self.validation_issues],
        }

    def to_text(self) -> str:
        lines = [
            "Creator artifact manifests",
            f"Intent: {self.intent_packet.intent_id}",
            f"Domain: {self.intent_packet.target_domain}",
        ]
        for manifest in self.artifact_manifests:
            lines.extend(
                [
                    "",
                    f"- {manifest.artifact_type}: {manifest.artifact_id}",
                    f"  repo: {manifest.repo}",
                    "  outputs: " + ", ".join(manifest.outputs),
                    "  validation: " + " && ".join(manifest.validation_commands),
                    "  gates: " + ", ".join(manifest.promotion_gates),
                ]
            )
        if self.validation_issues:
            lines.append("")
            lines.append("Validation issues:")
            lines.extend(f"- {issue.to_text()}" for issue in self.validation_issues)
        return "\n".join(lines)


def build_creator_artifact_bundle(packet: CreatorIntentPacket) -> CreatorArtifactBundle:
    manifests = build_artifact_manifests(packet)
    issues: list[ValidationIssue] = []
    for index, manifest in enumerate(manifests):
        for issue in validate_artifact_manifest(manifest):
            issues.append(ValidationIssue(path=f"artifact_manifests[{index}].{issue.path}", message=issue.message, severity=issue.severity))
    return CreatorArtifactBundle(intent_packet=packet, artifact_manifests=manifests, validation_issues=issues)


def build_artifact_manifests(packet: CreatorIntentPacket) -> list[ArtifactManifest]:
    domain = packet.target_domain
    targets = packet.artifact_targets or _targets_from_desired_outputs(packet)
    manifests: list[ArtifactManifest] = []

    if "domain_chip" in targets:
        manifests.append(_domain_chip_manifest(packet, domain))
    if "benchmark_pack" in targets:
        manifests.append(_benchmark_pack_manifest(packet, domain))
    if "specialization_path" in targets:
        manifests.append(_specialization_path_manifest(packet, domain))
    if "autoloop_policy" in targets:
        manifests.append(_autoloop_policy_manifest(packet, domain))
    if "tool_integration" in targets:
        manifests.extend(_tool_integration_manifests(packet, domain))
    if "swarm_publish_packet" in targets:
        manifests.append(_swarm_publish_manifest(packet, domain))

    manifests.append(_creator_report_manifest(packet, domain, [manifest.artifact_id for manifest in manifests]))
    return manifests


def _targets_from_desired_outputs(packet: CreatorIntentPacket) -> list[str]:
    outputs = packet.desired_outputs
    targets: list[str] = []
    for key in ("domain_chip", "benchmark_pack", "specialization_path", "autoloop_policy"):
        if outputs.get(key):
            targets.append(key)
    if outputs.get("telegram_flow") or outputs.get("spawner_mission"):
        targets.append("tool_integration")
    if outputs.get("swarm_publish_packet"):
        targets.append("swarm_publish_packet")
    return targets


def _repo_name(domain: str, artifact_type: str) -> str:
    if domain == "startup-yc":
        known = {
            "domain_chip": "domain-chip-startup-yc",
            "benchmark_pack": "startup-bench",
            "specialization_path": "specialization-path-startup-yc",
            "autoloop_policy": "specialization-path-startup-yc",
            "creator_report": "specialization-path-startup-yc",
        }
        if artifact_type in known:
            return known[artifact_type]
    if artifact_type == "domain_chip":
        return f"domain-chip-{domain}"
    if artifact_type == "benchmark_pack":
        return f"{domain}-bench"
    if artifact_type in {"specialization_path", "autoloop_policy", "creator_report"}:
        return f"specialization-path-{domain}"
    return domain


def _artifact_id(domain: str, artifact_type: str, suffix: str = "v1") -> str:
    return f"{domain}-{artifact_type.replace('_', '-')}-{suffix}"


def _domain_chip_manifest(packet: CreatorIntentPacket, domain: str) -> ArtifactManifest:
    repo = _repo_name(domain, "domain_chip")
    return ArtifactManifest(
        artifact_id=_artifact_id(domain, "domain_chip"),
        artifact_type="domain_chip",
        repo=repo,
        inputs=[packet.intent_id],
        outputs=[
            "spark-chip.json",
            "docs/DOMAIN_CHIP.md",
            "docs/ANTI_PATTERNS.md",
            "tests/test_chip_contract.py",
        ],
        validation_commands=[
            "python -m pytest tests",
            "spark-intelligence attachments status --json",
        ],
        promotion_gates=["schema_gate", "memory_hygiene_gate", "rollback_gate"],
        rollback_plan=f"Revert the {repo} artifact commit and remove the chip root from local attachment roots.",
    )


def _benchmark_pack_manifest(packet: CreatorIntentPacket, domain: str) -> ArtifactManifest:
    repo = _repo_name(domain, "benchmark_pack")
    return ArtifactManifest(
        artifact_id=_artifact_id(domain, "benchmark_pack"),
        artifact_type="benchmark_pack",
        repo=repo,
        inputs=[packet.intent_id],
        outputs=[
            f"benchmarks/{domain}.cases.json",
            f"benchmarks/{domain}.scoring.json",
            f"benchmarks/{domain}.traps.json",
            "docs/BENCHMARK_CALIBRATION.md",
        ],
        validation_commands=[
            *_benchmark_validation_commands(domain),
        ],
        promotion_gates=["schema_gate", "benchmark_gate", "risk_gate", "rollback_gate"],
        rollback_plan=f"Revert the {repo} benchmark-pack commit and remove generated cases from promotion ledgers.",
    )


def _benchmark_validation_commands(domain: str) -> list[str]:
    if domain == "startup-yc":
        return [
            'python -m unittest discover -s tests -p "test_*.py"',
            "python -m thestartupbench run-suite examples/dev_scenario_suite.json baseline --baseline-id heuristic_resilient_operator --seeds 1 --max-turns 1 --profile-path examples/official_eval_profile.json --output-dir tmp_creator_smoke",
        ]
    return [
        "python -m pytest tests",
        f"python scripts/run_{domain}_benchmark.py --suite smoke",
    ]


def _specialization_path_manifest(packet: CreatorIntentPacket, domain: str) -> ArtifactManifest:
    repo = _repo_name(domain, "specialization_path")
    inputs = [packet.intent_id]
    if "domain_chip" in packet.artifact_targets:
        inputs.append(_artifact_id(domain, "domain_chip"))
    if "benchmark_pack" in packet.artifact_targets:
        inputs.append(_artifact_id(domain, "benchmark_pack"))
    return ArtifactManifest(
        artifact_id=_artifact_id(domain, "specialization_path"),
        artifact_type="specialization_path",
        repo=repo,
        inputs=inputs,
        outputs=[
            "specialization-path.json",
            "docs/ABSORPTION_PROTOCOL.md",
            "packs/validated-pack.json",
            "scripts/run_absorption_pilot.py",
        ],
        validation_commands=[
            "python -m pytest tests",
            "python scripts/run_absorption_pilot.py --suite smoke",
        ],
        promotion_gates=["schema_gate", "lineage_gate", "transfer_gate", "memory_hygiene_gate", "rollback_gate"],
        rollback_plan=f"Revert the {repo} specialization-path commit and clear candidate absorption reports.",
    )


def _autoloop_policy_manifest(packet: CreatorIntentPacket, domain: str) -> ArtifactManifest:
    repo = _repo_name(domain, "autoloop_policy")
    inputs = [packet.intent_id]
    if "benchmark_pack" in packet.artifact_targets:
        inputs.append(_artifact_id(domain, "benchmark_pack"))
    if "specialization_path" in packet.artifact_targets:
        inputs.append(_artifact_id(domain, "specialization_path"))
    return ArtifactManifest(
        artifact_id=_artifact_id(domain, "autoloop_policy"),
        artifact_type="autoloop_policy",
        repo=repo,
        inputs=inputs,
        outputs=[
            "autoloop/policy.json",
            "autoloop/mutation_surface.json",
            "autoloop/rejected_mutations.jsonl",
            "docs/AUTOLOOP_POLICY.md",
        ],
        validation_commands=[
            "python -m pytest tests",
            "python scripts/run_autoloop.py --dry-run --rounds 1",
        ],
        promotion_gates=[
            "schema_gate",
            "lineage_gate",
            "benchmark_gate",
            "complexity_gate",
            "memory_hygiene_gate",
            "autonomy_gate",
            "rollback_gate",
        ],
        rollback_plan=f"Disable the {repo} autoloop policy and revert the kept mutation ledger commit.",
    )


def _tool_integration_manifests(packet: CreatorIntentPacket, domain: str) -> list[ArtifactManifest]:
    surfaces = set(packet.usage_surfaces)
    manifests: list[ArtifactManifest] = []
    if "telegram" in surfaces:
        manifests.append(
            ArtifactManifest(
                artifact_id=_artifact_id(domain, "tool_integration", "telegram-v1"),
                artifact_type="tool_integration",
                repo="spark-telegram-bot",
                inputs=[packet.intent_id],
                outputs=[
                    "src/creatorCommands.ts",
                    "tests/creatorCommands.test.ts",
                    "docs/CREATOR_COMMANDS.md",
                ],
                validation_commands=[
                    "npx ts-node tests/spawner.test.ts",
                    "npm run build",
                ],
                promotion_gates=["schema_gate", "risk_gate", "rollback_gate"],
                rollback_plan="Revert the Telegram creator-command commit and remove any registered command aliases.",
            )
        )
    if "spawner" in surfaces or packet.desired_outputs.get("spawner_mission"):
        manifests.append(
            ArtifactManifest(
                artifact_id=_artifact_id(domain, "tool_integration", "spawner-v1"),
                artifact_type="tool_integration",
                repo="spawner-ui",
                inputs=[packet.intent_id],
                outputs=[
                    "src/lib/server/creator-mission.ts",
                    "src/routes/api/creator/mission/+server.ts",
                    "docs/CREATOR_MISSION_API.md",
                ],
                validation_commands=[
                    "npm run check",
                    "npm run test:run -- src/lib/server/creator-mission.test.ts",
                ],
                promotion_gates=["schema_gate", "risk_gate", "rollback_gate"],
                rollback_plan="Revert the Spawner creator-mission commit and remove any queued creator mission traces from local state.",
            )
        )
    if "builder" in surfaces:
        manifests.append(
            ArtifactManifest(
                artifact_id=_artifact_id(domain, "tool_integration", "builder-v1"),
                artifact_type="tool_integration",
                repo="spark-intelligence-builder",
                inputs=[packet.intent_id],
                outputs=[
                    "src/spark_intelligence/creator/",
                    "tests/test_creator_intent.py",
                ],
                validation_commands=[
                    "python -m pytest tests/test_creator_intent.py",
                    "python -m compileall -q src/spark_intelligence/creator",
                ],
                promotion_gates=["schema_gate", "memory_hygiene_gate", "rollback_gate"],
                rollback_plan="Revert the Builder creator integration commit and preserve prior creator intent packets for audit.",
            )
        )
    return manifests


def _swarm_publish_manifest(packet: CreatorIntentPacket, domain: str) -> ArtifactManifest:
    return ArtifactManifest(
        artifact_id=_artifact_id(domain, "swarm_publish_packet"),
        artifact_type="swarm_publish_packet",
        repo="spark-swarm",
        inputs=[packet.intent_id, *_dependency_artifact_ids(packet, domain)],
        outputs=[
            f"collective/{domain}/promotion-packet.json",
            f"collective/{domain}/evidence-ledger.jsonl",
            f"docs/{domain.upper().replace('-', '_')}_PROMOTION.md",
        ],
        validation_commands=[
            "npm run test:smoke",
            "npm run typecheck",
        ],
        promotion_gates=["schema_gate", "lineage_gate", "benchmark_gate", "memory_hygiene_gate", "risk_gate", "rollback_gate"],
        rollback_plan="Revert the Swarm promotion packet commit and remove the candidate packet from collective sync payloads.",
    )


def _creator_report_manifest(packet: CreatorIntentPacket, domain: str, manifest_ids: list[str]) -> ArtifactManifest:
    repo = _repo_name(domain, "creator_report")
    return ArtifactManifest(
        artifact_id=_artifact_id(domain, "creator_report"),
        artifact_type="creator_report",
        repo=repo,
        inputs=[packet.intent_id, *manifest_ids],
        outputs=[
            "reports/creator-run-summary.json",
            "reports/creator-run-summary.md",
            "reports/creator-validation-ledger.jsonl",
        ],
        validation_commands=[
            "python -m json.tool reports/creator-run-summary.json",
            "python -m pytest tests",
        ],
        promotion_gates=["schema_gate", "lineage_gate", "rollback_gate"],
        rollback_plan=f"Delete the {repo} creator report artifacts and revert any report pointer updates.",
    )


def _dependency_artifact_ids(packet: CreatorIntentPacket, domain: str) -> list[str]:
    ids: list[str] = []
    for artifact_type in ("domain_chip", "benchmark_pack", "specialization_path", "autoloop_policy"):
        if artifact_type in packet.artifact_targets:
            ids.append(_artifact_id(domain, artifact_type))
    return ids
