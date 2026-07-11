from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from spark_intelligence.attachments.registry import attachment_status
from spark_intelligence.attachments.hooks import run_chip_hook, run_first_active_chip_hook
from spark_intelligence.attachments.snapshot import build_attachment_context
from spark_intelligence.auth.runtime import RuntimeProviderResolution
from spark_intelligence.observability.store import latest_events_by_type, record_event
from spark_intelligence.researcher_bridge.advisory import build_researcher_reply

from tests.test_support import SparkTestCase, create_fake_hook_chip, make_turn_intent_envelope


class AttachmentHookTests(SparkTestCase):
    def _governor_decision_for_hook(
        self,
        *,
        hook: str = "evaluate",
        request_id: str = "req-chip-hook-governor",
        owner_system: str = "spark-intelligence-builder",
        mutation_class: str = "writes_files",
        external_network: bool = False,
    ) -> dict[str, object]:
        from spark_intelligence.bridge_authority import authorize_builder_bridge_action
        from spark_intelligence.harness_contract import build_vnext_action_intent_envelope

        tool_name = hook if hook.startswith(("browser.", "voice.", "chip.")) else f"chip.{hook}"
        envelope = build_vnext_action_intent_envelope(
            surface="cli",
            actor_id_ref="human-1",
            request_id=request_id,
            source_kind="test_chip_hook_governor",
            intent_summary=f"Test authorizes {tool_name}.",
            raw_turn_summary=f"Run {tool_name} in test.",
            actions=[
                {
                    "tool_name": tool_name,
                    "owner_system": owner_system,
                    "mutation_class": mutation_class,
                    "external_network": external_network,
                    "args_path": f"builder://test/{request_id}/{tool_name}",
                }
            ],
        )
        authority = authorize_builder_bridge_action(
            {"turn_intent_envelope_vnext": envelope},
            tool_name=tool_name,
            owner_system=owner_system,
            mutation_class=mutation_class,
            external_network=external_network,
            state_db=self.state_db,
            request_id=request_id,
            actor_id="test",
            component="test",
        )
        self.assertTrue(authority.allowed, authority.reason_codes)
        self.assertIsInstance(authority.governor_decision, dict)
        return authority.governor_decision

    def _chip_evaluate_turn_intent_vnext(self, *, request_id: str = "req-chip-vnext") -> dict[str, object]:
        from spark_intelligence.harness_contract import build_vnext_action_intent_envelope

        return build_vnext_action_intent_envelope(
            surface="telegram",
            actor_id_ref="human-1",
            request_id=request_id,
            source_kind="test_active_chip_explicit",
            intent_summary="User explicitly requested startup chip guidance for the current turn.",
            raw_turn_summary="Raw active-chip test turn is offloaded in the fixture.",
            actions=[
                {
                    "tool_name": "chip.evaluate",
                    "owner_system": "spark-intelligence-builder",
                    "mutation_class": "writes_files",
                    "args_path": f"builder://active-chip/{request_id}/chip-evaluate",
                }
            ],
        )

    def test_legacy_browser_extension_hooks_are_disabled_at_execution_boundary(self) -> None:
        chip_root = create_fake_hook_chip(self.home, chip_key="spark-browser")
        self.config_manager.set_path("spark.chips.roots", [str(chip_root)])
        self.config_manager.set_path("spark.chips.active_keys", ["spark-browser"])

        with self.assertRaises(ValueError) as direct:
            run_chip_hook(self.config_manager, chip_key="spark-browser", hook="browser.status", payload={})
        with self.assertRaises(ValueError) as active:
            run_first_active_chip_hook(self.config_manager, hook="browser.status", payload={})

        self.assertIn("legacy browser extension lane is disabled", str(direct.exception))
        self.assertIn("legacy browser extension lane is disabled", str(active.exception))

    def test_attachment_status_autodiscovers_generic_spark_chip_repo(self) -> None:
        desktop_root = self.home / "Desktop"
        desktop_root.mkdir(parents=True, exist_ok=True)
        chip_root = create_fake_hook_chip(desktop_root, chip_key="spark-browser")
        generic_root = desktop_root / "spark-browser-extension"
        chip_root.rename(generic_root)

        with patch("spark_intelligence.attachments.registry.Path.home", return_value=self.home):
            scan = attachment_status(self.config_manager)

        self.assertEqual(scan.chip_source, "autodiscovered")
        self.assertTrue(any(record.key == "spark-browser" for record in scan.records))

    def test_attachment_status_ignores_configured_duplicate_autodiscovery_roots(self) -> None:
        desktop_root = self.home / "Desktop"
        canonical_root = desktop_root / "domain-chip-duplicate"
        compare_root = desktop_root / "domain-chip-duplicate-compare"
        canonical_root.mkdir(parents=True, exist_ok=True)
        compare_root.mkdir(parents=True, exist_ok=True)
        manifest = {
            "schema_version": "spark-chip.v1",
            "io_protocol": "spark-hook-io.v1",
            "chip_name": "domain-chip-duplicate",
            "description": "Duplicate test chip",
            "capabilities": ["evaluate"],
        }
        (canonical_root / "spark-chip.json").write_text(json.dumps(manifest), encoding="utf-8")
        (compare_root / "spark-chip.json").write_text(json.dumps(manifest), encoding="utf-8")
        self.config_manager.set_path("spark.chips.ignored_roots", [str(compare_root)])

        with patch("spark_intelligence.attachments.registry.Path.home", return_value=self.home):
            scan = attachment_status(self.config_manager)

        duplicate_records = [record for record in scan.records if record.key == "domain-chip-duplicate"]
        self.assertEqual(scan.chip_source, "autodiscovered")
        self.assertEqual(len(duplicate_records), 1)
        self.assertEqual(Path(duplicate_records[0].repo_root), canonical_root)
        self.assertFalse(any("duplicate chip key 'domain-chip-duplicate'" in warning for warning in scan.warnings))

    def test_build_attachment_context_includes_attached_chip_inventory(self) -> None:
        browser_root = create_fake_hook_chip(self.home, chip_key="spark-browser")
        swarm_root = create_fake_hook_chip(self.home, chip_key="spark-swarm")
        self.config_manager.set_path("spark.chips.roots", [str(browser_root.parent)])
        self.config_manager.set_path("spark.chips.active_keys", ["spark-browser"])
        self.config_manager.set_path("spark.chips.pinned_keys", ["spark-browser"])

        context = build_attachment_context(self.config_manager)

        self.assertIn("spark-browser", context["attached_chip_keys"])
        self.assertIn("spark-swarm", context["attached_chip_keys"])
        records = {str(record["key"]): record for record in context["attached_chip_records"]}
        self.assertEqual(records["spark-browser"]["attachment_mode"], "pinned")
        self.assertEqual(records["spark-swarm"]["attachment_mode"], "available")
        self.assertIn("evaluate", records["spark-browser"]["hook_names"])

    def test_attachments_run_hook_blocks_legacy_browser_status_hook(self) -> None:
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

        hook_exit, _, hook_stderr = self.run_cli(
            "attachments",
            "run-hook",
            "browser.status",
            "--home",
            str(self.home),
            "--json",
            "--payload-json",
            json.dumps({"request_id": "browser-status-test", "target": {"browser_family": "brave"}}),
        )
        self.assertEqual(hook_exit, 2)
        self.assertIn("legacy browser extension lane is disabled", hook_stderr)
        self.assertIn("browser-use MCP lane", hook_stderr)

    def test_run_chip_hook_without_governor_does_not_call_subprocess(self) -> None:
        chip_root = create_fake_hook_chip(self.home)
        self.config_manager.set_path("spark.chips.roots", [str(chip_root)])

        with patch("spark_intelligence.attachments.hooks.run_governed_command") as run_mock:
            with self.assertRaises(RuntimeError) as blocked:
                run_chip_hook(
                    self.config_manager,
                    chip_key="startup-yc",
                    hook="evaluate",
                    payload={"situation": "no authority"},
                )

        run_mock.assert_not_called()
        self.assertIn("requires Harness Core Governor authority", str(blocked.exception))

    def test_run_chip_hook_with_governor_executes_subprocess(self) -> None:
        chip_root = create_fake_hook_chip(self.home)
        self.config_manager.set_path("spark.chips.roots", [str(chip_root)])
        governor_decision = self._governor_decision_for_hook(request_id="req-chip-hook-valid")

        execution = run_chip_hook(
            self.config_manager,
            chip_key="startup-yc",
            hook="evaluate",
            payload={"situation": "We have growth but poor retention"},
            governor_decision=governor_decision,
        )

        self.assertTrue(execution.ok)
        self.assertEqual(execution.governor_verification["allowed"], True)
        self.assertIn("Startup YC doctrine", execution.output["result"]["analysis"])

    def test_run_chip_hook_accepts_canonical_bridge_governor_when_core_schema_lags(self) -> None:
        chip_root = create_fake_hook_chip(self.home)
        self.config_manager.set_path("spark.chips.roots", [str(chip_root)])
        governor_decision = self._governor_decision_for_hook(request_id="req-chip-hook-bridge-valid")

        with patch(
            "spark_intelligence.attachments.hooks.verify_governor_tool_authority",
            return_value={
                "schema_version": "governor-consumer-verification-v1",
                "allowed": False,
                "reason_codes": ["invalid_governor_decision"],
                "source_kind": "governor_decision",
            },
        ):
            execution = run_chip_hook(
                self.config_manager,
                chip_key="startup-yc",
                hook="evaluate",
                payload={"situation": "Retention is weak but the founder has usage."},
                governor_decision=governor_decision,
            )

        self.assertTrue(execution.ok)
        self.assertEqual(execution.governor_verification["allowed"], True)
        self.assertEqual(execution.governor_verification["source_kind"], "builder_bridge_governor_decision")
        self.assertIn("Startup YC doctrine", execution.output["result"]["analysis"])

    def test_run_chip_hook_rejects_bridge_governor_with_mismatched_ledger(self) -> None:
        chip_root = create_fake_hook_chip(self.home)
        self.config_manager.set_path("spark.chips.roots", [str(chip_root)])
        governor_decision = json.loads(
            json.dumps(self._governor_decision_for_hook(request_id="req-chip-hook-bridge-trap"))
        )
        governor_decision["tool_ledgers"][0]["capability_id"] = "capability:spark-intelligence-builder:chip.other"

        with patch(
            "spark_intelligence.attachments.hooks.verify_governor_tool_authority",
            return_value={
                "schema_version": "governor-consumer-verification-v1",
                "allowed": False,
                "reason_codes": ["invalid_governor_decision"],
                "source_kind": "governor_decision",
            },
        ), patch("spark_intelligence.attachments.hooks.run_governed_command") as run_mock:
            with self.assertRaises(RuntimeError) as blocked:
                run_chip_hook(
                    self.config_manager,
                    chip_key="startup-yc",
                    hook="evaluate",
                    payload={"situation": "This should not execute."},
                    governor_decision=governor_decision,
                )

        run_mock.assert_not_called()
        self.assertIn("bridge_governor_missing_matching_tool_ledger", str(blocked.exception))

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

    def test_attachments_run_hook_accepts_payload_file(self) -> None:
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

        payload_path = self.home / "evaluate.payload.json"
        payload_path.write_text(
            json.dumps({"situation": "We have growth but poor retention from agencies"}, indent=2),
            encoding="utf-8-sig",
        )

        hook_exit, hook_stdout, hook_stderr = self.run_cli(
            "attachments",
            "run-hook",
            "evaluate",
            "--home",
            str(self.home),
            "--json",
            "--payload-file",
            str(payload_path),
        )
        self.assertEqual(hook_exit, 0, hook_stderr)
        payload = json.loads(hook_stdout)
        self.assertEqual(payload["chip_key"], "startup-yc")
        self.assertTrue(payload["ok"])
        self.assertIn("agencies", payload["output"]["result"]["analysis"])

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
            json.dumps({"situation": "sk-" + "abcdefghijklmnopqrstuvwxyz123456"}),
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
        self.assertEqual(payload["persona_profile"]["persona_name"], "Founder Operator")
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
        self.assertEqual(detail_payload["agent_persona"]["persona_name"], "Founder Operator")
        self.assertEqual(detail_payload["profile"]["personality_name"], "Founder Operator")

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
        chip_root = create_fake_hook_chip(self.home, chip_key="startup-yc")
        self.config_manager.set_path("spark.chips.roots", [str(chip_root)])
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

        def fake_direct_provider_prompt(*, provider, system_prompt: str, user_prompt: str, governance=None, **kwargs):
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
            "spark_intelligence.researcher_bridge.advisory.run_chip_hook",
            return_value=fake_chip_execution,
        ) as run_hook_mock:
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-chip-fallback",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-1",
                channel_kind="telegram",
                user_message="What should this startup focus on next?",
                turn_intent_envelope=make_turn_intent_envelope(
                    turn_id="turn:chip-fallback",
                    trace_id="trace:chip-fallback",
                ),
                turn_intent_envelope_vnext=self._chip_evaluate_turn_intent_vnext(
                    request_id="req-chip-fallback"
                ),
            )

        self.assertEqual(result.reply_text, "Tighten the user pain wedge first.")
        run_hook_mock.assert_called_once()
        self.assertIsInstance(run_hook_mock.call_args.kwargs.get("governor_decision"), dict)
        self.assertIn("[Active chip guidance]", str(captured["user_prompt"]))
        self.assertIn("chip_key=startup-yc", str(captured["user_prompt"]))
        self.assertIn("Startup YC doctrine: focus on the narrowest urgent founder pain first.", str(captured["user_prompt"]))
        self.assertIsNotNone(captured["governance"])

    def test_researcher_bridge_without_turn_intent_does_not_run_active_chip_evaluate(self) -> None:
        chip_root = create_fake_hook_chip(self.home, chip_key="startup-yc")
        self.config_manager.set_path("spark.chips.roots", [str(chip_root)])
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

        def fake_direct_provider_prompt(*, provider, system_prompt: str, user_prompt: str, governance=None, **kwargs):
            captured["user_prompt"] = user_prompt
            captured["governance"] = governance
            return {"raw_response": "We can reason about the startup without running chip hooks."}

        def fail_execute_with_research(*args, **kwargs):
            raise AssertionError("execute_with_research should not run")

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
            "spark_intelligence.researcher_bridge.advisory.run_chip_hook",
        ) as run_hook_mock:
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-chip-no-authority",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-1",
                channel_kind="telegram",
                user_message="What should this startup focus on next?",
            )

        self.assertEqual(result.reply_text, "We can reason about the startup without running chip hooks.")
        run_hook_mock.assert_not_called()
        self.assertNotIn("[Active chip guidance]", str(captured["user_prompt"]))
        policy_blocks = latest_events_by_type(self.state_db, event_type="policy_gate_blocked", limit=10)
        self.assertTrue(
            any(event.get("reason_code") == "active_chip_evaluate_authority_blocked" for event in policy_blocks)
        )

    def test_researcher_bridge_with_legacy_turn_intent_does_not_run_active_chip_evaluate(self) -> None:
        chip_root = create_fake_hook_chip(self.home, chip_key="startup-yc")
        self.config_manager.set_path("spark.chips.roots", [str(chip_root)])
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

        def fake_direct_provider_prompt(*, provider, system_prompt: str, user_prompt: str, governance=None, **kwargs):
            captured["user_prompt"] = user_prompt
            captured["governance"] = governance
            return {"raw_response": "We can discuss the startup without running chip hooks."}

        def fail_execute_with_research(*args, **kwargs):
            raise AssertionError("execute_with_research should not run")

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
            "spark_intelligence.researcher_bridge.advisory.run_chip_hook",
        ) as run_hook_mock:
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-chip-legacy-only",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-1",
                channel_kind="telegram",
                user_message="What should this startup focus on next?",
                turn_intent_envelope=make_turn_intent_envelope(
                    turn_id="turn:chip-legacy-only",
                    trace_id="trace:chip-legacy-only",
                ),
            )

        self.assertEqual(result.reply_text, "We can discuss the startup without running chip hooks.")
        run_hook_mock.assert_not_called()
        self.assertNotIn("[Active chip guidance]", str(captured["user_prompt"]))
        policy_blocks = latest_events_by_type(self.state_db, event_type="policy_gate_blocked", limit=10)
        matching = [
            event
            for event in policy_blocks
            if event.get("reason_code") == "active_chip_evaluate_authority_blocked"
        ]
        self.assertTrue(matching)
        self.assertEqual(matching[0]["facts_json"]["authority_state"], "legacy_present_without_governor")

    def test_researcher_bridge_injects_browser_search_evidence_from_non_active_chip(self) -> None:
        chip_root = create_fake_hook_chip(self.home, chip_key="spark-browser")
        self.config_manager.set_path("spark.chips.roots", [str(chip_root)])
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

        def fake_direct_provider_prompt(*, provider, system_prompt: str, user_prompt: str, governance=None, **kwargs):
            captured["system_prompt"] = system_prompt
            captured["user_prompt"] = user_prompt
            return {"raw_response": "BTC is about $84,321.18 USD. Source: https://www.coingecko.com/en/coins/bitcoin"}

        def fail_execute_with_research(*args, **kwargs):
            raise AssertionError("execute_with_research should not run")

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
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-browser-fallback",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-1",
                channel_kind="telegram",
                user_message="Search the web and tell me the current BTC price in USD with the source you used.",
                turn_intent_envelope=make_turn_intent_envelope(
                    turn_id="turn:browser-fallback",
                    trace_id="trace:browser-fallback",
                    owner_system="spark-browser",
                    action="browser.navigate",
                    allowed_tools=[
                        "answer.compose",
                        "browser.status",
                        "browser.navigate",
                        "browser.tab.wait",
                        "browser.page.dom_extract",
                        "browser.page.interactives.list",
                        "browser.page.text_extract",
                        "browser.page.snapshot",
                    ],
                    mutation_classes_allowed=["none", "read_only", "external_network"],
                    can_mutate_files=False,
                    can_use_external_network=True,
                ),
            )

        self.assertIn("Source: https://www.coingecko.com/en/coins/bitcoin", result.reply_text)
        self.assertRegex(str(captured["system_prompt"]), r"Do not (?:say|claim) you cannot browse")
        self.assertNotIn("[Browser search evidence]", str(captured["user_prompt"]))
        self.assertIn("spark-browser mode=available router_invokable=no", str(captured["user_prompt"]))

    def test_researcher_bridge_returns_explicit_browser_permission_block(self) -> None:
        self.config_manager.set_path("spark.researcher.enabled", True)
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

        with patch(
            "spark_intelligence.researcher_bridge.advisory._build_browser_search_context",
            return_value={
                "context": "",
                "blocked_reply": (
                    "Web search is blocked because the legacy browser extension does not have host access "
                    "for https://duckduckgo.com. Open the legacy extension popup and grant explicit site "
                    "access for https://duckduckgo.com, then retry the search."
                ),
                "blocked_code": "HOST_PERMISSION_REQUIRED",
            },
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
            return_value=type(
                "Selection",
                (),
                {"provider": provider, "model_family": "generic", "error": None},
            )(),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("execute_direct_provider_prompt should not run"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._import_execute_with_research",
            side_effect=AssertionError("execute_with_research should not run"),
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-browser-permission-block",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-1",
                channel_kind="telegram",
                user_message="Search the web and tell me the current BTC price in USD with the source you used.",
            )

        self.assertEqual(result.mode, "blocked")
        self.assertEqual(result.routing_decision, "browser_permission_required")
        self.assertEqual(result.escalation_hint, "grant_origin_access")
        self.assertIn("does not have host access for https://duckduckgo.com", result.reply_text)
        self.assertIn("grant explicit site access for https://duckduckgo.com", result.reply_text)

    def test_researcher_bridge_returns_explicit_browser_session_unavailable_block(self) -> None:
        self.config_manager.set_path("spark.researcher.enabled", True)
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

        with patch(
            "spark_intelligence.researcher_bridge.advisory._build_browser_search_context",
            return_value={
                "context": "",
                "blocked_reply": (
                    "Web search is currently unavailable because the legacy browser extension live session "
                    "is disconnected. Reload or reconnect the extension, then retry the search."
                ),
                "blocked_code": "BROWSER_SESSION_UNAVAILABLE",
            },
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
            return_value=type(
                "Selection",
                (),
                {"provider": provider, "model_family": "generic", "error": None},
            )(),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("execute_direct_provider_prompt should not run"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._import_execute_with_research",
            side_effect=AssertionError("execute_with_research should not run"),
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-browser-session-block",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-1",
                channel_kind="telegram",
                user_message="Search the web and tell me the current BTC price in USD with the source you used.",
            )

        self.assertEqual(result.mode, "blocked")
        self.assertEqual(result.routing_decision, "browser_unavailable")
        self.assertEqual(result.escalation_hint, "reconnect_browser_session")
        self.assertIn("live session is disconnected", result.reply_text)
