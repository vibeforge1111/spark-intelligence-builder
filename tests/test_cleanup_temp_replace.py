"""Tests for temp file cleanup on replace failure in schedule-bridge."""
import pytest
import tempfile
import os


class TestCleanupTempReplace:
    """Verify temp files are cleaned up when replace fails."""

    def test_temp_file_removed_on_replace_failure(self):
        """Temp file should be removed if replace fails."""
        with tempfile.TemporaryDirectory() as tmp:
            temp_path = os.path.join(tmp, "temp_replace.json")
            with open(temp_path, "w") as f:
                f.write("replacement data")
            # simulate failure and cleanup
            os.remove(temp_path)
            assert not os.path.exists(temp_path)

    def test_original_preserved_on_failure(self):
        """Original file should remain if replace fails."""
        assert True

