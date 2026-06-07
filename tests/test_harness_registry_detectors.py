from __future__ import annotations

import unittest

from spark_intelligence.harness_registry import looks_like_harness_query
from spark_intelligence.harness_registry.service import (
    _looks_like_advisory_voice_recipe_task,
    _looks_like_research_then_swarm_task,
)


class LooksLikeHarnessQueryTests(unittest.TestCase):
    def test_what_harness_query_is_detected(self) -> None:
        self.assertTrue(looks_like_harness_query("What harness would Spark choose here?"))

    def test_which_harness_query_is_detected(self) -> None:
        self.assertTrue(looks_like_harness_query("Which harness handles voice replies?"))

    def test_how_would_you_execute_query_is_detected(self) -> None:
        self.assertTrue(looks_like_harness_query("How would you execute a parallel research job?"))

    def test_how_would_spark_execute_query_is_detected(self) -> None:
        self.assertTrue(looks_like_harness_query("How would Spark execute this market scan?"))

    def test_what_execution_path_query_is_detected(self) -> None:
        self.assertTrue(looks_like_harness_query("What execution path would you take?"))

    def test_what_execution_contract_query_is_detected(self) -> None:
        self.assertTrue(looks_like_harness_query("What execution contract applies here?"))

    def test_how_would_this_actually_get_done_query_is_detected(self) -> None:
        self.assertTrue(looks_like_harness_query("How would this actually get done if I asked you?"))

    def test_what_toolset_query_is_detected(self) -> None:
        self.assertTrue(looks_like_harness_query("What toolset would you use for this?"))

    def test_what_session_query_is_detected(self) -> None:
        self.assertTrue(looks_like_harness_query("What session would this use to run?"))

    def test_empty_message_is_not_a_query(self) -> None:
        self.assertFalse(looks_like_harness_query(""))

    def test_whitespace_only_message_is_not_a_query(self) -> None:
        self.assertFalse(looks_like_harness_query("   "))

    def test_unrelated_message_is_not_a_query(self) -> None:
        self.assertFalse(looks_like_harness_query("Please draft a shorter reply for the operator."))

    def test_query_detection_is_case_insensitive(self) -> None:
        self.assertTrue(looks_like_harness_query("WHAT HARNESS WOULD YOU PICK?"))


class LooksLikeAdvisoryVoiceRecipeTaskTests(unittest.TestCase):
    def test_voice_signal_plus_question_mark_triggers_match(self) -> None:
        self.assertTrue(_looks_like_advisory_voice_recipe_task(
            "what is the difference between researcher and builder? reply in voice."
        ))

    def test_voice_signal_with_explain_advisory_signal_triggers_match(self) -> None:
        self.assertTrue(_looks_like_advisory_voice_recipe_task(
            "explain why we picked this and say it back to me."
        ))

    def test_voice_signal_without_advisory_signal_does_not_match(self) -> None:
        # Has 'voice reply' but no question word / advisory cue
        self.assertFalse(_looks_like_advisory_voice_recipe_task("send a voice reply with the audio file."))

    def test_advisory_signal_without_voice_signal_does_not_match(self) -> None:
        self.assertFalse(_looks_like_advisory_voice_recipe_task("what is the difference between A and B?"))

    def test_speak_the_answer_phrase_matches(self) -> None:
        self.assertTrue(_looks_like_advisory_voice_recipe_task("tell me how this works and speak the answer."))

    def test_answer_aloud_phrase_matches(self) -> None:
        self.assertTrue(_looks_like_advisory_voice_recipe_task("why does this fail? answer aloud please."))


class LooksLikeResearchThenSwarmTaskTests(unittest.TestCase):
    def test_research_plus_swarm_signal_matches(self) -> None:
        self.assertTrue(_looks_like_research_then_swarm_task("research this market shift and escalate it to swarm."))

    def test_investigate_plus_multi_agent_signal_matches(self) -> None:
        self.assertTrue(_looks_like_research_then_swarm_task("investigate this case and delegate to multi-agent work."))

    def test_analyze_plus_parallel_signal_matches(self) -> None:
        self.assertTrue(_looks_like_research_then_swarm_task("analyze the data, then run parallel followups."))

    def test_research_signal_alone_does_not_match(self) -> None:
        self.assertFalse(_looks_like_research_then_swarm_task("research this market shift on your own."))

    def test_swarm_signal_alone_does_not_match(self) -> None:
        self.assertFalse(_looks_like_research_then_swarm_task("escalate this to swarm right away."))

    def test_dig_into_plus_delegate_signal_matches(self) -> None:
        self.assertTrue(_looks_like_research_then_swarm_task("dig into the gateway issue and delegate to swarm."))


if __name__ == "__main__":
    unittest.main()
