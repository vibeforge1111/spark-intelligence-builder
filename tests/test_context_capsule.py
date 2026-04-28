from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from spark_intelligence.auth.runtime import RuntimeProviderResolution
from spark_intelligence.context import build_spark_context_capsule
from spark_intelligence.observability.store import latest_events_by_type, record_event
from spark_intelligence.researcher_bridge.advisory import ResearcherProviderSelection, build_researcher_reply

from tests.test_support import SparkTestCase


class ContextCapsuleTests(SparkTestCase):
    def _memory_inspection(self) -> SimpleNamespace:
        return SimpleNamespace(
            read_result=SimpleNamespace(
                records=[
                    {
                        "predicate": "profile.current_focus",
                        "value": "diagnostics scan verification",
                        "timestamp": "2026-04-27T12:13:00Z",
                    },
                    {
                        "predicate": "profile.current_focus",
                        "value": "automatic memory maintenance verification",
                        "timestamp": "2026-04-27T12:55:00Z",
                    },
                    {
                        "predicate": "profile.current_plan",
                        "value": "verify scheduled memory cleanup",
                        "timestamp": "2026-04-27T12:56:00Z",
                    },
                ]
            )
        )

    def test_context_capsule_compiles_state_conversation_jobs_and_diagnostics(self) -> None:
        self.config_manager.set_path("workspace.id", "workspace-test")
        diagnostics_dir = self.home / "diagnostics"
        diagnostics_dir.mkdir(parents=True, exist_ok=True)
        (diagnostics_dir / "spark-diagnostic-2026-04-27T12-55-14+00-00.md").write_text(
            "\n".join(
                [
                    "---",
                    "type: spark-diagnostic-report",
                    "generated_at: 2026-04-27T12-55-14+00-00",
                    "---",
                    "",
                    "## Summary",
                    "",
                    "- scanned lines: `1062`",
                    "- failure lines: `0`",
                    "- finding signatures: `0`",
                    "- recurring signatures: `0`",
                    "",
                    "## Connector Health",
                    "",
                    "- `ok` `spark-telegram-bot` required -> http://127.0.0.1:8789/health - ready",
                    "- `ok` `spawner-ui` required -> http://127.0.0.1:5173/api/providers - ready",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        with self.state_db.connect() as conn:
            conn.execute(
                """
                UPDATE job_records
                SET last_run_at = ?, last_result = ?
                WHERE job_id = 'memory:sdk-maintenance'
                """,
                (
                    "2026-04-27T12:50:00Z",
                    "status=succeeded before=297 after=151 deletions=31",
                ),
            )
            conn.commit()
        record_event(
            self.state_db,
            event_type="intent_committed",
            component="telegram_runtime",
            summary="Inbound Telegram message recorded.",
            channel_id="telegram",
            session_id="session-1",
            request_id="req-old-1",
            facts={"message_text": "What jobs are running?"},
        )
        record_event(
            self.state_db,
            event_type="delivery_succeeded",
            component="telegram_runtime",
            summary="Outbound Telegram reply delivered.",
            channel_id="telegram",
            session_id="session-1",
            request_id="req-old-2",
            reason_code="telegram_bridge_outbound",
            facts={"delivered_text": "The memory maintenance job is scheduled."},
        )

        with patch(
            "spark_intelligence.context.capsule.inspect_human_memory_in_memory",
            return_value=self._memory_inspection(),
        ):
            capsule = build_spark_context_capsule(
                config_manager=self.config_manager,
                state_db=self.state_db,
                human_id="human-1",
                session_id="session-1",
                channel_kind="telegram",
                request_id="req-now",
                user_message="What is active now?",
            )

        rendered = capsule.render()
        self.assertIn("[Spark Context Capsule]", rendered)
        self.assertIn("current_focus: automatic memory maintenance verification", rendered)
        self.assertNotIn("current_focus: diagnostics scan verification", rendered)
        self.assertIn("current_plan: verify scheduled memory cleanup", rendered)
        self.assertIn("assistant: The memory maintenance job is scheduled.", rendered)
        self.assertIn("memory:sdk-maintenance", rendered)
        self.assertIn("spark-diagnostic-2026-04-27T12-55-14+00-00.md", rendered)
        self.assertIn("scanned_lines: 1062", rendered)
        self.assertIn("failure_lines: 0", rendered)
        self.assertIn("finding_signatures: 0", rendered)
        self.assertIn("status: clean_latest_scan_no_failures_or_findings", rendered)
        self.assertIn("connector_health: ok: 2", rendered)
        ledger = capsule.source_ledger()
        self.assertEqual([item["source"] for item in ledger], ["current_state", "diagnostics", "recent_conversation", "workflow_state"])
        self.assertEqual(ledger[0]["role"], "authority")
        self.assertEqual(ledger[1]["role"], "authority")
        self.assertEqual(ledger[3]["role"], "advisory")
        self.assertIn("does not close user goals", ledger[1]["note"])

    def test_context_capsule_contract_covers_telegram_arbitration_regressions(self) -> None:
        prompts = [
            "What is my current focus?",
            "What is my current plan?",
            "What is verified, what is still open, and what should only be closed by me?",
            (
                "Before we close this, verify whether my focus, plan, latest diagnostics, "
                "and maintenance summary survived this turn."
            ),
        ]

        diagnostics_dir = self.home / "diagnostics"
        diagnostics_dir.mkdir(parents=True, exist_ok=True)
        (diagnostics_dir / "spark-diagnostic-2026-04-27T13-38-48+00-00.md").write_text(
            "\n".join(
                [
                    "---",
                    "type: spark-diagnostic-report",
                    "generated_at: 2026-04-27T13:38:48+00:00",
                    "---",
                    "",
                    "- scanned lines: `1074`",
                    "- failure lines: `0`",
                    "- finding signatures: `0`",
                    "- recurring signatures: `0`",
                    "",
                    "## Connector Health",
                    "",
                    "- `ok` `spark-telegram-bot` required -> http://127.0.0.1:8789/health - ready",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        with self.state_db.connect() as conn:
            conn.execute(
                """
                UPDATE job_records
                SET last_run_at = ?, last_result = ?
                WHERE job_id = 'memory:sdk-maintenance'
                """,
                (
                    "2026-04-27T12:46:37Z",
                    "status=succeeded before=297 after=151 deletions=31 still_current=136 stale_preserved=0 superseded=16 archived=75",
                ),
            )
            conn.commit()

        for prompt in prompts:
            with self.subTest(prompt=prompt), patch(
                "spark_intelligence.context.capsule.inspect_human_memory_in_memory",
                return_value=SimpleNamespace(
                    read_result=SimpleNamespace(
                        records=[
                            {
                                "predicate": "profile.current_focus",
                                "value": "context capsule verification",
                                "timestamp": "2026-04-27T13:38:35Z",
                            },
                            {
                                "predicate": "profile.current_plan",
                                "value": "verify scheduled memory cleanup",
                                "timestamp": "2026-04-27T13:02:08Z",
                            },
                        ]
                    )
                ),
            ):
                rendered = build_spark_context_capsule(
                    config_manager=self.config_manager,
                    state_db=self.state_db,
                    human_id="human:telegram:8319079055",
                    session_id="session:telegram:dm:8319079055",
                    channel_kind="telegram",
                    request_id="telegram-regression",
                    user_message=prompt,
                ).render()

            self.assertIn("current_focus: context capsule verification", rendered)
            self.assertIn("current_plan: verify scheduled memory cleanup", rendered)
            self.assertIn("status: clean_latest_scan_no_failures_or_findings", rendered)
            self.assertIn("memory:sdk-maintenance", rendered)
            self.assertIn("do not replace that with an older handoff checklist", rendered)
            self.assertIn("older missions, apps, and workflow residue are out of scope", rendered)

    def test_context_status_query_does_not_collapse_to_single_focus_fact(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        diagnostics_dir = self.home / "diagnostics"
        diagnostics_dir.mkdir(parents=True, exist_ok=True)
        (diagnostics_dir / "spark-diagnostic-2026-04-27T13-38-48+00-00.md").write_text(
            "\n".join(
                [
                    "---",
                    "generated_at: 2026-04-27T13:38:48+00:00",
                    "---",
                    "",
                    "- scanned lines: `1074`",
                    "- failure lines: `0`",
                    "- finding signatures: `0`",
                    "",
                    "## Connector Health",
                    "",
                    "- `ok` `spark-telegram-bot` required -> http://127.0.0.1:8789/health - ready",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        with self.state_db.connect() as conn:
            conn.execute(
                """
                UPDATE job_records
                SET last_run_at = ?, last_result = ?
                WHERE job_id = 'memory:sdk-maintenance'
                """,
                (
                    "2026-04-27T12:46:37Z",
                    "status=succeeded before=297 after=151 deletions=31 archived=75 superseded=16",
                ),
            )
            conn.commit()

        with patch(
            "spark_intelligence.context.capsule.inspect_human_memory_in_memory",
            return_value=SimpleNamespace(
                read_result=SimpleNamespace(
                    records=[
                        {
                            "predicate": "profile.current_focus",
                            "value": "context capsule verification",
                            "timestamp": "2026-04-27T13:38:35Z",
                        },
                        {
                            "predicate": "profile.current_plan",
                            "value": "verify scheduled memory cleanup",
                            "timestamp": "2026-04-27T13:02:08Z",
                        },
                    ]
                )
            ),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("provider should not run for active context status"),
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-active-context-status",
                agent_id="agent:human:telegram:8319079055",
                human_id="human:telegram:8319079055",
                session_id="session:telegram:dm:8319079055",
                channel_kind="telegram",
                user_message="Based on those sources, what is my current focus and what is still open?",
            )

        self.assertEqual(result.routing_decision, "active_context_status")
        self.assertIn("Focus: context capsule verification", result.reply_text)
        self.assertIn("Plan: verify scheduled memory cleanup", result.reply_text)
        self.assertIn("Latest diagnostics are clean", result.reply_text)
        self.assertIn("Memory maintenance succeeded", result.reply_text)
        self.assertNotIn("297 items down to 151", result.reply_text)
        self.assertNotIn("kind=memory_sdk_maintenance", result.reply_text)
        self.assertIn("Focus \"context capsule verification\" remains open", result.reply_text)
        self.assertIn("Plan \"verify scheduled memory cleanup\" remains open", result.reply_text)
        self.assertIn("Clean diagnostics and successful maintenance are evidence", result.reply_text)

    def test_context_status_next_step_query_recommends_validation_before_closure(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        diagnostics_dir = self.home / "diagnostics"
        diagnostics_dir.mkdir(parents=True, exist_ok=True)
        (diagnostics_dir / "spark-diagnostic-2026-04-27T13-38-48+00-00.md").write_text(
            "\n".join(
                [
                    "---",
                    "generated_at: 2026-04-27T13:38:48+00:00",
                    "---",
                    "",
                    "- scanned lines: `1074`",
                    "- failure lines: `0`",
                    "- finding signatures: `0`",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        with self.state_db.connect() as conn:
            conn.execute(
                """
                UPDATE job_records
                SET last_run_at = ?, last_result = ?
                WHERE job_id = 'memory:sdk-maintenance'
                """,
                (
                    "2026-04-27T12:46:37Z",
                    "status=succeeded before=297 after=151 deletions=31 archived=75 superseded=16",
                ),
            )
            conn.commit()

        with patch(
            "spark_intelligence.context.capsule.inspect_human_memory_in_memory",
            return_value=SimpleNamespace(
                read_result=SimpleNamespace(
                    records=[
                        {
                            "predicate": "profile.current_focus",
                            "value": "context capsule verification",
                            "timestamp": "2026-04-27T13:38:35Z",
                        },
                        {
                            "predicate": "profile.current_plan",
                            "value": "verify scheduled memory cleanup",
                            "timestamp": "2026-04-27T13:02:08Z",
                        },
                    ]
                )
            ),
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-active-context-next",
                agent_id="agent:human:telegram:8319079055",
                human_id="human:telegram:8319079055",
                session_id="session:telegram:dm:8319079055",
                channel_kind="telegram",
                user_message="What should we work on next, based on my current focus and what is still open?",
            )

        self.assertEqual(result.routing_decision, "active_context_status")
        self.assertIn("Next", result.reply_text)
        self.assertIn("Spot-check a small sample", result.reply_text)
        self.assertIn("archived, deleted, and still-current memories", result.reply_text)
        self.assertIn("mark \"context capsule verification\" closed", result.reply_text)
        self.assertIn("Memory maintenance succeeded", result.reply_text)
        self.assertNotIn("297 items down to 151", result.reply_text)
        self.assertNotIn("Closure rule", result.reply_text)
        self.assertNotIn("everything is done", result.reply_text.lower())

    def test_open_ended_next_step_query_uses_memory_kernel_when_focus_is_saved(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        kernel_calls: list[dict[str, object]] = []

        def fake_read_memory_kernel(**payload):
            kernel_calls.append(payload)
            method = payload.get("method")
            predicate = payload.get("predicate")
            if method == "get_current_state" and predicate == "profile.current_focus":
                return SimpleNamespace(
                    abstained=False,
                    answer="persistent memory quality evaluation",
                    read_method="get_current_state",
                    source_class="current_state",
                    reason=None,
                    ignored_stale_records=[],
                    read_result=SimpleNamespace(retrieval_trace={"memory_kernel": {"source_class": "current_state"}}),
                )
            if method == "get_current_state" and predicate == "profile.current_plan":
                return SimpleNamespace(
                    abstained=False,
                    answer="evaluate open-ended recall",
                    read_method="get_current_state",
                    source_class="current_state",
                    reason=None,
                    ignored_stale_records=[],
                    read_result=SimpleNamespace(retrieval_trace={"memory_kernel": {"source_class": "current_state"}}),
                )
            return SimpleNamespace(
                abstained=False,
                answer="supporting evidence",
                read_method="retrieve_evidence",
                source_class="structured_evidence",
                reason=None,
                ignored_stale_records=[{"value": "context capsule verification"}],
                read_result=SimpleNamespace(
                    retrieval_trace={
                        "memory_kernel": {
                            "source_class": "structured_evidence",
                            "ignored_stale_record_count": 1,
                        }
                    }
                ),
            )

        fake_promotion_gates = {
            "status": "pass",
            "mode": "trace_only",
            "gates": {
                "source_swamp_resistance": {"status": "pass", "reason": "authority_present_or_small_supporting_packet"},
                "stale_current_conflict": {"status": "pass", "reason": "stale_candidates_discarded"},
                "recent_conversation_noise": {"status": "pass", "reason": "no_recent_conversation_selected"},
                "source_mix_stability": {"status": "pass", "reason": "small_or_balanced_packet"},
            },
        }

        def fake_hybrid_memory_retrieve(**payload):
            return SimpleNamespace(
                context_packet=SimpleNamespace(
                    trace={"promotion_gates": fake_promotion_gates},
                )
            )

        with patch(
            "spark_intelligence.researcher_bridge.advisory.read_memory_kernel",
            side_effect=fake_read_memory_kernel,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.hybrid_memory_retrieve",
            side_effect=fake_hybrid_memory_retrieve,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("provider should not run for memory kernel next-step route"),
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-memory-kernel-next",
                agent_id="agent:human:telegram:8319079055",
                human_id="human:telegram:8319079055",
                session_id="session:telegram:dm:8319079055",
                channel_kind="telegram",
                user_message="What should we focus on next?",
            )

        self.assertEqual(result.routing_decision, "memory_kernel_next_step")
        self.assertIn("Your active focus is persistent memory quality evaluation.", result.reply_text)
        self.assertIn("Run an open-ended recall check", result.reply_text)
        self.assertIn("I ignored 1 stale memory record", result.reply_text)
        self.assertEqual([call.get("method") for call in kernel_calls], ["get_current_state", "get_current_state", "retrieve_evidence"])
        bridge_events = latest_events_by_type(self.state_db, event_type="tool_result_received", limit=1)
        self.assertEqual((bridge_events[0]["facts_json"] or {}).get("routing_decision"), "memory_kernel_next_step")
        self.assertEqual((bridge_events[0]["facts_json"] or {}).get("focus_source_class"), "current_state")
        self.assertEqual((bridge_events[0]["facts_json"] or {}).get("ignored_stale_record_count"), 1)
        self.assertEqual((bridge_events[0]["facts_json"] or {}).get("context_packet_promotion_gates", {}).get("status"), "pass")

    def test_context_status_recent_closure_matches_canonical_human_id(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        record_event(
            self.state_db,
            event_type="tool_result_received",
            component="researcher_bridge",
            summary="Researcher bridge closed an old focus label and set a new current focus.",
            human_id="telegram:8319079055",
            agent_id="agent:human:telegram:8319079055",
            request_id="req-transition-raw-human-id",
            actor_id="researcher_bridge",
            reason_code="current_focus_transition",
            facts={
                "routing_decision": "current_focus_transition",
                "closed_focus": "context capsule verification",
                "new_focus": "persistent memory quality evaluation",
            },
        )

        with patch(
            "spark_intelligence.context.capsule.inspect_human_memory_in_memory",
            return_value=SimpleNamespace(
                read_result=SimpleNamespace(
                    records=[
                        {
                            "predicate": "profile.current_focus",
                            "value": "persistent memory quality evaluation",
                            "timestamp": "2026-04-27T16:56:22Z",
                        },
                    ]
                )
            ),
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-transition-canonical-human-query",
                agent_id="agent:human:telegram:8319079055",
                human_id="human:telegram:8319079055",
                session_id="session:telegram:dm:8319079055",
                channel_kind="telegram",
                user_message="What is my current focus, what did we just close, and what should we evaluate next?",
            )

        self.assertEqual(result.routing_decision, "active_context_status")
        self.assertIn("Focus: persistent memory quality evaluation", result.reply_text)
        self.assertIn("Recently closed", result.reply_text)
        self.assertIn("- context capsule verification", result.reply_text)

    def test_cleanup_sample_query_reports_counts_only_when_audit_samples_missing(self) -> None:
        record_event(
            self.state_db,
            event_type="memory_maintenance_run",
            component="memory_orchestrator",
            summary="Spark memory SDK maintenance run.",
            actor_id="memory_cli",
            facts={
                "status": "succeeded",
                "maintenance": {
                    "active_state_archived_count": 75,
                    "active_deletion_count": 31,
                    "active_state_still_current_count": 136,
                },
            },
        )

        result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-cleanup-sample-missing",
            agent_id="agent:human:telegram:8319079055",
            human_id="human:telegram:8319079055",
            session_id="session:telegram:dm:8319079055",
            channel_kind="telegram",
            user_message="Show me a small sample of archived, deleted, and still-current memories from the cleanup.",
        )

        self.assertEqual(result.routing_decision, "memory_cleanup_sample")
        self.assertIn("I only have cleanup counts right now", result.reply_text)
        self.assertIn("75 archived, 31 deleted, 136 still current", result.reply_text)
        self.assertIn("rerun memory maintenance", result.reply_text)

    def test_cleanup_sample_query_formats_latest_audit_samples(self) -> None:
        record_event(
            self.state_db,
            event_type="memory_maintenance_run",
            component="memory_orchestrator",
            summary="Spark memory SDK maintenance run.",
            actor_id="memory_cli",
            facts={
                "status": "succeeded",
                "maintenance": {
                    "audit_samples": {
                        "archived": [
                            {
                                "subject": "human:smoke:test",
                                "predicate": "system.memory.smoke",
                                "value": "ok",
                                "reason": "deleted_by_later_state_deletion",
                                "observation_id": "memory_cli:write:smoke",
                            },
                            {
                                "subject": "human:telegram:8319079055",
                                "predicate": "current_focus",
                                "value": "old diagnostics plan",
                                "reason": "deleted_by_later_state_deletion",
                                "observation_id": "telegram:1",
                            }
                        ],
                        "deleted": [
                            {
                                "subject": "human:telegram:949385504366",
                                "predicate": "current_owner",
                                "value": "delete current_owner",
                                "reason": "current_snapshot",
                                "observation_id": "sim:1",
                            },
                            {
                                "subject": "human:telegram:8319079055",
                                "predicate": "current_plan",
                                "value": "old plan",
                                "reason": "current_snapshot",
                                "observation_id": "telegram:2",
                            }
                        ],
                        "still_current": [
                            {
                                "subject": "human:telegram:905162608906",
                                "predicate": "telegram.summary.latest_flight",
                                "value": "flight to Paris on May 9",
                                "reason": "current_snapshot",
                                "observation_id": "sim:2",
                            },
                            {
                                "subject": "human:telegram:8319079055",
                                "predicate": "current_focus",
                                "value": "context capsule verification",
                                "reason": "current_snapshot",
                                "observation_id": "telegram:3",
                            }
                        ],
                    }
                },
            },
        )

        result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-cleanup-sample-present",
            agent_id="agent:human:telegram:8319079055",
            human_id="human:telegram:8319079055",
            session_id="session:telegram:dm:8319079055",
            channel_kind="telegram",
            user_message="Show me a small sample of archived, deleted, and still-current memories from the cleanup.",
        )

        self.assertEqual(result.routing_decision, "memory_cleanup_sample")
        self.assertIn("Here is a small memory cleanup audit sample", result.reply_text)
        self.assertIn("Archived", result.reply_text)
        self.assertIn("current_focus: old diagnostics plan", result.reply_text)
        self.assertLess(
            result.reply_text.index("current_focus: old diagnostics plan"),
            result.reply_text.index("system.memory.smoke: ok"),
        )
        self.assertIn("Deleted", result.reply_text)
        self.assertIn("current_plan: old plan", result.reply_text)
        self.assertLess(
            result.reply_text.index("current_plan: old plan"),
            result.reply_text.index("current_owner: delete current_owner"),
        )
        self.assertIn("Still current", result.reply_text)
        self.assertIn("current_focus: context capsule verification", result.reply_text)
        self.assertLess(
            result.reply_text.index("current_focus: context capsule verification"),
            result.reply_text.index("telegram.summary.latest_flight: flight to Paris on May 9"),
        )

    def test_cleanup_closure_evidence_does_not_overclaim_sample_review(self) -> None:
        self.config_manager.set_path("spark.memory.enabled", True)
        diagnostics_dir = self.home / "diagnostics"
        diagnostics_dir.mkdir(parents=True, exist_ok=True)
        (diagnostics_dir / "spark-diagnostic-2026-04-27T13-38-48+00-00.md").write_text(
            "\n".join(
                [
                    "---",
                    "generated_at: 2026-04-27T13:38:48+00:00",
                    "---",
                    "",
                    "- scanned lines: `1074`",
                    "- failure lines: `0`",
                    "- finding signatures: `0`",
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        record_event(
            self.state_db,
            event_type="memory_maintenance_run",
            component="memory_orchestrator",
            summary="Spark memory SDK maintenance run.",
            actor_id="memory_cli",
            facts={
                "status": "succeeded",
                "maintenance": {
                    "manual_observations_before": 305,
                    "manual_observations_after": 150,
                    "active_state_archived_count": 75,
                    "active_deletion_count": 30,
                    "active_state_superseded_count": 21,
                    "active_state_still_current_count": 135,
                    "active_state_stale_preserved_count": 0,
                    "audit_samples": {
                        "archived": [
                            {
                                "subject": "human:telegram:8319079055",
                                "predicate": "profile.current_focus",
                                "value": "diagnostics scan verification",
                                "reason": "deleted_by_later_state_deletion",
                            }
                        ],
                        "deleted": [
                            {
                                "subject": "human:telegram:8319079055",
                                "predicate": "profile.current_owner",
                                "value": "delete profile.current_owner",
                                "reason": "current_snapshot",
                            }
                        ],
                        "still_current": [
                            {
                                "subject": "human:telegram:8319079055",
                                "predicate": "profile.current_focus",
                                "value": "context capsule verification",
                                "reason": "current_snapshot",
                            },
                            {
                                "subject": "human:telegram:8319079055",
                                "predicate": "profile.current_plan",
                                "value": "verify scheduled memory cleanup",
                                "reason": "current_snapshot",
                            },
                        ],
                    },
                },
            },
        )

        with patch(
            "spark_intelligence.context.capsule.inspect_human_memory_in_memory",
            return_value=SimpleNamespace(
                read_result=SimpleNamespace(
                    records=[
                        {
                            "predicate": "profile.current_focus",
                            "value": "context capsule verification",
                            "timestamp": "2026-04-27T13:38:35Z",
                        },
                        {
                            "predicate": "profile.current_plan",
                            "value": "verify scheduled memory cleanup",
                            "timestamp": "2026-04-27T13:02:08Z",
                        },
                    ]
                )
            ),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("provider should not run for cleanup closure evidence"),
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-cleanup-closure-evidence",
                agent_id="agent:human:telegram:8319079055",
                human_id="human:telegram:8319079055",
                session_id="session:telegram:dm:8319079055",
                channel_kind="telegram",
                user_message="What exact evidence are you using to say it is ready to close?",
            )

        self.assertEqual(result.routing_decision, "memory_cleanup_closure_evidence")
        self.assertIn("not enough to prove every memory was reviewed", result.reply_text)
        self.assertIn("Memory maintenance succeeded: 305 -> 150", result.reply_text)
        self.assertIn("Audit sample reviewed", result.reply_text)
        self.assertIn("\nArchived examples\n- profile.current_focus: diagnostics scan verification", result.reply_text)
        self.assertIn("\nDeleted examples\n- profile.current_owner: delete profile.current_owner", result.reply_text)
        self.assertIn("\nStill-current examples\n- profile.current_focus: context capsule verification", result.reply_text)
        self.assertIn("\nSample verdict\n- The sample did not show obvious damage.", result.reply_text)
        self.assertIn("I reviewed a small audit sample", result.reply_text)
        self.assertIn("not every archived or deleted memory", result.reply_text)
        self.assertIn('Focus "context capsule verification"', result.reply_text)
        self.assertIn('Plan "verify scheduled memory cleanup"', result.reply_text)
        self.assertNotIn("Every archived", result.reply_text)
        self.assertNotIn("every archived entry", result.reply_text)
        self.assertNotIn("Every deleted", result.reply_text)
        self.assertNotIn("every deleted entry", result.reply_text)

    def test_memory_quality_evaluation_plan_does_not_fall_back_to_diagnostics_handoff(self) -> None:
        with patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("provider should not run for memory quality plan"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._build_browser_search_context",
            side_effect=AssertionError("browser search should not run for memory source explanation"),
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-memory-quality-plan",
                agent_id="agent:human:telegram:8319079055",
                human_id="human:telegram:8319079055",
                session_id="session:telegram:dm:8319079055",
                channel_kind="telegram",
                user_message=(
                    "Good. Give me a concrete evaluation plan for persistent memory quality in Telegram. "
                    "It should test natural recall, stale context avoidance, current-state priority, "
                    "and whether you can explain what memory sources you used."
                ),
            )

        self.assertEqual(result.routing_decision, "memory_quality_evaluation_plan")
        self.assertIn("Natural recall", result.reply_text)
        self.assertIn("Stale context avoidance", result.reply_text)
        self.assertIn("Current-state priority", result.reply_text)
        self.assertIn("Source explanation", result.reply_text)
        self.assertIn("Open-ended synthesis", result.reply_text)
        self.assertNotIn("fresh diagnostics scan", result.reply_text)
        self.assertNotIn("diagnostic integration upgrades", result.reply_text)

    def test_cleanup_samples_ready_to_close_uses_closure_evidence_route(self) -> None:
        record_event(
            self.state_db,
            event_type="memory_maintenance_run",
            component="memory_orchestrator",
            summary="Spark memory SDK maintenance run.",
            actor_id="memory_cli",
            facts={
                "status": "succeeded",
                "maintenance": {
                    "active_state_archived_count": 75,
                    "active_deletion_count": 30,
                    "active_state_still_current_count": 135,
                    "audit_samples": {
                        "still_current": [
                            {
                                "subject": "human:telegram:8319079055",
                                "predicate": "profile.current_focus",
                                "value": "context capsule verification",
                            }
                        ]
                    },
                },
            },
        )

        result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-cleanup-ready-close",
            agent_id="agent:human:telegram:8319079055",
            human_id="human:telegram:8319079055",
            session_id="session:telegram:dm:8319079055",
            channel_kind="telegram",
            user_message="Based on the cleanup samples, is my current focus ready to close? What should only I decide?",
        )

        self.assertEqual(result.routing_decision, "memory_cleanup_closure_evidence")
        self.assertIn("Only you should close", result.reply_text)
        self.assertIn("not every archived or deleted memory", result.reply_text)
        self.assertNotEqual(result.routing_decision, "active_context_status")

    def test_researcher_reply_injects_context_capsule_into_provider_prompt(self) -> None:
        self.config_manager.set_path("spark.researcher.enabled", True)
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        runtime_root = self.home / "fake-researcher"
        runtime_root.mkdir(parents=True, exist_ok=True)
        config_path = runtime_root / "spark-researcher.project.json"
        config_path.write_text("{}", encoding="utf-8")
        provider = RuntimeProviderResolution(
            provider_id="custom",
            provider_kind="openai_compatible",
            auth_profile_id="default",
            auth_method="api_key",
            api_mode="openai_chat_completions",
            execution_transport="direct_http",
            base_url="https://api.example.invalid/v1",
            default_model="test-model",
            secret_ref=None,
            secret_value="test-secret",
            source="test",
        )

        def fake_execute_direct_provider_prompt(*, user_prompt, **kwargs):
            self.assertIn("[Spark Context Capsule]", user_prompt)
            self.assertIn("current_focus: automatic memory maintenance verification", user_prompt)
            self.assertIn("[Context source contract]", user_prompt)
            self.assertIn("Do not invent unavailable slash commands", user_prompt)
            self.assertIn(
                "Do not infer that an active focus, plan, or blocker is resolved",
                user_prompt,
            )
            self.assertIn(
                "the system evidence is green but the focus/plan remains open until the user closes it",
                user_prompt,
            )
            self.assertIn(
                "verify by naming the current focus, current plan, latest diagnostics status, and maintenance summary",
                user_prompt,
            )
            self.assertIn(
                "what is verified, still open, or only they should close",
                user_prompt,
            )
            return {"raw_response": "Your active focus is automatic memory maintenance verification."}

        with patch(
            "spark_intelligence.researcher_bridge.advisory.discover_researcher_runtime_root",
            return_value=(runtime_root, "configured"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.resolve_researcher_config_path",
            return_value=config_path,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
            return_value=ResearcherProviderSelection(provider=provider, model_family="generic"),
        ), patch(
            "spark_intelligence.context.capsule.inspect_human_memory_in_memory",
            return_value=self._memory_inspection(),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=fake_execute_direct_provider_prompt,
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-context-capsule",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-context-capsule",
                channel_kind="telegram",
                user_message="Give me a quick orientation on the active work.",
            )

        self.assertEqual(result.routing_decision, "provider_fallback_chat")
        self.assertIn("automatic memory maintenance", result.reply_text)
        events = latest_events_by_type(self.state_db, event_type="context_capsule_compiled", limit=5)
        self.assertTrue(events)
        event_facts = [event["facts_json"] or {} for event in events]
        self.assertTrue(all(facts.get("keepability") == "ephemeral_context" for facts in event_facts))
        self.assertIn("researcher_bridge_provider", {facts.get("context_route") for facts in event_facts})
        self.assertIn("direct_provider_fallback", {facts.get("context_route") for facts in event_facts})
        self.assertTrue(
            any(((facts.get("source_counts") or {}).get("current_state", 0) > 0) for facts in event_facts)
        )
        self.assertTrue(any(facts.get("source_ledger") for facts in event_facts))
        provider_ledger = next(facts["source_ledger"] for facts in event_facts if facts.get("source_ledger"))
        self.assertEqual(provider_ledger[0]["source"], "current_state")
        self.assertEqual(provider_ledger[0]["role"], "authority")
        self.assertEqual(provider_ledger[-1]["source"], "workflow_state")
        self.assertEqual(provider_ledger[-1]["role"], "advisory")
