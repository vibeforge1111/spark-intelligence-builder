from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from spark_intelligence.harness_runtime import (
    build_harness_task_envelope,
    execute_harness_chain,
)

from tests.test_support import SparkTestCase, create_fake_researcher_runtime


class HarnessChainBlockPropagationTests(SparkTestCase):
    def _enable_fake_researcher(self) -> None:
        runtime_root = create_fake_researcher_runtime(self.home)
        self.config_manager.set_path("spark.researcher.enabled", True)
        self.config_manager.set_path("spark.researcher.runtime_root", str(runtime_root))

    def test_chain_returns_primary_result_when_no_follow_ups(self) -> None:
        envelope = build_harness_task_envelope(
            config_manager=self.config_manager,
            state_db=self.state_db,
            task="A plain question for builder direct.",
        )
        result = execute_harness_chain(
            config_manager=self.config_manager,
            state_db=self.state_db,
            envelope=envelope,
            follow_up_harness_ids=None,
        )
        self.assertIsNone(result.chain_status)
        self.assertIsNone(result.chained_results)
        self.assertEqual(result.envelope.harness_id, envelope.harness_id)

    def test_chain_returns_primary_result_when_follow_ups_empty(self) -> None:
        envelope = build_harness_task_envelope(
            config_manager=self.config_manager,
            state_db=self.state_db,
            task="A plain question for builder direct.",
        )
        result = execute_harness_chain(
            config_manager=self.config_manager,
            state_db=self.state_db,
            envelope=envelope,
            follow_up_harness_ids=[],
        )
        self.assertIsNone(result.chain_status)
        self.assertIsNone(result.chained_results)

    def test_chain_normalizes_whitespace_only_follow_ups_to_empty(self) -> None:
        envelope = build_harness_task_envelope(
            config_manager=self.config_manager,
            state_db=self.state_db,
            task="A plain question for builder direct.",
        )
        # Whitespace-only entries should be stripped + filtered out, leaving
        # an empty list which short-circuits to the primary-only return.
        result = execute_harness_chain(
            config_manager=self.config_manager,
            state_db=self.state_db,
            envelope=envelope,
            follow_up_harness_ids=["   ", "  \t  "],
        )
        self.assertIsNone(result.chain_status)
        self.assertIsNone(result.chained_results)

    def test_chain_blocks_when_voice_followup_returns_blocked(self) -> None:
        self._enable_fake_researcher()
        envelope = build_harness_task_envelope(
            config_manager=self.config_manager,
            state_db=self.state_db,
            task="Researcher then voice.",
            forced_harness_id="researcher.advisory",
        )

        class FakeResearcherResult:
            reply_text = "Here is the answer."
            evidence_summary = "status=ok"
            trace_ref = "trace:test"
            mode = "external_configured"
            provider_id = "custom"
            provider_model = "MiniMax-M2.7"
            provider_execution_transport = "direct_http"
            routing_decision = "provider_execution"
            active_chip_key = None

        def fake_voice_hook(*, hook, **kwargs):
            # Force the voice harness to land on the not-ready (blocked) branch
            # by reporting voice.status as not-ready.
            return (
                {
                    "result": {
                        "ready": False,
                        "reason": "voice provider offline",
                        "reply_text": "",
                    }
                },
                "domain-chip-voice-comms",
            )

        with (
            patch(
                "spark_intelligence.harness_runtime.service._run_researcher_bridge_reply",
                return_value=FakeResearcherResult(),
            ),
            patch(
                "spark_intelligence.harness_runtime.service._run_voice_hook",
                side_effect=fake_voice_hook,
            ),
        ):
            result = execute_harness_chain(
                config_manager=self.config_manager,
                state_db=self.state_db,
                envelope=envelope,
                follow_up_harness_ids=["voice.io"],
            )

        # Primary completed, follow-up blocked, chain_status reflects that.
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.chain_status, "blocked")
        self.assertEqual(len(result.chained_results or []), 1)
        voice_result = (result.chained_results or [])[0]
        self.assertEqual(voice_result.envelope.harness_id, "voice.io")
        self.assertEqual(voice_result.status, "blocked")

    def test_chain_stops_after_first_non_success_follow_up(self) -> None:
        self._enable_fake_researcher()
        envelope = build_harness_task_envelope(
            config_manager=self.config_manager,
            state_db=self.state_db,
            task="Researcher then voice then voice.",
            forced_harness_id="researcher.advisory",
        )

        class FakeResearcherResult:
            reply_text = "Here is the answer."
            evidence_summary = "status=ok"
            trace_ref = "trace:test"
            mode = "external_configured"
            provider_id = "custom"
            provider_model = "MiniMax-M2.7"
            provider_execution_transport = "direct_http"
            routing_decision = "provider_execution"
            active_chip_key = None

        call_log: list[str] = []

        def fake_voice_hook(*, hook, **kwargs):
            call_log.append(hook)
            return (
                {
                    "result": {
                        "ready": False,
                        "reason": "voice provider offline",
                        "reply_text": "",
                    }
                },
                "domain-chip-voice-comms",
            )

        with (
            patch(
                "spark_intelligence.harness_runtime.service._run_researcher_bridge_reply",
                return_value=FakeResearcherResult(),
            ),
            patch(
                "spark_intelligence.harness_runtime.service._run_voice_hook",
                side_effect=fake_voice_hook,
            ),
        ):
            result = execute_harness_chain(
                config_manager=self.config_manager,
                state_db=self.state_db,
                envelope=envelope,
                # Two voice follow-ups: the second should never run because the first blocks.
                follow_up_harness_ids=["voice.io", "voice.io"],
            )

        self.assertEqual(result.chain_status, "blocked")
        # Only one chained result should appear — the chain stopped after the first block.
        self.assertEqual(len(result.chained_results or []), 1)
