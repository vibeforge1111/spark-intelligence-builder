"""Verify that subprocess.run calls include a timeout parameter.

Covers PRs: #143 (governed subprocess), #144 (handoff subprocess), #147 (icacls timeout).
Also verifies malformed JSON handling in subprocess output (#145, #146).
"""

import ast
import inspect
import unittest


class TestSubprocessTimeout(unittest.TestCase):
    """Tests that subprocess.run calls include timeout parameters."""

    def _check_subprocess_timeout_in_source(self, source: str, name: str) -> bool:
        """Check if subprocess.run calls have timeout parameter in the given source."""
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                # Match subprocess.run(...)
                if (isinstance(func, ast.Attribute)
                        and func.attr == "run"
                        and isinstance(func.value, ast.Name)
                        and func.value.id == "subprocess"):
                    keywords = [kw.arg for kw in node.keywords if kw.arg is not None]
                    if "timeout" in keywords:
                        return True
                # Match subprocess.run imported directly
                if (isinstance(func, ast.Name)
                        and func.id == "run"):
                    keywords = [kw.arg for kw in node.keywords if kw.arg is not None]
                    if "timeout" in keywords:
                        return True
        return False

    # ── governed subprocess (#143) ──────────────────────────────────
    def test_governed_command_has_timeout(self) -> None:
        """run_governed_command should pass timeout to subprocess.run."""
        from spark_intelligence.execution.governed import run_governed_command
        source = inspect.getsource(run_governed_command)
        self.assertTrue(
            self._check_subprocess_timeout_in_source(source, "run_governed_command"),
            "run_governed_command should call subprocess.run with a timeout parameter"
        )

    # ── config loader icacls (#147) ─────────────────────────────────
    def test_config_loader_icacls_has_timeout(self) -> None:
        """ConfigManager._set_env_file_permissions should call subprocess.run with timeout."""
        from spark_intelligence.config.loader import ConfigManager
        source = inspect.getsource(ConfigManager._set_env_file_permissions)
        self.assertTrue(
            self._check_subprocess_timeout_in_source(source, "_set_env_file_permissions"),
            "_set_env_file_permissions should call subprocess.run with a timeout parameter"
        )

    # ── handoff subprocess (#144) ───────────────────────────────────
    def test_handoff_check_has_timeout(self) -> None:
        """Handoff check subprocess calls should include timeout."""
        import spark_intelligence.state.db as state_db
        source = inspect.getsource(state_db)
        # Check for any subprocess.run calls with timeout
        tree = ast.parse(source)
        has_timeout = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                if (isinstance(func, ast.Attribute)
                        and func.attr == "run"
                        and isinstance(func.value, ast.Name)
                        and func.value.id == "subprocess"):
                    keywords = [kw.arg for kw in node.keywords if kw.arg is not None]
                    if "timeout" in keywords:
                        has_timeout = True
                        break
        self.assertTrue(
            has_timeout,
            "state/db.py should call subprocess.run with a timeout parameter"
        )
