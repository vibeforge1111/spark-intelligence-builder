"""Verify that subprocess.run calls include timeout parameter."""
import ast
import inspect
import unittest


def _has_subprocess_timeout(source: str) -> bool:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if ((isinstance(func, ast.Attribute) and func.attr == "run")
                    or (isinstance(func, ast.Name) and func.id == "run")):
                keywords = [kw.arg for kw in node.keywords if kw.arg is not None]
                if "timeout" in keywords:
                    return True
    return False


class TestSubprocessTimeout(unittest.TestCase):
    def test_governed_command_has_timeout(self) -> None:
        from spark_intelligence.execution.governed import run_governed_command
        source = inspect.getsource(run_governed_command)
        self.assertTrue(_has_subprocess_timeout(source),
                        "run_governed_command should pass timeout to subprocess.run")

    def test_config_loader_icacls_has_timeout(self) -> None:
        from spark_intelligence.config.loader import ConfigManager
        source = inspect.getsource(ConfigManager._set_env_file_permissions)
        self.assertTrue(_has_subprocess_timeout(source),
                        "_set_env_file_permissions should use timeout")

    def test_handoff_check_has_timeout(self) -> None:
        import spark_intelligence.state.db as m
        source = inspect.getsource(m)
        self.assertTrue(_has_subprocess_timeout(source),
                        "state/db.py should use timeout in subprocess calls")
