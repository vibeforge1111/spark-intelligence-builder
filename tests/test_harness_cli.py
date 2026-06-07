from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

from spark_intelligence.observability.store import latest_events_by_type, persist_bound_ledger

from tests.test_support import SparkTestCase, create_fake_hook_chip, create_fake_researcher_runtime


class HarnessCliTests(SparkTestCase):
    def test_harness_tool_ledgers_filters_by_turn_id(self) -> None:
        persist_bound_ledger(
            self.state_db,
            row={
                "ledger_id": "ledger:cli-ledger",
                "turn_id": "turn:cli-ledger",
                "action_id": "action:cli-ledger",
                "capability_id": "capability:cli-ledger",
                "authorization_decision_id": "decision:cli-ledger",
                "tool_name": "test.tool",
                "surface": "cli_test",
                "status": "success",
                "ledger_json": {
                    "schema_version": "tool-call-ledger-v1",
                    "ledger_id": "ledger:cli-ledger",
                    "turn_id": "turn:cli-ledger",
                    "action_id": "action:cli-ledger",
                    "capability_id": "capability:cli-ledger",
                    "tool_name": "test.tool",
                    "authorization": {"decision_id": "decision:cli-ledger"},
                    "result": {"status": "success", "summary": "Recorded for CLI visibility."},
                },
            },
        )

        exit_code, stdout, stderr = self.run_cli(
            "harness",
            "tool-ledgers",
            "--home",
            str(self.home),
            "--turn-id",
            "turn:cli-ledger",
            "--json",
        )

        self.assertEqual(exit_code, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["ledgers"][0]["ledger_id"], "ledger:cli-ledger")
        self.assertEqual(payload["ledgers"][0]["surface"], "cli_test")

    def _enable_fake_researcher(self) -> None:
        runtime_root = create_fake_researcher_runtime(self.home)
        self.config_manager.set_path("spark.researcher.enabled", True)
        self.config_manager.set_path("spark.researcher.runtime_root", str(runtime_root))

    def test_harness_plan_reports_browser_harness_for_url_task(self) -> None:
        self._enable_fake_researcher()
        with patch(
            "spark_intelligence.system_registry.registry.collect_browser_use_adapter_status",
            return_value={
                "status": "completed",
                "backend_kind": "browser_use_adapter",
                "backend_label": "Browser-use Adapter",
                "adapter_status": "ready",
                "configured": True,
                "package_available": True,
                "evidence_summary": "browser-use adapter status=ready",
            },
        ):
            exit_code, stdout, stderr = self.run_cli(
                "harness",
                "plan",
                "Open https://example.com and inspect it.",
                "--home",
                str(self.home),
                "--json",
            )

        self.assertEqual(exit_code, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["harness_id"], "browser.grounded")
        self.assertEqual(payload["backend_kind"], "browser_use_adapter")

    def test_harness_execute_runs_researcher_advisory_runner(self) -> None:
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

        with patch(
            "spark_intelligence.harness_runtime.service._run_researcher_bridge_reply",
            return_value=FakeResult(),
        ):
            exit_code, stdout, stderr = self.run_cli(
                "harness",
                "execute",
                "Draft a direct answer for this operator question.",
                "--home",
                str(self.home),
                "--json",
            )

        self.assertEqual(exit_code, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["envelope"]["harness_id"], "researcher.advisory")
        self.assertEqual(payload["artifacts"]["reply_text"], "Here is the answer.")

    def test_harness_execute_respects_forced_harness_id(self) -> None:
        self._enable_fake_researcher()
        class FakeResult:
            reply_text = "Forced harness reply."
            evidence_summary = "status=ok"
            trace_ref = "trace:forced"
            mode = "external_configured"
            provider_id = "custom"
            provider_model = "MiniMax-M2.7"
            provider_execution_transport = "direct_http"
            routing_decision = "forced_harness"
            active_chip_key = None

        with patch(
            "spark_intelligence.harness_runtime.service._run_researcher_bridge_reply",
            return_value=FakeResult(),
        ):
            exit_code, stdout, stderr = self.run_cli(
                "harness",
                "execute",
                "What is the difference between Spark Researcher and Builder?",
                "--home",
                str(self.home),
                "--harness-id",
                "researcher.advisory",
                "--json",
            )

        self.assertEqual(exit_code, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["envelope"]["harness_id"], "researcher.advisory")
        self.assertEqual(payload["envelope"]["route_mode"], "forced_harness")
        self.assertEqual(payload["artifacts"]["reply_text"], "Forced harness reply.")

    def test_harness_execute_voice_io_attaches_local_authority_before_hooks(self) -> None:
        seen_hooks: list[str] = []

        def fake_voice_hook(
            _config_manager,
            *,
            hook: str,
            payload: dict[str, object],
            governor_decision: dict[str, object] | None = None,
        ):
            seen_hooks.append(hook)
            if hook == "voice.status":
                self.assertIsNone(governor_decision)
                return SimpleNamespace(
                    ok=True,
                    chip_key="domain-chip-voice-comms",
                    hook=hook,
                    repo_root=str(self.home),
                    command=["voice.status"],
                    exit_code=0,
                    output={"result": {"ready": True, "reason": "ready", "reply_text": "Voice ready."}},
                    payload=payload,
                    stderr="",
                    stdout="",
                )
            self.assertEqual(hook, "voice.speak")
            self.assertEqual(payload["text"], "Hello from the CLI harness.")
            governor = payload["governor_decision"]
            self.assertEqual(governor_decision, governor)
            self.assertEqual(governor["schema_version"], "governor-decision-v1")
            self.assertEqual(governor["tool_ledgers"][0]["tool_name"], "voice.speak")
            return SimpleNamespace(
                ok=True,
                chip_key="domain-chip-voice-comms",
                hook=hook,
                repo_root=str(self.home),
                command=["voice.speak"],
                exit_code=0,
                output={
                    "result": {
                        "provider_id": "local",
                        "voice_id": "voice-1",
                        "model_id": "test",
                        "mime_type": "audio/ogg",
                        "filename": "voice.ogg",
                        "voice_compatible": True,
                        "audio_base64": "aGVsbG8=",
                    }
                },
                payload=payload,
                stderr="",
                stdout="",
            )

        with patch("spark_intelligence.attachments.run_first_chip_hook_supporting", side_effect=fake_voice_hook):
            exit_code, stdout, stderr = self.run_cli(
                "harness",
                "execute",
                "Say: Hello from the CLI harness.",
                "--home",
                str(self.home),
                "--harness-id",
                "voice.io",
                "--json",
            )

        self.assertEqual(exit_code, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["status"], "completed")
        authority = payload["envelope"]["turn_intent_payload"]
        self.assertEqual(authority["schema_version"], "turn-intent-envelope-vnext")
        self.assertEqual(authority["action_authority"]["state"], "executable")
        self.assertIn(
            "capability:spark-voice-comms:voice.speak",
            {action["capability_id"] for action in authority["proposed_actions"]},
        )
        self.assertEqual(seen_hooks, ["voice.status", "voice.speak"])

    def test_harness_status_returns_registry_and_runtime_payload(self) -> None:
        exit_code, stdout, stderr = self.run_cli(
            "harness",
            "status",
            "--home",
            str(self.home),
            "--json",
        )

        self.assertEqual(exit_code, 0, stderr)
        payload = json.loads(stdout)
        self.assertIn("harness_registry", payload)
        self.assertIn("harness_runtime", payload)

    def test_harness_self_evolution_snapshot_records_observe_run(self) -> None:
        persist_bound_ledger(
            self.state_db,
            row={
                "turn_id": "turn:self-evolution-test",
                "action_id": "action:self-evolution-test",
                "capability_id": "capability:domain-chip-memory:memory.write",
                "authorization_decision_id": "decision:self-evolution-test",
                "ledger_id": "ledger:self-evolution-test",
                "tool_name": "memory.write",
                "owner_system": "domain-chip-memory",
                "mutation_class": "writes_memory",
                "outcome": "execute",
                "status": "success",
                "surface": "telegram",
                "request_id": "req:self-evolution-test",
                "trace_ref": "trace:self-evolution-test",
                "summary": "Memory write completed.",
                "ledger_json": {
                    "schema_version": "tool-call-ledger-v1",
                    "ledger_id": "ledger:self-evolution-test",
                    "turn_id": "turn:self-evolution-test",
                    "result": {"status": "success", "summary": "Memory write completed."},
                },
            },
            component="telegram_runtime",
        )

        exit_code, stdout, stderr = self.run_cli(
            "harness",
            "self-evolution-snapshot",
            "--home",
            str(self.home),
            "--json",
        )

        self.assertEqual(exit_code, 0, stderr)
        payload = json.loads(stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["mode"], "observe")
        self.assertEqual(payload["ledger_count"], 1)
        self.assertEqual(payload["self_evolution_run"]["schema_version"], "self-evolution-run-v1")
        self.assertEqual(payload["self_evolution_run"]["mode"], "observe")
        self.assertEqual(payload["readiness_score"]["overall"]["status"], "blocked")
        self.assertEqual(payload["experience_index"]["entries"][0]["entry_type"], "tool_ledger")
        events = latest_events_by_type(self.state_db, event_type="harness_self_evolution_observed", limit=1)
        self.assertEqual(len(events), 1)
        facts = events[0]["facts_json"]
        self.assertEqual(facts["ledger_count"], 1)
        self.assertEqual(
            facts["self_evolution_run"]["evolution_id"],
            payload["self_evolution_run"]["evolution_id"],
        )

    def test_harness_change_manifest_runner_blocks_without_executed_tests(self) -> None:
        self._persist_self_evolution_ledger()
        manifest_path = self._write_self_evolution_manifest(required_tests=["python -m unittest --help"])

        exit_code, stdout, stderr = self.run_cli(
            "harness",
            "change-manifest-runner",
            "--home",
            str(self.home),
            "--manifest",
            str(manifest_path),
            "--requested-verdict",
            "promote_private",
            "--json",
        )

        self.assertEqual(exit_code, 0, stderr)
        payload = json.loads(stdout)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["manifest_count"], 1)
        self.assertFalse(payload["run_tests"])
        self.assertEqual(payload["self_evolution_run"]["schema_version"], "self-evolution-run-v1")
        self.assertEqual(payload["self_evolution_run"]["promotion_decision"]["verdict"], "not_ready")
        self.assertIn(
            "required_tests_not_executed",
            payload["readiness_score"]["categories"]["verification"]["blockers"],
        )
        events = latest_events_by_type(self.state_db, event_type="harness_change_manifest_runner_evaluated", limit=1)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["facts_json"]["promotion_verdict"], "not_ready")

    def test_harness_change_manifest_runner_runs_allowlisted_tests_and_promotes_private(self) -> None:
        self._persist_self_evolution_ledger()
        manifest_path = self._write_self_evolution_manifest(required_tests=["python -m unittest --help"])

        exit_code, stdout, stderr = self.run_cli(
            "harness",
            "change-manifest-runner",
            "--home",
            str(self.home),
            "--manifest",
            str(manifest_path),
            "--requested-verdict",
            "promote_private",
            "--run-tests",
            "--allow-private-promotion",
            "--json",
        )

        self.assertEqual(exit_code, 0, stderr)
        payload = json.loads(stdout)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["run_tests"])
        self.assertTrue(payload["all_tests_passed"])
        self.assertEqual(payload["test_results"][0]["status"], "passed")
        self.assertEqual(payload["readiness_score"]["overall"]["status"], "private_ready")
        self.assertEqual(payload["self_evolution_run"]["promotion_decision"]["verdict"], "promote_private")
        events = latest_events_by_type(self.state_db, event_type="harness_change_manifest_runner_evaluated", limit=1)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["facts_json"]["promotion_verdict"], "promote_private")

    def test_harness_execute_supports_follow_up_harness_chain(self) -> None:
        self._enable_fake_researcher()
        create_fake_hook_chip(self.home, chip_key="domain-chip-voice-comms")
        self.config_manager.set_path("spark.chips.roots", [str(self.home)])
        self.config_manager.set_path("spark.chips.active_keys", ["domain-chip-voice-comms"])
        class FakeResearcherResult:
            reply_text = "Spark Researcher thinks; Builder delivers."
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
            self.assertEqual(payload["text"], "Spark Researcher thinks; Builder delivers.")
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
            exit_code, stdout, stderr = self.run_cli(
                "harness",
                "execute",
                "What is the difference between Spark Researcher and Builder?",
                "--home",
                str(self.home),
                "--harness-id",
                "researcher.advisory",
                "--then-harness-id",
                "voice.io",
                "--json",
            )

        self.assertEqual(exit_code, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["chain_status"], "completed")
        self.assertEqual(len(payload["chained_results"]), 1)
        self.assertEqual(payload["chained_results"][0]["envelope"]["harness_id"], "voice.io")

    def test_harness_plan_supports_named_recipe(self) -> None:
        self._enable_fake_researcher()
        create_fake_hook_chip(self.home, chip_key="domain-chip-voice-comms")
        self.config_manager.set_path("spark.chips.roots", [str(self.home)])
        self.config_manager.set_path("spark.chips.active_keys", ["domain-chip-voice-comms"])
        exit_code, stdout, stderr = self.run_cli(
            "harness",
            "plan",
            "What is the difference between Spark Researcher and Builder?",
            "--home",
            str(self.home),
            "--recipe",
            "advisory_voice_reply",
            "--json",
        )

        self.assertEqual(exit_code, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["harness_id"], "researcher.advisory")
        self.assertEqual(payload["recipe"]["recipe_id"], "advisory_voice_reply")

    def test_harness_execute_supports_named_recipe(self) -> None:
        self._enable_fake_researcher()
        create_fake_hook_chip(self.home, chip_key="domain-chip-voice-comms")
        self.config_manager.set_path("spark.chips.roots", [str(self.home)])
        self.config_manager.set_path("spark.chips.active_keys", ["domain-chip-voice-comms"])
        class FakeResearcherResult:
            reply_text = "Spark Researcher thinks; Builder delivers."
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
            self.assertIn("Spark Researcher thinks; Builder delivers.", payload["text"])
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
            exit_code, stdout, stderr = self.run_cli(
                "harness",
                "execute",
                "What is the difference between Spark Researcher and Builder?",
                "--home",
                str(self.home),
                "--recipe",
                "advisory_voice_reply",
                "--json",
            )

        self.assertEqual(exit_code, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["recipe"]["recipe_id"], "advisory_voice_reply")
        self.assertEqual(payload["chain_status"], "completed")
        self.assertEqual(payload["chained_results"][0]["envelope"]["harness_id"], "voice.io")

    def test_harness_plan_auto_selects_recipe_for_voice_advisory_task(self) -> None:
        self._enable_fake_researcher()
        create_fake_hook_chip(self.home, chip_key="domain-chip-voice-comms")
        self.config_manager.set_path("spark.chips.roots", [str(self.home)])
        self.config_manager.set_path("spark.chips.active_keys", ["domain-chip-voice-comms"])
        exit_code, stdout, stderr = self.run_cli(
            "harness",
            "plan",
            "What is the difference between Spark Researcher and Builder? Answer in voice.",
            "--home",
            str(self.home),
            "--json",
        )

        self.assertEqual(exit_code, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["recipe"]["recipe_id"], "advisory_voice_reply")
        self.assertEqual(payload["recipe"]["selection_mode"], "auto")

    def test_harness_execute_auto_selects_recipe_for_voice_advisory_task(self) -> None:
        self._enable_fake_researcher()
        create_fake_hook_chip(self.home, chip_key="domain-chip-voice-comms")
        self.config_manager.set_path("spark.chips.roots", [str(self.home)])
        self.config_manager.set_path("spark.chips.active_keys", ["domain-chip-voice-comms"])
        class FakeResearcherResult:
            reply_text = "Spark Researcher thinks; Builder delivers."
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
            self.assertIn("Spark Researcher thinks; Builder delivers.", payload["text"])
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
            exit_code, stdout, stderr = self.run_cli(
                "harness",
                "execute",
                "What is the difference between Spark Researcher and Builder? Answer in voice.",
                "--home",
                str(self.home),
                "--json",
            )

        self.assertEqual(exit_code, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["recipe"]["recipe_id"], "advisory_voice_reply")
        self.assertEqual(payload["recipe"]["selection_mode"], "auto")
        self.assertEqual(payload["chain_status"], "completed")

    def _persist_self_evolution_ledger(self) -> None:
        persist_bound_ledger(
            self.state_db,
            row={
                "turn_id": "turn:self-evolution-runner-test",
                "action_id": "action:self-evolution-runner-test",
                "capability_id": "capability:domain-chip-memory:memory.write",
                "authorization_decision_id": "decision:self-evolution-runner-test",
                "ledger_id": "ledger:self-evolution-runner-test",
                "tool_name": "memory.write",
                "owner_system": "domain-chip-memory",
                "mutation_class": "writes_memory",
                "outcome": "execute",
                "status": "success",
                "surface": "telegram",
                "request_id": "req:self-evolution-runner-test",
                "trace_ref": "trace:self-evolution-runner-test",
                "summary": "Memory write completed.",
                "ledger_json": {
                    "schema_version": "tool-call-ledger-v1",
                    "ledger_id": "ledger:self-evolution-runner-test",
                    "turn_id": "turn:self-evolution-runner-test",
                    "result": {"status": "success", "summary": "Memory write completed."},
                },
            },
            component="telegram_runtime",
        )

    def _write_self_evolution_manifest(self, *, required_tests: list[str]):
        from spark_intelligence.harness_contract import _ensure_harness_core_importable

        _ensure_harness_core_importable()
        from spark_harness_core import HarnessKernel, evidence_ref

        kernel = HarnessKernel(surface="builder")
        component = kernel.component(
            component_id="component:builder-self-evolution-runner",
            component_type="middleware",
            owner_repo="spark-intelligence-builder",
            path="src/spark_intelligence/harness_evolution.py",
            summary="Builder self-evolution runner adapter.",
            tests=required_tests,
        )
        manifest = kernel.change_manifest(
            target_component=component,
            failure_evidence=[
                evidence_ref(
                    "policy",
                    "SPARK_DEEP_AUDIT_2026-06-07.md:self-evolution",
                    "Audit found the change-manifest runner was not invoked by Builder runtime.",
                    confidence=1.0,
                )
            ],
            root_cause_hypothesis="Builder had an observe-only self-evolution snapshot but no change-manifest runner adapter.",
            edit_summary="Feed validated change manifests through Harness Core's gated runner and persist the result.",
            predicted_fixes=["Operators can evaluate accepted manifests against ledger and test evidence."],
            predicted_regression_risks=["Unsafe test commands must remain blocked by the allowlist."],
            required_tests=required_tests,
            rollback_plan="Revert the Builder harness change-manifest runner adapter.",
            verdict="accepted",
        )
        path = self.home / "self-evolution-manifest.json"
        path.write_text(json.dumps(manifest), encoding="utf-8")
        return path
