from __future__ import annotations

import unittest

from spark_intelligence.harness_registry import looks_like_harness_query


class HarnessQueryDetectorEdgeTests(unittest.TestCase):
    def test_empty_string_is_not_a_harness_query(self) -> None:
        self.assertFalse(looks_like_harness_query(""))

    def test_whitespace_only_is_not_a_harness_query(self) -> None:
        self.assertFalse(looks_like_harness_query("   \n\t  "))

    def test_none_input_is_treated_as_empty(self) -> None:
        self.assertFalse(looks_like_harness_query(None))  # type: ignore[arg-type]

    def test_uppercase_query_still_matches(self) -> None:
        self.assertTrue(looks_like_harness_query("WHAT HARNESS WOULD YOU USE?"))

    def test_query_with_leading_trailing_whitespace_matches(self) -> None:
        self.assertTrue(looks_like_harness_query("   how would Spark execute this?   "))

    def test_what_session_signal_is_detected(self) -> None:
        self.assertTrue(looks_like_harness_query("what session would this use?"))

    def test_what_toolset_signal_is_detected(self) -> None:
        self.assertTrue(looks_like_harness_query("what toolset would you use for browsing?"))

    def test_what_execution_contract_signal_is_detected(self) -> None:
        self.assertTrue(looks_like_harness_query("what execution contract applies here?"))

    def test_unrelated_question_is_not_a_harness_query(self) -> None:
        self.assertFalse(looks_like_harness_query("What time is it?"))

    def test_short_filler_phrase_does_not_match(self) -> None:
        self.assertFalse(looks_like_harness_query("ok"))


if __name__ == "__main__":
    unittest.main()
