from spark_intelligence.bridge_authority import (
    authorize_builder_bridge_action,
    authorize_pending_confirmation,
    build_telegram_memory_read_turn_intent_payload,
    detect_telegram_memory_read_authority_source_kind,
    extract_turn_intent_envelope,
    extract_turn_intent_envelope_vnext,
    record_scoped_bridge_tool_call_results,
    reset_bridge_authority_ledger_context,
    set_bridge_authority_ledger_context,
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


def test_extracts_vnext_turn_intent_from_message_payload() -> None:
    legacy_verdict = authorize_builder_bridge_action(
        {"spark_turn_intent": _envelope(route="memory.write")},
        tool_name="memory.write",
        owner_system="domain-chip-memory",
        mutation_class="writes_memory",
    )
    assert legacy_verdict.harness_core_envelope is not None
    update = {"message": {"turn_intent_envelope_vnext": legacy_verdict.harness_core_envelope}}

    envelope = extract_turn_intent_envelope_vnext(update)

    assert envelope is not None
    assert envelope["schema_version"] == "turn-intent-envelope-vnext"
    assert envelope["selected_move"] == "execute_action"


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


def test_authorizes_builder_memory_write_with_native_vnext_envelope() -> None:
    legacy_verdict = authorize_builder_bridge_action(
        {"spark_turn_intent": _envelope(route="memory.write")},
        tool_name="memory.write",
        owner_system="domain-chip-memory",
        mutation_class="writes_memory",
    )
    assert legacy_verdict.harness_core_envelope is not None

    verdict = authorize_builder_bridge_action(
        {"message": {"turn_intent_envelope_vnext": legacy_verdict.harness_core_envelope}},
        tool_name="memory.write",
        owner_system="domain-chip-memory",
        mutation_class="writes_memory",
    )

    assert verdict.allowed is True
    assert verdict.envelope is None
    assert verdict.harness_core_envelope is not None
    assert verdict.authorization_decision is not None
    assert verdict.authorization_decision["verdict"] == "allow"
    assert verdict.tool_call_ledger is not None
    assert verdict.tool_call_ledger["result"]["status"] == "not_started"


def test_blocks_native_vnext_when_action_is_not_proposed() -> None:
    legacy_verdict = authorize_builder_bridge_action(
        {"spark_turn_intent": _envelope(route="memory.write")},
        tool_name="memory.write",
        owner_system="domain-chip-memory",
        mutation_class="writes_memory",
    )
    assert legacy_verdict.harness_core_envelope is not None

    verdict = authorize_builder_bridge_action(
        {"turn_intent_envelope_vnext": legacy_verdict.harness_core_envelope},
        tool_name="memory.read",
        owner_system="domain-chip-memory",
        mutation_class="read_only",
    )

    assert verdict.allowed is False
    assert "proposed_action_not_authorized" in verdict.reason_codes
    assert verdict.authorization_decision is not None
    assert verdict.authorization_decision["verdict"] == "deny"


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


def test_records_bridge_tool_call_ledger_from_scoped_context(tmp_path) -> None:
    state_db = StateDB(tmp_path / "state.sqlite")
    state_db.initialize()
    update = {"message": {"spark_turn_intent": _envelope(route="memory.write")}}
    token = set_bridge_authority_ledger_context(
        state_db=state_db,
        component="telegram_runtime",
        request_id="req:context-ledger",
        channel_id="telegram",
        session_id="session:context",
        human_id="human:context",
        agent_id="agent:context",
        actor_id="telegram_runtime",
    )
    try:
        verdict = authorize_builder_bridge_action(
            update,
            tool_name="memory.write",
            owner_system="domain-chip-memory",
            mutation_class="writes_memory",
        )
    finally:
        reset_bridge_authority_ledger_context(token)

    assert verdict.allowed is True
    assert verdict.ledger_event_id
    events = latest_events_by_type(state_db, event_type="tool_call_ledger_recorded", limit=5)
    assert len(events) == 1
    event = events[0]
    assert event["component"] == "telegram_runtime"
    assert event["request_id"] == "req:context-ledger"
    assert event["session_id"] == "session:context"
    assert event["agent_id"] == "agent:context"


def test_records_final_bridge_tool_call_result_from_scoped_context(tmp_path) -> None:
    state_db = StateDB(tmp_path / "state.sqlite")
    state_db.initialize()
    update = {"message": {"spark_turn_intent": _envelope(route="memory.write")}}
    token = set_bridge_authority_ledger_context(
        state_db=state_db,
        component="telegram_runtime",
        request_id="req:final-ledger",
        channel_id="telegram",
        session_id="session:final",
        human_id="human:final",
        agent_id="agent:final",
        actor_id="telegram_runtime",
    )
    try:
        verdict = authorize_builder_bridge_action(
            update,
            tool_name="memory.write",
            owner_system="domain-chip-memory",
            mutation_class="writes_memory",
        )
        result_events = record_scoped_bridge_tool_call_results(
            status="success",
            summary="Memory write completed.",
            output_path="builder://test/final-ledger",
        )
    finally:
        reset_bridge_authority_ledger_context(token)

    assert verdict.allowed is True
    assert len(result_events) == 1
    events = latest_events_by_type(state_db, event_type="tool_call_ledger_result_recorded", limit=5)
    assert len(events) == 1
    event = events[0]
    assert event["event_id"] == result_events[0]
    assert event["component"] == "telegram_runtime"
    assert event["status"] == "success"
    assert event["parent_event_id"] == verdict.ledger_event_id
    assert event["facts_json"]["ledger_id"] == verdict.tool_call_ledger["ledger_id"]
    assert event["facts_json"]["result_status"] == "success"
    assert event["facts_json"]["tool_call_ledger"]["result"]["status"] == "success"


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
