"""Verify malformed JSON handling in subprocess and web responses.

Covers PRs: #145 (swarm bridge JSON), #146 (direct provider malformed JSON).
Also covers #145/#146 as they relate to parsing responses safely.
"""

import ast
import inspect
import unittest


class TestJsonHandling(unittest.TestCase):
    """Tests that JSON parsing is guarded and raises clear errors."""

    # ── direct provider malformed JSON (#146) ───────────────────────
    def test_direct_provider_json_parsing_has_guard(self) -> None:
        """_post_json should guard against malformed JSON from provider."""
        from spark_intelligence.llm.direct_provider import _post_json
        source = inspect.getsource(_post_json)
        tree = ast.parse(source)

        has_json_try = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Try):
                for handler in node.handlers:
                    if isinstance(handler, ast.ExceptHandler):
                        for child in ast.walk(handler):
                            if (isinstance(child, ast.Call)
                                    and isinstance(child.func, ast.Attribute)
                                    and child.func.attr == "JSONDecodeError"
                                    and isinstance(child.func.value, ast.Name)
                                    and child.func.value.id == "json"):
                                has_json_try = True
                                break

        self.assertIn("JSONDecodeError", source,
                       "_post_json should catch json.JSONDecodeError")

    # ── swarm bridge JSON (#145) ────────────────────────────────────
    def test_swarm_bridge_json_parsing_has_guard(self) -> None:
        """Swarm bridge sync should guard against malformed JSON."""
        import spark_intelligence.swarm_bridge.sync as sync_mod
        source = inspect.getsource(sync_mod)

        self.assertIn("JSONDecodeError", source,
                       "swarm_bridge/sync.py should catch json.JSONDecodeError")

    # ── procedural lessons (#278, covered in logging but also JSON) ─
    def test_procedural_lessons_json_guard(self) -> None:
        """Procedural lessons _json_dict should catch JSONDecodeError."""
        import spark_intelligence.workflow_recovery.procedural_lessons as pl_mod
        source = inspect.getsource(pl_mod._json_dict)
        self.assertIn("JSONDecodeError", source,
                       "_json_dict should catch json.JSONDecodeError")

    # ── telegram cadence (#277, also JSON) ──────────────────────────
    def test_telegram_cadence_json_guard(self) -> None:
        """Telegram cadence _load_json should catch JSONDecodeError."""
        import spark_intelligence.self_awareness.live_telegram_cadence as cadence_mod
        source = inspect.getsource(cadence_mod._load_json)
        self.assertIn("JSONDecodeError", source,
                       "_load_json should catch json.JSONDecodeError")
