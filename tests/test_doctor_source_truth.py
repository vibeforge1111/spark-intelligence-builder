from __future__ import annotations

import tomllib
from pathlib import Path
from unittest.mock import patch

from spark_intelligence.doctor.checks import _safe_doctor_error_detail, run_doctor
from spark_intelligence.observability.store import persist_bound_ledger

from tests.test_support import SparkTestCase


class DoctorSourceTruthTests(SparkTestCase):
    def _checks(self):
        return {check.name: check for check in run_doctor(self.config_manager, self.state_db).checks}

    def _use_modules_root(self) -> Path:
        root = self.home / "module-registry"
        root.mkdir(parents=True, exist_ok=True)
        self.config_manager.set_path("spark.local_projects.module_roots", [str(root)])
        return root

    def _write_builder_install(
        self,
        root: Path,
        *,
        commit: str,
        license_name: str = "MIT",
        needs_harness: bool = True,
        harness_dependency: bool = True,
        canonical: bool | None = None,
    ) -> None:
        source = root / "source"
        source.mkdir(parents=True, exist_ok=True)
        manifest = [
            "[module]",
            'name = "spark-intelligence-builder"',
            f'license = "{license_name}"',
            "",
            "[needs]",
            f'modules = {["spark-harness-core"] if needs_harness else []!r}'.replace("'", '"'),
            "",
        ]
        if canonical is not None:
            manifest.extend(
                [
                    "[source_truth]",
                    f"canonical = {str(canonical).lower()}",
                    'mirror_of = "spark-intelligence-builder"',
                    "",
                ]
            )
        (source / "spark.toml").write_text("\n".join(manifest), encoding="utf-8")
        dependencies = ['"jsonschema>=4.22.0"', '"referencing>=0.35.0"']
        if harness_dependency:
            dependencies.append(
                '"spark-harness-core @ git+https://github.com/vibeforge1111/spark-harness-core.git@aa19fd7e49151c9df9e76e38f32da4aba7870bdf"'
            )
        (source / "pyproject.toml").write_text(
            "[project]\nname = \"spark-intelligence\"\nlicense = \"MIT\"\ndependencies = ["
            + ",".join(dependencies)
            + "]\n",
            encoding="utf-8",
        )
        git = source / ".git"
        git.mkdir(exist_ok=True)
        (git / "HEAD").write_text(commit, encoding="utf-8")

    def test_doctor_error_detail_redacts_secrets_paths_and_excess_output(self) -> None:
        secret = "sk-proj-" + "A" * 30
        detail = _safe_doctor_error_detail(
            RuntimeError(f"failed at /Users/alice/private/config.yaml with {secret} " + "x" * 240)
        )

        self.assertIn("<local-path>", detail)
        self.assertIn("<redacted api key>", detail)
        self.assertIn("[truncated]", detail)
        self.assertNotIn("/Users/alice", detail)
        self.assertNotIn(secret, detail)
        self.assertLessEqual(len(detail), 215)

    def test_doctor_reports_harness_core_runtime_status(self) -> None:
        with (
            patch("spark_intelligence.harness_contract.HARNESS_CORE_AVAILABLE", False),
            patch("spark_intelligence.harness_contract.HARNESS_CORE_IMPORT_ERROR", "No module named spark_harness_core"),
        ):
            checks = self._checks()

        self.assertFalse(checks["harness-core"].ok)
        self.assertIn("No module named spark_harness_core", checks["harness-core"].detail)

    def test_doctor_treats_empty_tool_ledger_as_fresh_not_failed(self) -> None:
        check = self._checks()["tool-call-ledger-adoption"]

        self.assertTrue(check.ok)
        self.assertIn("total=0", check.detail)

    def test_doctor_counts_existing_canonical_ledgers_by_surface(self) -> None:
        for surface in ("telegram", "builder"):
            persist_bound_ledger(
                self.state_db,
                row={
                    "ledger_id": f"ledger:doctor:{surface}",
                    "surface": surface,
                    "ledger_json": {"ledger_id": f"ledger:doctor:{surface}", "surface": surface},
                },
                component="doctor-test",
            )

        check = self._checks()["tool-call-ledger-adoption"]

        self.assertTrue(check.ok)
        self.assertIn("total=2", check.detail)
        self.assertIn("builder=1", check.detail)
        self.assertIn("telegram=1", check.detail)

    def test_doctor_reports_canonical_builder_source_drift(self) -> None:
        modules = self._use_modules_root()
        self._write_builder_install(modules / "spark-intelligence-builder", commit="a" * 40)
        self._write_builder_install(
            modules / "spark-intelligence-builder-alt",
            commit="b" * 40,
            license_name="AGPL-3.0-only",
            needs_harness=False,
            harness_dependency=False,
        )

        check = self._checks()["builder-source-truth"]

        self.assertFalse(check.ok)
        self.assertIn("commit_drift", check.detail)
        self.assertIn("license_mismatch", check.detail)
        self.assertIn("missing_harness_module", check.detail)
        self.assertIn("missing_harness_dependency", check.detail)

    def test_doctor_allows_declared_release_mirror_without_commit_drift(self) -> None:
        modules = self._use_modules_root()
        self._write_builder_install(modules / "spark-intelligence-builder", commit="a" * 40)
        self._write_builder_install(
            modules / "spark-intelligence-builder-release",
            commit="b" * 40,
            canonical=False,
            needs_harness=False,
            harness_dependency=False,
        )

        check = self._checks()["builder-source-truth"]

        self.assertTrue(check.ok, check.detail)
        self.assertIn("mirrors=spark-intelligence-builder-release", check.detail)
        self.assertNotIn("commit_drift", check.detail)

    def test_doctor_flags_stale_editable_but_allows_installed_distribution(self) -> None:
        modules = self._use_modules_root()
        canonical = modules / "spark-intelligence-builder"
        self._write_builder_install(canonical, commit="a" * 40)
        harness = modules / "spark-harness-core" / "source" / "src"
        harness.mkdir(parents=True)
        stale = self.home / "stale-builder" / "src"
        stale.mkdir(parents=True)

        with patch(
            "spark_intelligence.doctor.checks._imported_package_src_roots",
            return_value={"spark_intelligence": stale, "spark_harness_core": harness},
            create=True,
        ):
            stale_check = self._checks()["python-import-source"]
        with patch(
            "spark_intelligence.doctor.checks._imported_package_src_roots",
            return_value={
                "spark_intelligence": Path("/tmp/venv/lib/python3.14/site-packages"),
                "spark_harness_core": harness,
            },
            create=True,
        ):
            installed_check = self._checks()["python-import-source"]

        self.assertFalse(stale_check.ok)
        self.assertIn("stale_editable", stale_check.detail)
        self.assertTrue(installed_check.ok, installed_check.detail)
        self.assertIn("installed_distribution", installed_check.detail)

        sibling_harness = Path(__file__).resolve().parents[2] / "spark-harness-core" / "src"
        if sibling_harness.is_dir():
            with patch(
                "spark_intelligence.doctor.checks._imported_package_src_roots",
                return_value={
                    "spark_intelligence": Path(__file__).resolve().parents[1] / "src",
                    "spark_harness_core": sibling_harness,
                },
                create=True,
            ):
                sibling_check = self._checks()["python-import-source"]
            self.assertTrue(sibling_check.ok, sibling_check.detail)

    def test_manifests_declare_pinned_harness_core_dependency(self) -> None:
        root = Path(__file__).resolve().parents[1]
        spark_manifest = tomllib.loads((root / "spark.toml").read_text(encoding="utf-8"))
        pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))

        self.assertIn("spark-harness-core", spark_manifest["needs"]["modules"])
        harness_dependencies = [
            item for item in pyproject["project"]["dependencies"] if item.startswith("spark-harness-core @ ")
        ]
        self.assertEqual(len(harness_dependencies), 1)
        self.assertIn("@f3641143f1c3f28f55178faefcea57fd443c98bc", harness_dependencies[0])
