from __future__ import annotations

import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

from spark_intelligence.harness_runtime import (
    build_harness_runtime_snapshot,
    build_harness_task_envelope,
    execute_harness_chain,
    execute_harness_task,
    with_harness_local_operator_turn_intent,
)
from spark_intelligence.observability.store import latest_events_by_type

from tests.test_support import SparkTestCase, create_fake_researcher_runtime


class HarnessRuntimeTests(SparkTestCase):
    def _enable_fake_researcher(self) -> None:
        runtime_root = create_fake_researcher_runtime(self.home)
        self.config_manager.set_path("spark.researcher.enabled", True)
        self.config_manager.set_path("spark.researcher.runtime_root", str(runtime_root))

    def _ready_browser_use_status(self) -> dict[str, object]:
        return {
            "status": "completed",
            "backend_kind": "browser_use_adapter",
            "backend_label": "Browser-use Adapter",
            "adapter_status": "ready",
            "configured": True,
            "package_available": True,
            "evidence_summary": "browser-use adapter status=ready",
        }

    def test_execute_harness_task_emits_failure_event_when_runner_raises(self) -> None:
        self._enable_fake_researcher()
        envelope = build_harness_task_envelope(
            config_manager=self.config_manager,
            state_db=self.state_db,
            task="Draft a direct answer for this operator question.",
            forced_harness_id="researcher.advisory",
        )

        with patch(
            "spark_intelligence.harness_runtime.service._run_researcher_bridge_reply",
            side_effect=RuntimeError("synthetic researcher failure"),
        ):
            with self.assertRaises(RuntimeError):
                execute_harness_task(
                    config_manager=self.config_manager,
                    state_db=self.state_db,
                    envelope=envelope,
                )

        failure_events = latest_events_by_type(
            self.state_db,
            event_type="harness_execution_failed",
            limit=5,
        )
        self.assertTrue(
            failure_events,
            "expected at least one harness_execution_failed event",
        )

    def test_build_harness_runtime_snapshot_honors_limit_and_orders_newest_first(self) -> None:
        for _ in range(3):
            envelope = build_harness_task_envelope(
                config_manager=self.config_manager,
                state_db=self.state_db,
                task="What chips are active right now?",
            )
            execute_harness_task(
                config_manager=self.config_manager,
                state_db=self.state_db,
                envelope=envelope,
            )

        snapshot = build_harness_runtime_snapshot(
            self.config_manager,
            self.state_db,
            limit=2,
        )

        self.assertEqual(len(snapshot.recent_runs), 2)
        self.assertEqual(snapshot.summary["recent_run_count"], 2)
        self.assertEqual(snapshot.summary["last_harness_id"], "builder.direct")
        for run in snapshot.recent_runs:
            self.assertEqual(run["harness_id"], "builder.direct")
            self.assertTrue(run["run_id"].startswith("run"))

    def test_build_harness_runtime_snapshot_tolerates_malformed_summary_json(self) -> None:
        envelope = build_harness_task_envelope(
            config_manager=self.config_manager,
            state_db=self.state_db,
            task="What chips are active right now?",
        )
        execute_harness_task(
            config_manager=self.config_manager,
            state_db=self.state_db,
            envelope=envelope,
        )
        with self.state_db.connect() as conn:
            conn.execute("UPDATE builder_runs SET summary_json = ?", ('{"broken"',))

        snapshot = build_harness_runtime_snapshot(self.config_manager, self.state_db)

        self.assertEqual(snapshot.recent_runs[0]["summary_json"], {})

    def test_build_harness_task_envelope_uses_router_selection(self) -> None:
        self._enable_fake_researcher()
        with patch(
            "spark_intelligence.system_registry.registry.collect_browser_use_adapter_status",
            return_value=self._ready_browser_use_status(),
        ):
            envelope = build_harness_task_envelope(
                config_manager=self.config_manager,
                state_db=self.state_db,
                task="Search https://example.com and inspect the page.",
                session_id="session-1",
                human_id="human-1",
                agent_id="agent-1",
            )

        self.assertEqual(envelope.harness_id, "browser.grounded")
        self.assertEqual(envelope.backend_kind, "browser_use_adapter")
        self.assertEqual(envelope.session_id, "session-1")

    def test_build_harness_runtime_snapshot_returns_safe_defaults_with_no_runs(self) -> None:
        snapshot = build_harness_runtime_snapshot(self.config_manager, self.state_db)

        self.assertEqual(snapshot.recent_runs, [])
        self.assertEqual(snapshot.summary["recent_run_count"], 0)
        self.assertEqual(snapshot.summary["open_run_count"], 0)
        self.assertEqual(snapshot.summary["failed_run_count"], 0)
        self.assertIsNone(snapshot.summary["last_harness_id"])
        self.assertTrue(snapshot.generated_at)
        self.assertTrue(snapshot.workspace_id)

    def test_execute_builder_direct_harness_records_runtime_run(self) -> None:
        envelope = build_harness_task_envelope(
            config_manager=self.config_manager,
            state_db=self.state_db,
            task="What chips are active right now?",
        )
        envelope = with_harness_local_operator_turn_intent(envelope)

        result = execute_harness_task(
            config_manager=self.config_manager,
            state_db=self.state_db,
            envelope=envelope,
        )

        self.assertEqual(result.status, "prepared")
        self.assertEqual(result.envelope.harness_id, "builder.direct")
        self.assertIn("execution_contract", result.artifacts)
        contract = result.artifacts["execution_contract"]
        self.assertEqual(contract["limitations"], envelope.limitations)
        self.assertEqual(contract["artifacts_expected"], envelope.artifacts_expected)

        snapshot = build_harness_runtime_snapshot(self.config_manager, self.state_db)
        self.assertEqual(snapshot.summary["recent_run_count"], 1)
        self.assertEqual(snapshot.summary["last_harness_id"], "builder.direct")

    def test_execute_browser_grounded_harness_prepares_navigate_payload_for_url(self) -> None:
        self._enable_fake_researcher()
        with patch(
            "spark_intelligence.system_registry.registry.collect_browser_use_adapter_status",
            return_value=self._ready_browser_use_status(),
        ):
            envelope = build_harness_task_envelope(
                config_manager=self.config_manager,
                state_db=self.state_db,
                task="Open https://example.com and inspect it.",
            )
        envelope = with_harness_local_operator_turn_intent(envelope)

        result = execute_harness_task(
            config_manager=self.config_manager,
            state_db=self.state_db,
            envelope=envelope,
        )

        self.assertEqual(result.status, "prepared")
        payload = result.artifacts.get("browser_navigate_payload") or {}
        self.assertEqual(payload.get("hook_name"), "browser.navigate")
        self.assertEqual((payload.get("arguments") or {}).get("url"), "https://example.com")

    def test_execute_browser_grounded_harness_strips_terminal_url_punctuation(self) -> None:
        self._enable_fake_researcher()
        with patch(
            "spark_intelligence.system_registry.registry.collect_browser_use_adapter_status",
            return_value=self._ready_browser_use_status(),
        ):
            envelope = build_harness_task_envelope(
                config_manager=self.config_manager,
                state_db=self.state_db,
                task="Open https://example.com/report.json, then summarize it.",
            )
        envelope = with_harness_local_operator_turn_intent(envelope)

        result = execute_harness_task(
            config_manager=self.config_manager,
            state_db=self.state_db,
            envelope=envelope,
        )

        payload = result.artifacts.get("browser_navigate_payload") or {}
        self.assertEqual((payload.get("arguments") or {}).get("url"), "https://example.com/report.json")

    def test_execute_browser_grounded_harness_rejects_non_public_ip_with_bounded_event(self) -> None:
        self._enable_fake_researcher()
        with patch(
            "spark_intelligence.system_registry.registry.collect_browser_use_adapter_status",
            return_value=self._ready_browser_use_status(),
        ):
            envelope = build_harness_task_envelope(
                config_manager=self.config_manager,
                state_db=self.state_db,
                task="Open http://127.0.0.1/admin and inspect it.",
            )
        envelope = with_harness_local_operator_turn_intent(envelope)

        with self.assertRaisesRegex(ValueError, "non-public IP"):
            execute_harness_task(
                config_manager=self.config_manager,
                state_db=self.state_db,
                envelope=envelope,
            )

        events = latest_events_by_type(self.state_db, event_type="harness_validation_failed", limit=5)
        self.assertTrue(events)
        self.assertEqual(events[0]["facts_json"]["error_type"], "ValueError")
        self.assertNotIn("127.0.0.1", str(events[0]))

    def test_execute_browser_grounded_harness_requires_url_for_first_runner(self) -> None:
        self._enable_fake_researcher()
        with patch(
            "spark_intelligence.system_registry.registry.collect_browser_use_adapter_status",
            return_value=self._ready_browser_use_status(),
        ):
            envelope = build_harness_task_envelope(
                config_manager=self.config_manager,
                state_db=self.state_db,
                task="Search the web for Spark architecture.",
            )

        result = execute_harness_task(
            config_manager=self.config_manager,
            state_db=self.state_db,
            envelope=envelope,
        )

        self.assertEqual(result.status, "needs_input")
        self.assertIn("browser_status_payload", result.artifacts)
        self.assertIn("needs_input", result.artifacts)

    def test_execute_researcher_advisory_harness_runs_bridge(self) -> None:
        self._enable_fake_researcher()
        class FakeResult:
            reply_text = "Here is the answer."
            evidence_summary = "status=ok"
            trace_ref = "trace:test"
            mode = "external_configured"
            provider_id = "custom"
            provider_model = "MiniMax-M2.7"
            provider_execution_transport = "direct_http"
            routing_decision = "provider_execution"
            active_chip_key = None

        envelope = build_harness_task_envelope(
            config_manager=self.config_manager,
            state_db=self.state_db,
            task="Draft a direct answer for this operator question.",
            channel_kind="telegram",
            session_id="session-r",
            human_id="human-r",
            agent_id="agent-r",
        )

        with patch(
            "spark_intelligence.harness_runtime.service._run_researcher_bridge_reply",
            return_value=FakeResult(),
        ) as bridge_mock:
            result = execute_harness_task(
                config_manager=self.config_manager,
                state_db=self.state_db,
                envelope=envelope,
            )

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.artifacts["reply_text"], "Here is the answer.")
        self.assertEqual(result.artifacts["trace_ref"], "trace:test")
        bridge_mock.assert_called_once()

    def test_build_harness_task_envelope_allows_forced_harness_override(self) -> None:
        self._enable_fake_researcher()
        envelope = build_harness_task_envelope(
            config_manager=self.config_manager,
            state_db=self.state_db,
            task="Explain this directly.",
            forced_harness_id="researcher.advisory",
        )

        self.assertEqual(envelope.harness_id, "researcher.advisory")
        self.assertEqual(envelope.route_mode, "forced_harness")

    def test_build_harness_task_envelope_rejects_unknown_forced_harness(self) -> None:
        with self.assertRaises(ValueError):
            build_harness_task_envelope(
                config_manager=self.config_manager,
                state_db=self.state_db,
                task="Explain this directly.",
                forced_harness_id="missing.harness",
            )

    def test_build_harness_task_envelope_rejects_empty_task(self) -> None:
        with self.assertRaisesRegex(ValueError, "cannot be empty"):
            build_harness_task_envelope(
                config_manager=self.config_manager,
                state_db=self.state_db,
                task="   ",
            )

    def test_execute_harness_task_rejects_unknown_runtime_envelope_before_opening_run(self) -> None:
        envelope = build_harness_task_envelope(
            config_manager=self.config_manager,
            state_db=self.state_db,
            task="Prepare this task.",
        )
        unknown = replace(envelope, harness_id="missing.harness")

        with self.assertRaisesRegex(ValueError, "Unknown harness id"):
            execute_harness_task(
                config_manager=self.config_manager,
                state_db=self.state_db,
                envelope=unknown,
            )

        snapshot = build_harness_runtime_snapshot(self.config_manager, self.state_db)
        self.assertEqual(snapshot.summary["recent_run_count"], 0)

    def test_execute_voice_io_harness_runs_speak_hook_when_text_present(self) -> None:
        envelope = build_harness_task_envelope(
            config_manager=self.config_manager,
            state_db=self.state_db,
            task="Say: Hello from Spark voice.",
            forced_harness_id="voice.io",
        )
        envelope = with_harness_local_operator_turn_intent(envelope)

        def fake_voice_hook(*, hook, **kwargs):
            if hook == "voice.status":
                return (
                    {
                        "result": {
                            "ready": True,
                            "reason": "voice ready",
                            "reply_text": "Voice chip is ready.",
                        }
                    },
                    "domain-chip-voice-comms",
                )
            return (
                {
                    "result": {
                        "provider_id": "elevenlabs",
                        "voice_id": "voice-123",
                        "model_id": "eleven_turbo_v2_5",
                        "mime_type": "audio/ogg",
                        "filename": "voice-reply-test.ogg",
                        "voice_compatible": True,
                        "audio_base64": "aGVsbG8=",
                    }
                },
                "domain-chip-voice-comms",
            )

        with patch(
            "spark_intelligence.harness_runtime.service._run_voice_hook",
            side_effect=fake_voice_hook,
        ):
            result = execute_harness_task(
                config_manager=self.config_manager,
                state_db=self.state_db,
                envelope=envelope,
            )

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.artifacts["voice_status"]["ready"], True)
        self.assertEqual(result.artifacts["spoken_audio"]["filename"], "voice-reply-test.ogg")
        self.assertEqual(result.artifacts["spoken_audio"]["audio_bytes"], 5)
        self.assertEqual(result.artifacts["spoken_audio"]["text"], "Hello from Spark voice.")

    def test_execute_voice_io_harness_rejects_invalid_base64_audio(self) -> None:
        envelope = build_harness_task_envelope(
            config_manager=self.config_manager,
            state_db=self.state_db,
            task="Say: Hello from Spark voice.",
            forced_harness_id="voice.io",
        )
        envelope = with_harness_local_operator_turn_intent(envelope)

        def fake_voice_hook(*, hook, **kwargs):
            if hook == "voice.status":
                return ({"result": {"ready": True}}, "domain-chip-voice-comms")
            return ({"result": {"audio_base64": "not valid base64!"}}, "domain-chip-voice-comms")

        with patch("spark_intelligence.harness_runtime.service._run_voice_hook", side_effect=fake_voice_hook):
            result = execute_harness_task(
                config_manager=self.config_manager,
                state_db=self.state_db,
                envelope=envelope,
            )

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.artifacts["spoken_audio"]["audio_bytes"], 0)
        self.assertIsNone(result.artifacts["spoken_audio"]["audio_sha256"])

    def test_execute_voice_io_harness_without_authority_does_not_run_chip_hook(self) -> None:
        envelope = build_harness_task_envelope(
            config_manager=self.config_manager,
            state_db=self.state_db,
            task="Say: Hello from Spark voice.",
            forced_harness_id="voice.io",
        )

        with patch("spark_intelligence.attachments.run_first_chip_hook_supporting") as hook_mock:
            result = execute_harness_task(
                config_manager=self.config_manager,
                state_db=self.state_db,
                envelope=envelope,
            )

        self.assertEqual(result.status, "blocked")
        hook_mock.assert_not_called()
        self.assertIn("missing", result.artifacts["voice_status"]["reason"])

    def test_execute_voice_io_harness_requests_input_without_explicit_text(self) -> None:
        envelope = build_harness_task_envelope(
            config_manager=self.config_manager,
            state_db=self.state_db,
            task="Use voice for this.",
            forced_harness_id="voice.io",
        )
        envelope = with_harness_local_operator_turn_intent(envelope)

        with patch(
            "spark_intelligence.harness_runtime.service._run_voice_hook",
            return_value=(
                {
                    "result": {
                        "ready": True,
                        "reason": "voice ready",
                        "reply_text": "Voice chip is ready.",
                    }
                },
                "domain-chip-voice-comms",
            ),
        ):
            result = execute_harness_task(
                config_manager=self.config_manager,
                state_db=self.state_db,
                envelope=envelope,
            )

        self.assertEqual(result.status, "needs_input")
        self.assertEqual(result.artifacts["needs_input"]["mode"], "unspecified")
        self.assertIn("resume_command", result.artifacts["resume_token"])

    def test_voice_status_block_records_sanitized_observability(self) -> None:
        envelope = build_harness_task_envelope(
            config_manager=self.config_manager,
            state_db=self.state_db,
            task="Say: status failure test.",
            forced_harness_id="voice.io",
        )
        envelope = with_harness_local_operator_turn_intent(envelope)

        with patch(
            "spark_intelligence.harness_runtime.service._run_voice_hook",
            side_effect=RuntimeError("token=top-secret /private/operator/path"),
        ):
            result = execute_harness_task(
                config_manager=self.config_manager,
                state_db=self.state_db,
                envelope=envelope,
            )

        self.assertEqual(result.status, "blocked")
        self.assertNotIn("top-secret", json.dumps(result.to_payload()))
        with self.state_db.connect() as conn:
            row = conn.execute(
                """
                SELECT reason_code, facts_json
                FROM builder_events
                WHERE request_id = ? AND event_type = 'harness_execution_blocked'
                """,
                (envelope.envelope_id,),
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["reason_code"], "voice_status_hook_failed")
        facts = json.loads(row["facts_json"])
        self.assertEqual(facts, {"exception_type": "RuntimeError", "hook": "voice.status"})

    def test_voice_speak_block_records_sanitized_observability(self) -> None:
        envelope = build_harness_task_envelope(
            config_manager=self.config_manager,
            state_db=self.state_db,
            task="Say: speak failure test.",
            forced_harness_id="voice.io",
        )
        envelope = with_harness_local_operator_turn_intent(envelope)

        def fake_voice_hook(*, hook, **kwargs):
            if hook == "voice.status":
                return ({"result": {"ready": True}}, "domain-chip-voice-comms")
            raise RuntimeError("api-key=top-secret /private/provider/path")

        with patch(
            "spark_intelligence.harness_runtime.service._run_voice_hook",
            side_effect=fake_voice_hook,
        ):
            result = execute_harness_task(
                config_manager=self.config_manager,
                state_db=self.state_db,
                envelope=envelope,
            )

        self.assertEqual(result.status, "blocked")
        self.assertNotIn("top-secret", json.dumps(result.to_payload()))
        with self.state_db.connect() as conn:
            row = conn.execute(
                """
                SELECT reason_code, facts_json
                FROM builder_events
                WHERE request_id = ? AND event_type = 'harness_execution_blocked'
                """,
                (envelope.envelope_id,),
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["reason_code"], "voice_speak_hook_failed")
        facts = json.loads(row["facts_json"])
        self.assertEqual(facts, {"exception_type": "RuntimeError", "hook": "voice.speak"})

    def test_execute_swarm_escalation_harness_builds_dry_run_payload(self) -> None:
        envelope = build_harness_task_envelope(
            config_manager=self.config_manager,
            state_db=self.state_db,
            task="Coordinate this through Swarm.",
            forced_harness_id="swarm.escalation",
        )

        with (
            patch(
                "spark_intelligence.harness_runtime.service._load_swarm_status",
                return_value=SimpleNamespace(
                    enabled=True,
                    configured=True,
                    researcher_ready=True,
                    payload_ready=True,
                    api_ready=True,
                    auth_state="configured",
                    workspace_id="workspace-1",
                    api_url="https://swarm.example",
                    last_decision={"mode": "manual_recommended"},
                    last_failure=None,
                ),
            ),
            patch(
                "spark_intelligence.harness_runtime.service._run_swarm_sync_dry_run",
                return_value=SimpleNamespace(
                    ok=True,
                    mode="dry_run",
                    message="Built payload",
                    payload_path="C:/tmp/swarm-payload.json",
                    api_url="https://swarm.example",
                    workspace_id="workspace-1",
                    accepted=None,
                    response_body={
                        "payload_keys": ["collective"],
                        "token": "private-response-token",
                        "detail": "provider rejected sk-proj-" + "A" * 30,
                    },
                ),
            ),
        ):
            result = execute_harness_task(
                config_manager=self.config_manager,
                state_db=self.state_db,
                envelope=envelope,
            )

        self.assertEqual(result.status, "prepared")
        self.assertEqual(result.artifacts["swarm_sync_result"]["mode"], "dry_run")
        response_body = result.artifacts["swarm_sync_result"]["response_body"]
        self.assertIsInstance(response_body, dict)
        self.assertEqual(response_body["payload_keys"], ["collective"])
        self.assertEqual(response_body["token"], "<redacted>")
        self.assertIn("<redacted api key>", response_body["detail"])
        self.assertNotIn("private-response-token", str(response_body))
        self.assertNotIn("sk-proj-", str(response_body))
        self.assertIn("swarm sync", result.artifacts["resume_token"]["resume_command"])

    def test_execute_swarm_escalation_harness_requests_payload_repair_when_not_ready(self) -> None:
        envelope = build_harness_task_envelope(
            config_manager=self.config_manager,
            state_db=self.state_db,
            task="Coordinate this through Swarm.",
            forced_harness_id="swarm.escalation",
        )

        with patch(
            "spark_intelligence.harness_runtime.service._load_swarm_status",
            return_value=SimpleNamespace(
                enabled=True,
                configured=True,
                researcher_ready=True,
                payload_ready=False,
                api_ready=False,
                auth_state="refreshable",
                workspace_id="workspace-1",
                api_url="https://swarm.example",
                last_decision=None,
                last_failure={"mode": "researcher_missing"},
            ),
        ):
            result = execute_harness_task(
                config_manager=self.config_manager,
                state_db=self.state_db,
                envelope=envelope,
            )

        self.assertEqual(result.status, "needs_input")
        self.assertEqual(result.artifacts["swarm_status"]["payload_ready"], False)
        self.assertIn("retry_command", result.artifacts["retry_token"])

    def test_execute_harness_chain_marks_blocked_when_primary_needs_input(self) -> None:
        self._enable_fake_researcher()
        with patch(
            "spark_intelligence.system_registry.registry.collect_browser_use_adapter_status",
            return_value=self._ready_browser_use_status(),
        ):
            envelope = build_harness_task_envelope(
                config_manager=self.config_manager,
                state_db=self.state_db,
                task="Search the web for Spark architecture.",
            )

        result = execute_harness_chain(
            config_manager=self.config_manager,
            state_db=self.state_db,
            envelope=envelope,
            follow_up_harness_ids=["voice.io"],
        )

        self.assertEqual(result.status, "needs_input")
        self.assertEqual(result.chain_status, "needs_input")
        self.assertEqual(result.chained_results or [], [])

    def test_execute_harness_chain_returns_primary_unchanged_when_no_follow_ups(self) -> None:
        envelope = build_harness_task_envelope(
            config_manager=self.config_manager,
            state_db=self.state_db,
            task="What chips are active right now?",
        )

        result_no_follow_ups = execute_harness_chain(
            config_manager=self.config_manager,
            state_db=self.state_db,
            envelope=envelope,
            follow_up_harness_ids=None,
        )

        self.assertEqual(result_no_follow_ups.envelope.harness_id, "builder.direct")
        self.assertIsNone(result_no_follow_ups.chain_status)
        self.assertIsNone(result_no_follow_ups.chained_results)

        result_blank_follow_ups = execute_harness_chain(
            config_manager=self.config_manager,
            state_db=self.state_db,
            envelope=envelope,
            follow_up_harness_ids=["", "   "],
        )

        self.assertIsNone(result_blank_follow_ups.chain_status)
        self.assertIsNone(result_blank_follow_ups.chained_results)

    def test_derive_follow_up_task_routes_per_target_harness(self) -> None:
        from spark_intelligence.harness_runtime.service import (
            HarnessExecutionResult,
            HarnessTaskEnvelope,
            _derive_follow_up_task,
        )

        def _make_envelope() -> HarnessTaskEnvelope:
            return HarnessTaskEnvelope(
                envelope_id="htask:derive-test",
                task="Original operator task.",
                harness_id="researcher.advisory",
                owner_system="researcher",
                backend_kind="researcher",
                session_scope="task",
                prompt_strategy="direct",
                retry_policy="none",
                approval_mode="none",
                route_mode="router",
                required_capabilities=[],
                artifacts_expected=[],
                next_actions=[],
                limitations=[],
                channel_kind=None,
                session_id=None,
                human_id=None,
                agent_id=None,
            )

        result_with_reply = HarnessExecutionResult(
            envelope=_make_envelope(),
            run_id="run:derive-with-reply",
            status="completed",
            summary="Summary text only.",
            artifacts={"reply_text": "Upstream reply text."},
            next_actions=[],
        )
        result_without_reply = HarnessExecutionResult(
            envelope=_make_envelope(),
            run_id="run:derive-without-reply",
            status="prepared",
            summary="Summary text only.",
            artifacts={},
            next_actions=[],
        )

        self.assertEqual(
            _derive_follow_up_task(current_result=result_with_reply, target_harness_id="voice.io"),
            "Say: Upstream reply text.",
        )
        self.assertEqual(
            _derive_follow_up_task(current_result=result_without_reply, target_harness_id="voice.io"),
            "Say: Summary text only.",
        )

        swarm_task = _derive_follow_up_task(
            current_result=result_with_reply, target_harness_id="swarm.escalation"
        )
        self.assertIn("Coordinate this through Swarm.", swarm_task)
        self.assertIn("Original harness: researcher.advisory", swarm_task)
        self.assertIn("Upstream reply: Upstream reply text.", swarm_task)
        swarm_task_no_reply = _derive_follow_up_task(
            current_result=result_without_reply, target_harness_id="swarm.escalation"
        )
        self.assertIn("Upstream summary: Summary text only.", swarm_task_no_reply)

        self.assertEqual(
            _derive_follow_up_task(
                current_result=result_with_reply, target_harness_id="researcher.advisory"
            ),
            "Upstream reply text.",
        )
        self.assertEqual(
            _derive_follow_up_task(
                current_result=result_without_reply, target_harness_id="researcher.advisory"
            ),
            "Original operator task.",
        )

        self.assertEqual(
            _derive_follow_up_task(
                current_result=result_with_reply, target_harness_id="builder.direct"
            ),
            "Upstream reply text.",
        )
        self.assertEqual(
            _derive_follow_up_task(
                current_result=result_without_reply, target_harness_id="builder.direct"
            ),
            "Summary text only.",
        )

        self.assertEqual(
            _derive_follow_up_task(
                current_result=result_with_reply, target_harness_id="future.workflow"
            ),
            "Original operator task.",
        )

    def test_extract_first_url_handles_single_first_none_and_wrapped(self) -> None:
        from spark_intelligence.harness_runtime.service import _extract_first_url

        self.assertEqual(
            _extract_first_url("Open https://example.com and inspect it."),
            "https://example.com",
        )
        self.assertEqual(
            _extract_first_url("First https://one.example then https://two.example."),
            "https://one.example",
        )
        self.assertEqual(
            _extract_first_url("Plain http URL http://insecure.example/path?x=1 here"),
            "http://insecure.example/path?x=1",
        )
        self.assertEqual(
            _extract_first_url("See (https://wrapped.example) here"),
            "https://wrapped.example",
        )
        self.assertIsNone(_extract_first_url("No url in this line at all."))
        self.assertIsNone(_extract_first_url(""))

    def test_execute_harness_task_returns_planned_for_envelope_with_no_runner(self) -> None:
        from spark_intelligence.harness_runtime.service import HarnessTaskEnvelope

        envelope = HarnessTaskEnvelope(
            envelope_id="htask:planned-test",
            task="Coordinate something we have not implemented yet.",
            harness_id="future.workflow",
            owner_system="builder",
            backend_kind="future_runner",
            session_scope="task",
            prompt_strategy="direct",
            retry_policy="none",
            approval_mode="none",
            route_mode="forced_harness",
            required_capabilities=[],
            artifacts_expected=[],
            next_actions=[],
            limitations=[],
            channel_kind=None,
            session_id=None,
            human_id=None,
            agent_id=None,
        )

        with self.assertRaisesRegex(ValueError, "Unknown harness id 'future.workflow'"):
            execute_harness_task(
                config_manager=self.config_manager,
                state_db=self.state_db,
                envelope=envelope,
            )

    def test_harness_execution_result_payload_serializes_chain_status_and_chained_results(self) -> None:
        from spark_intelligence.harness_runtime.service import (
            HarnessExecutionResult,
            HarnessTaskEnvelope,
        )

        def _make_envelope(envelope_id: str, harness_id: str) -> HarnessTaskEnvelope:
            return HarnessTaskEnvelope(
                envelope_id=envelope_id,
                task="Task for " + harness_id,
                harness_id=harness_id,
                owner_system="owner",
                backend_kind="backend",
                session_scope="task",
                prompt_strategy="direct",
                retry_policy="none",
                approval_mode="none",
                route_mode="router",
                required_capabilities=[],
                artifacts_expected=[],
                next_actions=[],
                limitations=[],
                channel_kind=None,
                session_id=None,
                human_id=None,
                agent_id=None,
            )

        chained = HarnessExecutionResult(
            envelope=_make_envelope("htask:chained", "voice.io"),
            run_id="run:chained",
            status="completed",
            summary="Chained voice harness summary.",
            artifacts={"spoken_audio": {"audio_bytes": 5}},
            next_actions=[],
        )
        primary = HarnessExecutionResult(
            envelope=_make_envelope("htask:primary", "researcher.advisory"),
            run_id="run:primary",
            status="completed",
            summary="Primary researcher harness summary.",
            artifacts={"reply_text": "Researcher reply."},
            next_actions=["follow-up next action"],
            chain_status="completed",
            chained_results=[chained],
        )

        payload = primary.to_payload()

        self.assertEqual(payload["chain_status"], "completed")
        self.assertEqual(len(payload["chained_results"]), 1)
        chained_payload = payload["chained_results"][0]
        self.assertEqual(chained_payload["envelope"]["harness_id"], "voice.io")
        self.assertEqual(chained_payload["run_id"], "run:chained")
        self.assertEqual(chained_payload["status"], "completed")

        no_chain = HarnessExecutionResult(
            envelope=_make_envelope("htask:solo", "builder.direct"),
            run_id="run:solo",
            status="prepared",
            summary="Solo builder.direct.",
            artifacts={},
            next_actions=[],
        )
        solo_payload = no_chain.to_payload()
        self.assertIsNone(solo_payload["chain_status"])
        self.assertEqual(solo_payload["chained_results"], [])

    def test_classify_voice_task_recognizes_speak_transcribe_and_unspecified(self) -> None:
        from spark_intelligence.harness_runtime.service import _classify_voice_task

        self.assertEqual(_classify_voice_task("Say: Hello from Spark."), ("speak", "Hello from Spark."))
        self.assertEqual(_classify_voice_task("voice: standby for ops"), ("speak", "standby for ops"))
        self.assertEqual(_classify_voice_task("Reply with voice: standby."), ("speak", "standby."))
        self.assertEqual(_classify_voice_task("send this as voice: hello there"), ("speak", "hello there"))
        self.assertEqual(_classify_voice_task("Please transcribe this audio note."), ("transcribe", None))
        self.assertEqual(_classify_voice_task("run transcription on this clip"), ("transcribe", None))
        self.assertEqual(_classify_voice_task("Hold the line for the operator."), ("unspecified", None))
        self.assertEqual(_classify_voice_task(""), ("unspecified", None))

    def test_execute_harness_chain_runs_researcher_then_voice(self) -> None:
        envelope = build_harness_task_envelope(
            config_manager=self.config_manager,
            state_db=self.state_db,
            task="What is the difference between Spark Researcher and Builder?",
            forced_harness_id="researcher.advisory",
        )
        envelope = with_harness_local_operator_turn_intent(envelope)

        class FakeResearcherResult:
            reply_text = "Spark Researcher thinks.\nBuilder delivers."
            evidence_summary = "status=ok"
            trace_ref = "trace:test"
            mode = "external_configured"
            provider_id = "custom"
            provider_model = "MiniMax-M2.7"
            provider_execution_transport = "direct_http"
            routing_decision = "provider_execution"
            active_chip_key = None

        def fake_voice_hook(*, hook, payload, **kwargs):
            if hook == "voice.status":
                return (
                    {
                        "result": {
                            "ready": True,
                            "reason": "voice ready",
                            "reply_text": "Voice chip is ready.",
                        }
                    },
                    "domain-chip-voice-comms",
                )
            self.assertEqual(payload["text"], "Spark Researcher thinks.\nBuilder delivers.")
            return (
                {
                    "result": {
                        "provider_id": "elevenlabs",
                        "voice_id": "voice-123",
                        "model_id": "eleven_turbo_v2_5",
                        "mime_type": "audio/ogg",
                        "filename": "voice-reply-test.ogg",
                        "voice_compatible": True,
                        "audio_base64": "aGVsbG8=",
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

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.chain_status, "completed")
        self.assertEqual(len(result.chained_results or []), 1)
        voice_result = (result.chained_results or [])[0]
        self.assertEqual(voice_result.envelope.harness_id, "voice.io")
        self.assertEqual(voice_result.status, "completed")

    def test_execute_harness_chain_records_bounded_partial_progress_on_failure(self) -> None:
        envelope = build_harness_task_envelope(
            config_manager=self.config_manager,
            state_db=self.state_db,
            task="What is the difference between Spark Researcher and Builder?",
            forced_harness_id="researcher.advisory",
        )
        envelope = with_harness_local_operator_turn_intent(envelope)

        researcher_result = SimpleNamespace(
            reply_text="A grounded answer.", evidence_summary="status=ok", trace_ref="trace:test",
            mode="external_configured", provider_id="custom", provider_model="MiniMax-M2.7",
            provider_execution_transport="direct_http", routing_decision="provider_execution",
            active_chip_key=None,
        )
        secret = "sk-proj-" + "A" * 30

        def execute_or_fail(*, config_manager, state_db, envelope):
            if envelope.harness_id == "voice.io":
                raise RuntimeError(secret)
            return execute_harness_task(
                config_manager=config_manager,
                state_db=state_db,
                envelope=envelope,
            )

        with (
            patch("spark_intelligence.harness_runtime.service._run_researcher_bridge_reply", return_value=researcher_result),
            patch("spark_intelligence.harness_runtime.service.execute_harness_task", side_effect=execute_or_fail),
            self.assertRaises(RuntimeError),
        ):
            execute_harness_chain(
                config_manager=self.config_manager,
                state_db=self.state_db,
                envelope=envelope,
                follow_up_harness_ids=["voice.io"],
            )

        events = latest_events_by_type(self.state_db, event_type="harness_chain_interrupted", limit=5)
        self.assertTrue(events)
        self.assertEqual(events[0]["facts_json"]["primary_harness_id"], "researcher.advisory")
        self.assertEqual(events[0]["facts_json"]["interrupted_harness_id"], "voice.io")
        self.assertEqual(events[0]["facts_json"]["error_type"], "RuntimeError")
        self.assertNotIn(secret, str(events[0]))

    def test_execute_harness_chain_propagates_needs_input_status(self) -> None:
        envelope = build_harness_task_envelope(
            config_manager=self.config_manager,
            state_db=self.state_db,
            task="Prepare a grounded answer, then coordinate it through Swarm.",
            forced_harness_id="researcher.advisory",
        )
        envelope = with_harness_local_operator_turn_intent(envelope)
        researcher_result = SimpleNamespace(
            reply_text="A grounded answer.", evidence_summary="status=ok", trace_ref="trace:test",
            mode="external_configured", provider_id="custom", provider_model="MiniMax-M2.7",
            provider_execution_transport="direct_http", routing_decision="provider_execution",
            active_chip_key=None,
        )

        with patch(
            "spark_intelligence.harness_runtime.service._run_researcher_bridge_reply",
            return_value=researcher_result,
        ):
            result = execute_harness_chain(
                config_manager=self.config_manager,
                state_db=self.state_db,
                envelope=envelope,
                follow_up_harness_ids=["swarm.escalation"],
            )

        self.assertEqual(result.status, "needs_input")
        self.assertEqual(result.chain_status, "needs_input")
        self.assertEqual((result.chained_results or [])[0].status, "needs_input")
