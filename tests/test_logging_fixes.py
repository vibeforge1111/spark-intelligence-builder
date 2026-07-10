"""Verify that silent except-pass patterns are replaced with proper logging.

Covers PRs replacing silent except-pass with logger.warning/error calls.
"""

import ast
import inspect
import unittest


def _has_logging_in_source(source: str) -> bool:
    """Check if source has any except handler that calls a logging method."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            for child in ast.walk(node):
                if (isinstance(child, ast.Call)
                        and isinstance(child.func, ast.Attribute)
                        and child.func.attr in ("warning", "error", "exception", "warn")):
                    return True
    return False


class TestSilentCatchLogging(unittest.TestCase):
    """Tests that silent except:pass has been replaced with logging."""

    def test_config_loader_logs(self) -> None:
        from spark_intelligence.config.loader import ConfigManager
        source = inspect.getsource(ConfigManager._detect_windows_domain)
        self.assertTrue(_has_logging_in_source(source),
                        "ConfigManager._detect_windows_domain should log errors")

    def test_telegram_runtime_logs(self) -> None:
        import spark_intelligence.adapters.telegram.runtime as m
        source = inspect.getsource(m)
        self.assertTrue(_has_logging_in_source(source),
                        "telegram/runtime.py should log suppressed errors")

    def test_memory_orchestrator_logs(self) -> None:
        import spark_intelligence.memory.orchestrator as m
        source = inspect.getsource(m)
        self.assertTrue(_has_logging_in_source(source),
                        "memory/orchestrator.py should log errors")

    def test_personality_loader_logs(self) -> None:
        import spark_intelligence.personality.loader as m
        source = inspect.getsource(m)
        self.assertTrue(_has_logging_in_source(source),
                        "personality/loader.py should log errors")

    def test_observability_checks_logs(self) -> None:
        import spark_intelligence.observability.checks as m
        source = inspect.getsource(m)
        self.assertTrue(_has_logging_in_source(source),
                        "observability/checks.py should log errors")

    def test_researcher_bridge_advisory_logs(self) -> None:
        import spark_intelligence.researcher_bridge.advisory as m
        source = inspect.getsource(m)
        self.assertTrue(_has_logging_in_source(source),
                        "researcher_bridge/advisory.py should log errors")

    def test_swarm_sync_logs(self) -> None:
        import spark_intelligence.swarm_bridge.sync as m
        source = inspect.getsource(m)
        self.assertTrue(_has_logging_in_source(source),
                        "swarm_bridge/sync.py should log errors")

    def test_route_probe_logs(self) -> None:
        from spark_intelligence.self_awareness.route_probe import run_route_probe_and_record
        source = inspect.getsource(run_route_probe_and_record)
        self.assertTrue(_has_logging_in_source(source),
                        "route_probe should log execution failures")

    def test_browser_registry_logs(self) -> None:
        from spark_intelligence.system_registry.registry import _collect_browser_registry_payload
        source = inspect.getsource(_collect_browser_registry_payload)
        self.assertTrue(_has_logging_in_source(source),
                        "browser registry should log missing hook errors")

    def test_procedural_lessons_logs(self) -> None:
        import spark_intelligence.workflow_recovery.procedural_lessons as m
        source = inspect.getsource(m._json_dict)
        self.assertTrue(_has_logging_in_source(source),
                        "procedural_lessons _json_dict should log JSON decode errors")

    def test_telegram_cadence_logs(self) -> None:
        import spark_intelligence.self_awareness.live_telegram_cadence as m
        source = inspect.getsource(m._load_json)
        self.assertTrue(_has_logging_in_source(source),
                        "telegram cadence _load_json should log errors")

    def test_scheduling_persistence_logs(self) -> None:
        import spark_intelligence.schedule_bridge.service as m
        source = inspect.getsource(m)
        self.assertTrue(_has_logging_in_source(source),
                        "schedule_bridge/service.py should log persistence errors")

    def test_capsule_save_logs(self) -> None:
        try:
            from spark_intelligence.self_awareness.context_capsule import ContextCapsule
            source = inspect.getsource(ContextCapsule)
            self.assertTrue(_has_logging_in_source(source),
                            "ContextCapsule should log save failures")
        except (ImportError, TypeError):
            self.skipTest("ContextCapsule not available")

    def test_operating_context_logs(self) -> None:
        import spark_intelligence.self_awareness.operating_context as m
        source = inspect.getsource(m)
        self.assertTrue(_has_logging_in_source(source),
                        "operating_context should log retrieval errors")

    def test_system_map_logs(self) -> None:
        try:
            import spark_intelligence.system_map.service as m
            source = inspect.getsource(m)
            self.assertTrue(_has_logging_in_source(source),
                            "system_map/service.py should log parse errors")
        except (ImportError, OSError):
            self.skipTest("system_map/service.py not available")

    def test_payload_drift_logs(self) -> None:
        try:
            import spark_intelligence.spawner.payload_drift as m
            source = inspect.getsource(m)
            self.assertTrue(_has_logging_in_source(source),
                            "payload_drift.py should log errors")
        except (ImportError, OSError):
            self.skipTest("payload_drift not available")
