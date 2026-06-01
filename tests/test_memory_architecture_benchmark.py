import unittest

from spark_intelligence.memory.architecture_benchmark import (
    PRODUCT_MEMORY_BASELINES,
    _product_memory_leaders,
    resolve_memory_architecture_baselines,
)


def test_product_memory_leaders_treats_accuracy_tie_as_shared_lead() -> None:
    leaders = _product_memory_leaders(
        [
            {
                "baseline_name": "summary_synthesis_memory",
                "overall": {"accuracy": 0.9131},
                "alignment": {"rate": 0.9084},
            },
            {
                "baseline_name": "dual_store_event_calendar_hybrid",
                "overall": {"accuracy": 0.9131},
                "alignment": {"rate": 0.9131},
            },
        ]
    )

    assert [row["baseline_name"] for row in leaders] == [
        "summary_synthesis_memory",
        "dual_store_event_calendar_hybrid",
    ]


class ResolveMemoryArchitectureBaselinesTests(unittest.TestCase):
    def test_unsupported_lists_allowed_names(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            resolve_memory_architecture_baselines(["ungrounded-llm"])
        message = str(ctx.exception)
        self.assertIn("ungrounded-llm", message)
        for baseline in PRODUCT_MEMORY_BASELINES:
            self.assertIn(baseline, message)

    def test_supported_round_trip(self) -> None:
        result = resolve_memory_architecture_baselines(["summary_synthesis_memory"])
        self.assertEqual(result, ("summary_synthesis_memory",))
