"""Regression test for actionable error in llm_wiki.user_notes._normalize_status.

When a caller passes an unrecognized promotion status into the user-note
promote path, the raised ValueError must:
1. name the rejected token (so the caller can spot a typo in their input),
   and
2. name the canonical accepted set sourced from VALID_USER_NOTE_STATUSES.
"""

from __future__ import annotations

import unittest

from spark_intelligence.llm_wiki.user_notes import (
    VALID_USER_NOTE_STATUSES,
    _normalize_status,
)


class LlmWikiUserNotesStatusMessageTests(unittest.TestCase):
    def test_message_names_rejected_value_and_canonical_set(self) -> None:
        with self.assertRaises(ValueError) as cm:
            _normalize_status("verfied")  # typo

        message = str(cm.exception)
        self.assertIn("'verfied'", message)
        for allowed in VALID_USER_NOTE_STATUSES:
            self.assertIn(allowed, message)

    def test_pure_hit_paths_unchanged(self) -> None:
        for value in VALID_USER_NOTE_STATUSES:
            self.assertEqual(_normalize_status(value), value)

    def test_casefold_and_whitespace_still_normalize(self) -> None:
        self.assertEqual(_normalize_status(" Verified "), "verified")
        self.assertEqual(_normalize_status("CANDIDATE"), "candidate")


if __name__ == "__main__":
    unittest.main()
