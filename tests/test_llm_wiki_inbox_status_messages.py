"""Regression test for actionable error in llm_wiki.inbox._normalize_status.

When a caller passes an unrecognized status filter into the candidate-inbox
scanner, the raised ValueError must:
1. name the rejected token (so the caller can spot a typo in their input),
   and
2. name the canonical accepted set sourced from VALID_INBOX_STATUSES (so the
   caller does not have to leave the shell to read the module).
"""

from __future__ import annotations

import unittest

from spark_intelligence.llm_wiki.inbox import (
    VALID_INBOX_STATUSES,
    _normalize_status,
)


class LlmWikiInboxStatusMessageTests(unittest.TestCase):
    def test_message_names_rejected_value_and_canonical_set(self) -> None:
        with self.assertRaises(ValueError) as cm:
            _normalize_status("approved")  # not in the canonical set

        message = str(cm.exception)
        self.assertIn("'approved'", message)
        for allowed in VALID_INBOX_STATUSES:
            self.assertIn(allowed, message)

    def test_pure_hit_paths_unchanged(self) -> None:
        for value in VALID_INBOX_STATUSES:
            self.assertEqual(_normalize_status(value), value)

    def test_casefold_and_whitespace_still_normalize(self) -> None:
        # Pre-existing behavior: input is casefolded and stripped before the
        # set membership check. This regression guard ensures the new error
        # message does not change that surface.
        self.assertEqual(_normalize_status(" Verified "), "verified")
        self.assertEqual(_normalize_status("CANDIDATE"), "candidate")


if __name__ == "__main__":
    unittest.main()
