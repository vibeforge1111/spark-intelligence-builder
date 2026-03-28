from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from spark_intelligence.auth.runtime import RuntimeProviderResolution
from spark_intelligence.observability.store import latest_events_by_type, record_event
from spark_intelligence.researcher_bridge.advisory import build_researcher_reply

from tests.test_support import SparkTestCase, create_fake_hook_chip


class AttachmentHookTests(SparkTestCase):
    def test_attachments_run_hook_allows_browser_status_hook(self) -> None:
        chip_root = create_fake_hook_chip(self.home, chip_key="spark-browser")
        self.config_manager.set_path("spark.chips.roots", [str(chip_root)])

        activate_exit, _, activate_stderr = self.run_cli(
            "attachments",
            "activate-chip",
            "spark-browser",
            "--home",
            str(self.home),
        )
        self.assertEqual(activate_exit, 0, activate_stderr)

        hook_exit, hook_stdout, hook_stderr = self.run_cli(
            "attachments",
            "run-hook",
            "browser.status",
            "--home",
            str(self.home),
            "--json",
            "--payload-json",
            json.dumps({"request_id": "browser-status-test", "target": {"browser_family": "brave"}}),
        )
        self.assertEqual(hook_exit, 0, hook_stderr)
        payload = json.loads(hook_stdout)
        self.assertEqual(payload["hook"], "browser.status")
        self.assertEqual(payload["output"]["result"]["browser"]["family"], "brave")

    def test_operator_handoff_observer_runs_packets_hook_and_records_handoff(self) -> None:
        chip_root = create_fake_hook_chip(self.home)
        self.config_manager.set_path("spark.chips.roots", [str(chip_root)])

        activate_exit, _, activate_stderr = self.run_cli(
            "attachments",
            "activate-chip",
            "startup-yc",
            "--home",
            str(self.home),
        )
        self.assertEqual(activate_exit, 0, activate_stderr)

        record_event(
            self.state_db,
            event_type="plugin_or_chip_influence_recorded",
            component="researcher_bridge",
            summary="unlabeled provenance mutation",
            request_id="req-observer-handoff",
            actor_id="researcher_bridge",
            facts={"keepability": "ephemeral_context"},
            provenance={},
        )

        handoff_exit, handoff_stdout, handoff_stderr = self.run_cli(
            "operator",
            "handoff-observer",
            "--home",
            str(self.home),
            "--reason",
            "observer runtime sync",
            "--json",
        )
        self.assertEqual(handoff_exit, 0, handoff_stderr)
        payload = json.loads(handoff_stdout)
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["chip_key"], "startup-yc")
        self.assertGreaterEqual(payload["packet_count"], 1)
        self.assertTrue(Path(payload["bundle_path"]).exists())
        self.assertTrue(Path(payload["result_path"]).exists())
        self.assertEqual(payload["execution"]["output"]["result"]["packet_count"], payload["packet_count"])

        handoffs_exit, handoffs_stdout, handoffs_stderr = self.run_cli(
            "operator",
            "observer-handoffs",
            "--home",
            str(self.home),
            "--json",
        )
        self.assertEqual(handoffs_exit, 0, handoffs_stderr)
        handoffs_payload = json.loads(handoffs_stdout)
        self.assertEqual(handoffs_payload["rows"][0]["handoff_id"], payload["handoff_id"])
        self.assertEqual(handoffs_payload["rows"][0]["chip_key"], "startup-yc")
        self.assertEqual(handoffs_payload["rows"][0]["status"], "completed")
        self.assertEqual(handoffs_payload["rows"][0]["packet_count"], payload["packet_count"])

        history_exit, history_stdout, history_stderr = self.run_cli(
            "operator",
            "history",
            "--home",
            str(self.home),
            "--action",
            "handoff_observer",
            "--json",
        )
        self.assertEqual(history_exit, 0, history_stderr)
        history_payload = json.loads(history_stdout)
        self.assertEqual(history_payload["rows"][0]["target_ref"], payload["handoff_id"])
        self.assertEqual(history_payload["rows"][0]["reason"], "observer runtime sync")

        events = latest_events_by_type(self.state_db, event_type="plugin_or_chip_influence_recorded", limit=20)
        self.assertTrue(
            any(
                str(event.get("component") or "") == "operator_cli"
                and str((event.get("provenance_json") or {}).get("source_kind") or "") == "chip_hook"
                and str((event.get("provenance_json") or {}).get("hook") or "") == "packets"
                for event in events
            )
        )

    def test_attachments_run_hook_executes_active_chip_evaluate(self) -> None:
        chip_root = create_fake_hook_chip(self.home)
        self.config_manager.set_path("spark.chips.roots", [str(chip_root)])

        activate_exit, _, activate_stderr = self.run_cli(
            "attachments",
            "activate-chip",
            "startup-yc",
            "--home",
            str(self.home),
        )
        self.assertEqual(activate_exit, 0, activate_stderr)

        hook_exit, hook_stdout, hook_stderr = self.run_cli(
            "attachments",
            "run-hook",
            "evaluate",
            "--home",
            str(self.home),
            "--json",
            "--payload-json",
            json.dumps({"situation": "We have growth but poor retention"}),
        )
        self.assertEqual(hook_exit, 0, hook_stderr)
        payload = json.loads(hook_stdout)
        self.assertEqual(payload["chip_key"], "startup-yc")
        self.assertTrue(payload["ok"])
        self.assertIn("Startup YC doctrine", payload["output"]["result"]["analysis"])
        events = latest_events_by_type(self.state_db, event_type="plugin_or_chip_influence_recorded", limit=10)
        self.assertTrue(
            any(
                str(event.get("component") or "") == "attachments_cli"
                and str((event.get("provenance_json") or {}).get("source_kind") or "") == "chip_hook"
                for event in events
            )
        )
        with self.state_db.connect() as conn:
            row = conn.execute(
                """
                SELECT status, close_reason
                FROM builder_runs
                WHERE run_kind = 'operator:attachments_hook:evaluate'
                ORDER BY opened_at DESC, run_id DESC
                LIMIT 1
                """
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["status"], "closed")
        self.assertEqual(row["close_reason"], "attachments_hook_completed")

    def test_attachments_run_hook_blocks_secret_like_output(self) -> None:
        chip_root = create_fake_hook_chip(self.home)
        self.config_manager.set_path("spark.chips.roots", [str(chip_root)])

        activate_exit, _, activate_stderr = self.run_cli(
            "attachments",
            "activate-chip",
            "startup-yc",
            "--home",
            str(self.home),
        )
        self.assertEqual(activate_exit, 0, activate_stderr)

        hook_exit, hook_stdout, hook_stderr = self.run_cli(
            "attachments",
            "run-hook",
            "evaluate",
            "--home",
            str(self.home),
            "--json",
            "--payload-json",
            json.dumps({"situation": "sk-abcdefghijklmnopqrstuvwxyz123456"}),
        )
        self.assertEqual(hook_stdout, "")
        self.assertNotEqual(hook_exit, 0)
        self.assertIn("blocked", hook_stderr.lower())
        self.assertTrue(latest_events_by_type(self.state_db, event_type="secret_boundary_violation", limit=10))
        with self.state_db.connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS c FROM quarantine_records").fetchone()
        self.assertGreaterEqual(int(row["c"]), 1)
        with self.state_db.connect() as conn:
            row = conn.execute(
                """
                SELECT status, close_reason
                FROM builder_runs
                WHERE run_kind = 'operator:attachments_hook:evaluate'
                ORDER BY opened_at DESC, run_id DESC
                LIMIT 1
                """
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["status"], "stalled")
        self.assertEqual(row["close_reason"], "secret_boundary_blocked")

    def test_agent_import_swarm_runs_identity_hook_and_canonicalizes_agent(self) -> None:
        chip_root = create_fake_hook_chip(self.home, chip_key="spark-swarm")
        self.config_manager.set_path("spark.chips.roots", [str(chip_root)])
        self.add_telegram_channel()

        approve_exit, _, approve_stderr = self.run_cli(
            "pairings",
            "approve",
            "telegram",
            "111",
            "--home",
            str(self.home),
            "--display-name",
            "Alice",
        )
        self.assertEqual(approve_exit, 0, approve_stderr)

        activate_exit, _, activate_stderr = self.run_cli(
            "attachments",
            "activate-chip",
            "spark-swarm",
            "--home",
            str(self.home),
        )
        self.assertEqual(activate_exit, 0, activate_stderr)

        import_exit, import_stdout, import_stderr = self.run_cli(
            "agent",
            "import-swarm",
            "--home",
            str(self.home),
            "--human-id",
            "human:telegram:111",
            "--json",
        )
        self.assertEqual(import_exit, 0, import_stderr)
        payload = json.loads(import_stdout)
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["chip_key"], "spark-swarm")
        self.assertEqual(payload["identity"]["agent_id"], "swarm-agent:111")
        self.assertTrue(Path(payload["payload_path"]).exists())
        self.assertTrue(Path(payload["result_path"]).exists())

        inspect_exit, inspect_stdout, inspect_stderr = self.run_cli(
            "agent",
            "inspect",
            "--home",
            str(self.home),
            "--human-id",
            "human:telegram:111",
            "--json",
        )
        self.assertEqual(inspect_exit, 0, inspect_stderr)
        inspect_payload = json.loads(inspect_stdout)
        self.assertEqual(inspect_payload["identity"]["agent_id"], "swarm-agent:111")
        self.assertEqual(inspect_payload["identity"]["preferred_source"], "spark_swarm")

        history_exit, history_stdout, history_stderr = self.run_cli(
            "operator",
            "history",
            "--home",
            str(self.home),
            "--action",
            "import_swarm_identity",
            "--json",
        )
        self.assertEqual(history_exit, 0, history_stderr)
        history_payload = json.loads(history_stdout)
        self.assertEqual(history_payload["rows"][0]["target_ref"], "human:telegram:111")

    def test_agent_import_personality_runs_hook_and_updates_persona_state(self) -> None:
        chip_root = create_fake_hook_chip(self.home, chip_key="spark-personality")
        self.config_manager.set_path("spark.chips.roots", [str(chip_root)])
        self.add_telegram_channel()

        approve_exit, _, approve_stderr = self.run_cli(
            "pairings",
            "approve",
            "telegram",
            "111",
            "--home",
            str(self.home),
            "--display-name",
            "Alice",
        )
        self.assertEqual(approve_exit, 0, approve_stderr)

        activate_exit, _, activate_stderr = self.run_cli(
            "attachments",
            "activate-chip",
            "spark-personality",
            "--home",
            str(self.home),
        )
        self.assertEqual(activate_exit, 0, activate_stderr)

        import_exit, import_stdout, import_stderr = self.run_cli(
            "agent",
            "import-personality",
            "--home",
            str(self.home),
            "--human-id",
            "human:telegram:111",
            "--json",
        )
        self.assertEqual(import_exit, 0, import_stderr)
        payload = json.loads(import_stdout)
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["chip_key"], "spark-personality")
        self.assertEqual(payload["persona_profile"]["persona_name"], "Alice")
        self.assertTrue(Path(payload["payload_path"]).exists())
        self.assertTrue(Path(payload["result_path"]).exists())
        self.assertTrue(Path(payload["evolver_state_path"]).exists())

        detail_exit, detail_stdout, detail_stderr = self.run_cli(
            "operator",
            "personality",
            "--home",
            str(self.home),
            "--human-id",
            "human:telegram:111",
            "--json",
        )
        self.assertEqual(detail_exit, 0, detail_stderr)
        detail_payload = json.loads(detail_stdout)
        self.assertEqual(detail_payload["agent_persona"]["persona_name"], "Alice")
        self.assertEqual(detail_payload["profile"]["personality_name"], "Alice")

        history_exit, history_stdout, history_stderr = self.run_cli(
            "operator",
            "history",
            "--home",
            str(self.home),
            "--action",
            "import_personality",
            "--json",
        )
        self.assertEqual(history_exit, 0, history_stderr)
        history_payload = json.loads(history_stdout)
        self.assertEqual(history_payload["rows"][0]["target_ref"], "agent:human:telegram:111")

    def test_researcher_bridge_includes_active_chip_evaluate_context_in_provider_fallback(self) -> None:
        self.config_manager.set_path("spark.chips.active_keys", ["startup-yc"])
        self.config_manager.set_path("spark.chips.pinned_keys", ["startup-yc"])
        self.config_manager.set_path("spark.researcher.enabled", True)

        runtime_root = self.home / "fake-researcher"
        runtime_root.mkdir(parents=True, exist_ok=True)
        config_path = runtime_root / "spark-researcher.project.json"
        config_path.write_text("{}", encoding="utf-8")
        captured: dict[str, object] = {}
        provider = RuntimeProviderResolution(
            provider_id="custom",
            provider_kind="custom",
            auth_profile_id="custom:default",
            auth_method="api_key_env",
            api_mode="openai_chat_completions",
            execution_transport="direct_http",
            base_url="https://api.minimax.io/v1",
            default_model="MiniMax-M2.7",
            secret_ref=None,
            secret_value="minimax-secret",
            source="test",
        )

        def fake_build_advisory(path: Path, task: str, *, model: str = "generic", limit: int = 4, domain: str | None = None):
            return {
                "guidance": [],
                "epistemic_status": {
                    "status": "under_supported",
                    "packet_stability": {"status": "no_belief_packets"},
                },
                "selected_packet_ids": [],
                "trace_path": "trace:under-supported",
            }

        def fake_direct_provider_prompt(*, provider, system_prompt: str, user_prompt: str, governance=None):
            captured["user_prompt"] = user_prompt
            captured["governance"] = governance
            return {"raw_response": "Tighten the user pain wedge first."}

        def fail_execute_with_research(*args, **kwargs):
            raise AssertionError("execute_with_research should not run")

        fake_chip_execution = type(
            "ChipExecution",
            (),
            {
                "ok": True,
                "chip_key": "startup-yc",
                "output": {
                    "result": {
                        "analysis": "Startup YC doctrine: focus on the narrowest urgent founder pain first.",
                        "task_type": "diagnostic_questioning",
                        "stage": "pmf_search",
                        "context_packet_ids": ["packet-1", "packet-2"],
                        "activations": [{"name": "focus_gap"}],
                        "detected_state_updates": [{"field": "stage", "value": "pmf_search"}],
                        "stage_transition_suggested": None,
                    }
                },
            },
        )()

        with patch(
            "spark_intelligence.researcher_bridge.advisory.discover_researcher_runtime_root",
            return_value=(runtime_root, "configured"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.resolve_researcher_config_path",
            return_value=config_path,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._import_build_advisory",
            return_value=fake_build_advisory,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._import_execute_with_research",
            return_value=fail_execute_with_research,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=fake_direct_provider_prompt,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._is_conversational_fallback_candidate",
            return_value=True,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
            return_value=type(
                "Selection",
                (),
                {"provider": provider, "model_family": "generic", "error": None},
            )(),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.run_first_active_chip_hook",
            return_value=fake_chip_execution,
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-chip-fallback",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-1",
                channel_kind="telegram",
                user_message="hey",
            )

        self.assertEqual(result.reply_text, "Tighten the user pain wedge first.")
        self.assertIn("[Active chip guidance]", str(captured["user_prompt"]))
        self.assertIn("chip_key=startup-yc", str(captured["user_prompt"]))
        self.assertIn("Startup YC doctrine: focus on the narrowest urgent founder pain first.", str(captured["user_prompt"]))
        self.assertIsNotNone(captured["governance"])
