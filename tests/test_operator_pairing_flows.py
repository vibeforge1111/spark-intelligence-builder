import json
from types import SimpleNamespace
from unittest.mock import patch

from spark_intelligence.adapters.telegram.runtime import simulate_telegram_update
from spark_intelligence.identity.service import (
    approve_pairing,
    pairing_summary,
    read_canonical_agent_state,
    record_pairing_context,
    rename_agent_identity,
    review_pairings,
)
from spark_intelligence.observability.store import recent_resume_richness_guard_records
from spark_intelligence.personality.loader import load_personality_profile, save_agent_persona_profile
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
        self.assertIn("alice is live in this Telegram DM now.", str(follow_up.detail["response_text"]))

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
        self.assertIn("alice is live in this Telegram DM now.", str(first_turn.detail["response_text"]))
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

    def test_swarm_status_command_uses_saved_agent_identity_in_runtime_reply(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])
        approve_pairing(
            state_db=self.state_db,
            channel_id="telegram",
            external_user_id="111",
            display_name="Alice",
        )
        rename_agent_identity(
            state_db=self.state_db,
            human_id="human:telegram:111",
            new_name="Atlas",
            source_surface="telegram",
            source_ref="test",
        )
        save_agent_persona_profile(
            agent_id="agent:human:telegram:111",
            human_id="human:telegram:111",
            state_db=self.state_db,
            base_traits={
                "warmth": 0.62,
                "directness": 0.83,
                "playfulness": 0.2,
                "pacing": 0.66,
                "assertiveness": 0.74,
            },
            persona_name="Atlas",
            persona_summary="Calm, strategic, very direct, low-fluff.",
        )

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
                    update_id=314,
                    user_id="111",
                    username="alice",
                    text="/swarm status",
                ),
            )

        self.assertTrue(result.ok)
        self.assertIn("Atlas: Swarm is ready.", str(result.detail["response_text"]))

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

    def test_swarm_overview_command_returns_hosted_summary(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        with patch(
            "spark_intelligence.adapters.telegram.runtime.swarm_read_overview",
            return_value={
                "session": {"workspaceName": "Vibe Forge Workspace"},
                "agent": {"name": "Vibe Forge Agent"},
                "attachedRepos": [
                    {"verificationState": "verified"},
                    {"verificationState": "pending"},
                ],
            },
        ):
            result = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=218,
                    user_id="111",
                    username="alice",
                    text="/swarm overview",
                ),
            )

        self.assertTrue(result.ok)
        self.assertIn("Swarm overview:", str(result.detail["response_text"]))
        self.assertIn("Vibe Forge Workspace", str(result.detail["response_text"]))
        self.assertIn("2 attached, 1 verified, 1 pending", str(result.detail["response_text"]))

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

    def test_natural_language_swarm_upgrades_command_returns_pending_summary(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        with patch(
            "spark_intelligence.adapters.telegram.runtime.swarm_read_upgrades",
            return_value=[
                {
                    "id": "upg-1",
                    "status": "awaiting_review",
                    "changeSummary": "Tighten startup benchmark prompt",
                    "riskLevel": "medium",
                    "updatedAt": "2026-04-08T10:00:00Z",
                },
                {
                    "id": "upg-2",
                    "status": "queued",
                    "changeSummary": "Refresh YC specialization defaults",
                    "riskLevel": "low",
                    "updatedAt": "2026-04-08T09:00:00Z",
                },
            ],
        ):
            result = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=219,
                    user_id="111",
                    username="alice",
                    text="What upgrades are pending in swarm?",
                ),
            )

        self.assertTrue(result.ok)
        self.assertIn("Swarm upgrades:", str(result.detail["response_text"]))
        self.assertIn("2 pending upgrade(s).", str(result.detail["response_text"]))
        self.assertIn("Next: `/swarm deliver upg-1` or `/swarm sync-delivery upg-1`", str(result.detail["response_text"]))
        self.assertEqual(result.detail["bridge_mode"], "runtime_command")

    def test_natural_language_swarm_issues_command_returns_open_issues(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        with patch(
            "spark_intelligence.adapters.telegram.runtime.swarm_read_operator_issues",
            return_value=[
                {
                    "id": "issue-1",
                    "status": "open",
                    "severity": "critical",
                    "summary": "Collective sync failed for the current workspace.",
                    "updatedAt": "2026-04-08T10:00:00Z",
                },
                {
                    "id": "issue-2",
                    "status": "resolved",
                    "severity": "warn",
                    "summary": "Old issue",
                    "updatedAt": "2026-04-07T10:00:00Z",
                },
            ],
        ):
            result = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=220,
                    user_id="111",
                    username="alice",
                    text="Show me operator issues in swarm",
                ),
            )

        self.assertTrue(result.ok)
        self.assertIn("Swarm operator issues:", str(result.detail["response_text"]))
        self.assertIn("1 open issue(s).", str(result.detail["response_text"]))
        self.assertIn("Collective sync failed", str(result.detail["response_text"]))

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

    def test_swarm_specializations_command_returns_recent_specializations(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        with patch(
            "spark_intelligence.adapters.telegram.runtime.swarm_read_specializations",
            return_value=[
                {
                    "id": "spec-1",
                    "label": "Startup",
                    "evolutionMode": "review_required",
                    "updatedAt": "2026-04-08T10:00:00Z",
                }
            ],
        ):
            result = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=2161,
                    user_id="111",
                    username="alice",
                    text="/swarm specializations",
                ),
            )

        self.assertTrue(result.ok)
        self.assertIn("Swarm specializations:", str(result.detail["response_text"]))
        self.assertIn("spec-1", str(result.detail["response_text"]))
        self.assertIn("review_required", str(result.detail["response_text"]))
        self.assertIn("Next: `/swarm mode spec-1 review_required`", str(result.detail["response_text"]))

    def test_natural_language_swarm_insights_command_returns_actionable_insights(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        with patch(
            "spark_intelligence.adapters.telegram.runtime.swarm_read_insights",
            return_value=[
                {
                    "id": "insight-1",
                    "summary": "Homepage headline insight",
                    "status": "captured",
                    "updatedAt": "2026-04-08T10:00:00Z",
                },
                {
                    "id": "insight-2",
                    "summary": "Old contradicted insight",
                    "status": "contradicted",
                    "updatedAt": "2026-04-07T10:00:00Z",
                },
            ],
        ):
            result = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=2162,
                    user_id="111",
                    username="alice",
                    text="show me absorbable insights in swarm",
                ),
            )

        self.assertTrue(result.ok)
        self.assertIn("Swarm insights:", str(result.detail["response_text"]))
        self.assertIn("insight-1", str(result.detail["response_text"]))
        self.assertNotIn("insight-2", str(result.detail["response_text"]))
        self.assertIn("Next: `/swarm absorb insight-1 because <reason>`", str(result.detail["response_text"]))

    def test_scoped_swarm_insights_command_filters_by_specialization(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        with (
            patch(
                "spark_intelligence.adapters.telegram.runtime.swarm_read_specializations",
                return_value=[
                    {"id": "specialization:startup-operator", "key": "startup-operator", "label": "Startup Operator"},
                ],
            ),
            patch(
                "spark_intelligence.adapters.telegram.runtime.swarm_read_insights",
                return_value=[
                    {
                        "id": "insight-1",
                        "specializationId": "specialization:startup-operator",
                        "summary": "Startup operator insight",
                        "status": "captured",
                        "updatedAt": "2026-04-08T10:00:00Z",
                    },
                    {
                        "id": "insight-2",
                        "specializationId": "specialization:other-lane",
                        "summary": "Other lane insight",
                        "status": "captured",
                        "updatedAt": "2026-04-08T09:00:00Z",
                    },
                ],
            ),
        ):
            result = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=21621,
                    user_id="111",
                    username="alice",
                    text="show me Startup Operator insights in swarm",
                ),
            )

        self.assertTrue(result.ok)
        self.assertIn("Swarm insights for Startup Operator:", str(result.detail["response_text"]))
        self.assertIn("insight-1", str(result.detail["response_text"]))
        self.assertNotIn("insight-2", str(result.detail["response_text"]))
        self.assertIn("absorb the latest Startup Operator insight", str(result.detail["response_text"]))

    def test_natural_language_swarm_masteries_command_returns_mastery_ids(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        with patch(
            "spark_intelligence.adapters.telegram.runtime.swarm_read_masteries",
            return_value=[
                {
                    "id": "mastery-1",
                    "summary": "Onboarding mastery",
                    "status": "shared_mastery",
                    "updatedAt": "2026-04-08T10:00:00Z",
                }
            ],
        ):
            result = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=2163,
                    user_id="111",
                    username="alice",
                    text="show me swarm masteries",
                ),
            )

        self.assertTrue(result.ok)
        self.assertIn("Swarm masteries:", str(result.detail["response_text"]))
        self.assertIn("mastery-1", str(result.detail["response_text"]))
        self.assertIn("shared_mastery", str(result.detail["response_text"]))
        self.assertIn("Next: `/swarm review mastery-1 approve because <reason>`", str(result.detail["response_text"]))

    def test_scoped_swarm_masteries_command_filters_by_specialization(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        with (
            patch(
                "spark_intelligence.adapters.telegram.runtime.swarm_read_specializations",
                return_value=[
                    {"id": "specialization:startup-operator", "key": "startup-operator", "label": "Startup Operator"},
                ],
            ),
            patch(
                "spark_intelligence.adapters.telegram.runtime.swarm_read_masteries",
                return_value=[
                    {
                        "id": "mastery-1",
                        "specializationScope": "startup-operator",
                        "summary": "Startup mastery",
                        "status": "shared_mastery",
                        "updatedAt": "2026-04-08T10:00:00Z",
                    },
                    {
                        "id": "mastery-2",
                        "specializationScope": "other-lane",
                        "summary": "Other mastery",
                        "status": "provisional_mastery",
                        "updatedAt": "2026-04-08T09:00:00Z",
                    },
                ],
            ),
        ):
            result = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=21631,
                    user_id="111",
                    username="alice",
                    text="/swarm masteries Startup Operator",
                ),
            )

        self.assertTrue(result.ok)
        self.assertIn("Swarm masteries for Startup Operator:", str(result.detail["response_text"]))
        self.assertIn("mastery-1", str(result.detail["response_text"]))
        self.assertNotIn("mastery-2", str(result.detail["response_text"]))
        self.assertIn("approve the latest Startup Operator mastery", str(result.detail["response_text"]))

    def test_scoped_swarm_upgrades_command_filters_by_specialization(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        with (
            patch(
                "spark_intelligence.adapters.telegram.runtime.swarm_read_specializations",
                return_value=[
                    {"id": "specialization:startup-operator", "key": "startup-operator", "label": "Startup Operator"},
                ],
            ),
            patch(
                "spark_intelligence.adapters.telegram.runtime.swarm_read_masteries",
                return_value=[
                    {
                        "id": "mastery-1",
                        "specializationScope": "startup-operator",
                        "summary": "Startup mastery",
                        "status": "shared_mastery",
                        "updatedAt": "2026-04-08T10:00:00Z",
                    },
                    {
                        "id": "mastery-2",
                        "specializationScope": "other-lane",
                        "summary": "Other mastery",
                        "status": "shared_mastery",
                        "updatedAt": "2026-04-08T09:00:00Z",
                    },
                ],
            ),
            patch(
                "spark_intelligence.adapters.telegram.runtime.swarm_read_upgrades",
                return_value=[
                    {
                        "id": "upgrade-1",
                        "derivedFromMasteryId": "mastery-1",
                        "changeSummary": "Startup upgrade",
                        "status": "queued",
                        "riskLevel": "medium",
                        "updatedAt": "2026-04-08T10:00:00Z",
                    },
                    {
                        "id": "upgrade-2",
                        "derivedFromMasteryId": "mastery-2",
                        "changeSummary": "Other upgrade",
                        "status": "queued",
                        "riskLevel": "high",
                        "updatedAt": "2026-04-08T09:00:00Z",
                    },
                ],
            ),
        ):
            result = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=21641,
                    user_id="111",
                    username="alice",
                    text="show me pending Startup Operator upgrades in swarm",
                ),
            )

        self.assertTrue(result.ok)
        self.assertIn("Swarm upgrades for Startup Operator:", str(result.detail["response_text"]))
        self.assertIn("Startup upgrade", str(result.detail["response_text"]))
        self.assertNotIn("Other upgrade", str(result.detail["response_text"]))
        self.assertIn("deliver the latest Startup Operator upgrade", str(result.detail["response_text"]))

    def test_swarm_absorb_command_runs_hosted_action(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        with patch(
            "spark_intelligence.adapters.telegram.runtime.swarm_absorb_insight",
            return_value={
                "insight": {"id": "insight-1", "summary": "Landing page insight"},
                "mastery": {"id": "mastery-1", "status": "provisional_mastery"},
                "review": {"decision": "approve"},
            },
        ) as absorb_mock:
            result = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=224,
                    user_id="111",
                    username="alice",
                    text="/swarm absorb insight-1 because this is repeatable",
                ),
            )

        self.assertTrue(result.ok)
        self.assertIn("Swarm insight absorbed.", str(result.detail["response_text"]))
        self.assertIn("Landing page insight", str(result.detail["response_text"]))
        absorb_mock.assert_called_once()

    def test_natural_language_swarm_absorb_command_resolves_latest_insight_by_specialization(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        with (
            patch(
                "spark_intelligence.adapters.telegram.runtime.swarm_read_specializations",
                return_value=[
                    {"id": "specialization:startup-operator", "key": "startup-operator", "label": "Startup Operator"},
                ],
            ),
            patch(
                "spark_intelligence.adapters.telegram.runtime.swarm_read_insights",
                return_value=[
                    {
                        "id": "insight-old",
                        "specializationId": "specialization:startup-operator",
                        "summary": "Older startup insight",
                        "status": "captured",
                        "updatedAt": "2026-04-08T09:00:00Z",
                    },
                    {
                        "id": "insight-new",
                        "specializationId": "specialization:startup-operator",
                        "summary": "Latest startup insight",
                        "status": "live_supported",
                        "updatedAt": "2026-04-08T10:00:00Z",
                    },
                ],
            ),
            patch(
                "spark_intelligence.adapters.telegram.runtime.swarm_absorb_insight",
                return_value={
                    "insight": {"id": "insight-new", "summary": "Latest startup insight"},
                    "mastery": {"id": "mastery-1", "status": "provisional_mastery"},
                    "review": {"decision": "approve"},
                },
            ) as absorb_mock,
        ):
            result = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=2241,
                    user_id="111",
                    username="alice",
                    text="absorb the latest Startup Operator insight",
                ),
            )

        self.assertTrue(result.ok)
        self.assertIn("Swarm insight absorbed.", str(result.detail["response_text"]))
        self.assertEqual(absorb_mock.call_args.kwargs["insight_id"], "insight-new")
        self.assertIn("Telegram natural-language request", str(absorb_mock.call_args.kwargs["reason"]))

    def test_natural_language_swarm_review_command_runs_hosted_action(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        with patch(
            "spark_intelligence.adapters.telegram.runtime.swarm_review_mastery",
            return_value={
                "mastery": {"id": "mastery-7", "status": "shared_mastery"},
                "review": {"decision": "approve"},
            },
        ) as review_mock:
            result = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=225,
                    user_id="111",
                    username="alice",
                    text="review mastery mastery-7 approve in swarm because it held up under testing",
                ),
            )

        self.assertTrue(result.ok)
        self.assertIn("Swarm mastery review recorded.", str(result.detail["response_text"]))
        self.assertIn("shared_mastery", str(result.detail["response_text"]))
        review_mock.assert_called_once()

    def test_swarm_review_command_requires_reason(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        result = simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=226,
                user_id="111",
                username="alice",
                text="/swarm review mastery-7 approve",
            ),
        )

        self.assertTrue(result.ok)
        self.assertEqual(
            str(result.detail["response_text"]),
            "Usage: `/swarm review <mastery_id> <approve|defer|reject> because <reason>`.",
        )

    def test_swarm_hosted_action_failure_returns_bounded_message(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        with patch(
            "spark_intelligence.adapters.telegram.runtime.swarm_absorb_insight",
            side_effect=RuntimeError("Swarm API request failed with HTTP 404. insight not found"),
        ):
            result = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=2261,
                    user_id="111",
                    username="alice",
                    text="/swarm absorb insight-1",
                ),
            )

        self.assertTrue(result.ok)
        self.assertEqual(
            str(result.detail["response_text"]),
            "Swarm action is unavailable right now.\nSwarm API request failed with HTTP 404. insight not found",
        )

    def test_natural_language_swarm_mode_command_runs_hosted_action(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        with patch(
            "spark_intelligence.adapters.telegram.runtime.swarm_set_evolution_mode",
            return_value={"id": "spec-2", "label": "Startup", "evolutionMode": "checked_auto_merge"},
        ) as mode_mock:
            result = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=227,
                    user_id="111",
                    username="alice",
                    text="set specialization spec-2 to checked auto merge in swarm",
                ),
            )

        self.assertTrue(result.ok)
        self.assertIn("Swarm evolution mode updated.", str(result.detail["response_text"]))
        self.assertIn("checked_auto_merge", str(result.detail["response_text"]))
        mode_mock.assert_called_once()

    def test_natural_language_swarm_mode_command_resolves_specialization_label(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        with (
            patch(
                "spark_intelligence.adapters.telegram.runtime.swarm_read_specializations",
                return_value=[
                    {"id": "specialization:startup-operator", "key": "startup-operator", "label": "Startup Operator"},
                    {"id": "specialization:startup-yc", "key": "startup-yc", "label": "Startup YC"},
                ],
            ),
            patch(
                "spark_intelligence.adapters.telegram.runtime.swarm_set_evolution_mode",
                return_value={"id": "specialization:startup-operator", "label": "Startup Operator", "evolutionMode": "checked_auto_merge"},
            ) as mode_mock,
        ):
            result = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=2271,
                    user_id="111",
                    username="alice",
                    text="set Startup Operator to checked auto merge in swarm",
                ),
            )

        self.assertTrue(result.ok)
        self.assertIn("Swarm evolution mode updated.", str(result.detail["response_text"]))
        self.assertIn("Startup Operator", str(result.detail["response_text"]))
        self.assertEqual(mode_mock.call_args.kwargs["specialization_id"], "specialization:startup-operator")

    def test_natural_language_swarm_review_command_resolves_latest_mastery_by_specialization(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        with (
            patch(
                "spark_intelligence.adapters.telegram.runtime.swarm_read_specializations",
                return_value=[
                    {"id": "specialization:startup-operator", "key": "startup-operator", "label": "Startup Operator"},
                    {"id": "specialization:trading-crypto", "key": "trading-crypto", "label": "Crypto Trading"},
                ],
            ),
            patch(
                "spark_intelligence.adapters.telegram.runtime.swarm_read_masteries",
                return_value=[
                    {
                        "id": "mastery-old",
                        "specializationScope": "startup-operator",
                        "summary": "Older startup mastery",
                        "status": "provisional_mastery",
                        "updatedAt": "2026-04-08T09:00:00Z",
                    },
                    {
                        "id": "mastery-new",
                        "specializationScope": "startup-operator",
                        "summary": "Latest startup mastery",
                        "status": "shared_mastery",
                        "updatedAt": "2026-04-08T10:00:00Z",
                    },
                ],
            ),
            patch(
                "spark_intelligence.adapters.telegram.runtime.swarm_review_mastery",
                return_value={
                    "mastery": {"id": "mastery-new", "status": "shared_mastery"},
                    "review": {"decision": "approve"},
                },
            ) as review_mock,
        ):
            result = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=2272,
                    user_id="111",
                    username="alice",
                    text="approve the latest Startup Operator mastery",
                ),
            )

        self.assertTrue(result.ok)
        self.assertIn("Swarm mastery review recorded.", str(result.detail["response_text"]))
        self.assertEqual(review_mock.call_args.kwargs["mastery_id"], "mastery-new")
        self.assertEqual(review_mock.call_args.kwargs["decision"], "approve")
        self.assertIn("Telegram natural-language request", review_mock.call_args.kwargs["reason"])

    def test_natural_language_swarm_mode_command_rejects_ambiguous_target(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        with patch(
            "spark_intelligence.adapters.telegram.runtime.swarm_read_specializations",
            return_value=[
                {"id": "specialization:startup-operator", "key": "startup-operator", "label": "Startup Operator"},
                {"id": "specialization:startup-yc", "key": "startup-yc", "label": "Startup YC"},
            ],
        ):
            result = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=2273,
                    user_id="111",
                    username="alice",
                    text="set startup to checked auto merge in swarm",
                ),
            )

        self.assertTrue(result.ok)
        self.assertEqual(
            str(result.detail["response_text"]),
            "Swarm action needs a clearer specialization target.\nUse `/swarm specializations` to pick an exact ID.",
        )

    def test_swarm_deliver_command_runs_hosted_action(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        with patch(
            "spark_intelligence.adapters.telegram.runtime.swarm_deliver_upgrade",
            return_value={
                "upgrade": {"id": "upgrade-2", "changeSummary": "Refresh onboarding flow", "status": "awaiting_review"},
                "delivery": {"status": "awaiting_review"},
            },
        ) as deliver_mock:
            result = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=228,
                    user_id="111",
                    username="alice",
                    text="/swarm deliver upgrade-2",
                ),
            )

        self.assertTrue(result.ok)
        self.assertIn("Swarm upgrade delivery recorded.", str(result.detail["response_text"]))
        self.assertIn("Refresh onboarding flow", str(result.detail["response_text"]))
        deliver_mock.assert_called_once()

    def test_natural_language_swarm_deliver_command_resolves_latest_upgrade_by_specialization(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        with (
            patch(
                "spark_intelligence.adapters.telegram.runtime.swarm_read_specializations",
                return_value=[
                    {"id": "specialization:startup-operator", "key": "startup-operator", "label": "Startup Operator"},
                ],
            ),
            patch(
                "spark_intelligence.adapters.telegram.runtime.swarm_read_masteries",
                return_value=[
                    {
                        "id": "mastery-a",
                        "specializationScope": "startup-operator",
                        "summary": "Startup mastery",
                        "status": "shared_mastery",
                        "updatedAt": "2026-04-08T10:00:00Z",
                    },
                ],
            ),
            patch(
                "spark_intelligence.adapters.telegram.runtime.swarm_read_upgrades",
                return_value=[
                    {
                        "id": "upgrade-old",
                        "derivedFromMasteryId": "mastery-a",
                        "changeSummary": "Old upgrade",
                        "status": "queued",
                        "updatedAt": "2026-04-08T09:00:00Z",
                    },
                    {
                        "id": "upgrade-new",
                        "derivedFromMasteryId": "mastery-a",
                        "changeSummary": "Latest upgrade",
                        "status": "awaiting_review",
                        "updatedAt": "2026-04-08T10:00:00Z",
                    },
                ],
            ),
            patch(
                "spark_intelligence.adapters.telegram.runtime.swarm_deliver_upgrade",
                return_value={
                    "upgrade": {"id": "upgrade-new", "changeSummary": "Latest upgrade", "status": "awaiting_review"},
                    "delivery": {"status": "awaiting_review"},
                },
            ) as deliver_mock,
        ):
            result = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=2281,
                    user_id="111",
                    username="alice",
                    text="deliver the latest Startup Operator upgrade",
                ),
            )

        self.assertTrue(result.ok)
        self.assertIn("Swarm upgrade delivery recorded.", str(result.detail["response_text"]))
        self.assertEqual(deliver_mock.call_args.kwargs["upgrade_id"], "upgrade-new")

    def test_natural_language_swarm_deliver_command_rejects_missing_upgrade_target(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        with (
            patch(
                "spark_intelligence.adapters.telegram.runtime.swarm_read_specializations",
                return_value=[
                    {"id": "specialization:startup-operator", "key": "startup-operator", "label": "Startup Operator"},
                ],
            ),
            patch(
                "spark_intelligence.adapters.telegram.runtime.swarm_read_masteries",
                return_value=[],
            ),
            patch(
                "spark_intelligence.adapters.telegram.runtime.swarm_read_upgrades",
                return_value=[],
            ),
        ):
            result = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=2282,
                    user_id="111",
                    username="alice",
                    text="deliver the latest Startup Operator upgrade",
                ),
            )

        self.assertTrue(result.ok)
        self.assertEqual(
            str(result.detail["response_text"]),
            "Swarm action needs a clearer upgrade target.\nNo pending upgrades matched Startup Operator. Use `/swarm upgrades` to pick an exact ID.",
        )

    def test_natural_language_swarm_sync_delivery_command_runs_hosted_action(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        with patch(
            "spark_intelligence.adapters.telegram.runtime.swarm_sync_upgrade_delivery_status",
            return_value={
                "upgrade": {"id": "upgrade-2", "changeSummary": "Refresh onboarding flow", "status": "merged"},
                "delivery": {"status": "merged"},
            },
        ) as sync_mock:
            result = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=229,
                    user_id="111",
                    username="alice",
                    text="sync delivery status for upgrade upgrade-2 in swarm",
                ),
            )

        self.assertTrue(result.ok)
        self.assertIn("Swarm delivery status synced.", str(result.detail["response_text"]))
        self.assertIn("merged", str(result.detail["response_text"]))
        sync_mock.assert_called_once()

    def test_natural_language_swarm_sync_delivery_command_resolves_latest_upgrade_by_specialization(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        with (
            patch(
                "spark_intelligence.adapters.telegram.runtime.swarm_read_specializations",
                return_value=[
                    {"id": "specialization:startup-operator", "key": "startup-operator", "label": "Startup Operator"},
                ],
            ),
            patch(
                "spark_intelligence.adapters.telegram.runtime.swarm_read_masteries",
                return_value=[
                    {
                        "id": "mastery-a",
                        "specializationScope": "startup-operator",
                        "summary": "Startup mastery",
                        "status": "shared_mastery",
                        "updatedAt": "2026-04-08T10:00:00Z",
                    },
                ],
            ),
            patch(
                "spark_intelligence.adapters.telegram.runtime.swarm_read_upgrades",
                return_value=[
                    {
                        "id": "upgrade-old",
                        "derivedFromMasteryId": "mastery-a",
                        "changeSummary": "Old upgrade",
                        "status": "queued",
                        "updatedAt": "2026-04-08T09:00:00Z",
                    },
                    {
                        "id": "upgrade-new",
                        "derivedFromMasteryId": "mastery-a",
                        "changeSummary": "Latest upgrade",
                        "status": "awaiting_review",
                        "updatedAt": "2026-04-08T10:00:00Z",
                    },
                ],
            ),
            patch(
                "spark_intelligence.adapters.telegram.runtime.swarm_sync_upgrade_delivery_status",
                return_value={
                    "upgrade": {"id": "upgrade-new", "changeSummary": "Latest upgrade", "status": "merged"},
                    "delivery": {"status": "merged"},
                },
            ) as sync_mock,
        ):
            result = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=2291,
                    user_id="111",
                    username="alice",
                    text="sync delivery status for the latest Startup Operator upgrade",
                ),
            )

        self.assertTrue(result.ok)
        self.assertIn("Swarm delivery status synced.", str(result.detail["response_text"]))
        self.assertEqual(sync_mock.call_args.kwargs["upgrade_id"], "upgrade-new")

    def test_natural_language_swarm_sync_delivery_command_rejects_missing_upgrade_target(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        with (
            patch(
                "spark_intelligence.adapters.telegram.runtime.swarm_read_specializations",
                return_value=[
                    {"id": "specialization:startup-operator", "key": "startup-operator", "label": "Startup Operator"},
                ],
            ),
            patch(
                "spark_intelligence.adapters.telegram.runtime.swarm_read_masteries",
                return_value=[],
            ),
            patch(
                "spark_intelligence.adapters.telegram.runtime.swarm_read_upgrades",
                return_value=[],
            ),
        ):
            result = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=2292,
                    user_id="111",
                    username="alice",
                    text="sync delivery status for the latest Startup Operator upgrade",
                ),
            )

        self.assertTrue(result.ok)
        self.assertEqual(
            str(result.detail["response_text"]),
            "Swarm action needs a clearer upgrade target.\nNo pending upgrades matched Startup Operator. Use `/swarm upgrades` to pick an exact ID.",
        )

    def test_natural_language_swarm_collective_command_returns_summary(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        with patch(
            "spark_intelligence.adapters.telegram.runtime.swarm_read_collective_snapshot",
            return_value={
                "specializations": [{"id": "spec-1"}],
                "evolutionPaths": [{"id": "path-1"}, {"id": "path-2"}],
                "insights": [{"id": "insight-1"}],
                "masteries": [{"id": "mastery-1"}],
                "contradictions": [{"id": "contr-1", "status": "open"}],
                "upgrades": [{"id": "upg-1", "status": "queued"}],
                "inbox": {"items": [{"id": "inbox-1"}]},
            },
        ):
            result = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=221,
                    user_id="111",
                    username="alice",
                    text="Summarize the collective in swarm",
                ),
            )

        self.assertTrue(result.ok)
        self.assertIn("Swarm collective summary:", str(result.detail["response_text"]))
        self.assertIn("Specializations: 1.", str(result.detail["response_text"]))
        self.assertIn("Pending upgrades: 1.", str(result.detail["response_text"]))

    def test_natural_language_swarm_paths_command_lists_attached_paths(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        with patch(
            "spark_intelligence.adapters.telegram.runtime.swarm_bridge_list_paths",
            return_value={
                "active_path_key": "startup-operator",
                "paths": [
                    {"key": "startup-operator", "label": "Startup Operator", "active": "yes"},
                    {"key": "gtm-distribution", "label": "GTM Distribution", "active": "no"},
                ],
            },
        ):
            result = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=2301,
                    user_id="111",
                    username="alice",
                    text="show me swarm paths",
                ),
            )

        self.assertTrue(result.ok)
        self.assertIn("Swarm paths:", str(result.detail["response_text"]))
        self.assertIn("* startup-operator: Startup Operator", str(result.detail["response_text"]))
        self.assertIn("Next: `/swarm autoloop startup-operator`", str(result.detail["response_text"]))

    def test_swarm_run_command_executes_local_bridge_path(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        with patch(
            "spark_intelligence.adapters.telegram.runtime.swarm_bridge_run_specialization_path",
            return_value=SimpleNamespace(
                ok=True,
                path_key="startup-operator",
                artifacts_path="C:/tmp/run-artifacts",
                payload_path="C:/tmp/payload.json",
            ),
        ) as run_mock:
            result = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=2302,
                    user_id="111",
                    username="alice",
                    text="/swarm run startup-operator",
                ),
            )

        self.assertTrue(result.ok)
        self.assertIn("Swarm specialization-path run completed.", str(result.detail["response_text"]))
        self.assertEqual(run_mock.call_args.kwargs["path_key"], "startup-operator")

    def test_natural_language_swarm_autoloop_command_runs_local_loop(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        with (
            patch(
                "spark_intelligence.adapters.telegram.runtime.swarm_bridge_list_paths",
                return_value={
                    "paths": [
                        {"key": "startup-operator", "label": "Startup Operator", "active": "yes"},
                    ],
                },
            ),
            patch(
                "spark_intelligence.adapters.telegram.runtime.swarm_bridge_autoloop",
                return_value=SimpleNamespace(
                    ok=True,
                    path_key="startup-operator",
                    session_id="session-123",
                    session_summary={
                        "sessionId": "session-123",
                        "completedRounds": 2,
                        "requestedRoundsTotal": 2,
                        "keptRounds": 1,
                        "revertedRounds": 1,
                        "stopReason": "completed_requested_rounds",
                        "plannerStatus": "spark_researcher_planned",
                        "latestPlannerKind": "spark_researcher_candidate",
                    },
                    round_history={
                        "currentScore": 0.78,
                        "bestScore": 0.81,
                        "noGainStreak": 0,
                    },
                    latest_round_summary={
                        "decision": "kept",
                        "baselineScore": 0.75,
                        "candidateScore": 0.78,
                        "benchmarkRunnerType": "script",
                        "benchmarkRunnerLabel": "benchmarks/startup-operator.tool_calls.json",
                        "planner": {
                            "candidateSummary": "Sharpen the startup operator tool script around explicit operator constraints",
                            "hypothesis": "Sharper tool-call constraints will improve benchmark decision quality.",
                        },
                        "mutationTarget": {
                            "path": "benchmarks/startup-operator.tool_calls.json",
                            "rationale": "Keep the mutation focused on the repo-owned Startup Bench tool script.",
                        },
                    },
                    latest_round_summary_path="C:/tmp/round-summary.json",
                ),
            ) as autoloop_mock,
        ):
            result = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=2303,
                    user_id="111",
                    username="alice",
                    text="start autoloop for Startup Operator in swarm for 2 rounds",
                ),
            )

        self.assertTrue(result.ok)
        self.assertIn("Startup Operator autoloop finished.", str(result.detail["response_text"]))
        self.assertIn("Session: session-123.", str(result.detail["response_text"]))
        self.assertIn("It completed 2 of 2 requested rounds and kept 1 candidate(s) while reverting 1.", str(result.detail["response_text"]))
        self.assertIn("Stop: requested rounds completed.", str(result.detail["response_text"]))
        self.assertIn("Benchmark runner: script via benchmarks/startup-operator.tool_calls.json.", str(result.detail["response_text"]))
        self.assertIn("Mutation target: benchmarks/startup-operator.tool_calls.json.", str(result.detail["response_text"]))
        self.assertIn("Round candidate: Sharpen the startup operator tool script around explicit operator constraints.", str(result.detail["response_text"]))
        self.assertIn("Hypothesis: Sharper tool-call constraints will improve benchmark decision quality.", str(result.detail["response_text"]))
        self.assertIn("Round delta: +0.0300 (0.7500 -> 0.7800).", str(result.detail["response_text"]))
        self.assertEqual(autoloop_mock.call_args.kwargs["path_key"], "startup-operator")
        self.assertEqual(autoloop_mock.call_args.kwargs["rounds"], 2)

    def test_natural_language_swarm_continue_uses_latest_session_when_unspecified(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        with (
            patch(
                "spark_intelligence.adapters.telegram.runtime.swarm_bridge_list_paths",
                return_value={
                    "paths": [
                        {"key": "startup-operator", "label": "Startup Operator", "active": "yes"},
                    ],
                },
            ),
            patch(
                "spark_intelligence.adapters.telegram.runtime.swarm_bridge_read_autoloop_session",
                return_value={
                    "session_id": "session-latest",
                    "session_summary": {"sessionId": "session-latest"},
                },
            ),
            patch(
                "spark_intelligence.adapters.telegram.runtime.swarm_bridge_autoloop",
                return_value=SimpleNamespace(
                    ok=True,
                    path_key="startup-operator",
                    session_id="session-latest",
                    session_summary={
                        "sessionId": "session-latest",
                        "completedRounds": 3,
                        "requestedRoundsTotal": 4,
                        "keptRounds": 2,
                        "revertedRounds": 1,
                        "stopReason": "completed_requested_rounds",
                    },
                    round_history={
                        "currentScore": 0.82,
                        "bestScore": 0.85,
                        "noGainStreak": 1,
                    },
                ),
            ) as autoloop_mock,
        ):
            result = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=2304,
                    user_id="111",
                    username="alice",
                    text="continue the Startup Operator autoloop in swarm for 1 more round",
                ),
            )

        self.assertTrue(result.ok)
        self.assertIn("Session: session-latest.", str(result.detail["response_text"]))
        self.assertEqual(autoloop_mock.call_args.kwargs["session_id"], "session-latest")

    def test_natural_language_swarm_continue_force_sets_force_flag(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        with (
            patch(
                "spark_intelligence.adapters.telegram.runtime.swarm_bridge_list_paths",
                return_value={
                    "paths": [
                        {"key": "startup-operator", "label": "Startup Operator", "active": "yes"},
                    ],
                },
            ),
            patch(
                "spark_intelligence.adapters.telegram.runtime.swarm_bridge_read_autoloop_session",
                return_value={
                    "session_id": "session-force",
                    "session_summary": {"sessionId": "session-force"},
                },
            ),
            patch(
                "spark_intelligence.adapters.telegram.runtime.swarm_bridge_autoloop",
                return_value=SimpleNamespace(
                    ok=True,
                    path_key="startup-operator",
                    session_id="session-force",
                    session_summary={
                        "sessionId": "session-force",
                        "completedRounds": 1,
                        "requestedRoundsTotal": 1,
                        "keptRounds": 0,
                        "revertedRounds": 1,
                        "stopReason": "completed_requested_rounds",
                    },
                    round_history={
                        "currentScore": 0.62,
                        "bestScore": 0.62,
                        "noGainStreak": 0,
                    },
                ),
            ) as autoloop_mock,
        ):
            result = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=23041,
                    user_id="111",
                    username="alice",
                    text="continue the Startup Operator autoloop in swarm for 1 more round anyway",
                ),
            )

        self.assertTrue(result.ok)
        self.assertEqual(autoloop_mock.call_args.kwargs["session_id"], "session-force")
        self.assertTrue(autoloop_mock.call_args.kwargs["force"])

    def test_natural_language_swarm_session_command_returns_latest_session_summary(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        with (
            patch(
                "spark_intelligence.adapters.telegram.runtime.swarm_bridge_list_paths",
                return_value={
                    "paths": [
                        {"key": "startup-operator", "label": "Startup Operator", "active": "yes"},
                    ],
                },
            ),
            patch(
                "spark_intelligence.adapters.telegram.runtime.swarm_bridge_read_autoloop_session",
                return_value={
                    "path_key": "startup-operator",
                    "session_summary": {
                        "sessionId": "session-777",
                        "completedRounds": 3,
                        "requestedRoundsTotal": 5,
                        "keptRounds": 2,
                        "revertedRounds": 1,
                        "stopReason": "paused_no_gain_streak",
                        "plannerStatus": "mixed",
                        "plannerReadinessStatus": "ready",
                        "rounds": [
                            {
                                "ordinal": 3,
                                "decision": "reverted",
                                "targetPath": "benchmarks/startup-operator.tool_calls.json",
                                "plannerKind": "spark_researcher_candidate",
                                "candidateSummary": "Tighten startup operator tool-call sequencing",
                            }
                        ],
                    },
                    "latest_round_summary": {
                        "decision": "reverted",
                        "baselineScore": 0.81,
                        "candidateScore": 0.73,
                        "benchmarkRunnerType": "script",
                        "benchmarkRunnerLabel": "benchmarks/startup-operator.tool_calls.json",
                        "planner": {
                            "candidateSummary": "Tighten startup operator tool-call sequencing",
                            "hypothesis": "Tighter tool-call sequencing may reduce benchmark drift.",
                        },
                        "mutationTarget": {
                            "path": "benchmarks/startup-operator.tool_calls.json",
                            "rationale": "Stay inside the repo-owned Startup Bench tool script target.",
                        },
                    },
                    "latest_round_summary_path": "C:/tmp/session-round-summary.json",
                    "round_history": {
                        "currentScore": 0.73,
                        "bestScore": 0.81,
                        "noGainStreak": 2,
                    },
                },
            ),
        ):
            result = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=2305,
                    user_id="111",
                    username="alice",
                    text="show me the latest Startup Operator autoloop session",
                ),
            )

        self.assertTrue(result.ok)
        self.assertIn("Latest Startup Operator autoloop session is paused on the no-gain guard.", str(result.detail["response_text"]))
        self.assertIn("Session: session-777.", str(result.detail["response_text"]))
        self.assertIn("It has completed 3 of 5 requested rounds, with 2 kept and 1 reverted.", str(result.detail["response_text"]))
        self.assertIn("Latest round: #3 reverted target=benchmarks/startup-operator.tool_calls.json.", str(result.detail["response_text"]))
        self.assertIn("Benchmark runner: script via benchmarks/startup-operator.tool_calls.json.", str(result.detail["response_text"]))
        self.assertIn("Mutation target: benchmarks/startup-operator.tool_calls.json.", str(result.detail["response_text"]))
        self.assertIn("Hypothesis: Tighter tool-call sequencing may reduce benchmark drift.", str(result.detail["response_text"]))
        self.assertIn("Round delta: -0.0800 (0.8100 -> 0.7300).", str(result.detail["response_text"]))
        self.assertIn("Interpretation: this mutation did not beat the current benchmarked baseline, so the repo stayed unchanged.", str(result.detail["response_text"]))
        self.assertIn("The autoloop is paused on the no-gain guard.", str(result.detail["response_text"]))
        self.assertIn("/swarm continue startup-operator session session-777 rounds 1 force", str(result.detail["response_text"]))

    def test_swarm_autoloop_pause_failure_returns_bounded_guidance(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        with (
            patch(
                "spark_intelligence.adapters.telegram.runtime.swarm_bridge_list_paths",
                return_value={
                    "paths": [
                        {"key": "startup-operator", "label": "Startup Operator", "active": "yes"},
                    ],
                },
            ),
            patch(
                "spark_intelligence.adapters.telegram.runtime.swarm_bridge_autoloop",
                return_value=SimpleNamespace(
                    ok=False,
                    exit_code=1,
                    path_key="startup-operator",
                    session_id="session-pause",
                    stdout=(
                        "Spark Swarm specialization path autoloop\n"
                        "Autoloop paused: auto-generated planner hit a no-gain streak of 2 rounds. Blocked before round 1.\n"
                        "Session stop: paused_no_gain_streak\n"
                    ),
                    stderr="",
                    session_summary={
                        "sessionId": "session-pause",
                        "completedRounds": 0,
                        "requestedRoundsTotal": 1,
                        "keptRounds": 0,
                        "revertedRounds": 0,
                        "plannerReadinessStatus": "available",
                        "noGainStreak": 2,
                    },
                    round_history={
                        "currentScore": 0.6222,
                        "bestScore": 0.6222,
                        "noGainStreak": 2,
                    },
                ),
            ),
        ):
            result = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=23042,
                    user_id="111",
                    username="alice",
                    text="start autoloop for Startup Operator in swarm for 1 round",
                ),
            )

        self.assertTrue(result.ok)
        self.assertIn("Startup Operator autoloop is paused.", str(result.detail["response_text"]))
        self.assertIn("It hit the no-gain guard before another round, so nothing changed yet.", str(result.detail["response_text"]))
        self.assertIn("/swarm continue startup-operator session session-pause rounds 1 force", str(result.detail["response_text"]))

    def test_natural_language_swarm_rerun_command_executes_requested_path(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        with (
            patch(
                "spark_intelligence.adapters.telegram.runtime.swarm_bridge_list_paths",
                return_value={
                    "paths": [
                        {"key": "startup-operator", "label": "Startup Operator", "active": "yes"},
                    ],
                },
            ),
            patch(
                "spark_intelligence.adapters.telegram.runtime.swarm_bridge_execute_rerun_request",
                return_value=SimpleNamespace(
                    ok=True,
                    path_key="startup-operator",
                    artifacts_path="C:/tmp/rerun-artifacts",
                    payload_path="C:/tmp/rerun-payload.json",
                ),
            ) as rerun_mock,
        ):
            result = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=2306,
                    user_id="111",
                    username="alice",
                    text="execute the latest Startup Operator rerun request in swarm",
                ),
            )

        self.assertTrue(result.ok)
        self.assertIn("Swarm rerun request executed.", str(result.detail["response_text"]))
        self.assertEqual(rerun_mock.call_args.kwargs["path_key"], "startup-operator")

    def test_swarm_read_failure_returns_bounded_message(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        with patch(
            "spark_intelligence.adapters.telegram.runtime.swarm_read_runtime_pulse",
            side_effect=RuntimeError("Swarm API URL is missing."),
        ):
            result = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=222,
                    user_id="111",
                    username="alice",
                    text="/swarm runtime",
                ),
            )

        self.assertTrue(result.ok)
        self.assertIn("Swarm read is unavailable right now.", str(result.detail["response_text"]))
        self.assertIn("Swarm API URL is missing.", str(result.detail["response_text"]))

    def test_swarm_issues_404_returns_host_gap_message(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        with patch(
            "spark_intelligence.adapters.telegram.runtime.swarm_read_operator_issues",
            side_effect=RuntimeError("Swarm API request failed with HTTP 404."),
        ):
            result = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=223,
                    user_id="111",
                    username="alice",
                    text="Show me operator issues in swarm",
                ),
            )

        self.assertTrue(result.ok)
        self.assertEqual(
            str(result.detail["response_text"]),
            "Swarm operator issues are unavailable right now.\n"
            "The current Spark Swarm host does not expose the operator issues route yet.",
        )

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
