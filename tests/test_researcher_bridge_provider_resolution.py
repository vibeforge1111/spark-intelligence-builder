from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

from spark_intelligence.auth.runtime import RuntimeProviderResolution
from spark_intelligence.memory import write_profile_fact_to_memory
from spark_intelligence.observability.store import latest_events_by_type
from spark_intelligence.researcher_bridge.advisory import (
    _build_contextual_task,
    _clean_messaging_reply,
    _render_direct_provider_chat_fallback,
    build_researcher_reply,
)

from tests.test_support import SparkTestCase


class ResearcherBridgeProviderResolutionTests(SparkTestCase):
    def test_clean_messaging_reply_rewrites_structured_chip_memo_for_telegram(self) -> None:
        cleaned = _clean_messaging_reply(
            (
                "# Revised: Decision Clarity Infrastructure\n\n"
                "**Recommendation**: Prioritize interpretation scaffolding for no actionable diff outputs.\n\n"
                "## Primary Focus\n"
                "Build operator-facing guidance for no actionable diff outputs.\n\n"
                "## Why This Works\n"
                "Undocumented silent outcomes create operator doubt.\n\n"
                "- Confidence: 0.45\n"
                "- Evidence gap: No verification operators lack this layer.\n\n"
                "trace_ref: trace:internal-cleanup\n"
                "packet_refs: packet-1\n\n"
                "## Next Step\n"
                "Survey 2-3 operator workflows."
            ),
            channel_kind="telegram",
        )

        self.assertEqual(
            cleaned,
            (
                'Prioritize interpretation scaffolding for no actionable diff outputs.\n\n'
                'Build operator-facing guidance for no actionable diff outputs. '
                'Undocumented silent outcomes create operator doubt.\n\n'
                'Next: Survey 2-3 operator workflows.'
            ),
        )

    def test_clean_messaging_reply_strips_internal_research_note_prefixes(self) -> None:
        cleaned = _clean_messaging_reply(
            "Based on the research notes provided, the strongest next move is to tighten operator docs.",
            channel_kind="telegram",
        )

        self.assertEqual(cleaned, "the strongest next move is to tighten operator docs.")

    def test_clean_messaging_reply_strips_think_blocks_before_delivery(self) -> None:
        cleaned = _clean_messaging_reply(
            "<think>private reasoning</think>\n\nUse the startup signal, not vanity metrics.",
            channel_kind="telegram",
        )

        self.assertEqual(cleaned, "Use the startup signal, not vanity metrics.")

    def test_clean_messaging_reply_strips_inline_markdown_emphasis_for_telegram(self) -> None:
        cleaned = _clean_messaging_reply(
            "**The 4 churned agencies** matter more than the headline MRR right now.",
            channel_kind="telegram",
        )

        self.assertEqual(cleaned, "The 4 churned agencies matter more than the headline MRR right now.")

    def test_build_contextual_task_summarizes_chip_guidance_without_meta_scaffolding(self) -> None:
        prompt = _build_contextual_task(
            user_message="what next",
            attachment_context={
                "active_chip_keys": ["startup-yc"],
                "pinned_chip_keys": [],
                "active_path_key": "startup-operator",
            },
            active_chip_evaluate={
                "chip_key": "startup-yc",
                "task_type": "diagnostic_questioning",
                "stage": "idea",
                "analysis": (
                    "# Revised: Decision Clarity Infrastructure\n\n"
                    "**Recommendation**: Prioritize interpretation scaffolding.\n\n"
                    "## Primary Focus\n"
                    "Build operator-facing guidance for no actionable diff outputs.\n\n"
                    "- Confidence: 0.45\n"
                    "- Evidence gap: No verification operators lack this layer.\n\n"
                    "## Next Step\n"
                    "Survey a few operator workflows."
                ),
            },
        )

        self.assertIn("[Active chip guidance]", prompt)
        self.assertIn("Use this guidance as background context only.", prompt)
        self.assertIn("Prioritize interpretation scaffolding.", prompt)
        self.assertNotIn("Confidence:", prompt)
        self.assertNotIn("Evidence gap:", prompt)
        self.assertNotIn("## Primary Focus", prompt)

    def test_build_contextual_task_prefers_agent_persona_over_global_personality_name(self) -> None:
        prompt = _build_contextual_task(
            user_message="what next",
            attachment_context={
                "active_chip_keys": ["startup-yc"],
                "pinned_chip_keys": [],
                "active_path_key": "startup-operator",
            },
            personality_profile={
                "personality_name": "Alice",
                "agent_persona_name": "Operator",
                "traits": {
                    "warmth": 0.7,
                    "directness": 1.0,
                    "playfulness": 0.5,
                    "pacing": 0.55,
                    "assertiveness": 0.3,
                },
                "style_labels": {
                    "warmth": "warm",
                    "directness": "very direct",
                    "playfulness": "balanced playfulness",
                    "pacing": "brisk",
                    "assertiveness": "balanced assertiveness",
                },
                "agent_persona_applied": True,
                "user_deltas_applied": False,
            },
        )

        self.assertIn("agent_persona=Operator", prompt)
        self.assertNotIn("active_personality=Alice", prompt)

    def test_build_researcher_reply_cleans_memo_style_execution_reply_for_telegram(self) -> None:
        self.config_manager.set_path("spark.researcher.enabled", True)
        connect_exit, _, connect_stderr = self.run_cli(
            "auth",
            "connect",
            "custom",
            "--home",
            str(self.home),
            "--api-key",
            "minimax-secret",
            "--model",
            "MiniMax-M2.7",
            "--base-url",
            "https://api.minimax.io/v1",
        )
        self.assertEqual(connect_exit, 0, connect_stderr)

        runtime_root = self.home / "fake-researcher"
        runtime_root.mkdir(parents=True, exist_ok=True)
        config_path = runtime_root / "spark-researcher.project.json"
        config_path.write_text("{}", encoding="utf-8")

        def fake_build_advisory(path: Path, task: str, *, model: str = "generic", limit: int = 4, domain: str | None = None):
            return {
                "guidance": ["Use evidence-backed guidance."],
                "epistemic_status": {"status": "grounded", "packet_stability": {"status": "durable_supported"}},
                "selected_packet_ids": ["packet-1"],
                "trace_path": "trace:test",
            }

        def fake_execute_with_research(*args, **kwargs):
            return {
                "status": "ok",
                "decision": "approve",
                "response": {
                    "raw_response": (
                        "# Revised: Decision Clarity Infrastructure\n\n"
                        "## Primary Focus\n"
                        "Build operator-facing guidance for no actionable diff outputs.\n\n"
                        "- Confidence: 0.45\n"
                        "- Evidence gap: No verification operators lack this layer.\n\n"
                        "trace_ref: trace:execution-internal\n"
                        "memory_refs: memory-1\n\n"
                        "## Next Step\n"
                        "Survey operator workflows."
                    )
                },
                "trace_path": "trace:execution",
            }

        with patch(
            "spark_intelligence.researcher_bridge.advisory.discover_researcher_runtime_root",
            return_value=(runtime_root, "configured"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.resolve_researcher_config_path",
            return_value=config_path,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._import_build_advisory",
            return_value=fake_build_advisory,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._import_execute_with_research",
            return_value=fake_execute_with_research,
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-clean-telegram",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-1",
                channel_kind="telegram",
                user_message="What should we focus on next?",
            )

        self.assertEqual(result.routing_decision, "provider_execution")
        self.assertEqual(
            result.reply_text,
            (
                "Build operator-facing guidance for no actionable diff outputs.\n\n"
                "Next: Survey operator workflows."
            ),
        )
        self.assertEqual(result.output_keepability, "ephemeral_context")
        self.assertEqual(result.promotion_disposition, "not_promotable")
        self.assertNotIn("Confidence:", result.reply_text)
        self.assertNotIn("Evidence gap:", result.reply_text)
        self.assertNotIn("Primary Focus", result.reply_text)
        self.assertNotIn("trace:", result.reply_text)
        self.assertNotIn("memory_refs", result.reply_text)
        events = latest_events_by_type(self.state_db, event_type="quarantine_recorded", limit=10)
        self.assertTrue(
            any(
                (event.get("facts_json") or {}).get("source_kind") == "reply_residue"
                for event in events
            )
        )

    def test_build_researcher_reply_uses_selected_draft_when_verifier_returns_no_response(self) -> None:
        self.config_manager.set_path("spark.researcher.enabled", True)
        connect_exit, _, connect_stderr = self.run_cli(
            "auth",
            "connect",
            "custom",
            "--home",
            str(self.home),
            "--api-key",
            "minimax-secret",
            "--model",
            "MiniMax-M2.7",
            "--base-url",
            "https://api.minimax.io/v1",
        )
        self.assertEqual(connect_exit, 0, connect_stderr)

        runtime_root = self.home / "fake-researcher"
        runtime_root.mkdir(parents=True, exist_ok=True)
        config_path = runtime_root / "spark-researcher.project.json"
        config_path.write_text("{}", encoding="utf-8")

        def fake_build_advisory(path: Path, task: str, *, model: str = "generic", limit: int = 4, domain: str | None = None):
            return {
                "guidance": [
                    "A lower learning rate should improve val_loss.",
                    "Operators need a documented interpretation for successful backend runs that yield no actionable diff.",
                ],
                "epistemic_status": {"status": "partial", "packet_stability": {"status": "provisional_only"}},
                "selected_packet_ids": ["packet-1"],
                "trace_path": "trace:test",
            }

        def fake_execute_with_research(*args, **kwargs):
            return {
                "status": "needs_verification",
                "decision": "needs_verification",
                "response": None,
                "draft": {
                    "response": {
                        "raw_response": (
                            "<think>private reasoning</think>\n\n"
                            "8% weekly churn is an emergency. Pick one ICP, talk to three churned users this week, "
                            "and pause net-new features until you know why they leave."
                        )
                    }
                },
                "drafts": {"selected": "a"},
                "critique": {
                    "decision": "needs_verification",
                    "selected": "a",
                    "issues": ["Verifier did not return parseable JSON."],
                    "best_next_question": "",
                },
                "trace_path": "trace:execution",
            }

        with patch(
            "spark_intelligence.researcher_bridge.advisory.discover_researcher_runtime_root",
            return_value=(runtime_root, "configured"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.resolve_researcher_config_path",
            return_value=config_path,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._import_build_advisory",
            return_value=fake_build_advisory,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._import_execute_with_research",
            return_value=fake_execute_with_research,
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-draft-fallback",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-1",
                channel_kind="telegram",
                user_message="We have 8 percent weekly churn and two ICPs. What should we prioritize this week?",
            )

        self.assertIn("8% weekly churn is an emergency.", result.reply_text)
        self.assertNotIn("learning rate", result.reply_text.lower())
        self.assertNotIn("<think>", result.reply_text)

    def test_build_researcher_reply_uses_direct_provider_chat_fallback_for_under_supported_conversation(self) -> None:
        self.config_manager.set_path("spark.researcher.enabled", True)
        connect_exit, _, connect_stderr = self.run_cli(
            "auth",
            "connect",
            "custom",
            "--home",
            str(self.home),
            "--api-key",
            "minimax-secret",
            "--model",
            "MiniMax-M2.7",
            "--base-url",
            "https://api.minimax.io/v1",
        )
        self.assertEqual(connect_exit, 0, connect_stderr)

        runtime_root = self.home / "fake-researcher"
        runtime_root.mkdir(parents=True, exist_ok=True)
        config_path = runtime_root / "spark-researcher.project.json"
        config_path.write_text("{}", encoding="utf-8")
        captured: dict[str, object] = {}

        def fake_build_advisory(path: Path, task: str, *, model: str = "generic", limit: int = 4, domain: str | None = None):
            captured["advisory_model"] = model
            return {
                "guidance": [],
                "epistemic_status": {
                    "status": "under_supported",
                    "packet_stability": {"status": "no_belief_packets"},
                },
                "selected_packet_ids": [],
                "trace_path": "trace:under-supported",
            }

        def fake_direct_provider_prompt(*, provider, system_prompt: str, user_prompt: str, governance=None):
            captured["provider_id"] = provider.provider_id
            captured["provider_model"] = provider.model
            captured["system_prompt"] = system_prompt
            captured["user_prompt"] = user_prompt
            captured["governance"] = governance
            return {"raw_response": "Hey there. How can I help?"}

        def fail_execute_with_research(*args, **kwargs):
            raise AssertionError("execute_with_research should not run for direct conversational fallback")

        with patch(
            "spark_intelligence.researcher_bridge.advisory.discover_researcher_runtime_root",
            return_value=(runtime_root, "configured"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.resolve_researcher_config_path",
            return_value=config_path,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._import_build_advisory",
            return_value=fake_build_advisory,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._import_execute_with_research",
            return_value=fail_execute_with_research,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=fake_direct_provider_prompt,
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-fallback",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-1",
                channel_kind="telegram",
                user_message="hey",
            )

        self.assertEqual(captured["advisory_model"], "generic")
        self.assertEqual(captured["provider_id"], "custom")
        self.assertEqual(captured["provider_model"], "MiniMax-M2.7")
        self.assertIn("1:1 messaging conversation", str(captured["system_prompt"]))
        self.assertNotIn("decisive startup operator", str(captured["system_prompt"]))
        self.assertIn("[fallback_mode=conversational_under_supported]", str(captured["user_prompt"]))
        self.assertIsNotNone(captured["governance"])
        self.assertEqual(result.output_keepability, "ephemeral_context")
        self.assertEqual(result.promotion_disposition, "not_promotable")
        self.assertEqual(result.reply_text, "Hey there. How can I help?")
        self.assertEqual(result.trace_ref, "trace:under-supported")
        self.assertEqual(result.provider_id, "custom")
        self.assertEqual(result.provider_execution_transport, "direct_http")
        self.assertEqual(result.evidence_summary, "status=under_supported provider_fallback=direct_http_chat")

    def test_render_direct_provider_chat_fallback_adds_startup_operator_contract_for_startup_chip(self) -> None:
        provider = RuntimeProviderResolution(
            provider_id="custom",
            provider_kind="custom",
            auth_profile_id="custom:default",
            auth_method="api_key_env",
            api_mode="chat_completions",
            execution_transport="direct_http",
            base_url="https://api.minimax.io/v1",
            default_model="MiniMax-M2.7",
            secret_ref=None,
            secret_value="secret",
            source="config+env",
        )
        captured: dict[str, object] = {}

        def fake_direct_provider_prompt(*, provider, system_prompt: str, user_prompt: str, governance=None):
            captured["system_prompt"] = system_prompt
            captured["user_prompt"] = user_prompt
            return {"raw_response": "Focus on in-house teams for now."}

        with patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=fake_direct_provider_prompt,
        ):
            reply = _render_direct_provider_chat_fallback(
                state_db=self.state_db,
                provider=provider,
                user_message="Should we drop agencies entirely?",
                channel_kind="telegram",
                attachment_context={
                    "active_chip_keys": ["startup-yc"],
                    "pinned_chip_keys": ["startup-yc"],
                    "active_path_key": "startup-operator",
                },
                active_chip_evaluate={
                    "chip_key": "startup-yc",
                    "task_type": "boundary_detection",
                    "stage": "post_launch",
                    "analysis": "Agencies churn fast. In-house teams activate slower and retain longer.",
                },
            )

        self.assertEqual(reply, "Focus on in-house teams for now.")
        self.assertIn("decisive startup operator", str(captured["system_prompt"]))
        self.assertIn("Do not invent numbers", str(captured["system_prompt"]))
        self.assertIn("Avoid numeric ranges like 3-5 or 2-3", str(captured["system_prompt"]))
        self.assertIn("no bold emphasis", str(captured["system_prompt"]))
        self.assertIn("Should we drop agencies entirely?", str(captured["user_prompt"]))

    def test_build_researcher_reply_persists_city_profile_fact_before_bridge_execution(self) -> None:
        self.config_manager.set_path("spark.researcher.enabled", True)
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)
        connect_exit, _, connect_stderr = self.run_cli(
            "auth",
            "connect",
            "custom",
            "--home",
            str(self.home),
            "--api-key",
            "minimax-secret",
            "--model",
            "MiniMax-M2.7",
            "--base-url",
            "https://api.minimax.io/v1",
        )
        self.assertEqual(connect_exit, 0, connect_stderr)

        runtime_root = self.home / "fake-researcher"
        runtime_root.mkdir(parents=True, exist_ok=True)
        config_path = runtime_root / "spark-researcher.project.json"
        config_path.write_text("{}", encoding="utf-8")

        def fake_build_advisory(path: Path, task: str, *, model: str = "generic", limit: int = 4, domain: str | None = None):
            return {
                "guidance": [],
                "epistemic_status": {
                    "status": "under_supported",
                    "packet_stability": {"status": "no_belief_packets"},
                },
                "selected_packet_ids": [],
                "trace_path": "trace:city-under-supported",
            }

        def fake_direct_provider_prompt(*, provider, system_prompt: str, user_prompt: str, governance=None):
            return {"raw_response": "Noted."}

        def fail_execute_with_research(*args, **kwargs):
            raise AssertionError("execute_with_research should not run for direct conversational fallback")

        with patch(
            "spark_intelligence.researcher_bridge.advisory.discover_researcher_runtime_root",
            return_value=(runtime_root, "configured"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.resolve_researcher_config_path",
            return_value=config_path,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._import_build_advisory",
            return_value=fake_build_advisory,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._import_execute_with_research",
            return_value=fail_execute_with_research,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=fake_direct_provider_prompt,
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-city-fact",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-city",
                channel_kind="telegram",
                user_message="I moved to Dubai.",
            )

        self.assertEqual(result.reply_text, "Noted.")
        write_events = latest_events_by_type(self.state_db, event_type="memory_write_requested", limit=10)
        self.assertTrue(write_events)
        observations = (write_events[0]["facts_json"] or {}).get("observations") or []
        self.assertEqual(observations[0]["predicate"], "profile.city")
        self.assertEqual(observations[0]["value"], "Dubai")
        self.assertEqual(observations[0]["text"], "I moved to Dubai.")
        influence_events = latest_events_by_type(self.state_db, event_type="plugin_or_chip_influence_recorded", limit=10)
        self.assertTrue(influence_events)
        detected = (influence_events[0]["facts_json"] or {}).get("detected_profile_fact") or {}
        self.assertEqual(detected.get("predicate"), "profile.city")
        self.assertEqual(detected.get("value"), "Dubai")

    def test_build_researcher_reply_injects_memory_backed_city_fact_for_city_query(self) -> None:
        self.config_manager.set_path("spark.researcher.enabled", True)
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)
        connect_exit, _, connect_stderr = self.run_cli(
            "auth",
            "connect",
            "custom",
            "--home",
            str(self.home),
            "--api-key",
            "minimax-secret",
            "--model",
            "MiniMax-M2.7",
            "--base-url",
            "https://api.minimax.io/v1",
        )
        self.assertEqual(connect_exit, 0, connect_stderr)

        write_profile_fact_to_memory(
            config_manager=self.config_manager,
            state_db=self.state_db,
            human_id="human-1",
            predicate="profile.city",
            value="Dubai",
            evidence_text="I moved to Dubai.",
            fact_name="profile_city",
            session_id="session-city-query",
            turn_id="turn-city-query-write",
            channel_kind="telegram",
        )

        runtime_root = self.home / "fake-researcher"
        runtime_root.mkdir(parents=True, exist_ok=True)
        config_path = runtime_root / "spark-researcher.project.json"
        config_path.write_text("{}", encoding="utf-8")
        captured: dict[str, object] = {}

        def fake_build_advisory(path: Path, task: str, *, model: str = "generic", limit: int = 4, domain: str | None = None):
            return {
                "guidance": [],
                "epistemic_status": {
                    "status": "under_supported",
                    "packet_stability": {"status": "no_belief_packets"},
                },
                "selected_packet_ids": [],
                "trace_path": "trace:city-query",
            }

        def fake_direct_provider_prompt(*, provider, system_prompt: str, user_prompt: str, governance=None):
            captured["user_prompt"] = user_prompt
            return {"raw_response": "You're in Dubai."}

        def fail_execute_with_research(*args, **kwargs):
            raise AssertionError("execute_with_research should not run for direct conversational fallback")

        with patch(
            "spark_intelligence.researcher_bridge.advisory.discover_researcher_runtime_root",
            return_value=(runtime_root, "configured"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.resolve_researcher_config_path",
            return_value=config_path,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._import_build_advisory",
            return_value=fake_build_advisory,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._import_execute_with_research",
            return_value=fail_execute_with_research,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=fake_direct_provider_prompt,
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-city-query",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-city-query",
                channel_kind="telegram",
                user_message="What city do you have for me?",
            )

        self.assertEqual(result.reply_text, "You're in Dubai.")
        self.assertIn("[Memory action: PROFILE_FACT_STATUS]", str(captured["user_prompt"]))
        self.assertIn("city: Dubai", str(captured["user_prompt"]))
        read_events = latest_events_by_type(self.state_db, event_type="memory_read_requested", limit=10)
        self.assertTrue(read_events)
        self.assertEqual((read_events[0]["facts_json"] or {}).get("predicate"), "profile.city")

    def test_build_researcher_reply_preserves_uncertainty_for_missing_city_query_fact(self) -> None:
        self.config_manager.set_path("spark.researcher.enabled", True)
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)
        connect_exit, _, connect_stderr = self.run_cli(
            "auth",
            "connect",
            "custom",
            "--home",
            str(self.home),
            "--api-key",
            "minimax-secret",
            "--model",
            "MiniMax-M2.7",
            "--base-url",
            "https://api.minimax.io/v1",
        )
        self.assertEqual(connect_exit, 0, connect_stderr)

        runtime_root = self.home / "fake-researcher"
        runtime_root.mkdir(parents=True, exist_ok=True)
        config_path = runtime_root / "spark-researcher.project.json"
        config_path.write_text("{}", encoding="utf-8")
        captured: dict[str, object] = {}

        def fake_build_advisory(path: Path, task: str, *, model: str = "generic", limit: int = 4, domain: str | None = None):
            return {
                "guidance": [],
                "epistemic_status": {
                    "status": "under_supported",
                    "packet_stability": {"status": "no_belief_packets"},
                },
                "selected_packet_ids": [],
                "trace_path": "trace:city-query-missing",
            }

        def fake_direct_provider_prompt(*, provider, system_prompt: str, user_prompt: str, governance=None):
            captured["user_prompt"] = user_prompt
            return {"raw_response": "I don't currently have a saved city for you."}

        def fail_execute_with_research(*args, **kwargs):
            raise AssertionError("execute_with_research should not run for direct conversational fallback")

        with patch(
            "spark_intelligence.researcher_bridge.advisory.discover_researcher_runtime_root",
            return_value=(runtime_root, "configured"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.resolve_researcher_config_path",
            return_value=config_path,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._import_build_advisory",
            return_value=fake_build_advisory,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._import_execute_with_research",
            return_value=fail_execute_with_research,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=fake_direct_provider_prompt,
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-city-query-missing",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-city-query-missing",
                channel_kind="telegram",
                user_message="What city do you have saved for me?",
            )

        self.assertEqual(result.reply_text, "I don't currently have a saved city for you.")
        self.assertIn("[Memory action: PROFILE_FACT_STATUS_MISSING]", str(captured["user_prompt"]))
        self.assertIn("Do not pretend you know.", str(captured["user_prompt"]))

    def test_build_researcher_reply_persists_timezone_profile_fact_before_bridge_execution(self) -> None:
        self.config_manager.set_path("spark.researcher.enabled", True)
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)
        connect_exit, _, connect_stderr = self.run_cli(
            "auth",
            "connect",
            "custom",
            "--home",
            str(self.home),
            "--api-key",
            "minimax-secret",
            "--model",
            "MiniMax-M2.7",
            "--base-url",
            "https://api.minimax.io/v1",
        )
        self.assertEqual(connect_exit, 0, connect_stderr)

        runtime_root = self.home / "fake-researcher"
        runtime_root.mkdir(parents=True, exist_ok=True)
        config_path = runtime_root / "spark-researcher.project.json"
        config_path.write_text("{}", encoding="utf-8")

        def fake_build_advisory(path: Path, task: str, *, model: str = "generic", limit: int = 4, domain: str | None = None):
            return {
                "guidance": [],
                "epistemic_status": {
                    "status": "under_supported",
                    "packet_stability": {"status": "no_belief_packets"},
                },
                "selected_packet_ids": [],
                "trace_path": "trace:timezone-under-supported",
            }

        def fake_direct_provider_prompt(*, provider, system_prompt: str, user_prompt: str, governance=None):
            return {"raw_response": "Noted."}

        def fail_execute_with_research(*args, **kwargs):
            raise AssertionError("execute_with_research should not run for direct conversational fallback")

        with patch(
            "spark_intelligence.researcher_bridge.advisory.discover_researcher_runtime_root",
            return_value=(runtime_root, "configured"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.resolve_researcher_config_path",
            return_value=config_path,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._import_build_advisory",
            return_value=fake_build_advisory,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._import_execute_with_research",
            return_value=fail_execute_with_research,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=fake_direct_provider_prompt,
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-timezone-fact",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-timezone",
                channel_kind="telegram",
                user_message="My timezone is Asia/Dubai.",
            )

        self.assertEqual(result.reply_text, "Noted.")
        write_events = latest_events_by_type(self.state_db, event_type="memory_write_requested", limit=10)
        self.assertTrue(write_events)
        observations = (write_events[0]["facts_json"] or {}).get("observations") or []
        self.assertEqual(observations[0]["predicate"], "profile.timezone")
        self.assertEqual(observations[0]["value"], "Asia/Dubai")
        influence_events = latest_events_by_type(self.state_db, event_type="plugin_or_chip_influence_recorded", limit=10)
        self.assertTrue(influence_events)
        detected = (influence_events[0]["facts_json"] or {}).get("detected_profile_fact") or {}
        self.assertEqual(detected.get("predicate"), "profile.timezone")
        self.assertEqual(detected.get("value"), "Asia/Dubai")

    def test_build_researcher_reply_injects_memory_backed_timezone_fact_for_timezone_query(self) -> None:
        self.config_manager.set_path("spark.researcher.enabled", True)
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)
        connect_exit, _, connect_stderr = self.run_cli(
            "auth",
            "connect",
            "custom",
            "--home",
            str(self.home),
            "--api-key",
            "minimax-secret",
            "--model",
            "MiniMax-M2.7",
            "--base-url",
            "https://api.minimax.io/v1",
        )
        self.assertEqual(connect_exit, 0, connect_stderr)

        write_profile_fact_to_memory(
            config_manager=self.config_manager,
            state_db=self.state_db,
            human_id="human-1",
            predicate="profile.timezone",
            value="Asia/Dubai",
            evidence_text="My timezone is Asia/Dubai.",
            fact_name="profile_timezone",
            session_id="session-timezone-query",
            turn_id="turn-timezone-query-write",
            channel_kind="telegram",
        )

        runtime_root = self.home / "fake-researcher"
        runtime_root.mkdir(parents=True, exist_ok=True)
        config_path = runtime_root / "spark-researcher.project.json"
        config_path.write_text("{}", encoding="utf-8")
        captured: dict[str, object] = {}

        def fake_build_advisory(path: Path, task: str, *, model: str = "generic", limit: int = 4, domain: str | None = None):
            return {
                "guidance": [],
                "epistemic_status": {
                    "status": "under_supported",
                    "packet_stability": {"status": "no_belief_packets"},
                },
                "selected_packet_ids": [],
                "trace_path": "trace:timezone-query",
            }

        def fake_direct_provider_prompt(*, provider, system_prompt: str, user_prompt: str, governance=None):
            captured["user_prompt"] = user_prompt
            return {"raw_response": "Your timezone is Asia/Dubai."}

        def fail_execute_with_research(*args, **kwargs):
            raise AssertionError("execute_with_research should not run for direct conversational fallback")

        with patch(
            "spark_intelligence.researcher_bridge.advisory.discover_researcher_runtime_root",
            return_value=(runtime_root, "configured"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.resolve_researcher_config_path",
            return_value=config_path,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._import_build_advisory",
            return_value=fake_build_advisory,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._import_execute_with_research",
            return_value=fail_execute_with_research,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=fake_direct_provider_prompt,
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-timezone-query",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-timezone-query",
                channel_kind="telegram",
                user_message="What timezone do you have for me?",
            )

        self.assertEqual(result.reply_text, "Your timezone is Asia/Dubai.")
        self.assertIn("[Memory action: PROFILE_FACT_STATUS]", str(captured["user_prompt"]))
        self.assertIn("timezone: Asia/Dubai", str(captured["user_prompt"]))
        read_events = latest_events_by_type(self.state_db, event_type="memory_read_requested", limit=10)
        self.assertTrue(read_events)
        self.assertEqual((read_events[0]["facts_json"] or {}).get("predicate"), "profile.timezone")

    def test_build_researcher_reply_persists_home_country_profile_fact_before_bridge_execution(self) -> None:
        self.config_manager.set_path("spark.researcher.enabled", True)
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)
        connect_exit, _, connect_stderr = self.run_cli(
            "auth",
            "connect",
            "custom",
            "--home",
            str(self.home),
            "--api-key",
            "minimax-secret",
            "--model",
            "MiniMax-M2.7",
            "--base-url",
            "https://api.minimax.io/v1",
        )
        self.assertEqual(connect_exit, 0, connect_stderr)

        runtime_root = self.home / "fake-researcher"
        runtime_root.mkdir(parents=True, exist_ok=True)
        config_path = runtime_root / "spark-researcher.project.json"
        config_path.write_text("{}", encoding="utf-8")

        def fake_build_advisory(path: Path, task: str, *, model: str = "generic", limit: int = 4, domain: str | None = None):
            return {
                "guidance": [],
                "epistemic_status": {
                    "status": "under_supported",
                    "packet_stability": {"status": "no_belief_packets"},
                },
                "selected_packet_ids": [],
                "trace_path": "trace:country-under-supported",
            }

        def fake_direct_provider_prompt(*, provider, system_prompt: str, user_prompt: str, governance=None):
            return {"raw_response": "Noted."}

        def fail_execute_with_research(*args, **kwargs):
            raise AssertionError("execute_with_research should not run for direct conversational fallback")

        with patch(
            "spark_intelligence.researcher_bridge.advisory.discover_researcher_runtime_root",
            return_value=(runtime_root, "configured"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.resolve_researcher_config_path",
            return_value=config_path,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._import_build_advisory",
            return_value=fake_build_advisory,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._import_execute_with_research",
            return_value=fail_execute_with_research,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=fake_direct_provider_prompt,
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-country-fact",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-country",
                channel_kind="telegram",
                user_message="My country is UAE.",
            )

        self.assertEqual(result.reply_text, "Noted.")
        write_events = latest_events_by_type(self.state_db, event_type="memory_write_requested", limit=10)
        self.assertTrue(write_events)
        observations = (write_events[0]["facts_json"] or {}).get("observations") or []
        self.assertEqual(observations[0]["predicate"], "profile.home_country")
        self.assertEqual(observations[0]["value"], "UAE")
        influence_events = latest_events_by_type(self.state_db, event_type="plugin_or_chip_influence_recorded", limit=10)
        self.assertTrue(influence_events)
        detected = (influence_events[0]["facts_json"] or {}).get("detected_profile_fact") or {}
        self.assertEqual(detected.get("predicate"), "profile.home_country")
        self.assertEqual(detected.get("value"), "UAE")

    def test_build_researcher_reply_injects_memory_backed_home_country_fact_for_country_query(self) -> None:
        self.config_manager.set_path("spark.researcher.enabled", True)
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)
        connect_exit, _, connect_stderr = self.run_cli(
            "auth",
            "connect",
            "custom",
            "--home",
            str(self.home),
            "--api-key",
            "minimax-secret",
            "--model",
            "MiniMax-M2.7",
            "--base-url",
            "https://api.minimax.io/v1",
        )
        self.assertEqual(connect_exit, 0, connect_stderr)

        write_profile_fact_to_memory(
            config_manager=self.config_manager,
            state_db=self.state_db,
            human_id="human-1",
            predicate="profile.home_country",
            value="UAE",
            evidence_text="My country is UAE.",
            fact_name="profile_home_country",
            session_id="session-country-query",
            turn_id="turn-country-query-write",
            channel_kind="telegram",
        )

        runtime_root = self.home / "fake-researcher"
        runtime_root.mkdir(parents=True, exist_ok=True)
        config_path = runtime_root / "spark-researcher.project.json"
        config_path.write_text("{}", encoding="utf-8")
        captured: dict[str, object] = {}

        def fake_build_advisory(path: Path, task: str, *, model: str = "generic", limit: int = 4, domain: str | None = None):
            return {
                "guidance": [],
                "epistemic_status": {
                    "status": "under_supported",
                    "packet_stability": {"status": "no_belief_packets"},
                },
                "selected_packet_ids": [],
                "trace_path": "trace:country-query",
            }

        def fake_direct_provider_prompt(*, provider, system_prompt: str, user_prompt: str, governance=None):
            captured["user_prompt"] = user_prompt
            return {"raw_response": "Your country is UAE."}

        def fail_execute_with_research(*args, **kwargs):
            raise AssertionError("execute_with_research should not run for direct conversational fallback")

        with patch(
            "spark_intelligence.researcher_bridge.advisory.discover_researcher_runtime_root",
            return_value=(runtime_root, "configured"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.resolve_researcher_config_path",
            return_value=config_path,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._import_build_advisory",
            return_value=fake_build_advisory,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._import_execute_with_research",
            return_value=fail_execute_with_research,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=fake_direct_provider_prompt,
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-country-query",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-country-query",
                channel_kind="telegram",
                user_message="What country do you have for me?",
            )

        self.assertEqual(result.reply_text, "Your country is UAE.")
        self.assertIn("[Memory action: PROFILE_FACT_STATUS]", str(captured["user_prompt"]))
        self.assertIn("country: UAE", str(captured["user_prompt"]))
        read_events = latest_events_by_type(self.state_db, event_type="memory_read_requested", limit=10)
        self.assertTrue(read_events)
        self.assertEqual((read_events[0]["facts_json"] or {}).get("predicate"), "profile.home_country")

    def test_build_researcher_reply_persists_preferred_name_profile_fact_before_bridge_execution(self) -> None:
        self.config_manager.set_path("spark.researcher.enabled", True)
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)
        connect_exit, _, connect_stderr = self.run_cli(
            "auth",
            "connect",
            "custom",
            "--home",
            str(self.home),
            "--api-key",
            "minimax-secret",
            "--model",
            "MiniMax-M2.7",
            "--base-url",
            "https://api.minimax.io/v1",
        )
        self.assertEqual(connect_exit, 0, connect_stderr)

        runtime_root = self.home / "fake-researcher"
        runtime_root.mkdir(parents=True, exist_ok=True)
        config_path = runtime_root / "spark-researcher.project.json"
        config_path.write_text("{}", encoding="utf-8")

        def fake_build_advisory(path: Path, task: str, *, model: str = "generic", limit: int = 4, domain: str | None = None):
            return {
                "guidance": [],
                "epistemic_status": {
                    "status": "under_supported",
                    "packet_stability": {"status": "no_belief_packets"},
                },
                "selected_packet_ids": [],
                "trace_path": "trace:name-under-supported",
            }

        def fake_direct_provider_prompt(*, provider, system_prompt: str, user_prompt: str, governance=None):
            return {"raw_response": "Noted."}

        def fail_execute_with_research(*args, **kwargs):
            raise AssertionError("execute_with_research should not run for direct conversational fallback")

        with patch(
            "spark_intelligence.researcher_bridge.advisory.discover_researcher_runtime_root",
            return_value=(runtime_root, "configured"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.resolve_researcher_config_path",
            return_value=config_path,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._import_build_advisory",
            return_value=fake_build_advisory,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._import_execute_with_research",
            return_value=fail_execute_with_research,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=fake_direct_provider_prompt,
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-name-fact",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-name",
                channel_kind="telegram",
                user_message="My name is Sarah.",
            )

        self.assertEqual(result.reply_text, "Noted.")
        write_events = latest_events_by_type(self.state_db, event_type="memory_write_requested", limit=10)
        self.assertTrue(write_events)
        observations = (write_events[0]["facts_json"] or {}).get("observations") or []
        self.assertEqual(observations[0]["predicate"], "profile.preferred_name")
        self.assertEqual(observations[0]["value"], "Sarah")
        influence_events = latest_events_by_type(self.state_db, event_type="plugin_or_chip_influence_recorded", limit=10)
        self.assertTrue(influence_events)
        detected = (influence_events[0]["facts_json"] or {}).get("detected_profile_fact") or {}
        self.assertEqual(detected.get("predicate"), "profile.preferred_name")
        self.assertEqual(detected.get("value"), "Sarah")

    def test_build_researcher_reply_injects_memory_backed_preferred_name_for_name_query(self) -> None:
        self.config_manager.set_path("spark.researcher.enabled", True)
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)
        connect_exit, _, connect_stderr = self.run_cli(
            "auth",
            "connect",
            "custom",
            "--home",
            str(self.home),
            "--api-key",
            "minimax-secret",
            "--model",
            "MiniMax-M2.7",
            "--base-url",
            "https://api.minimax.io/v1",
        )
        self.assertEqual(connect_exit, 0, connect_stderr)

        write_profile_fact_to_memory(
            config_manager=self.config_manager,
            state_db=self.state_db,
            human_id="human-1",
            predicate="profile.preferred_name",
            value="Sarah",
            evidence_text="My name is Sarah.",
            fact_name="profile_preferred_name",
            session_id="session-name-query",
            turn_id="turn-name-query-write",
            channel_kind="telegram",
        )

        runtime_root = self.home / "fake-researcher"
        runtime_root.mkdir(parents=True, exist_ok=True)
        config_path = runtime_root / "spark-researcher.project.json"
        config_path.write_text("{}", encoding="utf-8")
        captured: dict[str, object] = {}

        def fake_build_advisory(path: Path, task: str, *, model: str = "generic", limit: int = 4, domain: str | None = None):
            return {
                "guidance": [],
                "epistemic_status": {
                    "status": "under_supported",
                    "packet_stability": {"status": "no_belief_packets"},
                },
                "selected_packet_ids": [],
                "trace_path": "trace:name-query",
            }

        def fake_direct_provider_prompt(*, provider, system_prompt: str, user_prompt: str, governance=None):
            captured["user_prompt"] = user_prompt
            return {"raw_response": "Your name is Sarah."}

        def fail_execute_with_research(*args, **kwargs):
            raise AssertionError("execute_with_research should not run for direct conversational fallback")

        with patch(
            "spark_intelligence.researcher_bridge.advisory.discover_researcher_runtime_root",
            return_value=(runtime_root, "configured"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.resolve_researcher_config_path",
            return_value=config_path,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._import_build_advisory",
            return_value=fake_build_advisory,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._import_execute_with_research",
            return_value=fail_execute_with_research,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=fake_direct_provider_prompt,
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-name-query",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-name-query",
                channel_kind="telegram",
                user_message="What name do you have for me?",
            )

        self.assertEqual(result.reply_text, "Your name is Sarah.")
        self.assertIn("[Memory action: PROFILE_FACT_STATUS]", str(captured["user_prompt"]))
        self.assertIn("name: Sarah", str(captured["user_prompt"]))
        read_events = latest_events_by_type(self.state_db, event_type="memory_read_requested", limit=10)
        self.assertTrue(read_events)
        self.assertEqual((read_events[0]["facts_json"] or {}).get("predicate"), "profile.preferred_name")

    def test_build_researcher_reply_appends_swarm_recommendation_for_explicit_delegation(self) -> None:
        self.config_manager.set_path("spark.researcher.enabled", True)
        connect_exit, _, connect_stderr = self.run_cli(
            "auth",
            "connect",
            "custom",
            "--home",
            str(self.home),
            "--api-key",
            "minimax-secret",
            "--model",
            "MiniMax-M2.7",
            "--base-url",
            "https://api.minimax.io/v1",
        )
        self.assertEqual(connect_exit, 0, connect_stderr)

        runtime_root = self.home / "fake-researcher"
        runtime_root.mkdir(parents=True, exist_ok=True)
        config_path = runtime_root / "spark-researcher.project.json"
        config_path.write_text("{}", encoding="utf-8")

        def fake_build_advisory(path: Path, task: str, *, model: str = "generic", limit: int = 4, domain: str | None = None):
            return {
                "guidance": ["Use evidence-backed guidance."],
                "epistemic_status": {"status": "grounded", "packet_stability": {"status": "durable_supported"}},
                "selected_packet_ids": ["packet-1"],
                "trace_path": "trace:test",
            }

        def fake_execute_with_research(*args, **kwargs):
            return {
                "status": "ok",
                "decision": "approve",
                "response": {"raw_response": "We should break this into steps and assign owners."},
                "trace_path": "trace:execution",
            }

        with patch(
            "spark_intelligence.researcher_bridge.advisory.discover_researcher_runtime_root",
            return_value=(runtime_root, "configured"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.resolve_researcher_config_path",
            return_value=config_path,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._import_build_advisory",
            return_value=fake_build_advisory,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._import_execute_with_research",
            return_value=fake_execute_with_research,
        ), patch(
            "spark_intelligence.swarm_bridge.evaluate_swarm_escalation",
            return_value=type(
                "Decision",
                (),
                {
                    "escalate": True,
                    "mode": "manual_recommended",
                    "triggers": ["explicit_swarm", "parallel_work"],
                },
            )(),
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-swarm-recommend",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-1",
                channel_kind="telegram",
                user_message="Please delegate this as parallel swarm work.",
            )

        self.assertEqual(result.escalation_hint, "manual_recommended")
        self.assertEqual(result.routing_decision, "provider_execution+manual_recommended")
        self.assertIn("Swarm: recommended for this task", result.reply_text)
        self.assertIn("swarm=manual_recommended", result.evidence_summary)

    def test_build_researcher_reply_appends_swarm_recommendation_to_fallback_chat(self) -> None:
        self.config_manager.set_path("spark.researcher.enabled", True)
        connect_exit, _, connect_stderr = self.run_cli(
            "auth",
            "connect",
            "custom",
            "--home",
            str(self.home),
            "--api-key",
            "minimax-secret",
            "--model",
            "MiniMax-M2.7",
            "--base-url",
            "https://api.minimax.io/v1",
        )
        self.assertEqual(connect_exit, 0, connect_stderr)

        runtime_root = self.home / "fake-researcher"
        runtime_root.mkdir(parents=True, exist_ok=True)
        config_path = runtime_root / "spark-researcher.project.json"
        config_path.write_text("{}", encoding="utf-8")

        def fake_build_advisory(path: Path, task: str, *, model: str = "generic", limit: int = 4, domain: str | None = None):
            return {
                "guidance": [],
                "epistemic_status": {
                    "status": "under_supported",
                    "packet_stability": {"status": "no_belief_packets"},
                },
                "selected_packet_ids": [],
                "trace_path": "trace:under-supported",
            }

        with patch(
            "spark_intelligence.researcher_bridge.advisory.discover_researcher_runtime_root",
            return_value=(runtime_root, "configured"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.resolve_researcher_config_path",
            return_value=config_path,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._import_build_advisory",
            return_value=fake_build_advisory,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            return_value={"raw_response": "We can do that."},
        ), patch(
            "spark_intelligence.swarm_bridge.evaluate_swarm_escalation",
            return_value=type(
                "Decision",
                (),
                {
                    "escalate": True,
                    "mode": "manual_recommended",
                    "triggers": ["explicit_swarm"],
                },
            )(),
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-swarm-fallback",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-1",
                channel_kind="telegram",
                user_message="swarm this",
            )

        self.assertEqual(result.escalation_hint, "manual_recommended")
        self.assertEqual(result.routing_decision, "provider_fallback_chat+manual_recommended")
        self.assertIn("Swarm: recommended for this task", result.reply_text)
        self.assertIn("swarm=manual_recommended", result.evidence_summary)

    def test_build_researcher_reply_respects_disabled_conversational_fallback_policy(self) -> None:
        self.config_manager.set_path("spark.researcher.enabled", True)
        self.config_manager.set_path("spark.researcher.routing.conversational_fallback_enabled", False)
        connect_exit, _, connect_stderr = self.run_cli(
            "auth",
            "connect",
            "custom",
            "--home",
            str(self.home),
            "--api-key",
            "minimax-secret",
            "--model",
            "MiniMax-M2.7",
            "--base-url",
            "https://api.minimax.io/v1",
        )
        self.assertEqual(connect_exit, 0, connect_stderr)

        runtime_root = self.home / "fake-researcher"
        runtime_root.mkdir(parents=True, exist_ok=True)
        config_path = runtime_root / "spark-researcher.project.json"
        config_path.write_text("{}", encoding="utf-8")

        def fake_build_advisory(path: Path, task: str, *, model: str = "generic", limit: int = 4, domain: str | None = None):
            return {
                "guidance": [],
                "epistemic_status": {
                    "status": "under_supported",
                    "packet_stability": {"status": "no_belief_packets"},
                },
                "selected_packet_ids": [],
                "trace_path": "trace:under-supported",
            }

        def fake_execute_with_research(*args, **kwargs):
            return {
                "status": "ok",
                "decision": "approve",
                "response": {"raw_response": "Researcher-side provider execution reply"},
                "trace_path": "trace:execution",
            }

        with patch(
            "spark_intelligence.researcher_bridge.advisory.discover_researcher_runtime_root",
            return_value=(runtime_root, "configured"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.resolve_researcher_config_path",
            return_value=config_path,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._import_build_advisory",
            return_value=fake_build_advisory,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._import_execute_with_research",
            return_value=fake_execute_with_research,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("direct provider fallback should be disabled"),
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-fallback-disabled",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-1",
                channel_kind="telegram",
                user_message="hey",
            )

        self.assertEqual(result.routing_decision, "provider_execution")
        self.assertEqual(result.reply_text, "Researcher-side provider execution reply")

    def test_build_researcher_reply_uses_resolved_provider_model_family(self) -> None:
        self.config_manager.set_path("spark.researcher.enabled", True)
        connect_exit, _, connect_stderr = self.run_cli(
            "auth",
            "connect",
            "anthropic",
            "--home",
            str(self.home),
            "--api-key",
            "anthropic-secret",
            "--model",
            "claude-opus-4-6",
        )
        self.assertEqual(connect_exit, 0, connect_stderr)

        runtime_root = self.home / "fake-researcher"
        runtime_root.mkdir(parents=True, exist_ok=True)
        config_path = runtime_root / "spark-researcher.project.json"
        config_path.write_text("{}", encoding="utf-8")
        captured: dict[str, str] = {}
        captured_execution: dict[str, object] = {}

        def fake_build_advisory(path: Path, task: str, *, model: str = "generic", limit: int = 4, domain: str | None = None):
            captured["model"] = model
            return {
                "guidance": ["Use evidence-backed guidance."],
                "epistemic_status": {"status": "grounded", "packet_stability": {"status": "durable_supported"}},
                "selected_packet_ids": ["packet-1"],
                "trace_path": "trace:test",
            }

        def fake_execute_with_research(
            runtime_root: Path,
            *,
            advisory: dict[str, object],
            model: str,
            command_override: list[str] | None = None,
            dry_run: bool = False,
        ) -> dict[str, object]:
            captured_execution["model"] = model
            captured_execution["command_override"] = list(command_override or [])
            return {
                "status": "ok",
                "decision": "approve",
                "response": {"raw_response": "Provider-backed reply"},
                "trace_path": "trace:execution",
            }

        with patch(
            "spark_intelligence.researcher_bridge.advisory.discover_researcher_runtime_root",
            return_value=(runtime_root, "configured"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.resolve_researcher_config_path",
            return_value=config_path,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._import_build_advisory",
            return_value=fake_build_advisory,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._import_execute_with_research",
            return_value=fake_execute_with_research,
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-1",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-1",
                channel_kind="telegram",
                user_message="How should I answer this?",
            )

        self.assertEqual(captured["model"], "claude")
        self.assertEqual(captured_execution["model"], "claude")
        self.assertEqual(
            captured_execution["command_override"],
            [
                sys.executable,
                "-m",
                "spark_intelligence.llm.provider_wrapper",
                "{system_prompt_path}",
                "{user_prompt_path}",
                "{response_path}",
            ],
        )
        self.assertEqual(result.mode, "external_configured")
        self.assertEqual(result.reply_text, "Provider-backed reply")
        self.assertEqual(result.provider_id, "anthropic")
        self.assertEqual(result.provider_auth_method, "api_key_env")
        self.assertEqual(result.provider_model, "claude-opus-4-6")
        self.assertEqual(result.provider_model_family, "claude")
        self.assertEqual(result.provider_execution_transport, "direct_http")

    def test_build_researcher_reply_keeps_codex_on_external_wrapper_transport(self) -> None:
        self.config_manager.set_path("spark.researcher.enabled", True)
        start_exit, start_stdout, start_stderr = self.run_cli(
            "auth",
            "login",
            "openai-codex",
            "--home",
            str(self.home),
            "--json",
        )
        self.assertEqual(start_exit, 0, start_stderr)

        import json

        start_payload = json.loads(start_stdout)
        callback_url = (
            "http://127.0.0.1:1455/auth/callback"
            f"?state={start_payload['callback_state']}&code=test-oauth-code"
        )

        with patch(
            "spark_intelligence.auth.service.exchange_oauth_authorization_code",
            return_value={
                "access_token": "oauth-access-token",
                "refresh_token": "oauth-refresh-token",
                "expires_in": 3600,
            },
        ):
            complete_exit, _, complete_stderr = self.run_cli(
                "auth",
                "login",
                "openai-codex",
                "--home",
                str(self.home),
                "--callback-url",
                callback_url,
            )
        self.assertEqual(complete_exit, 0, complete_stderr)

        runtime_root = self.home / "fake-researcher"
        runtime_root.mkdir(parents=True, exist_ok=True)
        config_path = runtime_root / "spark-researcher.project.json"
        config_path.write_text("{}", encoding="utf-8")
        captured: dict[str, object] = {}

        def fake_build_advisory(path: Path, task: str, *, model: str = "generic", limit: int = 4, domain: str | None = None):
            captured["advisory_model"] = model
            return {
                "guidance": ["Use evidence-backed guidance."],
                "epistemic_status": {"status": "grounded", "packet_stability": {"status": "durable_supported"}},
                "selected_packet_ids": ["packet-1"],
                "trace_path": "trace:test",
            }

        def fake_execute_with_research(
            runtime_root: Path,
            *,
            advisory: dict[str, object],
            model: str,
            command_override: list[str] | None = None,
            dry_run: bool = False,
        ) -> dict[str, object]:
            captured["execution_model"] = model
            captured["command_override"] = command_override
            return {
                "status": "ok",
                "decision": "approve",
                "response": {"raw_response": "Codex-backed reply"},
                "trace_path": "trace:execution",
            }

        with patch(
            "spark_intelligence.researcher_bridge.advisory.discover_researcher_runtime_root",
            return_value=(runtime_root, "configured"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.resolve_researcher_config_path",
            return_value=config_path,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._import_build_advisory",
            return_value=fake_build_advisory,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._import_execute_with_research",
            return_value=fake_execute_with_research,
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-3",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-1",
                channel_kind="telegram",
                user_message="Should codex run through the shared bridge?",
            )

        self.assertEqual(captured["advisory_model"], "codex")
        self.assertEqual(captured["execution_model"], "codex")
        self.assertEqual(captured["command_override"], None)
        self.assertEqual(result.reply_text, "Codex-backed reply")
        self.assertEqual(result.provider_id, "openai-codex")
        self.assertEqual(result.provider_auth_method, "oauth")
        self.assertEqual(result.provider_model_family, "codex")
        self.assertEqual(result.provider_execution_transport, "external_cli_wrapper")

    def test_build_researcher_reply_fails_closed_when_provider_auth_is_unresolved(self) -> None:
        self.config_manager.set_path("spark.researcher.enabled", True)
        connect_exit, _, connect_stderr = self.run_cli(
            "auth",
            "connect",
            "openrouter",
            "--home",
            str(self.home),
            "--api-key-env",
            "MISSING_OPENROUTER_KEY",
            "--model",
            "anthropic/claude-3.7-sonnet",
        )
        self.assertEqual(connect_exit, 0, connect_stderr)

        result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-2",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-1",
            channel_kind="telegram",
            user_message="Should I reply?",
        )

        self.assertEqual(result.mode, "bridge_error")
        self.assertEqual(result.provider_id, None)
        self.assertEqual(result.provider_model_family, "generic")
        self.assertIn("missing secret value", result.reply_text)
