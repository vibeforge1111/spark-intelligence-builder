"""Verify Desktop path is resolved dynamically."""
import inspect
import unittest


class TestPathResolution(unittest.TestCase):
    def test_desktop_path_uses_xdg(self) -> None:
        try:
            from spark_intelligence.researcher_bridge import advisory as m
            source = inspect.getsource(m)
            has_dynamic = "Path.home" in source or "XDG" in source or "getenv" in source
            has_hardcoded = "C:\\\\Users" in source or "Desktop" in source.replace(" ", "")
            self.assertTrue(has_dynamic,
                            "Should use Path.home() or XDG for Desktop path")
            if has_hardcoded:
                self.assertIn("XDG", source,
                              "If Desktop is mentioned, XDG should also be referenced")
        except ImportError:
            self.skipTest("advisory module not available")
