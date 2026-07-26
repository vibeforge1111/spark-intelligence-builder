from __future__ import annotations

import unittest

from spark_intelligence.harness_runtime.service import _extract_first_url


class HarnessRuntimeUrlExtractorTests(unittest.TestCase):
    def test_returns_none_when_no_url_present(self) -> None:
        self.assertIsNone(_extract_first_url("Just plain text, no url here."))

    def test_returns_none_for_empty_string(self) -> None:
        self.assertIsNone(_extract_first_url(""))

    def test_returns_none_for_none_input(self) -> None:
        self.assertIsNone(_extract_first_url(None))  # type: ignore[arg-type]

    def test_extracts_http_url(self) -> None:
        self.assertEqual(
            _extract_first_url("Please open http://example.com to check"),
            "http://example.com",
        )

    def test_extracts_https_url(self) -> None:
        self.assertEqual(
            _extract_first_url("Please open https://example.com today"),
            "https://example.com",
        )

    def test_extracts_first_of_multiple_urls(self) -> None:
        self.assertEqual(
            _extract_first_url("Compare https://example.com and https://other.example"),
            "https://example.com",
        )

    def test_url_with_path_and_query_is_captured(self) -> None:
        self.assertEqual(
            _extract_first_url("Go to https://example.com/path?q=1&b=2 for details"),
            "https://example.com/path?q=1&b=2",
        )

    def test_match_is_case_insensitive_on_scheme(self) -> None:
        self.assertEqual(
            _extract_first_url("Visit HTTPS://Example.COM/Home today"),
            "HTTPS://Example.COM/Home",
        )

    def test_url_terminator_stops_at_closing_paren(self) -> None:
        # Parenthetical citations should not break the URL.
        self.assertEqual(
            _extract_first_url("See the docs (https://example.com)"),
            "https://example.com",
        )

    def test_trailing_whitespace_is_not_part_of_url(self) -> None:
        self.assertEqual(
            _extract_first_url("Open https://example.com next"),
            "https://example.com",
        )


if __name__ == "__main__":
    unittest.main()
