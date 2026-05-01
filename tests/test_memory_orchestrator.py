from __future__ import annotations

import json
import sys
from types import SimpleNamespace
from unittest.mock import patch

from spark_intelligence.doctor.checks import run_doctor
from spark_intelligence.memory import orchestrator as memory_orchestrator
from spark_intelligence.memory import (
    archive_belief_from_memory,
    archive_raw_episode_from_memory,
    archive_structured_evidence_from_memory,
    build_sdk_maintenance_payload,
    build_shadow_replay_payload,
    export_shadow_replay_batch,
    hybrid_memory_retrieve,
    inspect_human_memory_in_memory,
    lookup_current_state_in_memory,
    lookup_historical_state_in_memory,
    read_memory_kernel,
    retrieve_memory_events_in_memory,
    run_memory_sdk_maintenance,
    run_memory_sdk_smoke_test,
    write_belief_to_memory,
    write_profile_fact_to_memory,
    write_raw_episode_to_memory,
    write_structured_evidence_to_memory,
    write_telegram_event_to_memory,
)
from spark_intelligence.memory.episodic_events import (
    build_telegram_memory_event_observation_answer,
    build_telegram_memory_event_query_answer,
    detect_telegram_memory_event_observation,
    detect_telegram_memory_event_query,
    filter_telegram_memory_event_records,
    telegram_event_summary_predicate,
)
from spark_intelligence.memory.generic_observations import (
    build_telegram_generic_observation_answer,
    detect_telegram_generic_observation,
)
from spark_intelligence.memory.profile_facts import (
    build_profile_fact_event_history_answer,
    build_profile_fact_explanation_answer,
    build_profile_fact_history_answer,
    build_profile_fact_observation_answer,
    build_profile_fact_query_answer,
    build_profile_identity_summary_answer,
    build_profile_fact_query_context,
    build_profile_identity_summary_context,
    detect_profile_fact_observation,
    detect_profile_fact_query,
)
from spark_intelligence.observability.store import (
    build_watchtower_snapshot,
    latest_events_by_type,
    record_event,
    recent_memory_lane_records,
    recent_policy_gate_records,
    recent_reset_sensitive_state_registry,
)
from spark_intelligence.personality.loader import (
    detect_and_persist_nl_preferences,
    detect_personality_query,
    load_personality_profile,
)
from spark_intelligence.workflow_recovery import (
    record_bad_self_review_lesson,
    record_pending_task_timeout,
)

from tests.test_support import SparkTestCase


class _FakeMemoryClient:
    def __init__(self) -> None:
        self.observation_calls: list[dict[str, object]] = []
        self.event_calls: list[dict[str, object]] = []
        self.current_state_calls: list[dict[str, object]] = []
        self.evidence_calls: list[dict[str, object]] = []
        self.retrieval_event_calls: list[dict[str, object]] = []

    def write_observation(self, **payload):
        self.observation_calls.append(payload)
        memory_role = (
            "state_deletion"
            if str(payload.get("operation") or "").strip().lower() == "delete"
            else str(payload.get("memory_role") or "current_state")
        )
        return {
            "status": "accepted",
            "memory_role": memory_role,
            "provenance": [{"memory_role": memory_role, "source": "fake_sdk"}],
            "retrieval_trace": {"trace_id": "mem-trace-write"},
        }

    def write_event(self, **payload):
        self.event_calls.append(payload)
        return {
            "status": "accepted",
            "memory_role": "event",
            "provenance": [{"memory_role": "event", "source": "fake_sdk"}],
            "retrieval_trace": {"trace_id": "mem-trace-event-write"},
        }

    def get_current_state(self, **payload):
        self.current_state_calls.append(payload)
        return {
            "status": "supported",
            "memory_role": "current_state",
            "records": [
                {
                    "subject": payload["subject"],
                    "predicate": "personality.preference.directness",
                    "value": 0.35,
                }
            ],
            "provenance": [{"memory_role": "current_state", "source": "fake_sdk"}],
            "retrieval_trace": {"trace_id": "mem-trace-read"},
            "answer_explanation": {"method": "get_current_state"},
        }

    def retrieve_evidence(self, **payload):
        self.evidence_calls.append(payload)
        return {
            "status": "supported",
            "memory_role": "structured_evidence",
            "records": [
                {
                    "subject": payload.get("subject"),
                    "predicate": payload.get("predicate") or "profile.current_focus",
                    "value": "persistent memory quality evaluation",
                    "text": "Current focus is persistent memory quality evaluation.",
                }
            ],
            "provenance": [{"memory_role": "structured_evidence", "source": "fake_sdk"}],
            "retrieval_trace": {"trace_id": "mem-trace-evidence"},
        }

    def retrieve_events(self, **payload):
        self.retrieval_event_calls.append(payload)
        return {
            "status": "supported",
            "memory_role": "event",
            "records": [
                {
                    "subject": payload.get("subject"),
                    "predicate": payload.get("predicate") or "memory.maintenance",
                    "text": "Memory maintenance succeeded.",
                }
            ],
            "provenance": [{"memory_role": "event", "source": "fake_sdk"}],
            "retrieval_trace": {"trace_id": "mem-trace-events"},
        }


class _AbstainingMemoryClient(_FakeMemoryClient):
    def get_current_state(self, **payload):
        self.current_state_calls.append(payload)
        return {
            "status": "abstained",
            "reason": "not_found",
            "memory_role": "current_state",
            "provenance": [],
        }


class _InvalidMemoryRoleClient(_FakeMemoryClient):
    def write_observation(self, **payload):
        self.observation_calls.append(payload)
        return {
            "status": "accepted",
            "memory_role": "timeline",
            "provenance": [{"memory_role": "timeline", "source": "fake_sdk"}],
            "retrieval_trace": {"trace_id": "mem-trace-write-invalid"},
        }

    def get_current_state(self, **payload):
        self.current_state_calls.append(payload)
        return {
            "status": "supported",
            "memory_role": "timeline",
            "records": [
                {
                    "subject": payload["subject"],
                    "predicate": "personality.preference.directness",
                    "value": 0.35,
                }
            ],
            "provenance": [{"memory_role": "timeline", "source": "fake_sdk"}],
            "retrieval_trace": {"trace_id": "mem-trace-read-invalid"},
            "answer_explanation": {"method": "get_current_state"},
        }


class _HybridRetrievalMemoryClient(_FakeMemoryClient):
    def get_current_state(self, **payload):
        self.current_state_calls.append(payload)
        return {
            "status": "supported",
            "memory_role": "current_state",
            "records": [
                {
                    "subject": payload["subject"],
                    "predicate": payload["predicate"],
                    "value": "persistent memory quality evaluation",
                    "metadata": {
                        "source_surface": "current_state_test",
                        "entity_key": payload.get("entity_key"),
                    },
                }
            ],
            "provenance": [{"memory_role": "current_state", "source": "fake_sdk"}],
            "retrieval_trace": {"trace_id": "mem-trace-current"},
        }

    def retrieve_evidence(self, **payload):
        self.evidence_calls.append(payload)
        return {
            "status": "supported",
            "memory_role": "structured_evidence",
            "records": [
                {
                    "subject": payload.get("subject"),
                    "predicate": payload.get("predicate"),
                    "value": "verify scheduled memory cleanup",
                    "text": "Old workflow residue says verify scheduled memory cleanup.",
                    "metadata": {"status": "stale", "source_surface": "workflow_state"},
                }
            ],
            "provenance": [{"memory_role": "structured_evidence", "source": "fake_sdk"}],
            "retrieval_trace": {"trace_id": "mem-trace-evidence"},
        }

    def retrieve_events(self, **payload):
        self.retrieval_event_calls.append(payload)
        return {
            "status": "supported",
            "memory_role": "event",
            "records": [
                {
                    "subject": payload.get("subject"),
                    "predicate": "diagnostics.latest",
                    "text": "Diagnostics were clean.",
                    "metadata": {"source_surface": "diagnostics"},
                }
            ],
            "provenance": [{"memory_role": "event", "source": "fake_sdk"}],
            "retrieval_trace": {"trace_id": "mem-trace-events"},
        }


class _SupportingOnlyHybridRetrievalMemoryClient(_HybridRetrievalMemoryClient):
    def get_current_state(self, **payload):
        self.current_state_calls.append(payload)
        return {
            "status": "abstained",
            "reason": "not_found",
            "memory_role": "current_state",
            "records": [],
            "provenance": [],
            "retrieval_trace": {"trace_id": "mem-trace-current-miss"},
        }

    def retrieve_evidence(self, **payload):
        self.evidence_calls.append(payload)
        return {
            "status": "supported",
            "memory_role": "structured_evidence",
            "records": [
                {
                    "subject": payload.get("subject"),
                    "predicate": payload.get("predicate"),
                    "value": f"supporting memory evaluation note {index}",
                    "text": f"Supporting memory evaluation note {index}.",
                    "metadata": {"source_surface": "workflow_state"},
                }
                for index in range(3)
            ],
            "provenance": [{"memory_role": "structured_evidence", "source": "fake_sdk"}],
            "retrieval_trace": {"trace_id": "mem-trace-evidence"},
        }


class _AuthorityAnchoredDominantEvidenceMemoryClient(_HybridRetrievalMemoryClient):
    def retrieve_events(self, **payload):
        self.retrieval_event_calls.append(payload)
        return {
            "status": "abstained",
            "reason": "not_found",
            "memory_role": "event",
            "records": [],
            "provenance": [],
            "retrieval_trace": {"trace_id": "mem-trace-events-empty"},
        }

    def retrieve_evidence(self, **payload):
        self.evidence_calls.append(payload)
        return {
            "status": "supported",
            "memory_role": "structured_evidence",
            "records": [
                {
                    "subject": payload.get("subject"),
                    "predicate": payload.get("predicate"),
                    "value": f"clean supporting evidence {index}",
                    "text": f"Clean supporting evidence {index}.",
                    "metadata": {"status": "current", "source_surface": "evidence"},
                }
                for index in range(4)
            ],
            "provenance": [{"memory_role": "structured_evidence", "source": "fake_sdk"}],
            "retrieval_trace": {"trace_id": "mem-trace-evidence-dominant"},
        }


class _StaleEvidenceMemoryClient(_FakeMemoryClient):
    def retrieve_evidence(self, **payload):
        self.evidence_calls.append(payload)
        return {
            "status": "supported",
            "memory_role": "structured_evidence",
            "records": [
                {
                    "subject": payload.get("subject"),
                    "predicate": "profile.current_focus",
                    "value": "persistent memory quality evaluation",
                    "text": "Current focus is persistent memory quality evaluation.",
                }
            ],
            "provenance": [
                {"memory_role": "structured_evidence", "source": "fake_sdk", "status": "current"},
                {
                    "memory_role": "structured_evidence",
                    "source": "fake_sdk",
                    "status": "superseded",
                    "value": "context capsule verification",
                },
            ],
            "retrieval_trace": {"trace_id": "mem-trace-evidence"},
        }


class _MaintenanceMemoryClient(_FakeMemoryClient):
    def __init__(self) -> None:
        super().__init__()
        self.maintenance_calls: list[dict[str, object]] = []

    def reconsolidate_manual_memory(self, *, now: str | None = None):
        self.maintenance_calls.append({"now": now})
        return SimpleNamespace(
            manual_observations_before=4,
            manual_observations_after=3,
            current_state_snapshot_count=2,
            active_deletion_count=1,
            manual_events_count=0,
            active_state_still_current_count=1,
            active_state_stale_preserved_count=1,
            active_state_superseded_count=1,
            active_state_archived_count=0,
            audit_samples={
                "deleted": [
                    {
                        "predicate": "profile.current_owner",
                        "value": "delete profile.current_owner for human:test",
                        "action": "deleted",
                        "reason": "current_snapshot",
                        "deletion_observation_id": "obs-delete-owner",
                    }
                ],
                "stale_preserved": [
                    {
                        "predicate": "profile.current_plan",
                        "value": "verify scheduled memory cleanup",
                        "action": "stale_preserved",
                        "reason": "past_revalidate_at",
                        "revalidate_at": "2025-02-01T09:00:00Z",
                        "revalidation_lag_days": 60,
                    }
                ],
                "superseded": [
                    {
                        "predicate": "profile.current_focus",
                        "value": "context capsule verification",
                        "action": "superseded",
                        "reason": "newer_active_state",
                        "replacement_value": "persistent memory quality evaluation",
                        "replacement_observation_id": "obs-new-focus",
                    }
                ],
                "archived": [],
                "still_current": [
                    {
                        "predicate": "profile.current_focus",
                        "value": "persistent memory quality evaluation",
                        "action": "still_current",
                        "reason": "current_snapshot",
                    }
                ],
            },
            trace={
                "operation": "reconsolidate_manual_memory",
                "active_state_maintenance": {
                    "still_current": 1,
                    "stale_preserved": 1,
                    "superseded": 1,
                    "archived": 0,
                },
            },
        )


class MemoryOrchestratorTests(SparkTestCase):
    def test_memory_kernel_current_state_returns_unified_schema(self) -> None:
        fake_client = _FakeMemoryClient()
        with patch("spark_intelligence.memory.orchestrator._load_sdk_client_for_module", return_value=fake_client), patch(
            "spark_intelligence.memory.orchestrator.inspect_memory_sdk_runtime",
            return_value={"ready": True, "client_kind": "fake"},
        ):
            result = read_memory_kernel(
                config_manager=self.config_manager,
                state_db=self.state_db,
                method="get_current_state",
                subject="human:test",
                predicate="profile.current_focus",
                query="What is my current focus?",
                actor_id="test",
            )

        self.assertEqual(result.read_method, "get_current_state")
        self.assertEqual(result.source_class, "current_state")
        self.assertFalse(result.abstained)
        self.assertEqual(result.answer, "0.35")
        self.assertEqual(result.records[0]["subject"], "human:test")
        self.assertEqual(result.provenance[0]["source"], "fake_sdk")
        self.assertEqual(result.ignored_stale_records, [])
        self.assertEqual(result.read_result.retrieval_trace["memory_kernel"]["source_class"], "current_state")
        self.assertEqual(len(fake_client.current_state_calls), 1)

    def test_memory_kernel_passes_entity_key_to_current_state_reads(self) -> None:
        fake_client = _FakeMemoryClient()
        with patch("spark_intelligence.memory.orchestrator._load_sdk_client_for_module", return_value=fake_client), patch(
            "spark_intelligence.memory.orchestrator.inspect_memory_sdk_runtime",
            return_value={"ready": True, "client_kind": "fake"},
        ):
            result = read_memory_kernel(
                config_manager=self.config_manager,
                state_db=self.state_db,
                method="get_current_state",
                subject="human:test",
                predicate="entity.name",
                entity_key="named-object:tiny-desk-plant",
                query="What did I name the plant?",
                actor_id="test",
            )

        self.assertFalse(result.abstained)
        self.assertEqual(fake_client.current_state_calls[0]["entity_key"], "named-object:tiny-desk-plant")
        self.assertEqual(len(fake_client.current_state_calls), 1)

    def test_lookup_current_state_uses_profile_current_entity_key(self) -> None:
        fake_client = _FakeMemoryClient()
        with patch("spark_intelligence.memory.orchestrator._load_sdk_client_for_module", return_value=fake_client):
            result = lookup_current_state_in_memory(
                config_manager=self.config_manager,
                state_db=self.state_db,
                subject="human:test",
                predicate="profile.current_plan",
                actor_id="test",
            )

        self.assertFalse(result.read_result.abstained)
        self.assertEqual(fake_client.current_state_calls[0]["entity_key"], "profile.current_plan")

    def test_memory_kernel_evidence_marks_stale_provenance_without_using_it_as_answer(self) -> None:
        fake_client = _StaleEvidenceMemoryClient()
        with patch("spark_intelligence.memory.orchestrator._load_sdk_client_for_module", return_value=fake_client), patch(
            "spark_intelligence.memory.orchestrator.inspect_memory_sdk_runtime",
            return_value={"ready": True, "client_kind": "fake"},
        ):
            result = read_memory_kernel(
                config_manager=self.config_manager,
                state_db=self.state_db,
                method="retrieve_evidence",
                subject="human:test",
                predicate="profile.current_focus",
                query="What should we evaluate next?",
                actor_id="test",
            )

        self.assertEqual(result.read_method, "retrieve_evidence")
        self.assertEqual(result.source_class, "structured_evidence")
        self.assertEqual(result.answer, "persistent memory quality evaluation")
        self.assertEqual(len(result.ignored_stale_records), 1)
        self.assertEqual(result.ignored_stale_records[0]["value"], "context capsule verification")
        self.assertEqual(result.read_result.retrieval_trace["memory_kernel"]["ignored_stale_record_count"], 1)
        self.assertEqual(len(fake_client.evidence_calls), 1)

    def test_hybrid_memory_retrieve_prefers_current_state_and_traces_discarded_stale_residue(self) -> None:
        fake_client = _HybridRetrievalMemoryClient()
        with patch("spark_intelligence.memory.orchestrator._load_sdk_client_for_module", return_value=fake_client), patch(
            "spark_intelligence.memory.orchestrator.inspect_memory_sdk_runtime",
            return_value={"ready": True, "client_kind": "fake"},
        ):
            result = hybrid_memory_retrieve(
                config_manager=self.config_manager,
                state_db=self.state_db,
                query="What should we focus on next?",
                subject="human:test",
                predicate="profile.current_focus",
                limit=2,
                actor_id="test",
            )

        self.assertFalse(result.read_result.abstained)
        self.assertEqual(result.read_result.method, "hybrid_memory_retrieve")
        self.assertEqual(result.read_result.records[0]["value"], "persistent memory quality evaluation")
        trace = result.read_result.retrieval_trace["hybrid_memory_retrieve"]
        self.assertEqual(trace["selected_count"], 2)
        self.assertIn("current_state", {lane["lane"] for lane in trace["lane_summaries"]})
        self.assertIn("typed_temporal_graph", {lane["lane"] for lane in trace["lane_summaries"]})
        packet = trace["context_packet"]
        self.assertEqual(packet["sections"][0]["section"], "active_current_state")
        self.assertEqual(packet["sections"][0]["authority"], "authority")
        self.assertEqual(packet["source_mix"]["current_state"], 1)
        self.assertTrue(packet["trace"]["score_adaptive_truncation"])
        gates = packet["trace"]["promotion_gates"]
        self.assertEqual(gates["status"], "pass")
        self.assertEqual(gates["gates"]["stale_current_conflict"]["status"], "pass")
        self.assertEqual(gates["gates"]["source_swamp_resistance"]["status"], "pass")
        self.assertIn("diagnostics", {section["section"] for section in packet["sections"]})
        self.assertTrue(any(candidate["survived_context_budget"] for candidate in trace["candidates"]))
        graph_lane = next(lane for lane in trace["lane_summaries"] if lane["lane"] == "typed_temporal_graph")
        self.assertEqual(graph_lane["source_class"], "graphiti_temporal_graph")
        self.assertEqual(graph_lane["status"], "disabled")
        self.assertTrue(graph_lane["shadow_only"])
        self.assertEqual(graph_lane["reason"], "graph_sidecar_shadow_disabled")
        self.assertGreaterEqual(graph_lane["raw_local_record_count"], 1)
        current_candidates = [candidate for candidate in result.candidates if candidate.lane == "current_state"]
        stale_candidates = [candidate for candidate in result.candidates if candidate.reason_discarded == "stale_or_superseded"]
        self.assertTrue(current_candidates)
        self.assertTrue(current_candidates[0].selected)
        self.assertTrue(stale_candidates)
        self.assertEqual(len(fake_client.current_state_calls), 1)
        self.assertEqual(len(fake_client.evidence_calls), 1)
        self.assertEqual(len(fake_client.retrieval_event_calls), 1)

    def test_hybrid_memory_context_packet_warns_when_supporting_sources_swamp_authority(self) -> None:
        fake_client = _SupportingOnlyHybridRetrievalMemoryClient()
        with patch("spark_intelligence.memory.orchestrator._load_sdk_client_for_module", return_value=fake_client), patch(
            "spark_intelligence.memory.orchestrator.inspect_memory_sdk_runtime",
            return_value={"ready": True, "client_kind": "fake"},
        ):
            result = hybrid_memory_retrieve(
                config_manager=self.config_manager,
                state_db=self.state_db,
                query="What memory evaluation notes are available?",
                subject="human:test",
                predicate="profile.current_focus",
                limit=4,
                actor_id="test",
            )

        packet = result.context_packet
        self.assertIsNotNone(packet)
        assert packet is not None
        gates = packet.trace["promotion_gates"]
        self.assertEqual(gates["status"], "warn")
        self.assertEqual(gates["mode"], "trace_only")
        self.assertEqual(gates["gates"]["source_swamp_resistance"]["status"], "warn")
        self.assertEqual(gates["gates"]["stale_current_conflict"]["status"], "pass")
        self.assertEqual(gates["gates"]["source_swamp_resistance"]["evidence"]["authority_count"], 0)
        self.assertGreaterEqual(gates["gates"]["source_swamp_resistance"]["evidence"]["supporting_count"], 3)

    def test_hybrid_memory_context_packet_allows_clean_authority_anchored_supporting_mix(self) -> None:
        fake_client = _AuthorityAnchoredDominantEvidenceMemoryClient()
        with patch("spark_intelligence.memory.orchestrator._load_sdk_client_for_module", return_value=fake_client), patch(
            "spark_intelligence.memory.orchestrator.inspect_memory_sdk_runtime",
            return_value={"ready": True, "client_kind": "fake"},
        ):
            result = hybrid_memory_retrieve(
                config_manager=self.config_manager,
                state_db=self.state_db,
                query="What should we focus on next?",
                subject="human:test",
                predicate="profile.current_focus",
                limit=6,
                actor_id="test",
            )

        packet = result.context_packet
        self.assertIsNotNone(packet)
        assert packet is not None
        gates = packet.trace["promotion_gates"]
        mix_gate = gates["gates"]["source_mix_stability"]
        self.assertEqual(gates["status"], "pass")
        self.assertEqual(mix_gate["status"], "pass")
        self.assertEqual(mix_gate["reason"], "authority_anchor_with_clean_supporting_mix")
        self.assertEqual(mix_gate["evidence"]["authority_count"], 1)
        self.assertGreater(mix_gate["evidence"]["dominant_fraction"], 0.75)

    def test_hybrid_memory_retrieve_prepares_graphiti_shadow_lane_without_selecting_it(self) -> None:
        self.config_manager.set_path("spark.memory.sidecars.graphiti.enabled", True)
        fake_client = _HybridRetrievalMemoryClient()
        with patch("spark_intelligence.memory.orchestrator._load_sdk_client_for_module", return_value=fake_client), patch(
            "spark_intelligence.memory.orchestrator.inspect_memory_sdk_runtime",
            return_value={"ready": True, "client_kind": "fake"},
        ):
            result = hybrid_memory_retrieve(
                config_manager=self.config_manager,
                state_db=self.state_db,
                query="What should we focus on next?",
                subject="human:test",
                predicate="profile.current_focus",
                limit=2,
                actor_id="test",
            )

        self.assertEqual(result.read_result.records[0]["value"], "persistent memory quality evaluation")
        graph_lane = next(
            lane
            for lane in result.read_result.retrieval_trace["hybrid_memory_retrieve"]["lane_summaries"]
            if lane["lane"] == "typed_temporal_graph"
        )
        self.assertEqual(graph_lane["status"], "prepared")
        self.assertEqual(graph_lane["mode"], "shadow")
        self.assertEqual(graph_lane["reason"], "graph_sidecar_shadow_prepared_backend_not_configured")
        self.assertEqual(graph_lane["sidecar_hit_count"], 0)
        self.assertFalse(any(candidate.lane == "typed_temporal_graph" for candidate in result.candidates))

    def test_hybrid_memory_retrieve_surfaces_live_graphiti_hits_as_supporting_candidates(self) -> None:
        from domain_chip_memory import MemorySidecarHealthResult, MemorySidecarHit, MemorySidecarRetrievalResult

        class FakeGraphSidecar:
            mode = "shadow"

            def upsert_episode(self, episode):
                return SimpleNamespace(trace={"status": "persisted", "source_record_id": episode.source_record_id})

            def retrieve(self, request):
                return MemorySidecarRetrievalResult(
                    sidecar_name="graphiti_temporal_graph",
                    hits=[
                        MemorySidecarHit(
                            sidecar_name="graphiti_temporal_graph",
                            source_class="graphiti_temporal_graph",
                            source_record_id="graph-edge-1",
                            text="The GTM launch blocker is creator approvals.",
                            score=0.84,
                            provenance={"source": "graphiti", "uuid": "graph-edge-1"},
                            validity={"valid_at": "2026-04-29T10:00:00Z"},
                            entity_keys=["named-object:gtm-launch"],
                            metadata={"authority": "supporting_not_authoritative", "scope": "entity.blocker"},
                        )
                    ],
                    trace={"status": "ok", "backend_configured": True, "hit_count": 1},
                )

            def health(self):
                return MemorySidecarHealthResult(
                    sidecar_name="graphiti_temporal_graph",
                    status="ok",
                    enabled=True,
                    mode="shadow",
                    details={"authority": "not_authoritative", "backend": "kuzu"},
                )

        self.config_manager.set_path("spark.memory.sidecars.graphiti.enabled", True)
        self.config_manager.set_path("spark.memory.sidecars.graphiti.backend", "kuzu")
        self.config_manager.set_path("spark.memory.sidecars.graphiti.db_path", "{home}/graphiti.kuzu")
        self.config_manager.set_path("providers.default_provider", "custom")
        self.config_manager.set_path(
            "providers.records.custom",
            {
                "provider_kind": "zai",
                "api_key_env": "ZAI_API_KEY",
                "base_url": "https://api.z.ai/api/coding/paas/v4/",
                "default_model": "glm-5.1",
            },
        )
        self.config_manager.upsert_env_secret("ZAI_API_KEY", "test-secret")
        fake_client = _HybridRetrievalMemoryClient()

        def fake_build_sidecars(**kwargs):
            self.assertEqual(kwargs.get("graphiti_backend"), "kuzu")
            self.assertTrue(str(kwargs.get("graphiti_db_path")).endswith("graphiti.kuzu"))
            self.assertEqual(kwargs.get("graphiti_llm_api_key_env"), "ZAI_API_KEY")
            self.assertEqual(kwargs.get("graphiti_llm_api_key"), "test-secret")
            self.assertEqual(kwargs.get("graphiti_llm_base_url"), "https://api.z.ai/api/coding/paas/v4/")
            self.assertEqual(kwargs.get("graphiti_llm_model"), "glm-5.1")
            self.assertTrue(kwargs.get("graphiti_auto_build_indices"))
            self.assertEqual(kwargs.get("graphiti_call_timeout_seconds"), 6.0)
            return {"graphiti_temporal_graph": FakeGraphSidecar()}

        with patch("spark_intelligence.memory.orchestrator._load_sdk_client_for_module", return_value=fake_client), patch(
            "spark_intelligence.memory.orchestrator.inspect_memory_sdk_runtime",
            return_value={"ready": True, "client_kind": "fake"},
        ), patch("domain_chip_memory.build_default_memory_sidecars", side_effect=fake_build_sidecars):
            result = hybrid_memory_retrieve(
                config_manager=self.config_manager,
                state_db=self.state_db,
                query="What is blocking the GTM launch?",
                subject="human:test",
                predicate="entity.blocker",
                entity_key="named-object:gtm-launch",
                limit=5,
                actor_id="test",
            )

        graph_lane = next(
            lane
            for lane in result.read_result.retrieval_trace["hybrid_memory_retrieve"]["lane_summaries"]
            if lane["lane"] == "typed_temporal_graph"
        )
        self.assertEqual(graph_lane["status"], "ok")
        self.assertEqual(graph_lane["reason"], "graph_sidecar_shadow_live_hits")
        self.assertEqual(graph_lane["backend"], "kuzu")
        self.assertEqual(graph_lane["llm_provider"]["model"], "glm-5.1")
        self.assertTrue(graph_lane["llm_provider"]["api_key_configured"])
        self.assertTrue(graph_lane["auto_build_indices"])
        self.assertEqual(graph_lane["call_timeout_seconds"], 6.0)
        self.assertNotIn("test-secret", str(graph_lane))
        self.assertEqual(graph_lane["sidecar_hit_count"], 1)
        graph_candidates = [candidate for candidate in result.candidates if candidate.lane == "typed_temporal_graph"]
        self.assertEqual(len(graph_candidates), 1)
        self.assertEqual(graph_candidates[0].source_class, "graphiti_temporal_graph")
        self.assertEqual(graph_candidates[0].record["metadata"]["authority"], "supporting_not_authoritative")

    def test_hybrid_memory_retrieve_skips_graphiti_retrieve_after_upsert_error(self) -> None:
        from domain_chip_memory import MemorySidecarHealthResult, MemorySidecarUpsertResult

        class FakeGraphSidecar:
            mode = "shadow"

            def upsert_episode(self, episode):
                return MemorySidecarUpsertResult(
                    sidecar_name="graphiti_temporal_graph",
                    status="error",
                    trace={"backend_configured": True, "error": "TimeoutError"},
                )

            def retrieve(self, request):
                raise AssertionError("retrieve should not run after a shadow upsert error")

            def health(self):
                return MemorySidecarHealthResult(
                    sidecar_name="graphiti_temporal_graph",
                    status="ok",
                    enabled=True,
                    mode="shadow",
                    details={"authority": "not_authoritative", "backend": "kuzu"},
                )

        self.config_manager.set_path("spark.memory.sidecars.graphiti.enabled", True)
        fake_client = _HybridRetrievalMemoryClient()

        with patch("spark_intelligence.memory.orchestrator._load_sdk_client_for_module", return_value=fake_client), patch(
            "spark_intelligence.memory.orchestrator.inspect_memory_sdk_runtime",
            return_value={"ready": True, "client_kind": "fake"},
        ), patch(
            "domain_chip_memory.build_default_memory_sidecars",
            return_value={"graphiti_temporal_graph": FakeGraphSidecar()},
        ):
            result = hybrid_memory_retrieve(
                config_manager=self.config_manager,
                state_db=self.state_db,
                query="What is blocking the GTM launch?",
                subject="human:test",
                predicate="entity.blocker",
                entity_key="named-object:gtm-launch",
                limit=5,
                actor_id="test",
            )

        graph_lane = next(
            lane
            for lane in result.read_result.retrieval_trace["hybrid_memory_retrieve"]["lane_summaries"]
            if lane["lane"] == "typed_temporal_graph"
        )
        self.assertEqual(graph_lane["status"], "error")
        self.assertEqual(graph_lane["reason"], "graph_sidecar_shadow_upsert_error")
        self.assertEqual(graph_lane["retrieval_trace"]["status"], "skipped")
        self.assertFalse(any(candidate.lane == "typed_temporal_graph" for candidate in result.candidates))

    def test_hybrid_memory_retrieve_filters_raw_episodes_before_graphiti_export(self) -> None:
        raw_record = {
            "memory_role": "episodic",
            "predicate": "raw_turn",
            "value": "recall what i told you about the demo",
            "text": "recall what i told you about the demo",
            "metadata": {"raw_episode": True},
        }
        structured_record = {
            "memory_role": "current_state",
            "predicate": "entity.status",
            "value": "ready",
            "text": "The GTM launch status is ready.",
            "metadata": {"entity_key": "named-object:gtm-launch"},
        }

        self.assertFalse(memory_orchestrator._graph_sidecar_exportable_record(raw_record))
        self.assertTrue(memory_orchestrator._graph_sidecar_exportable_record(structured_record))

    def test_hybrid_memory_retrieve_reads_wiki_packets_as_supporting_context(self) -> None:
        wiki_dir = self.home / "wiki"
        wiki_dir.mkdir()
        (wiki_dir / "memory-stack.md").write_text(
            "# Spark Memory Stack\n\nGraphiti is a temporal graph sidecar for architecture decisions.",
            encoding="utf-8",
        )
        fake_client = _HybridRetrievalMemoryClient()
        with patch("spark_intelligence.memory.orchestrator._load_sdk_client_for_module", return_value=fake_client), patch(
            "spark_intelligence.memory.orchestrator.inspect_memory_sdk_runtime",
            return_value={"ready": True, "client_kind": "fake"},
        ):
            result = hybrid_memory_retrieve(
                config_manager=self.config_manager,
                state_db=self.state_db,
                query="What architecture decision did we make about Graphiti?",
                subject="human:test",
                predicate="profile.current_focus",
                limit=4,
                actor_id="test",
            )

        trace = result.read_result.retrieval_trace["hybrid_memory_retrieve"]
        wiki_lane = next(lane for lane in trace["lane_summaries"] if lane["lane"] == "wiki_packets")
        self.assertEqual(wiki_lane["status"], "supported")
        self.assertEqual(wiki_lane["source_class"], "obsidian_llm_wiki_packets")
        self.assertEqual(wiki_lane["record_count"], 1)
        wiki_candidates = [candidate for candidate in result.candidates if candidate.lane == "wiki_packets"]
        self.assertEqual(len(wiki_candidates), 1)
        self.assertEqual(wiki_candidates[0].source_class, "obsidian_llm_wiki_packets")
        self.assertEqual(wiki_candidates[0].record["metadata"]["authority"], "supporting_not_authoritative")
        packet = trace["context_packet"]
        sections = {section["section"]: section for section in packet["sections"]}
        self.assertIn("compiled_project_knowledge", sections)
        self.assertEqual(sections["compiled_project_knowledge"]["authority"], "supporting_not_authoritative")
        self.assertEqual(packet["source_mix"]["obsidian_llm_wiki_packets"], 1)

    def test_hybrid_memory_context_packet_keeps_current_state_inside_tight_budget(self) -> None:
        self.config_manager.set_path("spark.memory.hybrid_context.max_chars", 700)
        wiki_dir = self.home / "wiki"
        wiki_dir.mkdir()
        (wiki_dir / "long-memory-note.md").write_text(
            "# Long Memory Note\n\n" + ("Graphiti sidecar architecture detail. " * 80),
            encoding="utf-8",
        )
        fake_client = _HybridRetrievalMemoryClient()
        with patch("spark_intelligence.memory.orchestrator._load_sdk_client_for_module", return_value=fake_client), patch(
            "spark_intelligence.memory.orchestrator.inspect_memory_sdk_runtime",
            return_value={"ready": True, "client_kind": "fake"},
        ):
            result = hybrid_memory_retrieve(
                config_manager=self.config_manager,
                state_db=self.state_db,
                query="What should we focus on next and what did Graphiti add?",
                subject="human:test",
                predicate="profile.current_focus",
                limit=4,
                actor_id="test",
            )

        packet = result.context_packet
        self.assertIsNotNone(packet)
        assert packet is not None
        self.assertLessEqual(packet.used_chars, packet.max_chars)
        self.assertEqual(packet.sections[0]["section"], "active_current_state")
        self.assertEqual(packet.sections[0]["items"][0]["value"], "persistent memory quality evaluation")
        trace_candidates = result.read_result.retrieval_trace["hybrid_memory_retrieve"]["candidates"]
        current_trace = next(candidate for candidate in trace_candidates if candidate["lane"] == "current_state")
        self.assertTrue(current_trace["survived_context_budget"])

    def test_hybrid_memory_context_packet_splits_entity_state_section(self) -> None:
        fake_client = _HybridRetrievalMemoryClient()
        with patch("spark_intelligence.memory.orchestrator._load_sdk_client_for_module", return_value=fake_client), patch(
            "spark_intelligence.memory.orchestrator.inspect_memory_sdk_runtime",
            return_value={"ready": True, "client_kind": "fake"},
        ):
            result = hybrid_memory_retrieve(
                config_manager=self.config_manager,
                state_db=self.state_db,
                query="What did I name the plant?",
                subject="human:test",
                predicate="entity.name",
                entity_key="named-object:tiny-desk-plant",
                limit=2,
                actor_id="test",
            )

        packet = result.context_packet
        self.assertIsNotNone(packet)
        assert packet is not None
        self.assertEqual(packet.sections[0]["section"], "entity_state")
        self.assertEqual(packet.sections[0]["items"][0]["entity_key"], "named-object:tiny-desk-plant")

    def test_hybrid_memory_context_packet_includes_pending_tasks_and_procedural_lessons(self) -> None:
        record_pending_task_timeout(
            self.state_db,
            task_key="memory:graphiti-adapter",
            original_request="Wire Graphiti adapter behind a disabled feature flag.",
            human_id="human:test",
            target_repo="vibeforge1111/spark-intelligence-builder",
            target_component="memory_sidecars",
            timeout_point="after adapter contract inspection",
            last_evidence="Sidecar contract exists but runtime is still disabled.",
            next_retry_step="Continue with disabled adapter tests.",
        )
        record_bad_self_review_lesson(
            self.state_db,
            reviewed_subject="memory integration checkpoint",
            missing_evidence=["target repo", "diff", "tests"],
            source_task_key="memory:graphiti-adapter",
        )
        fake_client = _HybridRetrievalMemoryClient()
        with patch("spark_intelligence.memory.orchestrator._load_sdk_client_for_module", return_value=fake_client), patch(
            "spark_intelligence.memory.orchestrator.inspect_memory_sdk_runtime",
            return_value={"ready": True, "client_kind": "fake"},
        ):
            result = hybrid_memory_retrieve(
                config_manager=self.config_manager,
                state_db=self.state_db,
                query="continue the memory integration after the timeout",
                subject="human:test",
                predicate="profile.current_focus",
                limit=6,
                actor_id="test",
            )

        packet = result.context_packet
        self.assertIsNotNone(packet)
        assert packet is not None
        sections = {section["section"]: section for section in packet.sections}
        self.assertIn("pending_tasks", sections)
        self.assertIn("procedural_lessons", sections)
        self.assertIn("Continue with disabled adapter tests.", sections["pending_tasks"]["items"][0]["text"])
        self.assertIn("Inspect target repo, diff, route/demo state, and tests", sections["procedural_lessons"]["items"][0]["text"])
        trace = result.read_result.retrieval_trace["hybrid_memory_retrieve"]
        self.assertIn("pending_tasks", {lane["lane"] for lane in trace["lane_summaries"]})
        self.assertIn("procedural_lessons", {lane["lane"] for lane in trace["lane_summaries"]})
        self.assertEqual(packet.source_mix["pending_task"], 1)
        self.assertEqual(packet.source_mix["procedural_lesson"], 1)

    def test_hybrid_memory_retrieve_adds_recent_conversation_lane_with_query_overlap(self) -> None:
        record_event(
            self.state_db,
            event_type="telegram_message_received",
            component="telegram_gateway",
            summary="User said the tiny desk plant is named Mira.",
            session_id="session:recent",
            human_id="test",
            facts={
                "role": "user",
                "message_text": "The tiny desk plant is named Mira.",
            },
        )
        record_event(
            self.state_db,
            event_type="diagnostics_scan_complete",
            component="diagnostics",
            summary="Diagnostics mentioned Mira only as noise.",
            session_id="session:recent",
            human_id="test",
            facts={"message_text": "Mira diagnostics noise."},
        )
        fake_client = _HybridRetrievalMemoryClient()
        with patch("spark_intelligence.memory.orchestrator._load_sdk_client_for_module", return_value=fake_client), patch(
            "spark_intelligence.memory.orchestrator.inspect_memory_sdk_runtime",
            return_value={"ready": True, "client_kind": "fake"},
        ):
            result = hybrid_memory_retrieve(
                config_manager=self.config_manager,
                state_db=self.state_db,
                query="What did I say about Mira?",
                subject="human:test",
                predicate="profile.current_focus",
                limit=4,
                actor_id="test",
            )

        trace = result.read_result.retrieval_trace["hybrid_memory_retrieve"]
        recent_lane = next(lane for lane in trace["lane_summaries"] if lane["lane"] == "recent_conversation")
        self.assertEqual(recent_lane["status"], "supported")
        self.assertEqual(recent_lane["record_count"], 1)
        recent_candidates = [candidate for candidate in result.candidates if candidate.lane == "recent_conversation"]
        self.assertEqual(len(recent_candidates), 1)
        self.assertEqual(recent_candidates[0].record["metadata"]["role"], "user")
        packet = result.context_packet
        self.assertIsNotNone(packet)
        assert packet is not None
        sections = {section["section"]: section for section in packet.sections}
        self.assertIn("recent_conversation", sections)
        self.assertEqual(packet.source_mix["recent_conversation"], 1)
        recent_gate = packet.trace["promotion_gates"]["gates"]["recent_conversation_noise"]
        self.assertEqual(recent_gate["status"], "pass")
        self.assertEqual(recent_gate["evidence"]["recent_selected_count"], 1)
        self.assertEqual(recent_gate["evidence"]["recent_noise_count"], 0)

    def test_memory_kernel_dispatches_hybrid_memory_retrieve(self) -> None:
        fake_client = _HybridRetrievalMemoryClient()
        with patch("spark_intelligence.memory.orchestrator._load_sdk_client_for_module", return_value=fake_client), patch(
            "spark_intelligence.memory.orchestrator.inspect_memory_sdk_runtime",
            return_value={"ready": True, "client_kind": "fake"},
        ):
            result = read_memory_kernel(
                config_manager=self.config_manager,
                state_db=self.state_db,
                method="hybrid_memory_retrieve",
                query="What should we focus on next?",
                subject="human:test",
                predicate="profile.current_focus",
                limit=2,
                actor_id="test",
            )

        self.assertEqual(result.read_method, "hybrid_memory_retrieve")
        self.assertEqual(result.source_class, "hybrid")
        self.assertEqual(result.answer, "persistent memory quality evaluation")
        self.assertEqual(result.read_result.retrieval_trace["memory_kernel"]["source_class"], "hybrid")
        self.assertEqual(len(fake_client.current_state_calls), 1)

    def test_profile_city_detection_and_write_use_structured_current_state_observation(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)
        detected = detect_profile_fact_observation("I moved to Dubai.")
        self.assertIsNotNone(detected)
        assert detected is not None
        self.assertEqual(detected.predicate, "profile.city")
        self.assertEqual(detected.value, "Dubai")

        fake_client = _FakeMemoryClient()
        with patch("spark_intelligence.memory.orchestrator._load_sdk_client", return_value=fake_client):
            result = write_profile_fact_to_memory(
                config_manager=self.config_manager,
                state_db=self.state_db,
                human_id="human:test",
                predicate=detected.predicate,
                value=detected.value,
                evidence_text=detected.evidence_text,
                fact_name=detected.fact_name,
                session_id="session:city",
                turn_id="turn:city",
                channel_kind="telegram",
            )

        self.assertEqual(result.status, "succeeded")
        self.assertEqual(len(fake_client.observation_calls), 1)
        call = fake_client.observation_calls[0]
        self.assertEqual(call["subject"], "human:test")
        self.assertEqual(call["predicate"], "profile.city")
        self.assertEqual(call["value"], "Dubai")
        self.assertEqual(call["text"], "I moved to Dubai.")
        self.assertEqual(call["retention_class"], "durable_profile")
        self.assertTrue(call["document_time"])
        self.assertTrue(call["valid_from"])
        events = latest_events_by_type(self.state_db, event_type="memory_write_requested", limit=10)
        self.assertTrue(events)
        observations = (events[0]["facts_json"] or {}).get("observations") or []
        self.assertEqual(observations[0]["predicate"], "profile.city")
        self.assertEqual(observations[0]["value"], "Dubai")
        self.assertEqual(observations[0]["retention_class"], "durable_profile")

    def test_profile_current_state_predicates_request_active_state_retention(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        fake_client = _FakeMemoryClient()
        with patch("spark_intelligence.memory.orchestrator._load_sdk_client", return_value=fake_client):
            result = write_profile_fact_to_memory(
                config_manager=self.config_manager,
                state_db=self.state_db,
                human_id="human:test",
                predicate="profile.current_plan",
                value="launch Atlas in enterprise first",
                evidence_text="Our current plan is to launch Atlas in enterprise first.",
                fact_name="current_plan",
                session_id="session:plan",
                turn_id="turn:plan",
                channel_kind="telegram",
            )

        self.assertEqual(result.status, "succeeded")
        self.assertEqual(fake_client.observation_calls[0]["retention_class"], "active_state")
        self.assertEqual(fake_client.observation_calls[0]["metadata"]["entity_key"], "profile.current_plan")

    def test_lookup_current_state_uses_profile_preferred_name_entity_key(self) -> None:
        fake_client = _FakeMemoryClient()
        with patch("spark_intelligence.memory.orchestrator._load_sdk_client_for_module", return_value=fake_client):
            result = lookup_current_state_in_memory(
                config_manager=self.config_manager,
                state_db=self.state_db,
                subject="human:test",
                predicate="profile.preferred_name",
                actor_id="test",
            )

        self.assertFalse(result.read_result.abstained)
        self.assertEqual(fake_client.current_state_calls[0]["entity_key"], "profile.preferred_name")

    def test_profile_fact_write_records_salience_and_memory_lane(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        fake_client = _FakeMemoryClient()
        with patch("spark_intelligence.memory.orchestrator._load_sdk_client", return_value=fake_client):
            result = write_profile_fact_to_memory(
                config_manager=self.config_manager,
                state_db=self.state_db,
                human_id="human:test",
                predicate="profile.preferred_name",
                value="Cem",
                evidence_text="I'm not Maya by the way, I'm Cem.",
                fact_name="profile_preferred_name",
                session_id="session:identity",
                turn_id="turn:identity",
                channel_kind="telegram",
            )

        self.assertEqual(result.status, "succeeded")
        metadata = fake_client.observation_calls[0]["metadata"]
        self.assertEqual(metadata["promotion_stage"], "current_state_confirmed")
        self.assertEqual(metadata["why_saved"], "identity_correction_supersession")
        self.assertEqual(metadata["promotion_disposition"], "promote_current_state")
        self.assertGreaterEqual(metadata["salience_score"], 0.75)
        self.assertEqual(metadata["entity_key"], "profile.preferred_name")
        self.assertEqual(metadata["authority"], "identity_current_state")
        self.assertEqual(metadata["supersession_kind"], "identity_correction")
        self.assertTrue(metadata["stale_prior_values"])

        events = latest_events_by_type(self.state_db, event_type="memory_write_requested", limit=10)
        facts = events[0]["facts_json"] or {}
        self.assertEqual(facts["why_saved"], "identity_correction_supersession")
        self.assertEqual(facts["promotion_stage"], "current_state_confirmed")
        self.assertEqual(facts["promotion_disposition"], "promote_current_state")

        lane_records = recent_memory_lane_records(self.state_db, limit=10)
        self.assertTrue(lane_records)
        self.assertEqual(lane_records[0]["promotion_disposition"], "promote_current_state")
        self.assertEqual(lane_records[0]["status"], "candidate")

        lifecycle_events = latest_events_by_type(self.state_db, event_type="memory_lifecycle_transition", limit=10)
        lifecycle_facts = [
            event["facts_json"]
            for event in lifecycle_events
            if (event["facts_json"] or {}).get("transition_kind") == "salience_promotion"
        ]
        self.assertTrue(lifecycle_facts)
        self.assertEqual(lifecycle_facts[0]["lifecycle_action"], "promoted_by_salience")
        self.assertEqual(lifecycle_facts[0]["source_predicate"], "profile.preferred_name")
        self.assertEqual(lifecycle_facts[0]["destination"], "promote_current_state")
        self.assertEqual(lifecycle_facts[0]["why_saved"], "identity_correction_supersession")
        self.assertGreaterEqual(lifecycle_facts[0]["salience_score"], 0.75)

    def test_profile_name_correction_after_new_name_is_authoritative(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        detected = detect_profile_fact_observation("nom my name is Cem not Maya :))")
        self.assertIsNotNone(detected)
        assert detected is not None
        self.assertEqual(detected.value, "Cem")

        fake_client = _FakeMemoryClient()
        with patch("spark_intelligence.memory.orchestrator._load_sdk_client", return_value=fake_client):
            result = write_profile_fact_to_memory(
                config_manager=self.config_manager,
                state_db=self.state_db,
                human_id="human:test",
                predicate=detected.predicate,
                value=detected.value,
                evidence_text=detected.evidence_text,
                fact_name=detected.fact_name,
                session_id="session:identity",
                turn_id="turn:identity",
                channel_kind="telegram",
            )

        self.assertEqual(result.status, "succeeded")
        metadata = fake_client.observation_calls[0]["metadata"]
        self.assertEqual(metadata["promotion_stage"], "current_state_confirmed")
        self.assertEqual(metadata["why_saved"], "identity_correction_supersession")
        self.assertEqual(metadata["promotion_disposition"], "promote_current_state")
        self.assertGreaterEqual(metadata["salience_score"], 0.75)
        self.assertEqual(metadata["authority"], "identity_current_state")
        self.assertEqual(metadata["supersession_kind"], "identity_correction")

    def test_profile_fact_salience_gate_blocks_secret_like_memory(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        fake_client = _FakeMemoryClient()
        with patch("spark_intelligence.memory.orchestrator._load_sdk_client", return_value=fake_client):
            result = write_profile_fact_to_memory(
                config_manager=self.config_manager,
                state_db=self.state_db,
                human_id="human:test",
                predicate="profile.current_plan",
                value="use api key sk-testsecret123456",
                evidence_text="My current plan is to use api key sk-testsecret123456.",
                fact_name="profile_current_plan",
                session_id="session:secret",
                turn_id="turn:secret",
                channel_kind="telegram",
            )

        self.assertEqual(result.status, "skipped")
        self.assertEqual(result.reason, "salience_secret_like_material")
        self.assertEqual(len(fake_client.observation_calls), 0)

        policy_records = recent_policy_gate_records(self.state_db, limit=10)
        self.assertTrue(policy_records)
        self.assertEqual(policy_records[0]["gate_name"], "memory.salience")
        self.assertEqual(policy_records[0]["reason_code"], "salience_secret_like_material")

        lifecycle_events = latest_events_by_type(self.state_db, event_type="memory_lifecycle_transition", limit=10)
        lifecycle_facts = [
            event["facts_json"]
            for event in lifecycle_events
            if (event["facts_json"] or {}).get("transition_kind") == "salience_block"
        ]
        self.assertTrue(lifecycle_facts)
        self.assertEqual(lifecycle_facts[0]["lifecycle_action"], "blocked_by_salience")
        self.assertEqual(lifecycle_facts[0]["destination"], "policy_gate_trace_only")
        self.assertEqual(lifecycle_facts[0]["source_predicate"], "profile.current_plan")
        self.assertEqual(lifecycle_facts[0]["reason"], "secret_like_material_blocked")
        self.assertEqual(lifecycle_facts[0]["salience_score"], 0.0)

    def test_named_object_profile_fact_writes_generic_entity_state_projection(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        fake_client = _FakeMemoryClient()
        with patch("spark_intelligence.memory.orchestrator._load_sdk_client", return_value=fake_client), patch(
            "spark_intelligence.memory.orchestrator._now_iso",
            return_value="2026-04-28T09:00:00Z",
        ):
            result = write_profile_fact_to_memory(
                config_manager=self.config_manager,
                state_db=self.state_db,
                human_id="human:test",
                predicate="profile.current_low_stakes_test_fact",
                value="the tiny desk plant is named Mira",
                evidence_text="For later, the tiny desk plant is named Mira.",
                fact_name="current_low_stakes_test_fact",
                session_id="session:plant",
                turn_id="turn:plant",
                channel_kind="telegram",
            )

        self.assertEqual(result.status, "succeeded")
        self.assertEqual(len(fake_client.observation_calls), 2)
        profile_call = fake_client.observation_calls[0]
        entity_call = fake_client.observation_calls[1]
        self.assertEqual(profile_call["predicate"], "profile.current_low_stakes_test_fact")
        self.assertEqual(profile_call["metadata"]["entity_key"], "profile.current_low_stakes_test_fact")
        self.assertEqual(entity_call["predicate"], "entity.name")
        self.assertEqual(entity_call["value"], "Mira")
        self.assertEqual(entity_call["turn_id"], "turn:plant:entity-projection")
        self.assertEqual(entity_call["retention_class"], "active_state")
        self.assertEqual(entity_call["metadata"]["entity_type"], "named_object")
        self.assertEqual(entity_call["metadata"]["entity_key"], "named-object:tiny-desk-plant")
        self.assertEqual(entity_call["metadata"]["entity_label"], "tiny desk plant")
        self.assertEqual(entity_call["metadata"]["source_turn_id"], "turn:plant")
        self.assertEqual(entity_call["metadata"]["source_predicate"], "profile.current_low_stakes_test_fact")
        events = latest_events_by_type(self.state_db, event_type="memory_write_requested", limit=10)
        observations = (events[0]["facts_json"] or {}).get("observations") or []
        self.assertEqual([item["predicate"] for item in observations], ["profile.current_low_stakes_test_fact", "entity.name"])

    def test_non_named_low_stakes_fact_does_not_write_entity_projection(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        fake_client = _FakeMemoryClient()
        with patch("spark_intelligence.memory.orchestrator._load_sdk_client", return_value=fake_client):
            result = write_profile_fact_to_memory(
                config_manager=self.config_manager,
                state_db=self.state_db,
                human_id="human:test",
                predicate="profile.current_low_stakes_test_fact",
                value="the tester code is seven blue chairs",
                evidence_text="For later, the tester code is seven blue chairs.",
                fact_name="current_low_stakes_test_fact",
                session_id="session:test-fact",
                turn_id="turn:test-fact",
                channel_kind="telegram",
            )

        self.assertEqual(result.status, "succeeded")
        self.assertEqual(len(fake_client.observation_calls), 1)
        self.assertEqual(fake_client.observation_calls[0]["predicate"], "profile.current_low_stakes_test_fact")

    def test_direct_entity_state_fact_writes_entity_key_metadata(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        fake_client = _FakeMemoryClient()
        with patch("spark_intelligence.memory.orchestrator._load_sdk_client", return_value=fake_client), patch(
            "spark_intelligence.memory.orchestrator._now_iso",
            return_value="2026-04-28T10:00:00Z",
        ):
            result = write_profile_fact_to_memory(
                config_manager=self.config_manager,
                state_db=self.state_db,
                human_id="human:test",
                predicate="entity.location",
                value="the kitchen shelf",
                evidence_text="For later, the tiny desk plant is on the kitchen shelf.",
                fact_name="entity_location",
                session_id="session:entity-location",
                turn_id="turn:entity-location",
                channel_kind="telegram",
            )

        self.assertEqual(result.status, "succeeded")
        call = fake_client.observation_calls[0]
        self.assertEqual(call["predicate"], "entity.location")
        self.assertEqual(call["retention_class"], "active_state")
        self.assertEqual(call["metadata"]["entity_key"], "named-object:tiny-desk-plant")
        self.assertEqual(call["metadata"]["entity_label"], "tiny desk plant")
        self.assertEqual(call["metadata"]["entity_attribute"], "location")
        self.assertEqual(call["metadata"]["location_preposition"], "on")
        self.assertEqual(call["metadata"]["revalidate_after_days"], 21)

    def test_tentative_project_direction_promotes_to_entity_decision_with_salience(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        observation = detect_telegram_generic_observation(
            "We are leaning toward founder-led onboarding for the GTM launch."
        )
        self.assertIsNotNone(observation)
        assert observation is not None
        self.assertEqual(observation.predicate, "entity.decision")
        self.assertEqual(observation.value, "founder-led onboarding")
        self.assertEqual(
            build_telegram_generic_observation_answer(observation=observation),
            "I'll remember that the GTM launch decision is founder-led onboarding.",
        )

        fake_client = _FakeMemoryClient()
        with patch("spark_intelligence.memory.orchestrator._load_sdk_client", return_value=fake_client):
            result = write_profile_fact_to_memory(
                config_manager=self.config_manager,
                state_db=self.state_db,
                human_id="human:test",
                predicate=observation.predicate,
                value=observation.value,
                evidence_text=observation.evidence_text,
                fact_name=observation.fact_name,
                session_id="session:gtm-direction",
                turn_id="turn:gtm-direction",
                channel_kind="telegram",
                actor_id="telegram_generic_observation_loader",
            )

        self.assertEqual(result.status, "succeeded")
        call = fake_client.observation_calls[0]
        self.assertEqual(call["predicate"], "entity.decision")
        self.assertEqual(call["metadata"]["entity_key"], "named-object:gtm-launch")
        self.assertEqual(call["metadata"]["entity_label"], "GTM launch")
        self.assertEqual(call["metadata"]["why_saved"], "tentative_project_direction")
        self.assertIn("tentative_direction_signal", call["metadata"]["salience_reasons"])

    def test_current_state_lookup_preserves_matching_provenance_metadata(self) -> None:
        raw = memory_orchestrator._normalize_domain_lookup_result(
            result=SimpleNamespace(
                found=True,
                value="windowsill",
                text="human:test entity.location windowsill",
                memory_role="current_state",
                trace={"trace_id": "lookup-trace"},
                provenance=[
                    SimpleNamespace(
                        memory_role="current_state",
                        subject="human:test",
                        predicate="entity.location",
                        text="For later, the tiny desk plant is on the windowsill.",
                        session_id="session:entity-location",
                        turn_ids=["turn:entity-location"],
                        timestamp="2026-04-28T10:00:00Z",
                        observation_id="obs-location",
                        event_id="event-location",
                        retention_class="active_state",
                        lifecycle={},
                        metadata={
                            "value": "windowsill",
                            "entity_key": "named-object:tiny-desk-plant",
                            "entity_label": "tiny desk plant",
                            "entity_attribute": "location",
                            "location_preposition": "on",
                        },
                    )
                ],
            ),
            subject="human:test",
            predicate="entity.location",
        )

        self.assertEqual(raw["status"], "supported")
        record = raw["records"][0]
        self.assertEqual(record["value"], "windowsill")
        self.assertEqual(record["observation_id"], "obs-location")
        self.assertEqual(record["metadata"]["entity_key"], "named-object:tiny-desk-plant")
        self.assertEqual(record["metadata"]["entity_label"], "tiny desk plant")
        self.assertEqual(record["metadata"]["location_preposition"], "on")

    def test_historical_state_lookup_accepts_structured_evidence_contract(self) -> None:
        raw = memory_orchestrator._normalize_domain_lookup_result(
            result=SimpleNamespace(
                found=True,
                value="kitchen shelf",
                text="human:test entity.location kitchen shelf",
                memory_role="structured_evidence",
                trace={"trace_id": "historical-trace"},
                provenance=[
                    SimpleNamespace(
                        memory_role="structured_evidence",
                        subject="human:test",
                        predicate="entity.location",
                        text="For later, the tiny desk plant is on the kitchen shelf.",
                        session_id="session:entity-location",
                        turn_ids=["turn:old-location"],
                        timestamp="2026-04-28T09:00:00Z",
                        observation_id="obs-old-location",
                        event_id="event-old-location",
                        retention_class="active_state",
                        lifecycle={},
                        metadata={
                            "value": "kitchen shelf",
                            "entity_key": "named-object:tiny-desk-plant",
                            "entity_label": "tiny desk plant",
                            "entity_attribute": "location",
                            "location_preposition": "on",
                        },
                    )
                ],
            ),
            subject="human:test",
            predicate="entity.location",
            method="get_historical_state",
        )

        self.assertEqual(raw["status"], "supported")
        record = raw["records"][0]
        self.assertEqual(record["value"], "kitchen shelf")
        self.assertEqual(record["memory_role"], "structured_evidence")
        self.assertEqual(record["metadata"]["entity_key"], "named-object:tiny-desk-plant")
        self.assertEqual(record["metadata"]["location_preposition"], "on")

    def test_direct_entity_state_deletion_writes_entity_key_metadata(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        fake_client = _FakeMemoryClient()
        with patch("spark_intelligence.memory.orchestrator._load_sdk_client", return_value=fake_client), patch(
            "spark_intelligence.memory.orchestrator._now_iso",
            return_value="2026-04-28T10:30:00Z",
        ):
            result = memory_orchestrator.delete_profile_fact_from_memory(
                config_manager=self.config_manager,
                state_db=self.state_db,
                human_id="human:test",
                predicate="entity.location",
                evidence_text="Forget where the tiny desk plant is.",
                fact_name="entity_location",
                session_id="session:entity-location-delete",
                turn_id="turn:entity-location-delete",
                channel_kind="telegram",
            )

        self.assertEqual(result.status, "succeeded")
        call = fake_client.observation_calls[0]
        self.assertEqual(call["operation"], "delete")
        self.assertEqual(call["predicate"], "entity.location")
        self.assertIsNone(call["value"])
        self.assertEqual(call["valid_to"], "2026-04-28T10:30:00Z")
        self.assertEqual(call["deleted_at"], "2026-04-28T10:30:00Z")
        self.assertEqual(call["metadata"]["entity_type"], "named_object")
        self.assertEqual(call["metadata"]["entity_key"], "named-object:tiny-desk-plant")
        self.assertEqual(call["metadata"]["entity_label"], "tiny desk plant")
        self.assertEqual(call["metadata"]["entity_attribute"], "location")

    def test_profile_current_state_predicates_store_revalidation_metadata(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        fake_client = _FakeMemoryClient()
        with patch("spark_intelligence.memory.orchestrator._load_sdk_client", return_value=fake_client), patch(
            "spark_intelligence.memory.orchestrator._now_iso",
            return_value="2025-03-01T09:00:00Z",
        ):
            result = write_profile_fact_to_memory(
                config_manager=self.config_manager,
                state_db=self.state_db,
                human_id="human:test",
                predicate="profile.current_plan",
                value="launch Atlas in enterprise first",
                evidence_text="Our current plan is to launch Atlas in enterprise first.",
                fact_name="current_plan",
                session_id="session:plan:stale",
                turn_id="turn:plan:stale",
                channel_kind="telegram",
            )

        self.assertEqual(result.status, "succeeded")
        metadata = fake_client.observation_calls[0]["metadata"]
        self.assertEqual(metadata["revalidate_after_days"], 30)
        self.assertEqual(metadata["revalidate_at"], "2025-03-31T09:00:00+00:00")
        events = latest_events_by_type(self.state_db, event_type="memory_write_requested", limit=10)
        observations = (events[0]["facts_json"] or {}).get("observations") or []
        self.assertEqual(observations[0]["retention_class"], "active_state")
        self.assertEqual(observations[0]["metadata"]["revalidate_after_days"], 30)
        self.assertEqual(observations[0]["metadata"]["revalidate_at"], "2025-03-31T09:00:00+00:00")

    def test_run_memory_sdk_maintenance_records_active_state_counts(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        fake_client = _MaintenanceMemoryClient()
        with patch("spark_intelligence.memory.orchestrator._load_sdk_client_for_module", return_value=fake_client):
            result = run_memory_sdk_maintenance(
                config_manager=self.config_manager,
                state_db=self.state_db,
                sdk_module="domain_chip_memory",
                now="2025-04-02T09:00:00Z",
                actor_id="memory_test",
            )

        self.assertEqual(result.status, "succeeded")
        self.assertEqual(fake_client.maintenance_calls, [{"now": "2025-04-02T09:00:00Z"}])
        self.assertEqual(result.maintenance["active_state_stale_preserved_count"], 1)
        self.assertEqual(result.maintenance["active_state_superseded_count"], 1)
        self.assertEqual(result.trace["operation"], "reconsolidate_manual_memory")
        events = latest_events_by_type(self.state_db, event_type="memory_maintenance_run", limit=10)
        self.assertTrue(events)
        facts = events[0]["facts_json"] or {}
        self.assertEqual(facts["status"], "succeeded")
        self.assertEqual(facts["maintenance"]["active_state_stale_preserved_count"], 1)
        self.assertEqual(facts["trace"]["active_state_maintenance"]["superseded"], 1)
        lifecycle_events = latest_events_by_type(self.state_db, event_type="memory_lifecycle_transition", limit=10)
        lifecycle_facts = [event["facts_json"] or {} for event in lifecycle_events]
        self.assertEqual(len(lifecycle_facts), 3)
        actions = {facts.get("lifecycle_action") for facts in lifecycle_facts}
        self.assertIn("deleted", actions)
        self.assertIn("stale_preserved", actions)
        self.assertIn("superseded", actions)
        self.assertTrue(all(facts.get("transition_count") == 1 for facts in lifecycle_facts))
        self.assertTrue(all(facts.get("retention_class") == "maintenance_summary" for facts in lifecycle_facts))
        by_action = {facts.get("lifecycle_action"): facts for facts in lifecycle_facts}
        stale_facts = by_action["stale_preserved"]
        self.assertEqual(stale_facts["audit_sample_bucket"], "stale_preserved")
        self.assertEqual(stale_facts["audit_sample_count"], 1)
        self.assertEqual(stale_facts["audit_samples"][0]["revalidation_lag_days"], 60)
        superseded_facts = by_action["superseded"]
        self.assertEqual(superseded_facts["audit_sample_bucket"], "superseded")
        self.assertEqual(superseded_facts["audit_samples"][0]["replacement_value"], "persistent memory quality evaluation")

    def test_structured_evidence_writes_use_evidence_role_and_archive_retention(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        fake_client = _FakeMemoryClient()
        with patch("spark_intelligence.memory.orchestrator._load_sdk_client", return_value=fake_client):
            result = write_structured_evidence_to_memory(
                config_manager=self.config_manager,
                state_db=self.state_db,
                human_id="human:test",
                evidence_text="Users keep dropping during onboarding because Stripe verification fails.",
                domain_pack="evidence",
                evidence_kind="evidence_marker",
                session_id="session:evidence",
                turn_id="turn:evidence",
                channel_kind="telegram",
            )

        self.assertEqual(result.status, "succeeded")
        self.assertEqual(len(fake_client.observation_calls), 1)
        call = fake_client.observation_calls[0]
        self.assertEqual(call["predicate"], "evidence.telegram.evidence")
        self.assertEqual(call["memory_role"], "structured_evidence")
        self.assertEqual(call["retention_class"], "episodic_archive")
        self.assertEqual(call["metadata"]["evidence_kind"], "evidence_marker")
        self.assertEqual(call["metadata"]["domain_pack"], "evidence")
        self.assertEqual(call["metadata"]["archive_after_days"], 30)
        self.assertTrue(call["metadata"]["archive_at"])
        events = latest_events_by_type(self.state_db, event_type="memory_write_requested", limit=10)
        self.assertTrue(events)
        observations = (events[0]["facts_json"] or {}).get("observations") or []
        self.assertEqual(events[0]["facts_json"].get("memory_role"), "structured_evidence")
        self.assertEqual(observations[0]["predicate"], "evidence.telegram.evidence")
        self.assertEqual(observations[0]["retention_class"], "episodic_archive")

    def test_structured_evidence_write_marks_related_beliefs_invalidated(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        fake_client = _FakeMemoryClient()
        prior_records = [
            {
                "memory_role": "belief",
                "predicate": "belief.telegram.beliefs_and_inferences",
                "text": "I think enterprise teams need hands-on onboarding.",
                "timestamp": "2025-03-01T09:00:00Z",
                "observation_id": "obs-belief-1",
                "metadata": {"value": "I think enterprise teams need hands-on onboarding."},
                "lifecycle": {},
            }
        ]
        with patch("spark_intelligence.memory.orchestrator._load_sdk_client", return_value=fake_client), patch(
            "spark_intelligence.memory.orchestrator.retrieve_memory_evidence_in_memory",
            return_value=SimpleNamespace(
                read_result=SimpleNamespace(
                    abstained=False,
                    records=prior_records,
                )
            ),
        ):
            result = write_structured_evidence_to_memory(
                config_manager=self.config_manager,
                state_db=self.state_db,
                human_id="human:test",
                evidence_text="Users keep needing hands-on onboarding support because enterprise teams ask for setup help.",
                domain_pack="evidence",
                evidence_kind="evidence_marker",
                session_id="session:evidence:2",
                turn_id="turn:evidence:2",
                channel_kind="telegram",
            )

        self.assertEqual(result.status, "succeeded")
        call = fake_client.observation_calls[0]
        self.assertEqual(call["conflicts_with"], ["obs-belief-1"])
        self.assertEqual(call["metadata"]["belief_lifecycle_action"], "invalidated")
        self.assertEqual(call["metadata"]["invalidated_belief_ids"], ["obs-belief-1"])
        events = latest_events_by_type(self.state_db, event_type="memory_write_requested", limit=10)
        self.assertTrue(events)
        observations = (events[0]["facts_json"] or {}).get("observations") or []
        self.assertEqual(observations[0]["conflicts_with"], ["obs-belief-1"])
        self.assertEqual(observations[0]["belief_lifecycle_action"], "invalidated")

    def test_structured_evidence_write_promotes_corroborated_belief(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        fake_client = _FakeMemoryClient()
        prior_evidence_records = [
            {
                "memory_role": "structured_evidence",
                "predicate": "evidence.telegram.evidence",
                "text": "Users keep dropping during onboarding because Stripe verification fails.",
                "timestamp": "2025-03-01T09:00:00Z",
                "observation_id": "obs-evidence-1",
                "metadata": {"value": "Users keep dropping during onboarding because Stripe verification fails."},
                "lifecycle": {},
            }
        ]
        retrieve_results = [
            SimpleNamespace(read_result=SimpleNamespace(abstained=False, records=[])),
            SimpleNamespace(read_result=SimpleNamespace(abstained=False, records=prior_evidence_records)),
        ]
        with patch("spark_intelligence.memory.orchestrator._load_sdk_client", return_value=fake_client), patch(
            "spark_intelligence.memory.orchestrator.retrieve_memory_evidence_in_memory",
            side_effect=retrieve_results,
        ), patch(
            "spark_intelligence.memory.orchestrator.write_belief_to_memory",
            return_value=memory_orchestrator.MemoryWriteResult(
                status="succeeded",
                operation="create",
                method="write_observation",
                memory_role="belief",
                accepted_count=1,
                rejected_count=0,
                skipped_count=0,
                abstained=False,
                retrieval_trace=None,
                provenance=[],
                reason=None,
            ),
        ) as mocked_belief_write:
            result = write_structured_evidence_to_memory(
                config_manager=self.config_manager,
                state_db=self.state_db,
                human_id="human:test",
                evidence_text="Users still drop during onboarding because Stripe verification fails and the retry flow is confusing.",
                domain_pack="evidence",
                evidence_kind="evidence_marker",
                session_id="session:evidence:consolidation",
                turn_id="turn:evidence:consolidation",
                channel_kind="telegram",
            )

        self.assertEqual(result.status, "succeeded")
        mocked_belief_write.assert_called_once()
        belief_kwargs = mocked_belief_write.call_args.kwargs
        self.assertEqual(belief_kwargs["belief_kind"], "evidence_consolidation")
        self.assertEqual(belief_kwargs["domain_pack"], "evidence_onboarding_stripe_verification")
        self.assertIn("I think users still drop during onboarding", belief_kwargs["belief_text"])

    def test_structured_evidence_write_promotes_corroborated_current_blocker(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        fake_client = _FakeMemoryClient()
        prior_evidence_records = [
            {
                "memory_role": "structured_evidence",
                "predicate": "evidence.telegram.evidence",
                "text": "Users keep dropping during onboarding because Stripe verification fails.",
                "timestamp": "2025-03-01T09:00:00Z",
                "observation_id": "obs-evidence-1",
                "metadata": {"value": "Users keep dropping during onboarding because Stripe verification fails."},
                "lifecycle": {},
            }
        ]
        retrieve_results = [
            SimpleNamespace(read_result=SimpleNamespace(abstained=False, records=[])),
            SimpleNamespace(read_result=SimpleNamespace(abstained=False, records=prior_evidence_records)),
        ]
        with patch("spark_intelligence.memory.orchestrator._load_sdk_client", return_value=fake_client), patch(
            "spark_intelligence.memory.orchestrator.retrieve_memory_evidence_in_memory",
            side_effect=retrieve_results,
        ), patch(
            "spark_intelligence.memory.orchestrator.write_belief_to_memory",
            return_value=memory_orchestrator.MemoryWriteResult(
                status="succeeded",
                operation="create",
                method="write_observation",
                memory_role="belief",
                accepted_count=1,
                rejected_count=0,
                skipped_count=0,
                abstained=False,
                retrieval_trace=None,
                provenance=[],
                reason=None,
            ),
        ):
            result = write_structured_evidence_to_memory(
                config_manager=self.config_manager,
                state_db=self.state_db,
                human_id="human:test",
                evidence_text="Users still drop during onboarding because Stripe verification fails and the retry flow is confusing.",
                domain_pack="evidence",
                evidence_kind="evidence_marker",
                session_id="session:evidence:current-state",
                turn_id="turn:evidence:current-state",
                channel_kind="telegram",
            )

        self.assertEqual(result.status, "succeeded")
        current_state_calls = [
            call for call in fake_client.observation_calls if call.get("predicate") == "profile.current_blocker"
        ]
        self.assertEqual(len(current_state_calls), 1)
        promoted_call = current_state_calls[0]
        self.assertEqual(promoted_call["memory_role"], "current_state")
        self.assertEqual(promoted_call["retention_class"], "active_state")
        self.assertEqual(promoted_call["value"], "Stripe verification fails and the retry flow is confusing")
        self.assertEqual(promoted_call["metadata"]["fact_name"], "current_blocker")

    def test_structured_evidence_write_promotes_corroborated_current_dependency(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        fake_client = _FakeMemoryClient()
        prior_evidence_records = [
            {
                "memory_role": "structured_evidence",
                "predicate": "evidence.telegram.evidence",
                "text": "Users keep getting stuck during onboarding because we're waiting on Stripe approval.",
                "timestamp": "2025-03-01T09:00:00Z",
                "observation_id": "obs-evidence-dependency-1",
                "metadata": {"value": "Users keep getting stuck during onboarding because we're waiting on Stripe approval."},
                "lifecycle": {},
            }
        ]
        retrieve_results = [
            SimpleNamespace(read_result=SimpleNamespace(abstained=False, records=[])),
            SimpleNamespace(read_result=SimpleNamespace(abstained=False, records=prior_evidence_records)),
        ]
        with patch("spark_intelligence.memory.orchestrator._load_sdk_client", return_value=fake_client), patch(
            "spark_intelligence.memory.orchestrator.retrieve_memory_evidence_in_memory",
            side_effect=retrieve_results,
        ), patch(
            "spark_intelligence.memory.orchestrator.write_belief_to_memory",
            return_value=memory_orchestrator.MemoryWriteResult(
                status="succeeded",
                operation="create",
                method="write_observation",
                memory_role="belief",
                accepted_count=1,
                rejected_count=0,
                skipped_count=0,
                abstained=False,
                retrieval_trace=None,
                provenance=[],
                reason=None,
            ),
        ):
            result = write_structured_evidence_to_memory(
                config_manager=self.config_manager,
                state_db=self.state_db,
                human_id="human:test",
                evidence_text="Users still get stuck during onboarding because we're waiting on Stripe approval and review is slow.",
                domain_pack="evidence",
                evidence_kind="evidence_marker",
                session_id="session:evidence:dependency",
                turn_id="turn:evidence:dependency",
                channel_kind="telegram",
            )

        self.assertEqual(result.status, "succeeded")
        current_state_calls = [
            call for call in fake_client.observation_calls if call.get("predicate") == "profile.current_dependency"
        ]
        self.assertEqual(len(current_state_calls), 1)
        promoted_call = current_state_calls[0]
        self.assertEqual(promoted_call["memory_role"], "current_state")
        self.assertEqual(promoted_call["retention_class"], "active_state")
        self.assertEqual(promoted_call["value"], "Stripe approval and review is slow")
        self.assertEqual(promoted_call["metadata"]["fact_name"], "current_dependency")

    def test_structured_evidence_write_promotes_corroborated_current_constraint(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        fake_client = _FakeMemoryClient()
        prior_evidence_records = [
            {
                "memory_role": "structured_evidence",
                "predicate": "evidence.telegram.evidence",
                "text": "Users keep waiting during onboarding because we're limited by founder bandwidth.",
                "timestamp": "2025-03-01T09:00:00Z",
                "observation_id": "obs-evidence-constraint-1",
                "metadata": {"value": "Users keep waiting during onboarding because we're limited by founder bandwidth."},
                "lifecycle": {},
            }
        ]
        retrieve_results = [
            SimpleNamespace(read_result=SimpleNamespace(abstained=False, records=[])),
            SimpleNamespace(read_result=SimpleNamespace(abstained=False, records=prior_evidence_records)),
        ]
        with patch("spark_intelligence.memory.orchestrator._load_sdk_client", return_value=fake_client), patch(
            "spark_intelligence.memory.orchestrator.retrieve_memory_evidence_in_memory",
            side_effect=retrieve_results,
        ), patch(
            "spark_intelligence.memory.orchestrator.write_belief_to_memory",
            return_value=memory_orchestrator.MemoryWriteResult(
                status="succeeded",
                operation="create",
                method="write_observation",
                memory_role="belief",
                accepted_count=1,
                rejected_count=0,
                skipped_count=0,
                abstained=False,
                retrieval_trace=None,
                provenance=[],
                reason=None,
            ),
        ):
            result = write_structured_evidence_to_memory(
                config_manager=self.config_manager,
                state_db=self.state_db,
                human_id="human:test",
                evidence_text="Users still wait during onboarding because we're limited by founder bandwidth.",
                domain_pack="evidence",
                evidence_kind="evidence_marker",
                session_id="session:evidence:constraint",
                turn_id="turn:evidence:constraint",
                channel_kind="telegram",
            )

        self.assertEqual(result.status, "succeeded")
        current_state_calls = [
            call for call in fake_client.observation_calls if call.get("predicate") == "profile.current_constraint"
        ]
        self.assertEqual(len(current_state_calls), 1)
        promoted_call = current_state_calls[0]
        self.assertEqual(promoted_call["memory_role"], "current_state")
        self.assertEqual(promoted_call["retention_class"], "active_state")
        self.assertEqual(promoted_call["value"], "founder bandwidth")
        self.assertEqual(promoted_call["metadata"]["fact_name"], "current_constraint")

    def test_structured_evidence_write_promotes_corroborated_current_risk(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        fake_client = _FakeMemoryClient()
        prior_evidence_records = [
            {
                "memory_role": "structured_evidence",
                "predicate": "evidence.telegram.evidence",
                "text": "There is still a risk of enterprise churn during onboarding because activation is weak.",
                "timestamp": "2025-03-01T09:00:00Z",
                "observation_id": "obs-evidence-risk-1",
                "metadata": {"value": "There is still a risk of enterprise churn during onboarding because activation is weak."},
                "lifecycle": {},
            }
        ]
        retrieve_results = [
            SimpleNamespace(read_result=SimpleNamespace(abstained=False, records=[])),
            SimpleNamespace(read_result=SimpleNamespace(abstained=False, records=prior_evidence_records)),
        ]
        with patch("spark_intelligence.memory.orchestrator._load_sdk_client", return_value=fake_client), patch(
            "spark_intelligence.memory.orchestrator.retrieve_memory_evidence_in_memory",
            side_effect=retrieve_results,
        ), patch(
            "spark_intelligence.memory.orchestrator.write_belief_to_memory",
            return_value=memory_orchestrator.MemoryWriteResult(
                status="succeeded",
                operation="create",
                method="write_observation",
                memory_role="belief",
                accepted_count=1,
                rejected_count=0,
                skipped_count=0,
                abstained=False,
                retrieval_trace=None,
                provenance=[],
                reason=None,
            ),
        ):
            result = write_structured_evidence_to_memory(
                config_manager=self.config_manager,
                state_db=self.state_db,
                human_id="human:test",
                evidence_text="There is still a risk of enterprise churn during onboarding because activation is weak and teams are delaying rollout.",
                domain_pack="evidence",
                evidence_kind="evidence_marker",
                session_id="session:evidence:risk",
                turn_id="turn:evidence:risk",
                channel_kind="telegram",
            )

        self.assertEqual(result.status, "succeeded")
        current_state_calls = [
            call for call in fake_client.observation_calls if call.get("predicate") == "profile.current_risk"
        ]
        self.assertEqual(len(current_state_calls), 1)
        promoted_call = current_state_calls[0]
        self.assertEqual(promoted_call["memory_role"], "current_state")
        self.assertEqual(promoted_call["retention_class"], "active_state")
        self.assertEqual(promoted_call["value"], "enterprise churn during onboarding")
        self.assertEqual(promoted_call["metadata"]["fact_name"], "current_risk")

    def test_structured_evidence_write_promotes_corroborated_current_owner(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        fake_client = _FakeMemoryClient()
        prior_evidence_records = [
            {
                "memory_role": "structured_evidence",
                "predicate": "evidence.telegram.evidence",
                "text": "The onboarding rollout is currently owned by Nadia.",
                "timestamp": "2025-03-01T09:00:00Z",
                "observation_id": "obs-evidence-owner-1",
                "metadata": {"value": "The onboarding rollout is currently owned by Nadia."},
                "lifecycle": {},
            }
        ]
        retrieve_results = [
            SimpleNamespace(read_result=SimpleNamespace(abstained=False, records=[])),
            SimpleNamespace(read_result=SimpleNamespace(abstained=False, records=prior_evidence_records)),
        ]
        with patch("spark_intelligence.memory.orchestrator._load_sdk_client", return_value=fake_client), patch(
            "spark_intelligence.memory.orchestrator.retrieve_memory_evidence_in_memory",
            side_effect=retrieve_results,
        ), patch(
            "spark_intelligence.memory.orchestrator.write_belief_to_memory",
            return_value=memory_orchestrator.MemoryWriteResult(
                status="succeeded",
                operation="create",
                method="write_observation",
                memory_role="belief",
                accepted_count=1,
                rejected_count=0,
                skipped_count=0,
                abstained=False,
                retrieval_trace=None,
                provenance=[],
                reason=None,
            ),
        ):
            result = write_structured_evidence_to_memory(
                config_manager=self.config_manager,
                state_db=self.state_db,
                human_id="human:test",
                evidence_text="The onboarding rollout is still owned by Nadia during security review.",
                domain_pack="evidence",
                evidence_kind="evidence_marker",
                session_id="session:evidence:owner",
                turn_id="turn:evidence:owner",
                channel_kind="telegram",
            )

        self.assertEqual(result.status, "succeeded")
        current_state_calls = [
            call for call in fake_client.observation_calls if call.get("predicate") == "profile.current_owner"
        ]
        self.assertEqual(len(current_state_calls), 1)
        promoted_call = current_state_calls[0]
        self.assertEqual(promoted_call["memory_role"], "current_state")
        self.assertEqual(promoted_call["retention_class"], "active_state")
        self.assertEqual(promoted_call["value"], "Nadia")
        self.assertEqual(promoted_call["metadata"]["fact_name"], "current_owner")

    def test_structured_evidence_write_does_not_promote_uncorroborated_current_dependency(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        fake_client = _FakeMemoryClient()
        retrieve_results = [
            SimpleNamespace(read_result=SimpleNamespace(abstained=False, records=[])),
            SimpleNamespace(read_result=SimpleNamespace(abstained=False, records=[])),
        ]
        with patch("spark_intelligence.memory.orchestrator._load_sdk_client", return_value=fake_client), patch(
            "spark_intelligence.memory.orchestrator.retrieve_memory_evidence_in_memory",
            side_effect=retrieve_results,
        ), patch(
            "spark_intelligence.memory.orchestrator.write_belief_to_memory",
            return_value=memory_orchestrator.MemoryWriteResult(
                status="succeeded",
                operation="create",
                method="write_observation",
                memory_role="belief",
                accepted_count=1,
                rejected_count=0,
                skipped_count=0,
                abstained=False,
                retrieval_trace=None,
                provenance=[],
                reason=None,
            ),
        ) as mocked_belief_write, patch(
            "spark_intelligence.memory.orchestrator.write_profile_fact_to_memory",
            return_value=memory_orchestrator.MemoryWriteResult(
                status="succeeded",
                operation="update",
                method="write_observation",
                memory_role="current_state",
                accepted_count=1,
                rejected_count=0,
                skipped_count=0,
                abstained=False,
                retrieval_trace=None,
                provenance=[],
                reason=None,
            ),
        ) as mocked_profile_write:
            result = write_structured_evidence_to_memory(
                config_manager=self.config_manager,
                state_db=self.state_db,
                human_id="human:test",
                evidence_text="Users keep getting stuck during onboarding because we're waiting on Stripe approval.",
                domain_pack="evidence",
                evidence_kind="evidence_marker",
                session_id="session:evidence:dependency:single",
                turn_id="turn:evidence:dependency:single",
                channel_kind="telegram",
            )

        self.assertEqual(result.status, "succeeded")
        mocked_belief_write.assert_not_called()
        mocked_profile_write.assert_not_called()

    def test_structured_evidence_write_promotes_high_confidence_current_status_without_corroboration(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        fake_client = _FakeMemoryClient()
        retrieve_results = [
            SimpleNamespace(read_result=SimpleNamespace(abstained=False, records=[])),
            SimpleNamespace(read_result=SimpleNamespace(abstained=False, records=[])),
        ]
        with patch("spark_intelligence.memory.orchestrator._load_sdk_client", return_value=fake_client), patch(
            "spark_intelligence.memory.orchestrator.retrieve_memory_evidence_in_memory",
            side_effect=retrieve_results,
        ), patch(
            "spark_intelligence.memory.orchestrator.write_belief_to_memory",
            return_value=memory_orchestrator.MemoryWriteResult(
                status="succeeded",
                operation="create",
                method="write_observation",
                memory_role="belief",
                accepted_count=1,
                rejected_count=0,
                skipped_count=0,
                abstained=False,
                retrieval_trace=None,
                provenance=[],
                reason=None,
            ),
        ) as mocked_belief_write, patch(
            "spark_intelligence.memory.orchestrator.write_profile_fact_to_memory",
            return_value=memory_orchestrator.MemoryWriteResult(
                status="succeeded",
                operation="update",
                method="write_observation",
                memory_role="current_state",
                accepted_count=1,
                rejected_count=0,
                skipped_count=0,
                abstained=False,
                retrieval_trace=None,
                provenance=[],
                reason=None,
            ),
        ) as mocked_profile_write:
            result = write_structured_evidence_to_memory(
                config_manager=self.config_manager,
                state_db=self.state_db,
                human_id="human:test",
                evidence_text="Status update: pending security review for the onboarding rollout.",
                domain_pack="evidence",
                evidence_kind="evidence_marker",
                session_id="session:evidence:status:single",
                turn_id="turn:evidence:status:single",
                channel_kind="telegram",
            )

        self.assertEqual(result.status, "succeeded")
        mocked_belief_write.assert_not_called()
        mocked_profile_write.assert_called_once()
        promoted_kwargs = mocked_profile_write.call_args.kwargs
        self.assertEqual(promoted_kwargs["predicate"], "profile.current_status")
        self.assertEqual(promoted_kwargs["value"], "pending security review for the onboarding rollout")
        self.assertEqual(promoted_kwargs["fact_name"], "current_status")

    def test_structured_evidence_write_promotes_high_confidence_project_state_fields_without_corroboration(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        cases = (
            (
                "Customer interviews confirm our plan is to simplify onboarding approvals.",
                "profile.current_plan",
                "simplify onboarding approvals",
                "current_plan",
            ),
            (
                "Interview notes show our priority is onboarding reliability.",
                "profile.current_focus",
                "onboarding reliability",
                "current_focus",
            ),
            (
                "After testing both flows, our decision is to keep human onboarding support.",
                "profile.current_decision",
                "keep human onboarding support",
                "current_decision",
            ),
            (
                "The weekly review says our commitment is to ship onboarding fixes this week.",
                "profile.current_commitment",
                "ship onboarding fixes this week",
                "current_commitment",
            ),
            (
                "Roadmap notes confirm our next milestone is launch the self-serve onboarding beta.",
                "profile.current_milestone",
                "launch the self-serve onboarding beta",
                "current_milestone",
            ),
            (
                "Interview notes suggest our assumption is enterprise teams want hands-on onboarding.",
                "profile.current_assumption",
                "enterprise teams want hands-on onboarding",
                "current_assumption",
            ),
        )

        for index, (evidence_text, expected_predicate, expected_value, expected_fact_name) in enumerate(cases, start=1):
            with self.subTest(predicate=expected_predicate):
                fake_client = _FakeMemoryClient()
                retrieve_results = [
                    SimpleNamespace(read_result=SimpleNamespace(abstained=False, records=[])),
                    SimpleNamespace(read_result=SimpleNamespace(abstained=False, records=[])),
                ]
                with patch("spark_intelligence.memory.orchestrator._load_sdk_client", return_value=fake_client), patch(
                    "spark_intelligence.memory.orchestrator.retrieve_memory_evidence_in_memory",
                    side_effect=retrieve_results,
                ), patch(
                    "spark_intelligence.memory.orchestrator.write_belief_to_memory",
                    return_value=memory_orchestrator.MemoryWriteResult(
                        status="succeeded",
                        operation="create",
                        method="write_observation",
                        memory_role="belief",
                        accepted_count=1,
                        rejected_count=0,
                        skipped_count=0,
                        abstained=False,
                        retrieval_trace=None,
                        provenance=[],
                        reason=None,
                    ),
                ) as mocked_belief_write, patch(
                    "spark_intelligence.memory.orchestrator.write_profile_fact_to_memory",
                    return_value=memory_orchestrator.MemoryWriteResult(
                        status="succeeded",
                        operation="update",
                        method="write_observation",
                        memory_role="current_state",
                        accepted_count=1,
                        rejected_count=0,
                        skipped_count=0,
                        abstained=False,
                        retrieval_trace=None,
                        provenance=[],
                        reason=None,
                    ),
                ) as mocked_profile_write:
                    result = write_structured_evidence_to_memory(
                        config_manager=self.config_manager,
                        state_db=self.state_db,
                        human_id=f"human:test:{index}",
                        evidence_text=evidence_text,
                        domain_pack="evidence",
                        evidence_kind="evidence_marker",
                        session_id=f"session:evidence:project-state:{index}",
                        turn_id=f"turn:evidence:project-state:{index}",
                        channel_kind="telegram",
                    )

                self.assertEqual(result.status, "succeeded")
                mocked_belief_write.assert_not_called()
                mocked_profile_write.assert_called_once()
                promoted_kwargs = mocked_profile_write.call_args.kwargs
                self.assertEqual(promoted_kwargs["predicate"], expected_predicate)
                self.assertEqual(promoted_kwargs["value"], expected_value)
                self.assertEqual(promoted_kwargs["fact_name"], expected_fact_name)

    def test_raw_episode_writes_use_raw_turn_predicate_and_archive_retention(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        fake_client = _FakeMemoryClient()
        with patch("spark_intelligence.memory.orchestrator._load_sdk_client", return_value=fake_client):
            result = write_raw_episode_to_memory(
                config_manager=self.config_manager,
                state_db=self.state_db,
                human_id="human:test",
                episode_text="The pricing page felt confusing during the demo.",
                domain_pack="raw_episode",
                session_id="session:raw",
                turn_id="turn:raw",
                channel_kind="telegram",
            )

        self.assertEqual(result.status, "succeeded")
        self.assertEqual(len(fake_client.observation_calls), 1)
        call = fake_client.observation_calls[0]
        self.assertEqual(call["predicate"], "raw_turn")
        self.assertEqual(call["memory_role"], "episodic")
        self.assertEqual(call["retention_class"], "episodic_archive")
        self.assertTrue(call["metadata"]["raw_episode"])
        self.assertEqual(call["metadata"]["archive_after_days"], 14)
        self.assertTrue(call["metadata"]["archive_at"])
        events = latest_events_by_type(self.state_db, event_type="memory_write_requested", limit=10)
        self.assertTrue(events)
        observations = (events[0]["facts_json"] or {}).get("observations") or []
        self.assertEqual(events[0]["facts_json"].get("memory_role"), "episodic")
        self.assertEqual(observations[0]["predicate"], "raw_turn")
        self.assertEqual(observations[0]["retention_class"], "episodic_archive")

    def test_archive_raw_episode_from_memory_writes_delete_tombstone(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        fake_client = _FakeMemoryClient()
        with patch("spark_intelligence.memory.orchestrator._load_sdk_client", return_value=fake_client):
            result = archive_raw_episode_from_memory(
                config_manager=self.config_manager,
                state_db=self.state_db,
                human_id="human:test",
                episode_text="The pricing page felt confusing during the demo.",
                raw_episode_observation_id="obs-episode-1",
                archive_reason="covered_by_newer_structured_evidence",
                session_id="session:raw:archive",
                turn_id="turn:raw:archive",
                channel_kind="telegram",
            )

        self.assertEqual(result.status, "succeeded")
        call = fake_client.observation_calls[0]
        self.assertEqual(call["operation"], "delete")
        self.assertEqual(call["predicate"], "raw_turn")
        self.assertEqual(call["supersedes"], "obs-episode-1")
        self.assertTrue(call["metadata"]["raw_episode"])
        self.assertEqual(call["metadata"]["raw_episode_lifecycle_action"], "archived")
        self.assertEqual(call["metadata"]["archive_reason"], "covered_by_newer_structured_evidence")
        events = latest_events_by_type(self.state_db, event_type="memory_write_requested", limit=10)
        self.assertTrue(events)
        observations = (events[0]["facts_json"] or {}).get("observations") or []
        self.assertEqual(observations[0]["operation"], "delete")
        self.assertEqual(observations[0]["raw_episode_lifecycle_action"], "archived")
        lifecycle_events = latest_events_by_type(self.state_db, event_type="memory_lifecycle_transition", limit=10)
        self.assertTrue(lifecycle_events)
        lifecycle_facts = lifecycle_events[0]["facts_json"] or {}
        self.assertEqual(lifecycle_facts["transition_kind"], "archive")
        self.assertEqual(lifecycle_facts["memory_role"], "episodic")
        self.assertEqual(lifecycle_facts["source_predicate"], "raw_turn")
        self.assertEqual(lifecycle_facts["source_observation_id"], "obs-episode-1")
        self.assertEqual(lifecycle_facts["archive_reason"], "covered_by_newer_structured_evidence")
        self.assertEqual(lifecycle_facts["destination"], "archive_tombstone")
        self.assertIn("pricing page felt confusing", lifecycle_facts["source_text"])

    def test_archive_structured_evidence_from_memory_writes_delete_tombstone(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        fake_client = _FakeMemoryClient()
        with patch("spark_intelligence.memory.orchestrator._load_sdk_client", return_value=fake_client):
            result = archive_structured_evidence_from_memory(
                config_manager=self.config_manager,
                state_db=self.state_db,
                human_id="human:test",
                predicate="evidence.telegram.evidence",
                evidence_text="Users keep dropping during onboarding because Stripe verification fails.",
                evidence_observation_id="obs-evidence-1",
                archive_reason="eclipsed_by_newer_structured_evidence",
                session_id="session:evidence:archive",
                turn_id="turn:evidence:archive",
                channel_kind="telegram",
            )

        self.assertEqual(result.status, "succeeded")
        call = fake_client.observation_calls[0]
        self.assertEqual(call["operation"], "delete")
        self.assertEqual(call["predicate"], "evidence.telegram.evidence")
        self.assertEqual(call["supersedes"], "obs-evidence-1")
        self.assertEqual(call["metadata"]["structured_evidence_lifecycle_action"], "archived")
        self.assertEqual(call["metadata"]["archive_reason"], "eclipsed_by_newer_structured_evidence")
        events = latest_events_by_type(self.state_db, event_type="memory_write_requested", limit=10)
        self.assertTrue(events)
        observations = (events[0]["facts_json"] or {}).get("observations") or []
        self.assertEqual(observations[0]["operation"], "delete")
        self.assertEqual(observations[0]["structured_evidence_lifecycle_action"], "archived")
        lifecycle_events = latest_events_by_type(self.state_db, event_type="memory_lifecycle_transition", limit=10)
        self.assertTrue(lifecycle_events)
        lifecycle_facts = lifecycle_events[0]["facts_json"] or {}
        self.assertEqual(lifecycle_facts["transition_kind"], "archive")
        self.assertEqual(lifecycle_facts["memory_role"], "structured_evidence")
        self.assertEqual(lifecycle_facts["source_predicate"], "evidence.telegram.evidence")
        self.assertEqual(lifecycle_facts["source_observation_id"], "obs-evidence-1")
        self.assertEqual(lifecycle_facts["archive_reason"], "eclipsed_by_newer_structured_evidence")
        self.assertEqual(lifecycle_facts["retention_class"], "episodic_archive")
        self.assertIn("Stripe verification fails", lifecycle_facts["source_text"])

    def test_belief_writes_use_belief_role_and_derived_retention(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        fake_client = _FakeMemoryClient()
        with patch("spark_intelligence.memory.orchestrator._load_sdk_client", return_value=fake_client):
            result = write_belief_to_memory(
                config_manager=self.config_manager,
                state_db=self.state_db,
                human_id="human:test",
                belief_text="I think enterprise teams need hands-on onboarding.",
                domain_pack="beliefs_and_inferences",
                belief_kind="belief_marker",
                session_id="session:belief",
                turn_id="turn:belief",
                channel_kind="telegram",
            )

        self.assertEqual(result.status, "succeeded")
        self.assertEqual(len(fake_client.observation_calls), 1)
        call = fake_client.observation_calls[0]
        self.assertEqual(call["predicate"], "belief.telegram.beliefs_and_inferences")
        self.assertEqual(call["memory_role"], "belief")
        self.assertEqual(call["retention_class"], "derived_belief")
        self.assertEqual(call["metadata"]["belief_kind"], "belief_marker")
        self.assertEqual(call["metadata"]["revalidate_after_days"], 30)
        self.assertTrue(call["metadata"]["revalidate_at"])
        events = latest_events_by_type(self.state_db, event_type="memory_write_requested", limit=10)
        self.assertTrue(events)
        observations = (events[0]["facts_json"] or {}).get("observations") or []
        self.assertEqual(events[0]["facts_json"].get("memory_role"), "belief")
        self.assertEqual(observations[0]["predicate"], "belief.telegram.beliefs_and_inferences")
        self.assertEqual(observations[0]["retention_class"], "derived_belief")

    def test_belief_writes_supersede_latest_active_belief_for_same_pack(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        fake_client = _FakeMemoryClient()
        prior_records = [
            {
                "memory_role": "belief",
                "predicate": "belief.telegram.beliefs_and_inferences",
                "text": "I think self-serve onboarding will work.",
                "timestamp": "2025-03-01T09:00:00Z",
                "observation_id": "obs-belief-1",
                "metadata": {"value": "I think self-serve onboarding will work."},
                "lifecycle": {},
            }
        ]
        with patch("spark_intelligence.memory.orchestrator._load_sdk_client", return_value=fake_client), patch(
            "spark_intelligence.memory.orchestrator.retrieve_memory_evidence_in_memory",
            return_value=SimpleNamespace(
                read_result=SimpleNamespace(
                    abstained=False,
                    records=prior_records,
                )
            ),
        ) as retrieve_mock:
            result = write_belief_to_memory(
                config_manager=self.config_manager,
                state_db=self.state_db,
                human_id="human:test",
                belief_text="I think enterprise teams need hands-on onboarding.",
                domain_pack="beliefs_and_inferences",
                belief_kind="belief_marker",
                session_id="session:belief:2",
                turn_id="turn:belief:2",
                channel_kind="telegram",
            )

        self.assertEqual(result.status, "succeeded")
        self.assertFalse(retrieve_mock.call_args.kwargs["record_activity"])
        call = fake_client.observation_calls[0]
        self.assertEqual(call["supersedes"], "obs-belief-1")
        self.assertEqual(call["conflicts_with"], ["obs-belief-1"])
        self.assertEqual(call["metadata"]["previous_belief_text"], "I think self-serve onboarding will work.")
        events = latest_events_by_type(self.state_db, event_type="memory_write_requested", limit=10)
        self.assertTrue(events)
        observations = (events[0]["facts_json"] or {}).get("observations") or []
        self.assertEqual(observations[0]["supersedes"], "obs-belief-1")
        self.assertEqual(observations[0]["conflicts_with"], ["obs-belief-1"])
        lifecycle_events = latest_events_by_type(self.state_db, event_type="memory_lifecycle_transition", limit=10)
        self.assertTrue(lifecycle_events)
        lifecycle_facts = lifecycle_events[0]["facts_json"] or {}
        self.assertEqual(lifecycle_facts["transition_kind"], "supersession")
        self.assertEqual(lifecycle_facts["memory_role"], "belief")
        self.assertEqual(lifecycle_facts["source_observation_id"], "obs-belief-1")
        self.assertEqual(lifecycle_facts["old_value"], "I think self-serve onboarding will work.")
        self.assertEqual(lifecycle_facts["new_value"], "I think enterprise teams need hands-on onboarding.")
        self.assertEqual(lifecycle_facts["destination"], "historical_belief_state")

    def test_archive_belief_from_memory_writes_delete_tombstone(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        fake_client = _FakeMemoryClient()
        with patch("spark_intelligence.memory.orchestrator._load_sdk_client", return_value=fake_client):
            result = archive_belief_from_memory(
                config_manager=self.config_manager,
                state_db=self.state_db,
                human_id="human:test",
                predicate="belief.telegram.beliefs_and_inferences",
                belief_text="I think enterprise teams need hands-on onboarding.",
                belief_observation_id="obs-belief-1",
                archive_reason="invalidated_and_past_revalidation",
                session_id="session:belief:archive",
                turn_id="turn:belief:archive",
                channel_kind="telegram",
            )

        self.assertEqual(result.status, "succeeded")
        call = fake_client.observation_calls[0]
        self.assertEqual(call["operation"], "delete")
        self.assertEqual(call["predicate"], "belief.telegram.beliefs_and_inferences")
        self.assertEqual(call["supersedes"], "obs-belief-1")
        self.assertEqual(call["metadata"]["belief_lifecycle_action"], "archived")
        self.assertEqual(call["metadata"]["archive_reason"], "invalidated_and_past_revalidation")
        events = latest_events_by_type(self.state_db, event_type="memory_write_requested", limit=10)
        self.assertTrue(events)
        observations = (events[0]["facts_json"] or {}).get("observations") or []
        self.assertEqual(observations[0]["operation"], "delete")
        self.assertEqual(observations[0]["belief_lifecycle_action"], "archived")
        lifecycle_events = latest_events_by_type(self.state_db, event_type="memory_lifecycle_transition", limit=10)
        self.assertTrue(lifecycle_events)
        lifecycle_facts = lifecycle_events[0]["facts_json"] or {}
        self.assertEqual(lifecycle_facts["transition_kind"], "archive")
        self.assertEqual(lifecycle_facts["memory_role"], "belief")
        self.assertEqual(lifecycle_facts["source_predicate"], "belief.telegram.beliefs_and_inferences")
        self.assertEqual(lifecycle_facts["source_observation_id"], "obs-belief-1")
        self.assertEqual(lifecycle_facts["archive_reason"], "invalidated_and_past_revalidation")
        self.assertEqual(lifecycle_facts["destination"], "archive_tombstone")
        self.assertIn("hands-on onboarding", lifecycle_facts["source_text"])

    def test_telegram_event_detection_write_and_answer_use_event_memory_lane(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)
        detected = detect_telegram_memory_event_observation("My meeting with Omar is on May 3.")
        self.assertIsNotNone(detected)
        assert detected is not None
        self.assertEqual(detected.predicate, "telegram.event.meeting")
        self.assertEqual(detected.value, "meeting with Omar on May 3")

        fake_client = _FakeMemoryClient()
        with patch("spark_intelligence.memory.orchestrator._load_sdk_client", return_value=fake_client):
            result = write_telegram_event_to_memory(
                config_manager=self.config_manager,
                state_db=self.state_db,
                human_id="human:test",
                predicate=detected.predicate,
                value=detected.value,
                evidence_text=detected.evidence_text,
                event_name=detected.event_name,
                session_id="session:event",
                turn_id="turn:event",
                channel_kind="telegram",
            )

        self.assertEqual(result.status, "succeeded")
        self.assertEqual(len(fake_client.event_calls), 1)
        self.assertEqual(len(fake_client.observation_calls), 1)
        call = fake_client.event_calls[0]
        self.assertEqual(call["subject"], "human:test")
        self.assertEqual(call["predicate"], "telegram.event.meeting")
        self.assertEqual(call["value"], "meeting with Omar on May 3")
        self.assertEqual(call["text"], "My meeting with Omar is on May 3.")
        self.assertEqual(call["retention_class"], "time_bound_event")
        self.assertTrue(call["document_time"])
        self.assertTrue(call["valid_from"])
        self.assertEqual(call["metadata"]["promotion_disposition"], "promote_structured_evidence")
        self.assertIn("detected_structured_fact", call["metadata"]["salience_reasons"])
        summary_call = fake_client.observation_calls[0]
        self.assertEqual(summary_call["predicate"], "telegram.summary.latest_meeting")
        self.assertEqual(summary_call["value"], "meeting with Omar on May 3")
        self.assertEqual(summary_call["metadata"]["entity_key"], "telegram.summary.latest_meeting")
        self.assertEqual(summary_call["retention_class"], "active_state")
        self.assertEqual(summary_call["metadata"]["promotion_stage"], "structured_evidence")
        write_events = latest_events_by_type(self.state_db, event_type="memory_write_requested", limit=10)
        self.assertTrue(write_events)
        recorded_events = (write_events[0]["facts_json"] or {}).get("events") or []
        self.assertEqual(recorded_events[0]["predicate"], "telegram.event.meeting")
        self.assertEqual(recorded_events[0]["value"], "meeting with Omar on May 3")
        self.assertEqual(recorded_events[0]["retention_class"], "time_bound_event")
        self.assertEqual(recorded_events[0]["promotion_disposition"], "promote_structured_evidence")
        self.assertEqual(
            build_telegram_memory_event_observation_answer(observation=detected),
            "I'll remember your meeting with Omar on May 3.",
        )

    def test_telegram_event_salience_gate_blocks_secret_like_memory(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        fake_client = _FakeMemoryClient()
        with patch("spark_intelligence.memory.orchestrator._load_sdk_client", return_value=fake_client):
            result = write_telegram_event_to_memory(
                config_manager=self.config_manager,
                state_db=self.state_db,
                human_id="human:test",
                predicate="telegram.event.meeting",
                value="meeting about api key sk-testsecret123456 on May 3",
                evidence_text="My meeting about api key sk-testsecret123456 is on May 3.",
                event_name="telegram_event_meeting",
                session_id="session:event-secret",
                turn_id="turn:event-secret",
                channel_kind="telegram",
            )

        self.assertEqual(result.status, "skipped")
        self.assertEqual(result.reason, "salience_secret_like_material")
        self.assertEqual(fake_client.event_calls, [])
        self.assertEqual(fake_client.observation_calls, [])
        policy_records = recent_policy_gate_records(self.state_db, limit=10)
        self.assertTrue(policy_records)
        self.assertEqual(policy_records[0]["gate_name"], "memory.salience")

    def test_telegram_event_summary_current_state_overwrites_while_event_history_is_preserved(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        write_telegram_event_to_memory(
            config_manager=self.config_manager,
            state_db=self.state_db,
            human_id="human:test",
            predicate="telegram.event.flight",
            value="flight to London on May 6",
            evidence_text="My flight to London is on May 6.",
            event_name="telegram_event_flight",
            session_id="session:flight:1",
            turn_id="turn:flight:1",
            channel_kind="telegram",
        )
        write_telegram_event_to_memory(
            config_manager=self.config_manager,
            state_db=self.state_db,
            human_id="human:test",
            predicate="telegram.event.flight",
            value="flight to Paris on May 9",
            evidence_text="My flight to Paris is on May 9.",
            event_name="telegram_event_flight",
            session_id="session:flight:2",
            turn_id="turn:flight:2",
            channel_kind="telegram",
        )

        latest_lookup = lookup_current_state_in_memory(
            config_manager=self.config_manager,
            state_db=self.state_db,
            subject="human:test",
            predicate="telegram.summary.latest_flight",
            sdk_module="domain_chip_memory",
        )
        history_lookup = retrieve_memory_events_in_memory(
            config_manager=self.config_manager,
            state_db=self.state_db,
            query="What flight did I mention?",
            subject="human:test",
            predicate="telegram.event.flight",
            sdk_module="domain_chip_memory",
        )
        inspection = inspect_human_memory_in_memory(
            config_manager=self.config_manager,
            state_db=self.state_db,
            human_id="test",
            sdk_module="domain_chip_memory",
        )

        self.assertFalse(latest_lookup.read_result.abstained)
        self.assertEqual(len(latest_lookup.read_result.records), 1)
        self.assertEqual(latest_lookup.read_result.records[0]["value"], "flight to Paris on May 9")
        self.assertEqual(len(history_lookup.read_result.records), 2)
        self.assertEqual(
            [record["value"] for record in history_lookup.read_result.records],
            ["flight to Paris on May 9", "flight to London on May 6"],
        )
        summary_records = [
            record
            for record in inspection.read_result.records
            if record.get("predicate") == "telegram.summary.latest_flight"
        ]
        self.assertEqual(len(summary_records), 1)
        self.assertEqual(summary_records[0]["value"], "flight to Paris on May 9")

    def test_telegram_event_detection_rejects_hypothetical_and_non_temporal_phrasing(self) -> None:
        self.assertIsNone(
            detect_telegram_memory_event_observation("Maybe my meeting with Omar is on May 3.")
        )
        self.assertIsNone(
            detect_telegram_memory_event_observation("What if my meeting with Omar is on May 3?")
        )
        self.assertIsNone(
            detect_telegram_memory_event_observation("My meeting with Omar is on track.")
        )

    def test_profile_city_detection_strips_temporal_tail_words(self) -> None:
        detected = detect_profile_fact_observation("I live in Abu Dhabi now.")
        self.assertIsNotNone(detected)
        assert detected is not None
        self.assertEqual(detected.predicate, "profile.city")
        self.assertEqual(detected.value, "Abu Dhabi")

    def test_profile_startup_detection_strips_temporal_tail_words(self) -> None:
        detected = detect_profile_fact_observation("My startup is Atlas Labs now.")
        self.assertIsNotNone(detected)
        assert detected is not None
        self.assertEqual(detected.predicate, "profile.startup_name")
        self.assertEqual(detected.value, "Atlas Labs")

    def test_profile_startup_detection_accepts_run_phrasing(self) -> None:
        detected = detect_profile_fact_observation("I run Atlas Labs.")
        self.assertIsNotNone(detected)
        assert detected is not None
        self.assertEqual(detected.predicate, "profile.startup_name")
        self.assertEqual(detected.value, "Atlas Labs")

    def test_profile_startup_detection_accepts_reverse_startup_phrasing(self) -> None:
        detected = detect_profile_fact_observation("Atlas Labs is my startup.")
        self.assertIsNotNone(detected)
        assert detected is not None
        self.assertEqual(detected.predicate, "profile.startup_name")
        self.assertEqual(detected.value, "Atlas Labs")

    def test_profile_current_mission_detection_strips_temporal_tail_words(self) -> None:
        detected = detect_profile_fact_observation("I am trying to rebuild the company now.")
        self.assertIsNotNone(detected)
        assert detected is not None
        self.assertEqual(detected.predicate, "profile.current_mission")
        self.assertEqual(detected.value, "rebuild the company")

    def test_profile_name_detection_strips_temporal_tail_words(self) -> None:
        detected = detect_profile_fact_observation("My name is Sarah now.")
        self.assertIsNotNone(detected)
        assert detected is not None
        self.assertEqual(detected.predicate, "profile.preferred_name")
        self.assertEqual(detected.value, "Sarah")

    def test_profile_name_correction_detection_overrides_wrong_prior_name(self) -> None:
        detected = detect_profile_fact_observation("I'm not Maya by the way, I'm Cem.")
        self.assertIsNotNone(detected)
        assert detected is not None
        self.assertEqual(detected.predicate, "profile.preferred_name")
        self.assertEqual(detected.value, "Cem")

    def test_profile_name_correction_detection_handles_not_after_new_name(self) -> None:
        detected = detect_profile_fact_observation("nom my name is Cem not Maya :))")
        self.assertIsNotNone(detected)
        assert detected is not None
        self.assertEqual(detected.predicate, "profile.preferred_name")
        self.assertEqual(detected.value, "Cem")

    def test_profile_occupation_detection_accepts_temporal_tail_words(self) -> None:
        detected = detect_profile_fact_observation("I am an entrepreneur now.")
        self.assertIsNotNone(detected)
        assert detected is not None
        self.assertEqual(detected.predicate, "profile.occupation")
        self.assertEqual(detected.value, "entrepreneur")

    def test_profile_country_detection_accepts_based_in_phrasing(self) -> None:
        detected = detect_profile_fact_observation("I'm based in Canada now.")
        self.assertIsNotNone(detected)
        assert detected is not None
        self.assertEqual(detected.predicate, "profile.home_country")
        self.assertEqual(detected.value, "Canada")

    def test_profile_country_detection_accepts_based_out_of_phrasing(self) -> None:
        detected = detect_profile_fact_observation("I'm based out of Canada.")
        self.assertIsNotNone(detected)
        assert detected is not None
        self.assertEqual(detected.predicate, "profile.home_country")
        self.assertEqual(detected.value, "Canada")

    def test_profile_country_detection_accepts_im_in_country_phrasing(self) -> None:
        detected = detect_profile_fact_observation("I'm in Canada now.")
        self.assertIsNotNone(detected)
        assert detected is not None
        self.assertEqual(detected.predicate, "profile.home_country")
        self.assertEqual(detected.value, "Canada")

    def test_profile_city_detection_keeps_im_in_city_phrasing(self) -> None:
        detected = detect_profile_fact_observation("I'm in Abu Dhabi now.")
        self.assertIsNotNone(detected)
        assert detected is not None
        self.assertEqual(detected.predicate, "profile.city")
        self.assertEqual(detected.value, "Abu Dhabi")

    def test_profile_country_detection_accepts_moved_to_country_phrasing(self) -> None:
        detected = detect_profile_fact_observation("I moved to Canada.")
        self.assertIsNotNone(detected)
        assert detected is not None
        self.assertEqual(detected.predicate, "profile.home_country")
        self.assertEqual(detected.value, "Canada")

    def test_profile_city_detection_keeps_moved_to_city_phrasing(self) -> None:
        detected = detect_profile_fact_observation("I moved to Dubai.")
        self.assertIsNotNone(detected)
        assert detected is not None
        self.assertEqual(detected.predicate, "profile.city")
        self.assertEqual(detected.value, "Dubai")

    def test_profile_country_detection_accepts_live_in_country_phrasing(self) -> None:
        detected = detect_profile_fact_observation("I live in Canada.")
        self.assertIsNotNone(detected)
        assert detected is not None
        self.assertEqual(detected.predicate, "profile.home_country")
        self.assertEqual(detected.value, "Canada")

    def test_profile_country_detection_accepts_live_in_country_aliases(self) -> None:
        uae = detect_profile_fact_observation("I live in UAE.")
        self.assertIsNotNone(uae)
        assert uae is not None
        self.assertEqual(uae.predicate, "profile.home_country")
        self.assertEqual(uae.value, "UAE")

        us = detect_profile_fact_observation("I live in the US.")
        self.assertIsNotNone(us)
        assert us is not None
        self.assertEqual(us.predicate, "profile.home_country")
        self.assertEqual(us.value, "United States")

    def test_profile_city_detection_keeps_live_in_city_phrasing(self) -> None:
        detected = detect_profile_fact_observation("I live in Dubai.")
        self.assertIsNotNone(detected)
        assert detected is not None
        self.assertEqual(detected.predicate, "profile.city")
        self.assertEqual(detected.value, "Dubai")

    def test_profile_country_detection_normalizes_explicit_country_aliases_with_the(self) -> None:
        from_us = detect_profile_fact_observation("I'm from the US.")
        self.assertIsNotNone(from_us)
        assert from_us is not None
        self.assertEqual(from_us.predicate, "profile.home_country")
        self.assertEqual(from_us.value, "United States")

        based_in_us = detect_profile_fact_observation("I'm based in the US.")
        self.assertIsNotNone(based_in_us)
        assert based_in_us is not None
        self.assertEqual(based_in_us.predicate, "profile.home_country")
        self.assertEqual(based_in_us.value, "United States")

        based_out_uk = detect_profile_fact_observation("I'm based out of the UK.")
        self.assertIsNotNone(based_out_uk)
        assert based_out_uk is not None
        self.assertEqual(based_out_uk.predicate, "profile.home_country")
        self.assertEqual(based_out_uk.value, "United Kingdom")

        from_uae = detect_profile_fact_observation("I'm from the UAE.")
        self.assertIsNotNone(from_uae)
        assert from_uae is not None
        self.assertEqual(from_uae.predicate, "profile.home_country")
        self.assertEqual(from_uae.value, "UAE")

    def test_profile_founder_detection_accepts_founded_phrasing(self) -> None:
        detected = detect_profile_fact_observation("I founded Atlas Labs.")
        self.assertIsNotNone(detected)
        assert detected is not None
        self.assertEqual(detected.predicate, "profile.founder_of")
        self.assertEqual(detected.value, "Atlas Labs")

    def test_profile_founder_detection_accepts_started_built_and_launched_phrasing(self) -> None:
        started = detect_profile_fact_observation("I started Atlas Labs.")
        self.assertIsNotNone(started)
        assert started is not None
        self.assertEqual(started.predicate, "profile.founder_of")
        self.assertEqual(started.value, "Atlas Labs")

        built = detect_profile_fact_observation("I built Atlas Labs.")
        self.assertIsNotNone(built)
        assert built is not None
        self.assertEqual(built.predicate, "profile.founder_of")
        self.assertEqual(built.value, "Atlas Labs")

        launched = detect_profile_fact_observation("I launched Atlas Labs.")
        self.assertIsNotNone(launched)
        assert launched is not None
        self.assertEqual(launched.predicate, "profile.founder_of")
        self.assertEqual(launched.value, "Atlas Labs")

    def test_profile_founder_detection_accepts_founded_startup_called_phrasing(self) -> None:
        detected = detect_profile_fact_observation("I founded a startup called Atlas Labs.")
        self.assertIsNotNone(detected)
        assert detected is not None
        self.assertEqual(detected.predicate, "profile.founder_of")
        self.assertEqual(detected.value, "Atlas Labs")

    def test_profile_fact_write_does_not_double_prefix_prefixed_human_id(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        fake_client = _FakeMemoryClient()
        with patch("spark_intelligence.memory.orchestrator._load_sdk_client", return_value=fake_client):
            result = write_profile_fact_to_memory(
                config_manager=self.config_manager,
                state_db=self.state_db,
                human_id="human:telegram:8319079055",
                predicate="profile.startup_name",
                value="Seedify",
                evidence_text="My startup is Seedify.",
                fact_name="profile_startup_name",
                session_id="session:prefixed-human-id",
                turn_id="turn:prefixed-human-id",
                channel_kind="telegram",
            )

        self.assertEqual(result.status, "succeeded")
        self.assertEqual(len(fake_client.observation_calls), 1)
        call = fake_client.observation_calls[0]
        self.assertEqual(call["subject"], "human:telegram:8319079055")

    def test_inspect_human_memory_uses_legacy_double_prefixed_fallback_for_prefixed_human_id(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        class _LegacyFallbackMemoryClient:
            def __init__(self) -> None:
                self.current_state_calls: list[dict[str, object]] = []

            def get_current_state(self, **payload):
                self.current_state_calls.append(payload)
                subject = str(payload.get("subject") or "")
                if subject == "human:telegram:8319079055":
                    return {
                        "status": "not_found",
                        "memory_role": "unknown",
                        "records": [],
                        "provenance": [],
                        "retrieval_trace": {"trace_id": "mem-trace-primary"},
                    }
                if subject == "human:human:telegram:8319079055":
                    return {
                        "status": "supported",
                        "memory_role": "current_state",
                        "records": [
                            {
                                "subject": subject,
                                "predicate": "profile.startup_name",
                                "value": "Seedify",
                            }
                        ],
                        "provenance": [{"memory_role": "current_state", "source": "fake_sdk"}],
                        "retrieval_trace": {"trace_id": "mem-trace-legacy"},
                    }
                raise AssertionError(f"unexpected subject {subject}")

        fake_client = _LegacyFallbackMemoryClient()
        with patch("spark_intelligence.memory.orchestrator._load_sdk_client_for_module", return_value=fake_client):
            result = inspect_human_memory_in_memory(
                config_manager=self.config_manager,
                state_db=self.state_db,
                human_id="human:telegram:8319079055",
                sdk_module="domain_chip_memory",
            )

        self.assertFalse(result.read_result.abstained)
        self.assertTrue(result.read_result.records)
        self.assertEqual(result.read_result.records[0]["predicate"], "profile.startup_name")
        self.assertEqual(
            [str(call.get("subject") or "") for call in fake_client.current_state_calls],
            ["human:telegram:8319079055", "human:human:telegram:8319079055"],
        )

    def test_inspect_memory_sdk_runtime_falls_back_to_local_domain_chip_memory_src(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        local_root = self.home / "domain-chip-memory"
        package_dir = local_root / "src" / "domain_chip_memory"
        package_dir.mkdir(parents=True, exist_ok=True)
        (package_dir / "__init__.py").write_text(
            "class SparkMemorySDK:\n"
            "    def __init__(self):\n"
            "        self.ready = True\n"
            "\n"
            "def build_sdk_contract_summary():\n"
            "    return {\n"
            "        'runtime_class': 'SparkMemorySDK',\n"
            "        'runtime_memory_architecture': 'dual_store_event_calendar_hybrid',\n"
            "        'runtime_memory_provider': 'heuristic_v1',\n"
            "    }\n",
            encoding="utf-8",
        )

        original_module = sys.modules.pop("domain_chip_memory", None)
        try:
            with patch.object(memory_orchestrator, "DEFAULT_DOMAIN_CHIP_MEMORY_ROOT", local_root):
                runtime = memory_orchestrator.inspect_memory_sdk_runtime(
                    config_manager=self.config_manager,
                    sdk_module="domain_chip_memory",
                )
        finally:
            sys.modules.pop("domain_chip_memory", None)
            if original_module is not None:
                sys.modules["domain_chip_memory"] = original_module

        self.assertTrue(runtime["ready"])
        self.assertEqual(runtime["configured_module"], "domain_chip_memory")
        self.assertEqual(runtime["resolved_module"], "domain_chip_memory")
        self.assertEqual(runtime["runtime_class"], "SparkMemorySDK")
        self.assertEqual(runtime["runtime_memory_architecture"], "summary_synthesis_memory")
        self.assertEqual(runtime["runtime_memory_provider"], "heuristic_v1")

    def test_lookup_current_state_uses_legacy_double_prefixed_fallback_for_prefixed_subject(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        class _LegacyLookupMemoryClient:
            def __init__(self) -> None:
                self.current_state_calls: list[dict[str, object]] = []

            def get_current_state(self, **payload):
                self.current_state_calls.append(payload)
                subject = str(payload.get("subject") or "")
                if subject == "human:telegram:8319079055":
                    return {
                        "status": "not_found",
                        "memory_role": "unknown",
                        "records": [],
                        "provenance": [],
                        "retrieval_trace": {"trace_id": "mem-trace-primary"},
                    }
                if subject == "human:human:telegram:8319079055":
                    return {
                        "status": "supported",
                        "memory_role": "current_state",
                        "records": [
                            {
                                "subject": subject,
                                "predicate": "profile.startup_name",
                                "value": "Seedify",
                            }
                        ],
                        "provenance": [{"memory_role": "current_state", "source": "fake_sdk"}],
                        "retrieval_trace": {"trace_id": "mem-trace-legacy"},
                    }
                raise AssertionError(f"unexpected subject {subject}")

        fake_client = _LegacyLookupMemoryClient()
        with patch("spark_intelligence.memory.orchestrator._load_sdk_client_for_module", return_value=fake_client):
            result = lookup_current_state_in_memory(
                config_manager=self.config_manager,
                state_db=self.state_db,
                subject="human:telegram:8319079055",
                predicate="profile.startup_name",
                sdk_module="domain_chip_memory",
            )

        self.assertFalse(result.read_result.abstained)
        self.assertTrue(result.read_result.records)
        self.assertEqual(result.read_result.records[0]["predicate"], "profile.startup_name")
        self.assertEqual(
            [str(call.get("subject") or "") for call in fake_client.current_state_calls],
            ["human:telegram:8319079055", "human:human:telegram:8319079055"],
        )

    def test_profile_timezone_detection_normalizes_structured_fact(self) -> None:
        detected = detect_profile_fact_observation("My timezone is Asia/Dubai.")
        self.assertIsNotNone(detected)
        assert detected is not None
        self.assertEqual(detected.predicate, "profile.timezone")
        self.assertEqual(detected.value, "Asia/Dubai")

    def test_profile_home_country_detection_normalizes_structured_fact(self) -> None:
        detected = detect_profile_fact_observation("My country is UAE.")
        self.assertIsNotNone(detected)
        assert detected is not None
        self.assertEqual(detected.predicate, "profile.home_country")
        self.assertEqual(detected.value, "UAE")

    def test_profile_preferred_name_detection_normalizes_structured_fact(self) -> None:
        detected = detect_profile_fact_observation("My name is Sarah.")
        self.assertIsNotNone(detected)
        assert detected is not None
        self.assertEqual(detected.predicate, "profile.preferred_name")
        self.assertEqual(detected.value, "Sarah")

    def test_profile_founder_startup_hack_and_mission_detection_normalize_structured_facts(self) -> None:
        startup = detect_profile_fact_observation("My startup is Seedify.")
        self.assertIsNotNone(startup)
        assert startup is not None
        self.assertEqual(startup.predicate, "profile.startup_name")
        self.assertEqual(startup.value, "Seedify")

        founder = detect_profile_fact_observation("I am the founder of Spark Swarm.")
        self.assertIsNotNone(founder)
        assert founder is not None
        self.assertEqual(founder.predicate, "profile.founder_of")
        self.assertEqual(founder.value, "Spark Swarm")

        hack_actor = detect_profile_fact_observation("We were hacked by North Korea.")
        self.assertIsNotNone(hack_actor)
        assert hack_actor is not None
        self.assertEqual(hack_actor.predicate, "profile.hack_actor")
        self.assertEqual(hack_actor.value, "North Korea")

        mission = detect_profile_fact_observation("I am trying to survive the hack and revive the companies.")
        self.assertIsNotNone(mission)
        assert mission is not None
        self.assertEqual(mission.predicate, "profile.current_mission")
        self.assertEqual(mission.value, "survive the hack and revive the companies")

    def test_profile_fact_query_detects_startup_founder_occupation_and_identity_summary_queries(self) -> None:
        city_query = detect_profile_fact_query("Which city do I live in?")
        self.assertIsNotNone(city_query)
        assert city_query is not None
        self.assertEqual(city_query.predicate, "profile.city")
        self.assertEqual(city_query.query_kind, "single_fact")

        country_query = detect_profile_fact_query("What country do I live in?")
        self.assertIsNotNone(country_query)
        assert country_query is not None
        self.assertEqual(country_query.predicate, "profile.home_country")
        self.assertEqual(country_query.query_kind, "single_fact")

        startup_query = detect_profile_fact_query("What startup did I create?")
        self.assertIsNotNone(startup_query)
        assert startup_query is not None
        self.assertEqual(startup_query.predicate, "profile.founder_of")
        self.assertEqual(startup_query.query_kind, "single_fact")

        founder_query = detect_profile_fact_query("What company did I found?")
        self.assertIsNotNone(founder_query)
        assert founder_query is not None
        self.assertEqual(founder_query.predicate, "profile.founder_of")
        self.assertEqual(founder_query.query_kind, "single_fact")

        occupation_query = detect_profile_fact_query("What is my occupation?")
        self.assertIsNotNone(occupation_query)
        assert occupation_query is not None
        self.assertEqual(occupation_query.predicate, "profile.occupation")
        self.assertEqual(occupation_query.query_kind, "single_fact")

        identity_query = detect_profile_fact_query("Who am I?")
        self.assertIsNotNone(identity_query)
        assert identity_query is not None
        self.assertEqual(identity_query.query_kind, "identity_summary")
        self.assertEqual(identity_query.predicate_prefix, "profile.")

        remember_query = detect_profile_fact_query("What do you remember about me?")
        self.assertIsNotNone(remember_query)
        assert remember_query is not None
        self.assertEqual(remember_query.query_kind, "identity_summary")
        self.assertEqual(remember_query.predicate_prefix, "profile.")

        favorite_color_query = detect_profile_fact_query("What is my favorite color?")
        self.assertIsNotNone(favorite_color_query)
        assert favorite_color_query is not None
        self.assertEqual(favorite_color_query.predicate, "profile.favorite_color")
        self.assertEqual(favorite_color_query.query_kind, "single_fact")

        dog_name_query = detect_profile_fact_query("What is my dog's name?")
        self.assertIsNotNone(dog_name_query)
        assert dog_name_query is not None
        self.assertEqual(dog_name_query.predicate, "profile.dog_name")
        self.assertEqual(dog_name_query.query_kind, "single_fact")

        favorite_food_query = detect_profile_fact_query("What food do I love the most?")
        self.assertIsNotNone(favorite_food_query)
        assert favorite_food_query is not None
        self.assertEqual(favorite_food_query.predicate, "profile.favorite_food")
        self.assertEqual(favorite_food_query.query_kind, "single_fact")

        summarize_profile_query = detect_profile_fact_query("Summarize my profile in one sentence.")
        self.assertIsNotNone(summarize_profile_query)
        assert summarize_profile_query is not None
        self.assertEqual(summarize_profile_query.query_kind, "identity_summary")
        self.assertEqual(summarize_profile_query.predicate_prefix, "profile.")

        full_profile_query = detect_profile_fact_query(
            "Give me a full profile summary with my latest location too."
        )
        self.assertIsNotNone(full_profile_query)
        assert full_profile_query is not None
        self.assertEqual(full_profile_query.query_kind, "identity_summary")
        self.assertEqual(full_profile_query.predicate_prefix, "profile.")

    def test_profile_fact_answers_cover_founder_occupation_and_clean_observation_wording(self) -> None:
        founder_query = detect_profile_fact_query("What company did I found?")
        self.assertIsNotNone(founder_query)
        assert founder_query is not None
        self.assertEqual(
            build_profile_fact_query_answer(query=founder_query, value="Spark Swarm"),
            "You founded Spark Swarm.",
        )

        occupation_query = detect_profile_fact_query("What am I?")
        self.assertIsNotNone(occupation_query)
        assert occupation_query is not None
        self.assertEqual(
            build_profile_fact_query_answer(query=occupation_query, value="entrepreneur"),
            "You're an entrepreneur.",
        )

        occupation_observation = detect_profile_fact_observation("I am an entrepreneur.")
        self.assertIsNotNone(occupation_observation)
        assert occupation_observation is not None
        self.assertEqual(
            build_profile_fact_observation_answer(observation=occupation_observation),
            "I'll remember you're an entrepreneur.",
        )

        spark_role_observation = detect_profile_fact_observation(
            "Spark will be an important part of the rebuild."
        )
        self.assertIsNotNone(spark_role_observation)
        assert spark_role_observation is not None
        self.assertEqual(
            build_profile_fact_observation_answer(observation=spark_role_observation),
            "I'll remember Spark will be an important part of the rebuild.",
        )

        mission_explanation_query = detect_profile_fact_query(
            "How do you know what I'm trying to do now?"
        )
        self.assertIsNotNone(mission_explanation_query)
        assert mission_explanation_query is not None
        self.assertEqual(mission_explanation_query.predicate, "profile.current_mission")
        self.assertEqual(mission_explanation_query.query_kind, "fact_explanation")
        self.assertIsNone(
            detect_profile_fact_observation("How do you know what I'm trying to do now?")
        )

    def test_build_profile_fact_explanation_answer_prefers_original_evidence_text_metadata(self) -> None:
        query = detect_profile_fact_query("How do you know where I live?")
        self.assertIsNotNone(query)
        assert query is not None

        answer = build_profile_fact_explanation_answer(
            query=query,
            explanation={
                "answer": "Dubai",
                "evidence": [
                    {
                        "text": "human:telegram:12345 profile.city Dubai",
                        "metadata": {"evidence_text": "I moved to Dubai."},
                    }
                ],
            },
        )

        self.assertEqual(
            answer,
            'Because I have a saved memory record from when you said: "I moved to Dubai." You live in Dubai.',
        )

    def test_profile_fact_query_detects_history_and_event_history_queries(self) -> None:
        history_query = detect_profile_fact_query("Where did I live before?")
        self.assertIsNotNone(history_query)
        assert history_query is not None
        self.assertEqual(history_query.predicate, "profile.city")
        self.assertEqual(history_query.query_kind, "fact_history")

        country_history_query = detect_profile_fact_query("What was my previous country?")
        self.assertIsNotNone(country_history_query)
        assert country_history_query is not None
        self.assertEqual(country_history_query.predicate, "profile.home_country")
        self.assertEqual(country_history_query.query_kind, "fact_history")

        event_history_query = detect_profile_fact_query(
            "What memory events do you have about where I live?"
        )
        self.assertIsNotNone(event_history_query)
        assert event_history_query is not None
        self.assertEqual(event_history_query.predicate, "profile.city")
        self.assertEqual(event_history_query.query_kind, "event_history")

        cofounder_history_query = detect_profile_fact_query("Who was my cofounder before?")
        self.assertIsNotNone(cofounder_history_query)
        assert cofounder_history_query is not None
        self.assertEqual(cofounder_history_query.predicate, "profile.cofounder_name")
        self.assertEqual(cofounder_history_query.query_kind, "fact_history")

        cofounder_event_history_query = detect_profile_fact_query("Show my cofounder history.")
        self.assertIsNotNone(cofounder_event_history_query)
        assert cofounder_event_history_query is not None
        self.assertEqual(cofounder_event_history_query.predicate, "profile.cofounder_name")
        self.assertEqual(cofounder_event_history_query.query_kind, "event_history")

    def test_profile_fact_history_and_event_answers_are_grounded_and_compact(self) -> None:
        history_query = detect_profile_fact_query("Where did I live before?")
        self.assertIsNotNone(history_query)
        assert history_query is not None
        self.assertEqual(
            build_profile_fact_history_answer(
                query=history_query,
                previous_value="Dubai",
                current_value="Abu Dhabi",
            ),
            "Before Abu Dhabi, you lived in Dubai.",
        )
        self.assertEqual(
            build_profile_fact_history_answer(
                query=history_query,
                previous_value=None,
                current_value="Abu Dhabi",
            ),
            "I don't currently have an earlier saved city.",
        )

        event_history_query = detect_profile_fact_query(
            "What memory events do you have about where I live?"
        )
        self.assertIsNotNone(event_history_query)
        assert event_history_query is not None
        self.assertEqual(
            build_profile_fact_event_history_answer(
                query=event_history_query,
                records=[
                    {"value": "Dubai"},
                    {"value": "Dubai"},
                    {"value": "Abu Dhabi"},
                ],
            ),
            "I have 2 saved city events: Dubai then Abu Dhabi.",
        )

        cofounder_history_query = detect_profile_fact_query("Who was my cofounder before?")
        self.assertIsNotNone(cofounder_history_query)
        assert cofounder_history_query is not None
        self.assertEqual(
            build_profile_fact_history_answer(
                query=cofounder_history_query,
                previous_value="Omar",
                current_value="Sara",
            ),
            "Before Sara, your cofounder was Omar.",
        )

    def test_telegram_event_query_detection_filtering_and_answers_are_compact(self) -> None:
        query = detect_telegram_memory_event_query("What event did I mention?")
        self.assertIsNotNone(query)
        assert query is not None
        self.assertIsNone(query.predicate)

        meeting_query = detect_telegram_memory_event_query("What meetings did I mention?")
        self.assertIsNotNone(meeting_query)
        assert meeting_query is not None
        self.assertEqual(meeting_query.predicate, "telegram.event.meeting")

        latest_query = detect_telegram_memory_event_query("What flight do I have?")
        self.assertIsNotNone(latest_query)
        assert latest_query is not None
        self.assertEqual(latest_query.query_kind, "latest_event")
        self.assertEqual(latest_query.predicate, "telegram.event.flight")
        self.assertEqual(latest_query.summary_predicate, "telegram.summary.latest_flight")
        self.assertEqual(
            telegram_event_summary_predicate("telegram.event.deadline"),
            "telegram.summary.latest_deadline",
        )

        filtered = filter_telegram_memory_event_records(
            query=query,
            records=[
                {
                    "predicate": "telegram.event.meeting",
                    "value": "meeting with Omar on May 3",
                    "timestamp": "2026-04-10T10:00:00+00:00",
                    "turn_ids": ["turn-1"],
                },
                {
                    "predicate": "profile.city",
                    "value": "Dubai",
                    "timestamp": "2026-04-10T11:00:00+00:00",
                    "turn_ids": ["turn-2"],
                },
                {
                    "predicate": "telegram.event.call",
                    "value": "call with Sarah on May 4",
                    "timestamp": "2026-04-10T12:00:00+00:00",
                    "turn_ids": ["turn-3"],
                },
            ],
        )
        self.assertEqual([record["predicate"] for record in filtered], ["telegram.event.meeting", "telegram.event.call"])
        self.assertEqual(
            build_telegram_memory_event_query_answer(
                query=query,
                records=filtered,
            ),
            "I have 2 saved events: meeting with Omar on May 3 then call with Sarah on May 4.",
        )
        self.assertEqual(
            build_telegram_memory_event_query_answer(
                query=latest_query,
                records=[
                    {
                        "predicate": "telegram.summary.latest_flight",
                        "value": "flight to London on May 6",
                        "timestamp": "2026-04-10T12:00:00+00:00",
                        "turn_ids": ["turn-4"],
                    }
                ],
            ),
            "Your latest saved flight is flight to London on May 6.",
        )

    def test_telegram_event_detection_supports_flight_and_deadline_types(self) -> None:
        flight = detect_telegram_memory_event_observation("My flight to London is on May 6.")
        self.assertIsNotNone(flight)
        assert flight is not None
        self.assertEqual(flight.predicate, "telegram.event.flight")
        self.assertEqual(flight.value, "flight to London on May 6")

        deadline = detect_telegram_memory_event_observation("My deadline for the proposal is by Friday.")
        self.assertIsNotNone(deadline)
        assert deadline is not None
        self.assertEqual(deadline.predicate, "telegram.event.deadline")
        self.assertEqual(deadline.value, "deadline for the proposal by Friday")

    def test_build_profile_identity_summary_context_lists_saved_facts(self) -> None:
        context = build_profile_identity_summary_context(
            records=[
                {"predicate": "profile.occupation", "value": "entrepreneur"},
                {"predicate": "profile.startup_name", "value": "Seedify"},
                {"predicate": "profile.current_mission", "value": "revive the companies"},
            ]
        )

        self.assertIn("[Memory action: PROFILE_IDENTITY_SUMMARY]", context)
        self.assertIn("- occupation: entrepreneur", context)
        self.assertIn("- startup: Seedify", context)
        self.assertIn("- current mission: revive the companies", context)

    def test_build_profile_identity_summary_prefers_latest_values_per_predicate(self) -> None:
        context = build_profile_identity_summary_context(
            records=[
                {"predicate": "profile.home_country", "value": "UAE"},
                {"predicate": "profile.home_country", "value": "Canada"},
                {"predicate": "profile.founder_of", "value": "Spark Swarm"},
                {"predicate": "profile.founder_of", "value": "Atlas Labs"},
            ]
        )

        self.assertIn("- country: Canada", context)
        self.assertIn("- founder of: Atlas Labs", context)
        self.assertNotIn("- country: UAE", context)
        self.assertNotIn("- founder of: Spark Swarm", context)

        answer = build_profile_identity_summary_answer(
            records=[
                {"predicate": "profile.home_country", "value": "UAE"},
                {"predicate": "profile.home_country", "value": "Canada"},
                {"predicate": "profile.startup_name", "value": "Seedify"},
                {"predicate": "profile.startup_name", "value": "Atlas Labs"},
                {"predicate": "profile.founder_of", "value": "Spark Swarm"},
                {"predicate": "profile.founder_of", "value": "Atlas Labs"},
            ]
        )

        self.assertIn("You founded Atlas Labs.", answer)
        self.assertIn("You're based in Canada.", answer)
        self.assertNotIn("Spark Swarm", answer)
        self.assertNotIn("Seedify", answer)
        self.assertNotIn("UAE", answer)

    def test_build_profile_identity_summary_answer_compacts_saved_facts(self) -> None:
        answer = build_profile_identity_summary_answer(
            records=[
                {"predicate": "profile.occupation", "value": "entrepreneur"},
                {"predicate": "profile.city", "value": "Dubai"},
                {"predicate": "profile.startup_name", "value": "Seedify"},
                {"predicate": "profile.founder_of", "value": "Spark Swarm"},
                {"predicate": "profile.home_country", "value": "UAE"},
                {"predicate": "profile.current_mission", "value": "survive the hack and revive the companies"},
                {"predicate": "profile.spark_role", "value": "important part of the rebuild"},
            ]
        )

        self.assertIn("You're an entrepreneur in Dubai.", answer)
        self.assertIn("You founded Spark Swarm.", answer)
        self.assertIn("Your startup is Seedify.", answer)
        self.assertIn("Your country is UAE.", answer)
        self.assertIn("Your current mission is to survive the hack and revive the companies.", answer)
        self.assertIn("Spark will be an important part of the rebuild.", answer)

    def test_build_profile_identity_summary_answer_deduplicates_matching_founder_and_startup(self) -> None:
        answer = build_profile_identity_summary_answer(
            records=[
                {"predicate": "profile.startup_name", "value": "Atlas Labs"},
                {"predicate": "profile.founder_of", "value": "Atlas Labs"},
            ]
        )

        self.assertEqual(answer, "You founded Atlas Labs.")

    def test_build_profile_fact_query_context_demands_single_sentence_grounded_answer(self) -> None:
        query = detect_profile_fact_query("What role will Spark play in this?")
        self.assertIsNotNone(query)
        assert query is not None

        context = build_profile_fact_query_context(
            query=query,
            value="important part of the rebuild",
        )

        self.assertIn("[Memory action: PROFILE_FACT_STATUS]", context)
        self.assertIn("Expected concise answer: Spark will be an important part of the rebuild.", context)
        self.assertIn("Answer in one sentence only.", context)
        self.assertIn("Do not add broader narrative", context)

    def test_memory_sdk_smoke_test_runs_real_domain_chip_roundtrip(self) -> None:
        result = run_memory_sdk_smoke_test(
            config_manager=self.config_manager,
            state_db=self.state_db,
            sdk_module="domain_chip_memory",
            subject="human:smoke:test",
            predicate="system.memory.smoke",
            value="ok",
        )

        self.assertEqual(result.sdk_module, "domain_chip_memory")
        self.assertGreaterEqual(result.write_result.accepted_count, 1)
        self.assertFalse(result.read_result.abstained)
        self.assertTrue(result.read_result.records)
        self.assertEqual(result.read_result.records[0]["predicate"], "system.memory.smoke")
        self.assertEqual(result.read_result.records[0]["value"], "ok")
        self.assertIsNotNone(result.cleanup_result)
        self.assertGreaterEqual(result.cleanup_result.accepted_count, 1)
        smoke_events = latest_events_by_type(self.state_db, event_type="memory_smoke_succeeded", limit=5)
        self.assertTrue(smoke_events)
        smoke_facts = smoke_events[0]["facts_json"] or {}
        self.assertEqual(smoke_facts.get("sdk_module"), "domain_chip_memory")
        self.assertEqual(smoke_facts.get("predicate"), "system.memory.smoke")

    def test_lookup_current_state_in_memory_reads_back_structured_fact(self) -> None:
        run_memory_sdk_smoke_test(
            config_manager=self.config_manager,
            state_db=self.state_db,
            sdk_module="domain_chip_memory",
            subject="human:lookup:test",
            predicate="system.memory.lookup",
            value="ok",
            cleanup=False,
        )

        result = lookup_current_state_in_memory(
            config_manager=self.config_manager,
            state_db=self.state_db,
            subject="human:lookup:test",
            predicate="system.memory.lookup",
            sdk_module="domain_chip_memory",
        )

        self.assertFalse(result.read_result.abstained)
        self.assertTrue(result.read_result.records)
        self.assertEqual(result.read_result.records[0]["predicate"], "system.memory.lookup")
        self.assertEqual(result.read_result.records[0]["value"], "ok")

    def test_lookup_current_state_in_memory_rehydrates_domain_chip_state_after_cache_clear(self) -> None:
        run_memory_sdk_smoke_test(
            config_manager=self.config_manager,
            state_db=self.state_db,
            sdk_module="domain_chip_memory",
            subject="human:lookup:persisted",
            predicate="system.memory.persisted",
            value="ok",
            cleanup=False,
        )
        memory_orchestrator._SDK_CLIENT_CACHE.clear()

        result = lookup_current_state_in_memory(
            config_manager=self.config_manager,
            state_db=self.state_db,
            subject="human:lookup:persisted",
            predicate="system.memory.persisted",
            sdk_module="domain_chip_memory",
        )

        self.assertFalse(result.read_result.abstained)
        self.assertTrue(result.read_result.records)
        self.assertEqual(result.read_result.records[0]["predicate"], "system.memory.persisted")
        self.assertEqual(result.read_result.records[0]["value"], "ok")

    def test_domain_chip_persistence_merges_concurrent_client_writes(self) -> None:
        memory_orchestrator._SDK_CLIENT_CACHE.clear()
        client_a = memory_orchestrator._load_sdk_client_for_module(
            module_name="domain_chip_memory",
            home_path=self.config_manager.paths.home,
        )
        memory_orchestrator._SDK_CLIENT_CACHE.clear()
        client_b = memory_orchestrator._load_sdk_client_for_module(
            module_name="domain_chip_memory",
            home_path=self.config_manager.paths.home,
        )
        self.assertIsNotNone(client_a)
        self.assertIsNotNone(client_b)

        client_a.write_observation(
            operation="update",
            subject="human:merge:test:a",
            predicate="profile.current_owner",
            value="Omar",
            text="Our owner is Omar.",
            session_id="session:merge:test:a",
            turn_id="turn:merge:test:a",
            timestamp="2026-04-21T10:00:00+00:00",
            metadata={
                "entity_type": "human",
                "field_name": "current_owner",
                "memory_role": "current_state",
                "source_surface": "test",
                "fact_name": "current_owner",
                "normalized_value": "Omar",
            },
        )
        client_b.write_observation(
            operation="update",
            subject="human:merge:test:b",
            predicate="profile.current_constraint",
            value="budget for one engineer",
            text="Our constraint is budget for only one engineer.",
            session_id="session:merge:test:b",
            turn_id="turn:merge:test:b",
            timestamp="2026-04-21T10:00:01+00:00",
            metadata={
                "entity_type": "human",
                "field_name": "current_constraint",
                "memory_role": "current_state",
                "source_surface": "test",
                "fact_name": "current_constraint",
                "normalized_value": "budget for one engineer",
            },
        )

        memory_orchestrator._SDK_CLIENT_CACHE.clear()

        owner_result = lookup_current_state_in_memory(
            config_manager=self.config_manager,
            state_db=self.state_db,
            subject="human:merge:test:a",
            predicate="profile.current_owner",
            sdk_module="domain_chip_memory",
        )
        constraint_result = lookup_current_state_in_memory(
            config_manager=self.config_manager,
            state_db=self.state_db,
            subject="human:merge:test:b",
            predicate="profile.current_constraint",
            sdk_module="domain_chip_memory",
        )

        self.assertFalse(owner_result.read_result.abstained)
        self.assertEqual(owner_result.read_result.records[0]["value"], "Omar")
        self.assertFalse(constraint_result.read_result.abstained)
        self.assertEqual(constraint_result.read_result.records[0]["value"], "budget for one engineer")

    def test_domain_chip_persistence_merges_concurrent_current_state_and_event_writes(self) -> None:
        memory_orchestrator._SDK_CLIENT_CACHE.clear()
        client_a = memory_orchestrator._load_sdk_client_for_module(
            module_name="domain_chip_memory",
            home_path=self.config_manager.paths.home,
        )
        memory_orchestrator._SDK_CLIENT_CACHE.clear()
        client_b = memory_orchestrator._load_sdk_client_for_module(
            module_name="domain_chip_memory",
            home_path=self.config_manager.paths.home,
        )
        self.assertIsNotNone(client_a)
        self.assertIsNotNone(client_b)

        client_a.write_observation(
            operation="update",
            subject="human:merge:test:mixed",
            predicate="profile.current_owner",
            value="Nadia",
            text="The current owner is Nadia.",
            session_id="session:merge:test:mixed:owner",
            turn_id="turn:merge:test:mixed:owner",
            timestamp="2026-04-21T10:01:00+00:00",
            metadata={
                "entity_type": "human",
                "field_name": "current_owner",
                "memory_role": "current_state",
                "source_surface": "test",
                "fact_name": "current_owner",
                "normalized_value": "Nadia",
            },
        )
        client_b.write_event(
            operation="event",
            subject="human:merge:test:mixed",
            predicate="telegram.event.meeting",
            value="meeting with Omar on May 3",
            text="My meeting with Omar is on May 3.",
            session_id="session:merge:test:mixed:event",
            turn_id="turn:merge:test:mixed:event",
            timestamp="2026-04-21T10:01:01+00:00",
            metadata={
                "entity_type": "human",
                "memory_role": "event",
                "source_surface": "test",
                "event_name": "telegram_event_meeting",
                "normalized_value": "meeting with Omar on May 3",
                "value": "meeting with Omar on May 3",
            },
        )

        memory_orchestrator._SDK_CLIENT_CACHE.clear()

        owner_result = lookup_current_state_in_memory(
            config_manager=self.config_manager,
            state_db=self.state_db,
            subject="human:merge:test:mixed",
            predicate="profile.current_owner",
            sdk_module="domain_chip_memory",
        )
        event_result = retrieve_memory_events_in_memory(
            config_manager=self.config_manager,
            state_db=self.state_db,
            query="What event did I mention?",
            subject="human:merge:test:mixed",
            predicate="telegram.event.meeting",
            sdk_module="domain_chip_memory",
        )

        self.assertFalse(owner_result.read_result.abstained)
        self.assertEqual(owner_result.read_result.records[0]["value"], "Nadia")
        self.assertFalse(event_result.read_result.abstained)
        self.assertEqual(len(event_result.read_result.records), 1)
        self.assertEqual(event_result.read_result.records[0]["value"], "meeting with Omar on May 3")

    def test_domain_chip_persistence_preserves_stale_client_delete(self) -> None:
        memory_orchestrator._SDK_CLIENT_CACHE.clear()
        client_a = memory_orchestrator._load_sdk_client_for_module(
            module_name="domain_chip_memory",
            home_path=self.config_manager.paths.home,
        )
        memory_orchestrator._SDK_CLIENT_CACHE.clear()
        client_b = memory_orchestrator._load_sdk_client_for_module(
            module_name="domain_chip_memory",
            home_path=self.config_manager.paths.home,
        )
        self.assertIsNotNone(client_a)
        self.assertIsNotNone(client_b)

        client_a.write_observation(
            operation="update",
            subject="human:merge:test:delete",
            predicate="profile.current_owner",
            value="Omar",
            text="Our owner is Omar.",
            session_id="session:merge:test:delete:write",
            turn_id="turn:merge:test:delete:write",
            timestamp="2026-04-21T10:02:00+00:00",
            metadata={
                "entity_type": "human",
                "field_name": "current_owner",
                "memory_role": "current_state",
                "source_surface": "test",
                "fact_name": "current_owner",
                "normalized_value": "Omar",
            },
        )
        client_b.write_observation(
            operation="delete",
            subject="human:merge:test:delete",
            predicate="profile.current_owner",
            value=None,
            text="Forget our owner.",
            session_id="session:merge:test:delete:delete",
            turn_id="turn:merge:test:delete:delete",
            timestamp="2026-04-21T10:02:01+00:00",
            metadata={
                "entity_type": "human",
                "field_name": "current_owner",
                "memory_role": "current_state",
                "source_surface": "test",
                "fact_name": "current_owner",
            },
        )

        memory_orchestrator._SDK_CLIENT_CACHE.clear()

        current_result = lookup_current_state_in_memory(
            config_manager=self.config_manager,
            state_db=self.state_db,
            subject="human:merge:test:delete",
            predicate="profile.current_owner",
            sdk_module="domain_chip_memory",
        )

        self.assertTrue(current_result.read_result.abstained)
        self.assertEqual(current_result.read_result.records, [])

    def test_domain_chip_persistence_preserves_repeated_stale_client_overwrites_across_predicates(self) -> None:
        memory_orchestrator._SDK_CLIENT_CACHE.clear()
        client_a = memory_orchestrator._load_sdk_client_for_module(
            module_name="domain_chip_memory",
            home_path=self.config_manager.paths.home,
        )
        memory_orchestrator._SDK_CLIENT_CACHE.clear()
        client_b = memory_orchestrator._load_sdk_client_for_module(
            module_name="domain_chip_memory",
            home_path=self.config_manager.paths.home,
        )
        memory_orchestrator._SDK_CLIENT_CACHE.clear()
        client_c = memory_orchestrator._load_sdk_client_for_module(
            module_name="domain_chip_memory",
            home_path=self.config_manager.paths.home,
        )
        self.assertIsNotNone(client_a)
        self.assertIsNotNone(client_b)
        self.assertIsNotNone(client_c)

        client_a.write_observation(
            operation="update",
            subject="human:merge:test:multi",
            predicate="profile.current_owner",
            value="Omar",
            text="Our owner is Omar.",
            session_id="session:merge:test:multi:owner:1",
            turn_id="turn:merge:test:multi:owner:1",
            timestamp="2026-04-21T10:03:00+00:00",
            metadata={
                "entity_type": "human",
                "field_name": "current_owner",
                "memory_role": "current_state",
                "source_surface": "test",
                "fact_name": "current_owner",
                "normalized_value": "Omar",
            },
        )
        client_b.write_observation(
            operation="update",
            subject="human:merge:test:multi",
            predicate="profile.current_risk",
            value="delayed instrumentation",
            text="Our main risk is delayed instrumentation.",
            session_id="session:merge:test:multi:risk",
            turn_id="turn:merge:test:multi:risk",
            timestamp="2026-04-21T10:03:01+00:00",
            metadata={
                "entity_type": "human",
                "field_name": "current_risk",
                "memory_role": "current_state",
                "source_surface": "test",
                "fact_name": "current_risk",
                "normalized_value": "delayed instrumentation",
            },
        )
        client_c.write_observation(
            operation="update",
            subject="human:merge:test:multi",
            predicate="profile.current_dependency",
            value="partner API access",
            text="Our dependency is partner API access.",
            session_id="session:merge:test:multi:dependency",
            turn_id="turn:merge:test:multi:dependency",
            timestamp="2026-04-21T10:03:02+00:00",
            metadata={
                "entity_type": "human",
                "field_name": "current_dependency",
                "memory_role": "current_state",
                "source_surface": "test",
                "fact_name": "current_dependency",
                "normalized_value": "partner API access",
            },
        )
        client_a.write_observation(
            operation="update",
            subject="human:merge:test:multi",
            predicate="profile.current_owner",
            value="Sara",
            text="The current owner is Sara.",
            session_id="session:merge:test:multi:owner:2",
            turn_id="turn:merge:test:multi:owner:2",
            timestamp="2026-04-21T10:03:03+00:00",
            metadata={
                "entity_type": "human",
                "field_name": "current_owner",
                "memory_role": "current_state",
                "source_surface": "test",
                "fact_name": "current_owner",
                "normalized_value": "Sara",
            },
        )

        memory_orchestrator._SDK_CLIENT_CACHE.clear()

        owner_result = lookup_current_state_in_memory(
            config_manager=self.config_manager,
            state_db=self.state_db,
            subject="human:merge:test:multi",
            predicate="profile.current_owner",
            sdk_module="domain_chip_memory",
        )
        risk_result = lookup_current_state_in_memory(
            config_manager=self.config_manager,
            state_db=self.state_db,
            subject="human:merge:test:multi",
            predicate="profile.current_risk",
            sdk_module="domain_chip_memory",
        )
        dependency_result = lookup_current_state_in_memory(
            config_manager=self.config_manager,
            state_db=self.state_db,
            subject="human:merge:test:multi",
            predicate="profile.current_dependency",
            sdk_module="domain_chip_memory",
        )

        self.assertFalse(owner_result.read_result.abstained)
        self.assertEqual(owner_result.read_result.records[0]["value"], "Sara")
        self.assertFalse(risk_result.read_result.abstained)
        self.assertEqual(risk_result.read_result.records[0]["value"], "delayed instrumentation")
        self.assertFalse(dependency_result.read_result.abstained)
        self.assertEqual(dependency_result.read_result.records[0]["value"], "partner API access")

    def test_lookup_historical_state_in_memory_reads_prior_value_after_overwrite(self) -> None:
        expected_read_result = memory_orchestrator.MemoryReadResult(
            status="supported",
            method="get_historical_state",
            memory_role="structured_evidence",
            records=[
                {
                    "predicate": "profile.city",
                    "value": "Dubai",
                    "timestamp": "2026-04-10T10:00:00+00:00",
                }
            ],
            provenance=[],
            retrieval_trace={"operation": "get_historical_state"},
            answer_explanation=None,
            abstained=False,
        )

        with patch(
            "spark_intelligence.memory.orchestrator.inspect_memory_sdk_runtime",
            return_value={"ready": True, "configured_module": "domain_chip_memory"},
        ), patch(
            "spark_intelligence.memory.orchestrator._load_sdk_client_for_module",
            return_value=object(),
        ), patch(
            "spark_intelligence.memory.orchestrator._get_historical_state_with_subject_fallback",
            return_value=("human:history:test", expected_read_result),
        ) as history_lookup_mock:
            result = lookup_historical_state_in_memory(
                config_manager=self.config_manager,
                state_db=self.state_db,
                subject="human:history:test",
                predicate="profile.city",
                as_of="2026-04-10T10:00:00+00:00",
                sdk_module="domain_chip_memory",
            )

        self.assertFalse(result.read_result.abstained)
        self.assertEqual(result.as_of, "2026-04-10T10:00:00+00:00")
        self.assertTrue(result.read_result.records)
        self.assertEqual(result.read_result.records[0]["predicate"], "profile.city")
        self.assertEqual(result.read_result.records[0]["value"], "Dubai")
        history_lookup_mock.assert_called_once()

    def test_inspect_human_memory_in_memory_returns_all_current_state_records_for_subject(self) -> None:
        run_memory_sdk_smoke_test(
            config_manager=self.config_manager,
            state_db=self.state_db,
            sdk_module="domain_chip_memory",
            subject="human:inspect:test",
            predicate="system.memory.one",
            value="alpha",
            cleanup=False,
        )
        run_memory_sdk_smoke_test(
            config_manager=self.config_manager,
            state_db=self.state_db,
            sdk_module="domain_chip_memory",
            subject="human:inspect:test",
            predicate="system.memory.two",
            value="beta",
            cleanup=False,
        )
        memory_orchestrator._SDK_CLIENT_CACHE.clear()

        result = inspect_human_memory_in_memory(
            config_manager=self.config_manager,
            state_db=self.state_db,
            human_id="inspect:test",
            sdk_module="domain_chip_memory",
        )

        self.assertFalse(result.read_result.abstained)
        predicates = {str(record.get("predicate") or "") for record in result.read_result.records}
        self.assertEqual(predicates, {"system.memory.one", "system.memory.two"})

    def test_domain_chip_memory_sdk_module_supports_live_preference_write_then_read(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)
        self.config_manager.set_path("spark.memory.sdk_module", "domain_chip_memory")

        deltas = detect_and_persist_nl_preferences(
            human_id="human:test",
            user_message="be more direct and stop hedging",
            state_db=self.state_db,
            config_manager=self.config_manager,
            session_id="session:domain-chip",
            turn_id="turn:domain-chip-write",
            channel_kind="telegram",
        )

        self.assertIsNotNone(deltas)

        query = detect_personality_query(
            user_message="what's my personality",
            human_id="human:test",
            state_db=self.state_db,
            profile=load_personality_profile(
                human_id="human:test",
                state_db=self.state_db,
                config_manager=self.config_manager,
            ),
            config_manager=self.config_manager,
            session_id="session:domain-chip",
            turn_id="turn:domain-chip-read",
        )

        self.assertEqual(query.kind, "status")
        self.assertIn("Memory-backed current-state facts:", query.context_injection)
        self.assertIn("directness:", query.context_injection)
        write_events = latest_events_by_type(self.state_db, event_type="memory_write_succeeded", limit=10)
        read_events = latest_events_by_type(self.state_db, event_type="memory_read_succeeded", limit=10)
        self.assertTrue(write_events)
        self.assertTrue(read_events)
        self.assertFalse(bool((read_events[0]["facts_json"] or {}).get("shadow_only")))

    def test_load_personality_profile_gracefully_handles_missing_runtime_dependencies(self) -> None:
        missing_state_path = self.home / "missing-personality-evolver.json"
        with patch("spark_intelligence.personality.loader._PERSONALITY_EVOLUTION_FILE", missing_state_path):
            profile = load_personality_profile(
                human_id="human:test",
                state_db=None,
                config_manager=None,
            )

        assert profile is not None
        self.assertEqual(profile["source"], "defaults")
        self.assertFalse(profile["user_deltas_applied"])
        self.assertEqual(profile["traits"]["warmth"], 0.5)
        self.assertEqual(profile["traits"]["directness"], 0.5)

    def test_durable_preference_updates_write_structured_memory_observations(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)
        fake_client = _FakeMemoryClient()

        with patch("spark_intelligence.memory.orchestrator._load_sdk_client", return_value=fake_client):
            deltas = detect_and_persist_nl_preferences(
                human_id="human:test",
                user_message="be more direct and stop hedging",
                state_db=self.state_db,
                config_manager=self.config_manager,
                session_id="session:memory",
                turn_id="turn:memory-write",
                channel_kind="telegram",
            )

        self.assertIsNotNone(deltas)
        self.assertTrue(fake_client.observation_calls)
        first_call = fake_client.observation_calls[0]
        self.assertEqual(first_call["subject"], "human:test")
        self.assertEqual(first_call["memory_role"], "current_state")
        self.assertEqual(first_call["session_id"], "session:memory")
        self.assertEqual(first_call["turn_id"], "turn:memory-write")
        self.assertIn("personality.preference.", str(first_call["predicate"]))
        events = latest_events_by_type(self.state_db, event_type="memory_write_succeeded", limit=10)
        self.assertTrue(events)

    def test_personality_reset_deletes_memory_preferences(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)
        fake_client = _FakeMemoryClient()

        with patch("spark_intelligence.memory.orchestrator._load_sdk_client", return_value=fake_client):
            detect_and_persist_nl_preferences(
                human_id="human:test",
                user_message="be more direct",
                state_db=self.state_db,
                config_manager=self.config_manager,
                session_id="session:memory",
                turn_id="turn:memory-create",
                channel_kind="telegram",
            )
            fake_client.observation_calls.clear()
            query = detect_personality_query(
                user_message="reset personality",
                human_id="human:test",
                state_db=self.state_db,
                profile=load_personality_profile(
                    human_id="human:test",
                    state_db=self.state_db,
                    config_manager=self.config_manager,
                ),
                config_manager=self.config_manager,
                session_id="session:memory",
                turn_id="turn:memory-reset",
            )

        self.assertEqual(query.kind, "reset")
        self.assertTrue(fake_client.observation_calls)
        self.assertTrue(all(call["operation"] == "delete" for call in fake_client.observation_calls))
        reset_events = latest_events_by_type(self.state_db, event_type="session_reset_performed", limit=10)
        self.assertTrue(reset_events)
        registry_rows = recent_reset_sensitive_state_registry(self.state_db, limit=20)
        human_rows = [row for row in registry_rows if row["scope_ref"] == "human:test"]
        self.assertTrue(human_rows)
        self.assertTrue(all(int(row["active"]) == 0 for row in human_rows))

    def test_status_query_uses_narrow_current_state_read_when_memory_is_live(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)
        fake_client = _FakeMemoryClient()
        profile = load_personality_profile(
            human_id="human:test",
            state_db=self.state_db,
            config_manager=self.config_manager,
        )

        with patch("spark_intelligence.memory.orchestrator._load_sdk_client", return_value=fake_client):
            query = detect_personality_query(
                user_message="what's my personality",
                human_id="human:test",
                state_db=self.state_db,
                profile=profile,
                config_manager=self.config_manager,
                session_id="session:memory",
                turn_id="turn:memory-read",
            )

        self.assertEqual(query.kind, "status")
        self.assertEqual(len(fake_client.current_state_calls), 1)
        self.assertIn("Memory-backed current-state facts:", query.context_injection)
        self.assertIn("directness: 0.35", query.context_injection)
        events = latest_events_by_type(self.state_db, event_type="memory_read_succeeded", limit=10)
        self.assertTrue(events)

    def test_shadow_mode_preserves_memory_uncertainty_in_status_queries(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", True)
        fake_client = _AbstainingMemoryClient()
        profile = load_personality_profile(
            human_id="human:test",
            state_db=self.state_db,
            config_manager=self.config_manager,
        )

        with patch("spark_intelligence.memory.orchestrator._load_sdk_client", return_value=fake_client):
            query = detect_personality_query(
                user_message="show my personality",
                human_id="human:test",
                state_db=self.state_db,
                profile=profile,
                config_manager=self.config_manager,
                session_id="session:memory",
                turn_id="turn:memory-shadow",
            )

        self.assertEqual(query.kind, "status")
        self.assertNotIn("Memory-backed current-state facts:", query.context_injection)
        events = latest_events_by_type(self.state_db, event_type="memory_read_abstained", limit=10)
        self.assertTrue(events)
        facts = events[0]["facts_json"] or {}
        self.assertTrue(facts.get("shadow_only"))

    def test_watchtower_memory_shadow_panel_counts_sdk_outcomes(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)
        fake_client = _FakeMemoryClient()

        with patch("spark_intelligence.memory.orchestrator._load_sdk_client", return_value=fake_client):
            detect_and_persist_nl_preferences(
                human_id="human:test",
                user_message="be more direct",
                state_db=self.state_db,
                config_manager=self.config_manager,
                session_id="session:memory",
                turn_id="turn:memory-write",
                channel_kind="telegram",
            )
            detect_personality_query(
                user_message="show my personality",
                human_id="human:test",
                state_db=self.state_db,
                profile=load_personality_profile(
                    human_id="human:test",
                    state_db=self.state_db,
                    config_manager=self.config_manager,
                ),
                config_manager=self.config_manager,
                session_id="session:memory",
                turn_id="turn:memory-read",
            )

        snapshot = build_watchtower_snapshot(self.state_db)
        panel = snapshot["panels"]["memory_shadow"]

        self.assertEqual(panel["counts"]["write_requests"], 1)
        self.assertEqual(panel["counts"]["accepted_observations"], 1)
        self.assertEqual(panel["counts"]["read_requests"], 2)
        self.assertEqual(panel["counts"]["read_hits"], 1)
        self.assertEqual(panel["counts"]["shadow_only_reads"], 0)

    def test_doctor_flags_live_memory_when_all_reads_abstain(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)
        fake_client = _AbstainingMemoryClient()

        with patch("spark_intelligence.memory.orchestrator._load_sdk_client", return_value=fake_client):
            detect_personality_query(
                user_message="what's my personality",
                human_id="human:test",
                state_db=self.state_db,
                profile=load_personality_profile(
                    human_id="human:test",
                    state_db=self.state_db,
                    config_manager=self.config_manager,
                ),
                config_manager=self.config_manager,
                session_id="session:memory",
                turn_id="turn:memory-read",
            )

        report = run_doctor(self.config_manager, self.state_db)
        checks = {check.name: check for check in report.checks}

        self.assertIn("watchtower-memory-shadow", checks)
        self.assertFalse(checks["watchtower-memory-shadow"].ok)
        self.assertIn("memory_abstaining", checks["watchtower-memory-shadow"].detail)

    def test_invalid_sdk_memory_role_is_abstained_and_counted_in_watchtower(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)
        fake_client = _InvalidMemoryRoleClient()

        with (
            patch("spark_intelligence.memory.orchestrator._load_sdk_client", return_value=fake_client),
            patch("spark_intelligence.memory.orchestrator._load_sdk_client_for_module", return_value=fake_client),
        ):
            write_result = write_profile_fact_to_memory(
                config_manager=self.config_manager,
                state_db=self.state_db,
                human_id="human:test",
                predicate="profile.city",
                value="Dubai",
                evidence_text="I moved to Dubai.",
                fact_name="city",
                session_id="session:invalid-role",
                turn_id="turn:invalid-role-write",
                channel_kind="telegram",
            )
            lookup_result = lookup_current_state_in_memory(
                config_manager=self.config_manager,
                state_db=self.state_db,
                subject="human:test",
                predicate="profile.city",
            )

        self.assertTrue(write_result.abstained)
        self.assertEqual(write_result.reason, "invalid_memory_role")
        self.assertTrue(lookup_result.read_result.abstained)
        self.assertEqual(lookup_result.read_result.reason, "invalid_memory_role")

        snapshot = build_watchtower_snapshot(self.state_db)
        panel = snapshot["panels"]["memory_shadow"]
        self.assertEqual(panel["counts"]["contract_violations"], 2)
        self.assertEqual(panel["counts"]["invalid_role_events"], 2)

        report = run_doctor(self.config_manager, self.state_db)
        checks = {check.name: check for check in report.checks}
        self.assertIn("watchtower-memory-contract", checks)
        self.assertFalse(checks["watchtower-memory-contract"].ok)
        self.assertIn("contract_violations=2", checks["watchtower-memory-contract"].detail)

    def test_retrieve_evidence_uses_metadata_memory_role_when_sdk_record_is_unknown(self) -> None:
        result = SimpleNamespace(
            items=[
                SimpleNamespace(
                    memory_role="unknown",
                    subject="human:test",
                    predicate="profile.city",
                    text="I moved to Dubai.",
                    session_id="session:test",
                    turn_ids=["turn:test"],
                    timestamp="2026-04-20T12:00:00Z",
                    metadata={"memory_role": "current_state", "value": "Dubai"},
                )
            ],
            trace={"trace_id": "trace:test"},
        )

        normalized = memory_orchestrator._normalize_domain_retrieval_result(
            result=result,
            method="retrieve_evidence",
        )

        self.assertEqual(normalized["status"], "supported")
        self.assertEqual(normalized["memory_role"], "current_state")
        self.assertEqual(normalized["records"][0]["memory_role"], "current_state")
        self.assertEqual(normalized["records"][0]["value"], "Dubai")

    def test_shadow_replay_payload_uses_real_turns_and_memory_observation_probes(self) -> None:
        with self.state_db.connect() as conn:
            conn.execute(
                """
                INSERT INTO builder_events(
                    event_id, event_type, truth_kind, target_surface, component, run_id, request_id, channel_id,
                    session_id, human_id, agent_id, actor_id, evidence_lane, severity, status, summary, facts_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "evt-user-1",
                    "intent_committed",
                    "fact",
                    "spark_intelligence_builder",
                    "telegram_runtime",
                    "run-1",
                    "turn-1",
                    "telegram",
                    "session-1",
                    "human:test",
                    "agent:test",
                    "telegram_runtime",
                    "realworld_validated",
                    "medium",
                    "recorded",
                    "Turn committed.",
                    json.dumps({"message_text": "Please be more direct with me."}),
                ),
            )
            conn.execute(
                """
                INSERT INTO builder_events(
                    event_id, event_type, truth_kind, target_surface, component, run_id, request_id, channel_id,
                    session_id, human_id, agent_id, actor_id, evidence_lane, severity, status, summary, facts_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "evt-assistant-1",
                    "delivery_succeeded",
                    "delivery",
                    "spark_intelligence_builder",
                    "telegram_runtime",
                    "run-1",
                    "turn-1",
                    "telegram",
                    "session-1",
                    "human:test",
                    "agent:test",
                    "telegram_runtime",
                    "realworld_validated",
                    "medium",
                    "ok",
                    "Delivery succeeded.",
                    json.dumps({"delivered_text": "Noted. I will be more direct.", "ack_ref": "telegram:1"}),
                ),
            )
            conn.execute(
                """
                INSERT INTO builder_events(
                    event_id, event_type, truth_kind, target_surface, component, request_id, session_id, human_id,
                    actor_id, evidence_lane, severity, status, summary, facts_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "evt-mem-req-1",
                    "memory_write_requested",
                    "fact",
                    "spark_intelligence_builder",
                    "memory_orchestrator",
                    "turn-1",
                    "session-1",
                    "human:test",
                    "personality_loader",
                    "realworld_validated",
                    "medium",
                    "recorded",
                    "Memory write requested.",
                    json.dumps(
                        {
                            "observations": [
                                {
                                    "subject": "human:test",
                                    "predicate": "personality.preference.directness",
                                    "value": 0.35,
                                    "operation": "update",
                                    "memory_role": "current_state",
                                }
                            ]
                        }
                    ),
                ),
            )
            conn.execute(
                """
                INSERT INTO builder_events(
                    event_id, event_type, truth_kind, target_surface, component, request_id, session_id, human_id,
                    actor_id, evidence_lane, severity, status, summary, facts_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "evt-mem-ok-1",
                    "memory_write_succeeded",
                    "fact",
                    "spark_intelligence_builder",
                    "memory_orchestrator",
                    "turn-1",
                    "session-1",
                    "human:test",
                    "personality_loader",
                    "realworld_validated",
                    "medium",
                    "recorded",
                    "Memory write succeeded.",
                    json.dumps({"accepted_count": 1, "rejected_count": 0, "skipped_count": 0}),
                ),
            )
            conn.execute(
                """
                INSERT INTO builder_events(
                    event_id, event_type, truth_kind, target_surface, component, run_id, request_id, channel_id,
                    session_id, human_id, agent_id, actor_id, evidence_lane, severity, status, summary, facts_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "evt-user-2",
                    "intent_committed",
                    "fact",
                    "spark_intelligence_builder",
                    "telegram_runtime",
                    "run-2",
                    "turn-2",
                    "telegram",
                    "session-1",
                    "human:test",
                    "agent:test",
                    "telegram_runtime",
                    "realworld_validated",
                    "medium",
                    "recorded",
                    "Turn committed.",
                    json.dumps({"message_text": "Actually, be much more blunt."}),
                ),
            )
            conn.execute(
                """
                INSERT INTO builder_events(
                    event_id, event_type, truth_kind, target_surface, component, request_id, session_id, human_id,
                    actor_id, evidence_lane, severity, status, summary, facts_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "evt-mem-req-2",
                    "memory_write_requested",
                    "fact",
                    "spark_intelligence_builder",
                    "memory_orchestrator",
                    "turn-2",
                    "session-1",
                    "human:test",
                    "personality_loader",
                    "realworld_validated",
                    "medium",
                    "recorded",
                    "Memory write requested.",
                    json.dumps(
                        {
                            "observations": [
                                {
                                    "subject": "human:test",
                                    "predicate": "personality.preference.directness",
                                    "value": 0.75,
                                    "operation": "update",
                                    "memory_role": "current_state",
                                }
                            ]
                        }
                    ),
                ),
            )
            conn.execute(
                """
                INSERT INTO builder_events(
                    event_id, event_type, truth_kind, target_surface, component, request_id, session_id, human_id,
                    actor_id, evidence_lane, severity, status, summary, facts_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "evt-mem-ok-2",
                    "memory_write_succeeded",
                    "fact",
                    "spark_intelligence_builder",
                    "memory_orchestrator",
                    "turn-2",
                    "session-1",
                    "human:test",
                    "personality_loader",
                    "realworld_validated",
                    "medium",
                    "recorded",
                    "Memory write succeeded.",
                    json.dumps({"accepted_count": 1, "rejected_count": 0, "skipped_count": 0}),
                ),
            )
            conn.commit()

        payload = build_shadow_replay_payload(
            state_db=self.state_db,
            conversation_limit=10,
            event_limit=100,
        )

        self.assertEqual(payload["writable_roles"], ["user"])
        self.assertEqual(len(payload["conversations"]), 1)
        conversation = payload["conversations"][0]
        self.assertEqual(conversation["conversation_id"], "session-1")
        self.assertEqual(len(conversation["turns"]), 3)
        self.assertEqual(conversation["turns"][0]["role"], "user")
        self.assertEqual(conversation["turns"][0]["content"], "Please be more direct with me.")
        self.assertEqual(conversation["turns"][0]["metadata"]["memory_kind"], "observation")
        self.assertEqual(conversation["turns"][0]["metadata"]["subject"], "human:test")
        self.assertEqual(conversation["turns"][0]["metadata"]["predicate"], "personality.preference.directness")
        self.assertEqual(conversation["turns"][0]["metadata"]["value"], 0.35)
        self.assertEqual(conversation["turns"][0]["metadata"]["operation"], "update")
        self.assertEqual(conversation["turns"][1]["role"], "assistant")
        self.assertEqual(conversation["turns"][1]["content"], "Noted. I will be more direct.")
        probe_types = {probe["probe_type"] for probe in conversation["probes"]}
        self.assertIn("current_state", probe_types)
        self.assertIn("evidence", probe_types)
        self.assertIn("historical_state", probe_types)
        historical_probe = next(probe for probe in conversation["probes"] if probe["probe_type"] == "historical_state")
        self.assertEqual(historical_probe["expected_value"], 0.35)

    def test_shadow_replay_payload_supports_bridge_native_memory_turns(self) -> None:
        with self.state_db.connect() as conn:
            conn.execute(
                """
                INSERT INTO builder_events(
                    event_id, event_type, truth_kind, target_surface, component, request_id, session_id, human_id,
                    actor_id, evidence_lane, severity, status, summary, created_at, facts_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "evt-bridge-write-req",
                    "memory_write_requested",
                    "fact",
                    "spark_intelligence_builder",
                    "memory_orchestrator",
                    "sim:write-1",
                    "session-bridge",
                    "telegram:test",
                    "researcher_bridge",
                    "realworld_validated",
                    "medium",
                    "recorded",
                    "Memory write requested.",
                    "2026-04-10T11:45:07Z",
                    json.dumps(
                        {
                            "memory_role": "current_state",
                            "method": "write_observation",
                            "observations": [
                                {
                                    "subject": "human:telegram:test",
                                    "predicate": "profile.startup_name",
                                    "value": "Seedify",
                                    "operation": "update",
                                    "memory_role": "current_state",
                                    "text": "My startup is Seedify.",
                                }
                            ],
                        }
                    ),
                ),
            )
            conn.execute(
                """
                INSERT INTO builder_events(
                    event_id, event_type, truth_kind, target_surface, component, request_id, session_id, human_id,
                    actor_id, evidence_lane, severity, status, summary, created_at, facts_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "evt-bridge-write-ok",
                    "memory_write_succeeded",
                    "fact",
                    "spark_intelligence_builder",
                    "memory_orchestrator",
                    "sim:write-1",
                    "session-bridge",
                    "telegram:test",
                    "researcher_bridge",
                    "realworld_validated",
                    "medium",
                    "recorded",
                    "Memory write succeeded.",
                    "2026-04-10T11:45:07Z",
                    json.dumps({"accepted_count": 1, "rejected_count": 0, "skipped_count": 0}),
                ),
            )
            conn.execute(
                """
                INSERT INTO builder_events(
                    event_id, event_type, truth_kind, target_surface, component, request_id, session_id, human_id,
                    actor_id, evidence_lane, severity, status, summary, created_at, facts_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "evt-bridge-write-result",
                    "tool_result_received",
                    "fact",
                    "spark_intelligence_builder",
                    "researcher_bridge",
                    "sim:write-1",
                    "session-bridge",
                    "telegram:test",
                    "researcher_bridge",
                    "realworld_validated",
                    "medium",
                    "recorded",
                    "Researcher bridge acknowledged a profile fact update directly from memory.",
                    "2026-04-10T11:45:07Z",
                    json.dumps(
                        {
                            "bridge_mode": "memory_profile_fact_update",
                            "routing_decision": "memory_profile_fact_observation",
                            "fact_name": "profile_startup_name",
                            "predicate": "profile.startup_name",
                            "value": "Seedify",
                            "operation": "update",
                            "keepability": "ephemeral_context",
                            "promotion_disposition": "not_promotable",
                        }
                    ),
                ),
            )
            conn.execute(
                """
                INSERT INTO builder_events(
                    event_id, event_type, truth_kind, target_surface, component, request_id, session_id, human_id,
                    actor_id, evidence_lane, severity, status, summary, created_at, facts_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "evt-bridge-query-influence",
                    "plugin_or_chip_influence_recorded",
                    "fact",
                    "spark_intelligence_builder",
                    "researcher_bridge",
                    "sim:query-1",
                    "session-bridge",
                    "telegram:test",
                    "researcher_bridge",
                    "realworld_validated",
                    "medium",
                    "recorded",
                    "Personality influence was recorded before bridge execution.",
                    "2026-04-10T11:45:08Z",
                    json.dumps(
                        {
                            "detected_profile_fact_query": {
                                "fact_name": "profile_startup_name",
                                "label": "startup",
                                "predicate": "profile.startup_name",
                                "query_kind": "single_fact",
                                "message_text": "What is my startup?",
                            }
                        }
                    ),
                ),
            )
            conn.execute(
                """
                INSERT INTO builder_events(
                    event_id, event_type, truth_kind, target_surface, component, request_id, session_id, human_id,
                    actor_id, evidence_lane, severity, status, summary, created_at, facts_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "evt-bridge-query-result",
                    "tool_result_received",
                    "fact",
                    "spark_intelligence_builder",
                    "researcher_bridge",
                    "sim:query-1",
                    "session-bridge",
                    "telegram:test",
                    "researcher_bridge",
                    "realworld_validated",
                    "medium",
                    "recorded",
                    "Researcher bridge answered a single-fact profile query directly from memory.",
                    "2026-04-10T11:45:08Z",
                    json.dumps(
                        {
                            "bridge_mode": "memory_profile_fact",
                            "routing_decision": "memory_profile_fact_query",
                            "fact_name": "profile_startup_name",
                            "predicate": "profile.startup_name",
                            "label": "startup",
                            "value_found": True,
                            "keepability": "ephemeral_context",
                            "promotion_disposition": "not_promotable",
                        }
                    ),
                ),
            )
            conn.commit()

        payload = build_shadow_replay_payload(
            state_db=self.state_db,
            conversation_limit=10,
            event_limit=100,
        )

        self.assertEqual(len(payload["conversations"]), 1)
        conversation = payload["conversations"][0]
        self.assertEqual(conversation["conversation_id"], "session-bridge")
        self.assertEqual(len(conversation["turns"]), 4)
        self.assertEqual(
            [turn["content"] for turn in conversation["turns"]],
            [
                "My startup is Seedify.",
                "I'll remember you created Seedify.",
                "What is my startup?",
                "You created Seedify.",
            ],
        )
        self.assertEqual(conversation["turns"][0]["metadata"]["memory_kind"], "observation")
        self.assertEqual(conversation["turns"][0]["metadata"]["predicate"], "profile.startup_name")
        probe_types = {probe["probe_type"] for probe in conversation["probes"]}
        self.assertEqual(probe_types, {"current_state", "evidence"})

    def test_shadow_replay_payload_preserves_explicit_identity_summary_prompt(self) -> None:
        with self.state_db.connect() as conn:
            conn.execute(
                """
                INSERT INTO builder_events(
                    event_id, event_type, truth_kind, target_surface, component, request_id, session_id, human_id,
                    actor_id, evidence_lane, severity, status, summary, created_at, facts_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "evt-identity-query-influence",
                    "plugin_or_chip_influence_recorded",
                    "fact",
                    "spark_intelligence_builder",
                    "researcher_bridge",
                    "sim:identity-query-1",
                    "session-identity-bridge",
                    "telegram:test",
                    "researcher_bridge",
                    "realworld_validated",
                    "medium",
                    "recorded",
                    "Personality influence was recorded before bridge execution.",
                    "2026-04-11T12:00:00Z",
                    json.dumps(
                        {
                            "detected_profile_fact_query": {
                                "fact_name": "profile_identity_summary",
                                "label": "identity summary",
                                "predicate_prefix": "profile.",
                                "query_kind": "identity_summary",
                                "message_text": "Give me a full profile summary with my latest location too.",
                            }
                        }
                    ),
                ),
            )
            conn.commit()

        payload = build_shadow_replay_payload(
            state_db=self.state_db,
            conversation_limit=10,
            event_limit=20,
        )

        self.assertEqual(len(payload["conversations"]), 1)
        conversation = payload["conversations"][0]
        self.assertEqual(len(conversation["turns"]), 1)
        self.assertEqual(
            conversation["turns"][0]["content"],
            "Give me a full profile summary with my latest location too.",
        )

    def test_shadow_replay_delete_probes_skip_current_and_target_pre_delete_history(self) -> None:
        with self.state_db.connect() as conn:
            events = [
                (
                    "evt-user-1",
                    "intent_committed",
                    "run-1",
                    "turn-1",
                    "session-delete",
                    "2026-03-27T10:00:00Z",
                    {"message_text": "My cat is named Nova."},
                ),
                (
                    "evt-user-2",
                    "intent_committed",
                    "run-2",
                    "turn-2",
                    "session-delete",
                    "2026-03-27T10:05:00Z",
                    {"message_text": "I do not have that cat anymore."},
                ),
            ]
            for event_id, event_type, run_id, request_id, session_id, created_at, facts in events:
                conn.execute(
                    """
                    INSERT INTO builder_events(
                        event_id, event_type, truth_kind, target_surface, component, run_id, request_id, channel_id,
                        session_id, human_id, agent_id, actor_id, evidence_lane, severity, status, summary, created_at, facts_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event_id,
                        event_type,
                        "fact",
                        "spark_intelligence_builder",
                        "telegram_runtime",
                        run_id,
                        request_id,
                        "telegram",
                        session_id,
                        "human:test",
                        "agent:test",
                        "telegram_runtime",
                        "realworld_validated",
                        "medium",
                        "recorded",
                        "Turn committed.",
                        created_at,
                        json.dumps(facts),
                    ),
                )
            for event_id, request_id, created_at, facts in (
                (
                    "evt-mem-req-1",
                    "turn-1",
                    "2026-03-27T10:00:01Z",
                    {
                        "observations": [
                            {
                                "subject": "human:test",
                                "predicate": "profile.pet_name",
                                "value": "Nova",
                                "operation": "update",
                                "memory_role": "current_state",
                            }
                        ]
                    },
                ),
                (
                    "evt-mem-req-2",
                    "turn-2",
                    "2026-03-27T10:05:01Z",
                    {
                        "observations": [
                            {
                                "subject": "human:test",
                                "predicate": "profile.pet_name",
                                "value": None,
                                "operation": "delete",
                                "memory_role": "current_state",
                            }
                        ]
                    },
                ),
            ):
                conn.execute(
                    """
                    INSERT INTO builder_events(
                        event_id, event_type, truth_kind, target_surface, component, request_id, session_id, human_id,
                        actor_id, evidence_lane, severity, status, summary, created_at, facts_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        event_id,
                        "memory_write_requested",
                        "fact",
                        "spark_intelligence_builder",
                        "memory_orchestrator",
                        request_id,
                        "session-delete",
                        "human:test",
                        "personality_loader",
                        "realworld_validated",
                        "medium",
                        "recorded",
                        "Memory write requested.",
                        created_at,
                        json.dumps(facts),
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO builder_events(
                        event_id, event_type, truth_kind, target_surface, component, request_id, session_id, human_id,
                        actor_id, evidence_lane, severity, status, summary, created_at, facts_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"{event_id}:ok",
                        "memory_write_succeeded",
                        "fact",
                        "spark_intelligence_builder",
                        "memory_orchestrator",
                        request_id,
                        "session-delete",
                        "human:test",
                        "personality_loader",
                        "realworld_validated",
                        "medium",
                        "recorded",
                        "Memory write succeeded.",
                        created_at,
                        json.dumps({"accepted_count": 1, "rejected_count": 0, "skipped_count": 0}),
                    ),
                )
            conn.commit()

        payload = build_shadow_replay_payload(
            state_db=self.state_db,
            conversation_limit=10,
            event_limit=100,
        )

        conversation = payload["conversations"][0]
        probes = conversation["probes"]
        current_state_probes = [probe for probe in probes if probe["probe_type"] == "current_state"]
        evidence_probes = [probe for probe in probes if probe["probe_type"] == "evidence"]
        historical_probes = [probe for probe in probes if probe["probe_type"] == "historical_state"]

        self.assertEqual(len(current_state_probes), 0)
        self.assertEqual(len(evidence_probes), 2)
        self.assertEqual(len(historical_probes), 0)

    def test_shadow_replay_event_memory_omits_observation_probes(self) -> None:
        with self.state_db.connect() as conn:
            conn.execute(
                """
                INSERT INTO builder_events(
                    event_id, event_type, truth_kind, target_surface, component, run_id, request_id, channel_id,
                    session_id, human_id, agent_id, actor_id, evidence_lane, severity, status, summary, created_at, facts_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "evt-user-event",
                    "intent_committed",
                    "fact",
                    "spark_intelligence_builder",
                    "telegram_runtime",
                    "run-event",
                    "turn-event",
                    "telegram",
                    "session-event",
                    "human:test",
                    "agent:test",
                    "telegram_runtime",
                    "realworld_validated",
                    "medium",
                    "recorded",
                    "Turn committed.",
                    "2026-03-27T11:00:00Z",
                    json.dumps({"message_text": "I booked a dentist appointment for April 4 at 3pm."}),
                ),
            )
            conn.execute(
                """
                INSERT INTO builder_events(
                    event_id, event_type, truth_kind, target_surface, component, request_id, session_id, human_id,
                    actor_id, evidence_lane, severity, status, summary, created_at, facts_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "evt-mem-req-event",
                    "memory_write_requested",
                    "fact",
                    "spark_intelligence_builder",
                    "memory_orchestrator",
                    "turn-event",
                    "session-event",
                    "human:test",
                    "personality_loader",
                    "realworld_validated",
                    "medium",
                    "recorded",
                    "Memory write requested.",
                    "2026-03-27T11:00:01Z",
                    json.dumps(
                        {
                            "observations": [
                                {
                                    "subject": "human:test",
                                    "predicate": "calendar.dentist_visit.at",
                                    "value": "2026-04-04T15:00:00+04:00",
                                    "operation": "event",
                                    "memory_role": "event",
                                }
                            ]
                        }
                    ),
                ),
            )
            conn.execute(
                """
                INSERT INTO builder_events(
                    event_id, event_type, truth_kind, target_surface, component, request_id, session_id, human_id,
                    actor_id, evidence_lane, severity, status, summary, created_at, facts_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "evt-mem-ok-event",
                    "memory_write_succeeded",
                    "fact",
                    "spark_intelligence_builder",
                    "memory_orchestrator",
                    "turn-event",
                    "session-event",
                    "human:test",
                    "personality_loader",
                    "realworld_validated",
                    "medium",
                    "recorded",
                    "Memory write succeeded.",
                    "2026-03-27T11:00:02Z",
                    json.dumps({"accepted_count": 1, "rejected_count": 0, "skipped_count": 0}),
                ),
            )
            conn.commit()

        payload = build_shadow_replay_payload(
            state_db=self.state_db,
            conversation_limit=10,
            event_limit=100,
        )

        conversation = payload["conversations"][0]
        self.assertEqual(conversation["turns"][0]["metadata"]["memory_kind"], "event")
        self.assertEqual(conversation["turns"][0]["metadata"]["memory_role"], "event")
        self.assertNotIn("probes", conversation)

    def test_shadow_replay_omits_invalid_memory_role_observations(self) -> None:
        with self.state_db.connect() as conn:
            conn.execute(
                """
                INSERT INTO builder_events(
                    event_id, event_type, truth_kind, target_surface, component, run_id, request_id, channel_id,
                    session_id, human_id, agent_id, actor_id, evidence_lane, severity, status, summary, created_at, facts_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "evt-user-invalid-role",
                    "intent_committed",
                    "fact",
                    "spark_intelligence_builder",
                    "telegram_runtime",
                    "run-invalid-role",
                    "turn-invalid-role",
                    "telegram",
                    "session-invalid-role",
                    "human:test",
                    "agent:test",
                    "telegram_runtime",
                    "realworld_validated",
                    "medium",
                    "recorded",
                    "Turn committed.",
                    "2026-03-27T12:00:00Z",
                    json.dumps({"message_text": "Remember this."}),
                ),
            )
            conn.execute(
                """
                INSERT INTO builder_events(
                    event_id, event_type, truth_kind, target_surface, component, request_id, session_id, human_id,
                    actor_id, evidence_lane, severity, status, summary, created_at, facts_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "evt-mem-req-invalid-role",
                    "memory_write_requested",
                    "fact",
                    "spark_intelligence_builder",
                    "memory_orchestrator",
                    "turn-invalid-role",
                    "session-invalid-role",
                    "human:test",
                    "personality_loader",
                    "realworld_validated",
                    "medium",
                    "recorded",
                    "Memory write requested.",
                    "2026-03-27T12:00:01Z",
                    json.dumps(
                        {
                            "observations": [
                                {
                                    "subject": "human:test",
                                    "predicate": "profile.city",
                                    "value": "Dubai",
                                    "operation": "update",
                                    "memory_role": "timeline",
                                }
                            ]
                        }
                    ),
                ),
            )
            conn.execute(
                """
                INSERT INTO builder_events(
                    event_id, event_type, truth_kind, target_surface, component, request_id, session_id, human_id,
                    actor_id, evidence_lane, severity, status, summary, created_at, facts_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "evt-mem-ok-invalid-role",
                    "memory_write_succeeded",
                    "fact",
                    "spark_intelligence_builder",
                    "memory_orchestrator",
                    "turn-invalid-role",
                    "session-invalid-role",
                    "human:test",
                    "personality_loader",
                    "realworld_validated",
                    "medium",
                    "recorded",
                    "Memory write succeeded.",
                    "2026-03-27T12:00:02Z",
                    json.dumps({"accepted_count": 1, "rejected_count": 0, "skipped_count": 0}),
                ),
            )
            conn.commit()

        payload = build_shadow_replay_payload(
            state_db=self.state_db,
            conversation_limit=10,
            event_limit=100,
        )

        conversation = payload["conversations"][0]
        self.assertNotIn("memory_kind", conversation["turns"][0].get("metadata") or {})
        self.assertNotIn("probes", conversation)

    def test_shadow_replay_batch_export_runs_validation_and_batch_report(self) -> None:
        with self.state_db.connect() as conn:
            for index in range(1, 4):
                conn.execute(
                    """
                    INSERT INTO builder_events(
                        event_id, event_type, truth_kind, target_surface, component, run_id, request_id, channel_id,
                        session_id, human_id, agent_id, actor_id, evidence_lane, severity, status, summary, facts_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"evt-user-{index}",
                        "intent_committed",
                        "fact",
                        "spark_intelligence_builder",
                        "telegram_runtime",
                        f"run-{index}",
                        f"turn-{index}",
                        "telegram",
                        f"session-{index}",
                        "human:test",
                        "agent:test",
                        "telegram_runtime",
                        "realworld_validated",
                        "medium",
                        "recorded",
                        "Turn committed.",
                        json.dumps({"message_text": f"Turn {index}"}),
                    ),
                )
            conn.commit()

        with patch(
            "spark_intelligence.memory.shadow_replay.run_governed_command",
            side_effect=[
                SimpleNamespace(exit_code=0, stdout=json.dumps({"valid": True, "errors": [], "warnings": []}), stderr=""),
                SimpleNamespace(
                    exit_code=0,
                    stdout=json.dumps({"report": {"run_count": 3, "summary": {"accepted_writes": 0, "rejected_writes": 0, "skipped_turns": 3}}}),
                    stderr="",
                ),
            ],
        ) as governed:
            result = export_shadow_replay_batch(
                config_manager=self.config_manager,
                state_db=self.state_db,
                conversation_limit=3,
                conversations_per_file=2,
                validate=True,
                run_report=True,
                report_write_path=self.home / "artifacts" / "spark-shadow-batch-report.json",
            )

        self.assertEqual(len(result.files), 2)
        self.assertTrue(result.validation["valid"])
        self.assertEqual(result.report["report"]["run_count"], 3)
        self.assertEqual(governed.call_count, 2)

    def test_sdk_maintenance_payload_uses_only_accepted_explicit_memory_writes(self) -> None:
        with self.state_db.connect() as conn:
            conn.execute(
                """
                INSERT INTO builder_events(
                    event_id, event_type, truth_kind, target_surface, component, request_id, session_id, human_id,
                    actor_id, evidence_lane, severity, status, summary, created_at, facts_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "evt-user-maint-1",
                    "intent_committed",
                    "fact",
                    "spark_intelligence_builder",
                    "telegram_runtime",
                    "turn-maint-1",
                    "session-maint",
                    "human:test",
                    "telegram_runtime",
                    "realworld_validated",
                    "medium",
                    "recorded",
                    "Turn committed.",
                    "2026-03-27T10:00:00Z",
                    json.dumps({"message_text": "I moved to Dubai."}),
                ),
            )
            conn.execute(
                """
                INSERT INTO builder_events(
                    event_id, event_type, truth_kind, target_surface, component, request_id, session_id, human_id,
                    actor_id, evidence_lane, severity, status, summary, created_at, facts_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "evt-mem-req-maint-1",
                    "memory_write_requested",
                    "fact",
                    "spark_intelligence_builder",
                    "memory_orchestrator",
                    "turn-maint-1",
                    "session-maint",
                    "human:test",
                    "personality_loader",
                    "realworld_validated",
                    "medium",
                    "recorded",
                    "Memory write requested.",
                    "2026-03-27T10:00:01Z",
                    json.dumps(
                        {
                            "operation": "update",
                            "method": "write_observation",
                            "observations": [
                                {
                                    "subject": "human:test",
                                    "predicate": "profile.city",
                                    "value": "Dubai",
                                    "operation": "update",
                                    "memory_role": "current_state",
                                    "retention_class": "active_state",
                                    "metadata": {
                                        "entity_key": "primary",
                                        "revalidate_at": "2026-04-26T10:00:01Z",
                                    },
                                }
                            ],
                        }
                    ),
                ),
            )
            conn.execute(
                """
                INSERT INTO builder_events(
                    event_id, event_type, truth_kind, target_surface, component, request_id, session_id, human_id,
                    actor_id, evidence_lane, severity, status, summary, created_at, facts_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "evt-mem-ok-maint-1",
                    "memory_write_succeeded",
                    "fact",
                    "spark_intelligence_builder",
                    "memory_orchestrator",
                    "turn-maint-1",
                    "session-maint",
                    "human:test",
                    "personality_loader",
                    "realworld_validated",
                    "medium",
                    "recorded",
                    "Memory write succeeded.",
                    "2026-03-27T10:00:02Z",
                    json.dumps({"accepted_count": 1, "rejected_count": 0, "skipped_count": 0}),
                ),
            )
            conn.execute(
                """
                INSERT INTO builder_events(
                    event_id, event_type, truth_kind, target_surface, component, request_id, session_id, human_id,
                    actor_id, evidence_lane, severity, status, summary, created_at, facts_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "evt-mem-req-maint-2",
                    "memory_write_requested",
                    "fact",
                    "spark_intelligence_builder",
                    "memory_orchestrator",
                    "turn-maint-2",
                    "session-maint",
                    "human:test",
                    "personality_loader",
                    "realworld_validated",
                    "medium",
                    "recorded",
                    "Memory write requested.",
                    "2026-03-27T10:05:00Z",
                    json.dumps(
                        {
                            "operation": "update",
                            "method": "write_observation",
                            "observations": [
                                {
                                    "subject": "human:test",
                                    "predicate": "profile.city",
                                    "value": "Abu Dhabi",
                                    "operation": "update",
                                    "memory_role": "current_state",
                                }
                            ],
                        }
                    ),
                ),
            )
            conn.execute(
                """
                INSERT INTO builder_events(
                    event_id, event_type, truth_kind, target_surface, component, request_id, session_id, human_id,
                    actor_id, evidence_lane, severity, status, summary, created_at, facts_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "evt-mem-ok-maint-2",
                    "memory_write_succeeded",
                    "fact",
                    "spark_intelligence_builder",
                    "memory_orchestrator",
                    "turn-maint-2",
                    "session-maint",
                    "human:test",
                    "personality_loader",
                    "realworld_validated",
                    "medium",
                    "recorded",
                    "Memory write succeeded.",
                    "2026-03-27T10:05:01Z",
                    json.dumps({"accepted_count": 0, "rejected_count": 1, "skipped_count": 0}),
                ),
            )
            conn.commit()

        payload = build_sdk_maintenance_payload(
            state_db=self.state_db,
            event_limit=100,
        )

        self.assertEqual(len(payload["writes"]), 1)
        write = payload["writes"][0]
        self.assertEqual(write["write_kind"], "observation")
        self.assertEqual(write["text"], "I moved to Dubai.")
        self.assertEqual(write["subject"], "human:test")
        self.assertEqual(write["predicate"], "profile.city")
        self.assertEqual(write["value"], "Dubai")
        self.assertEqual(write["retention_class"], "active_state")
        self.assertEqual(write["metadata"]["retention_class"], "active_state")
        self.assertEqual(write["metadata"]["revalidate_at"], "2026-04-26T10:00:01Z")
        self.assertEqual(write["metadata"]["entity_key"], "primary")
        self.assertEqual(payload["checks"]["current_state"][0]["predicate"], "profile.city")
        self.assertNotIn("historical_state", payload["checks"])


class RuntimeArchitecturePinTests(SparkTestCase):
    def test_pin_constants_exported(self) -> None:
        self.assertEqual(
            memory_orchestrator.PINNED_RUNTIME_MEMORY_ARCHITECTURE,
            "summary_synthesis_memory",
        )
        self.assertEqual(
            memory_orchestrator.PINNED_RUNTIME_MEMORY_PROVIDER,
            "heuristic_v1",
        )

    def test_apply_pin_sets_env_when_absent(self) -> None:
        import os
        saved_arch = os.environ.pop("SPARK_MEMORY_RUNTIME_ARCHITECTURE", None)
        saved_prov = os.environ.pop("SPARK_MEMORY_RUNTIME_PROVIDER", None)
        try:
            memory_orchestrator._apply_runtime_architecture_pin()
            self.assertEqual(
                os.environ["SPARK_MEMORY_RUNTIME_ARCHITECTURE"],
                memory_orchestrator.PINNED_RUNTIME_MEMORY_ARCHITECTURE,
            )
            self.assertEqual(
                os.environ["SPARK_MEMORY_RUNTIME_PROVIDER"],
                memory_orchestrator.PINNED_RUNTIME_MEMORY_PROVIDER,
            )
        finally:
            if saved_arch is not None:
                os.environ["SPARK_MEMORY_RUNTIME_ARCHITECTURE"] = saved_arch
            if saved_prov is not None:
                os.environ["SPARK_MEMORY_RUNTIME_PROVIDER"] = saved_prov

    def test_apply_pin_respects_existing_env(self) -> None:
        import os
        saved = os.environ.get("SPARK_MEMORY_RUNTIME_ARCHITECTURE")
        os.environ["SPARK_MEMORY_RUNTIME_ARCHITECTURE"] = "summary_synthesis_memory"
        try:
            memory_orchestrator._apply_runtime_architecture_pin()
            self.assertEqual(
                os.environ["SPARK_MEMORY_RUNTIME_ARCHITECTURE"],
                "summary_synthesis_memory",
            )
        finally:
            if saved is None:
                os.environ.pop("SPARK_MEMORY_RUNTIME_ARCHITECTURE", None)
            else:
                os.environ["SPARK_MEMORY_RUNTIME_ARCHITECTURE"] = saved


class PredicateRegistryConsolidationTests(SparkTestCase):
    _EXPECTED_PREDICATE_DAYS = {
        "profile.current_plan": 30,
        "profile.current_focus": 21,
        "profile.current_decision": 30,
        "profile.current_blocker": 14,
        "profile.current_status": 14,
        "profile.current_commitment": 21,
        "profile.current_milestone": 21,
        "profile.current_risk": 14,
        "profile.current_dependency": 14,
        "profile.current_constraint": 14,
        "profile.current_assumption": 30,
        "profile.current_owner": 21,
    }

    def test_pack_carries_revalidation_days_for_all_current_state_predicates(self) -> None:
        from spark_intelligence.memory.generic_observations import pack_revalidation_days
        for predicate, expected in self._EXPECTED_PREDICATE_DAYS.items():
            self.assertEqual(
                pack_revalidation_days(predicate),
                expected,
                msg=f"pack_revalidation_days({predicate!r}) should resolve via TelegramGenericPack registry",
            )

    def test_retention_policy_prefers_pack_then_falls_back_to_orphan_dict(self) -> None:
        from spark_intelligence.memory.retention_policy import active_state_revalidation_days_for
        for predicate, expected in self._EXPECTED_PREDICATE_DAYS.items():
            self.assertEqual(active_state_revalidation_days_for(predicate), expected)
        for orphan in (
            "telegram.summary.latest_meeting",
            "telegram.summary.latest_deadline",
            "telegram.summary.latest_shipped",
        ):
            self.assertEqual(active_state_revalidation_days_for(orphan), 14)
        self.assertIsNone(active_state_revalidation_days_for("bogus.predicate"))
        self.assertIsNone(active_state_revalidation_days_for(None))

    def test_profile_facts_helper_uses_unified_lookup(self) -> None:
        from spark_intelligence.memory.profile_facts import active_state_revalidation_days
        self.assertEqual(active_state_revalidation_days("profile.current_plan"), 30)
        self.assertEqual(active_state_revalidation_days("telegram.summary.latest_meeting"), 14)
        self.assertIsNone(active_state_revalidation_days("bogus.predicate"))
