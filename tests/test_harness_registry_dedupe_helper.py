from __future__ import annotations

import unittest

from spark_intelligence.harness_registry.service import _dedupe_preserve_order


class HarnessRegistryDedupePreserveOrderTests(unittest.TestCase):
    def test_returns_empty_list_for_empty_input(self) -> None:
        self.assertEqual(_dedupe_preserve_order([]), [])

    def test_preserves_first_occurrence_order(self) -> None:
        self.assertEqual(
            _dedupe_preserve_order(["a", "b", "a", "c", "b"]),
            ["a", "b", "c"],
        )

    def test_strips_whitespace_before_dedupe(self) -> None:
        self.assertEqual(
            _dedupe_preserve_order(["  a  ", "a", "  a"]),
            ["a"],
        )

    def test_drops_empty_strings(self) -> None:
        self.assertEqual(_dedupe_preserve_order(["", "a", "", "b"]), ["a", "b"])

    def test_drops_whitespace_only_strings(self) -> None:
        self.assertEqual(_dedupe_preserve_order(["   ", "\t", "\n", "x"]), ["x"])

    def test_treats_none_as_empty(self) -> None:
        self.assertEqual(_dedupe_preserve_order([None, "a", None]), ["a"])  # type: ignore[list-item]

    def test_stringifies_non_string_items(self) -> None:
        # Each item is coerced via str(item or "").strip().
        self.assertEqual(_dedupe_preserve_order([1, 2, 1, 3]), ["1", "2", "3"])  # type: ignore[list-item]

    def test_case_sensitive_dedupe(self) -> None:
        # The function does NOT lowercase before comparing; "A" and "a" are distinct.
        self.assertEqual(_dedupe_preserve_order(["A", "a", "A"]), ["A", "a"])

    def test_returns_new_list_does_not_mutate_input(self) -> None:
        source = ["a", "b", "a"]
        result = _dedupe_preserve_order(source)
        self.assertEqual(source, ["a", "b", "a"])
        self.assertEqual(result, ["a", "b"])
        self.assertIsNot(result, source)


if __name__ == "__main__":
    unittest.main()
