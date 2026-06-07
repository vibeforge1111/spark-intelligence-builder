"""Regression tests for actionable error messages in llm_wiki.promote normalizers.

When a caller passes an unrecognized status value into one of the three
normalizers in spark_intelligence.llm_wiki.promote, the raised ValueError
must:
1. name the rejected token (so the caller can find the typo in their input),
   and
2. name the canonical accepted set (so the caller does not have to leave the
   shell to read the module to recover).

These are the three message sites pinned by this test:
- _normalize_status (promotion status: candidate, verified)
- _normalize_eval_coverage_status (missing, observed, covered)
- _normalize_gate_status (pass, warn, fail)
"""

from __future__ import annotations

import unittest

from spark_intelligence.llm_wiki.promote import (
    VALID_EVAL_COVERAGE_STATUSES,
    VALID_GATE_STATUSES,
    VALID_PROMOTION_STATUSES,
    _normalize_eval_coverage_status,
    _normalize_gate_status,
    _normalize_status,
)


class LlmWikiPromoteStatusMessagesTests(unittest.TestCase):
    def test_promotion_status_message_names_typed_value_and_allowed_set(self) -> None:
        with self.assertRaises(ValueError) as cm:
            _normalize_status("verfied")  # near-miss typo

        message = str(cm.exception)
        self.assertIn("'verfied'", message)
        for allowed in VALID_PROMOTION_STATUSES:
            self.assertIn(allowed, message)

    def test_eval_coverage_status_message_names_typed_value_and_allowed_set(self) -> None:
        with self.assertRaises(ValueError) as cm:
            _normalize_eval_coverage_status("partial")

        message = str(cm.exception)
        self.assertIn("'partial'", message)
        for allowed in VALID_EVAL_COVERAGE_STATUSES:
            self.assertIn(allowed, message)

    def test_gate_status_message_names_typed_value_and_allowed_set(self) -> None:
        with self.assertRaises(ValueError) as cm:
            _normalize_gate_status("blocked")

        message = str(cm.exception)
        self.assertIn("'blocked'", message)
        for allowed in VALID_GATE_STATUSES:
            self.assertIn(allowed, message)

    def test_pure_hit_paths_unchanged(self) -> None:
        # Pure-hit return paths stay byte-identical: the new message text
        # only fires when ValueError was already going to fire.
        for value in VALID_PROMOTION_STATUSES:
            self.assertEqual(_normalize_status(value), value)
        for value in VALID_EVAL_COVERAGE_STATUSES:
            self.assertEqual(_normalize_eval_coverage_status(value), value)
        for value in VALID_GATE_STATUSES:
            self.assertEqual(_normalize_gate_status(value), value)


if __name__ == "__main__":
    unittest.main()
