from __future__ import annotations

import json
from pathlib import Path

from spark_intelligence.self_awareness import build_agent_operating_context
from spark_intelligence.self_awareness.operating_panel import build_agent_operating_panel
from spark_intelligence.self_awareness.system_map_read_model import build_spark_system_map_context

from tests.test_support import SparkTestCase


class SystemMapReadModelTests(SparkTestCase):
    def test_system_map_context_summarizes_without_exporting_unknown_values(self) -> None:
        system_map_dir = self._write_compiled_system_map(raw_sentinel="telegram.bot_token=secret")

        context = build_spark_system_map_context(self.config_manager)
        encoded = json.dumps(context)

        self.assertTrue(context["present"])
        self.assertEqual(context["source_ref"], "spark os compile")
        self.assertEqual(context["counts"]["modules"], 2)
        self.assertEqual(context["counts"]["repos"], 3)
        self.assertEqual(context["counts"]["gaps"], 1)
        self.assertEqual(context["counts"]["chip_manifests"], 2)
        self.assertEqual(context["counts"]["skill_graphs"], 1)
        self.assertEqual(context["counts"]["creator_system_surfaces"], 1)
        self.assertEqual(context["counts"]["specialization_path_surfaces"], 1)
        self.assertEqual(context["counts"]["capability_cards"], 2)
        self.assertEqual(context["counts"]["authority_sources"], 2)
        self.assertEqual(context["counts"]["builder_event_rows"], 123)
        self.assertEqual(context["counts"]["builder_event_samples"], 3)
        self.assertEqual(context["counts"]["builder_trace_groups"], 2)
        self.assertEqual(context["counts"]["builder_trace_topology_groups"], 2)
        self.assertEqual(context["counts"]["trace_health_flags"], 3)
        self.assertEqual(context["counts"]["spawner_prd_request_ids"], 2)
        self.assertEqual(context["counts"]["spawner_prd_derived_trace_refs"], 1)
        self.assertEqual(context["counts"]["spawner_builder_trace_ref_overlaps"], 1)
        self.assertEqual(context["cross_system_trace"]["spawner_trace_contract_status"], "derived_available")
        self.assertEqual(context["cross_system_trace"]["telegram_final_answer_trace_join_status"], "join_key_present")
        self.assertEqual(context["trace_health"]["missing_trace_ref_count"], 8)
        self.assertEqual(context["trace_health"]["high_severity_open_count"], 1)
        self.assertEqual(context["trace_health"]["orphan_parent_event_id_count"], 1)
        self.assertEqual(context["trace_health"]["missing_trace_ref_sources"]["row_count"], 2)
        self.assertEqual(
            context["trace_health"]["missing_trace_ref_sources"]["rows"][0]["component"],
            "memory_orchestrator",
        )
        self.assertEqual(
            context["trace_health"]["orphan_parent_event_sources"]["rows"][0]["component"],
            "workflow_recovery",
        )
        self.assertEqual(context["trace_topology"]["group_count"], 2)
        self.assertEqual(context["trace_topology"]["parent_link_count"], 1)
        self.assertEqual(context["trace_topology"]["groups"][0]["topology"]["edge_sample"][0]["child_event_id"], "evt-2")
        self.assertEqual(context["trace_health"]["recent_windows"][0]["window"], "24h")
        self.assertEqual(context["trace_health"]["recent_windows"][0]["missing_trace_ref_ratio"], 0.25)
        self.assertEqual(context["memory_movement"]["status"], "supported")
        self.assertEqual(context["memory_movement"]["row_count"], 42)
        self.assertEqual(context["memory_movement"]["movement_counts"]["saved"], 7)
        self.assertEqual(context["capability_garden"]["card_count"], 2)
        self.assertEqual(context["capability_garden"]["status_counts"]["local-artifacts"], 1)
        self.assertEqual(context["capability_garden"]["cards"][0]["id"], "creator-system:spark-domain-chip-labs")
        self.assertIn("Network publication approval", context["capability_garden"]["cards"][0]["blockers"][0])
        self.assertEqual(context["output_dir"], str(system_map_dir))
        self.assertNotIn("telegram.bot_token", encoded)
        self.assertNotIn("telegram.bot_token=secret", encoded)
        self.assertNotIn("secret command", encoded)

    def test_aoc_includes_system_map_as_read_only_source(self) -> None:
        self._write_compiled_system_map()

        context = build_agent_operating_context(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-system-map-aoc",
            user_message="what can Spark see across the system?",
        )
        payload = context.to_payload()

        self.assertTrue(payload["spark_system_map"]["present"])
        self.assertEqual(payload["spark_system_map"]["authority"], "observability_non_authoritative")
        self.assertEqual(payload["spark_system_map"]["counts"]["modules"], 2)
        self.assertTrue(
            any(
                item["source"] == "spark_os_system_map"
                and item["role"] == "cross_repo_system_truth_snapshot"
                and item["present"]
                for item in payload["source_ledger"]
            )
        )
        self.assertIn(
            "Spark OS map: 2 modules, 3 repos, 2 chips, 1 gaps, memory movement supported (42 rows), black-box samples 3, trace groups 2, trace health flags 3, trace topology 2 groups, spawner trace refs 1, capability cards 2",
            context.to_text(),
        )

    def test_panel_source_ledger_receives_system_map_source(self) -> None:
        self._write_compiled_system_map()

        panel = build_agent_operating_panel(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-system-map-panel",
            user_message="show the operating panel",
        ).to_payload()
        source_items = panel["source_ledger"]["items"]

        system_map_source = next(item for item in source_items if item["source"] == "spark_os_system_map")
        sections = {section["section_id"]: section for section in panel["sections"]["sections"]}
        self.assertTrue(system_map_source["present"])
        self.assertEqual(system_map_source["freshness"], "fresh")
        self.assertEqual(
            system_map_source["summary"],
            "2 modules, 3 repos, 1 gaps, memory rows 42, black-box samples 3, trace groups 2, trace health flags 3, spawner trace refs 1, capability cards 2",
        )
        self.assertEqual(panel["trace_repair_queue"]["status"], "needs_repair")
        self.assertEqual(panel["trace_repair_queue"]["counts"]["missing_trace_ref_count"], 8)
        self.assertEqual(panel["trace_repair_queue"]["counts"]["orphan_parent_event_id_count"], 1)
        self.assertEqual(panel["trace_repair_queue"]["trace_topology"]["group_count"], 2)
        self.assertEqual(
            panel["trace_repair_queue"]["top_missing_trace_ref_sources"][0]["component"],
            "memory_orchestrator",
        )
        self.assertEqual(
            panel["trace_repair_queue"]["top_orphan_parent_sources"][0]["component"],
            "workflow_recovery",
        )
        self.assertEqual(sections["trace_repair_queue"]["status"], "needs_repair")
        self.assertEqual(sections["capability_garden"]["status"], "review_needed")
        self.assertTrue(
            any(
                item["label"] == "creator-system:spark-domain-chip-labs"
                for item in sections["capability_garden"]["items"]
            )
        )
        self.assertTrue(
            any(
                item["label"] == "memory_orchestrator/memory_read_requested"
                for item in sections["trace_repair_queue"]["items"]
            )
        )
        self.assertTrue(
            any(
                item["label"] == "Orphan workflow_recovery/lesson_promoted"
                for item in sections["trace_repair_queue"]["items"]
            )
        )

    def _write_compiled_system_map(self, *, raw_sentinel: str = "") -> Path:
        system_map_dir = self.home / "system-map"
        system_map_dir.mkdir(parents=True)
        self.config_manager.set_path("spark.system_map.output_dir", str(system_map_dir))
        (system_map_dir / "system-map.json").write_text(
            json.dumps(
                {
                    "schema_version": "spark.system_map.compiled.v0",
                    "generated_at": "2026-05-10T13:21:20Z",
                    "privacy": {
                        "raw_secret_values_read": False,
                        "raw_logs_read": False,
                        "raw_conversation_content_read": False,
                        "raw_memory_evidence_read": False,
                        "sqlite_row_contents_read": False,
                    },
                    "modules": [{"id": "spark-cli"}, {"id": "spark-intelligence-builder"}],
                    "discovered_repos": [{"name": "a"}, {"name": "b"}, {"name": "c"}],
                    "gaps": [{"severity": "decision"}],
                    "unknown_future_field": raw_sentinel,
                }
            ),
            encoding="utf-8",
        )
        (system_map_dir / "authority-view.json").write_text(
            json.dumps(
                {
                    "schema_version": "spark.authority_view.compiled.v0",
                    "observed_sources": {
                        "cli_access_policy": {"exists": True},
                        "telegram_access_policy": {"exists": True},
                        "browser_policy": {"exists": False},
                    },
                }
            ),
            encoding="utf-8",
        )
        (system_map_dir / "capability-catalog.json").write_text(
            json.dumps(
                {
                    "schema_version": "spark.capability_catalog.compiled.v0",
                    "chip_manifests": [{"chip_name": "memory"}, {"chip_name": "browser"}],
                    "skill_graphs": [{"repo": "spark-skill-graphs"}],
                    "creator_system_surfaces": [{"repo": "spark-domain-chip-labs"}],
                    "specialization_path_surfaces": [{"repo": "spark-swarm"}],
                    "capability_cards": [
                        {
                            "schema_version": "spark.capability_card.v1",
                            "id": "creator-system:spark-domain-chip-labs",
                            "name": "Spark Domain Chip Labs",
                            "unknown_future_field": "secret command should stay out",
                            "owner_repo": "spark-domain-chip-labs",
                            "surface_type": "creator-system",
                            "status": "local-artifacts",
                            "requested_authority": ["local_files_read", "review_only"],
                            "memory_policy": "non_authoritative_evidence_only",
                            "evidence_summary": {"schema_count": 56, "creator_run_count": 1},
                            "benchmark_summary": {"benchmark_manifest_count": 1},
                            "review_summary": {"review_source_count": 4},
                            "blockers": ["Network publication approval is not compiled into the card yet."],
                            "next_action": "Normalize review verdicts.",
                            "privacy_boundary": "Raw packet bodies are not exported.",
                            "public_boundary": "Network publication is blocked.",
                        },
                        {
                            "schema_version": "spark.capability_card.v1",
                            "id": "specialization-path:spark-swarm",
                            "owner_repo": "spark-swarm",
                            "surface_type": "specialization-path",
                            "status": "schema-shaped",
                            "requested_authority": ["local_files_read", "review_only"],
                            "memory_policy": "selective_or_surface_defined",
                            "evidence_summary": {"configured_path_count": 5, "schema_count": 6},
                            "benchmark_summary": {"benchmark_adapter_counts": {"startup-bench": 2}},
                            "review_summary": {"publication_governance_source_count": 7},
                            "blockers": ["Publication approval verdict is not compiled into the card yet."],
                            "next_action": "Normalize benchmark verdicts.",
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )
        (system_map_dir / "trace-index.json").write_text(
            json.dumps(
                {
                    "schema_version": "spark.trace_index.compiled.v0",
                    "builder_events": {"row_count": 123},
                    "builder_event_samples": {"sample_count": 3},
                    "builder_trace_groups": {
                        "group_count": 2,
                        "groups": [
                            {
                                "trace_ref": "trace-1",
                                "event_count": 2,
                                "first_seen_at": "2026-05-10T13:00:00Z",
                                "last_seen_at": "2026-05-10T13:01:00Z",
                                "topology": {
                                    "available": True,
                                    "root_event_count": 1,
                                    "parent_link_count": 1,
                                    "orphan_parent_event_count": 0,
                                    "edge_sample_count": 1,
                                    "edge_sample": [
                                        {
                                            "parent_event_id": "evt-1",
                                            "child_event_id": "evt-2",
                                            "parent_event_type": "intent_committed",
                                            "child_event_type": "route_selected",
                                            "child_component": "router",
                                            "parent_exists": True,
                                            "parent_in_same_trace": True,
                                        }
                                    ],
                                },
                            }
                        ],
                    },
                    "builder_trace_health": {
                        "health_flags": [
                            "missing_trace_refs",
                            "open_high_severity_events",
                            "orphan_parent_event_ids",
                        ],
                        "missing_trace_ref_count": 8,
                        "high_severity_open_count": 1,
                        "orphan_parent_event_id_count": 1,
                        "trace_group_count": 2,
                        "missing_trace_ref_sources": {
                            "group_by": [
                                "component",
                                "event_type",
                                "status",
                                "severity",
                                "target_surface",
                                "evidence_lane",
                            ],
                            "rows": [
                                {
                                    "component": "memory_orchestrator",
                                    "event_type": "memory_read_requested",
                                    "status": "recorded",
                                    "severity": "medium",
                                    "target_surface": "spark_intelligence_builder",
                                    "evidence_lane": "realworld_validated",
                                    "event_count": 5,
                                },
                                {
                                    "component": "researcher_bridge",
                                    "event_type": "tool_result_received",
                                    "status": "recorded",
                                    "severity": "medium",
                                    "target_surface": "spark_intelligence_builder",
                                    "evidence_lane": "realworld_validated",
                                    "event_count": 3,
                                },
                            ],
                        },
                        "orphan_parent_event_sources": {
                            "group_by": [
                                "component",
                                "event_type",
                                "status",
                                "severity",
                                "target_surface",
                                "evidence_lane",
                            ],
                            "rows": [
                                {
                                    "component": "workflow_recovery",
                                    "event_type": "lesson_promoted",
                                    "status": "recorded",
                                    "severity": "medium",
                                    "target_surface": "spark_intelligence_builder",
                                    "evidence_lane": "realworld_validated",
                                    "event_count": 1,
                                }
                            ],
                        },
                        "recent_windows": [
                            {
                                "window": "24h",
                                "threshold": "2026-05-09T14:42:28Z",
                                "row_count": 4,
                                "missing_trace_ref_count": 1,
                                "missing_trace_ref_ratio": 0.25,
                            }
                        ],
                    },
                    "spawner_prd_auto_trace_samples": {
                        "join_keys": {
                            "request_id_count": 2,
                            "mission_id_count": 1,
                            "trace_ref_count": 0,
                            "derived_trace_ref_count": 1,
                        },
                        "derived_trace_contract": {
                            "scheme": "trace:spawner-prd:<missionId>",
                            "source": "missionId",
                            "status": "derived_available",
                        },
                        "builder_request_overlap": {"matched_builder_request_id_count": 0},
                        "builder_trace_ref_overlap": {"matched_builder_trace_ref_count": 1},
                    },
                    "telegram_final_answer_gate_samples": {
                        "trace_join": {
                            "request_id_field_present": True,
                            "trace_ref_field_present": True,
                            "status": "join_key_present",
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
        (system_map_dir / "memory-movement-index.json").write_text(
            json.dumps(
                {
                    "schema_version": "spark.memory_movement_index.compiled.v0",
                    "authority": "observability_non_authoritative",
                    "builder_memory_tables": {"table_count": 1},
                    "safe_status_export": {
                        "exists": True,
                        "status": {
                            "status": "supported",
                            "row_count": 42,
                            "movement_counts": {"captured": 9, "saved": 7},
                        },
                    },
                    "unknown_future_field": raw_sentinel,
                }
            ),
            encoding="utf-8",
        )
        (system_map_dir / "gaps.md").write_text("# gaps\n", encoding="utf-8")
        return system_map_dir
