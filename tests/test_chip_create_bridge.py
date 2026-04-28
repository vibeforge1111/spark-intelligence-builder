from spark_intelligence.chip_create_bridge.service import (
    detect_chip_create_intent,
    format_chip_create_suggestion,
)


def test_detects_natural_domain_chip_create_request():
    intent = detect_chip_create_intent(
        "let's build a domain-chip that creates us cool images out of ASCII patterns"
    )

    assert intent == {
        "action": "create",
        "brief": "creates us cool images out of ASCII patterns",
    }


def test_chip_create_suggestion_does_not_expose_slash_command():
    reply = format_chip_create_suggestion("creates us cool images out of ASCII patterns")

    assert "/chip create" not in reply
    assert "slash command" not in reply.lower()
    assert "natural-language chip creation request" in reply
