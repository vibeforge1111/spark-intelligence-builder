from __future__ import annotations

import json

from spark_intelligence.identity.service import (
    approve_pairing,
    link_spark_swarm_agent,
    read_canonical_agent_state,
    rename_agent_identity,
    resolve_inbound_dm,
)
from spark_intelligence.personality.loader import (
    detect_and_persist_nl_preferences,
    load_personality_profile,
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
        self.assertEqual(state.agent_name, "Alice")
        self.assertEqual(state.preferred_source, "builder_local")
        self.assertEqual(state.status, "active")
        self.assertEqual(state.alias_agent_ids, [])

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
        self.assertEqual(row["old_name"], "Alice")
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
