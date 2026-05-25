from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from spark_intelligence.browser.service import collect_browser_use_adapter_status

from tests.test_support import SparkTestCase


class BrowserUseServiceTests(SparkTestCase):
    def test_browser_use_ready_requires_fresh_complete_receipt(self) -> None:
        status_path = self.home / "browser-use-status.json"
        screenshot_path = self.home / "probe-screenshot.png"
        screenshot_path.write_bytes(b"png")
        status_path.write_text(
            json.dumps(
                {
                    "status": "ready",
                    "last_success_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                    "proofs": ["doctor", "public_page_open", "screenshot_capture", "state_read"],
                    "screenshot_path": str(screenshot_path),
                }
            ),
            encoding="utf-8",
        )

        with patch.dict("os.environ", {"SPARK_BROWSER_USE_STATUS_PATH": str(status_path)}), \
             patch("spark_intelligence.browser.service.importlib.util.find_spec", return_value=object()), \
             patch("spark_intelligence.browser.service.shutil.which", return_value="browser-use"):
            payload = collect_browser_use_adapter_status(self.config_manager)

        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["proofs"], ["doctor", "public_page_open", "screenshot_capture", "state_read"])
        self.assertTrue(payload["proof_fresh"])

    def test_browser_use_ready_signal_is_configured_when_receipt_is_incomplete(self) -> None:
        status_path = self.home / "browser-use-status.json"
        status_path.write_text(
            json.dumps(
                {
                    "status": "ready",
                    "last_success_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                    "proofs": ["doctor", "public_page_open"],
                }
            ),
            encoding="utf-8",
        )

        with patch.dict("os.environ", {"SPARK_BROWSER_USE_STATUS_PATH": str(status_path)}), \
             patch("spark_intelligence.browser.service.importlib.util.find_spec", return_value=object()), \
             patch("spark_intelligence.browser.service.shutil.which", return_value="browser-use"):
            payload = collect_browser_use_adapter_status(self.config_manager)

        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual(payload["status"], "configured")
        self.assertIn("proof receipt is incomplete", payload["last_failure_reason"])

    def test_browser_use_ready_signal_is_configured_when_receipt_is_stale(self) -> None:
        status_path = self.home / "browser-use-status.json"
        screenshot_path = self.home / "probe-screenshot.png"
        screenshot_path.write_bytes(b"png")
        status_path.write_text(
            json.dumps(
                {
                    "status": "ready",
                    "last_success_at": (datetime.now(UTC) - timedelta(hours=2)).isoformat().replace("+00:00", "Z"),
                    "proofs": ["doctor", "public_page_open", "screenshot_capture", "state_read"],
                    "screenshot_path": str(screenshot_path),
                }
            ),
            encoding="utf-8",
        )

        with patch.dict("os.environ", {"SPARK_BROWSER_USE_STATUS_PATH": str(status_path)}), \
             patch("spark_intelligence.browser.service.importlib.util.find_spec", return_value=object()), \
             patch("spark_intelligence.browser.service.shutil.which", return_value="browser-use"):
            payload = collect_browser_use_adapter_status(self.config_manager)

        self.assertIsNotNone(payload)
        assert payload is not None
        self.assertEqual(payload["status"], "configured")
        self.assertIn("stale", payload["last_failure_reason"])
