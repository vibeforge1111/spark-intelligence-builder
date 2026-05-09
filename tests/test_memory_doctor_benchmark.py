from __future__ import annotations

import unittest

from spark_intelligence.memory.doctor import MemoryDoctorFinding
from spark_intelligence.memory.doctor_benchmark import (
    memory_doctor_benchmark_summary,
    score_memory_doctor_benchmark,
)


class MemoryDoctorBenchmarkTests(unittest.TestCase):
    def test_scores_full_memory_doctor_visibility_pack(self) -> None:
        benchmark = score_memory_doctor_benchmark(
            scanned_delete_turns=1,
            scanned_multi_delete_turns=1,
            findings=[],
            active_profile={"status": "checked", "facts": {"preferred_name": "Cem"}},
            topic_scan={"status": "checked", "topic": "Maya"},
            context_capsule={
                "status": "checked",
                "recent_conversation_count": 2,
                "gateway_trace": {
                    "status": "checked",
                    "recent_gateway_message_count": 1,
                    "lineage_gap": False,
                },
            },
            movement_trace={
                "stages": [
                    {"stage": "memory_lifecycle_and_policy", "lifecycle_transition_count": 1},
                    {"stage": "memory_reads", "abstained_count": 1},
                ]
            },
            dashboard={"abstention_reasons": ["not_found"]},
        )

        self.assertEqual(benchmark["score"], 100)
        self.assertEqual({case["status"] for case in benchmark["cases"]}, {"pass"})
        self.assertEqual(memory_doctor_benchmark_summary(benchmark), "100/100, weakest=close_turn_recall:pass")

    def test_scores_memory_doctor_failure_pack(self) -> None:
        benchmark = score_memory_doctor_benchmark(
            scanned_delete_turns=1,
            scanned_multi_delete_turns=1,
            findings=[
                MemoryDoctorFinding(
                    name="memory_delete_intent_integrity",
                    ok=False,
                    severity="high",
                    detail="expected 3 delete write(s), requested 1, accepted 1",
                )
            ],
            active_profile={"status": "not_requested"},
            topic_scan={"status": "checked", "topic": "Maya"},
            context_capsule={
                "status": "checked",
                "recent_conversation_count": 0,
                "gateway_trace": {
                    "status": "checked",
                    "recent_gateway_message_count": 2,
                    "lineage_gap": True,
                },
            },
            movement_trace={"stages": []},
            dashboard={},
        )

        cases = {case["category"]: case for case in benchmark["cases"]}
        self.assertLess(benchmark["score"], 50)
        self.assertEqual(cases["close_turn_recall"]["status"], "fail")
        self.assertEqual(cases["identity_correction"]["status"], "fail")
        self.assertEqual(cases["supersession"]["status"], "fail")
        self.assertEqual(cases["forgetting"]["status"], "fail")
        self.assertEqual(cases["abstention"]["status"], "fail")
        self.assertEqual(benchmark["weakest_case"]["status"], "fail")

    def test_scores_forget_postcondition_failure(self) -> None:
        benchmark = score_memory_doctor_benchmark(
            scanned_delete_turns=1,
            scanned_multi_delete_turns=0,
            findings=[
                MemoryDoctorFinding(
                    name="memory_forget_postcondition_failed",
                    ok=False,
                    severity="high",
                    detail="forget request completed, but active current-state memory still contains: current owner.",
                )
            ],
            active_profile={"status": "checked", "facts": {"current_owner": "Maya"}},
            topic_scan={"status": "not_requested"},
            context_capsule={"status": "checked", "recent_conversation_count": 1, "gateway_trace": {"status": "checked"}},
            movement_trace={"stages": [{"stage": "memory_reads", "abstained_count": 1}]},
            dashboard={"abstention_reasons": ["not_found"]},
        )

        cases = {case["category"]: case for case in benchmark["cases"]}
        self.assertEqual(cases["forgetting"]["status"], "fail")
        self.assertIn("active current-state memory still contains", cases["forgetting"]["detail"])

    def test_scores_close_turn_answer_grounding_failure(self) -> None:
        benchmark = score_memory_doctor_benchmark(
            scanned_delete_turns=1,
            scanned_multi_delete_turns=0,
            findings=[],
            active_profile={"status": "checked", "facts": {"preferred_name": "Cem"}},
            topic_scan={"status": "checked", "topic": "Cedar Compass 509"},
            context_capsule={
                "status": "checked",
                "recent_conversation_count": 2,
                "gateway_trace": {
                    "status": "checked",
                    "recent_gateway_message_count": 1,
                    "lineage_gap": False,
                    "answer_topic_miss": True,
                    "route_contamination": True,
                },
            },
            movement_trace={"stages": [{"stage": "memory_reads", "abstained_count": 1}]},
            dashboard={"abstention_reasons": ["not_found"]},
        )

        cases = {case["category"]: case for case in benchmark["cases"]}
        self.assertEqual(cases["close_turn_recall"]["status"], "fail")
        self.assertIn("visible answer ignored", cases["close_turn_recall"]["detail"])
        self.assertEqual(benchmark["weakest_case"]["category"], "close_turn_recall")

    def test_scores_close_turn_delivery_topic_miss(self) -> None:
        benchmark = score_memory_doctor_benchmark(
            scanned_delete_turns=1,
            scanned_multi_delete_turns=0,
            findings=[],
            active_profile={"status": "checked", "facts": {"preferred_name": "Cem"}},
            topic_scan={"status": "checked", "topic": "Cedar Compass 509"},
            context_capsule={
                "status": "checked",
                "recent_conversation_count": 2,
                "gateway_trace": {
                    "status": "checked",
                    "recent_gateway_message_count": 1,
                    "lineage_gap": False,
                    "delivery_trace": {
                        "status": "checked",
                        "delivery_ok": True,
                        "delivery_topic_miss": True,
                    },
                },
            },
            movement_trace={"stages": [{"stage": "memory_reads", "abstained_count": 1}]},
            dashboard={"abstention_reasons": ["not_found"]},
        )

        cases = {case["category"]: case for case in benchmark["cases"]}
        self.assertEqual(cases["close_turn_recall"]["status"], "fail")
        self.assertIn("delivered text did not", cases["close_turn_recall"]["detail"])
        self.assertEqual(benchmark["weakest_case"]["category"], "close_turn_recall")


if __name__ == "__main__":
    unittest.main()
