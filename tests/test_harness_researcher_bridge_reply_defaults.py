from __future__ import annotations

from unittest.mock import patch

from spark_intelligence.harness_runtime.service import (
    HarnessTaskEnvelope,
    _run_researcher_bridge_reply,
)

from tests.test_support import SparkTestCase


def _make_envelope(
    *,
    envelope_id: str = "htask:bridge-001",
    task: str = "Explain this directly.",
    channel_kind: str | None = "telegram",
    session_id: str | None = "session-1",
    human_id: str | None = "human-1",
    agent_id: str | None = "agent-1",
) -> HarnessTaskEnvelope:
    return HarnessTaskEnvelope(
        envelope_id=envelope_id,
        task=task,
        harness_id="researcher.advisory",
        owner_system="Spark Researcher",
        backend_kind="provider_bridge",
        session_scope="current_conversation",
        prompt_strategy="ephemeral_contextual_task_through_researcher_bridge",
        route_mode="researcher_advisory",
        required_capabilities=[],
        artifacts_expected=[],
        next_actions=[],
        limitations=[],
        channel_kind=channel_kind,
        session_id=session_id,
        human_id=human_id,
        agent_id=agent_id,
    )


class RunResearcherBridgeReplyDefaultsTests(SparkTestCase):
    def test_explicit_identity_fields_pass_through_unchanged(self) -> None:
        envelope = _make_envelope()
        with patch(
            "spark_intelligence.researcher_bridge.advisory.build_researcher_reply",
            return_value="ok",
        ) as bridge_mock:
            _run_researcher_bridge_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                envelope=envelope,
            )
        bridge_mock.assert_called_once()
        kwargs = bridge_mock.call_args.kwargs
        self.assertEqual(kwargs["request_id"], "htask:bridge-001")
        self.assertEqual(kwargs["agent_id"], "agent-1")
        self.assertEqual(kwargs["human_id"], "human-1")
        self.assertEqual(kwargs["session_id"], "session-1")
        self.assertEqual(kwargs["channel_kind"], "telegram")
        self.assertEqual(kwargs["user_message"], "Explain this directly.")

    def test_missing_agent_id_falls_back_to_agent_builder_local(self) -> None:
        envelope = _make_envelope(agent_id=None)
        with patch(
            "spark_intelligence.researcher_bridge.advisory.build_researcher_reply",
            return_value="ok",
        ) as bridge_mock:
            _run_researcher_bridge_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                envelope=envelope,
            )
        kwargs = bridge_mock.call_args.kwargs
        self.assertEqual(kwargs["agent_id"], "agent:builder-local")

    def test_missing_human_id_falls_back_to_human_local_operator(self) -> None:
        envelope = _make_envelope(human_id=None)
        with patch(
            "spark_intelligence.researcher_bridge.advisory.build_researcher_reply",
            return_value="ok",
        ) as bridge_mock:
            _run_researcher_bridge_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                envelope=envelope,
            )
        kwargs = bridge_mock.call_args.kwargs
        self.assertEqual(kwargs["human_id"], "human:local-operator")

    def test_missing_session_id_falls_back_to_envelope_scoped_session(self) -> None:
        envelope = _make_envelope(envelope_id="htask:no-session", session_id=None)
        with patch(
            "spark_intelligence.researcher_bridge.advisory.build_researcher_reply",
            return_value="ok",
        ) as bridge_mock:
            _run_researcher_bridge_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                envelope=envelope,
            )
        kwargs = bridge_mock.call_args.kwargs
        # Session id falls back to "session:{envelope_id}" so each turn has a stable session key
        self.assertEqual(kwargs["session_id"], "session:htask:no-session")

    def test_missing_channel_kind_falls_back_to_cli(self) -> None:
        envelope = _make_envelope(channel_kind=None)
        with patch(
            "spark_intelligence.researcher_bridge.advisory.build_researcher_reply",
            return_value="ok",
        ) as bridge_mock:
            _run_researcher_bridge_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                envelope=envelope,
            )
        kwargs = bridge_mock.call_args.kwargs
        self.assertEqual(kwargs["channel_kind"], "cli")
