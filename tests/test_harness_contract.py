import pytest

from spark_intelligence.harness_contract import (
    HARNESS_CORE_AVAILABLE,
    authorize_legacy_tool_call,
    authorize_tool_call,
    parse_turn_intent_envelope,
)


def _base_envelope() -> dict:
    return {
        "schema": "spark.turn_intent.v1",
        "turnId": "turn:test",
        "traceId": "trace:test",
        "surface": "telegram",
        "directive": {
            "mode": "answer",
            "noExecution": True,
            "noPublish": False,
            "localOnly": False,
            "explanationOnly": True,
            "quotedOrMetaLanguage": True,
        },
        "selectedIntent": {
            "kind": "plain_conversation",
            "ownerSystem": "spark-telegram-bot",
            "action": "answer",
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
            "allowedTools": ["answer.compose"],
            "deniedTools": ["spawner.run", "publish.run", "external.fetch"],
            "enabledToolsets": ["telegram.reply", "spark-telegram-bot"],
            "mutationClassesAllowed": ["none", "read_only"],
            "requiresApprovalFor": [],
            "networkPolicy": "none",
            "elevatedAllowed": False,
        },
        "executionPolicy": {
            "canMutateFiles": False,
            "canLaunchMission": False,
            "canWriteMemory": False,
            "canDeleteSchedule": False,
            "canCreateChip": False,
            "canPublish": False,
            "canUseExternalNetwork": False,
        },
        "threatDefense": {
            "reasonCodes": ["fresh_user_turn_is_authority", "no_execution_boundary"],
        },
    }


def test_parses_answer_only_envelope() -> None:
    assert HARNESS_CORE_AVAILABLE is True
    envelope = parse_turn_intent_envelope(_base_envelope())

    assert envelope.schema == "spark.turn_intent.v1"
    assert envelope.directive.no_execution is True
    assert envelope.directive.quoted_or_meta_language is True
    assert envelope.session_scope.memory_load_policy == "evidence_only"
    assert envelope.threat_reason_codes == ("fresh_user_turn_is_authority", "no_execution_boundary")


def test_blocks_mutation_without_matching_verdict() -> None:
    envelope = parse_turn_intent_envelope(_base_envelope())

    verdict, reasons = authorize_tool_call(
        envelope,
        tool_name="spawner.run",
        owner_system="spawner-ui",
        mutation_class="launches_mission",
    )

    assert verdict == "blocked"
    assert "no_execution_boundary" in reasons
    assert "tool_denied_by_policy" in reasons
    assert "mutation_class_not_authorized" in reasons
    assert "owner_mismatch" in reasons


def test_allows_benchmark_led_startup_execution() -> None:
    payload = _base_envelope()
    payload["directive"] = {
        "mode": "execute",
        "noExecution": False,
        "noPublish": True,
        "localOnly": True,
        "explanationOnly": False,
        "quotedOrMetaLanguage": False,
    }
    payload["selectedIntent"] = {
        "kind": "answer_improvement_canary",
        "ownerSystem": "spark-intelligence-builder",
        "action": "startup.answer_canary",
        "confidence": "explicit",
        "requiresConfirmation": False,
        "source": "explicit",
    }
    payload["toolPolicy"]["allowedTools"] = ["answer.compose", "spawner.run"]
    payload["toolPolicy"]["deniedTools"] = ["publish.run", "external.fetch"]
    payload["toolPolicy"]["mutationClassesAllowed"] = ["none", "read_only", "launches_mission"]
    payload["toolPolicy"]["requiresApprovalFor"] = ["launches_mission"]
    payload["toolPolicy"]["enabledToolsets"] = ["telegram.reply", "spark-intelligence-builder"]
    payload["toolPolicy"]["networkPolicy"] = "local_only"
    payload["executionPolicy"]["canLaunchMission"] = True
    payload["threatDefense"]["reasonCodes"] = ["fresh_user_turn_is_authority", "local_only_boundary"]

    envelope = parse_turn_intent_envelope(payload)
    verdict, reasons = authorize_tool_call(
        envelope,
        tool_name="spawner.run",
        owner_system="spark-intelligence-builder",
        mutation_class="launches_mission",
    )

    assert verdict == "allowed"
    assert reasons == ()
    assert envelope.directive.local_only is True


def test_shared_harness_core_returns_vnext_authorization_decision() -> None:
    payload = _base_envelope()
    payload["directive"]["mode"] = "execute"
    payload["directive"]["noExecution"] = False
    payload["directive"]["explanationOnly"] = False
    payload["directive"]["quotedOrMetaLanguage"] = False
    payload["selectedIntent"]["ownerSystem"] = "domain-chip-memory"
    payload["selectedIntent"]["kind"] = "memory_action"
    payload["selectedIntent"]["action"] = "memory.write"
    payload["toolPolicy"]["allowedTools"] = ["answer.compose", "memory.write"]
    payload["toolPolicy"]["deniedTools"] = []
    payload["toolPolicy"]["enabledToolsets"] = ["spark-harness-core", "domain-chip-memory"]
    payload["toolPolicy"]["mutationClassesAllowed"] = ["none", "read_only", "writes_memory"]
    payload["executionPolicy"]["canWriteMemory"] = True

    envelope = parse_turn_intent_envelope(payload)
    authorization = authorize_legacy_tool_call(
        envelope,
        tool_name="memory.write",
        owner_system="domain-chip-memory",
        mutation_class="writes_memory",
    )

    assert authorization.verdict == "allowed"
    assert authorization.turn_intent_envelope_vnext is not None
    assert authorization.authorization_decision is not None
    assert authorization.turn_intent_envelope_vnext["schema_version"] == "turn-intent-envelope-vnext"
    assert authorization.authorization_decision["schema_version"] == "authorization-decision-v1"
    assert authorization.authorization_decision["verdict"] == "allow"


def test_rejects_invalid_schema() -> None:
    payload = _base_envelope()
    payload["schema"] = "spark.turn_intent.v0"

    with pytest.raises(ValueError, match="Unsupported"):
        parse_turn_intent_envelope(payload)
