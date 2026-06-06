from __future__ import annotations

from typing import Any

from spark_intelligence import harness_contract as _harness_contract

if not _harness_contract.HARNESS_CORE_AVAILABLE:  # pragma: no cover - depends on local Spark install state.
    raise RuntimeError(f"Spark Harness Core unavailable: {_harness_contract.HARNESS_CORE_IMPORT_ERROR}")

from spark_harness_core import HarnessKernel, evidence_ref


OWNER_REPO = "spark-intelligence-builder"
_KERNEL = HarnessKernel(surface="builder", actor_id_ref="system:spark-intelligence-builder")


def _evidence(plane_id: str, summary: str, *, kind: str = "policy", confidence: float = 0.95) -> dict[str, Any]:
    _ = plane_id
    return evidence_ref(
        kind=kind,
        source=OWNER_REPO,
        summary=summary,
        confidence=confidence,
    )


def _evidence_only_plane(*, plane_id: str, surface: str, plane_type: str, source_path: str, summary: str) -> dict[str, Any]:
    return _KERNEL.legacy_authority_plane(
        plane_id=f"legacy-plane:{plane_id}",
        owner_repo=OWNER_REPO,
        surface=surface,
        plane_type=plane_type,
        source_path=source_path,
        summary=summary,
        authority_risk={},
        disposition="evidence_adapter",
        evidence=[_evidence(plane_id, summary, kind="route_candidate")],
        evidence_only=True,
    )


def _quarantined_plane(*, plane_id: str, surface: str, plane_type: str, source_path: str, summary: str) -> dict[str, Any]:
    return _KERNEL.legacy_authority_plane(
        plane_id=f"legacy-plane:{plane_id}",
        owner_repo=OWNER_REPO,
        surface=surface,
        plane_type=plane_type,
        source_path=source_path,
        summary=summary,
        authority_risk={},
        disposition="quarantined",
        evidence=[_evidence(plane_id, summary, confidence=0.98)],
    )


def _consumer_plane(
    *,
    plane_id: str,
    surface: str,
    plane_type: str,
    source_path: str,
    summary: str,
    authority_risk: dict[str, bool],
) -> dict[str, Any]:
    return _KERNEL.legacy_authority_plane(
        plane_id=f"legacy-plane:{plane_id}",
        owner_repo=OWNER_REPO,
        surface=surface,
        plane_type=plane_type,
        source_path=source_path,
        summary=summary,
        authority_risk=authority_risk,
        disposition="canonical_consumer",
        evidence=[_evidence(plane_id, summary)],
        governor_required=True,
        consumer_of_governor=True,
        ledger_required=True,
    )


def build_builder_legacy_authority_planes() -> list[dict[str, Any]]:
    return [
        _evidence_only_plane(
            plane_id="builder-intent-boundary",
            surface="builder",
            plane_type="keyword_detector",
            source_path="src/spark_intelligence/intent_boundary.py",
            summary="Builder no-action/meta-language helpers block or annotate candidate routes; they cannot authorize work.",
        ),
        _evidence_only_plane(
            plane_id="builder-schedule-list-detector",
            surface="telegram",
            plane_type="regex_router",
            source_path="src/spark_intelligence/schedule_bridge/service.py",
            summary="Schedule read/list detection is route evidence only; runtime reads still require bridge authority.",
        ),
        _evidence_only_plane(
            plane_id="builder-schedule-create-suggestion",
            surface="telegram",
            plane_type="regex_router",
            source_path="src/spark_intelligence/schedule_create_bridge/service.py",
            summary="Natural schedule-create detection renders a suggestion and does not create schedules by itself.",
        ),
        _evidence_only_plane(
            plane_id="builder-chip-create-suggestion",
            surface="domain_chip",
            plane_type="keyword_detector",
            source_path="src/spark_intelligence/chip_create_bridge/service.py",
            summary="Chip-create language detection is suggestion evidence until a governed chip action is authorized.",
        ),
        _evidence_only_plane(
            plane_id="builder-mission-loop-detectors",
            surface="builder",
            plane_type="regex_router",
            source_path="src/spark_intelligence/mission_bridge/service.py,src/spark_intelligence/loop_bridge/service.py",
            summary="Mission and loop detectors can classify discussion, but cannot launch or resume work without authority.",
        ),
        _evidence_only_plane(
            plane_id="builder-draft-disambiguation-detectors",
            surface="builder",
            plane_type="pending_state_helper",
            source_path="src/spark_intelligence/bot_drafts/service.py,src/spark_intelligence/disambiguation_bridge/service.py",
            summary="Draft and disambiguation helpers shape replies and pending choices; they do not own mutation authority.",
        ),
        _evidence_only_plane(
            plane_id="builder-browser-search-boundary",
            surface="browser",
            plane_type="regex_router",
            source_path="src/spark_intelligence/researcher_bridge/advisory.py",
            summary="Browser-search language checks are evidence for grounded research and cannot run hooks without bridge authority.",
        ),
        _evidence_only_plane(
            plane_id="builder-harness-recipe-keywords",
            surface="builder",
            plane_type="keyword_detector",
            source_path="src/spark_intelligence/harness_registry/service.py",
            summary="Harness recipe keyword matching may suggest a chain only after task-specific route readiness is true.",
        ),
        _consumer_plane(
            plane_id="builder-legacy-v1-governed-migration-adapter",
            surface="builder",
            plane_type="local_dispatcher",
            source_path="src/spark_intelligence/bridge_authority.py",
            summary="Legacy TurnIntent V1 can enter only through the governed migration adapter that emits VNext, authorization, Governor, and ledger artifacts.",
            authority_risk={
                "can_execute": True,
                "can_mutate_state": True,
                "can_route_turns": True,
                "can_write_memory": True,
                "can_call_network": True,
                "can_schedule": True,
            },
        ),
        _quarantined_plane(
            plane_id="builder-swarm-keyword-escalation-without-readiness",
            surface="recursive_swarm",
            plane_type="keyword_detector",
            source_path="src/spark_intelligence/swarm_bridge/sync.py",
            summary="Swarm words cannot set escalation true unless payload readiness is present.",
        ),
        _consumer_plane(
            plane_id="builder-bridge-authority-vnext",
            surface="builder",
            plane_type="local_dispatcher",
            source_path="src/spark_intelligence/bridge_authority.py",
            summary="Builder bridge actions consume native VNext envelopes, authorization decisions, Governor decisions, and tool ledgers.",
            authority_risk={
                "can_execute": True,
                "can_mutate_state": True,
                "can_route_turns": True,
                "can_write_memory": True,
                "can_call_network": True,
                "can_schedule": True,
            },
        ),
        _consumer_plane(
            plane_id="builder-telegram-runtime-bridge",
            surface="telegram",
            plane_type="local_dispatcher",
            source_path="src/spark_intelligence/adapters/telegram/runtime.py",
            summary="Telegram runtime Builder bridges call authorize_builder_bridge_action before memory, schedule, chip, voice, swarm, or browser work.",
            authority_risk={
                "can_execute": True,
                "can_mutate_state": True,
                "can_write_memory": True,
                "can_launch_mission": True,
                "can_call_network": True,
                "can_schedule": True,
            },
        ),
        _consumer_plane(
            plane_id="builder-chip-hook-execution-boundary",
            surface="domain_chip",
            plane_type="tool_launcher",
            source_path="src/spark_intelligence/attachments/hooks.py,src/spark_intelligence/cli.py,src/spark_intelligence/researcher_bridge/advisory.py",
            summary="All chip hook subprocess execution consumes explicit Governor decisions at the shared hook boundary before payload files or commands run.",
            authority_risk={
                "can_execute": True,
                "can_mutate_state": True,
                "can_write_memory": True,
                "can_call_network": True,
            },
        ),
        _consumer_plane(
            plane_id="builder-researcher-memory-authority",
            surface="memory",
            plane_type="tool_launcher",
            source_path="src/spark_intelligence/researcher_bridge/advisory.py",
            summary="Researcher memory writes require Governor decisions; bare VNext proposals are insufficient for writes.",
            authority_risk={
                "can_execute": True,
                "can_mutate_state": True,
                "can_write_memory": True,
            },
        ),
        _consumer_plane(
            plane_id="builder-schedule-delete-runtime",
            surface="telegram",
            plane_type="schedule_trigger",
            source_path="src/spark_intelligence/adapters/telegram/runtime.py,src/spark_intelligence/schedule_bridge/service.py",
            summary="Schedule delete intent arms confirmation and executes only after native VNext bridge authority.",
            authority_risk={
                "can_execute": True,
                "can_mutate_state": True,
                "can_schedule": True,
            },
        ),
        _consumer_plane(
            plane_id="builder-browser-chip-hooks",
            surface="browser",
            plane_type="tool_launcher",
            source_path="src/spark_intelligence/researcher_bridge/advisory.py",
            summary="Browser chip hooks are tool consumers guarded by VNext/Governor authority.",
            authority_risk={
                "can_execute": True,
                "can_call_network": True,
            },
        ),
        _consumer_plane(
            plane_id="builder-swarm-bridge-actions",
            surface="recursive_swarm",
            plane_type="tool_launcher",
            source_path="src/spark_intelligence/adapters/telegram/runtime.py,src/spark_intelligence/swarm_bridge/sync.py",
            summary="Swarm bridge actions consume authority and readiness evidence before rerun, escalation, or delivery work.",
            authority_risk={
                "can_execute": True,
                "can_mutate_state": True,
                "can_launch_mission": True,
                "can_call_network": True,
            },
        ),
    ]


def build_builder_legacy_authority_inventory() -> dict[str, Any]:
    return _KERNEL.legacy_authority_inventory(
        inventory_id="builder-legacy-authority-inventory",
        owner_repo=OWNER_REPO,
        surfaces=["builder", "telegram", "memory", "browser", "domain_chip", "recursive_swarm"],
        planes=build_builder_legacy_authority_planes(),
    )
