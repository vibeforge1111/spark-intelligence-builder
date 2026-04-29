import sys
import types

from spark_intelligence.context import (
    ContextBudgetPolicy,
    ConversationTurn,
    build_conversation_frame,
    build_conversation_frame_with_cold_context,
    estimate_tokens,
    retrieve_domain_chip_cold_context,
)


def test_context_budget_policy_targets_reliable_200k_with_larger_model_window() -> None:
    policy = ContextBudgetPolicy(model_context_window_tokens=400_000, target_effective_context_tokens=200_000)
    payload = policy.to_payload()

    assert payload["safe_input_budget_tokens"] == 200_000
    assert payload["requires_larger_model_for_full_target"] is False
    assert payload["compaction_trigger_tokens"] == 260_000


def test_context_budget_policy_warns_when_200k_model_cannot_reliably_hold_200k_input() -> None:
    policy = ContextBudgetPolicy(model_context_window_tokens=200_000, target_effective_context_tokens=200_000)
    payload = policy.to_payload()

    assert payload["safe_input_budget_tokens"] < 200_000
    assert payload["requires_larger_model_for_full_target"] is True


def test_conversation_frame_resolves_access_followup_from_recent_focus() -> None:
    frame = build_conversation_frame(
        current_message="Change it to 4",
        turns=[
            ConversationTurn(role="user", text="Change my access level to three please", turn_id="u1"),
            ConversationTurn(role="assistant", text="Done - I changed this chat to Level 3 - Research + Build.", turn_id="a1"),
        ],
    )

    assert frame.reference_resolution.resolved is True
    assert frame.reference_resolution.kind == "access_level"
    assert frame.reference_resolution.value == "4"
    assert frame.focus_stack[0].kind == "access_level"


def test_conversation_frame_resolves_numbered_list_reference_against_latest_artifact() -> None:
    frame = build_conversation_frame(
        current_message="I like the second one, can we expand that?",
        turns=[
            ConversationTurn(role="user", text="What could we build?", turn_id="u1"),
            ConversationTurn(
                role="assistant",
                text="\n".join(
                    [
                        "A few directions:",
                        "1. Spark Command Palette",
                        "2. Domain Chip Workbench",
                        "3. Spark Timeline",
                    ]
                ),
                turn_id="a1",
            ),
        ],
    )

    assert frame.reference_resolution.resolved is True
    assert frame.reference_resolution.kind == "list_item"
    assert frame.reference_resolution.value == "Domain Chip Workbench"
    assert frame.reference_resolution.source_artifact_key == "list:a1"


def test_conversation_frame_preserves_hot_turns_and_compacts_older_context() -> None:
    turns = [
        ConversationTurn(role="user", text=f"older planning turn {index}", turn_id=f"u{index}")
        for index in range(20)
    ]

    frame = build_conversation_frame(
        current_message="what were we doing?",
        turns=turns,
        policy=ContextBudgetPolicy(hot_min_turns=6, hot_target_tokens=40),
    )

    assert len(frame.hot_turns) >= 6
    assert "Older user goals" in frame.warm_summary
    assert frame.budget["assembled_estimated_tokens"] >= estimate_tokens(frame.warm_summary)
    assert any(item["source"] == "hot_turns" for item in frame.source_ledger)


def test_cold_context_retrieval_is_optional_when_domain_chip_memory_is_unavailable() -> None:
    assert retrieve_domain_chip_cold_context(sdk=None, subject="human:telegram:1", query="what do you know?") == []


def test_cold_context_retrieval_can_inject_domain_chip_evidence(monkeypatch) -> None:
    module = types.ModuleType("domain_chip_memory.builder_read_adapter")

    class FakeRequest:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    def fake_execute(_sdk, request):
        return {
            "event_type": "memory_read_succeeded",
            "facts": {
                "retrieval_trace": {
                    "items": [
                        {
                            "text": f"{request.method} says the preferred build path is bounded context.",
                            "predicate": "preference",
                            "memory_role": "structured_evidence",
                            "session_id": "s1",
                            "turn_ids": ["t1"],
                        }
                    ]
                }
            },
        }

    module.BuilderMemoryReadRequest = FakeRequest
    module.execute_builder_memory_read = fake_execute
    package = types.ModuleType("domain_chip_memory")
    monkeypatch.setitem(sys.modules, "domain_chip_memory", package)
    monkeypatch.setitem(sys.modules, "domain_chip_memory.builder_read_adapter", module)

    items = retrieve_domain_chip_cold_context(
        sdk=object(),
        subject="human:telegram:1",
        query="what context path should we use?",
        limit=4,
    )
    frame = build_conversation_frame_with_cold_context(
        current_message="what context path should we use?",
        turns=[ConversationTurn(role="user", text="we need better memory")],
        sdk=object(),
        subject="human:telegram:1",
    )

    assert items
    assert items[0].source == "domain_chip_memory"
    assert "bounded context" in frame.warm_summary
    assert any(item["source"] == "retrieved_context" and item["count"] > 0 for item in frame.source_ledger)
