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
        self.assertIn("Focus \"context capsule verification\" remains open", result.reply_text)
        self.assertIn("Plan \"verify scheduled memory cleanup\" remains open", result.reply_text)
        self.assertIn("Clean diagnostics and successful maintenance are evidence", result.reply_text)

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
