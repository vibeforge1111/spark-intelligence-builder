from __future__ import annotations

import importlib
import sys
import tempfile
import unittest
from pathlib import Path

from spark_intelligence.researcher_bridge.advisory import _import_researcher_module


class ResearcherRuntimeSwitchingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tempdir.name)
        self._sys_path = list(sys.path)
        self._module_snapshot = {
            name: module
            for name, module in sys.modules.items()
            if name == "spark_researcher" or name.startswith("spark_researcher.")
        }
        self._clear_researcher_modules()

    def tearDown(self) -> None:
        self._clear_researcher_modules()
        sys.modules.update(self._module_snapshot)
        sys.path[:] = self._sys_path
        self._tempdir.cleanup()

    def _runtime(self, label: str) -> Path:
        runtime = self.root / label
        package = runtime / "src" / "spark_researcher"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text(f"ROOT = {label!r}\n", encoding="utf-8")
        (package / "chips.py").write_text(f"ROOT = {label!r}\n", encoding="utf-8")
        (package / "runner.py").write_text(
            f"from . import chips\nROOT = {label!r}\nCHIP_ROOT = chips.ROOT\n",
            encoding="utf-8",
        )
        return runtime

    def test_runtime_switch_round_trip_reloads_every_submodule_from_selected_root(self) -> None:
        runtime_a = self._runtime("A")
        runtime_b = self._runtime("B")

        first_a = _import_researcher_module(runtime_a, "spark_researcher.runner")
        loaded_b = _import_researcher_module(runtime_b, "spark_researcher.runner")
        second_a = _import_researcher_module(runtime_a, "spark_researcher.runner")

        self.assertEqual((first_a.ROOT, first_a.CHIP_ROOT), ("A", "A"))
        self.assertEqual((loaded_b.ROOT, loaded_b.CHIP_ROOT), ("B", "B"))
        self.assertEqual((second_a.ROOT, second_a.CHIP_ROOT), ("A", "A"))
        self.assertIn(str((runtime_a / "src").resolve()), sys.path[:1])

    def test_desired_parent_with_stale_child_reloads_one_coherent_module_graph(self) -> None:
        runtime_a = self._runtime("A")
        runtime_b = self._runtime("B")
        stale_runner = _import_researcher_module(runtime_a, "spark_researcher.runner")
        self.assertEqual(stale_runner.ROOT, "A")

        sys.modules.pop("spark_researcher", None)
        sys.path.insert(0, str((runtime_b / "src").resolve()))
        importlib.invalidate_caches()
        desired_parent = importlib.import_module("spark_researcher")
        self.assertEqual(desired_parent.ROOT, "B")
        self.assertEqual(sys.modules["spark_researcher.runner"].ROOT, "A")

        reloaded = _import_researcher_module(runtime_b, "spark_researcher.runner")

        self.assertEqual((reloaded.ROOT, reloaded.CHIP_ROOT), ("B", "B"))
        self.assertTrue(
            all(
                str(getattr(module, "__file__", "")).startswith(str((runtime_b / "src").resolve()))
                for name, module in sys.modules.items()
                if name == "spark_researcher" or name.startswith("spark_researcher.")
            )
        )

    @staticmethod
    def _clear_researcher_modules() -> None:
        for name in list(sys.modules):
            if name == "spark_researcher" or name.startswith("spark_researcher."):
                sys.modules.pop(name, None)


if __name__ == "__main__":
    unittest.main()
