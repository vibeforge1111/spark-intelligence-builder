from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch

from spark_intelligence.adapters.telegram.runtime import simulate_telegram_update
from spark_intelligence.llm_wiki import promote_llm_wiki_improvement
from spark_intelligence.researcher_bridge.advisory import build_researcher_reply

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
