from spark_intelligence.bridge_authority import (
    authorize_builder_bridge_action,
    authorize_pending_confirmation,
    extract_turn_intent_envelope,
)


def _envelope(*, route: str = "memory.write", no_execution: bool = False) -> dict:
    allowed_tools = ["answer.compose"]
    mutation_classes = ["none", "read_only"]
    if route == "memory.write" and not no_execution:
        allowed_tools.append("memory.write")
        mutation_classes.append("writes_memory")
    if route == "schedule.delete" and not no_execution:
        allowed_tools.append("schedule.delete")
        mutation_classes.append("deletes_schedule")
    return {
        "schema": "spark.turn_intent.v1",
        "turnId": "turn:test",
        "traceId": "trace:test",
        "surface": "telegram",
        "directive": {
            "mode": "execute" if not no_execution else "answer",
            "noExecution": no_execution,
            "noPublish": False,
            "localOnly": False,
            "explanationOnly": no_execution,
            "quotedOrMetaLanguage": no_execution,
        },
        "selectedIntent": {
            "kind": "test",
            "ownerSystem": "spark-intelligence-builder" if route == "schedule.delete" else "domain-chip-memory",
            "action": route,
            "confidence": "explicit",
            "requiresConfirmation": False,
            "source": "explicit",
        },
        "sessionScope": {
            "sessionKey": "telegram:dm:chat:user",
            "surface": "telegram",
            "conversationKind": "dm",
            "userRef": "user:test",
            "chatRef": "chat:test",
            "memoryLoadPolicy": "evidence_only",
            "pendingStateScope": "same_session_only",
        },
        "toolPolicy": {
            "allowedTools": allowed_tools,
            "deniedTools": [],
            "enabledToolsets": ["telegram.reply", "spark-intelligence-builder"],
            "mutationClassesAllowed": mutation_classes,
            "requiresApprovalFor": [],
            "networkPolicy": "none",
            "elevatedAllowed": False,
        },
        "executionPolicy": {
            "canMutateFiles": False,
            "canLaunchMission": False,
            "canWriteMemory": route == "memory.write" and not no_execution,
            "canDeleteSchedule": route == "schedule.delete" and not no_execution,
            "canCreateChip": False,
            "canPublish": False,
            "canUseExternalNetwork": False,
        },
        "threatDefense": {"reasonCodes": ["fresh_user_turn_is_authority"]},
    }


def test_extracts_turn_intent_from_message_payload() -> None:
    update = {"message": {"spark_turn_intent": _envelope()}}

    envelope = extract_turn_intent_envelope(update)

    assert envelope is not None
    assert envelope.schema == "spark.turn_intent.v1"
    assert envelope.selected_intent.action == "memory.write"


def test_authorizes_builder_memory_write_only_with_envelope_policy() -> None:
    update = {"message": {"spark_turn_intent": _envelope(route="memory.write")}}

    verdict = authorize_builder_bridge_action(
        update,
        tool_name="memory.write",
        owner_system="domain-chip-memory",
        mutation_class="writes_memory",
    )

    assert verdict.allowed is True
    assert verdict.reason_codes == ()


def test_blocks_builder_memory_write_without_envelope() -> None:
    verdict = authorize_builder_bridge_action(
        {"message": {"text": "draft a launch note"}},
        tool_name="memory.write",
        owner_system="domain-chip-memory",
        mutation_class="writes_memory",
    )

    assert verdict.allowed is False
    assert "missing_or_invalid_envelope" in verdict.reason_codes


def test_authorizes_schedule_delete_and_pending_confirmation() -> None:
    update = {"spark_turn_intent": _envelope(route="schedule.delete")}

    delete_verdict = authorize_builder_bridge_action(
        update,
        tool_name="schedule.delete",
        owner_system="spark-intelligence-builder",
        mutation_class="deletes_schedule",
    )
    confirmation_verdict = authorize_pending_confirmation(
        update,
        tool_name="schedule.delete",
        owner_system="spark-intelligence-builder",
        mutation_class="deletes_schedule",
    )

    assert delete_verdict.allowed is True
    assert confirmation_verdict.allowed is True


def test_blocks_pending_confirmation_when_turn_is_meta_language() -> None:
    update = {"spark_turn_intent": _envelope(route="schedule.delete", no_execution=True)}

    verdict = authorize_pending_confirmation(
        update,
        tool_name="schedule.delete",
        owner_system="spark-intelligence-builder",
        mutation_class="deletes_schedule",
    )

    assert verdict.allowed is False
    assert "no_execution_boundary" in verdict.reason_codes


def test_blocks_pending_confirmation_for_unrelated_executable_envelope() -> None:
    update = {"spark_turn_intent": _envelope(route="memory.write")}

    verdict = authorize_pending_confirmation(
        update,
        tool_name="schedule.delete",
        owner_system="spark-intelligence-builder",
        mutation_class="deletes_schedule",
    )

    assert verdict.allowed is False
    assert "tool_not_allowed_by_policy" in verdict.reason_codes
    assert "mutation_class_not_authorized" in verdict.reason_codes
    assert "owner_mismatch" in verdict.reason_codes
