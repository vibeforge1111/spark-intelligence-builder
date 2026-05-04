from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

from spark_intelligence.adapters.telegram.runtime import simulate_telegram_update
from spark_intelligence.llm_wiki import promote_llm_wiki_improvement
from spark_intelligence.researcher_bridge.advisory import build_researcher_reply
from spark_intelligence.self_awareness import build_live_telegram_regression_cadence

from tests.test_support import SparkTestCase, make_telegram_update


MATRIX_PATH = Path(__file__).resolve().parents[1] / "ops" / "natural-language-live-commands.json"


class NaturalLanguageRouteEvalMatrixTests(SparkTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.config_manager.set_path("spark.researcher.enabled", True)
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)
        self.config_manager.set_path("spark.swarm.enabled", False)
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])
        self._seed_wiki_candidate()
        self._seed_build_quality_repo()

    def test_route_matrix_cases_match_actual_routing(self) -> None:
        matrix = _load_matrix()
        with patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("route matrix cases should not call the provider"),
        ):
            for index, case in enumerate(matrix["cases"], start=1):
                with self.subTest(case=case["id"]):
                    result = self._run_case(case, index=index)
                    self.assertEqual(result["mode"], case["expected_mode"])
                    self.assertEqual(result["routing_decision"], case["expected_routing_decision"])
                    self.assertEqual(result["promotion_disposition"], case["expected_promotion_disposition"])
                    expected_keepability = case.get("expected_output_keepability")
                    if expected_keepability:
                        self.assertEqual(result["output_keepability"], expected_keepability)
                    for needle in case.get("response_contains", []):
                        self.assertIn(needle, result["reply_text"])
                    for needle in case.get("response_not_contains", []):
                        self.assertNotIn(needle, result["reply_text"])

    def test_matrix_declares_required_guardrails(self) -> None:
        matrix = _load_matrix()
        guardrails = set(matrix["guardrails"])
        self.assertIn("wiki remains supporting_not_authoritative", guardrails)
        self.assertIn("self-awareness answers are not durable memory", guardrails)
        self.assertIn("debug explanations are operator_debug_only", guardrails)
        self.assertIn(
            "explicit user memory writes may update governed memory while bridge replies remain not_promotable",
            guardrails,
        )
        self.assertEqual(
            matrix["release_cadence"]["evidence_boundary"],
            "Only real Telegram runtime traces with simulation=false count as live evidence.",
        )
        suites = {str(case.get("suite") or "") for case in matrix["cases"]}
        self.assertIn("self_awareness", suites)
        self.assertIn("llm_wiki", suites)
        self.assertIn("telegram_commands", suites)

    def test_live_telegram_cadence_report_declares_release_gate_and_artifacts(self) -> None:
        result = build_live_telegram_regression_cadence(config_manager=self.config_manager)
        payload = result.payload

        self.assertEqual(payload["kind"], "live_telegram_regression_cadence")
        self.assertEqual(payload["status"], "needs_live_evidence")
        self.assertEqual(payload["authority"], "observability_non_authoritative")
        self.assertEqual(payload["memory_policy"], "typed_report_not_chat_memory")
        self.assertTrue(payload["report_written"])
        self.assertTrue((self.home / "artifacts" / "live-telegram-regression" / "cadence-latest.json").exists())
        runbook_path = self.home / "artifacts" / "live-telegram-regression" / "prompt-pack-latest.txt"
        self.assertEqual(payload["operator_runbook"]["path"], str(runbook_path))
        self.assertTrue(payload["operator_runbook"]["written"])
        self.assertTrue(runbook_path.exists())
        runbook_text = runbook_path.read_text(encoding="utf-8")
        self.assertIn("1. /self", runbook_text)
        self.assertIn("12. Why did you answer that way?", runbook_text)
        self.assertIn("Simulation, soak, and CLI traces do not count", runbook_text)
        self.assertIn("SinceUtc:", runbook_text)
        self.assertIn("verify_live_traces", runbook_text)
        self.assertEqual(payload["operator_runbook"]["since_utc"], payload["checked_at"])
        self.assertEqual(payload["summary"]["case_count"], len(_load_matrix()["cases"]))
        suite_ids = {row["suite"] for row in payload["suites"]}
        self.assertIn("self_awareness", suite_ids)
        self.assertIn("llm_wiki", suite_ids)
        self.assertIn("simulation=false", payload["artifact_contract"]["trace_requirements"])
        self.assertIn("trace_eligibility", payload["artifact_contract"]["required_fields"])
        self.assertIn("since_utc", payload["artifact_contract"]["required_fields"])
        self.assertIn("evaluated_traces", payload["artifact_contract"]["required_fields"])
        self.assertIn("recorded_at >= cadence checked_at", " ".join(payload["artifact_contract"]["trace_requirements"]))
        self.assertIn("live_telegram_evidence_missing", payload["warnings"])
        self.assertIn("-PrintPromptsOnly", payload["commands"]["print_prompts"])
        self.assertIn("-Json", payload["commands"]["verify_live_traces"])
        self.assertIn("-OutputDir", payload["commands"]["verify_live_traces"])
        self.assertIn("-SinceUtc", payload["commands"]["verify_live_traces"])
        self.assertIn(payload["checked_at"], payload["commands"]["verify_live_traces"])

    def test_live_telegram_cadence_surfaces_failed_trace_eligibility(self) -> None:
        evidence_dir = self.home / "artifacts" / "live-telegram-regression"
        evidence_dir.mkdir(parents=True)
        latest = {
            "ok": False,
            "spark_home": str(self.home),
            "scanned_traces": 5,
            "evaluated_traces": 3,
            "scanned_runtime_traces": 0,
            "matched": 0,
            "expected": 12,
            "missing": "slash self",
            "recorded_at": "2026-05-02T15:46:40Z",
            "trace_eligibility": {
                "scanned_traces": 5,
                "eligible_runtime_traces": 0,
                "ignored_simulation_traces": 5,
                "ignored_non_runtime_surface_traces": 5,
                "ignored_non_telegram_request_traces": 5,
                "latest_trace": {
                    "recorded_at": "2026-04-26T17:54:20+00:00",
                    "simulation": "True",
                    "origin_surface": "simulation_cli",
                    "request_id": "sim:1777226044853946",
                },
                "latest_eligible_runtime_trace": None,
            },
            "next_action": "Run with -PrintPromptsOnly, send the prompts to the live Spark Telegram bot in order, then rerun this verifier.",
        }
        (evidence_dir / "latest.json").write_text(json.dumps(latest, indent=2), encoding="utf-8-sig")

        result = build_live_telegram_regression_cadence(config_manager=self.config_manager)
        payload = result.payload

        self.assertEqual(payload["status"], "needs_live_evidence")
        self.assertEqual(payload["latest_evidence_status"], "failed_or_incomplete")
        self.assertIn("live_telegram_evidence_failed_or_incomplete", payload["warnings"])
        self.assertEqual(payload["latest_evidence"]["missing"], "slash self")
        self.assertEqual(payload["latest_evidence"]["scanned_traces"], 5)
        self.assertEqual(payload["latest_evidence"]["evaluated_traces"], 3)
        self.assertEqual(payload["latest_evidence"]["scanned_runtime_traces"], 0)
        self.assertEqual(payload["latest_evidence"]["trace_eligibility"]["ignored_simulation_traces"], 5)
        self.assertEqual(
            payload["latest_evidence"]["trace_eligibility"]["latest_trace"]["origin_surface"],
            "simulation_cli",
        )
        self.assertIn("PrintPromptsOnly", payload["latest_evidence"]["next_action"])
        text = result.to_text()
        self.assertIn("latest_missing: slash self", text)
        self.assertIn("eligible_runtime_traces: 0", text)
        self.assertIn("prompt_runbook:", text)
        self.assertIn("next_action: Run with -PrintPromptsOnly", text)

    def test_self_live_telegram_cadence_cli_emits_machine_readable_contract(self) -> None:
        exit_code, stdout, stderr = self.run_cli(
            "self",
            "live-telegram-cadence",
            "--home",
            str(self.home),
            "--json",
        )

        self.assertEqual(exit_code, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["kind"], "live_telegram_regression_cadence")
        self.assertEqual(payload["status"], "needs_live_evidence")
        self.assertTrue(payload["report_written"])
        self.assertIn("self_awareness_releases_need_live_telegram_evidence", payload["promotion_gate"])

    def _run_case(self, case: dict[str, object], *, index: int) -> dict[str, str]:
        surface = str(case["surface"])
        prompt = str(case["prompt"])
        if surface == "telegram_runtime":
            result = simulate_telegram_update(
                config_manager=self.config_manager,
                state_db=self.state_db,
                update_payload=make_telegram_update(
                    update_id=7000 + index,
                    user_id="111",
                    username="alice",
                    text=prompt,
                ),
            )
            self.assertTrue(result.ok)
            detail = result.detail
            return {
                "mode": str(detail.get("bridge_mode") or ""),
                "routing_decision": str(detail.get("routing_decision") or ""),
                "promotion_disposition": str(detail.get("promotion_disposition") or "not_promotable"),
                "output_keepability": str(detail.get("output_keepability") or ""),
                "reply_text": str(detail.get("response_text") or ""),
            }
        if surface == "builder_bridge":
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id=f"req-route-matrix-{index}-{case['id']}",
                agent_id="agent-1",
                human_id="human:telegram:111",
                session_id="session:route-matrix",
                channel_kind="telegram",
                user_message=prompt,
            )
            return {
                "mode": result.mode,
                "routing_decision": str(result.routing_decision or ""),
                "promotion_disposition": result.promotion_disposition,
                "output_keepability": result.output_keepability,
                "reply_text": result.reply_text,
            }
        raise AssertionError(f"unknown matrix surface: {surface}")

    def _seed_wiki_candidate(self) -> None:
        promote_llm_wiki_improvement(
            config_manager=self.config_manager,
            title="Route matrix candidate",
            summary="Spark should preserve route authority boundaries in natural-language evals.",
            evidence_refs=["pytest tests/test_natural_language_route_eval_matrix.py::route_matrix"],
            source_refs=["operator_session:self-awareness-hardening"],
        )

    def _seed_build_quality_repo(self) -> None:
        repo_root = self.home / "spawner-ui"
        route_dir = repo_root / "src" / "routes" / "memory-quality"
        route_dir.mkdir(parents=True)
        (repo_root / "package.json").write_text('{"scripts":{"test":"vitest"}}', encoding="utf-8")
        (route_dir / "+page.svelte").write_text("<h1>Memory Quality</h1>\n", encoding="utf-8")
        subprocess.run(["git", "init"], cwd=repo_root, check=True, capture_output=True, text=True)
        subprocess.run(["git", "add", "."], cwd=repo_root, check=True, capture_output=True, text=True)
        subprocess.run(
            [
                "git",
                "-c",
                "user.email=spark-tests@example.com",
                "-c",
                "user.name=Spark Tests",
                "commit",
                "-m",
                "Initial spawner fixture",
            ],
            cwd=repo_root,
            check=True,
            capture_output=True,
            text=True,
        )
        (route_dir / "+page.svelte").write_text("<h1>Memory Quality Dashboard</h1>\n", encoding="utf-8")
        self.config_manager.set_path("spark.local_projects.include_known_spark_repos", False)
        self.config_manager.set_path("spark.local_projects.include_attachment_repos", False)
        self.config_manager.set_path("spark.local_projects.roots", [str(repo_root)])


def _load_matrix() -> dict[str, object]:
    return json.loads(MATRIX_PATH.read_text(encoding="utf-8"))
