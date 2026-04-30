from spark_intelligence.creator import (
    ArtifactManifest,
    CreatorTrace,
    CreatorTraceTask,
    build_artifact_manifests,
    build_creator_artifact_bundle,
    build_creator_intent_packet,
    validate_artifact_manifest,
    validate_creator_intent_packet,
    validate_creator_trace,
)


def test_creator_plan_detects_full_startup_yc_swarm_flow():
    packet = build_creator_intent_packet(
        "Create a Startup YC specialization path with benchmarked autoloop from Telegram and Spark Swarm"
    )

    assert packet.schema_version == "spark-creator-intent.v1"
    assert packet.target_domain == "startup-yc"
    assert packet.privacy_mode == "swarm_shared"
    assert packet.risk_level == "medium"
    assert packet.desired_outputs["domain_chip"] is True
    assert packet.desired_outputs["specialization_path"] is True
    assert packet.desired_outputs["benchmark_pack"] is True
    assert packet.desired_outputs["autoloop_policy"] is True
    assert packet.desired_outputs["telegram_flow"] is True
    assert packet.desired_outputs["swarm_publish_packet"] is True
    assert packet.intent_id.startswith("creator-intent-startup-yc-")
    assert packet.artifact_targets == [
        "domain_chip",
        "benchmark_pack",
        "specialization_path",
        "autoloop_policy",
        "tool_integration",
        "swarm_publish_packet",
    ]
    assert packet.usage_surfaces == ["telegram", "builder", "swarm"]
    assert packet.benchmark_requirements["visible_cases"] == 20
    assert packet.benchmark_requirements["fresh_agent_absorption"] is True
    assert packet.network_contribution_policy == "github_pr_required"
    assert "spark_telegram_bot" in packet.tools_in_scope
    assert "spark_swarm" in packet.tools_in_scope
    assert packet.target_operator_surface == "telegram+builder+swarm"
    assert validate_creator_intent_packet(packet) == []


def test_creator_plan_defaults_domain_chip_to_benchmarked_local_work():
    packet = build_creator_intent_packet("Make Spark good at investor diligence")

    assert packet.target_domain == "spark-good-investor-diligence"
    assert packet.privacy_mode == "local_only"
    assert packet.desired_outputs["domain_chip"] is True
    assert packet.desired_outputs["benchmark_pack"] is True
    assert packet.desired_outputs["specialization_path"] is False
    assert packet.desired_outputs["autoloop_policy"] is False
    assert packet.artifact_targets == ["domain_chip", "benchmark_pack"]
    assert packet.usage_surfaces == ["builder"]
    assert packet.network_contribution_policy == "workspace_only"


def test_creator_plan_honors_explicit_private_mode():
    packet = build_creator_intent_packet(
        "Build a github repo backed benchmark for founder research but keep it private"
    )

    assert packet.privacy_mode == "local_only"
    assert packet.risk_level == "low"
    assert "github" in packet.tools_in_scope


def test_creator_plan_marks_recursive_publish_as_medium_risk():
    packet = build_creator_intent_packet(
        "Create a recursive benchmark and autoloop for Spark Telegram bot and publish learnings to the network"
    )

    assert packet.target_domain == "spark-telegram-bot"
    assert packet.privacy_mode == "swarm_shared"
    assert packet.risk_level == "medium"
    assert packet.desired_outputs["autoloop_policy"] is True
    assert packet.desired_outputs["swarm_publish_packet"] is True


def test_creator_intent_validator_reports_contract_gaps():
    packet = build_creator_intent_packet("Make Spark good at investor diligence").to_dict()
    packet["artifact_targets"] = []
    packet["privacy_mode"] = "public_internet"

    issues = validate_creator_intent_packet(packet)

    assert {issue.path for issue in issues} == {"privacy_mode", "artifact_targets"}


def test_artifact_manifest_validator_accepts_prd_contract_shape():
    manifest = ArtifactManifest(
        artifact_id="startup-yc-specialization-path-v1",
        artifact_type="specialization_path",
        repo="specialization-path-startup-yc",
        inputs=["creator-intent-startup-yc-12345678", "domain-chip-startup-yc"],
        outputs=["docs/", "packs/", "scripts/"],
        validation_commands=["python scripts/run_startup_yc_absorption_pilot.py --suite smoke"],
        promotion_gates=["schema_gate", "lineage_gate", "benchmark_gate", "rollback_gate"],
        rollback_plan="Revert the artifact commit and remove any candidate promotion packet.",
    )

    assert validate_artifact_manifest(manifest) == []


def test_artifact_manifest_validator_requires_validation_and_rollback():
    manifest = ArtifactManifest(
        artifact_id="startup-yc-benchmark-pack-v1",
        artifact_type="benchmark_pack",
        repo="startup-bench",
        outputs=["benchmarks/startup-yc.json"],
        promotion_gates=["unknown_gate"],
    )

    issues = validate_artifact_manifest(manifest)

    assert {issue.path for issue in issues} == {
        "validation_commands",
        "promotion_gates[0]",
        "rollback_plan",
    }


def test_creator_trace_validator_accepts_task_evidence_and_readiness():
    trace = CreatorTrace(
        trace_id="creator-trace-startup-yc-001",
        intent_id="creator-intent-startup-yc-12345678",
        tasks=[
            CreatorTraceTask(
                task_id="benchmark-pack",
                status="passed",
                evidence=["startup-bench smoke passed"],
                risk=["held-out suite still pending"],
            )
        ],
        repo_changes=["docs/creator_system/CREATOR_SYSTEM_PRD_V1.md"],
        benchmarks=["startup-bench smoke"],
        publish_readiness="workspace_validated",
    )

    assert validate_creator_trace(trace) == []


def test_creator_trace_validator_rejects_empty_tasks_and_bad_status():
    trace = CreatorTrace(
        trace_id="creator-trace-startup-yc-001",
        intent_id="creator-intent-startup-yc-12345678",
        tasks=[CreatorTraceTask(task_id="benchmark-pack", status="done")],
        publish_readiness="network_now",
    )

    issues = validate_creator_trace(trace)

    assert {issue.path for issue in issues} == {"publish_readiness", "tasks[0].status"}


def test_creator_artifact_bundle_generates_valid_startup_yc_manifests():
    packet = build_creator_intent_packet(
        "Create a Startup YC specialization path with benchmarked autoloop from Telegram, Spawner, and Spark Swarm"
    )

    bundle = build_creator_artifact_bundle(packet)

    assert bundle.validation_issues == []
    by_id = {manifest.artifact_id: manifest for manifest in bundle.artifact_manifests}
    assert by_id["startup-yc-domain-chip-v1"].repo == "domain-chip-startup-yc"
    assert by_id["startup-yc-benchmark-pack-v1"].repo == "startup-bench"
    assert by_id["startup-yc-specialization-path-v1"].repo == "specialization-path-startup-yc"
    assert by_id["startup-yc-autoloop-policy-v1"].repo == "specialization-path-startup-yc"
    assert by_id["startup-yc-tool-integration-telegram-v1"].repo == "spark-telegram-bot"
    assert by_id["startup-yc-tool-integration-spawner-v1"].repo == "spawner-ui"
    assert by_id["startup-yc-tool-integration-builder-v1"].repo == "spark-intelligence-builder"
    assert by_id["startup-yc-swarm-publish-packet-v1"].repo == "spark-swarm"
    assert by_id["startup-yc-creator-report-v1"].repo == "specialization-path-startup-yc"
    assert "benchmark_gate" in by_id["startup-yc-autoloop-policy-v1"].promotion_gates


def test_creator_artifact_manifests_default_to_local_chip_and_bench():
    packet = build_creator_intent_packet("Make Spark good at investor diligence")

    manifests = build_artifact_manifests(packet)

    assert [manifest.artifact_type for manifest in manifests] == [
        "domain_chip",
        "benchmark_pack",
        "creator_report",
    ]
    assert all(validate_artifact_manifest(manifest) == [] for manifest in manifests)
    assert manifests[0].repo == "domain-chip-spark-good-investor-diligence"
    assert manifests[1].repo == "spark-good-investor-diligence-bench"
