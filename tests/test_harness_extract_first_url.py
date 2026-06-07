from __future__ import annotations

import unittest

from spark_intelligence.harness_runtime.service import _extract_first_url


class ExtractFirstUrlTests(unittest.TestCase):
    def test_returns_first_http_url(self) -> None:
        self.assertEqual(
            _extract_first_url("Open http://example.com please."),
            "http://example.com",
        )

    def test_returns_first_https_url(self) -> None:
        self.assertEqual(
            _extract_first_url("Inspect https://example.com/page for the diff."),
            "https://example.com/page",
        )

    def test_returns_first_when_multiple_urls_present(self) -> None:
        self.assertEqual(
            _extract_first_url("Compare https://first.example and https://second.example"),
            "https://first.example",
        )

    def test_match_is_case_insensitive_for_scheme(self) -> None:
        self.assertEqual(
            _extract_first_url("Open HTTPS://Example.Com now."),
            "HTTPS://Example.Com",
        )

    def test_strips_at_closing_parenthesis(self) -> None:
        # The URL regex stops at ')' which is the common markdown link / parenthetical end.
        self.assertEqual(
            _extract_first_url("See (https://example.com/page) for details."),
            "https://example.com/page",
        )

    def test_returns_none_when_no_url_present(self) -> None:
        self.assertIsNone(_extract_first_url("There is no URL in this sentence."))

    def test_returns_none_for_empty_string(self) -> None:
        self.assertIsNone(_extract_first_url(""))

    def test_url_with_query_and_fragment_is_captured(self) -> None:
        self.assertEqual(
            _extract_first_url("Visit https://example.com/path?q=spark#anchor for details."),
            "https://example.com/path?q=spark#anchor",
        )

    def test_ftp_or_other_scheme_is_not_matched(self) -> None:
        # Only http/https are recognized; ftp:// is not a routable browser URL.
        self.assertIsNone(_extract_first_url("Fetch ftp://example.com/file please."))


if __name__ == "__main__":
    unittest.main()
