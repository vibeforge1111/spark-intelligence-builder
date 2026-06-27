from copy import deepcopy

from spark_intelligence.bridge_authority import (
    BridgeAuthorityVerdict,
    DOMAIN_CHIP_MEMORY_WRITE_TOOL_NAME,
    authorize_builder_bridge_action,
    authorize_pending_confirmation,
    build_governor_decision_from_bridge_authority,
    build_telegram_memory_read_turn_intent_payload,
    build_telegram_memory_read_turn_intent_payload_vnext,
    build_telegram_memory_diagnostic_turn_intent_payload_vnext,
    build_telegram_memory_turn_intent_payload_vnext,
    detect_telegram_memory_read_authority_source_kind,
    extract_turn_intent_envelope,
    extract_turn_intent_envelope_vnext,
    record_bridge_tool_call_result_ledger,
    record_scoped_bridge_tool_call_results,
    reset_bridge_authority_ledger_context,
    set_bridge_authority_ledger_context,
)
from spark_intelligence.harness_contract import build_vnext_tool_intent_envelope
from spark_intelligence.observability.store import latest_events_by_type
from spark_intelligence.researcher_bridge.advisory import (
    _authorize_researcher_memory_read,
    _authorize_researcher_memory_write,
)
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


def _tool_vnext(
    *,
    request_id: str,
    tool_name: str,
    owner_system: str,
    mutation_class: str,
    source_kind: str = "test_native_vnext",
) -> dict:
    payload = build_vnext_tool_intent_envelope(
        surface="telegram",
        actor_id_ref="user:test",
        request_id=request_id,
        source_kind=source_kind,
        tool_name=tool_name,
        owner_system=owner_system,
        mutation_class=mutation_class,  # type: ignore[arg-type]
        intent_summary=f"Test VNext authority for {tool_name}.",
        raw_turn_summary="Raw test turn remains offloaded.",
        confidence=0.95,
    )
    assert payload is not None
    return payload


def test_extracts_turn_intent_from_message_payload() -> None:
    update = {"message": {"spark_turn_intent": _envelope()}}

    envelope = extract_turn_intent_envelope(update)

    assert envelope is not None
    assert envelope.schema == "spark.turn_intent.v1"
    assert envelope.selected_intent.action == "memory.write"


def test_extracts_vnext_turn_intent_from_message_payload() -> None:
    payload = build_telegram_memory_turn_intent_payload_vnext(
        request_id="req-extract-vnext",
        channel_kind="telegram",
        session_id="session-extract-vnext",
        human_id="human-extract-vnext",
        user_message="My favorite color is cobalt blue.",
        source_kind="telegram_runtime_profile_fact_observation",
    )
    assert payload is not None
    update = {"message": {"turn_intent_envelope_vnext": payload}}

    envelope = extract_turn_intent_envelope_vnext(update)

    assert envelope is not None
    assert envelope["schema_version"] == "turn-intent-envelope-vnext"
    assert envelope["selected_move"] == "execute_action"


def test_authorizes_builder_memory_write_through_governed_legacy_adapter() -> None:
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
    assert verdict.tool_call_ledger is not None
    assert verdict.governor_decision is not None
    assert verdict.harness_core_envelope["schema_version"] == "turn-intent-envelope-vnext"
    assert verdict.authorization_decision["schema_version"] == "authorization-decision-v1"
    assert verdict.authorization_decision["verdict"] == "allow"
    assert verdict.tool_call_ledger["schema_version"] == "tool-call-ledger-v1"
    assert verdict.governor_decision["schema_version"] == "governor-decision-v1"
    assert verdict.governor_decision["outcome"] == "execute"
    assert verdict.governor_decision["execution_boundary"]["legacy_authority_demoted"] is True


def test_authorizes_builder_memory_write_with_native_vnext_envelope() -> None:
    payload = build_telegram_memory_turn_intent_payload_vnext(
        request_id="req-write-native-vnext",
        channel_kind="telegram",
        session_id="session-write-native-vnext",
        human_id="human-write-native-vnext",
        user_message="My favorite color is cobalt blue.",
        source_kind="telegram_runtime_profile_fact_observation",
    )
    assert payload is not None

    verdict = authorize_builder_bridge_action(
        {"message": {"turn_intent_envelope_vnext": payload}},
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
    assert verdict.governor_decision is not None
    assert verdict.governor_decision["outcome"] == "execute"
    assert verdict.governor_decision["execution_boundary"]["legacy_authority_demoted"] is True


def test_bridge_governor_degrades_copied_tool_ledger() -> None:
    payload_a = build_telegram_memory_turn_intent_payload_vnext(
        request_id="req-write-ledger-source",
        channel_kind="telegram",
        session_id="session-write-ledger-source",
        human_id="human-write-ledger-source",
        user_message="My favorite color is cobalt blue.",
        source_kind="telegram_runtime_profile_fact_observation",
    )
    payload_b = build_telegram_memory_turn_intent_payload_vnext(
        request_id="req-write-ledger-target",
        channel_kind="telegram",
        session_id="session-write-ledger-target",
        human_id="human-write-ledger-target",
        user_message="My favorite color is green.",
        source_kind="telegram_runtime_profile_fact_observation",
    )
    assert payload_a is not None
    assert payload_b is not None
    source = authorize_builder_bridge_action(
        {"turn_intent_envelope_vnext": payload_a},
        tool_name="memory.write",
        owner_system="domain-chip-memory",
        mutation_class="writes_memory",
    )
    target = authorize_builder_bridge_action(
        {"turn_intent_envelope_vnext": payload_b},
        tool_name="memory.write",
        owner_system="domain-chip-memory",
        mutation_class="writes_memory",
    )
    assert source.tool_call_ledger is not None
    assert target.authorization_decision is not None

    copied = BridgeAuthorityVerdict(
        allowed=True,
        reason_codes=(),
        envelope=target.envelope,
        harness_core_envelope=target.harness_core_envelope,
        proposed_action=target.proposed_action,
        authorization_decision=target.authorization_decision,
        tool_call_ledger=deepcopy(source.tool_call_ledger),
    )

    governor = build_governor_decision_from_bridge_authority(copied)

    assert governor is not None
    assert governor["outcome"] == "degrade"
    assert governor["execution_boundary"]["action_authorized"] is False
    assert "tool_ledger_turn_mismatch" in governor["execution_boundary"]["reasons"]


def test_blocks_native_vnext_when_action_is_not_proposed() -> None:
    payload = build_telegram_memory_turn_intent_payload_vnext(
        request_id="req-block-native-vnext",
        channel_kind="telegram",
        session_id="session-block-native-vnext",
        human_id="human-block-native-vnext",
        user_message="My favorite color is cobalt blue.",
        source_kind="telegram_runtime_profile_fact_observation",
    )
    assert payload is not None

    verdict = authorize_builder_bridge_action(
        {"turn_intent_envelope_vnext": payload},
        tool_name="memory.read",
        owner_system="domain-chip-memory",
        mutation_class="read_only",
    )

    assert verdict.allowed is False
    assert "proposed_action_not_authorized" in verdict.reason_codes
    assert verdict.authorization_decision is not None
    assert verdict.authorization_decision["verdict"] == "deny"
    assert verdict.governor_decision is not None
    assert verdict.governor_decision["outcome"] == "deny"
    assert verdict.governor_decision["execution_boundary"]["action_authorized"] is False


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


def test_builds_memory_write_vnext_turn_intent_for_explicit_observation() -> None:
    payload = build_telegram_memory_turn_intent_payload_vnext(
        request_id="req-write-vnext",
        channel_kind="telegram",
        session_id="session-write-vnext",
        human_id="human-write-vnext",
        user_message="My favorite color is cobalt blue.",
        source_kind="telegram_runtime_profile_fact_observation",
    )

    assert payload is not None
    assert payload["schema_version"] == "turn-intent-envelope-vnext"
    assert payload["selected_move"] == "execute_action"
    assert payload["action_authority"]["state"] == "executable"
    assert payload["proposed_actions"][0]["capability_id"] == "capability:domain-chip-memory:memory.write"
    assert payload["proposed_actions"][0]["action_type"] == "write_memory"

    verdict = authorize_builder_bridge_action(
        {"turn_intent_envelope_vnext": payload},
        tool_name=DOMAIN_CHIP_MEMORY_WRITE_TOOL_NAME,
        owner_system="domain-chip-memory",
        mutation_class="writes_memory",
    )

    assert verdict.allowed is True
    assert verdict.envelope is None
    assert verdict.authorization_decision is not None
    assert verdict.authorization_decision["verdict"] == "allow"
    assert verdict.tool_call_ledger is not None
    assert verdict.tool_call_ledger["tool_name"] == "memory.write"
    assert verdict.governor_decision is not None
    assert verdict.governor_decision["outcome"] == "execute"


def test_builds_memory_read_vnext_turn_intent_for_explicit_recall() -> None:
    payload = build_telegram_memory_read_turn_intent_payload_vnext(
        request_id="req-read-vnext",
        channel_kind="telegram",
        session_id="session-read-vnext",
        human_id="human-read-vnext",
        user_message="What is my current plan?",
        source_kind=detect_telegram_memory_read_authority_source_kind("What is my current plan?") or "test",
    )

    assert payload is not None
    assert payload["schema_version"] == "turn-intent-envelope-vnext"
    assert payload["selected_move"] == "read_current_state"
    assert payload["action_authority"]["state"] == "read_only"
    assert payload["proposed_actions"][0]["action_type"] == "read"

    verdict = authorize_builder_bridge_action(
        {"turn_intent_envelope_vnext": payload},
        tool_name="memory.read",
        owner_system="domain-chip-memory",
        mutation_class="read_only",
    )

    assert verdict.allowed is True
    assert verdict.envelope is None
    assert verdict.authorization_decision is not None
    assert verdict.authorization_decision["verdict"] == "allow"
    assert verdict.governor_decision is not None
    assert verdict.governor_decision["outcome"] == "execute"


def test_builds_memory_diagnostic_vnext_turn_intent() -> None:
    payload = build_telegram_memory_diagnostic_turn_intent_payload_vnext(
        request_id="req-doctor-vnext",
        channel_kind="telegram",
        session_id="session-doctor-vnext",
        human_id="human-doctor-vnext",
        source_kind="telegram_runtime_memory_doctor",
    )

    assert payload is not None
    assert payload["schema_version"] == "turn-intent-envelope-vnext"
    assert payload["selected_move"] == "read_current_state"
    assert payload["action_authority"]["state"] == "read_only"
    assert payload["proposed_actions"][0]["action_type"] == "read"

    verdict = authorize_builder_bridge_action(
        {"turn_intent_envelope_vnext": payload},
        tool_name="memory.diagnose",
        owner_system="spark-intelligence-builder",
        mutation_class="read_only",
    )

    assert verdict.allowed is True
    assert verdict.envelope is None
    assert verdict.authorization_decision is not None
    assert verdict.authorization_decision["verdict"] == "allow"


def test_researcher_memory_write_blocks_bare_vnext_without_governor(tmp_path) -> None:
    state_db = StateDB(tmp_path / "state.sqlite")
    state_db.initialize()
    payload = build_telegram_memory_turn_intent_payload_vnext(
        request_id="req-researcher-write-bare-vnext",
        channel_kind="telegram",
        session_id="session-researcher-write-bare-vnext",
        human_id="human-researcher-write-bare-vnext",
        user_message="My favorite color is cobalt blue.",
        source_kind="telegram_runtime_profile_fact_observation",
    )

    allowed = _authorize_researcher_memory_write(
        state_db=state_db,
        turn_intent_envelope=None,
        turn_intent_envelope_vnext=payload,
        run_id=None,
        request_id="req-researcher-write-bare-vnext",
        channel_kind="telegram",
        session_id="session-researcher-write-bare-vnext",
        human_id="human-researcher-write-bare-vnext",
        agent_id="agent:test",
        user_message="My favorite color is cobalt blue.",
        source_kind="telegram_runtime_profile_fact_observation",
        operation="update",
        allow_adapter_envelope=False,
    )

    assert allowed is False
    blocks = latest_events_by_type(state_db, event_type="policy_gate_blocked", limit=5)
    assert blocks
    assert blocks[0]["facts_json"]["reason_codes"] == ["missing_governor_decision"]


def test_researcher_memory_write_authorizes_with_governor_decision(tmp_path) -> None:
    state_db = StateDB(tmp_path / "state.sqlite")
    state_db.initialize()
    payload = build_telegram_memory_turn_intent_payload_vnext(
        request_id="req-researcher-write-governor",
        channel_kind="telegram",
        session_id="session-researcher-write-governor",
        human_id="human-researcher-write-governor",
        user_message="My favorite color is cobalt blue.",
        source_kind="telegram_runtime_profile_fact_observation",
    )
    bridge_verdict = authorize_builder_bridge_action(
        {"turn_intent_envelope_vnext": payload},
        tool_name="memory.write",
        owner_system="domain-chip-memory",
        mutation_class="writes_memory",
        state_db=state_db,
        request_id="req-researcher-write-governor",
        channel_id="telegram",
        session_id="session-researcher-write-governor",
        human_id="human-researcher-write-governor",
        agent_id="agent:test",
        actor_id="test",
        component="test",
    )
    assert bridge_verdict.allowed is True
    assert bridge_verdict.governor_decision is not None

    allowed = _authorize_researcher_memory_write(
        state_db=state_db,
        governor_decision=bridge_verdict.governor_decision,
        turn_intent_envelope=None,
        turn_intent_envelope_vnext=payload,
        run_id=None,
        request_id="req-researcher-write-governor",
        channel_kind="telegram",
        session_id="session-researcher-write-governor",
        human_id="human-researcher-write-governor",
        agent_id="agent:test",
        user_message="My favorite color is cobalt blue.",
        source_kind="telegram_runtime_profile_fact_observation",
        operation="update",
        allow_adapter_envelope=False,
    )

    assert allowed is True
    blocks = latest_events_by_type(state_db, event_type="policy_gate_blocked", limit=5)
    assert blocks == []


def test_researcher_memory_write_rejects_copied_governor_ledger(tmp_path) -> None:
    state_db = StateDB(tmp_path / "state.sqlite")
    state_db.initialize()
    payload_source = build_telegram_memory_turn_intent_payload_vnext(
        request_id="req-researcher-write-ledger-source",
        channel_kind="telegram",
        session_id="session-researcher-write-ledger-source",
        human_id="human-researcher-write-ledger-source",
        user_message="My favorite color is cobalt blue.",
        source_kind="telegram_runtime_profile_fact_observation",
    )
    payload_target = build_telegram_memory_turn_intent_payload_vnext(
        request_id="req-researcher-write-ledger-target",
        channel_kind="telegram",
        session_id="session-researcher-write-ledger-target",
        human_id="human-researcher-write-ledger-target",
        user_message="My favorite color is green.",
        source_kind="telegram_runtime_profile_fact_observation",
    )
    assert payload_source is not None
    assert payload_target is not None
    source = authorize_builder_bridge_action(
        {"turn_intent_envelope_vnext": payload_source},
        tool_name="memory.write",
        owner_system="domain-chip-memory",
        mutation_class="writes_memory",
    )
    target = authorize_builder_bridge_action(
        {"turn_intent_envelope_vnext": payload_target},
        tool_name="memory.write",
        owner_system="domain-chip-memory",
        mutation_class="writes_memory",
    )
    assert source.tool_call_ledger is not None
    assert target.governor_decision is not None
    copied_governor = deepcopy(target.governor_decision)
    copied_governor["tool_ledgers"] = [deepcopy(source.tool_call_ledger)]

    allowed = _authorize_researcher_memory_write(
        state_db=state_db,
        governor_decision=copied_governor,
        turn_intent_envelope=None,
        turn_intent_envelope_vnext=payload_target,
        run_id=None,
        request_id="req-researcher-write-ledger-target",
        channel_kind="telegram",
        session_id="session-researcher-write-ledger-target",
        human_id="human-researcher-write-ledger-target",
        agent_id="agent:test",
        user_message="My favorite color is green.",
        source_kind="telegram_runtime_profile_fact_observation",
        operation="update",
        allow_adapter_envelope=False,
    )

    assert allowed is False
    blocks = latest_events_by_type(state_db, event_type="policy_gate_blocked", limit=5)
    assert blocks
    assert "governor_missing_matching_tool_ledger" in blocks[0]["facts_json"]["reason_codes"]


def test_researcher_memory_read_authorizes_with_vnext_only(tmp_path) -> None:
    state_db = StateDB(tmp_path / "state.sqlite")
    state_db.initialize()
    payload = build_telegram_memory_read_turn_intent_payload_vnext(
        request_id="req-researcher-read-vnext",
        channel_kind="telegram",
        session_id="session-researcher-read-vnext",
        human_id="human-researcher-read-vnext",
        user_message="What is my current plan?",
        source_kind="telegram_runtime_current_plan_read",
    )

    allowed = _authorize_researcher_memory_read(
        state_db=state_db,
        turn_intent_envelope=None,
        turn_intent_envelope_vnext=payload,
        run_id=None,
        request_id="req-researcher-read-vnext",
        channel_kind="telegram",
        session_id="session-researcher-read-vnext",
        human_id="human-researcher-read-vnext",
        agent_id="agent:test",
        user_message="What is my current plan?",
        source_kind="telegram_runtime_current_plan_read",
        read_kind="memory_current_plan",
        allow_missing_envelope=False,
    )

    assert allowed is True
    assert latest_events_by_type(state_db, event_type="policy_gate_blocked", limit=5) == []


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


def test_blocks_memory_write_when_vnext_action_is_not_proposed() -> None:
    payload = build_telegram_memory_read_turn_intent_payload_vnext(
        request_id="req-write-not-proposed-vnext",
        channel_kind="telegram",
        session_id="session-write-not-proposed-vnext",
        human_id="human-write-not-proposed-vnext",
        user_message="What is my current plan?",
        source_kind="telegram_runtime_current_plan_read",
    )
    assert payload is not None
    update = {"message": {"turn_intent_envelope_vnext": payload}}

    verdict = authorize_builder_bridge_action(
        update,
        tool_name="memory.write",
        owner_system="domain-chip-memory",
        mutation_class="writes_memory",
    )

    assert verdict.allowed is False
    assert "proposed_action_not_authorized" in verdict.reason_codes
    assert verdict.authorization_decision is not None
    assert verdict.authorization_decision["verdict"] == "deny"
    assert verdict.tool_call_ledger is not None
    assert verdict.tool_call_ledger["authorization"]["verdict"] == "deny"
    assert verdict.governor_decision is not None
    assert verdict.governor_decision["outcome"] == "deny"


def test_blocked_bridge_verdict_cannot_record_success_result(tmp_path) -> None:
    state_db = StateDB(tmp_path / "state.sqlite")
    state_db.initialize()
    payload = build_telegram_memory_read_turn_intent_payload_vnext(
        request_id="req-blocked-result",
        channel_kind="telegram",
        session_id="session-blocked-result",
        human_id="human-blocked-result",
        user_message="What is my current plan?",
        source_kind="telegram_runtime_current_plan_read",
    )
    assert payload is not None
    update = {"message": {"turn_intent_envelope_vnext": payload}}

    verdict = authorize_builder_bridge_action(
        update,
        tool_name="memory.write",
        owner_system="domain-chip-memory",
        mutation_class="writes_memory",
        state_db=state_db,
        request_id="req:blocked-result",
        component="telegram_bridge",
    )
    event_id = record_bridge_tool_call_result_ledger(
        state_db,
        verdict,
        status="success",
        summary="This blocked action must not be finalizable as execution.",
        output_path="builder://test/blocked-result",
        component="telegram_bridge",
        request_id="req:blocked-result",
    )

    assert verdict.allowed is False
    assert event_id is None
    assert len(latest_events_by_type(state_db, event_type="tool_call_ledger_recorded", limit=5)) == 1
    assert latest_events_by_type(state_db, event_type="tool_call_ledger_result_recorded", limit=5) == []


def test_expected_guardrail_denial_records_medium_lifecycle_proof(tmp_path) -> None:
    state_db = StateDB(tmp_path / "state.sqlite")
    state_db.initialize()
    payload = build_telegram_memory_read_turn_intent_payload_vnext(
        request_id="req-guardrail-proof",
        channel_kind="telegram",
        session_id="session-guardrail-proof",
        human_id="human-guardrail-proof",
        user_message="What is my current plan?",
        source_kind="telegram_runtime_current_plan_read",
    )
    assert payload is not None
    update = {"message": {"turn_intent_envelope_vnext": payload}}

    verdict = authorize_builder_bridge_action(
        update,
        tool_name="memory.write",
        owner_system="domain-chip-memory",
        mutation_class="writes_memory",
        state_db=state_db,
        request_id="req:guardrail-proof",
        component="telegram_bridge",
    )

    assert verdict.allowed is False
    assert verdict.ledger_event_id
    events = latest_events_by_type(state_db, event_type="tool_call_ledger_recorded", limit=5)
    assert len(events) == 1
    event = events[0]
    assert event["status"] == "recorded"
    assert event["severity"] == "medium"
    assert event["facts_json"]["authorization_verdict"] == "deny"
    assert event["facts_json"]["result_status"] == "not_started"


def test_integrity_denial_remains_high_blocked_lifecycle_proof(tmp_path) -> None:
    state_db = StateDB(tmp_path / "state.sqlite")
    state_db.initialize()
    payload = build_telegram_memory_turn_intent_payload_vnext(
        request_id="req-owner-mismatch-proof",
        channel_kind="telegram",
        session_id="session-owner-mismatch-proof",
        human_id="human-owner-mismatch-proof",
        user_message="My favorite color is cobalt blue.",
        source_kind="telegram_runtime_profile_fact_observation",
    )
    assert payload is not None
    update = {"message": {"turn_intent_envelope_vnext": payload}}

    verdict = authorize_builder_bridge_action(
        update,
        tool_name="memory.write",
        owner_system="spark-intelligence-builder",
        mutation_class="writes_memory",
        state_db=state_db,
        request_id="req:owner-mismatch-proof",
        component="telegram_bridge",
    )

    assert verdict.allowed is False
    assert "owner_mismatch" in verdict.reason_codes
    events = latest_events_by_type(state_db, event_type="tool_call_ledger_recorded", limit=5)
    assert len(events) == 1
    event = events[0]
    assert event["status"] == "blocked"
    assert event["severity"] == "high"


def test_blocked_scoped_bridge_verdict_cannot_record_success_result(tmp_path) -> None:
    state_db = StateDB(tmp_path / "state.sqlite")
    state_db.initialize()
    payload = build_telegram_memory_read_turn_intent_payload_vnext(
        request_id="req-blocked-scoped-result",
        channel_kind="telegram",
        session_id="session-blocked-scoped",
        human_id="human-blocked-scoped",
        user_message="What is my current plan?",
        source_kind="telegram_runtime_current_plan_read",
    )
    assert payload is not None
    update = {"message": {"turn_intent_envelope_vnext": payload}}
    token = set_bridge_authority_ledger_context(
        state_db=state_db,
        component="telegram_runtime",
        request_id="req:blocked-scoped-result",
        channel_id="telegram",
        session_id="session:blocked-scoped",
        human_id="human:blocked-scoped",
        agent_id="agent:blocked-scoped",
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
            summary="This blocked scoped action must not be finalizable as execution.",
            output_path="builder://test/blocked-scoped-result",
        )
    finally:
        reset_bridge_authority_ledger_context(token)

    assert verdict.allowed is False
    assert result_events == ()
    assert len(latest_events_by_type(state_db, event_type="tool_call_ledger_recorded", limit=5)) == 1
    assert latest_events_by_type(state_db, event_type="tool_call_ledger_result_recorded", limit=5) == []


def test_records_bridge_tool_call_ledger_to_observability_store(tmp_path) -> None:
    state_db = StateDB(tmp_path / "state.sqlite")
    state_db.initialize()
    payload = build_telegram_memory_turn_intent_payload_vnext(
        request_id="req-test-ledger",
        channel_kind="telegram",
        session_id="session-test-ledger",
        human_id="human-test-ledger",
        user_message="My favorite color is cobalt blue.",
        source_kind="telegram_runtime_profile_fact_observation",
    )
    assert payload is not None
    update = {"message": {"turn_intent_envelope_vnext": payload}}

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
    payload = build_telegram_memory_turn_intent_payload_vnext(
        request_id="req-context-ledger",
        channel_kind="telegram",
        session_id="session-context-ledger",
        human_id="human-context-ledger",
        user_message="My favorite color is cobalt blue.",
        source_kind="telegram_runtime_profile_fact_observation",
    )
    assert payload is not None
    update = {"message": {"turn_intent_envelope_vnext": payload}}
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
    payload = build_telegram_memory_turn_intent_payload_vnext(
        request_id="req-final-ledger",
        channel_kind="telegram",
        session_id="session-final-ledger",
        human_id="human-final-ledger",
        user_message="My favorite color is cobalt blue.",
        source_kind="telegram_runtime_profile_fact_observation",
    )
    assert payload is not None
    update = {"message": {"turn_intent_envelope_vnext": payload}}
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
    update = {
        "turn_intent_envelope_vnext": _tool_vnext(
            request_id="req-schedule-delete",
            tool_name="schedule.delete",
            owner_system="spark-intelligence-builder",
            mutation_class="deletes_schedule",
        )
    }

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
    assert "quoted_or_meta_language" in verdict.reason_codes
    assert verdict.authorization_decision is not None
    assert verdict.authorization_decision["verdict"] == "deny"


def test_blocks_pending_confirmation_for_unrelated_executable_envelope() -> None:
    payload = build_telegram_memory_turn_intent_payload_vnext(
        request_id="req-unrelated-vnext",
        channel_kind="telegram",
        session_id="session-unrelated-vnext",
        human_id="human-unrelated-vnext",
        user_message="My favorite color is cobalt blue.",
        source_kind="telegram_runtime_profile_fact_observation",
    )
    assert payload is not None
    update = {"turn_intent_envelope_vnext": payload}

    verdict = authorize_pending_confirmation(
        update,
        tool_name="schedule.delete",
        owner_system="spark-intelligence-builder",
        mutation_class="deletes_schedule",
    )

    assert verdict.allowed is False
    assert "proposed_action_not_authorized" in verdict.reason_codes
