from spark_intelligence.bridge_authority import (
    authorize_builder_bridge_action,
    authorize_pending_confirmation,
    build_telegram_memory_read_turn_intent_payload,
    detect_telegram_memory_read_authority_source_kind,
    extract_turn_intent_envelope,
)
from spark_intelligence.observability.store import latest_events_by_type
from spark_intelligence.state.db import StateDB


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
    assert verdict.harness_core_envelope is not None
    assert verdict.authorization_decision is not None
    assert verdict.harness_core_envelope["schema_version"] == "turn-intent-envelope-vnext"
    assert verdict.authorization_decision["schema_version"] == "authorization-decision-v1"
    assert verdict.authorization_decision["verdict"] == "allow"
    assert verdict.tool_call_ledger is not None
    assert verdict.tool_call_ledger["schema_version"] == "tool-call-ledger-v1"
    assert verdict.tool_call_ledger["authorization"]["decision_id"] == verdict.authorization_decision["decision_id"]


def test_builds_memory_read_turn_intent_for_explicit_recall() -> None:
    payload = build_telegram_memory_read_turn_intent_payload(
        request_id="req-read",
        channel_kind="telegram",
        session_id="session-read",
        human_id="human-read",
        user_message="What is my current plan?",
        source_kind=detect_telegram_memory_read_authority_source_kind("What is my current plan?") or "test",
    )

    assert payload is not None
    update = {"spark_turn_intent": payload}
    envelope = extract_turn_intent_envelope(update)

    assert envelope is not None
    assert envelope.selected_intent.action == "memory.read"
    assert envelope.selected_intent.owner_system == "domain-chip-memory"
    assert "memory.read" in envelope.tool_policy.allowed_tools
    assert "read_only" in envelope.tool_policy.mutation_classes_allowed
    assert envelope.execution_policy.can_write_memory is False
    verdict = authorize_builder_bridge_action(
        update,
        tool_name="memory.read",
        owner_system="domain-chip-memory",
        mutation_class="read_only",
    )
    assert verdict.allowed is True
    assert verdict.reason_codes == ()


def test_blocks_memory_read_turn_intent_for_meta_examples() -> None:
    source_kind = detect_telegram_memory_read_authority_source_kind(
        "For example: 'what do you remember about my plan?' is not a request to use memory."
    )

    assert source_kind is None
    assert (
        build_telegram_memory_read_turn_intent_payload(
            request_id="req-meta",
            channel_kind="telegram",
            session_id="session-meta",
            human_id="human-meta",
            user_message="For example: 'what do you remember about my plan?' is not a request to use memory.",
            source_kind="test",
        )
        is None
    )


def test_blocks_builder_memory_write_without_envelope() -> None:
    verdict = authorize_builder_bridge_action(
        {"message": {"text": "draft a launch note"}},
        tool_name="memory.write",
        owner_system="domain-chip-memory",
        mutation_class="writes_memory",
    )

    assert verdict.allowed is False
    assert "missing_or_invalid_envelope" in verdict.reason_codes
    assert verdict.authorization_decision is None


def test_blocks_memory_write_when_execution_policy_denies_it() -> None:
    payload = _envelope(route="memory.write")
    payload["executionPolicy"]["canWriteMemory"] = False
    update = {"message": {"spark_turn_intent": payload}}

    verdict = authorize_builder_bridge_action(
        update,
        tool_name="memory.write",
        owner_system="domain-chip-memory",
        mutation_class="writes_memory",
    )

    assert verdict.allowed is False
    assert "write_memory_not_authorized" in verdict.reason_codes
    assert verdict.authorization_decision is not None
    assert verdict.authorization_decision["verdict"] == "deny"
    assert verdict.tool_call_ledger is not None
    assert verdict.tool_call_ledger["authorization"]["verdict"] == "deny"


def test_records_bridge_tool_call_ledger_to_observability_store(tmp_path) -> None:
    state_db = StateDB(tmp_path / "state.sqlite")
    state_db.initialize()
    update = {"message": {"spark_turn_intent": _envelope(route="memory.write")}}

    verdict = authorize_builder_bridge_action(
        update,
        tool_name="memory.write",
        owner_system="domain-chip-memory",
        mutation_class="writes_memory",
        state_db=state_db,
        request_id="req:test-ledger",
        component="telegram_bridge",
    )

    assert verdict.allowed is True
    assert verdict.ledger_event_id
    events = latest_events_by_type(state_db, event_type="tool_call_ledger_recorded", limit=5)
    assert len(events) == 1
    event = events[0]
    assert event["event_id"] == verdict.ledger_event_id
    assert event["component"] == "telegram_bridge"
    assert event["status"] == "authorized"
    assert event["facts_json"]["ledger_id"] == verdict.tool_call_ledger["ledger_id"]
    assert event["facts_json"]["authorization_verdict"] == "allow"
    assert event["facts_json"]["tool_call_ledger"]["schema_version"] == "tool-call-ledger-v1"


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
    assert verdict.authorization_decision is not None
    assert verdict.authorization_decision["verdict"] == "deny"


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
