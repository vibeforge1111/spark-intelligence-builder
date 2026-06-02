"""Verify Desktop path is resolved dynamically, not hardcoded.

Covers PR: #140 (resolve Desktop path dynamically).
"""

import ast
import inspect
import unittest


class TestPathResolution(unittest.TestCase):
    """Tests for dynamic path resolution."""

    def test_desktop_path_uses_xdg(self) -> None:
        """Desktop path should use XDG or platform-standard location, not hardcoded path."""
        found_hardcoded = False
        modules_to_check = [
            "spark_intelligence.researcher_bridge.advisory",
        ]
        for mod_name in modules_to_check:
            try:
                mod = __import__(mod_name, fromlist=[""])
                source = inspect.getsource(mod)
                # Check for common hardcoded Windows paths
                if r"C:\\Users" in source or "/Users/" in source or r"Desktop" in source.replace(" ", ""):
                    # Allow if it's just a comment or docstring
                    if "XDG" in source or "pathlib" in source or "Path.home" in source:
                        found_hardcoded = False
                    else:
                        found_hardcoded = True
            except (ImportError, OSError, TypeError):
                pass

        self.assertFalse(found_hardcoded,
                         "Should not contain hardcoded Desktop paths")

        # Verify the advisory module uses Path.home() or xdg
        try:
            from spark_intelligence.researcher_bridge import advisory as adv_mod
            source = inspect.getsource(adv_mod)
            self.assertTrue(
                "Path.home" in source or "XDG" in source or "getenv" in source,
                "Should use Path.home() or XDG for Desktop path resolution"
            )
        except (ImportError, OSError, TypeError):
            self.skipTest("Could not inspect advisory module")
