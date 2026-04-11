from spark_intelligence.memory.architecture_benchmark import _product_memory_leaders


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
