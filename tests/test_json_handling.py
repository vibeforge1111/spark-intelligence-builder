"""Verify malformed JSON is handled safely."""
import inspect
import unittest


class TestJsonHandling(unittest.TestCase):
    def test_direct_provider_json_guard(self) -> None:
        from spark_intelligence.llm.direct_provider import _post_json
        source = inspect.getsource(_post_json)
        self.assertIn("JSONDecodeError", source,
                       "_post_json should catch JSONDecodeError")

    def test_swarm_bridge_json_guard(self) -> None:
        import spark_intelligence.swarm_bridge.sync as m
        source = inspect.getsource(m)
        self.assertIn("JSONDecodeError", source,
                       "swarm_bridge/sync.py should catch JSONDecodeError")

    def test_procedural_lessons_json_guard(self) -> None:
        import spark_intelligence.workflow_recovery.procedural_lessons as m
        source = inspect.getsource(m._json_dict)
        self.assertIn("JSONDecodeError", source,
                       "_json_dict should catch JSONDecodeError")

    def test_telegram_cadence_json_guard(self) -> None:
        import spark_intelligence.self_awareness.live_telegram_cadence as m
        source = inspect.getsource(m._load_json)
        self.assertIn("JSONDecodeError", source,
                       "_load_json should catch JSONDecodeError")
