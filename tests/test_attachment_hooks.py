from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from spark_intelligence.auth.runtime import RuntimeProviderResolution
from spark_intelligence.observability.store import latest_events_by_type
from spark_intelligence.researcher_bridge.advisory import build_researcher_reply

from tests.test_support import SparkTestCase


def _create_fake_hook_chip(root: Path, *, chip_key: str = "startup-yc") -> Path:
    repo_root = root / f"domain-chip-{chip_key}"
    package_root = repo_root / "src" / "fake_startup_chip"
    package_root.mkdir(parents=True, exist_ok=True)
    (package_root / "__init__.py").write_text("", encoding="utf-8")
    (package_root / "chip_hooks.py").write_text(
        """
from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("hook", choices=["evaluate", "suggest", "packets", "watchtower"])
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    if args.hook == "evaluate":
        situation = payload.get("situation", "")
        result = {
            "returncode": 0,
            "stdout": "task_type: diagnostic_questioning\\nstage: pmf_search\\ncontext_packets: 2\\nactivations: 1\\nhas_analysis: True",
            "stderr": "",
            "metrics": {"context_packet_count": 2, "activation_count": 1, "task_type": "diagnostic_questioning"},
            "result": {
                "analysis": f"Startup YC doctrine: focus on the narrowest urgent founder pain first. Situation: {situation}",
                "task_type": "diagnostic_questioning",
                "stage": "pmf_search",
                "context_packet_ids": ["packet-1", "packet-2"],
                "activations": [{"name": "focus_gap"}],
                "detected_state_updates": [{"field": "stage", "value": "pmf_search"}],
                "stage_transition_suggested": None,
            },
        }
    else:
        result = {"result": {}}

    Path(args.output).write_text(json.dumps(result, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
        """.strip(),
        encoding="utf-8",
    )
    (repo_root / "spark-chip.json").write_text(
        json.dumps(
            {
                "schema_version": "spark-chip.v1",
                "io_protocol": "spark-hook-io.v1",
                "chip_name": chip_key,
                "domain": "startup-advisory",
                "description": "Fake startup chip for hook tests.",
                "capabilities": ["evaluate", "suggest", "packets", "watchtower"],
                "commands": {
                    "evaluate": ["python", "-m", "fake_startup_chip.chip_hooks", "evaluate"],
                    "suggest": ["python", "-m", "fake_startup_chip.chip_hooks", "suggest"],
                    "packets": ["python", "-m", "fake_startup_chip.chip_hooks", "packets"],
                    "watchtower": ["python", "-m", "fake_startup_chip.chip_hooks", "watchtower"],
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return repo_root


class AttachmentHookTests(SparkTestCase):
    def test_attachments_run_hook_executes_active_chip_evaluate(self) -> None:
        chip_root = _create_fake_hook_chip(self.home)
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
        chip_root = _create_fake_hook_chip(self.home)
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
