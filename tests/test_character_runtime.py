from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

from spark_intelligence.character_runtime import ensure_spark_character_path

from tests.test_support import SparkTestCase


class CharacterRuntimeTests(SparkTestCase):
    def test_ensure_spark_character_path_prepends_src_from_env(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            package_dir = root / "src" / "spark_character"
            package_dir.mkdir(parents=True)
            original_path = list(sys.path)

            try:
                with patch.dict(os.environ, {"SPARK_CHARACTER_ROOT": str(root)}):
                    resolved = ensure_spark_character_path()

                self.assertEqual(resolved, root / "src")
                self.assertEqual(sys.path[0], str(root / "src"))
            finally:
                sys.path[:] = original_path

    def test_ensure_spark_character_path_soft_fails_when_env_is_missing(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(ensure_spark_character_path())
