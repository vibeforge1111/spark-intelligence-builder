from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from spark_intelligence.attachments.registry import _resolve_chip_roots
from spark_intelligence.build_quality_review import _known_dashboard_repo_path
from spark_intelligence.memory import architecture_benchmark, knowledge_base, sdk_maintenance, shadow_replay
from spark_intelligence.researcher_bridge.advisory import discover_researcher_runtime_root
from spark_intelligence.swarm_bridge.local import _resolve_swarm_runtime_root

from tests.test_support import SparkTestCase


class InstalledRuntimeDiscoveryTests(SparkTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.user_home = self.home / "user"
        self.spark_home = self.user_home / ".spark"
        self.user_home.mkdir(parents=True)

    def _module_source(self, module_name: str) -> Path:
        source = self.spark_home / "modules" / module_name / "source"
        source.mkdir(parents=True)
        return source

    def test_researcher_prefers_installed_module_source_before_legacy_desktop(self) -> None:
        source = self._module_source("spark-researcher")

        with patch.dict(os.environ, {"SPARK_HOME": str(self.spark_home)}), patch(
            "pathlib.Path.home", return_value=self.user_home
        ):
            runtime_root, source_kind = discover_researcher_runtime_root(self.config_manager)

        self.assertEqual(runtime_root, source.resolve())
        self.assertEqual(source_kind, "installed_module")

    def test_researcher_uses_platform_safe_spark_fallback(self) -> None:
        fallback = self.user_home / ".spark" / "spark-researcher"
        fallback.mkdir(parents=True)

        with patch("pathlib.Path.home", return_value=self.user_home):
            runtime_root, source_kind = discover_researcher_runtime_root(
                self.config_manager
            )

        self.assertEqual(runtime_root, fallback)
        self.assertEqual(source_kind, "autodiscovered")

    def test_attachment_discovery_prefers_installed_chip_root_before_legacy_desktop(self) -> None:
        chip = self.spark_home / "chips" / "domain-chip-installed-proof"
        chip.mkdir(parents=True)
        (chip / "spark-chip.json").write_text("{}", encoding="utf-8")

        with patch.dict(os.environ, {"SPARK_HOME": str(self.spark_home)}), patch(
            "pathlib.Path.home", return_value=self.user_home
        ):
            roots, source_kind = _resolve_chip_roots(self.config_manager)

        self.assertEqual(roots, [chip.resolve()])
        self.assertEqual(source_kind, "installed")

    def test_explicit_spark_home_does_not_mix_default_home_chips(self) -> None:
        configured_chip = self.spark_home / "chips" / "domain-chip-configured"
        configured_chip.mkdir(parents=True)
        (configured_chip / "spark-chip.json").write_text("{}", encoding="utf-8")
        default_home = self.home / "default-user"
        default_chip = default_home / ".spark" / "chips" / "domain-chip-default"
        default_chip.mkdir(parents=True)
        (default_chip / "spark-chip.json").write_text("{}", encoding="utf-8")

        with patch.dict(os.environ, {"SPARK_HOME": str(self.spark_home)}), patch(
            "pathlib.Path.home", return_value=default_home
        ):
            roots, source_kind = _resolve_chip_roots(self.config_manager)

        self.assertEqual(roots, [configured_chip.resolve()])
        self.assertEqual(source_kind, "installed")

    def test_memory_tools_use_installed_domain_chip_source_before_legacy_desktop(self) -> None:
        source = self._module_source("domain-chip-memory")
        execution = SimpleNamespace(stdout="{}", stderr="", exit_code=0, ok=True)
        cases = (
            (knowledge_base, knowledge_base._run_domain_chip_memory_cli),
            (shadow_replay, shadow_replay._run_domain_chip_memory_cli),
            (sdk_maintenance, sdk_maintenance._run_domain_chip_memory_cli),
        )

        with patch.dict(os.environ, {"SPARK_HOME": str(self.spark_home)}), patch(
            "pathlib.Path.home", return_value=self.user_home
        ):
            for module, runner in cases:
                with self.subTest(module=module.__name__), patch.object(
                    module, "DEFAULT_VALIDATOR_ROOT", self.user_home / "Desktop" / "missing", create=True
                ), patch.object(
                    module, "DEFAULT_MAINTENANCE_VALIDATOR_ROOT", self.user_home / "Desktop" / "missing", create=True
                ), patch.object(module, "run_governed_command", return_value=execution) as governed:
                    result = runner("proof-command")
                    self.assertEqual(result.get("stderr", ""), "")
                    self.assertEqual(Path(governed.call_args.kwargs["cwd"]), source.resolve())

    def test_architecture_benchmark_uses_installed_domain_chip_source(self) -> None:
        source = self._module_source("domain-chip-memory")
        output_dir = self.home / "benchmark-output"

        with patch.dict(os.environ, {"SPARK_HOME": str(self.spark_home)}), patch(
            "pathlib.Path.home", return_value=self.user_home
        ), patch.object(
            architecture_benchmark,
            "inspect_memory_sdk_runtime",
            return_value={"runtime_class": "proof", "runtime_memory_architecture": "proof"},
        ), patch.object(
            architecture_benchmark,
            "_run_product_memory_scorecards",
            return_value=([], {}),
        ) as scorecards:
            architecture_benchmark.benchmark_memory_architectures(
                config_manager=self.config_manager,
                output_dir=output_dir,
                baseline_names=["summary_synthesis_memory"],
            )

        self.assertEqual(scorecards.call_args.args[0], source.resolve())

    def test_quality_dashboard_uses_installed_module_source(self) -> None:
        source = self._module_source("spark-memory-quality-dashboard")

        with patch.dict(os.environ, {"SPARK_HOME": str(self.spark_home)}), patch(
            "pathlib.Path.home", return_value=self.user_home
        ):
            resolved = _known_dashboard_repo_path(self.config_manager)

        self.assertEqual(resolved, str(source.resolve()))

    def test_quality_dashboard_uses_platform_safe_spark_fallback(self) -> None:
        fallback = (
            self.user_home
            / ".spark"
            / "memory"
            / "spark-memory-quality-dashboard"
        )
        fallback.mkdir(parents=True)

        with patch("pathlib.Path.home", return_value=self.user_home):
            resolved = _known_dashboard_repo_path(self.config_manager)

        self.assertEqual(resolved, str(fallback))

    def test_swarm_does_not_invent_unregistered_spark_home_runtime(self) -> None:
        invented = self.spark_home / "spark-swarm"
        invented.mkdir(parents=True)

        with patch.dict(os.environ, {"SPARK_HOME": str(self.spark_home)}), patch(
            "pathlib.Path.home", return_value=self.user_home
        ):
            with self.assertRaisesRegex(RuntimeError, "not configured"):
                _resolve_swarm_runtime_root(self.config_manager)
