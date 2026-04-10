from __future__ import annotations

import json

from spark_intelligence.identity.service import (
    approve_pairing,
    build_spark_swarm_identity_import_payload,
    link_identity_alias,
    link_spark_swarm_agent,
    normalize_spark_swarm_identity_import,
    read_canonical_agent_state,
    rename_agent_identity,
    resolve_inbound_dm,
)
from spark_intelligence.personality.loader import (
    apply_telegram_surface_persona,
    build_telegram_persona_reply_contract,
    build_telegram_surface_identity_preamble,
    build_personality_system_directive,
    build_personality_import_payload,
    detect_and_persist_agent_persona_preferences,
    detect_and_persist_nl_preferences,
    load_personality_profile,
    migrate_legacy_human_personality_to_agent_persona,
    normalize_personality_import,
    resolve_builder_persona_agent_id,
    save_agent_persona_profile,
)
from spark_intelligence.researcher_bridge.advisory import build_researcher_reply

from tests.test_support import SparkTestCase


class AgentIdentityContractTests(SparkTestCase):
    def test_builder_local_pairing_creates_canonical_local_agent_identity(self) -> None:
        approve_pairing(
            state_db=self.state_db,
            channel_id="telegram",
            external_user_id="111",
            display_name="Alice",
        )

        state = read_canonical_agent_state(
            state_db=self.state_db,
            human_id="human:telegram:111",
        )

        self.assertEqual(state.agent_id, "agent:human:telegram:111")
        # After Finding G fix: the agent is created with an empty name on
        # pairing. The user supplies a name via onboarding / rename_agent_identity.
        self.assertEqual(state.agent_name, "")
        self.assertFalse(state.has_user_defined_name)
        self.assertEqual(state.preferred_source, "builder_local")
        self.assertEqual(state.status, "active")
        self.assertEqual(state.alias_agent_ids, [])

    def test_build_spark_swarm_identity_import_payload_includes_current_identity_and_pairings(self) -> None:
        self.add_telegram_channel()
        approve_pairing(
            state_db=self.state_db,
            channel_id="telegram",
            external_user_id="111",
            display_name="Alice",
        )

        payload = build_spark_swarm_identity_import_payload(
            state_db=self.state_db,
            human_id="human:telegram:111",
            workspace_id="ws-test",
        )

        self.assertEqual(payload["schema_version"], "spark-swarm-agent-import-request.v1")
        self.assertEqual(payload["hook"], "identity")
        self.assertEqual(payload["workspace_id"], "ws-test")
        self.assertEqual(payload["human_id"], "human:telegram:111")
        self.assertEqual(payload["current_identity"]["agent_id"], "agent:human:telegram:111")
        self.assertEqual(payload["sessions"][0]["session_id"], "session:telegram:dm:111")
        self.assertEqual(payload["pairings"][0]["status"], "approved")

    def test_normalize_spark_swarm_identity_import_validates_expected_result_shape(self) -> None:
        normalized = normalize_spark_swarm_identity_import(
            human_id="human:telegram:111",
            hook_output={
                "result": {
                    "human_id": "human:telegram:111",
                    "external_system": "spark_swarm",
                    "swarm_agent_id": "swarm-agent:atlas",
                    "agent_name": "Atlas",
                    "confirmed_at": "2026-03-28T12:00:00+00:00",
                    "metadata": {"workspace_id": "ws-test"},
                }
            },
        )

        self.assertEqual(normalized["swarm_agent_id"], "swarm-agent:atlas")
        self.assertEqual(normalized["agent_name"], "Atlas")
        self.assertEqual(normalized["confirmed_at"], "2026-03-28T12:00:00+00:00")
        self.assertEqual(normalized["metadata"]["workspace_id"], "ws-test")

    def test_build_personality_import_payload_includes_profile_and_history(self) -> None:
        self.add_telegram_channel()
        approve_pairing(
            state_db=self.state_db,
            channel_id="telegram",
            external_user_id="111",
            display_name="Alice",
        )
        agent_state = rename_agent_identity(
            state_db=self.state_db,
            human_id="human:telegram:111",
            new_name="Atlas",
            source_surface="telegram",
            source_ref="test-setup",
        )
        save_agent_persona_profile(
            agent_id=agent_state.agent_id,
            human_id="human:telegram:111",
            state_db=self.state_db,
            base_traits={"warmth": 0.6, "directness": 0.7, "playfulness": 0.4, "pacing": 0.5, "assertiveness": 0.7},
            persona_name="Atlas",
            persona_summary="Direct and calm.",
        )

        payload = build_personality_import_payload(
            human_id="human:telegram:111",
            agent_id=agent_state.agent_id,
            state_db=self.state_db,
            config_manager=self.config_manager,
        )

        self.assertEqual(payload["schema_version"], "spark-personality-import-request.v1")
        self.assertEqual(payload["hook"], "personality")
        self.assertEqual(payload["human_id"], "human:telegram:111")
        self.assertEqual(payload["agent_id"], agent_state.agent_id)
        self.assertEqual(payload["identity"]["agent_name"], "Atlas")
        self.assertEqual(payload["current_agent_persona"]["persona_name"], "Atlas")

    def test_normalize_personality_import_validates_expected_result_shape(self) -> None:
        result = normalize_personality_import(
            human_id="human:telegram:111",
            agent_id="agent:human:telegram:111",
            hook_output={
                "result": {
                    "human_id": "human:telegram:111",
                    "agent_id": "agent:human:telegram:111",
                    "persona_name": "Founder Operator",
                    "persona_summary": "Direct, calm, low-fluff.",
                    "base_traits": {
                        "warmth": 0.46,
                        "directness": 0.82,
                        "playfulness": 0.18,
                        "pacing": 0.63,
                        "assertiveness": 0.79,
                    },
                    "behavioral_rules": ["Push toward execution."],
                    "evolver_state": {
                        "traits": {
                            "warmth": 0.46,
                            "directness": 0.82,
                            "playfulness": 0.18,
                            "pacing": 0.63,
                            "assertiveness": 0.79,
                        },
                        "last_signals": {
                            "personality_id": "founder_operator",
                            "personality_name": "Founder Operator",
                        },
                    },
                }
            },
        )

        self.assertEqual(result.persona_name, "Founder Operator")
        self.assertEqual(result.base_traits["directness"], 0.82)
        self.assertEqual(result.behavioral_rules, ["Push toward execution."])
        self.assertEqual(result.evolver_state["last_signals"]["personality_id"], "founder_operator")

    def test_build_telegram_surface_identity_preamble_uses_agent_name_for_runtime_and_welcome(self) -> None:
        profile = {
            "traits": {
                "warmth": 0.72,
                "directness": 0.82,
                "playfulness": 0.2,
                "pacing": 0.68,
                "assertiveness": 0.77,
            },
            "agent_persona_name": "Atlas",
        }

        runtime_preamble = build_telegram_surface_identity_preamble(
            profile=profile,
            agent_name="Atlas",
            surface="runtime_command",
        )
        welcome_preamble = build_telegram_surface_identity_preamble(
            profile=profile,
            agent_name="Atlas",
            surface="approval_welcome",
        )

        self.assertEqual(runtime_preamble, "")
        self.assertEqual(welcome_preamble, "Pairing approved. Atlas is live in this Telegram DM now.")

    def test_apply_telegram_surface_persona_keeps_deterministic_runtime_reply_unprefixed(self) -> None:
        profile = {
            "traits": {
                "warmth": 0.64,
                "directness": 0.61,
                "playfulness": 0.18,
                "pacing": 0.58,
                "assertiveness": 0.74,
            },
            "agent_persona_name": "Atlas",
        }

        styled = apply_telegram_surface_persona(
            reply_text="Swarm is ready.\nAuth: configured.",
            profile=profile,
            agent_name="Atlas",
            surface="runtime_command",
        )

        self.assertEqual(styled, "Swarm is ready.\nAuth: configured.")

    def test_rename_agent_identity_changes_name_without_changing_agent_id(self) -> None:
        approve_pairing(
            state_db=self.state_db,
            channel_id="telegram",
            external_user_id="111",
            display_name="Alice",
        )

        renamed = rename_agent_identity(
            state_db=self.state_db,
            human_id="human:telegram:111",
            new_name="Atlas",
            source_surface="telegram",
            source_ref="turn-rename",
        )

        self.assertEqual(renamed.agent_id, "agent:human:telegram:111")
        self.assertEqual(renamed.agent_name, "Atlas")
        with self.state_db.connect() as conn:
            row = conn.execute(
                """
                SELECT old_name, new_name, source_surface, source_ref
                FROM agent_rename_history
                WHERE human_id = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                ("human:telegram:111",),
            ).fetchone()
        self.assertIsNotNone(row)
        # Under Finding G fix: approve_pairing creates the agent with an
        # empty name, so the first rename records old_name as empty string.
        self.assertEqual(row["old_name"], "")
        self.assertEqual(row["new_name"], "Atlas")
        self.assertEqual(row["source_surface"], "telegram")
        self.assertEqual(row["source_ref"], "turn-rename")

    def test_load_personality_profile_merges_agent_base_traits_and_human_overlay(self) -> None:
        approve_pairing(
            state_db=self.state_db,
            channel_id="telegram",
            external_user_id="111",
            display_name="Alice",
        )
        agent_state = read_canonical_agent_state(
            state_db=self.state_db,
            human_id="human:telegram:111",
        )

        save_agent_persona_profile(
            agent_id=agent_state.agent_id,
            human_id="human:telegram:111",
            state_db=self.state_db,
            base_traits={
                "warmth": 0.55,
                "directness": 0.55,
                "playfulness": 0.40,
                "pacing": 0.50,
                "assertiveness": 0.55,
            },
            persona_name="Founder Operator",
            persona_summary="Direct, calm, low-fluff.",
            source_surface="operator",
            source_ref="seed-agent-persona",
        )
        detect_and_persist_nl_preferences(
            human_id="human:telegram:111",
            user_message="be more direct and stop hedging",
            state_db=self.state_db,
        )

        profile = load_personality_profile(
            human_id="human:telegram:111",
            agent_id=agent_state.agent_id,
            state_db=self.state_db,
            config_manager=self.config_manager,
        )

        assert profile is not None
        self.assertTrue(profile["agent_persona_applied"])
        self.assertTrue(profile["user_deltas_applied"])
        self.assertEqual(profile["agent_persona_name"], "Founder Operator")
        self.assertAlmostEqual(profile["traits"]["warmth"], 0.55)
        self.assertAlmostEqual(profile["traits"]["directness"], 1.0)
        self.assertAlmostEqual(profile["traits"]["assertiveness"], 0.95)
        self.assertEqual(profile["agent_behavioral_rules"], [])

    def test_explicit_style_message_persists_saved_behavioral_rules(self) -> None:
        approve_pairing(
            state_db=self.state_db,
            channel_id="telegram",
            external_user_id="111",
            display_name="Alice",
        )
        agent_state = read_canonical_agent_state(
            state_db=self.state_db,
            human_id="human:telegram:111",
        )

        mutation = detect_and_persist_agent_persona_preferences(
            agent_id=agent_state.agent_id,
            human_id="human:telegram:111",
            user_message=(
                "Be a little more casual and human.\n"
                "Keep replies shorter unless I ask for depth.\n"
                "Avoid long monologues on simple questions.\n"
                "Ask one good follow-up question when it helps."
            ),
            state_db=self.state_db,
            source_surface="telegram",
            source_ref="turn-style-rules",
        )

        self.assertIsNotNone(mutation)
        assert mutation is not None
        self.assertGreaterEqual(len(mutation.behavioral_rules), 3)
        self.assertIn("Keep replies shorter unless I ask for depth", mutation.behavioral_rules)

        profile = load_personality_profile(
            human_id="human:telegram:111",
            agent_id=agent_state.agent_id,
            state_db=self.state_db,
            config_manager=self.config_manager,
        )

        assert profile is not None
        self.assertIn("Keep replies shorter unless I ask for depth", profile["agent_behavioral_rules"])
        self.assertTrue(profile["agent_persona_applied"])

    def test_build_personality_system_directive_includes_saved_behavioral_rules(self) -> None:
        profile = {
            "traits": {
                "warmth": 0.7,
                "directness": 0.8,
                "playfulness": 0.5,
                "pacing": 0.7,
                "assertiveness": 0.6,
            },
            "style_labels": {
                "warmth": "warm",
                "directness": "very direct",
                "playfulness": "balanced playfulness",
                "pacing": "brisk",
                "assertiveness": "assertive",
            },
            "agent_persona_name": "Founder Operator",
            "agent_persona_summary": "Sharp, concise, decision-oriented",
            "agent_behavioral_rules": [
                "Keep replies shorter unless asked for depth",
                "Avoid generic explainers on broad ideas",
                "Identify the key split before giving advice",
            ],
            "agent_persona_applied": True,
            "user_deltas_applied": False,
        }

        directive = build_personality_system_directive(profile)

        self.assertIn("Your agent persona is 'Founder Operator'.", directive)
        self.assertIn("Saved persona summary: Sharp, concise, decision-oriented.", directive)
        self.assertIn("Keep replies shorter unless asked for depth.", directive)
        self.assertIn("Identify the key split before giving advice.", directive)

    def test_build_telegram_persona_reply_contract_prioritizes_visible_dm_style(self) -> None:
        profile = {
            "traits": {
                "warmth": 0.7,
                "directness": 0.8,
                "playfulness": 0.2,
                "pacing": 0.75,
                "assertiveness": 0.7,
            },
            "agent_persona_name": "Founder Operator",
            "agent_persona_summary": "Sharp, concise, decision-oriented",
            "agent_behavioral_rules": [
                "Keep replies shorter unless asked for depth",
                "Avoid generic explainers on broad ideas",
                "Identify the key split before giving advice",
            ],
        }

        contract = build_telegram_persona_reply_contract(profile)

        self.assertIn("visible Telegram reply", contract)
        self.assertIn("steady voice of 'Founder Operator'", contract)
        self.assertIn("Core stance: Sharp, concise, decision-oriented.", contract)
        self.assertIn("Lead with the answer, recommendation, or key split in the first sentence.", contract)
        self.assertIn("Sound human and present, not sterile or robotic.", contract)
        self.assertIn("Do not force humor, banter, or performative enthusiasm.", contract)
        self.assertIn("Treat Telegram like an ongoing 1:1 conversation", contract)
        self.assertIn("Do not fall back to generic check-in questions", contract)
        self.assertIn("ask at most one specific question tied to the user's last message", contract)
        self.assertIn("Honor these saved Telegram reply rules", contract)

    def test_apply_telegram_surface_persona_rewrites_generic_check_in_reply(self) -> None:
        profile = {
            "traits": {
                "warmth": 0.64,
                "directness": 0.61,
                "playfulness": 0.18,
                "pacing": 0.58,
                "assertiveness": 0.74,
            },
            "agent_persona_name": "Atlas",
        }

        styled = apply_telegram_surface_persona(
            reply_text="Hey! What's on your mind?",
            profile=profile,
            agent_name="Atlas",
            surface="chat",
        )

        self.assertEqual(styled, "Ready when you are.")

    def test_swarm_link_canonicalizes_local_agent_and_rebinds_active_session(self) -> None:
        self.add_telegram_channel()
        approve_pairing(
            state_db=self.state_db,
            channel_id="telegram",
            external_user_id="111",
            display_name="Alice",
        )

        linked = link_spark_swarm_agent(
            state_db=self.state_db,
            human_id="human:telegram:111",
            swarm_agent_id="swarm-agent:atlas",
            agent_name="Atlas",
            metadata={"workspace_id": "ws-test"},
        )

        self.assertEqual(linked.agent_id, "swarm-agent:atlas")
        self.assertEqual(linked.preferred_source, "spark_swarm")
        self.assertEqual(linked.status, "active")
        self.assertEqual(linked.alias_agent_ids, ["agent:human:telegram:111"])

        with self.state_db.connect() as conn:
            alias_row = conn.execute(
                """
                SELECT canonical_agent_id, alias_kind, reason_code
                FROM agent_identity_aliases
                WHERE alias_agent_id = ?
                LIMIT 1
                """,
                ("agent:human:telegram:111",),
            ).fetchone()
            session_row = conn.execute(
                """
                SELECT agent_id
                FROM session_bindings
                WHERE session_id = ?
                LIMIT 1
                """,
                ("session:telegram:dm:111",),
            ).fetchone()
        self.assertIsNotNone(alias_row)
        self.assertEqual(alias_row["canonical_agent_id"], "swarm-agent:atlas")
        self.assertEqual(alias_row["alias_kind"], "superseded_local")
        self.assertEqual(alias_row["reason_code"], "spark_swarm_link")
        self.assertIsNotNone(session_row)
        self.assertEqual(session_row["agent_id"], "swarm-agent:atlas")

        resolution = resolve_inbound_dm(
            state_db=self.state_db,
            channel_id="telegram",
            external_user_id="111",
            display_name="Alice",
        )

        self.assertTrue(resolution.allowed)
        self.assertEqual(resolution.agent_id, "swarm-agent:atlas")

    def test_second_swarm_agent_id_marks_identity_conflict_without_rebinding_active_session(self) -> None:
        self.add_telegram_channel()
        approve_pairing(
            state_db=self.state_db,
            channel_id="telegram",
            external_user_id="111",
            display_name="Alice",
        )

        first_link = link_spark_swarm_agent(
            state_db=self.state_db,
            human_id="human:telegram:111",
            swarm_agent_id="swarm-agent:atlas",
            agent_name="Atlas",
            metadata={"workspace_id": "ws-test"},
        )
        self.assertEqual(first_link.agent_id, "swarm-agent:atlas")

        conflicted = link_spark_swarm_agent(
            state_db=self.state_db,
            human_id="human:telegram:111",
            swarm_agent_id="swarm-agent:zephyr",
            agent_name="Zephyr",
            metadata={"workspace_id": "ws-test"},
        )

        self.assertEqual(conflicted.agent_id, "swarm-agent:zephyr")
        self.assertEqual(conflicted.preferred_source, "spark_swarm")
        self.assertEqual(conflicted.status, "identity_conflict")
        self.assertEqual(conflicted.conflict_agent_id, "swarm-agent:atlas")
        self.assertEqual(conflicted.conflict_reason, "multiple_agent_ids_for_human")

        with self.state_db.connect() as conn:
            session_row = conn.execute(
                """
                SELECT agent_id
                FROM session_bindings
                WHERE session_id = ?
                LIMIT 1
                """,
                ("session:telegram:dm:111",),
            ).fetchone()

        self.assertIsNotNone(session_row)
        self.assertEqual(session_row["agent_id"], "swarm-agent:atlas")

        resolution = resolve_inbound_dm(
            state_db=self.state_db,
            channel_id="telegram",
            external_user_id="111",
            display_name="Alice",
        )

        self.assertTrue(resolution.allowed)
        self.assertEqual(resolution.agent_id, "swarm-agent:atlas")

    def test_link_identity_alias_repoints_existing_session_binding_for_alias_surface(self) -> None:
        approve_pairing(
            state_db=self.state_db,
            channel_id="tui",
            external_user_id="local-operator",
            display_name="Operator",
        )

        with self.state_db.connect() as conn:
            row = conn.execute(
                """
                SELECT agent_id
                FROM session_bindings
                WHERE session_id = ?
                LIMIT 1
                """,
                ("session:tui:dm:local-operator",),
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["agent_id"], "agent:human:tui:local-operator")

        alias = link_identity_alias(
            state_db=self.state_db,
            primary_channel="telegram",
            primary_external_user="8319079055",
            alias_channel="tui",
            alias_external_user="local-operator",
            created_by="test-suite",
        )

        self.assertEqual(alias.primary_human_id, "human:telegram:8319079055")
        self.assertEqual(alias.primary_agent_id, "agent:human:telegram:8319079055")

        with self.state_db.connect() as conn:
            row = conn.execute(
                """
                SELECT agent_id
                FROM session_bindings
                WHERE session_id = ?
                LIMIT 1
                """,
                ("session:tui:dm:local-operator",),
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["agent_id"], "agent:human:telegram:8319079055")

    def test_swarm_linked_agent_persona_stays_on_builder_local_agent_id(self) -> None:
        approve_pairing(
            state_db=self.state_db,
            channel_id="telegram",
            external_user_id="111",
            display_name="Alice",
        )
        linked = link_spark_swarm_agent(
            state_db=self.state_db,
            human_id="human:telegram:111",
            swarm_agent_id="swarm-agent:atlas",
            agent_name="Atlas",
            metadata={"workspace_id": "ws-test"},
        )

        persona_profile = save_agent_persona_profile(
            agent_id=linked.agent_id,
            human_id="human:telegram:111",
            state_db=self.state_db,
            base_traits={
                "warmth": 0.55,
                "directness": 0.81,
                "playfulness": 0.22,
                "pacing": 0.61,
                "assertiveness": 0.77,
            },
            persona_name="Founder Operator",
            persona_summary="Direct, calm, low-fluff.",
            source_surface="operator",
            source_ref="seed-agent-persona",
        )

        profile = load_personality_profile(
            human_id="human:telegram:111",
            agent_id=linked.agent_id,
            state_db=self.state_db,
            config_manager=self.config_manager,
        )

        builder_persona_agent_id = resolve_builder_persona_agent_id(human_id="human:telegram:111")
        self.assertEqual(persona_profile["agent_id"], builder_persona_agent_id)
        assert profile is not None
        self.assertEqual(profile["agent_id"], builder_persona_agent_id)
        self.assertTrue(profile["agent_persona_applied"])
        self.assertEqual(profile["agent_persona_name"], "Founder Operator")

        with self.state_db.connect() as conn:
            local_row = conn.execute(
                """
                SELECT persona_name
                FROM agent_persona_profiles
                WHERE agent_id = ?
                LIMIT 1
                """,
                (builder_persona_agent_id,),
            ).fetchone()
            swarm_row = conn.execute(
                """
                SELECT persona_name
                FROM agent_persona_profiles
                WHERE agent_id = ?
                LIMIT 1
                """,
                (linked.agent_id,),
            ).fetchone()

        self.assertIsNotNone(local_row)
        self.assertEqual(local_row["persona_name"], "Founder Operator")
        self.assertIsNone(swarm_row)

    def test_swarm_link_reanchors_legacy_swarm_persona_history_to_builder_local_agent_id(self) -> None:
        approve_pairing(
            state_db=self.state_db,
            channel_id="telegram",
            external_user_id="111",
            display_name="Alice",
        )
        with self.state_db.connect() as conn:
            conn.execute(
                """
                INSERT INTO agent_persona_profiles(
                    agent_id, persona_name, persona_summary, base_traits_json, behavioral_rules_json, provenance_json, updated_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "swarm-agent:atlas",
                    "Founder Operator",
                    "Direct, calm, low-fluff.",
                    json.dumps(
                        {
                            "warmth": 0.55,
                            "directness": 0.81,
                            "playfulness": 0.22,
                            "pacing": 0.61,
                            "assertiveness": 0.77,
                        }
                    ),
                    json.dumps(["Push toward execution."]),
                    json.dumps({"source_ref": "agent import-personality"}),
                    "2026-04-09T08:00:00+00:00",
                    "2026-04-09T08:00:00+00:00",
                ),
            )
            conn.execute(
                """
                INSERT INTO agent_persona_mutations(
                    mutation_id, agent_id, human_id, mutation_kind, delta_traits_json, persona_name, persona_summary, source_surface, source_ref, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "agent-persona-test123",
                    "swarm-agent:atlas",
                    "human:telegram:111",
                    "external_import",
                    json.dumps({"directness": 0.81}, sort_keys=True),
                    "Founder Operator",
                    "Direct, calm, low-fluff.",
                    "agent_cli",
                    "agent import-personality",
                    "2026-04-09T08:00:00+00:00",
                ),
            )
            conn.execute(
                """
                INSERT INTO operator_events(actor_human_id, action, target_kind, target_ref, reason, details_json)
                VALUES (?, 'import_personality', 'agent_persona', ?, ?, ?)
                """,
                (
                    "local-operator",
                    "swarm-agent:atlas",
                    "personality import",
                    json.dumps({"status": "completed", "chip_key": "spark-personality"}, sort_keys=True),
                ),
            )
            conn.commit()

        linked = link_spark_swarm_agent(
            state_db=self.state_db,
            human_id="human:telegram:111",
            swarm_agent_id="swarm-agent:atlas",
            agent_name="Atlas",
            metadata={"workspace_id": "ws-test"},
        )

        profile = load_personality_profile(
            human_id="human:telegram:111",
            agent_id=linked.agent_id,
            state_db=self.state_db,
            config_manager=self.config_manager,
        )
        builder_persona_agent_id = resolve_builder_persona_agent_id(human_id="human:telegram:111")

        assert profile is not None
        self.assertEqual(profile["agent_id"], builder_persona_agent_id)
        self.assertEqual(profile["agent_persona_name"], "Founder Operator")

        with self.state_db.connect() as conn:
            local_row = conn.execute(
                "SELECT persona_name FROM agent_persona_profiles WHERE agent_id = ? LIMIT 1",
                (builder_persona_agent_id,),
            ).fetchone()
            swarm_row = conn.execute(
                "SELECT persona_name FROM agent_persona_profiles WHERE agent_id = ? LIMIT 1",
                ("swarm-agent:atlas",),
            ).fetchone()
            mutation_row = conn.execute(
                "SELECT agent_id FROM agent_persona_mutations WHERE mutation_id = ? LIMIT 1",
                ("agent-persona-test123",),
            ).fetchone()
            operator_row = conn.execute(
                """
                SELECT target_ref
                FROM operator_events
                WHERE action = 'import_personality' AND target_kind = 'agent_persona'
                ORDER BY event_id DESC
                LIMIT 1
                """,
            ).fetchone()

        self.assertIsNotNone(local_row)
        self.assertEqual(local_row["persona_name"], "Founder Operator")
        self.assertIsNone(swarm_row)
        self.assertIsNotNone(mutation_row)
        self.assertEqual(mutation_row["agent_id"], builder_persona_agent_id)
        self.assertIsNotNone(operator_row)
        self.assertEqual(operator_row["target_ref"], builder_persona_agent_id)

    def test_build_researcher_reply_persists_explicit_agent_persona_authoring(self) -> None:
        approve_pairing(
            state_db=self.state_db,
            channel_id="telegram",
            external_user_id="111",
            display_name="Alice",
        )
        agent_state = read_canonical_agent_state(
            state_db=self.state_db,
            human_id="human:telegram:111",
        )

        result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-agent-persona",
            agent_id=agent_state.agent_id,
            human_id="human:telegram:111",
            session_id="session:telegram:dm:111",
            channel_kind="telegram",
            user_message="Your personality should be more direct and more assertive. Your name is Atlas.",
        )

        self.assertTrue(result.reply_text)
        updated_state = read_canonical_agent_state(
            state_db=self.state_db,
            human_id="human:telegram:111",
        )
        self.assertEqual(updated_state.agent_name, "Atlas")

        profile = load_personality_profile(
            human_id="human:telegram:111",
            agent_id=updated_state.agent_id,
            state_db=self.state_db,
            config_manager=self.config_manager,
        )
        assert profile is not None
        self.assertTrue(profile["agent_persona_applied"])
        self.assertGreater(profile["traits"]["directness"], 0.5)
        self.assertGreater(profile["traits"]["assertiveness"], 0.5)

        with self.state_db.connect() as conn:
            trait_row = conn.execute(
                "SELECT COUNT(*) AS c FROM personality_trait_profiles WHERE human_id = ?",
                ("human:telegram:111",),
            ).fetchone()
            agent_row = conn.execute(
                """
                SELECT persona_name, persona_summary, base_traits_json
                FROM agent_persona_profiles
                WHERE agent_id = ?
                LIMIT 1
                """,
                (updated_state.agent_id,),
            ).fetchone()
        self.assertEqual(int(trait_row["c"]), 0)
        self.assertIsNotNone(agent_row)
        self.assertEqual(agent_row["persona_name"], "Atlas")
        base_traits = json.loads(agent_row["base_traits_json"])
        self.assertGreater(base_traits["directness"], 0.5)
        self.assertGreater(base_traits["assertiveness"], 0.5)

    def test_build_researcher_reply_persists_rich_agent_style_rules(self) -> None:
        approve_pairing(
            state_db=self.state_db,
            channel_id="telegram",
            external_user_id="111",
            display_name="Alice",
        )
        agent_state = read_canonical_agent_state(
            state_db=self.state_db,
            human_id="human:telegram:111",
        )

        result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-agent-style-rules",
            agent_id=agent_state.agent_id,
            human_id="human:telegram:111",
            session_id="session:telegram:dm:111",
            channel_kind="telegram",
            user_message=(
                "When I mention an idea, don't default to a generic explainer.\n"
                "First narrow the idea and help me frame the interesting version of it.\n"
                "Prefer crisp thinking over long lists.\n"
                "If the topic is broad, identify the key split first."
            ),
        )

        self.assertTrue(result.reply_text)

        with self.state_db.connect() as conn:
            agent_row = conn.execute(
                """
                SELECT behavioral_rules_json, provenance_json
                FROM agent_persona_profiles
                WHERE agent_id = ?
                LIMIT 1
                """,
                (agent_state.agent_id,),
            ).fetchone()
            trait_row = conn.execute(
                "SELECT COUNT(*) AS c FROM personality_trait_profiles WHERE human_id = ?",
                ("human:telegram:111",),
            ).fetchone()

        self.assertIsNotNone(agent_row)
        behavioral_rules = json.loads(agent_row["behavioral_rules_json"] or "[]")
        provenance = json.loads(agent_row["provenance_json"] or "{}")
        self.assertIn("First narrow the idea and help me frame the interesting version of it", behavioral_rules)
        self.assertIn("If the topic is broad, identify the key split first", behavioral_rules)
        self.assertEqual(provenance.get("source_ref"), "req-agent-style-rules")
        self.assertEqual(int(trait_row["c"]), 0)

    def test_stale_swarm_name_does_not_override_newer_builder_rename(self) -> None:
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
            source_ref="turn-new",
        )

        linked = link_spark_swarm_agent(
            state_db=self.state_db,
            human_id="human:telegram:111",
            swarm_agent_id="swarm-agent:atlas",
            agent_name="Legacy Swarm Name",
            confirmed_at="2026-03-28T00:00:00+00:00",
            metadata={"workspace_id": "ws-test"},
        )

        self.assertEqual(linked.agent_name, "Atlas")
        self.assertEqual(linked.name_source, "telegram")

    def test_fresher_swarm_name_overrides_older_builder_name(self) -> None:
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
            source_ref="turn-old",
        )

        linked = link_spark_swarm_agent(
            state_db=self.state_db,
            human_id="human:telegram:111",
            swarm_agent_id="swarm-agent:atlas",
            agent_name="Swarm Prime",
            confirmed_at="2026-03-29T00:00:00+00:00",
            metadata={"workspace_id": "ws-test"},
        )

        self.assertEqual(linked.agent_name, "Swarm Prime")
        self.assertEqual(linked.name_source, "spark_swarm")

    def test_migrate_legacy_human_personality_to_agent_persona_preserves_effective_profile(self) -> None:
        approve_pairing(
            state_db=self.state_db,
            channel_id="telegram",
            external_user_id="111",
            display_name="Alice",
        )
        agent_state = read_canonical_agent_state(
            state_db=self.state_db,
            human_id="human:telegram:111",
        )
        detect_and_persist_nl_preferences(
            human_id="human:telegram:111",
            user_message="be more direct and stop hedging",
            state_db=self.state_db,
        )
        before = load_personality_profile(
            human_id="human:telegram:111",
            agent_id=agent_state.agent_id,
            state_db=self.state_db,
            config_manager=self.config_manager,
        )

        result = migrate_legacy_human_personality_to_agent_persona(
            human_id="human:telegram:111",
            state_db=self.state_db,
            source_surface="agent_cli",
            source_ref="migration-test",
        )

        self.assertEqual(result.status, "migrated")
        self.assertTrue(result.cleared_overlay)
        after = load_personality_profile(
            human_id="human:telegram:111",
            agent_id=agent_state.agent_id,
            state_db=self.state_db,
            config_manager=self.config_manager,
        )
        assert before is not None
        assert after is not None
        self.assertTrue(after["agent_persona_applied"])
        self.assertFalse(after["user_deltas_applied"])
        self.assertEqual(after["traits"], before["traits"])
