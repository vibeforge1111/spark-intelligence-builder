from spark_intelligence.disambiguation_bridge.service import detect_ambiguous_intent


def test_mission_disambiguation_does_not_hijack_current_plan_run_word() -> None:
    intent = detect_ambiguous_intent(
        "Our current plan is to run the Neon Harbor memory test through Telegram first."
    )

    assert intent is None


def test_mission_disambiguation_still_catches_mission_surface_terms() -> None:
    intent = detect_ambiguous_intent("Show me the mission board status")

    assert intent is not None
    assert intent["primary"] == "mission"


def test_signal_words_as_words_do_not_trigger_clarifier() -> None:
    intent = detect_ambiguous_intent(
        "mission and chip are words here; do not ask a routing question"
    )

    assert intent is None
