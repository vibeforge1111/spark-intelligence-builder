import base64
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from spark_intelligence.adapters.telegram.runtime import (
    _prepare_voice_reply_text,
    poll_telegram_updates_once,
    simulate_telegram_update,
)
from spark_intelligence.identity.service import (
    approve_pairing,
    consume_pairing_welcome,
    pairing_summary,
    read_canonical_agent_state,
    record_pairing_context,
    rename_agent_identity,
    review_pairings,
)
from spark_intelligence.observability.store import recent_resume_richness_guard_records
from spark_intelligence.personality.loader import (
    load_agent_persona_profile,
    load_personality_profile,
    maybe_handle_agent_persona_onboarding_turn,
    save_agent_persona_profile,
)
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
        self.assertIn("Access is not authorized for this channel", str(result.detail["response_text"]))

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
        # Finding G fix: agent starts with empty name; onboarding will prompt
        # for the agent's name. Welcome no longer leaks the human's username.
        self.assertIn("Let's set up your agent.", str(follow_up.detail["response_text"]))
        self.assertNotIn("alice is live", str(follow_up.detail["response_text"]))

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
                    text="Boss",
                ),
            )
            fourth_turn = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=107,
                    user_id="111",
                    username="alice",
                    text="freestyle",
                ),
            )
            # P2-9: after picking "freestyle", the state should be the renamed
            # awaiting_persona_freestyle step (previously named awaiting_persona).
            with self.state_db.connect() as conn:
                freestyle_blob_row = conn.execute(
                    "SELECT value FROM runtime_state WHERE state_key = ?",
                    ("agent_onboarding:human:telegram:111",),
                ).fetchone()
            freestyle_blob = json.loads(str(freestyle_blob_row["value"]))
            self.assertEqual(freestyle_blob["step"], "awaiting_persona_freestyle")
            fifth_turn = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=108,
                    user_id="111",
                    username="alice",
                    text="calm, strategic, very direct, low-fluff",
                ),
            )
            # P2-10: after the persona freestyle description, the onboarding
            # state must pause at awaiting_guardrails_ack and surface the
            # commitments card. Any non-`change` reply is treated as
            # acceptance per Q-C of the v2 design.
            with self.state_db.connect() as conn:
                guardrails_blob_row = conn.execute(
                    "SELECT value FROM runtime_state WHERE state_key = ?",
                    ("agent_onboarding:human:telegram:111",),
                ).fetchone()
            guardrails_blob = json.loads(str(guardrails_blob_row["value"]))
            self.assertEqual(guardrails_blob["step"], "awaiting_guardrails_ack")
            sixth_turn = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=109,
                    user_id="111",
                    username="alice",
                    text="ok",
                ),
            )

        self.assertTrue(first_turn.ok)
        self.assertIn("Pairing approved.", str(first_turn.detail["response_text"]))
        # Finding G fix: welcome no longer embeds the human's username as the
        # agent identity. Onboarding flow still asks for the agent name next.
        self.assertIn("Let's set up your agent.", str(first_turn.detail["response_text"]))
        self.assertNotIn("alice is live", str(first_turn.detail["response_text"]))
        self.assertIn("What should I call your agent?", str(first_turn.detail["response_text"]))

        self.assertTrue(second_turn.ok)
        self.assertIn("Your agent is now `Atlas`", str(second_turn.detail["response_text"]))
        self.assertIn("How should your agent address you?", str(second_turn.detail["response_text"]))

        self.assertTrue(third_turn.ok)
        self.assertIn("Boss", str(third_turn.detail["response_text"]))
        self.assertIn("How should we shape your personality?", str(third_turn.detail["response_text"]))
        self.assertIn("Guided", str(third_turn.detail["response_text"]))
        self.assertIn("Express", str(third_turn.detail["response_text"]))
        self.assertIn("Freestyle", str(third_turn.detail["response_text"]))

        self.assertTrue(fourth_turn.ok)
        self.assertIn("describe the personality", str(fourth_turn.detail["response_text"]))

        # P2-10: persona description now returns the guardrails card and the
        # final recap moves to the follow-up ack turn.
        self.assertTrue(fifth_turn.ok)
        fifth_text = str(fifth_turn.detail["response_text"])
        self.assertIn("commits to", fifth_text)
        self.assertIn("No glazing", fifth_text)
        self.assertIn("Reply `ok`", fifth_text)

        self.assertTrue(sixth_turn.ok)
        sixth_text = str(sixth_turn.detail["response_text"])
        self.assertIn("Locked in.", sixth_text)
        self.assertIn("Here's the recap", sixth_text)
        self.assertIn("Atlas", sixth_text)
        self.assertIn("Boss", sixth_text)
        self.assertIn(
            "anti-glazing, better-way surfacing, honest failure reporting",
            sixth_text,
        )
        self.assertIn("connect your Spark Swarm agent", sixth_text)

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
        with self.state_db.connect() as conn:
            row = conn.execute(
                "SELECT user_address FROM humans WHERE human_id = ?",
                ("human:telegram:111",),
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["user_address"], "Boss")

    def test_onboarding_user_address_skip_clears_column_and_uses_neutral_ack(self) -> None:
        self.add_telegram_channel()
        simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=203,
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
            simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=204,
                    user_id="111",
                    username="alice",
                    text="hey",
                ),
            )
            simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=205,
                    user_id="111",
                    username="alice",
                    text="Nova",
                ),
            )
            skip_turn = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=206,
                    user_id="111",
                    username="alice",
                    text="skip",
                ),
            )

        self.assertTrue(skip_turn.ok)
        reply = str(skip_turn.detail["response_text"])
        self.assertIn("no salutation", reply.lower())
        self.assertIn("How should we shape your personality?", reply)
        self.assertNotIn("Operator", reply)
        self.assertNotIn("there,", reply)

        with self.state_db.connect() as conn:
            row = conn.execute(
                "SELECT user_address FROM humans WHERE human_id = ?",
                ("human:telegram:111",),
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertIsNone(row["user_address"])

    def test_onboarding_persona_mode_reprompts_on_invalid_and_tags_choice(self) -> None:
        self.add_telegram_channel()
        simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=303,
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
            simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=304, user_id="111", username="alice", text="hey"
                ),
            )
            simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=305, user_id="111", username="alice", text="Nova"
                ),
            )
            simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=306, user_id="111", username="alice", text="skip"
                ),
            )
            invalid_turn = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=307,
                    user_id="111",
                    username="alice",
                    text="maybe something in between",
                ),
            )
            mode_turn = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=308, user_id="111", username="alice", text="express"
                ),
            )

        self.assertTrue(invalid_turn.ok)
        invalid_reply = str(invalid_turn.detail["response_text"])
        self.assertIn("I didn't catch that", invalid_reply)
        self.assertIn("guided", invalid_reply)
        self.assertIn("express", invalid_reply)
        self.assertIn("freestyle", invalid_reply)

        with self.state_db.connect() as conn:
            blob_row = conn.execute(
                "SELECT value FROM runtime_state WHERE state_key = ?",
                ("agent_onboarding:human:telegram:111",),
            ).fetchone()
        self.assertIsNotNone(blob_row)
        blob = json.loads(str(blob_row["value"]))
        self.assertEqual(blob["persona_mode"], "express")
        # P2-8: picking "express" now transitions to awaiting_persona_express
        # and replies with the preset catalog instead of falling through to
        # the freestyle describe-your-personality prompt.
        self.assertEqual(blob["step"], "awaiting_persona_express")
        self.assertTrue(mode_turn.ok)
        self.assertIn("Pick a preset", str(mode_turn.detail["response_text"]))

    def test_onboarding_guided_mode_walks_five_questions_and_saves_profile(self) -> None:
        self.add_telegram_channel()
        simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=400,
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
            simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=401, user_id="111", username="alice", text="hey"
                ),
            )
            simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=402, user_id="111", username="alice", text="Nova"
                ),
            )
            simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=403, user_id="111", username="alice", text="Boss"
                ),
            )
            first_question_turn = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=404, user_id="111", username="alice", text="guided"
                ),
            )
            q2_turn = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=405, user_id="111", username="alice", text="4"
                ),
            )
            q3_turn = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=406, user_id="111", username="alice", text="5"
                ),
            )
            q4_turn = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=407, user_id="111", username="alice", text="two"
                ),
            )
            q5_turn = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=408, user_id="111", username="alice", text="3"
                ),
            )
            completion_turn = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=409, user_id="111", username="alice", text="5"
                ),
            )
            # P2-10: guided persona now lands on the guardrails_ack card
            # rather than jumping straight to completed.
            with self.state_db.connect() as conn:
                guided_ack_blob_row = conn.execute(
                    "SELECT value FROM runtime_state WHERE state_key = ?",
                    ("agent_onboarding:human:telegram:111",),
                ).fetchone()
            guided_ack_blob = json.loads(str(guided_ack_blob_row["value"]))
            self.assertEqual(guided_ack_blob["step"], "awaiting_guardrails_ack")
            ack_turn = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=410, user_id="111", username="alice", text="ok"
                ),
            )

        # Mode picker should have returned the first guided question.
        self.assertTrue(first_question_turn.ok)
        first_question_text = str(first_question_turn.detail["response_text"])
        self.assertIn("Question 1 of 5", first_question_text)
        self.assertIn("How warm", first_question_text)
        self.assertIn("reserved and formal", first_question_text)
        self.assertIn("very warm", first_question_text)

        # Intermediate questions should advance trait-by-trait.
        self.assertIn("Question 2 of 5", str(q2_turn.detail["response_text"]))
        self.assertIn("How direct", str(q2_turn.detail["response_text"]))
        self.assertIn("Question 3 of 5", str(q3_turn.detail["response_text"]))
        self.assertIn("How playful", str(q3_turn.detail["response_text"]))
        self.assertIn("Question 4 of 5", str(q4_turn.detail["response_text"]))
        self.assertIn("pace", str(q4_turn.detail["response_text"]).lower())
        self.assertIn("Question 5 of 5", str(q5_turn.detail["response_text"]))
        self.assertIn("How assertive", str(q5_turn.detail["response_text"]))

        # P2-10: the last guided rating now surfaces the guardrails card, and
        # the final recap lives on the follow-up ack turn.
        self.assertTrue(completion_turn.ok)
        completion_text = str(completion_turn.detail["response_text"])
        self.assertIn("commits to", completion_text)
        self.assertIn("No glazing", completion_text)

        self.assertTrue(ack_turn.ok)
        ack_text = str(ack_turn.detail["response_text"])
        self.assertIn("Locked in", ack_text)
        self.assertIn("Nova", ack_text)
        self.assertIn("Here's the recap", ack_text)

        # The onboarding blob should be marked completed after the ack turn.
        with self.state_db.connect() as conn:
            blob_row = conn.execute(
                "SELECT value FROM runtime_state WHERE state_key = ?",
                ("agent_onboarding:human:telegram:111",),
            ).fetchone()
        self.assertIsNotNone(blob_row)
        blob = json.loads(str(blob_row["value"]))
        self.assertEqual(blob["step"], "completed")
        self.assertEqual(blob["status"], "completed")

        # The persona profile should reflect the ratings: warmth=4(0.70),
        # directness=5(0.90), playfulness=2(0.30), pacing=3(0.50),
        # assertiveness=5(0.90).
        with self.state_db.connect() as conn:
            row = conn.execute(
                "SELECT base_traits_json, provenance_json FROM agent_persona_profiles ORDER BY updated_at DESC LIMIT 1"
            ).fetchone()
        self.assertIsNotNone(row)
        base_traits = json.loads(str(row["base_traits_json"]))
        self.assertAlmostEqual(base_traits["warmth"], 0.70, places=2)
        self.assertAlmostEqual(base_traits["directness"], 0.90, places=2)
        self.assertAlmostEqual(base_traits["playfulness"], 0.30, places=2)
        self.assertAlmostEqual(base_traits["pacing"], 0.50, places=2)
        self.assertAlmostEqual(base_traits["assertiveness"], 0.90, places=2)
        provenance = json.loads(str(row["provenance_json"]))
        self.assertEqual(provenance.get("persona_mode"), "guided")
        self.assertEqual(
            provenance.get("guided_ratings"),
            {
                "warmth": 4,
                "directness": 5,
                "playfulness": 2,
                "pacing": 3,
                "assertiveness": 5,
            },
        )

    def test_onboarding_guided_reprompts_on_invalid_rating_without_advancing(self) -> None:
        self.add_telegram_channel()
        simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=500,
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
            for update_id, text in (
                (501, "hey"),
                (502, "Nova"),
                (503, "skip"),
                (504, "guided"),
            ):
                simulate_telegram_update(
                    config_manager=self.config_manager,
                    state_db=self.state_db,
                    update_payload=make_telegram_update(
                        update_id=update_id, user_id="111", username="alice", text=text
                    ),
                )
            invalid_turn = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=505, user_id="111", username="alice", text="maybe 7"
                ),
            )

        self.assertTrue(invalid_turn.ok)
        reply = str(invalid_turn.detail["response_text"])
        self.assertIn("number from 1 to 5", reply)
        self.assertIn("Question 1 of 5", reply)

        # The stored index should still be 0 and ratings still empty.
        with self.state_db.connect() as conn:
            blob_row = conn.execute(
                "SELECT value FROM runtime_state WHERE state_key = ?",
                ("agent_onboarding:human:telegram:111",),
            ).fetchone()
        self.assertIsNotNone(blob_row)
        blob = json.loads(str(blob_row["value"]))
        self.assertEqual(blob["step"], "awaiting_persona_guided")
        self.assertEqual(blob.get("persona_guided_trait_index"), 0)
        self.assertEqual(blob.get("persona_guided_ratings") or {}, {})

    def test_onboarding_express_mode_saves_preset_traits_and_completes(self) -> None:
        self.add_telegram_channel()
        simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=600,
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
            simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=601, user_id="111", username="alice", text="hey"
                ),
            )
            simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=602, user_id="111", username="alice", text="Nova"
                ),
            )
            simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=603, user_id="111", username="alice", text="Boss"
                ),
            )
            catalog_turn = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=604, user_id="111", username="alice", text="express"
                ),
            )
            completion_turn = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=605, user_id="111", username="alice", text="warm"
                ),
            )
            # P2-10: the express preset pick now transitions to the
            # guardrails_ack card and the recap moves to the follow-up turn.
            with self.state_db.connect() as conn:
                express_ack_blob_row = conn.execute(
                    "SELECT value FROM runtime_state WHERE state_key = ?",
                    ("agent_onboarding:human:telegram:111",),
                ).fetchone()
            express_ack_blob = json.loads(str(express_ack_blob_row["value"]))
            self.assertEqual(express_ack_blob["step"], "awaiting_guardrails_ack")
            self.assertEqual(express_ack_blob.get("express_preset_label"), "Warm")
            ack_turn = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=606, user_id="111", username="alice", text="ok"
                ),
            )

        # Picking "express" should return the preset catalog.
        self.assertTrue(catalog_turn.ok)
        catalog_text = str(catalog_turn.detail["response_text"])
        self.assertIn("Pick a preset", catalog_text)
        self.assertIn("operator", catalog_text)
        self.assertIn("claude-like", catalog_text)
        self.assertIn("concise", catalog_text)
        self.assertIn("warm", catalog_text)

        # P2-10: selecting "warm" now surfaces the guardrails card rather
        # than jumping straight to the completion recap.
        self.assertTrue(completion_turn.ok)
        completion_text = str(completion_turn.detail["response_text"])
        self.assertIn("commits to", completion_text)
        self.assertIn("No glazing", completion_text)
        self.assertIn("Reply `ok`", completion_text)

        # The ack turn produces the final "Locked in" recap.
        self.assertTrue(ack_turn.ok)
        ack_text = str(ack_turn.detail["response_text"])
        self.assertIn("Locked in", ack_text)
        self.assertIn("Nova", ack_text)
        self.assertIn("Here's the recap", ack_text)

        # The onboarding blob should be marked completed after the ack turn.
        with self.state_db.connect() as conn:
            blob_row = conn.execute(
                "SELECT value FROM runtime_state WHERE state_key = ?",
                ("agent_onboarding:human:telegram:111",),
            ).fetchone()
        self.assertIsNotNone(blob_row)
        blob = json.loads(str(blob_row["value"]))
        self.assertEqual(blob["step"], "completed")
        self.assertEqual(blob["status"], "completed")

        # The persona profile should reflect the "warm" preset trait vector
        # and provenance should record persona_mode=express and the chosen key.
        with self.state_db.connect() as conn:
            row = conn.execute(
                "SELECT base_traits_json, provenance_json "
                "FROM agent_persona_profiles ORDER BY updated_at DESC LIMIT 1"
            ).fetchone()
        self.assertIsNotNone(row)
        base_traits = json.loads(str(row["base_traits_json"]))
        self.assertAlmostEqual(base_traits["warmth"], 0.85, places=2)
        self.assertAlmostEqual(base_traits["directness"], 0.50, places=2)
        self.assertAlmostEqual(base_traits["playfulness"], 0.60, places=2)
        self.assertAlmostEqual(base_traits["pacing"], 0.40, places=2)
        self.assertAlmostEqual(base_traits["assertiveness"], 0.45, places=2)
        provenance = json.loads(str(row["provenance_json"]))
        self.assertEqual(provenance.get("persona_mode"), "express")
        self.assertEqual(provenance.get("express_preset"), "warm")

    def test_onboarding_express_reprompts_on_unknown_preset_then_recovers(self) -> None:
        self.add_telegram_channel()
        simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=700,
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
            for update_id, text in (
                (701, "hey"),
                (702, "Nova"),
                (703, "skip"),
                (704, "express"),
            ):
                simulate_telegram_update(
                    config_manager=self.config_manager,
                    state_db=self.state_db,
                    update_payload=make_telegram_update(
                        update_id=update_id, user_id="111", username="alice", text=text
                    ),
                )
            unknown_turn = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=705, user_id="111", username="alice", text="pancakes"
                ),
            )

            # Unknown reply should re-prompt with the catalog and leave the
            # state pinned at awaiting_persona_express.
            self.assertTrue(unknown_turn.ok)
            reply = str(unknown_turn.detail["response_text"])
            self.assertIn("didn't catch that preset", reply)
            self.assertIn("Pick a preset", reply)
            with self.state_db.connect() as conn:
                blob_row = conn.execute(
                    "SELECT value FROM runtime_state WHERE state_key = ?",
                    ("agent_onboarding:human:telegram:111",),
                ).fetchone()
            blob = json.loads(str(blob_row["value"]))
            self.assertEqual(blob["step"], "awaiting_persona_express")

            recovery_turn = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=706, user_id="111", username="alice", text="2"
                ),
            )
            # P2-10: recovery via a valid preset now surfaces the guardrails
            # card instead of completing directly. Land the recap via an ack
            # turn before exiting the patched researcher-bridge block.
            ack_turn = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=707, user_id="111", username="alice", text="ok"
                ),
            )

        # Recovery via the numeric choice "2" should pick the second preset
        # (claude-like) and queue the guardrails card.
        self.assertTrue(recovery_turn.ok)
        recovery_text = str(recovery_turn.detail["response_text"])
        self.assertIn("commits to", recovery_text)
        self.assertIn("No glazing", recovery_text)
        # The ack turn then produces the "Locked in" recap.
        self.assertTrue(ack_turn.ok)
        self.assertIn("Locked in", str(ack_turn.detail["response_text"]))
        with self.state_db.connect() as conn:
            row = conn.execute(
                "SELECT provenance_json FROM agent_persona_profiles "
                "ORDER BY updated_at DESC LIMIT 1"
            ).fetchone()
        provenance = json.loads(str(row["provenance_json"]))
        self.assertEqual(provenance.get("express_preset"), "claude-like")

    def test_onboarding_cancel_wipes_state_and_resets_agent_name_at_any_step(self) -> None:
        """P2-11: `/cancel` is honored at every onboarding step.

        Q-E of docs/PERSONALITY_ONBOARDING_V2_DESIGN_2026-04-10.md \u00a711:
        `/cancel` wipes BOTH the in-progress onboarding state AND the
        saved agent name (back to the empty-string sentinel). The persona
        profile is left untouched per Q-J. The pairing row stays active,
        so the user can restart onboarding by saying `hi` again.
        """
        self.add_telegram_channel()
        simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=900,
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
            # Walk into awaiting_persona_mode so /cancel fires mid-flow,
            # not just at the very first prompt.
            welcome_turn = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=901, user_id="111", username="alice", text="hey"
                ),
            )
            simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=902, user_id="111", username="alice", text="Atlas"
                ),
            )
            simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=903, user_id="111", username="alice", text="Boss"
                ),
            )

            # Confirm we are pinned at awaiting_persona_mode before /cancel.
            with self.state_db.connect() as conn:
                pre_blob_row = conn.execute(
                    "SELECT value FROM runtime_state WHERE state_key = ?",
                    ("agent_onboarding:human:telegram:111",),
                ).fetchone()
            pre_blob = json.loads(str(pre_blob_row["value"]))
            self.assertEqual(pre_blob["step"], "awaiting_persona_mode")

            cancel_turn = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=904, user_id="111", username="alice", text="/cancel"
                ),
            )

        # P2-11: welcome card should advertise the /cancel escape hatch.
        self.assertTrue(welcome_turn.ok)
        welcome_text = str(welcome_turn.detail["response_text"])
        self.assertIn("/cancel", welcome_text)

        # The cancel reply itself must confirm the wipe.
        self.assertTrue(cancel_turn.ok)
        cancel_text = str(cancel_turn.detail["response_text"])
        self.assertIn("Onboarding cancelled", cancel_text)
        self.assertIn("cleared the agent name", cancel_text)

        # The in-progress onboarding state blob should be gone entirely.
        with self.state_db.connect() as conn:
            blob_row = conn.execute(
                "SELECT value FROM runtime_state WHERE state_key = ?",
                ("agent_onboarding:human:telegram:111",),
            ).fetchone()
        self.assertIsNone(blob_row)

        # The saved agent name should be back to the empty-string sentinel.
        agent_state = read_canonical_agent_state(
            state_db=self.state_db,
            human_id="human:telegram:111",
        )
        self.assertEqual(agent_state.agent_name, "")
        self.assertFalse(agent_state.has_user_defined_name)

        # A rename-history row should record the cancel with the
        # onboarding_cancel source surface.
        with self.state_db.connect() as conn:
            history_rows = conn.execute(
                "SELECT old_name, new_name, source_surface FROM agent_rename_history "
                "WHERE human_id = ? ORDER BY created_at DESC",
                ("human:telegram:111",),
            ).fetchall()
        self.assertTrue(
            any(
                row["new_name"] == "" and row["source_surface"] == "onboarding_cancel"
                for row in history_rows
            ),
            f"expected onboarding_cancel rename history row, got: {list(history_rows)}",
        )

    def test_onboarding_reonboard_consent_yes_restarts_setup_for_existing_user(self) -> None:
        """P2-12: existing users with a saved persona see a one-tap skip offer.

        Q-H of docs/PERSONALITY_ONBOARDING_V2_DESIGN_2026-04-10.md \u00a711:
        existing users with a saved persona get a one-tap skip offer
        instead of being silently bypassed. Replying `yes` transitions the
        state machine back to `awaiting_name` so the user can re-run the
        short v2 setup conversation.
        """
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
            source_ref="seed-name",
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

        # First turn: no in-progress onboarding state but existing persona
        # plus start_if_eligible=True should return the reonboard-consent
        # offer card and create the new awaiting_reonboard_consent state.
        offer_turn = maybe_handle_agent_persona_onboarding_turn(
            human_id="human:telegram:111",
            agent_id="agent:human:telegram:111",
            user_message="hey",
            state_db=self.state_db,
            source_surface="telegram",
            source_ref="test-reonboard-offer",
            start_if_eligible=True,
        )
        self.assertIsNotNone(offer_turn)
        assert offer_turn is not None  # for type-checker
        self.assertEqual(offer_turn.step, "awaiting_reonboard_consent")
        self.assertFalse(offer_turn.completed)
        self.assertIn("re-run setup", offer_turn.reply_text)
        self.assertIn("Atlas", offer_turn.reply_text)
        self.assertIn("`yes`", offer_turn.reply_text)

        with self.state_db.connect() as conn:
            offer_blob_row = conn.execute(
                "SELECT value FROM runtime_state WHERE state_key = ?",
                ("agent_onboarding:human:telegram:111",),
            ).fetchone()
        self.assertIsNotNone(offer_blob_row)
        offer_blob = json.loads(str(offer_blob_row["value"]))
        self.assertEqual(offer_blob["status"], "active")
        self.assertEqual(offer_blob["step"], "awaiting_reonboard_consent")

        # Second turn: `yes` accepts the offer and transitions to awaiting_name.
        yes_turn = maybe_handle_agent_persona_onboarding_turn(
            human_id="human:telegram:111",
            agent_id="agent:human:telegram:111",
            user_message="yes",
            state_db=self.state_db,
            source_surface="telegram",
            source_ref="test-reonboard-yes",
            start_if_eligible=False,
        )
        self.assertIsNotNone(yes_turn)
        assert yes_turn is not None  # for type-checker
        self.assertEqual(yes_turn.step, "awaiting_name")
        self.assertFalse(yes_turn.completed)
        self.assertIn("Restarting setup", yes_turn.reply_text)
        self.assertIn("/cancel", yes_turn.reply_text)
        # Should offer to keep the existing name since one is already saved.
        self.assertIn("Atlas", yes_turn.reply_text)
        self.assertIn("keep", yes_turn.reply_text)

        with self.state_db.connect() as conn:
            yes_blob_row = conn.execute(
                "SELECT value FROM runtime_state WHERE state_key = ?",
                ("agent_onboarding:human:telegram:111",),
            ).fetchone()
        self.assertIsNotNone(yes_blob_row)
        yes_blob = json.loads(str(yes_blob_row["value"]))
        self.assertEqual(yes_blob["status"], "active")
        self.assertEqual(yes_blob["step"], "awaiting_name")

        # The saved persona profile should be untouched through the offer
        # and the yes acceptance (persona only changes after the user
        # actually completes the v2 conversation again).
        persona_after = load_agent_persona_profile(
            agent_id="agent:human:telegram:111",
            human_id="human:telegram:111",
            state_db=self.state_db,
        )
        self.assertEqual(persona_after.get("persona_name"), "Atlas")
        self.assertEqual(
            persona_after.get("persona_summary"),
            "Calm, strategic, very direct, low-fluff.",
        )
        self.assertAlmostEqual(persona_after["base_traits"]["directness"], 0.83)

    def test_onboarding_reonboard_consent_skip_closes_offer_and_preserves_persona(self) -> None:
        """P2-12: any reply other than yes closes the offer without touching persona.

        Q-H of docs/PERSONALITY_ONBOARDING_V2_DESIGN_2026-04-10.md \u00a711:
        the one-tap skip offer defaults to 'keep things as they are'. After
        the user replies with anything other than an explicit yes token,
        the onboarding state is marked completed (so the offer never
        re-shows) and the handler returns None so the normal reply path
        handles the message. The saved persona profile is untouched.
        """
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
            source_ref="seed-name",
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

        # First turn creates the offer state.
        offer_turn = maybe_handle_agent_persona_onboarding_turn(
            human_id="human:telegram:111",
            agent_id="agent:human:telegram:111",
            user_message="hey",
            state_db=self.state_db,
            source_surface="telegram",
            source_ref="test-reonboard-skip-offer",
            start_if_eligible=True,
        )
        self.assertIsNotNone(offer_turn)
        assert offer_turn is not None  # for type-checker
        self.assertEqual(offer_turn.step, "awaiting_reonboard_consent")

        # Second turn: a non-yes reply closes out the offer and returns None
        # so the researcher bridge handles the message normally.
        skip_turn = maybe_handle_agent_persona_onboarding_turn(
            human_id="human:telegram:111",
            agent_id="agent:human:telegram:111",
            user_message="what's the weather",
            state_db=self.state_db,
            source_surface="telegram",
            source_ref="test-reonboard-skip",
            start_if_eligible=False,
        )
        self.assertIsNone(skip_turn)

        # The state blob should exist but be marked completed so the offer
        # never re-shows for this user.
        with self.state_db.connect() as conn:
            skip_blob_row = conn.execute(
                "SELECT value FROM runtime_state WHERE state_key = ?",
                ("agent_onboarding:human:telegram:111",),
            ).fetchone()
        self.assertIsNotNone(skip_blob_row)
        skip_blob = json.loads(str(skip_blob_row["value"]))
        self.assertEqual(skip_blob["status"], "completed")

        # A subsequent eligible call must NOT re-offer the card: the state
        # is completed so the handler short-circuits.
        follow_up_turn = maybe_handle_agent_persona_onboarding_turn(
            human_id="human:telegram:111",
            agent_id="agent:human:telegram:111",
            user_message="hi again",
            state_db=self.state_db,
            source_surface="telegram",
            source_ref="test-reonboard-followup",
            start_if_eligible=True,
        )
        self.assertIsNone(follow_up_turn)

        # The saved persona profile should be untouched by the skip path.
        persona_after = load_agent_persona_profile(
            agent_id="agent:human:telegram:111",
            human_id="human:telegram:111",
            state_db=self.state_db,
        )
        self.assertEqual(persona_after.get("persona_name"), "Atlas")
        self.assertEqual(
            persona_after.get("persona_summary"),
            "Calm, strategic, very direct, low-fluff.",
        )
        self.assertAlmostEqual(persona_after["base_traits"]["directness"], 0.83)

        # The saved agent name should also be untouched.
        agent_state = read_canonical_agent_state(
            state_db=self.state_db,
            human_id="human:telegram:111",
        )
        self.assertEqual(agent_state.agent_name, "Atlas")

    def test_runtime_existing_user_with_persona_sees_reonboard_offer_after_welcome_consumed(self) -> None:
        """P2-13: existing-user reonboard offer fires through the runtime.

        Wires the P2-12 `awaiting_reonboard_consent` state into the
        Telegram runtime. After pairing welcome has been consumed, an
        existing user with a saved persona profile should still see the
        one-tap skip offer on their next DM, and replying `yes` should
        transition the state machine back to `awaiting_name` so they can
        re-run the short v2 setup conversation. Neither turn should
        reach the researcher bridge.

        P2-13 of docs/PERSONALITY_PHASE2_PLAN_2026-04-10.md; Q-H of
        docs/PERSONALITY_ONBOARDING_V2_DESIGN_2026-04-10.md \u00a711.
        """
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
            source_ref="seed-name",
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
        # Consume the pairing welcome so the old
        # `start_if_eligible=pairing_welcome_pending(...)` gate is no
        # longer True for this user. P2-13's new gate
        # (`agent_has_reonboard_candidate`) should still fire.
        self.assertTrue(
            consume_pairing_welcome(
                state_db=self.state_db,
                channel_id="telegram",
                external_user_id="111",
            )
        )

        with patch(
            "spark_intelligence.adapters.telegram.runtime.build_researcher_reply",
            side_effect=AssertionError(
                "researcher bridge should not run during reonboard offer"
            ),
        ):
            offer_result = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=1300,
                    user_id="111",
                    username="alice",
                    text="hey atlas",
                ),
            )
            yes_result = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=1301,
                    user_id="111",
                    username="alice",
                    text="yes",
                ),
            )

        self.assertTrue(offer_result.ok)
        offer_text = str(offer_result.detail["response_text"])
        self.assertIn("re-run setup", offer_text)
        self.assertIn("Atlas", offer_text)
        self.assertIn("`yes`", offer_text)

        self.assertTrue(yes_result.ok)
        yes_text = str(yes_result.detail["response_text"])
        self.assertIn("Restarting setup", yes_text)
        self.assertIn("keep", yes_text)
        self.assertIn("/cancel", yes_text)

        # The in-progress blob should now sit at awaiting_name, ready
        # for the rename turn.
        with self.state_db.connect() as conn:
            blob_row = conn.execute(
                "SELECT value FROM runtime_state WHERE state_key = ?",
                ("agent_onboarding:human:telegram:111",),
            ).fetchone()
        self.assertIsNotNone(blob_row)
        blob = json.loads(str(blob_row["value"]))
        self.assertEqual(blob["status"], "active")
        self.assertEqual(blob["step"], "awaiting_name")

        # The saved persona profile should still be untouched by the
        # offer and the yes acceptance.
        persona_after = load_agent_persona_profile(
            agent_id="agent:human:telegram:111",
            human_id="human:telegram:111",
            state_db=self.state_db,
        )
        self.assertEqual(persona_after.get("persona_name"), "Atlas")
        self.assertAlmostEqual(persona_after["base_traits"]["directness"], 0.83)

    def test_runtime_existing_user_reonboard_skip_passes_through_to_researcher_bridge(self) -> None:
        """P2-13: skip reply on the reonboard offer falls through to the bridge.

        After the runtime raises the one-tap skip offer, the user can
        reply with anything other than an explicit yes token to keep
        their existing persona. That reply should NOT be intercepted by
        the onboarding state machine — it must fall through to the
        researcher bridge so the normal DM response path handles it.
        The saved persona profile and agent name stay untouched and the
        onboarding state is marked `completed` so the offer never
        re-shows.

        P2-13 of docs/PERSONALITY_PHASE2_PLAN_2026-04-10.md; Q-H of
        docs/PERSONALITY_ONBOARDING_V2_DESIGN_2026-04-10.md \u00a711.
        """
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
            source_ref="seed-name",
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
        self.assertTrue(
            consume_pairing_welcome(
                state_db=self.state_db,
                channel_id="telegram",
                external_user_id="111",
            )
        )

        # First turn: offer card, no researcher bridge.
        with patch(
            "spark_intelligence.adapters.telegram.runtime.build_researcher_reply",
            side_effect=AssertionError(
                "researcher bridge should not run during reonboard offer"
            ),
        ):
            offer_result = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=1400,
                    user_id="111",
                    username="alice",
                    text="hey",
                ),
            )
        self.assertTrue(offer_result.ok)
        self.assertIn(
            "re-run setup",
            str(offer_result.detail["response_text"]),
        )

        # Second turn: any non-yes reply falls through to the
        # researcher bridge. The bridge's reply text is what the user
        # actually sees.
        with patch(
            "spark_intelligence.adapters.telegram.runtime.build_researcher_reply",
            return_value=ResearcherBridgeResult(
                request_id="req-reonboard-skip",
                reply_text="Here is the grounded answer.",
                evidence_summary="status=under_supported",
                escalation_hint=None,
                trace_ref="trace:reonboard-skip",
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
                routing_decision="provider_fallback_chat",
            ),
        ) as bridge_patch:
            skip_result = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=1401,
                    user_id="111",
                    username="alice",
                    text="nah just chat with me",
                ),
            )
            self.assertEqual(bridge_patch.call_count, 1)

        self.assertTrue(skip_result.ok)
        self.assertEqual(
            skip_result.detail["response_text"],
            "Here is the grounded answer.",
        )

        # The onboarding blob should be marked completed so the offer
        # never re-shows for this user.
        with self.state_db.connect() as conn:
            blob_row = conn.execute(
                "SELECT value FROM runtime_state WHERE state_key = ?",
                ("agent_onboarding:human:telegram:111",),
            ).fetchone()
        self.assertIsNotNone(blob_row)
        blob = json.loads(str(blob_row["value"]))
        self.assertEqual(blob["status"], "completed")

        # A follow-up DM should go straight to the bridge: the state
        # blob is completed, so `agent_has_reonboard_candidate` is
        # False and the entry gate short-circuits.
        with patch(
            "spark_intelligence.adapters.telegram.runtime.build_researcher_reply",
            return_value=ResearcherBridgeResult(
                request_id="req-reonboard-followup",
                reply_text="Another grounded answer.",
                evidence_summary="status=under_supported",
                escalation_hint=None,
                trace_ref="trace:reonboard-followup",
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
                routing_decision="provider_fallback_chat",
            ),
        ) as followup_patch:
            followup_result = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=1402,
                    user_id="111",
                    username="alice",
                    text="another question",
                ),
            )
            self.assertEqual(followup_patch.call_count, 1)
        self.assertTrue(followup_result.ok)
        self.assertEqual(
            followup_result.detail["response_text"],
            "Another grounded answer.",
        )

        # Saved persona and agent name both untouched.
        persona_after = load_agent_persona_profile(
            agent_id="agent:human:telegram:111",
            human_id="human:telegram:111",
            state_db=self.state_db,
        )
        self.assertEqual(persona_after.get("persona_name"), "Atlas")
        agent_state = read_canonical_agent_state(
            state_db=self.state_db,
            human_id="human:telegram:111",
        )
        self.assertEqual(agent_state.agent_name, "Atlas")

    def test_onboarding_guardrails_ack_change_branch_stays_pinned_then_accepts(self) -> None:
        """P2-10: `change` soft-reprompts without advancing; `ok` completes.

        Verifies Q-C ("show but don't gate") behavior on the guardrails ack
        state: any reply other than `change`/`adjust` (including the literal
        word `ok`) is treated as acceptance, while `change` leaves the state
        pinned at awaiting_guardrails_ack and returns a pointer toward the
        NL preference path.
        """
        self.add_telegram_channel()
        simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=800,
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
            # Walk through to the guardrails ack card via the express path.
            for update_id, text in (
                (801, "hey"),
                (802, "Nova"),
                (803, "Boss"),
                (804, "express"),
                (805, "operator"),
            ):
                simulate_telegram_update(
                    config_manager=self.config_manager,
                    state_db=self.state_db,
                    update_payload=make_telegram_update(
                        update_id=update_id, user_id="111", username="alice", text=text
                    ),
                )

            # The preset pick should have pinned the state to the guardrails
            # card. Replying "change" should soft-reprompt without advancing.
            with self.state_db.connect() as conn:
                pre_blob_row = conn.execute(
                    "SELECT value FROM runtime_state WHERE state_key = ?",
                    ("agent_onboarding:human:telegram:111",),
                ).fetchone()
            pre_blob = json.loads(str(pre_blob_row["value"]))
            self.assertEqual(pre_blob["step"], "awaiting_guardrails_ack")

            change_turn = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=806, user_id="111", username="alice", text="change"
                ),
            )
            # The state should still be pinned at awaiting_guardrails_ack
            # after the "change" reply.
            with self.state_db.connect() as conn:
                mid_blob_row = conn.execute(
                    "SELECT value FROM runtime_state WHERE state_key = ?",
                    ("agent_onboarding:human:telegram:111",),
                ).fetchone()
            mid_blob = json.loads(str(mid_blob_row["value"]))
            self.assertEqual(mid_blob["step"], "awaiting_guardrails_ack")

            accept_turn = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=807, user_id="111", username="alice", text="ok"
                ),
            )

        self.assertTrue(change_turn.ok)
        change_reply = str(change_turn.detail["response_text"])
        self.assertIn("ok", change_reply.lower())
        self.assertIn("be gentler", change_reply)
        self.assertIn("be more direct", change_reply)

        self.assertTrue(accept_turn.ok)
        accept_reply = str(accept_turn.detail["response_text"])
        self.assertIn("Locked in", accept_reply)
        self.assertIn("Here's the recap", accept_reply)
        self.assertIn("Nova", accept_reply)
        self.assertIn("Boss", accept_reply)
        self.assertIn(
            "anti-glazing, better-way surfacing, honest failure reporting",
            accept_reply,
        )

        with self.state_db.connect() as conn:
            final_blob_row = conn.execute(
                "SELECT value FROM runtime_state WHERE state_key = ?",
                ("agent_onboarding:human:telegram:111",),
            ).fetchone()
        final_blob = json.loads(str(final_blob_row["value"]))
        self.assertEqual(final_blob["step"], "completed")
        self.assertEqual(final_blob["status"], "completed")

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
        self.assertIn("Access is not authorized for this channel", str(follow_up.detail["response_text"]))

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
        self.assertIn("Access is not authorized for this channel", str(follow_up.detail["response_text"]))

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

        # NOTE: the mock reply text intentionally avoids trivial greetings
        # ("hello there", "hey", "hi", etc.) because
        # personality/loader._normalize_telegram_conversation_reply rewrites
        # those into "Ready when you are." via _TELEGRAM_TRIVIAL_GREETING_RE
        # (introduced in commit dbe3cdb, after this test was first written
        # in fb533e0). Use a substantive reply so the test only exercises
        # the think-block stripping behavior it is actually asserting.
        with patch(
            "spark_intelligence.adapters.telegram.runtime.build_researcher_reply",
            return_value=ResearcherBridgeResult(
                request_id="req-think-off",
                reply_text="<think>private reasoning</think>\n\nThe answer is 42.",
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
        self.assertEqual(result.detail["response_text"], "The answer is 42.")

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
        self.assertEqual(result.detail["response_text"], "Ready when you are.")
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

    def test_natural_language_think_commands_toggle_telegram_visibility(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        enable_result = simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=1131,
                user_id="111",
                username="alice",
                text="Turn thinking on",
            ),
        )
        self.assertTrue(enable_result.ok)
        self.assertIn("Thinking visibility enabled", str(enable_result.detail["response_text"]))

        status_result = simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=1132,
                user_id="111",
                username="alice",
                text="What is the thinking status?",
            ),
        )
        self.assertTrue(status_result.ok)
        self.assertIn("currently on", str(status_result.detail["response_text"]))

        disable_result = simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=1133,
                user_id="111",
                username="alice",
                text="Turn thinking off",
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
        self.assertIn("Auth is configured, last sync was uploaded, and the last decision was manual_recommended.", str(result.detail["response_text"]))
        self.assertIn("Next: `/swarm sync` or `/swarm collective`.", str(result.detail["response_text"]))

    def test_swarm_doctor_command_returns_operator_facing_diagnostics(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        with patch(
            "spark_intelligence.adapters.telegram.runtime.swarm_doctor",
            return_value=SimpleNamespace(
                auth_source="workspace_env",
                payload_source="specialization_path",
                active_path_key="trading-crypto",
                active_path_repo_root="/tmp/trading-crypto",
                scenario_path="/tmp/trading-crypto/benchmarks/scenarios/trend-ema-btceth-4h.json",
                mutation_target_path="/tmp/trading-crypto/benchmarks/trading-crypto-candidate.json",
                blockers=[],
                recommendations=["Run `/swarm sync` to upload the latest collective payload to Spark Swarm."],
            ),
        ):
            result = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=1141,
                    user_id="111",
                    username="alice",
                    text="/swarm doctor",
                ),
            )

        self.assertTrue(result.ok)
        self.assertIn("Swarm doctor: ready.", str(result.detail["response_text"]))
        self.assertIn("Auth source: workspace_env. Payload source: specialization_path.", str(result.detail["response_text"]))
        self.assertIn("No blockers detected.", str(result.detail["response_text"]))
        self.assertIn("Next: Run `/swarm sync` to upload the latest collective payload to Spark Swarm.", str(result.detail["response_text"]))

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
        self.assertIn("Swarm is ready.", str(result.detail["response_text"]))

    def test_normal_chat_reply_uses_saved_agent_identity_surface_style(self) -> None:
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
        self.assertTrue(
            consume_pairing_welcome(
                state_db=self.state_db,
                channel_id="telegram",
                external_user_id="111",
            )
        )
        # P2-13: mark the onboarding state `completed` so the new
        # `agent_has_reonboard_candidate` entry gate short-circuits and
        # this test still exercises the normal chat path, not the
        # one-tap skip offer. Simulates an existing user who has
        # already dismissed or completed the reonboard flow.
        with self.state_db.connect() as conn:
            conn.execute(
                "INSERT INTO runtime_state(state_key, value) VALUES (?, ?) "
                "ON CONFLICT(state_key) DO UPDATE SET value=excluded.value",
                (
                    "agent_onboarding:human:telegram:111",
                    json.dumps(
                        {
                            "status": "completed",
                            "step": "completed",
                            "agent_id": "agent:human:telegram:111",
                            "agent_name": "Atlas",
                            "persona_summary": "Calm, strategic, very direct, low-fluff.",
                            "completed_at": "2026-04-01T00:00:00+00:00",
                            "updated_at": "2026-04-01T00:00:00+00:00",
                        },
                        sort_keys=True,
                    ),
                ),
            )
            conn.commit()

        with patch(
            "spark_intelligence.adapters.telegram.runtime.build_researcher_reply",
            return_value=ResearcherBridgeResult(
                request_id="req-normal-chat-persona",
                reply_text="Here is the grounded answer.",
                evidence_summary="status=under_supported provider_fallback=direct_http_chat",
                escalation_hint=None,
                trace_ref="trace:normal-chat-persona",
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
                routing_decision="provider_fallback_chat",
            ),
        ):
            result = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=315,
                    user_id="111",
                    username="alice",
                    text="Tell me something useful.",
                ),
            )

        self.assertTrue(result.ok)
        self.assertEqual(result.detail["response_text"], "Here is the grounded answer.")

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
        self.assertIn("Swarm is attached to Vibe Forge Workspace.", str(result.detail["response_text"]))
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

    def test_chip_status_reports_when_no_chips_are_attached(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        with patch(
            "spark_intelligence.adapters.telegram.runtime.list_chip_records",
            return_value=[],
        ):
            result = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=842,
                    user_id="111",
                    username="alice",
                    text="/chip status",
                ),
            )

        self.assertTrue(result.ok)
        self.assertIn("No chips are attached in this workspace yet.", str(result.detail["response_text"]))

    def test_chip_status_reports_trading_chip_details(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        record = SimpleNamespace(
            key="domain-chip-trading-crypto",
            commands={"evaluate": ["python"], "suggest": ["python"]},
            repo_root="C:/chips/domain-chip-trading-crypto",
            description="Crypto trading doctrine-and-strategy chip.",
            frontier={
                "allowed_mutations": {
                    "doctrine_id": ["trend_regime_following"],
                    "strategy_id": ["ema_pullback_long"],
                    "market_regime": ["trend"],
                }
            },
        )

        with patch(
            "spark_intelligence.adapters.telegram.runtime.list_chip_records",
            return_value=[record],
        ), patch(
            "spark_intelligence.adapters.telegram.runtime.build_attachment_context",
            return_value={
                "active_chip_keys": ["domain-chip-trading-crypto"],
                "pinned_chip_keys": ["domain-chip-trading-crypto"],
            },
        ):
            result = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=843,
                    user_id="111",
                    username="alice",
                    text="/chip status domain-chip-trading-crypto",
                ),
            )

        self.assertTrue(result.ok)
        self.assertIn("Chip `domain-chip-trading-crypto` is active and pinned.", str(result.detail["response_text"]))
        self.assertIn("Hooks: evaluate, suggest.", str(result.detail["response_text"]))
        self.assertIn("Frontier mutation fields: doctrine_id, market_regime, strategy_id.", str(result.detail["response_text"]))

    def test_chip_evaluate_runs_direct_chip_command_with_mutation_pairs(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        with patch(
            "spark_intelligence.adapters.telegram.runtime.run_chip_hook",
            return_value=SimpleNamespace(
                ok=True,
                chip_key="domain-chip-trading-crypto",
                hook="evaluate",
                repo_root="C:/chips/domain-chip-trading-crypto",
                command=["python", "-m", "domain_chip_trading_crypto.cli", "evaluate"],
                exit_code=0,
                stdout="",
                stderr="",
                output={
                    "metrics": {
                        "profitability_score": 0.81,
                        "sharpe_ratio": 0.74,
                        "max_drawdown": 0.19,
                        "win_rate": 0.57,
                        "paper_trade_readiness": 0.79,
                        "verdict_confidence": 0.83,
                    },
                    "result": {
                        "claim": "Backtest profitability must be judged with drawdown, regime fit, and paper-trade readiness.",
                        "verdict": "approve",
                        "mechanism": "Trend doctrine works when regime filters are explicit.",
                        "boundary": "Weak in chop without a regime filter.",
                        "recommended_next_step": "queue_for_paper_trade",
                    },
                },
            ),
        ) as run_hook_mock, patch(
            "spark_intelligence.adapters.telegram.runtime.record_chip_hook_execution",
            return_value=None,
        ), patch(
            "spark_intelligence.adapters.telegram.runtime.screen_chip_hook_text",
            side_effect=lambda **kwargs: {"allowed": True, "text": kwargs["text"], "quarantine_id": None},
        ):
            result = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=844,
                    user_id="111",
                    username="alice",
                    text=(
                        "/chip evaluate domain-chip-trading-crypto "
                        "doctrine_id=trend_regime_following strategy_id=ema_pullback_long "
                        "market_regime=trend timeframe=1h venue=binance asset_universe=BTC paper_gate=strict"
                    ),
                ),
            )

        self.assertTrue(result.ok)
        self.assertIn("Chip `domain-chip-trading-crypto` evaluate completed.", str(result.detail["response_text"]))
        self.assertIn("Verdict: approve.", str(result.detail["response_text"]))
        self.assertIn("Recommended next step: queue_for_paper_trade.", str(result.detail["response_text"]))
        self.assertIn("Scope: this was a direct chip hook run only", str(result.detail["response_text"]))
        payload = run_hook_mock.call_args.kwargs["payload"]
        self.assertEqual(payload["candidate"]["mutations"]["doctrine_id"], "trend_regime_following")
        self.assertEqual(payload["candidate"]["mutations"]["strategy_id"], "ema_pullback_long")

    def test_chip_autoloop_explains_specialization_path_requirement(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        result = simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=845,
                user_id="111",
                username="alice",
                text="/chip autoloop domain-chip-trading-crypto",
            ),
        )

        self.assertTrue(result.ok)
        self.assertIn("Chip autoloop is not available", str(result.detail["response_text"]))
        self.assertIn("/swarm autoloop <path_key>", str(result.detail["response_text"]))

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
        self.assertIn("Swarm sync completed.", str(result.detail["response_text"]))
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
        self.assertIn("Swarm sync completed.", str(result.detail["response_text"]))
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
            "Swarm action is unavailable.\n"
            "Reason: Swarm API request failed with HTTP 404. insight not found.\n"
            "Next: verify the target id or route on the current Swarm host.",
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
        self.assertIn("Swarm collective has 1 open contradiction(s), 1 pending upgrade(s), and 1 inbox item(s).", str(result.detail["response_text"]))
        self.assertIn("Specializations: 1. Paths: 2. Insights: 1. Masteries: 1.", str(result.detail["response_text"]))
        self.assertIn("Next: `/swarm upgrades`, `/swarm issues`, or `/swarm inbox`.", str(result.detail["response_text"]))

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
        self.assertIn("Startup Operator run completed.", str(result.detail["response_text"]))
        self.assertIn("Next: `/swarm autoloop startup-operator` or `/swarm session startup-operator`.", str(result.detail["response_text"]))
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
        self.assertIn("Swarm read is unavailable.", str(result.detail["response_text"]))
        self.assertIn("Reason: Swarm API URL is missing.", str(result.detail["response_text"]))
        self.assertIn("Next: configure the Swarm API URL, then retry.", str(result.detail["response_text"]))

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

    def test_current_plan_memory_update_beats_schedule_suggestion_shortcircuit(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        with patch(
            "spark_intelligence.adapters.telegram.runtime.build_researcher_reply",
            return_value=ResearcherBridgeResult(
                request_id="req-current-plan-scheduled-memory",
                reply_text="I'll remember that your current plan is to verify scheduled memory cleanup.",
                evidence_summary="status=memory_generic_observation_update",
                escalation_hint=None,
                trace_ref=None,
                mode="memory_generic_observation_update",
                runtime_root=None,
                config_path=None,
                attachment_context=None,
                provider_id=None,
                provider_auth_profile_id=None,
                provider_auth_method=None,
                provider_model=None,
                provider_model_family=None,
                provider_execution_transport=None,
                provider_base_url=None,
                provider_source=None,
                routing_decision="memory_generic_observation",
            ),
        ) as bridge_mock:
            result = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=219,
                    user_id="111",
                    username="alice",
                    text="Set my current plan to verify scheduled memory cleanup.",
                ),
            )

        self.assertTrue(result.ok)
        self.assertEqual(
            result.detail["response_text"],
            "I'll remember that your current plan is to verify scheduled memory cleanup.",
        )
        self.assertEqual(result.detail["bridge_mode"], "memory_generic_observation_update")
        bridge_mock.assert_called_once()
        self.assertEqual(
            bridge_mock.call_args.kwargs["user_message"],
            "Set my current plan to verify scheduled memory cleanup.",
        )

    def test_jobs_running_question_beats_mission_board_shortcircuit(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        with patch(
            "spark_intelligence.adapters.telegram.runtime.build_researcher_reply",
            return_value=ResearcherBridgeResult(
                request_id="req-jobs-running-mission-control",
                reply_text="Runtime health: healthy.\nActive loops: job:memory:sdk-maintenance.",
                evidence_summary="status=mission_control_direct source=verified_runtime_health",
                escalation_hint=None,
                trace_ref=None,
                mode="mission_control_direct",
                runtime_root=None,
                config_path=None,
                attachment_context=None,
                provider_id=None,
                provider_auth_profile_id=None,
                provider_auth_method=None,
                provider_model=None,
                provider_model_family=None,
                provider_execution_transport=None,
                provider_base_url=None,
                provider_source=None,
                routing_decision="mission_control_direct",
            ),
        ) as bridge_mock:
            result = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=220,
                    user_id="111",
                    username="alice",
                    text="What jobs are running?",
                ),
            )

        self.assertTrue(result.ok)
        self.assertEqual(
            result.detail["response_text"],
            "Runtime health: healthy.\nActive loops: job:memory:sdk-maintenance.",
        )
        self.assertEqual(result.detail["bridge_mode"], "mission_control_direct")
        bridge_mock.assert_called_once()

    def test_browser_permission_block_reply_is_composed_for_telegram(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        with patch(
            "spark_intelligence.adapters.telegram.runtime.build_researcher_reply",
            return_value=ResearcherBridgeResult(
                request_id="req-browser-permission",
                reply_text=(
                    "Web search is blocked because the browser extension does not have host access "
                    "for https://duckduckgo.com. Open the extension popup and grant explicit site "
                    "access for https://duckduckgo.com, then retry the search."
                ),
                evidence_summary="Browser search blocked by missing host permission.",
                escalation_hint="grant_origin_access",
                trace_ref="trace:browser-permission",
                mode="blocked",
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
                routing_decision="browser_permission_required",
            ),
        ):
            result = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=2181,
                    user_id="111",
                    username="alice",
                    text="Search the web for BTC and cite the source.",
                ),
            )

        self.assertTrue(result.ok)
        self.assertEqual(
            result.detail["response_text"],
            "I can't open that page yet.\n"
            "I don't have access to https://duckduckgo.com.\n"
            "Grant site access for https://duckduckgo.com in the extension popup and I'll try again.",
        )

    def test_browser_session_block_reply_is_composed_for_telegram(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        with patch(
            "spark_intelligence.adapters.telegram.runtime.build_researcher_reply",
            return_value=ResearcherBridgeResult(
                request_id="req-browser-session",
                reply_text=(
                    "Web search is currently unavailable because the browser-use live session "
                    "is disconnected. Reload or reconnect the extension, then retry the search."
                ),
                evidence_summary="Browser search unavailable because the live browser session is disconnected.",
                escalation_hint="reconnect_browser_session",
                trace_ref="trace:browser-session",
                mode="blocked",
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
                routing_decision="browser_unavailable",
            ),
        ):
            result = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=2182,
                    user_id="111",
                    username="alice",
                    text="Search the web for BTC and cite the source.",
                ),
            )

        self.assertTrue(result.ok)
        self.assertEqual(
            result.detail["response_text"],
            "I can't search the web right now.\n"
            "My live browser session dropped.\n"
            "Reconnect it and ask me again.",
        )

    def test_browser_evidence_source_capture_warning_gets_next_step_for_telegram(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        with patch(
            "spark_intelligence.adapters.telegram.runtime.build_researcher_reply",
            return_value=ResearcherBridgeResult(
                request_id="req-browser-evidence-warning",
                reply_text=(
                    "Bitcoin is trading with elevated volatility after the latest ETF flow update.\n\n"
                    "Source capture failed on the result page, so retry the search if you need an authoritative citation."
                ),
                evidence_summary="status=browser_evidence provider_fallback=direct_http_chat",
                escalation_hint=None,
                trace_ref="trace:browser-evidence-warning",
                mode="browser_evidence",
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
                routing_decision="browser_search_provider_chat",
            ),
        ):
            result = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=2183,
                    user_id="111",
                    username="alice",
                    text="Search the web for BTC and give me the latest.",
                ),
            )

        self.assertTrue(result.ok)
        self.assertEqual(
            result.detail["response_text"],
            "Bitcoin is trading with elevated volatility after the latest ETF flow update.\n\n"
            "Citation status: source capture failed on the result page.\n"
            "Next: retry the search if you need an authoritative citation.",
        )

    def test_researcher_disabled_reply_is_composed_for_telegram(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        with patch(
            "spark_intelligence.adapters.telegram.runtime.build_researcher_reply",
            return_value=ResearcherBridgeResult(
                request_id="req-bridge-disabled",
                reply_text="[Spark Researcher disabled] The operator has disabled the Spark Researcher bridge for this workspace.",
                evidence_summary="Spark Researcher bridge disabled by operator.",
                escalation_hint=None,
                trace_ref="trace:bridge-disabled",
                mode="disabled",
                runtime_root=None,
                config_path=None,
                attachment_context={},
                provider_id=None,
                provider_auth_profile_id=None,
                provider_auth_method=None,
                provider_model=None,
                provider_model_family=None,
                provider_execution_transport=None,
                provider_base_url=None,
                provider_source=None,
                routing_decision="bridge_disabled",
            ),
        ):
            result = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=2184,
                    user_id="111",
                    username="alice",
                    text="Research this company for me.",
                ),
            )

        self.assertTrue(result.ok)
        self.assertEqual(
            result.detail["response_text"],
            "I can't pull live research for you right now.\n"
            "Live research is turned off in this workspace.\n"
            "Try again once it's been turned back on.",
        )

    def test_researcher_provider_auth_failure_is_composed_for_telegram(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        with patch(
            "spark_intelligence.adapters.telegram.runtime.build_researcher_reply",
            return_value=ResearcherBridgeResult(
                request_id="req-provider-auth-error",
                reply_text="[Spark Researcher provider auth error] Missing OPENAI_API_KEY for auth profile custom:default",
                evidence_summary="Provider resolution failed closed before bridge execution.",
                escalation_hint="provider_auth_error",
                trace_ref="trace:provider-auth-error",
                mode="bridge_error",
                runtime_root=None,
                config_path=None,
                attachment_context={},
                provider_id="custom",
                provider_auth_profile_id="custom:default",
                provider_auth_method="api_key_env",
                provider_model="MiniMax-M2.7",
                provider_model_family="generic",
                provider_execution_transport="direct_http",
                provider_base_url="https://api.minimax.io/v1",
                provider_source="config+env",
                routing_decision="provider_resolution_failed",
            ),
        ):
            result = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=2185,
                    user_id="111",
                    username="alice",
                    text="Research this company for me.",
                ),
            )

        self.assertTrue(result.ok)
        self.assertEqual(
            result.detail["response_text"],
            "I can't pull live research for you right now.\n"
            "My model credentials aren't set up correctly on this side.\n"
            "Detail: Missing OPENAI_API_KEY for auth profile custom:default\n"
            "Get the model auth fixed, then ask me again.",
        )

    def test_researcher_bridge_error_is_composed_for_telegram(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        with patch(
            "spark_intelligence.adapters.telegram.runtime.build_researcher_reply",
            return_value=ResearcherBridgeResult(
                request_id="req-bridge-error",
                reply_text="[Spark Researcher bridge error] subprocess timed out after 30s",
                evidence_summary="External bridge failed closed.",
                escalation_hint="bridge_error",
                trace_ref="trace:bridge-error",
                mode="bridge_error",
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
                routing_decision="bridge_error",
            ),
        ):
            result = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=2186,
                    user_id="111",
                    username="alice",
                    text="Research this company for me.",
                ),
            )

        self.assertTrue(result.ok)
        self.assertEqual(
            result.detail["response_text"],
            "Spark Intelligence hit an internal bridge error. The operator can inspect local gateway traces.",
        )
        self.assertIn("replace_bridge_error", result.detail["guardrail_actions"])

    def test_spark_character_fallback_reply_is_delivered_without_bridge_error_replacement(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        with patch(
            "spark_intelligence.adapters.telegram.runtime.build_researcher_reply",
            return_value=ResearcherBridgeResult(
                request_id="req-bridge-error",
                reply_text="[Spark Researcher bridge error] subprocess timed out after 30s",
                evidence_summary="External bridge failed closed.",
                escalation_hint="bridge_error",
                trace_ref="trace:bridge-error",
                mode="bridge_error",
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
                routing_decision="bridge_error",
            ),
        ), patch(
            "spark_intelligence.adapters.telegram.runtime.try_spark_character_fallback",
            return_value="Fallback answer from Spark Character.",
        ):
            result = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=2187,
                    user_id="111",
                    username="alice",
                    text="Research this company for me.",
                ),
            )

        self.assertTrue(result.ok)
        self.assertEqual(result.detail["response_text"], "Fallback answer from Spark Character.")
        self.assertEqual(result.detail["bridge_mode"], "spark_character_fallback")
        self.assertEqual(result.detail["routing_decision"], "spark_character_fallback")
        self.assertEqual(result.detail["primary_bridge_mode"], "bridge_error")
        self.assertEqual(result.detail["primary_routing_decision"], "bridge_error")
        self.assertNotIn("replace_bridge_error", result.detail["guardrail_actions"])

    def test_researcher_secret_boundary_block_is_composed_for_telegram(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        with patch(
            "spark_intelligence.adapters.telegram.runtime.build_researcher_reply",
            return_value=ResearcherBridgeResult(
                request_id="req-secret-boundary",
                reply_text=(
                    "[Spark Researcher blocked] Sensitive material was detected in model-visible context. "
                    "I did not send it to the bridge or provider."
                ),
                evidence_summary="Pre-model secret boundary blocked bridge execution.",
                escalation_hint="secret_boundary_violation",
                trace_ref="trace:secret-boundary",
                mode="blocked",
                runtime_root=None,
                config_path=None,
                attachment_context={},
                provider_id=None,
                provider_auth_profile_id=None,
                provider_auth_method=None,
                provider_model=None,
                provider_model_family=None,
                provider_execution_transport=None,
                provider_base_url=None,
                provider_source=None,
                routing_decision="secret_boundary_blocked",
            ),
        ):
            result = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=2187,
                    user_id="111",
                    username="alice",
                    text="Research this secret token and its key material.",
                ),
            )

        self.assertTrue(result.ok)
        self.assertEqual(
            result.detail["response_text"],
            "I held off on that one.\n"
            "There was sensitive material in the request that I'd rather not send out.\n"
            "Pull the secret bits and ask me again.",
        )

    def test_researcher_stub_reply_is_composed_for_telegram(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        with patch(
            "spark_intelligence.adapters.telegram.runtime.build_researcher_reply",
            return_value=ResearcherBridgeResult(
                request_id="req-stub",
                reply_text="[Spark Researcher stub] I received your message in telegram for tg:111: Research this company for me.",
                evidence_summary="No external Spark Researcher runtime was configured or discovered. active_chips=0 active_path=none",
                escalation_hint=None,
                trace_ref="trace:stub",
                mode="stub",
                runtime_root=None,
                config_path=None,
                attachment_context={},
                provider_id=None,
                provider_auth_profile_id=None,
                provider_auth_method=None,
                provider_model=None,
                provider_model_family=None,
                provider_execution_transport=None,
                provider_base_url=None,
                provider_source=None,
                routing_decision="stub",
            ),
        ):
            result = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=2188,
                    user_id="111",
                    username="alice",
                    text="Research this company for me.",
                ),
            )

        self.assertTrue(result.ok)
        self.assertEqual(
            result.detail["response_text"],
            "I can't pull live research for you right now.\n"
            "Nothing's wired up to handle that yet in this workspace.\n"
            "Once it's set up, ask me again.",
        )

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

    def test_style_status_command_reports_saved_persona_state(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])
        rename_agent_identity(
            state_db=self.state_db,
            human_id="human:telegram:111",
            new_name="Operator",
            source_surface="telegram",
            source_ref="seed-name",
        )
        save_agent_persona_profile(
            agent_id="agent:human:telegram:111",
            human_id="human:telegram:111",
            state_db=self.state_db,
            base_traits={
                "warmth": 0.5,
                "directness": 0.8,
                "playfulness": 0.2,
                "pacing": 0.7,
                "assertiveness": 0.7,
            },
            persona_name="Founder Operator",
            persona_summary="Direct, calm, low-fluff.",
            behavioral_rules=["Keep replies short", "Avoid filler"],
        )

        result = simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=116,
                user_id="111",
                username="alice",
                text="/style status",
            ),
        )

        self.assertTrue(result.ok)
        self.assertIn("Style status for Operator.", result.detail["response_text"])
        self.assertIn("Persona: Founder Operator.", result.detail["response_text"])
        self.assertIn("Summary: Direct, calm, low-fluff.", result.detail["response_text"])
        self.assertIn("Rules: Keep replies short | Avoid filler.", result.detail["response_text"])

    def test_style_train_command_persists_explicit_style_feedback(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        result = simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=117,
                user_id="111",
                username="alice",
                text="/style train be more direct and keep replies short",
            ),
        )

        self.assertTrue(result.ok)
        self.assertIn("Saved style update", result.detail["response_text"])
        profile = load_personality_profile(
            human_id="human:telegram:111",
            agent_id="agent:human:telegram:111",
            state_db=self.state_db,
            config_manager=self.config_manager,
        )
        self.assertTrue(profile["agent_persona_applied"])
        self.assertEqual(profile["style_labels"]["directness"], "very direct")

    def test_style_train_claude_like_continuity_persists_conversation_rules(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        result = simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=11705,
                user_id="111",
                username="alice",
                text="/style train be more Claude-like in conversation continuity",
            ),
        )

        self.assertTrue(result.ok)
        self.assertIn("Saved style update", result.detail["response_text"])
        self.assertIn("Stay anchored to the user's last message", result.detail["response_text"])
        profile = load_personality_profile(
            human_id="human:telegram:111",
            agent_id="agent:human:telegram:111",
            state_db=self.state_db,
            config_manager=self.config_manager,
        )
        self.assertIn(
            "Stay anchored to the user's last message instead of resetting the conversation",
            profile["agent_behavioral_rules"],
        )
        self.assertIn(
            "Avoid generic opener questions and canned assistant greetings",
            profile["agent_behavioral_rules"],
        )

    def test_natural_language_style_status_command_returns_saved_style_summary(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        result = simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=11706,
                user_id="111",
                username="alice",
                text="Can you show me my current style?",
            ),
        )

        self.assertTrue(result.ok)
        self.assertIn("Style status for", result.detail["response_text"])
        self.assertEqual(result.detail["bridge_mode"], "runtime_command")

    def test_style_compare_command_returns_side_by_side_examples(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        result = simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=11707,
                user_id="111",
                username="alice",
                text="/style compare",
            ),
        )

        self.assertTrue(result.ok)
        self.assertIn("Style compare for", result.detail["response_text"])
        self.assertIn("Baseline:", result.detail["response_text"])
        self.assertIn("Current:", result.detail["response_text"])
        self.assertIn("Give me the answer in two lines and skip filler.", result.detail["response_text"])

    def test_natural_language_style_compare_command_returns_side_by_side_examples(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        result = simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=11708,
                user_id="111",
                username="alice",
                text="Compare my style",
            ),
        )

        self.assertTrue(result.ok)
        self.assertIn("Style compare for", result.detail["response_text"])
        self.assertEqual(result.detail["bridge_mode"], "runtime_command")

    def test_style_score_command_returns_rubric_scores(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        result = simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=11709,
                user_id="111",
                username="alice",
                text="/style score",
            ),
        )

        self.assertTrue(result.ok)
        self.assertIn("Style score for", result.detail["response_text"])
        self.assertIn("Continuity:", result.detail["response_text"])
        self.assertIn("Anti-filler:", result.detail["response_text"])
        self.assertIn("Next focus:", result.detail["response_text"])

    def test_natural_language_style_score_command_returns_rubric_scores(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        result = simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=11710,
                user_id="111",
                username="alice",
                text="Score my style",
            ),
        )

        self.assertTrue(result.ok)
        self.assertIn("Style score for", result.detail["response_text"])
        self.assertEqual(result.detail["bridge_mode"], "runtime_command")

    def test_style_examples_command_returns_current_voice_samples(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        result = simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=11713,
                user_id="111",
                username="alice",
                text="/style examples",
            ),
        )

        self.assertTrue(result.ok)
        self.assertIn("Style examples for", result.detail["response_text"])
        self.assertIn("Sample:", result.detail["response_text"])
        self.assertIn("Search the web for BTC and give me the answer carefully.", result.detail["response_text"])

    def test_natural_language_style_examples_command_returns_current_voice_samples(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        result = simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=11714,
                user_id="111",
                username="alice",
                text="Show me my style examples",
            ),
        )

        self.assertTrue(result.ok)
        self.assertIn("Style examples for", result.detail["response_text"])
        self.assertEqual(result.detail["bridge_mode"], "runtime_command")

    def test_style_before_after_command_returns_preview_delta_without_persisting(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])
        before_profile = load_personality_profile(
            human_id="human:telegram:111",
            agent_id="agent:human:telegram:111",
            state_db=self.state_db,
            config_manager=self.config_manager,
        )

        result = simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=11718,
                user_id="111",
                username="alice",
                text="/style before-after be more direct and keep replies short",
            ),
        )

        self.assertTrue(result.ok)
        self.assertIn("Style before/after for", result.detail["response_text"])
        self.assertIn("Overall:", result.detail["response_text"])
        self.assertIn("Before:", result.detail["response_text"])
        self.assertIn("After:", result.detail["response_text"])
        profile = load_personality_profile(
            human_id="human:telegram:111",
            agent_id="agent:human:telegram:111",
            state_db=self.state_db,
            config_manager=self.config_manager,
        )
        self.assertEqual(profile["style_labels"]["directness"], before_profile["style_labels"]["directness"])

    def test_natural_language_style_before_after_command_returns_preview_delta(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        result = simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=11719,
                user_id="111",
                username="alice",
                text="Show me style before and after for be more direct and keep replies short",
            ),
        )

        self.assertTrue(result.ok)
        self.assertIn("Style before/after for", result.detail["response_text"])
        self.assertEqual(result.detail["bridge_mode"], "runtime_command")

    def test_style_test_command_returns_training_probe_prompts(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        result = simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=1170,
                user_id="111",
                username="alice",
                text="/style test",
            ),
        )

        self.assertTrue(result.ok)
        self.assertIn("Style test kit", result.detail["response_text"])
        self.assertIn("Search the web for BTC and cite the source.", result.detail["response_text"])
        self.assertIn("/style feedback <note>", result.detail["response_text"])

    def test_style_feedback_command_maps_common_negative_feedback_into_style_update(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        result = simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=1171,
                user_id="111",
                username="alice",
                text="/style feedback too verbose",
            ),
        )

        self.assertTrue(result.ok)
        self.assertIn("Saved style feedback", result.detail["response_text"])
        self.assertIn("less verbose and keep replies short", result.detail["response_text"])
        profile = load_personality_profile(
            human_id="human:telegram:111",
            agent_id="agent:human:telegram:111",
            state_db=self.state_db,
            config_manager=self.config_manager,
        )
        self.assertEqual(profile["style_labels"]["directness"], "very direct")
        # The "too verbose" feedback is mapped by _build_style_training_message
        # to the rule "Be less verbose and keep replies short." (see
        # adapters/telegram/runtime.py:3577). The original assertion here
        # said "Be more direct" which never matched the actual stored rule
        # and was broken from its introduction in commit 1c5211a.
        self.assertIn("Be less verbose and keep replies short", profile["agent_behavioral_rules"])

    def test_style_feedback_maps_canned_conversation_feedback_into_behavior_rules(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        result = simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=11711,
                user_id="111",
                username="alice",
                text="/style feedback less canned and more grounded follow-up questions",
            ),
        )

        self.assertTrue(result.ok)
        self.assertIn("Saved style feedback", result.detail["response_text"])
        self.assertIn("Avoid canned enthusiasm and generic assistant phrasing", result.detail["response_text"])
        profile = load_personality_profile(
            human_id="human:telegram:111",
            agent_id="agent:human:telegram:111",
            state_db=self.state_db,
            config_manager=self.config_manager,
        )
        self.assertIn(
            "Avoid generic opener questions and canned assistant greetings",
            profile["agent_behavioral_rules"],
        )

    def test_style_feedback_maps_less_polished_and_performative_into_behavior_rules(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        result = simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=117115,
                user_id="111",
                username="alice",
                text="/style feedback Be less polished and less performative.",
            ),
        )

        self.assertTrue(result.ok)
        self.assertIn("Saved style feedback", result.detail["response_text"])
        self.assertIn("Avoid polished, performative, or theatrical phrasing", result.detail["response_text"])
        profile = load_personality_profile(
            human_id="human:telegram:111",
            agent_id="agent:human:telegram:111",
            state_db=self.state_db,
            config_manager=self.config_manager,
        )
        self.assertIn(
            "Avoid polished, performative, or theatrical phrasing",
            profile["agent_behavioral_rules"],
        )

    def test_style_feedback_maps_meta_commentary_into_behavior_rules(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        result = simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=117116,
                user_id="111",
                username="alice",
                text="/style feedback In Telegram DM, give the answer first and skip meta commentary.",
            ),
        )

        self.assertTrue(result.ok)
        self.assertIn("Saved style feedback", result.detail["response_text"])
        self.assertIn("Skip meta commentary", result.detail["response_text"])
        profile = load_personality_profile(
            human_id="human:telegram:111",
            agent_id="agent:human:telegram:111",
            state_db=self.state_db,
            config_manager=self.config_manager,
        )
        self.assertIn("Give the answer first", profile["agent_behavioral_rules"])
        self.assertIn(
            "Skip meta commentary about your process, tone, or performance",
            profile["agent_behavioral_rules"],
        )

    def test_style_feedback_maps_previous_turn_instruction_into_behavior_rules(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        result = simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=117117,
                user_id="111",
                username="alice",
                text="/style feedback When I ask about my previous message, answer the immediately previous turn, not the broader conversation.",
            ),
        )

        self.assertTrue(result.ok)
        self.assertIn("Saved style feedback", result.detail["response_text"])
        self.assertIn("answer the immediately previous visible turn", result.detail["response_text"])
        profile = load_personality_profile(
            human_id="human:telegram:111",
            agent_id="agent:human:telegram:111",
            state_db=self.state_db,
            config_manager=self.config_manager,
        )
        self.assertIn(
            "When the user asks about the previous message or previous turn, answer the immediately previous visible turn",
            profile["agent_behavioral_rules"],
        )

    def test_natural_language_style_feedback_routes_into_saved_style_feedback(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        result = simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=11712,
                user_id="111",
                username="alice",
                text="That was too verbose",
            ),
        )

        self.assertTrue(result.ok)
        self.assertIn("Saved style feedback", result.detail["response_text"])
        profile = load_personality_profile(
            human_id="human:telegram:111",
            agent_id="agent:human:telegram:111",
            state_db=self.state_db,
            config_manager=self.config_manager,
        )
        self.assertEqual(profile["style_labels"]["directness"], "very direct")

    def test_style_history_reports_empty_when_no_saved_mutations_exist(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        result = simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=11715,
                user_id="111",
                username="alice",
                text="/style history",
            ),
        )

        self.assertTrue(result.ok)
        self.assertIn("Style history for", result.detail["response_text"])
        self.assertIn("is empty", result.detail["response_text"])

    def test_style_history_reports_recent_saved_mutations(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=11716,
                user_id="111",
                username="alice",
                text="/style train be more direct and keep replies short",
            ),
        )
        result = simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=11717,
                user_id="111",
                username="alice",
                text="/style history",
            ),
        )

        self.assertTrue(result.ok)
        self.assertIn("Recent style history for", result.detail["response_text"])
        self.assertIn("training", result.detail["response_text"])
        self.assertIn("Be more direct and keep replies short", result.detail["response_text"])

    def test_style_presets_command_lists_available_presets(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        result = simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=11720,
                user_id="111",
                username="alice",
                text="/style presets",
            ),
        )

        self.assertTrue(result.ok)
        self.assertIn("Style presets available.", result.detail["response_text"])
        self.assertIn("`operator`", result.detail["response_text"])
        self.assertIn("`claude-like`", result.detail["response_text"])

    def test_style_preset_command_applies_named_preset(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        result = simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=11721,
                user_id="111",
                username="alice",
                text="/style preset concise",
            ),
        )

        self.assertTrue(result.ok)
        self.assertIn("Saved style preset", result.detail["response_text"])
        profile = load_personality_profile(
            human_id="human:telegram:111",
            agent_id="agent:human:telegram:111",
            state_db=self.state_db,
            config_manager=self.config_manager,
        )
        self.assertEqual(profile["style_labels"]["directness"], "very direct")
        self.assertIn("Be more direct and keep replies short", profile["agent_behavioral_rules"])

    def test_natural_language_style_preset_command_applies_named_preset(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        result = simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=11722,
                user_id="111",
                username="alice",
                text="Set style preset to claude-like",
            ),
        )

        self.assertTrue(result.ok)
        self.assertIn("Saved style preset", result.detail["response_text"])
        self.assertEqual(result.detail["bridge_mode"], "runtime_command")
        profile = load_personality_profile(
            human_id="human:telegram:111",
            agent_id="agent:human:telegram:111",
            state_db=self.state_db,
            config_manager=self.config_manager,
        )
        self.assertIn(
            "Avoid generic opener questions and canned assistant greetings",
            profile["agent_behavioral_rules"],
        )

    def test_style_undo_reverts_last_saved_style_change(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=11723,
                user_id="111",
                username="alice",
                text="/style train be more direct and keep replies short",
            ),
        )
        result = simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=11724,
                user_id="111",
                username="alice",
                text="/style undo",
            ),
        )

        self.assertTrue(result.ok)
        self.assertIn("Reverted last style change for", result.detail["response_text"])
        profile = load_personality_profile(
            human_id="human:telegram:111",
            agent_id="agent:human:telegram:111",
            state_db=self.state_db,
            config_manager=self.config_manager,
        )
        self.assertNotIn("Be more direct and keep replies short", profile["agent_behavioral_rules"])

    def test_natural_language_style_undo_reverts_last_saved_style_change(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=11725,
                user_id="111",
                username="alice",
                text="/style preset concise",
            ),
        )
        result = simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=11726,
                user_id="111",
                username="alice",
                text="Undo the last style change",
            ),
        )

        self.assertTrue(result.ok)
        self.assertIn("Reverted last style change for", result.detail["response_text"])
        self.assertEqual(result.detail["bridge_mode"], "runtime_command")

    def test_style_savepoint_and_restore_round_trip(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=11727,
                user_id="111",
                username="alice",
                text="/style train be more direct and keep replies short",
            ),
        )
        savepoint_result = simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=11728,
                user_id="111",
                username="alice",
                text="/style savepoint concise baseline",
            ),
        )
        simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=11729,
                user_id="111",
                username="alice",
                text="/style preset warm",
            ),
        )
        restore_result = simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=11730,
                user_id="111",
                username="alice",
                text="/style restore concise baseline",
            ),
        )

        self.assertTrue(savepoint_result.ok)
        self.assertIn("Saved style savepoint `concise baseline`", savepoint_result.detail["response_text"])
        self.assertTrue(restore_result.ok)
        self.assertIn("Restored style savepoint `concise baseline`", restore_result.detail["response_text"])
        profile = load_personality_profile(
            human_id="human:telegram:111",
            agent_id="agent:human:telegram:111",
            state_db=self.state_db,
            config_manager=self.config_manager,
        )
        self.assertIn("Be more direct and keep replies short", profile["agent_behavioral_rules"])

    def test_style_savepoints_command_lists_named_checkpoints(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=11731,
                user_id="111",
                username="alice",
                text="/style savepoint alpha voice",
            ),
        )
        result = simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=11732,
                user_id="111",
                username="alice",
                text="/style savepoints",
            ),
        )

        self.assertTrue(result.ok)
        self.assertIn("Style savepoints.", result.detail["response_text"])
        self.assertIn("alpha voice", result.detail["response_text"])

    def test_natural_language_style_savepoint_and_restore_routes_runtime_command(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        savepoint_result = simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=11733,
                user_id="111",
                username="alice",
                text="Save style savepoint named checkpoint one",
            ),
        )
        restore_result = simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=11734,
                user_id="111",
                username="alice",
                text="Restore style savepoint named checkpoint one",
            ),
        )

        self.assertTrue(savepoint_result.ok)
        self.assertEqual(savepoint_result.detail["bridge_mode"], "runtime_command")
        self.assertTrue(restore_result.ok)
        self.assertEqual(restore_result.detail["bridge_mode"], "runtime_command")

    def test_style_diff_compares_current_voice_to_named_savepoint(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=11735,
                user_id="111",
                username="alice",
                text="/style savepoint baseline one",
            ),
        )
        simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=11736,
                user_id="111",
                username="alice",
                text="/style train be more direct and keep replies short",
            ),
        )
        result = simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=11737,
                user_id="111",
                username="alice",
                text="/style diff baseline one",
            ),
        )

        self.assertTrue(result.ok)
        self.assertIn("Style diff for", result.detail["response_text"])
        self.assertIn("Score delta:", result.detail["response_text"])
        self.assertIn("Rules then:", result.detail["response_text"])
        self.assertIn("Rules now:", result.detail["response_text"])

    def test_natural_language_style_diff_routes_runtime_command(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=11738,
                user_id="111",
                username="alice",
                text="/style savepoint baseline two",
            ),
        )
        result = simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=11739,
                user_id="111",
                username="alice",
                text="Compare my style to savepoint baseline two",
            ),
        )

        self.assertTrue(result.ok)
        self.assertIn("Style diff for", result.detail["response_text"])
        self.assertEqual(result.detail["bridge_mode"], "runtime_command")

    def test_style_bad_requires_feedback_note(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        result = simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=1172,
                user_id="111",
                username="alice",
                text="/style bad",
            ),
        )

        self.assertTrue(result.ok)
        self.assertIn("Style bad feedback needs a short note.", result.detail["response_text"])

    def test_voice_command_reports_current_telegram_voice_gap(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        with patch(
            "spark_intelligence.adapters.telegram.runtime.run_first_chip_hook_supporting",
            return_value=SimpleNamespace(
                ok=True,
                chip_key="domain-chip-voice-comms",
                stdout="",
                stderr="",
                output={
                    "result": {
                        "reply_text": (
                            "Voice chip is ready.\n"
                            "Current state: transcription is configured via openai using model whisper-1\n"
                            "Next: send a Telegram voice note and I will route it through this chip."
                        )
                    }
                },
            ),
        ):
            result = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=118,
                    user_id="111",
                    username="alice",
                    text="/voice",
                ),
            )

        self.assertTrue(result.ok)
        self.assertIn("Voice chip is ready.", result.detail["response_text"])
        self.assertIn("configured via openai", result.detail["response_text"])

    def test_voice_plan_command_returns_concrete_pipeline_steps(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        with patch(
            "spark_intelligence.adapters.telegram.runtime.run_first_chip_hook_supporting",
            return_value=SimpleNamespace(
                ok=True,
                chip_key="domain-chip-voice-comms",
                stdout="",
                stderr="",
                output={
                    "result": {
                        "reply_text": (
                            "Telegram voice plan:\n"
                            "1. transcribe Telegram voice/audio through `domain-chip-voice-comms`.\n"
                            "2. route the transcript through the same Builder Telegram runtime and saved persona.\n"
                            "3. add optional voice reply synthesis as a second hook without bloating Builder.\n"
                            "Next: attach the chip, validate `voice.status`, then dogfood real Telegram voice notes."
                        )
                    }
                },
            ),
        ):
            result = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=1181,
                    user_id="111",
                    username="alice",
                    text="/voice plan",
                ),
            )

        self.assertTrue(result.ok)
        self.assertIn("Telegram voice plan:", result.detail["response_text"])
        self.assertIn("domain-chip-voice-comms", result.detail["response_text"])

    def test_voice_onboard_command_routes_to_voice_onboard_hook(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])
        captured_payload: dict[str, object] = {}

        def fake_run_first(config_manager, *, hook: str, payload: dict[str, object]):
            captured_payload.update(payload)
            self.assertEqual(hook, "voice.onboard")
            self.assertIn("advisor_context", payload)
            advisor_context = payload["advisor_context"]
            self.assertIsInstance(advisor_context, dict)
            self.assertIn("runtime", advisor_context)
            self.assertIn("source_ledger", advisor_context)
            return SimpleNamespace(
                ok=True,
                chip_key="spark-voice-comms",
                stdout="",
                stderr="",
                output={
                    "result": {
                        "reply_text": (
                            "Spark voice onboarding\n"
                            "- Recommended path: local_free\n"
                            "- Local TTS: install optional `pyttsx3` for offline TTS"
                        )
                    }
                },
            )

        with patch(
            "spark_intelligence.adapters.telegram.runtime.run_first_chip_hook_supporting",
            side_effect=fake_run_first,
        ):
            result = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=11810,
                    user_id="111",
                    username="alice",
                    text="/voice onboard local",
                ),
            )

        self.assertTrue(result.ok)
        self.assertEqual(captured_payload["route"], "local")
        self.assertIn("Spark voice onboarding", result.detail["response_text"])

    def test_natural_language_voice_setup_routes_to_onboarding(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])
        captured_payload: dict[str, object] = {}

        def fake_run_first(config_manager, *, hook: str, payload: dict[str, object]):
            captured_payload.update(payload)
            self.assertEqual(hook, "voice.onboard")
            return SimpleNamespace(
                ok=True,
                chip_key="spark-voice-comms",
                stdout="",
                stderr="",
                output={"result": {"reply_text": "Spark voice onboarding\n- Recommended path: paid_provider"}},
            )

        with patch(
            "spark_intelligence.adapters.telegram.runtime.run_first_chip_hook_supporting",
            side_effect=fake_run_first,
        ):
            result = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=118101,
                    user_id="111",
                    username="alice",
                    text="Can you help me set up voice using paid?",
                ),
            )

        self.assertTrue(result.ok)
        self.assertEqual(captured_payload["route"], "paid")
        self.assertIn("paid_provider", result.detail["response_text"])

    def test_natural_language_local_voice_setup_routes_to_onboarding(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])
        captured_payload: dict[str, object] = {}

        def fake_run_first(config_manager, *, hook: str, payload: dict[str, object]):
            captured_payload.update(payload)
            self.assertEqual(hook, "voice.onboard")
            return SimpleNamespace(
                ok=True,
                chip_key="spark-voice-comms",
                stdout="",
                stderr="",
                output={"result": {"reply_text": "Spark voice onboarding\n- Recommended path: local_free"}},
            )

        with patch(
            "spark_intelligence.adapters.telegram.runtime.run_first_chip_hook_supporting",
            side_effect=fake_run_first,
        ):
            result = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=118102,
                    user_id="111",
                    username="alice",
                    text="Can you help me set up voice locally for Spark?",
                ),
            )

        self.assertTrue(result.ok)
        self.assertEqual(captured_payload["route"], "local")
        self.assertIn("local_free", result.detail["response_text"])

    def test_voice_install_kokoro_command_routes_to_voice_install_hook(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])
        captured_payload: dict[str, object] = {}

        def fake_run_first(config_manager, *, hook: str, payload: dict[str, object]):
            captured_payload.update(payload)
            self.assertEqual(hook, "voice.install")
            return SimpleNamespace(
                ok=True,
                chip_key="spark-voice-comms",
                stdout="",
                stderr="",
                output={"result": {"reply_text": "Kokoro local TTS install\nStatus: package install completed."}},
            )

        with patch(
            "spark_intelligence.adapters.telegram.runtime.run_first_chip_hook_supporting",
            side_effect=fake_run_first,
        ):
            result = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=118104,
                    user_id="111",
                    username="alice",
                    text="/voice install kokoro",
                ),
            )

        self.assertTrue(result.ok)
        self.assertEqual(captured_payload["target"], "kokoro")
        self.assertIn("Kokoro local TTS install", result.detail["response_text"])

    def test_natural_language_kokoro_install_routes_to_voice_install_hook(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])
        captured_payload: dict[str, object] = {}

        def fake_run_first(config_manager, *, hook: str, payload: dict[str, object]):
            captured_payload.update(payload)
            self.assertEqual(hook, "voice.install")
            return SimpleNamespace(
                ok=True,
                chip_key="spark-voice-comms",
                stdout="",
                stderr="",
                output={"result": {"reply_text": "Kokoro local TTS install\nStatus: package install completed."}},
            )

        with patch(
            "spark_intelligence.adapters.telegram.runtime.run_first_chip_hook_supporting",
            side_effect=fake_run_first,
        ):
            result = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=118105,
                    user_id="111",
                    username="alice",
                    text="Can you install Kokoro voice locally?",
                ),
            )

        self.assertTrue(result.ok)
        self.assertEqual(captured_payload["target"], "kokoro")
        self.assertIn("Kokoro local TTS install", result.detail["response_text"])

    def test_natural_language_paid_quality_voice_setup_routes_to_onboarding(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])
        captured_payload: dict[str, object] = {}

        def fake_run_first(config_manager, *, hook: str, payload: dict[str, object]):
            captured_payload.update(payload)
            self.assertEqual(hook, "voice.onboard")
            return SimpleNamespace(
                ok=True,
                chip_key="spark-voice-comms",
                stdout="",
                stderr="",
                output={"result": {"reply_text": "Spark voice onboarding\n- Recommended path: paid_provider"}},
            )

        with patch(
            "spark_intelligence.adapters.telegram.runtime.run_first_chip_hook_supporting",
            side_effect=fake_run_first,
        ):
            result = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=118103,
                    user_id="111",
                    username="alice",
                    text="I want paid high quality voice replies for my Spark agent",
                ),
            )

        self.assertTrue(result.ok)
        self.assertEqual(captured_payload["route"], "paid")
        self.assertIn("paid_provider", result.detail["response_text"])

    def test_voice_reply_command_tracks_telegram_dm_voice_state(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        initial = simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=11811,
                user_id="111",
                username="alice",
                text="/voice reply status",
            ),
        )
        enabled = simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=11812,
                user_id="111",
                username="alice",
                text="/voice reply on",
            ),
        )
        after_enable = simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=11813,
                user_id="111",
                username="alice",
                text="/voice reply",
            ),
        )
        disabled = simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=11814,
                user_id="111",
                username="alice",
                text="/voice reply off",
            ),
        )

        self.assertIn("currently off", initial.detail["response_text"])
        self.assertIn("Voice replies enabled", enabled.detail["response_text"])
        self.assertIn("currently on", after_enable.detail["response_text"])
        self.assertIn("Voice replies disabled", disabled.detail["response_text"])

    def test_natural_language_voice_reply_enable_command_tracks_dm_voice_state(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        enabled = simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=11815,
                user_id="111",
                username="alice",
                text="Turn voice replies on",
            ),
        )
        status = simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=11816,
                user_id="111",
                username="alice",
                text="/voice reply",
            ),
        )

        self.assertIn("Voice replies enabled", enabled.detail["response_text"])
        self.assertIn("currently on", status.detail["response_text"])

    def test_natural_language_install_voice_command_tracks_dm_voice_state(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        result = simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=118165,
                user_id="111",
                username="alice",
                text="can you install a voice to yourself right now?",
            ),
        )

        self.assertIn("Voice replies enabled", result.detail["response_text"])

        typo_result = simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=118166,
                user_id="111",
                username="alice",
                text="can you install a voice to youself right now?",
            ),
        )

        self.assertIn("Voice replies enabled", typo_result.detail["response_text"])

    def test_natural_language_voice_speak_command_queues_one_shot_voice_reply(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        result = simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=11817,
                user_id="111",
                username="alice",
                text="Please speak this out loud: hello from voice",
            ),
        )

        self.assertTrue(result.ok)
        self.assertIn("Sending that as a voice reply now", result.detail["response_text"])

    def test_live_bridge_voice_speak_returns_media_payload(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        def fake_voice_hook(_config_manager, *, hook: str, payload: dict[str, object]):
            if hook != "voice.speak":
                raise AssertionError(f"Unexpected hook: {hook}")
            self.assertEqual(payload["text"], "Hello from bridge.")
            return SimpleNamespace(
                ok=True,
                chip_key="spark-voice-comms",
                stdout="",
                stderr="",
                output={
                    "result": {
                        "audio_base64": base64.b64encode(b"fake-wav").decode("ascii"),
                        "mime_type": "audio/wav",
                        "filename": "telegram-reply.wav",
                        "voice_compatible": False,
                        "provider_id": "kokoro",
                        "voice_id": "af_sarah",
                    }
                },
            )

        def fake_ffmpeg_run(command, **_kwargs):
            Path(command[-1]).write_bytes(b"fake-ogg")
            return SimpleNamespace(returncode=0)

        with patch(
            "spark_intelligence.adapters.telegram.runtime.run_first_chip_hook_supporting",
            side_effect=fake_voice_hook,
        ), patch(
            "spark_intelligence.adapters.telegram.runtime.shutil.which",
            return_value="ffmpeg",
        ), patch(
            "spark_intelligence.adapters.telegram.runtime.subprocess.run",
            side_effect=fake_ffmpeg_run,
        ):
            result = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=11818,
                    user_id="111",
                    username="alice",
                    text="/voice speak Hello from bridge",
                ),
                simulation=False,
            )

        self.assertTrue(result.ok)
        voice_media = result.detail["voice_media"]
        self.assertEqual(voice_media["audio_base64"], base64.b64encode(b"fake-ogg").decode("ascii"))
        self.assertEqual(voice_media["mime_type"], "audio/ogg")
        self.assertEqual(voice_media["filename"], "telegram-reply.ogg")
        self.assertTrue(voice_media["voice_compatible"])
        self.assertEqual(voice_media["provider_id"], "kokoro")

    def test_voice_speak_command_delivers_audio_on_poll_path(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"], bot_token="test-token")

        class FakeVoicePollingClient:
            def __init__(self, updates: list[dict[str, object]]) -> None:
                self.updates = updates
                self.sent_messages: list[dict[str, object]] = []
                self.sent_voices: list[dict[str, object]] = []
                self.sent_documents: list[dict[str, object]] = []

            def get_updates(self, *, offset: int | None = None, timeout_seconds: int = 5) -> list[dict[str, object]]:
                return self.updates

            def send_message(self, *, chat_id: str, text: str) -> dict[str, object]:
                self.sent_messages.append({"chat_id": chat_id, "text": text})
                return {"ok": True}

            def send_document(
                self,
                *,
                chat_id: str,
                document_bytes: bytes,
                filename: str,
                caption: str | None = None,
                mime_type: str | None = None,
            ) -> dict[str, object]:
                self.sent_documents.append(
                    {
                        "chat_id": chat_id,
                        "document_bytes": document_bytes,
                        "filename": filename,
                        "caption": caption,
                        "mime_type": mime_type,
                    }
                )
                return {"ok": True}

            def send_voice(
                self,
                *,
                chat_id: str,
                voice_bytes: bytes,
                filename: str,
                caption: str | None = None,
                mime_type: str | None = None,
            ) -> dict[str, object]:
                self.sent_voices.append(
                    {
                        "chat_id": chat_id,
                        "voice_bytes": voice_bytes,
                        "filename": filename,
                        "caption": caption,
                        "mime_type": mime_type,
                    }
                )
                return {"ok": True}

        client = FakeVoicePollingClient(
            [
                make_telegram_update(
                    update_id=11821,
                    user_id="111",
                    username="alice",
                    text="/voice speak Hello from audio",
                )
            ]
        )

        def fake_voice_hook(_config_manager, *, hook: str, payload: dict[str, object]):
            if hook != "voice.speak":
                raise AssertionError(f"Unexpected hook: {hook}")
            self.assertEqual(payload["text"], "Hello from audio.")
            return SimpleNamespace(
                ok=True,
                chip_key="domain-chip-voice-comms",
                stdout="",
                stderr="",
                output={
                    "result": {
                        "audio_base64": base64.b64encode(b"fake-mp3").decode("ascii"),
                        "mime_type": "audio/ogg",
                        "filename": "telegram-reply-abcdef12.ogg",
                        "voice_compatible": True,
                        "provider_id": "elevenlabs",
                        "voice_id": "spark-core",
                    }
                },
            )

        with patch(
            "spark_intelligence.adapters.telegram.runtime.run_first_chip_hook_supporting",
            side_effect=fake_voice_hook,
        ):
            result = poll_telegram_updates_once(
                config_manager=self.config_manager,
                state_db=self.state_db,
                client=client,
                timeout_seconds=0,
            )

        self.assertEqual(result.processed_count, 1)
        self.assertEqual(len(client.sent_voices), 1)
        self.assertEqual(len(client.sent_documents), 0)
        self.assertEqual(len(client.sent_messages), 0)
        self.assertIn("Sending that as a voice reply now", str(client.sent_voices[0]["caption"]))
        self.assertEqual(client.sent_voices[0]["mime_type"], "audio/ogg")
        self.assertEqual(client.sent_voices[0]["filename"], "telegram-reply-abcdef12.ogg")

    def test_voice_reply_on_sends_runtime_command_as_audio(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"], bot_token="test-token")

        simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=11831,
                user_id="111",
                username="alice",
                text="/voice reply on",
            ),
        )

        class FakeVoicePollingClient:
            def __init__(self, updates: list[dict[str, object]]) -> None:
                self.updates = updates
                self.sent_messages: list[dict[str, object]] = []
                self.sent_voices: list[dict[str, object]] = []
                self.sent_documents: list[dict[str, object]] = []

            def get_updates(self, *, offset: int | None = None, timeout_seconds: int = 5) -> list[dict[str, object]]:
                return self.updates

            def send_message(self, *, chat_id: str, text: str) -> dict[str, object]:
                self.sent_messages.append({"chat_id": chat_id, "text": text})
                return {"ok": True}

            def send_document(
                self,
                *,
                chat_id: str,
                document_bytes: bytes,
                filename: str,
                caption: str | None = None,
                mime_type: str | None = None,
            ) -> dict[str, object]:
                self.sent_documents.append(
                    {
                        "chat_id": chat_id,
                        "document_bytes": document_bytes,
                        "filename": filename,
                        "caption": caption,
                        "mime_type": mime_type,
                    }
                )
                return {"ok": True}

            def send_voice(
                self,
                *,
                chat_id: str,
                voice_bytes: bytes,
                filename: str,
                caption: str | None = None,
                mime_type: str | None = None,
            ) -> dict[str, object]:
                self.sent_voices.append(
                    {
                        "chat_id": chat_id,
                        "voice_bytes": voice_bytes,
                        "filename": filename,
                        "caption": caption,
                        "mime_type": mime_type,
                    }
                )
                return {"ok": True}

        client = FakeVoicePollingClient(
            [
                make_telegram_update(
                    update_id=11832,
                    user_id="111",
                    username="alice",
                    text="/swarm status",
                )
            ]
        )

        with patch(
            "spark_intelligence.adapters.telegram.runtime.run_first_chip_hook_supporting",
            return_value=SimpleNamespace(
                ok=True,
                chip_key="domain-chip-voice-comms",
                stdout="",
                stderr="",
                output={
                    "result": {
                        "audio_base64": base64.b64encode(b"fake-runtime-audio").decode("ascii"),
                        "mime_type": "audio/ogg",
                        "filename": "telegram-reply-runtime.ogg",
                        "voice_compatible": True,
                        "provider_id": "elevenlabs",
                        "voice_id": "spark-core",
                    }
                },
            ),
        ):
            result = poll_telegram_updates_once(
                config_manager=self.config_manager,
                state_db=self.state_db,
                client=client,
                timeout_seconds=0,
            )

        self.assertEqual(result.processed_count, 1)
        self.assertEqual(len(client.sent_voices), 1)
        self.assertEqual(len(client.sent_documents), 0)
        self.assertEqual(len(client.sent_messages), 0)
        self.assertIn("Swarm is", str(client.sent_voices[0]["caption"]))

    def test_voice_message_uses_transcript_as_runtime_command(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        class FakeVoiceClient:
            def get_file(self, *, file_id: str) -> dict[str, object]:
                return {"file_path": "voice/file_1.ogg"}

            def download_file(self, *, file_path: str) -> bytes:
                return b"fake-ogg-bytes"

        def fake_voice_hook(_config_manager, *, hook: str, payload: dict[str, object]):
            if hook == "voice.transcribe":
                self.assertEqual(payload["message_kind"], "voice")
                self.assertEqual(
                    payload["builder_env_file_path"],
                    str(self.config_manager.paths.env_file.resolve()),
                )
                return SimpleNamespace(
                    ok=True,
                    chip_key="domain-chip-voice-comms",
                    stdout="/voice plan",
                    stderr="",
                    output={"result": {"transcript_text": "/voice plan", "provider_id": "openai", "model": "whisper-1"}},
                )
            if hook == "voice.plan":
                return SimpleNamespace(
                    ok=True,
                    chip_key="domain-chip-voice-comms",
                    stdout="voice plan ready",
                    stderr="",
                    output={
                        "result": {
                            "reply_text": (
                                "Telegram voice plan:\n"
                                "1. transcribe Telegram voice/audio through `domain-chip-voice-comms`.\n"
                                "2. route the transcript through the same Builder Telegram runtime and saved persona.\n"
                                "3. add optional voice reply synthesis as a second hook without bloating Builder.\n"
                                "Next: attach the chip, validate `voice.status`, then dogfood real Telegram voice notes."
                            )
                        }
                    },
                )
            raise AssertionError(f"Unexpected voice hook: {hook}")

        with patch(
            "spark_intelligence.adapters.telegram.runtime.run_first_chip_hook_supporting",
            side_effect=fake_voice_hook,
        ):
            result = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=1182,
                    user_id="111",
                    username="alice",
                    text=None,
                    voice={"file_id": "voice-1", "duration": 3, "mime_type": "audio/ogg"},
                ),
                client=FakeVoiceClient(),
            )

        self.assertTrue(result.ok)
        self.assertEqual(result.detail["message_kind"], "voice")
        self.assertEqual(result.detail["transcript_text"], "/voice plan")
        self.assertIn("Telegram voice plan:", result.detail["response_text"])

    def test_voice_message_bridge_returns_voice_media_for_live_bot_delivery(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"], bot_token="test-token")

        class FakeVoiceClient:
            def get_file(self, *, file_id: str) -> dict[str, object]:
                return {"file_path": "voice/file_bridge.ogg"}

            def download_file(self, *, file_path: str) -> bytes:
                return b"fake-ogg-bytes"

        def fake_voice_hook(_config_manager, *, hook: str, payload: dict[str, object]):
            if hook == "voice.transcribe":
                return SimpleNamespace(
                    ok=True,
                    chip_key="spark-voice-comms",
                    stdout="how calibrated is voice",
                    stderr="",
                    output={
                        "result": {
                            "transcript_text": "how calibrated is voice",
                            "provider_id": "local_faster_whisper",
                            "model": "tiny",
                            "mode": "local_faster_whisper",
                        }
                    },
                )
            if hook == "voice.speak":
                self.assertIn("Voice is calibrated", str(payload["text"]))
                return SimpleNamespace(
                    ok=True,
                    chip_key="spark-voice-comms",
                    stdout="",
                    stderr="",
                    output={
                        "result": {
                            "audio_base64": base64.b64encode(b"fake-live-voice").decode("ascii"),
                            "mime_type": "audio/ogg",
                            "filename": "telegram-reply-live.ogg",
                            "voice_compatible": True,
                            "provider_id": "openai-realtime",
                            "voice_id": "sage",
                        }
                    },
                )
            raise AssertionError(f"Unexpected voice hook: {hook}")

        with patch(
            "spark_intelligence.adapters.telegram.runtime.run_first_chip_hook_supporting",
            side_effect=fake_voice_hook,
        ), patch(
            "spark_intelligence.adapters.telegram.runtime.build_researcher_reply",
            return_value=ResearcherBridgeResult(
                request_id="telegram:11821",
                reply_text="Voice is calibrated enough for a first live pass.",
                evidence_summary="",
                escalation_hint=None,
                mode="provider_fallback_chat",
                routing_decision="provider_fallback_chat",
                trace_ref="trace:test-voice-bridge",
                runtime_root=None,
                config_path=None,
                attachment_context=None,
            ),
        ):
            result = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=11821,
                    user_id="111",
                    username="alice",
                    text=None,
                    voice={"file_id": "voice-bridge", "duration": 3, "mime_type": "audio/ogg"},
                ),
                client=FakeVoiceClient(),
                simulation=False,
            )

        self.assertTrue(result.ok)
        self.assertEqual(result.detail["transcript_text"], "how calibrated is voice")
        self.assertEqual(result.detail["voice_media"]["provider_id"], "openai-realtime")
        self.assertEqual(result.detail["voice_media"]["audio_base64"], base64.b64encode(b"fake-live-voice").decode("ascii"))

    def test_voice_message_poll_trace_records_bounded_transcript_telemetry(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"], bot_token="test-token")

        class FakeVoicePollingClient:
            def __init__(self, updates: list[dict[str, object]]) -> None:
                self.updates = updates
                self.sent_messages: list[dict[str, object]] = []

            def get_updates(self, *, offset: int | None = None, timeout_seconds: int = 5) -> list[dict[str, object]]:
                return self.updates

            def get_file(self, *, file_id: str) -> dict[str, object]:
                return {"file_path": "voice/file_telemetry.ogg"}

            def download_file(self, *, file_path: str) -> bytes:
                return b"fake-ogg-bytes"

            def send_message(self, *, chat_id: str, text: str) -> dict[str, object]:
                self.sent_messages.append({"chat_id": chat_id, "text": text})
                return {"ok": True}

        client = FakeVoicePollingClient(
            [
                make_telegram_update(
                    update_id=11841,
                    user_id="111",
                    username="alice",
                    text=None,
                    voice={"file_id": "voice-telemetry", "duration": 3, "mime_type": "audio/ogg"},
                )
            ]
        )

        def fake_voice_hook(_config_manager, *, hook: str, payload: dict[str, object]):
            if hook == "voice.transcribe":
                return SimpleNamespace(
                    ok=True,
                    chip_key="domain-chip-voice-comms",
                    stdout="hello there from voice",
                    stderr="",
                    output={
                        "result": {
                            "transcript_text": "hello there from voice",
                            "provider_id": "local_faster_whisper",
                            "model": "tiny",
                            "mode": "local_faster_whisper",
                        }
                    },
                )
            raise AssertionError(f"Unexpected voice hook: {hook}")

        with patch(
            "spark_intelligence.adapters.telegram.runtime.run_first_chip_hook_supporting",
            side_effect=fake_voice_hook,
        ), patch(
            "spark_intelligence.adapters.telegram.runtime.build_researcher_reply",
            return_value=ResearcherBridgeResult(
                request_id="telegram:11841",
                reply_text="Hey. What's on your mind?",
                evidence_summary="",
                escalation_hint=None,
                mode="provider_fallback_chat",
                routing_decision="provider_fallback_chat",
                trace_ref="trace:test-voice",
                runtime_root=None,
                config_path=None,
                attachment_context=None,
            ),
        ):
            result = poll_telegram_updates_once(
                config_manager=self.config_manager,
                state_db=self.state_db,
                client=client,
                timeout_seconds=0,
            )

        self.assertEqual(result.processed_count, 1)
        self.assertEqual(len(client.sent_messages), 1)

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
        self.assertEqual(processed[0]["voice_transcript_preview"], "hello there from voice")
        self.assertEqual(processed[0]["voice_transcript_length"], len("hello there from voice"))
        self.assertEqual(processed[0]["voice_transcribe_provider_id"], "local_faster_whisper")
        self.assertEqual(processed[0]["voice_transcribe_model"], "tiny")
        self.assertEqual(processed[0]["voice_transcribe_mode"], "local_faster_whisper")

    def test_voice_message_auto_replies_with_audio_without_voice_reply_toggle(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"], bot_token="test-token")

        class FakeVoicePollingClient:
            def __init__(self, updates: list[dict[str, object]]) -> None:
                self.updates = updates
                self.sent_messages: list[dict[str, object]] = []
                self.sent_voices: list[dict[str, object]] = []
                self.sent_documents: list[dict[str, object]] = []

            def get_updates(self, *, offset: int | None = None, timeout_seconds: int = 5) -> list[dict[str, object]]:
                return self.updates

            def get_file(self, *, file_id: str) -> dict[str, object]:
                return {"file_path": "voice/file_auto_reply.ogg"}

            def download_file(self, *, file_path: str) -> bytes:
                return b"fake-ogg-bytes"

            def send_message(self, *, chat_id: str, text: str) -> dict[str, object]:
                self.sent_messages.append({"chat_id": chat_id, "text": text})
                return {"ok": True}

            def send_document(
                self,
                *,
                chat_id: str,
                document_bytes: bytes,
                filename: str,
                caption: str | None = None,
                mime_type: str | None = None,
            ) -> dict[str, object]:
                self.sent_documents.append(
                    {
                        "chat_id": chat_id,
                        "document_bytes": document_bytes,
                        "filename": filename,
                        "caption": caption,
                        "mime_type": mime_type,
                    }
                )
                return {"ok": True}

            def send_voice(
                self,
                *,
                chat_id: str,
                voice_bytes: bytes,
                filename: str,
                caption: str | None = None,
                mime_type: str | None = None,
            ) -> dict[str, object]:
                self.sent_voices.append(
                    {
                        "chat_id": chat_id,
                        "voice_bytes": voice_bytes,
                        "filename": filename,
                        "caption": caption,
                        "mime_type": mime_type,
                    }
                )
                return {"ok": True}

        client = FakeVoicePollingClient(
            [
                make_telegram_update(
                    update_id=11842,
                    user_id="111",
                    username="alice",
                    text=None,
                    voice={"file_id": "voice-auto-reply", "duration": 3, "mime_type": "audio/ogg"},
                )
            ]
        )

        voice_speak_payload: dict[str, object] | None = None

        def fake_voice_hook(_config_manager, *, hook: str, payload: dict[str, object]):
            nonlocal voice_speak_payload
            if hook == "voice.transcribe":
                return SimpleNamespace(
                    ok=True,
                    chip_key="domain-chip-voice-comms",
                    stdout="hello spark how are you",
                    stderr="",
                    output={
                        "result": {
                            "transcript_text": "hello spark how are you",
                            "provider_id": "local_faster_whisper",
                            "model": "tiny",
                            "mode": "local_faster_whisper",
                        }
                    },
                )
            if hook == "voice.speak":
                voice_speak_payload = payload
                return SimpleNamespace(
                    ok=True,
                    chip_key="domain-chip-voice-comms",
                    stdout="",
                    stderr="",
                    output={
                        "result": {
                            "audio_base64": base64.b64encode(b"fake-audio-reply").decode("ascii"),
                            "mime_type": "audio/ogg",
                            "filename": "telegram-reply-auto.ogg",
                            "voice_compatible": True,
                            "provider_id": "elevenlabs",
                            "voice_id": "spark-core",
                        }
                    },
                )
            raise AssertionError(f"Unexpected voice hook: {hook}")

        with patch(
            "spark_intelligence.adapters.telegram.runtime.run_first_chip_hook_supporting",
            side_effect=fake_voice_hook,
        ), patch(
            "spark_intelligence.adapters.telegram.runtime.build_researcher_reply",
            return_value=ResearcherBridgeResult(
                request_id="telegram:11842",
                reply_text=(
                    "Swarm: recommended for this task because the bridge detected a hosted execution path.\n"
                    "Hey, doing well -- ready to work. What's on your mind?"
                ),
                evidence_summary="",
                escalation_hint=None,
                trace_ref="trace:test-voice-auto",
                mode="provider_fallback_chat",
                runtime_root=None,
                config_path=None,
                attachment_context=None,
                routing_decision="provider_fallback_chat",
            ),
        ):
            result = poll_telegram_updates_once(
                config_manager=self.config_manager,
                state_db=self.state_db,
                client=client,
                timeout_seconds=0,
            )

        self.assertEqual(result.processed_count, 1)
        self.assertEqual(len(client.sent_voices), 1)
        self.assertEqual(len(client.sent_documents), 0)
        self.assertEqual(len(client.sent_messages), 0)
        self.assertEqual(str(client.sent_voices[0]["caption"]), "Hey, doing well -- ready to work.")
        self.assertIsNotNone(voice_speak_payload)
        assert voice_speak_payload is not None
        self.assertEqual(str(voice_speak_payload["text"]), "Hey, doing well, ready to work.")

    def test_prepare_voice_reply_text_makes_builder_replies_more_spoken(self) -> None:
        text = (
            "Here is the plan:\n"
            "- Keep the sentences short\n"
            "- Avoid stacked clauses\n"
            "Next: ask me if you want more detail"
        )

        prepared = _prepare_voice_reply_text(text)

        self.assertEqual(prepared, "Here is the plan. Keep the sentences short Avoid stacked clauses.")

    def test_prepare_voice_reply_text_allows_longer_spoken_replies_before_truncating(self) -> None:
        sentence = "This is a short sentence for voice playback."
        text = " ".join([sentence] * 10)

        prepared = _prepare_voice_reply_text(text)

        self.assertEqual(prepared, text)

    def test_voice_message_returns_bounded_transcription_unavailable_reply_for_unsupported_provider(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        class FakeVoiceClient:
            def get_file(self, *, file_id: str) -> dict[str, object]:
                return {"file_path": "voice/file_1.ogg"}

            def download_file(self, *, file_path: str) -> bytes:
                return b"fake-ogg-bytes"

        with patch(
            "spark_intelligence.adapters.telegram.runtime.run_first_chip_hook_supporting",
            return_value=SimpleNamespace(
                ok=False,
                chip_key="domain-chip-voice-comms",
                stdout="",
                stderr="",
                output={"error": "Active provider 'anthropic' does not support direct voice transcription in this runtime."},
            ),
        ):
            result = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=1183,
                    user_id="111",
                    username="alice",
                    text=None,
                    voice={"file_id": "voice-2", "duration": 3, "mime_type": "audio/ogg"},
                ),
                client=FakeVoiceClient(),
            )

        self.assertTrue(result.ok)
        self.assertIn("Voice transcription is unavailable right now.", result.detail["response_text"])
        self.assertIn("Active provider 'anthropic' does not support direct voice transcription", result.detail["response_text"])

    def test_voice_message_returns_bounded_transcription_unavailable_reply_without_bot_token(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        result = simulate_telegram_update(
            config_manager=self.config_manager,
            state_db=self.state_db,
            update_payload=make_telegram_update(
                update_id=119,
                user_id="111",
                username="alice",
                text=None,
                voice={"file_id": "voice-1", "duration": 3},
            ),
        )

        self.assertTrue(result.ok)
        self.assertIn("Voice transcription is unavailable right now.", result.detail["response_text"])
        self.assertIn("Telegram bot token is not available", result.detail["response_text"])
