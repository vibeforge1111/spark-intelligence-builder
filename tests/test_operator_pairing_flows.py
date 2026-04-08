import json
from types import SimpleNamespace
from unittest.mock import patch

from spark_intelligence.adapters.telegram.runtime import simulate_telegram_update
from spark_intelligence.identity.service import (
    approve_pairing,
    pairing_summary,
    read_canonical_agent_state,
    record_pairing_context,
    review_pairings,
)
from spark_intelligence.observability.store import recent_resume_richness_guard_records
from spark_intelligence.personality.loader import load_personality_profile
from spark_intelligence.researcher_bridge.advisory import ResearcherBridgeResult

from tests.test_support import SparkTestCase, make_telegram_update


class OperatorPairingFlowTests(SparkTestCase):
    def test_pairing_context_preserves_richer_state_on_sparse_resume_write(self) -> None:
        record_pairing_context(
            state_db=self.state_db,
            channel_id="telegram",
            external_user_id="111",
            context={
                "display_name": "alice",
                "telegram_username": "alice",
                "chat_id": "chat-1",
                "last_message_text": "hello from a richer state",
                "last_update_id": 101,
                "last_seen_at": "2026-03-28T00:00:00+00:00",
            },
        )
        record_pairing_context(
            state_db=self.state_db,
            channel_id="telegram",
            external_user_id="111",
            context={
                "last_seen_at": "2026-03-28T00:05:00+00:00",
            },
        )

        with self.state_db.connect() as conn:
            context = conn.execute(
                "SELECT value FROM runtime_state WHERE state_key = ? LIMIT 1",
                ("pairing_context:telegram:111",),
            ).fetchone()
        self.assertIsNotNone(context)
        payload = json.loads(context["value"])
        self.assertEqual(payload["last_seen_at"], "2026-03-28T00:05:00+00:00")
        self.assertEqual(payload["last_message_text"], "hello from a richer state")
        self.assertEqual(payload["chat_id"], "chat-1")
        guard_rows = recent_resume_richness_guard_records(self.state_db, limit=10)
        self.assertTrue(guard_rows)
        self.assertEqual(guard_rows[0]["state_key"], "pairing_context:telegram:111")

    def test_allowlist_user_is_allowed_without_creating_pending_or_approved_pairing(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        result = simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=100,
                user_id="111",
                username="alice",
                text="hello",
            ),
        )

        self.assertTrue(result.ok)
        self.assertEqual(result.decision, "allowed")
        pending = review_pairings(self.state_db, channel_id="telegram", status="pending")
        self.assertEqual(pending.rows, [])
        summary = pairing_summary(state_db=self.state_db, channel_id="telegram")
        self.assertEqual(summary.counts["approved"], 0)

    def test_pending_pairing_creates_reviewable_request(self) -> None:
        self.add_telegram_channel()

        result = simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=101,
                user_id="111",
                username="alice",
                text="/start",
            ),
        )

        self.assertFalse(result.ok)
        self.assertEqual(result.decision, "pending_pairing")
        self.assertIn("operator approval", str(result.detail["response_text"]))

        report = review_pairings(self.state_db, channel_id="telegram", status="pending")
        self.assertEqual(len(report.rows), 1)
        self.assertEqual(report.rows[0]["external_user_id"], "111")
        self.assertEqual(report.rows[0]["status"], "pending")
        self.assertEqual(report.rows[0]["context"]["telegram_username"], "alice")

    def test_hold_latest_logs_exact_target_ref(self) -> None:
        self.add_telegram_channel()
        simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=102,
                user_id="111",
                username="alice",
                text="hello",
            ),
        )

        exit_code, _, stderr = self.run_cli(
            "operator",
            "hold-latest",
            "telegram",
            "--home",
            str(self.home),
            "--reason",
            "manual review",
        )

        self.assertEqual(exit_code, 0, stderr)
        held = review_pairings(self.state_db, channel_id="telegram", status="held")
        self.assertEqual(len(held.rows), 1)
        self.assertEqual(held.rows[0]["external_user_id"], "111")

        history_exit, history_stdout, history_stderr = self.run_cli(
            "operator",
            "history",
            "--home",
            str(self.home),
            "--action",
            "hold_latest_pairing",
            "--json",
        )

        self.assertEqual(history_exit, 0, history_stderr)
        payload = json.loads(history_stdout)
        self.assertEqual(payload["rows"][0]["target_ref"], "telegram:111")
        self.assertEqual(payload["rows"][0]["reason"], "manual review")

    def test_approve_latest_restores_allowed_dm(self) -> None:
        self.add_telegram_channel()
        simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=103,
                user_id="111",
                username="alice",
                text="/start",
            ),
        )

        exit_code, _, stderr = self.run_cli(
            "operator",
            "approve-latest",
            "telegram",
            "--home",
            str(self.home),
        )

        self.assertEqual(exit_code, 0, stderr)
        summary = pairing_summary(state_db=self.state_db, channel_id="telegram")
        self.assertEqual(summary.counts["approved"], 1)
        self.assertEqual(summary.latest_approved["external_user_id"], "111")

        follow_up = simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=104,
                user_id="111",
                username="alice",
                text="need help",
            ),
        )

        self.assertTrue(follow_up.ok)
        self.assertEqual(follow_up.decision, "allowed")
        self.assertIn("Pairing approved.", str(follow_up.detail["response_text"]))

    def test_first_post_approval_dm_runs_multi_turn_agent_onboarding(self) -> None:
        self.add_telegram_channel()
        simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=103,
                user_id="111",
                username="alice",
                text="/start",
            ),
        )

        exit_code, _, stderr = self.run_cli(
            "operator",
            "approve-latest",
            "telegram",
            "--home",
            str(self.home),
        )
        self.assertEqual(exit_code, 0, stderr)

        with patch(
            "spark_intelligence.adapters.telegram.runtime.build_researcher_reply",
            side_effect=AssertionError("researcher bridge should not run during onboarding"),
        ):
            first_turn = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=104,
                    user_id="111",
                    username="alice",
                    text="hey",
                ),
            )
            second_turn = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=105,
                    user_id="111",
                    username="alice",
                    text="Atlas",
                ),
            )
            third_turn = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=106,
                    user_id="111",
                    username="alice",
                    text="calm, strategic, very direct, low-fluff",
                ),
            )

        self.assertTrue(first_turn.ok)
        self.assertIn("Pairing approved.", str(first_turn.detail["response_text"]))
        self.assertIn("What should I call your agent?", str(first_turn.detail["response_text"]))

        self.assertTrue(second_turn.ok)
        self.assertIn("Your agent is now `Atlas`", str(second_turn.detail["response_text"]))
        self.assertIn("describe the personality", str(second_turn.detail["response_text"]))

        self.assertTrue(third_turn.ok)
        self.assertIn("Locked in.", str(third_turn.detail["response_text"]))
        self.assertIn("connect your Spark Swarm agent", str(third_turn.detail["response_text"]))

        agent_state = read_canonical_agent_state(
            state_db=self.state_db,
            human_id="human:telegram:111",
        )
        self.assertEqual(agent_state.agent_name, "Atlas")
        profile = load_personality_profile(
            human_id="human:telegram:111",
            agent_id=agent_state.agent_id,
            state_db=self.state_db,
            config_manager=self.config_manager,
        )
        assert profile is not None
        self.assertTrue(profile["agent_persona_applied"])
        self.assertGreater(profile["traits"]["directness"], 0.5)
        self.assertEqual(profile["agent_persona_name"], "Atlas")
        self.assertIn("calm, strategic, very direct, low-fluff", str(profile["agent_persona_summary"]))

    def test_revoke_latest_blocks_future_dm_with_revoked_reply(self) -> None:
        self.add_telegram_channel()
        simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=105,
                user_id="111",
                username="alice",
                text="hello",
            ),
        )

        exit_code, _, stderr = self.run_cli(
            "operator",
            "revoke-latest",
            "telegram",
            "--home",
            str(self.home),
            "--reason",
            "deny",
        )

        self.assertEqual(exit_code, 0, stderr)
        summary = pairing_summary(state_db=self.state_db, channel_id="telegram")
        self.assertEqual(summary.counts["revoked"], 1)

        follow_up = simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=106,
                user_id="111",
                username="alice",
                text="hello again",
            ),
        )

        self.assertFalse(follow_up.ok)
        self.assertEqual(follow_up.decision, "revoked")
        self.assertIn("no longer paired", str(follow_up.detail["response_text"]))

    def test_narrowing_allowlist_blocks_removed_user_even_after_prior_access(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111", "222"])

        first_access = simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=107,
                user_id="222",
                username="bob",
                text="hello",
            ),
        )
        self.assertTrue(first_access.ok)
        self.assertEqual(first_access.decision, "allowed")

        exit_code, _, stderr = self.run_cli(
            "channel",
            "add",
            "telegram",
            "--home",
            str(self.home),
            "--pairing-mode",
            "allowlist",
            "--allowed-user",
            "111",
        )
        self.assertEqual(exit_code, 0, stderr)

        follow_up = simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=108,
                user_id="222",
                username="bob",
                text="hello again",
            ),
        )

        self.assertFalse(follow_up.ok)
        self.assertEqual(follow_up.decision, "blocked")
        self.assertIn("requires explicit allowlist access", str(follow_up.detail["response_text"]))

    def test_revoke_pairing_does_not_override_configured_allowlist_access(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])
        approve_pairing(
            state_db=self.state_db,
            channel_id="telegram",
            external_user_id="111",
            display_name="alice",
        )

        exit_code, _, stderr = self.run_cli(
            "pairings",
            "revoke",
            "telegram",
            "111",
            "--home",
            str(self.home),
        )
        self.assertEqual(exit_code, 0, stderr)

        follow_up = simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=109,
                user_id="111",
                username="alice",
                text="still here",
            ),
        )

        self.assertTrue(follow_up.ok)
        self.assertEqual(follow_up.decision, "allowed")

    def test_telegram_replies_hide_think_blocks_by_default(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        with patch(
            "spark_intelligence.adapters.telegram.runtime.build_researcher_reply",
            return_value=ResearcherBridgeResult(
                request_id="req-think-off",
                reply_text="<think>private reasoning</think>\n\nHello there.",
                evidence_summary="status=under_supported provider_fallback=direct_http_chat",
                escalation_hint=None,
                trace_ref="trace:think-off",
                mode="external_configured",
                runtime_root="C:/fake-researcher",
                config_path="C:/fake-researcher/spark-researcher.project.json",
                attachment_context={},
                provider_id="custom",
                provider_auth_profile_id="custom:default",
                provider_auth_method="api_key_env",
                provider_model="MiniMax-M2.7",
                provider_model_family="generic",
                provider_execution_transport="direct_http",
                provider_base_url="https://api.minimax.io/v1",
                provider_source="config+env",
            ),
        ):
            result = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=110,
                    user_id="111",
                    username="alice",
                    text="hey",
                ),
            )

        self.assertTrue(result.ok)
        self.assertEqual(result.detail["response_text"], "Hello there.")

    def test_telegram_replies_strip_internal_swarm_routing_note(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        with patch(
            "spark_intelligence.adapters.telegram.runtime.build_researcher_reply",
            return_value=ResearcherBridgeResult(
                request_id="req-swarm-routing-note",
                reply_text=(
                    "Hey! What's on your mind?\n\n"
                    "Swarm: recommended for this task because it asks for delegation or multi-agent work "
                    "(multi_chip_context)."
                ),
                evidence_summary="status=under_supported provider_fallback=direct_http_chat",
                escalation_hint=None,
                trace_ref="trace:swarm-routing-note",
                mode="external_configured",
                runtime_root="C:/fake-researcher",
                config_path="C:/fake-researcher/spark-researcher.project.json",
                attachment_context={},
                provider_id="custom",
                provider_auth_profile_id="custom:default",
                provider_auth_method="api_key_env",
                provider_model="MiniMax-M2.7",
                provider_model_family="generic",
                provider_execution_transport="direct_http",
                provider_base_url="https://api.minimax.io/v1",
                provider_source="config+env",
            ),
        ):
            result = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=111,
                    user_id="111",
                    username="alice",
                    text="delegate this if needed",
                ),
            )

        self.assertTrue(result.ok)
        self.assertEqual(result.detail["response_text"], "Hey! What's on your mind?")
        self.assertNotIn("Swarm:", str(result.detail["response_text"]))

    def test_think_command_toggles_telegram_visibility(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        enable_result = simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=111,
                user_id="111",
                username="alice",
                text="/think on",
            ),
        )
        self.assertTrue(enable_result.ok)
        self.assertIn("Thinking visibility enabled", str(enable_result.detail["response_text"]))

        with patch(
            "spark_intelligence.adapters.telegram.runtime.build_researcher_reply",
            return_value=ResearcherBridgeResult(
                request_id="req-think-on",
                reply_text="<think>private reasoning</think>\n\nHello there.",
                evidence_summary="status=under_supported provider_fallback=direct_http_chat",
                escalation_hint=None,
                trace_ref="trace:think-on",
                mode="external_configured",
                runtime_root="C:/fake-researcher",
                config_path="C:/fake-researcher/spark-researcher.project.json",
                attachment_context={},
                provider_id="custom",
                provider_auth_profile_id="custom:default",
                provider_auth_method="api_key_env",
                provider_model="MiniMax-M2.7",
                provider_model_family="generic",
                provider_execution_transport="direct_http",
                provider_base_url="https://api.minimax.io/v1",
                provider_source="config+env",
            ),
        ):
            visible_result = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=112,
                    user_id="111",
                    username="alice",
                    text="hey again",
                ),
            )

        self.assertTrue(visible_result.ok)
        self.assertIn("<think>private reasoning</think>", str(visible_result.detail["response_text"]))

        disable_result = simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=113,
                user_id="111",
                username="alice",
                text="/think off",
            ),
        )
        self.assertTrue(disable_result.ok)
        self.assertIn("Thinking visibility disabled", str(disable_result.detail["response_text"]))

    def test_swarm_status_command_returns_live_bridge_summary(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        with patch(
            "spark_intelligence.adapters.telegram.runtime.swarm_status",
            return_value=SimpleNamespace(
                api_ready=True,
                auth_state="configured",
                last_sync={"mode": "uploaded"},
                last_decision={"mode": "manual_recommended"},
            ),
        ):
            result = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=114,
                    user_id="111",
                    username="alice",
                    text="/swarm status",
                ),
            )

        self.assertTrue(result.ok)
        self.assertIn("Swarm is ready.", str(result.detail["response_text"]))
        self.assertIn("Auth: configured.", str(result.detail["response_text"]))
        self.assertIn("Last sync: uploaded.", str(result.detail["response_text"]))

    def test_natural_language_swarm_status_command_returns_live_bridge_summary(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        with patch(
            "spark_intelligence.adapters.telegram.runtime.swarm_status",
            return_value=SimpleNamespace(
                api_ready=True,
                auth_state="configured",
                last_sync={"mode": "uploaded"},
                last_decision={"mode": "manual_recommended"},
            ),
        ):
            result = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=214,
                    user_id="111",
                    username="alice",
                    text="Can you show me the swarm status?",
                ),
            )

        self.assertTrue(result.ok)
        self.assertIn("Swarm is ready.", str(result.detail["response_text"]))
        self.assertEqual(result.detail["bridge_mode"], "runtime_command")

    def test_swarm_evaluate_command_returns_escalation_decision(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        with patch(
            "spark_intelligence.adapters.telegram.runtime.evaluate_swarm_escalation",
            return_value=SimpleNamespace(
                mode="manual_recommended",
                escalate=True,
                triggers=["explicit_swarm", "parallel_work"],
                reason="This task shows explicit escalation signals and Spark Swarm is available.",
            ),
        ):
            result = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=115,
                    user_id="111",
                    username="alice",
                    text="/swarm evaluate delegate this as parallel swarm work",
                ),
            )

        self.assertTrue(result.ok)
        self.assertIn("Swarm decision: manual_recommended.", str(result.detail["response_text"]))
        self.assertIn("Escalate: yes.", str(result.detail["response_text"]))
        self.assertIn("explicit_swarm, parallel_work", str(result.detail["response_text"]))

    def test_natural_language_swarm_evaluate_command_returns_escalation_decision(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        with patch(
            "spark_intelligence.adapters.telegram.runtime.evaluate_swarm_escalation",
            return_value=SimpleNamespace(
                mode="manual_recommended",
                escalate=True,
                triggers=["explicit_swarm", "parallel_work"],
                reason="This task shows explicit escalation signals and Spark Swarm is available.",
            ),
        ) as evaluate_mock:
            result = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=215,
                    user_id="111",
                    username="alice",
                    text="Can you evaluate this for swarm: delegate this as parallel swarm work",
                ),
            )

        self.assertTrue(result.ok)
        self.assertIn("Swarm decision: manual_recommended.", str(result.detail["response_text"]))
        self.assertEqual(evaluate_mock.call_args.kwargs["task"], "delegate this as parallel swarm work")

    def test_swarm_sync_command_runs_sync_from_telegram_runtime(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        with patch(
            "spark_intelligence.adapters.telegram.runtime.sync_swarm_collective",
            return_value=SimpleNamespace(
                ok=True,
                mode="uploaded",
                accepted=True,
                message="Uploaded the latest Spark Researcher collective payload to Spark Swarm.",
            ),
        ):
            result = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=116,
                    user_id="111",
                    username="alice",
                    text="/swarm sync",
                ),
            )

        self.assertTrue(result.ok)
        self.assertIn("Swarm sync ok.", str(result.detail["response_text"]))
        self.assertIn("Mode: uploaded.", str(result.detail["response_text"]))
        self.assertIn("Accepted: yes.", str(result.detail["response_text"]))

    def test_natural_language_swarm_sync_command_runs_sync_from_telegram_runtime(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        with patch(
            "spark_intelligence.adapters.telegram.runtime.sync_swarm_collective",
            return_value=SimpleNamespace(
                ok=True,
                mode="uploaded",
                accepted=True,
                message="Uploaded the latest Spark Researcher collective payload to Spark Swarm.",
            ),
        ):
            result = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=216,
                    user_id="111",
                    username="alice",
                    text="Please sync with swarm",
                ),
            )

        self.assertTrue(result.ok)
        self.assertIn("Swarm sync ok.", str(result.detail["response_text"]))
        self.assertEqual(result.detail["bridge_mode"], "runtime_command")

    def test_generic_swarm_mention_stays_on_normal_chat_path(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        with patch(
            "spark_intelligence.adapters.telegram.runtime.build_researcher_reply",
            return_value=ResearcherBridgeResult(
                request_id="req-swarm-chat",
                reply_text="Normal chat path.",
                evidence_summary="status=under_supported provider_fallback=direct_http_chat",
                escalation_hint=None,
                trace_ref="trace:swarm-chat",
                mode="external_configured",
                runtime_root="C:/fake-researcher",
                config_path="C:/fake-researcher/spark-researcher.project.json",
                attachment_context=None,
                provider_id="custom",
                provider_auth_profile_id="custom:default",
                provider_auth_method="api_key_env",
                provider_model="MiniMax-M2.7",
                provider_model_family="generic",
                provider_execution_transport="direct_http",
                provider_base_url="https://api.minimax.io/v1",
                provider_source="config+env",
                routing_decision="provider_fallback_chat",
                active_chip_key="startup-yc",
                active_chip_task_type="memo_quality",
                active_chip_evaluate_used=True,
            ),
        ) as bridge_mock:
            result = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=217,
                    user_id="111",
                    username="alice",
                    text="I was reading about Spark Swarm today.",
                ),
            )

        self.assertTrue(result.ok)
        self.assertEqual(result.detail["response_text"], "Normal chat path.")
        self.assertEqual(result.detail["bridge_mode"], "external_configured")
        bridge_mock.assert_called_once()

    def test_status_and_gateway_traces_surface_bridge_route_and_active_chip(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        with patch(
            "spark_intelligence.adapters.telegram.runtime.build_researcher_reply",
            return_value=ResearcherBridgeResult(
                request_id="req-route",
                reply_text="Hello there.",
                evidence_summary="status=under_supported provider_fallback=direct_http_chat",
                escalation_hint=None,
                trace_ref="trace:route",
                mode="external_configured",
                runtime_root="C:/fake-researcher",
                config_path="C:/fake-researcher/spark-researcher.project.json",
                attachment_context={"active_chip_keys": ["startup-yc"], "active_path_key": "startup-operator"},
                provider_id="custom",
                provider_auth_profile_id="custom:default",
                provider_auth_method="api_key_env",
                provider_model="MiniMax-M2.7",
                provider_model_family="generic",
                provider_execution_transport="direct_http",
                provider_base_url="https://api.minimax.io/v1",
                provider_source="config+env",
                routing_decision="provider_fallback_chat",
                active_chip_key="startup-yc",
                active_chip_task_type="diagnostic_questioning",
                active_chip_evaluate_used=True,
            ),
        ):
            result = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=115,
                    user_id="111",
                    username="alice",
                    text="hey",
                ),
            )

        self.assertTrue(result.ok)

        status_exit, status_stdout, status_stderr = self.run_cli(
            "status",
            "--home",
            str(self.home),
        )
        self.assertEqual(status_exit, 1, status_stderr)
        self.assertIn("- last bridge route: provider_fallback_chat", status_stdout)
        self.assertIn("- last active chip route: startup-yc:diagnostic_questioning used=yes", status_stdout)

        traces_exit, traces_stdout, traces_stderr = self.run_cli(
            "gateway",
            "traces",
            "--home",
            str(self.home),
            "--json",
            "--limit",
            "5",
        )
        self.assertEqual(traces_exit, 0, traces_stderr)
        payload = json.loads(traces_stdout)
        processed = [record for record in payload if record.get("event") == "telegram_update_processed"]
        self.assertEqual(len(processed), 1)
        self.assertEqual(processed[0]["routing_decision"], "provider_fallback_chat")
        self.assertEqual(processed[0]["active_chip_key"], "startup-yc")
        self.assertEqual(processed[0]["active_chip_task_type"], "diagnostic_questioning")
        self.assertTrue(processed[0]["active_chip_evaluate_used"])
