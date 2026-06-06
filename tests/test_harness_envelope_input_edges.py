from __future__ import annotations

from spark_intelligence.harness_runtime import build_harness_task_envelope

from tests.test_support import SparkTestCase, create_fake_researcher_runtime


class HarnessEnvelopeInputEdgeTests(SparkTestCase):
    def _enable_fake_researcher(self) -> None:
        runtime_root = create_fake_researcher_runtime(self.home)
        self.config_manager.set_path("spark.researcher.enabled", True)
        self.config_manager.set_path("spark.researcher.runtime_root", str(runtime_root))

    def test_empty_task_string_is_preserved_on_envelope(self) -> None:
        envelope = build_harness_task_envelope(
            config_manager=self.config_manager,
            state_db=self.state_db,
            task="",
        )
        self.assertEqual(envelope.task, "")
        # The envelope still gets a router-picked harness_id even with empty task input.
        self.assertNotEqual(envelope.harness_id, "")

    def test_whitespace_task_is_stripped(self) -> None:
        envelope = build_harness_task_envelope(
            config_manager=self.config_manager,
            state_db=self.state_db,
            task="   What chips are active?   ",
        )
        self.assertEqual(envelope.task, "What chips are active?")

    def test_envelope_id_has_htask_prefix_and_fixed_suffix_length(self) -> None:
        envelope = build_harness_task_envelope(
            config_manager=self.config_manager,
            state_db=self.state_db,
            task="Explain how the runtime works.",
        )
        self.assertTrue(envelope.envelope_id.startswith("htask:"))
        # The hex suffix is uuid4().hex[:12] — exactly 12 chars after the prefix.
        self.assertEqual(len(envelope.envelope_id), len("htask:") + 12)

    def test_envelope_id_is_unique_per_call(self) -> None:
        env1 = build_harness_task_envelope(
            config_manager=self.config_manager,
            state_db=self.state_db,
            task="Same task text.",
        )
        env2 = build_harness_task_envelope(
            config_manager=self.config_manager,
            state_db=self.state_db,
            task="Same task text.",
        )
        self.assertNotEqual(env1.envelope_id, env2.envelope_id)

    def test_optional_identity_fields_default_to_none(self) -> None:
        envelope = build_harness_task_envelope(
            config_manager=self.config_manager,
            state_db=self.state_db,
            task="Plain question.",
        )
        self.assertIsNone(envelope.channel_kind)
        self.assertIsNone(envelope.session_id)
        self.assertIsNone(envelope.human_id)
        self.assertIsNone(envelope.agent_id)

    def test_optional_identity_fields_are_passed_through(self) -> None:
        envelope = build_harness_task_envelope(
            config_manager=self.config_manager,
            state_db=self.state_db,
            task="Plain question.",
            channel_kind="telegram",
            session_id="session-x",
            human_id="human-x",
            agent_id="agent-x",
        )
        self.assertEqual(envelope.channel_kind, "telegram")
        self.assertEqual(envelope.session_id, "session-x")
        self.assertEqual(envelope.human_id, "human-x")
        self.assertEqual(envelope.agent_id, "agent-x")

    def test_forced_harness_with_surrounding_whitespace_is_normalized(self) -> None:
        self._enable_fake_researcher()
        envelope = build_harness_task_envelope(
            config_manager=self.config_manager,
            state_db=self.state_db,
            task="Explain something.",
            forced_harness_id="  researcher.advisory  ",
        )
        self.assertEqual(envelope.harness_id, "researcher.advisory")
        self.assertEqual(envelope.route_mode, "forced_harness")

    def test_forced_harness_whitespace_only_falls_back_to_router_selection(self) -> None:
        # A whitespace-only forced_harness_id is normalized to "" and should be
        # treated as "no override" rather than as an unknown harness.
        envelope = build_harness_task_envelope(
            config_manager=self.config_manager,
            state_db=self.state_db,
            task="Plain default question.",
            forced_harness_id="   ",
        )
        # Router-picked route_mode should NOT be "forced_harness" in this case.
        self.assertNotEqual(envelope.route_mode, "forced_harness")
