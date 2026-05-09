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
                    "diagnostic_invocation_count": 1,
                    "diagnostic_invocations": [
                        {
                            "request_id": "req-doctor-intake",
                            "request_selector": "previous_gateway_turn",
                            "contextual_trigger_score": 4,
                            "contextual_trigger_threshold": 3,
                            "contextual_trigger_signals": [
                                "close_turn_repeat_frustration",
                                "previous_turn_memory_failure_signal",
                            ],
                        }
                    ],
                },
            },
            movement_trace={
                "stages": [
                    {"stage": "memory_lifecycle_and_policy", "lifecycle_transition_count": 1},
                    {"stage": "memory_reads", "abstained_count": 1},
                    {
                        "stage": "memory_doctor_intake",
                        "status": "checked",
                        "contextual_trigger_count": 1,
                    },
                ]
            },
            dashboard={"abstention_reasons": ["not_found"]},
            root_cause={
                "status": "clear",
                "primary_gap": None,
                "failure_layer": None,
                "chain": [],
                "confidence": "high",
            },
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
            root_cause={
                "status": "identified",
                "primary_gap": "memory_delete_intent_integrity",
                "failure_layer": "delete_write_fanout",
                "chain": ["forget_request", "memory_write_requests", "memory_write_results"],
                "confidence": "high",
                "confidence_reason": "failing finding maps directly to a known failure layer",
                "disconfirming_checks": [
                    "every requested delete target has a matching accepted delete write",
                ],
                "repair_plan": {
                    "next_action": "Rerun the affected forget request, then ask `check memory deletes`.",
                    "audit_focus": ["memory_write_requested", "memory_write_succeeded"],
                },
            },
        )

        cases = {case["category"]: case for case in benchmark["cases"]}
        self.assertLess(benchmark["score"], 50)
        self.assertEqual(cases["close_turn_recall"]["status"], "fail")
        self.assertEqual(cases["identity_correction"]["status"], "fail")
        self.assertEqual(cases["supersession"]["status"], "fail")
        self.assertEqual(cases["forgetting"]["status"], "fail")
        self.assertEqual(cases["abstention"]["status"], "fail")
        self.assertEqual(cases["doctor_intake"]["status"], "fail")
        self.assertEqual(cases["root_cause_classification"]["status"], "pass")
        self.assertEqual(benchmark["weakest_case"]["status"], "fail")

    def test_scores_root_cause_classification_requires_failure_layer(self) -> None:
        benchmark = score_memory_doctor_benchmark(
            scanned_delete_turns=0,
            scanned_multi_delete_turns=0,
            findings=[
                MemoryDoctorFinding(
                    name="context_capsule_gateway_trace_gap",
                    ok=False,
                    severity="high",
                    detail="gateway had prior turns but provider capsule had none",
                )
            ],
            active_profile={"status": "checked", "facts": {"preferred_name": "Cem"}},
            topic_scan={"status": "checked", "topic": "Cedar Compass 509"},
            context_capsule={"status": "checked", "recent_conversation_count": 0, "gateway_trace": {"status": "checked"}},
            movement_trace={"stages": [{"stage": "memory_reads", "abstained_count": 1}], "gaps": []},
            dashboard={"abstention_reasons": ["not_found"]},
            root_cause={},
        )

        cases = {case["category"]: case for case in benchmark["cases"]}
        self.assertEqual(cases["root_cause_classification"]["status"], "fail")
        self.assertIn("no identified root-cause layer", cases["root_cause_classification"]["detail"])

    def test_scores_root_cause_classification_requires_repair_plan(self) -> None:
        benchmark = score_memory_doctor_benchmark(
            scanned_delete_turns=0,
            scanned_multi_delete_turns=0,
            findings=[
                MemoryDoctorFinding(
                    name="context_capsule_gateway_trace_gap",
                    ok=False,
                    severity="high",
                    detail="gateway had prior turns but provider capsule had none",
                )
            ],
            active_profile={"status": "checked", "facts": {"preferred_name": "Cem"}},
            topic_scan={"status": "checked", "topic": "Cedar Compass 509"},
            context_capsule={"status": "checked", "recent_conversation_count": 0, "gateway_trace": {"status": "checked"}},
            movement_trace={
                "stages": [{"stage": "memory_reads", "abstained_count": 1}],
                "gaps": [{"name": "gateway_to_context_capsule_gap"}],
            },
            dashboard={"abstention_reasons": ["not_found"]},
            root_cause={
                "status": "identified",
                "primary_gap": "context_capsule_gateway_trace_gap",
                "movement_gap": "gateway_to_context_capsule_gap",
                "failure_layer": "context_ingress",
                "chain": ["telegram_gateway", "context_capsule", "provider_context"],
                "confidence": "high",
            },
        )

        cases = {case["category"]: case for case in benchmark["cases"]}
        self.assertEqual(cases["root_cause_classification"]["status"], "observable_incomplete")
        self.assertIn("repair plan lacked an action", cases["root_cause_classification"]["detail"])

    def test_scores_root_cause_classification_requires_falsification_hooks(self) -> None:
        benchmark = score_memory_doctor_benchmark(
            scanned_delete_turns=0,
            scanned_multi_delete_turns=0,
            findings=[
                MemoryDoctorFinding(
                    name="context_capsule_gateway_trace_gap",
                    ok=False,
                    severity="high",
                    detail="gateway had prior turns but provider capsule had none",
                )
            ],
            active_profile={"status": "checked", "facts": {"preferred_name": "Cem"}},
            topic_scan={"status": "checked", "topic": "Cedar Compass 509"},
            context_capsule={"status": "checked", "recent_conversation_count": 0, "gateway_trace": {"status": "checked"}},
            movement_trace={
                "stages": [{"stage": "memory_reads", "abstained_count": 1}],
                "gaps": [{"name": "gateway_to_context_capsule_gap"}],
            },
            dashboard={"abstention_reasons": ["not_found"]},
            root_cause={
                "status": "identified",
                "primary_gap": "context_capsule_gateway_trace_gap",
                "movement_gap": "gateway_to_context_capsule_gap",
                "failure_layer": "context_ingress",
                "chain": ["telegram_gateway", "context_capsule", "provider_context"],
                "confidence": "high",
                "repair_plan": {
                    "next_action": "Repair the recent-conversation capsule path.",
                    "audit_focus": ["gateway_trace", "context_capsule_source_ledger"],
                },
            },
        )

        cases = {case["category"]: case for case in benchmark["cases"]}
        self.assertEqual(cases["root_cause_classification"]["status"], "observable_incomplete")
        self.assertIn("lacked confidence reasoning", cases["root_cause_classification"]["detail"])

    def test_scores_doctor_intake_requires_calibrated_trigger_metadata(self) -> None:
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
                    "diagnostic_invocation_count": 1,
                    "diagnostic_invocations": [
                        {
                            "request_id": "req-doctor-intake",
                            "request_selector": "previous_gateway_turn",
                            "contextual_trigger_score": 4,
                        }
                    ],
                },
            },
            movement_trace={
                "stages": [
                    {"stage": "memory_lifecycle_and_policy", "lifecycle_transition_count": 1},
                    {"stage": "memory_reads", "abstained_count": 1},
                    {
                        "stage": "memory_doctor_intake",
                        "status": "checked",
                        "contextual_trigger_count": 1,
                    },
                ]
            },
            dashboard={"abstention_reasons": ["not_found"]},
        )

        cases = {case["category"]: case for case in benchmark["cases"]}
        self.assertEqual(cases["doctor_intake"]["status"], "observable_incomplete")
        self.assertIn("threshold/signals are missing", cases["doctor_intake"]["detail"])

    def test_scores_doctor_intake_requires_movement_trace_stage(self) -> None:
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
                    "diagnostic_invocation_count": 1,
                    "diagnostic_invocations": [
                        {
                            "request_id": "req-doctor-intake",
                            "request_selector": "previous_gateway_turn",
                            "contextual_trigger_score": 4,
                            "contextual_trigger_threshold": 3,
                            "contextual_trigger_signals": ["close_turn_repeat_frustration"],
                        }
                    ],
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

        cases = {case["category"]: case for case in benchmark["cases"]}
        self.assertEqual(cases["doctor_intake"]["status"], "movement_trace_missing")
        self.assertIn("absent from the movement trace", cases["doctor_intake"]["detail"])

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
