from __future__ import annotations

from typing import Any


BENCHMARK_VERSION = "2026-05-09.v5"


def score_memory_doctor_benchmark(
    *,
    scanned_delete_turns: int,
    scanned_multi_delete_turns: int,
    findings: list[Any],
    active_profile: dict[str, object],
    topic_scan: dict[str, object],
    context_capsule: dict[str, object],
    movement_trace: dict[str, object],
    dashboard: dict[str, object],
) -> dict[str, object]:
    cases = [
        _score_close_turn_recall(context_capsule=context_capsule),
        _score_identity_correction(active_profile=active_profile, topic_scan=topic_scan),
        _score_supersession(active_profile=active_profile, topic_scan=topic_scan, movement_trace=movement_trace),
        _score_forgetting(
            scanned_delete_turns=scanned_delete_turns,
            scanned_multi_delete_turns=scanned_multi_delete_turns,
            findings=findings,
        ),
        _score_abstention(movement_trace=movement_trace, dashboard=dashboard),
        _score_doctor_intake(context_capsule=context_capsule, movement_trace=movement_trace),
    ]
    total_weight = sum(int(case["weight"]) for case in cases)
    earned = sum(float(case["score"]) * int(case["weight"]) for case in cases)
    overall = int(round((earned / total_weight) * 100)) if total_weight else 0
    weakest = sorted(cases, key=lambda item: (float(item["score"]), -int(item["weight"]), str(item["case_id"])))[0]
    return {
        "version": BENCHMARK_VERSION,
        "authority": "diagnostic_score_not_memory_truth",
        "score": overall,
        "case_count": len(cases),
        "cases": cases,
        "weakest_case": {
            "case_id": weakest["case_id"],
            "category": weakest["category"],
            "status": weakest["status"],
            "detail": weakest["detail"],
        },
    }


def memory_doctor_benchmark_summary(benchmark: dict[str, object]) -> str:
    score = benchmark.get("score") if isinstance(benchmark, dict) else None
    weakest = benchmark.get("weakest_case") if isinstance(benchmark.get("weakest_case"), dict) else {}
    if score is None:
        return "benchmark unavailable"
    category = str(weakest.get("category") or "unknown")
    status = str(weakest.get("status") or "unknown")
    return f"{score}/100, weakest={category}:{status}"


def _score_close_turn_recall(*, context_capsule: dict[str, object]) -> dict[str, object]:
    gateway_trace = context_capsule.get("gateway_trace") if isinstance(context_capsule.get("gateway_trace"), dict) else {}
    delivery_trace = gateway_trace.get("delivery_trace") if isinstance(gateway_trace.get("delivery_trace"), dict) else {}
    recent_count = int(context_capsule.get("recent_conversation_count") or 0)
    gateway_status = str(gateway_trace.get("status") or "unknown")
    gateway_count = int(gateway_trace.get("recent_gateway_message_count") or 0)
    if context_capsule.get("status") != "checked":
        return _case(
            "close_turn_recall",
            "close_turn_recall",
            25,
            0.0,
            "fail",
            "No provider context capsule was available.",
            "send a Telegram turn that reaches provider context and rerun Memory Doctor",
        )
    if delivery_trace.get("delivery_failed"):
        return _case(
            "close_turn_recall",
            "close_turn_recall",
            25,
            0.0,
            "fail",
            "Generated reply reached Telegram outbound audit, but delivery failed.",
            "inspect Telegram delivery errors and replay the same close-turn probe",
        )
    if delivery_trace.get("delivery_topic_miss"):
        return _case(
            "close_turn_recall",
            "close_turn_recall",
            25,
            0.0,
            "fail",
            "Generated reply contained the expected topic, but delivered text did not.",
            "inspect outbound guardrails/delivery mutation and replay the same close-turn probe",
        )
    if gateway_trace.get("answer_topic_miss"):
        return _case(
            "close_turn_recall",
            "close_turn_recall",
            25,
            0.0,
            "fail",
            "Recent conversation reached the provider capsule, but the visible answer ignored the expected close-turn topic.",
            "fix answer arbitration and replay the same two-turn close-turn probe",
        )
    if gateway_trace.get("route_contamination"):
        return _case(
            "close_turn_recall",
            "close_turn_recall",
            25,
            0.0,
            "fail",
            "Close-turn recall routed through provisional researcher advisory despite recent-conversation context.",
            "route close-turn recall to grounded conversational/provider recall before researcher packets",
        )
    if gateway_trace.get("lineage_gap"):
        return _case(
            "close_turn_recall",
            "close_turn_recall",
            25,
            0.0,
            "fail",
            f"Gateway had {gateway_count} same-session message(s), but recent_conversation was empty.",
            "repair gateway-to-context capsule transcript inclusion and replay the close-turn recall",
        )
    if recent_count > 0:
        return _case(
            "close_turn_recall",
            "close_turn_recall",
            25,
            1.0,
            "pass",
            f"Provider capsule included {recent_count} recent-conversation turn(s).",
            "keep this regression in the Telegram close-turn pack",
        )
    return _case(
        "close_turn_recall",
        "close_turn_recall",
        25,
        0.5,
        "observable_no_sample",
        f"Context capsule was checked, but no close-turn sample was present; gateway status={gateway_status}.",
        "run a two-turn Telegram probe and rerun Memory Doctor",
    )


def _score_identity_correction(
    *,
    active_profile: dict[str, object],
    topic_scan: dict[str, object],
) -> dict[str, object]:
    if active_profile.get("status") != "checked":
        return _case(
            "identity_correction",
            "identity_correction",
            20,
            0.0,
            "fail",
            "Active profile/current-state memory was not checked.",
            "rerun from Telegram or pass a human id so current profile can be inspected",
        )
    facts = active_profile.get("facts") if isinstance(active_profile.get("facts"), dict) else {}
    preferred_name = str(facts.get("preferred_name") or "").strip()
    if preferred_name:
        return _case(
            "identity_correction",
            "identity_correction",
            20,
            1.0,
            "pass",
            f"Active preferred_name is visible as {preferred_name}.",
            "verify correction prompts supersede older aliases before durable promotion",
        )
    if topic_scan.get("topic"):
        return _case(
            "identity_correction",
            "identity_correction",
            20,
            0.5,
            "observable_no_sample",
            "Active profile is visible, but no preferred_name fact was present.",
            "seed a name correction and rerun Memory Doctor for the old wrong name",
        )
    return _case(
        "identity_correction",
        "identity_correction",
        20,
        0.5,
        "observable_no_sample",
        "Active profile is visible, but this run did not include an identity topic.",
        "run Memory Doctor for the stale alias or corrected name",
    )


def _score_supersession(
    *,
    active_profile: dict[str, object],
    topic_scan: dict[str, object],
    movement_trace: dict[str, object],
) -> dict[str, object]:
    active_checked = active_profile.get("status") == "checked"
    topic_checked = topic_scan.get("status") == "checked"
    lifecycle_stage = _stage_by_name(movement_trace, "memory_lifecycle_and_policy")
    lifecycle_count = int(lifecycle_stage.get("lifecycle_transition_count") or 0) if lifecycle_stage else 0
    if active_checked and topic_checked:
        return _case(
            "supersession",
            "supersession",
            20,
            1.0,
            "pass",
            "Doctor can compare active current-state memory against recent topic traces.",
            "add old-vs-new contradiction fixtures for identity and owner corrections",
        )
    if lifecycle_count > 0:
        return _case(
            "supersession",
            "supersession",
            20,
            0.5,
            "observable_no_sample",
            f"Lifecycle transitions are visible ({lifecycle_count}), but active topic comparison is incomplete.",
            "rerun with a topic and human id to verify old memories are superseded",
        )
    return _case(
        "supersession",
        "supersession",
        20,
        0.0,
        "fail",
        "No active topic comparison or lifecycle transition evidence was visible.",
        "record correction lifecycle transitions and expose the active state comparison",
    )


def _score_forgetting(
    *,
    scanned_delete_turns: int,
    scanned_multi_delete_turns: int,
    findings: list[Any],
) -> dict[str, object]:
    failing_delete_findings = [
        finding
        for finding in findings
        if str(getattr(finding, "name", ""))
        in {"memory_delete_intent_integrity", "memory_forget_postcondition_failed"}
        and not bool(getattr(finding, "ok", True))
    ]
    if failing_delete_findings:
        return _case(
            "forgetting",
            "forgetting",
            20,
            0.0,
            "fail",
            str(getattr(failing_delete_findings[0], "detail", "Delete integrity failed.")),
            "fix multi-delete write fanout and replay the same forget request",
        )
    if scanned_delete_turns > 0:
        return _case(
            "forgetting",
            "forgetting",
            20,
            1.0,
            "pass",
            f"Scanned {scanned_delete_turns} delete turn(s), {scanned_multi_delete_turns} multi-delete turn(s), with no delete integrity failure.",
            "keep multi-delete and single-delete forget probes in the regression pack",
        )
    return _case(
        "forgetting",
        "forgetting",
        20,
        0.5,
        "observable_no_sample",
        "No forget/delete turn was present in this doctor window.",
        "run a controlled forget command and rerun Memory Doctor",
    )


def _score_abstention(
    *,
    movement_trace: dict[str, object],
    dashboard: dict[str, object],
) -> dict[str, object]:
    memory_reads = _stage_by_name(movement_trace, "memory_reads")
    abstained_count = int(memory_reads.get("abstained_count") or 0) if memory_reads else 0
    abstention_reasons = dashboard.get("abstention_reasons") if isinstance(dashboard.get("abstention_reasons"), list) else []
    if abstained_count > 0 or abstention_reasons:
        return _case(
            "abstention",
            "abstention",
            15,
            1.0,
            "pass",
            f"Abstention visibility is present: abstained_count={abstained_count}, reasons={len(abstention_reasons)}.",
            "verify abstention replies stay concise and do not fabricate missing memory",
        )
    if memory_reads:
        return _case(
            "abstention",
            "abstention",
            15,
            0.5,
            "observable_no_sample",
            "Memory read stage is visible, but no abstention sample occurred.",
            "run an unknown-fact recall probe and verify abstention is recorded",
        )
    return _case(
        "abstention",
        "abstention",
        15,
        0.0,
        "fail",
        "Memory read/abstention stage was not visible.",
        "record memory_read_abstained events and expose them in Watchtower memory shadow",
    )


def _score_doctor_intake(*, context_capsule: dict[str, object], movement_trace: dict[str, object]) -> dict[str, object]:
    gateway_trace = context_capsule.get("gateway_trace") if isinstance(context_capsule.get("gateway_trace"), dict) else {}
    invocation_count = int(gateway_trace.get("diagnostic_invocation_count") or 0)
    invocations = gateway_trace.get("diagnostic_invocations") if isinstance(gateway_trace.get("diagnostic_invocations"), list) else []
    contextual_invocations = [
        invocation
        for invocation in invocations
        if isinstance(invocation, dict) and invocation.get("contextual_trigger_score") is not None
    ]
    intake_stage = _stage_by_name(movement_trace, "memory_doctor_intake")
    if contextual_invocations:
        calibrated_invocations = [
            invocation
            for invocation in contextual_invocations
            if invocation.get("contextual_trigger_threshold") is not None
            and invocation.get("contextual_trigger_signals")
        ]
        if not calibrated_invocations:
            return _case(
                "doctor_intake",
                "doctor_intake",
                10,
                0.5,
                "observable_incomplete",
                "Telegram contextual trigger lineage is visible, but threshold/signals are missing.",
                "verify runtime_command_metadata records trigger score, threshold, signals, and previous-failure evidence",
            )
        if intake_stage.get("status") != "checked":
            return _case(
                "doctor_intake",
                "doctor_intake",
                10,
                0.75,
                "movement_trace_missing",
                "Telegram contextual trigger metadata is calibrated, but Memory Doctor intake is absent from the movement trace.",
                "rerun Memory Doctor after wiring the memory_doctor_intake movement stage",
            )
        stage_contextual_count = int(intake_stage.get("contextual_trigger_count") or 0)
        if stage_contextual_count < len(contextual_invocations):
            return _case(
                "doctor_intake",
                "doctor_intake",
                10,
                0.75,
                "movement_trace_partial",
                "Memory Doctor intake stage exists, but contextual trigger counts do not match gateway invocation lineage.",
                "make the intake movement stage derive counts from the same diagnostic invocation ledger",
            )
        return _case(
            "doctor_intake",
            "doctor_intake",
            10,
            1.0,
            "pass",
            f"Telegram contextual trigger calibration is visible for {invocation_count} Memory Doctor invocation(s).",
            "keep close-turn frustration phrases in the Telegram Memory Doctor regression pack",
        )
    if invocation_count > 0:
        return _case(
            "doctor_intake",
            "doctor_intake",
            10,
            0.5,
            "observable_no_sample",
            f"Telegram Memory Doctor invocation lineage is visible ({invocation_count}), but no contextual trigger sample was present.",
            "rerun from Telegram with a close-turn blankness/frustration complaint",
        )
    return _case(
        "doctor_intake",
        "doctor_intake",
        10,
        0.0,
        "fail",
        "No Telegram Memory Doctor invocation lineage was visible for the diagnosed request.",
        "run Memory Doctor directly from Telegram against the confusing turn and verify runtime_command_metadata is recorded",
    )


def _stage_by_name(movement_trace: dict[str, object], stage_name: str) -> dict[str, object]:
    stages = movement_trace.get("stages") if isinstance(movement_trace.get("stages"), list) else []
    for stage in stages:
        if isinstance(stage, dict) and stage.get("stage") == stage_name:
            return stage
    return {}


def _case(
    case_id: str,
    category: str,
    weight: int,
    score: float,
    status: str,
    detail: str,
    next_probe: str,
) -> dict[str, object]:
    return {
        "case_id": case_id,
        "category": category,
        "weight": weight,
        "score": score,
        "status": status,
        "detail": detail,
        "next_probe": next_probe,
    }
