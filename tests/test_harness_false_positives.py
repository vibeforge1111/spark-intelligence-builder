import unittest

from spark_intelligence.harness_registry.service import (
    looks_like_harness_query,
    _looks_like_advisory_voice_recipe_task,
)


class TestHarnessFalsePositives(unittest.TestCase):
    def test_looks_like_harness_query_false_positive(self):
        # "somewhat harness" must not trigger the "what harness" signal.
        text = "I bought somewhat harness for my dog"
        self.assertFalse(looks_like_harness_query(text))

    def test_looks_like_harness_query_true_positive_still_fires(self):
        # The genuine phrase must still match on a word boundary.
        self.assertTrue(looks_like_harness_query("what harness should I use here"))

    def test_looks_like_advisory_voice_false_positive(self):
        # "somewhat ... say that back" must not trip the advisory-voice detector.
        text = "somewhat I want to say that backwards"
        self.assertFalse(_looks_like_advisory_voice_recipe_task(text))


if __name__ == "__main__":
    unittest.main()
