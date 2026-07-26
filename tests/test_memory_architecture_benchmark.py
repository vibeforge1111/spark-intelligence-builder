import pytest

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


def test_unsupported_baseline_error_names_allowed_product_baselines() -> None:
    with pytest.raises(ValueError) as exc_info:
        resolve_memory_architecture_baselines(["ungrounded-llm"])

    message = str(exc_info.value)
    assert message.startswith("unsupported_baselines:ungrounded-llm;allowed_baselines:")
    assert message.removeprefix(
        "unsupported_baselines:ungrounded-llm;allowed_baselines:"
    ).split(",") == sorted(PRODUCT_MEMORY_BASELINES)


def test_unsupported_baseline_error_respects_caller_allowed_policy() -> None:
    with pytest.raises(ValueError) as exc_info:
        resolve_memory_architecture_baselines(
            ["unknown"],
            allowed_baselines=("beta", "alpha"),
        )

    assert str(exc_info.value) == (
        "unsupported_baselines:unknown;allowed_baselines:alpha,beta"
    )
