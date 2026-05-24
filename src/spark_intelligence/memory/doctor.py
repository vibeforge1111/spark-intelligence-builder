from __future__ import annotations

import json
from dataclasses import dataclass

from spark_intelligence.memory.generic_observations import detect_telegram_generic_deletions
from spark_intelligence.observability.store import latest_events_by_type
from spark_intelligence.state.db import StateDB


@dataclass(frozen=True)
class MemoryDoctorFinding:
    name: str
    ok: bool
    severity: str
    detail: str
    request_id: str | None = None
    expected_delete_count: int = 0
    requested_delete_count: int = 0
    accepted_delete_count: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "ok": self.ok,
            "severity": self.severity,
            "detail": self.detail,
            "request_id": self.request_id,
            "expected_delete_count": self.expected_delete_count,
            "requested_delete_count": self.requested_delete_count,
            "accepted_delete_count": self.accepted_delete_count,
        }


@dataclass(frozen=True)
class MemoryDoctorReport:
    findings: list[MemoryDoctorFinding]
    scanned_delete_turns: int
    scanned_multi_delete_turns: int

    @property
    def ok(self) -> bool:
        return all(finding.ok for finding in self.findings)

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "scanned_delete_turns": self.scanned_delete_turns,
            "scanned_multi_delete_turns": self.scanned_multi_delete_turns,
            "findings": [finding.to_dict() for finding in self.findings],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    def to_text(self) -> str:
        status = "ok" if self.ok else "degraded"
        lines = [f"Spark memory doctor: {status}"]
        lines.append(
            f"- scanned delete turns: {self.scanned_delete_turns} "
            f"(multi-delete={self.scanned_multi_delete_turns})"
        )
        for finding in self.findings:
            marker = "ok" if finding.ok else "fail"
            lines.append(f"- [{marker}] {finding.name}: {finding.detail}")
        return "\n".join(lines)

    def to_telegram_text(self) -> str:
        status = "healthy" if self.ok else "needs attention"
        lines = [f"Memory Doctor: {status}."]
        lines.append(
            f"Scanned {self.scanned_delete_turns} delete turn(s), "
            f"{self.scanned_multi_delete_turns} multi-delete."
        )
        failing = [finding for finding in self.findings if not finding.ok]
        if not failing:
            lines.append("Delete integrity looks good: every detected multi-forget turn has matching delete writes.")
            lines.append("Try: `show active memory for my preferred name` or `check memory deletes`.")
            return "\n".join(lines)
        first = failing[0]
        lines.append(
            "Problem: "
            f"{first.detail} "
            f"(request={first.request_id or 'unknown'})."
        )
        lines.append("Next: rerun the affected forget request, then ask `check memory deletes`.")
        return "\n".join(lines)


def run_memory_doctor(state_db: StateDB, *, limit: int = 200) -> MemoryDoctorReport:
    influence_events = latest_events_by_type(
        state_db,
        event_type="plugin_or_chip_influence_recorded",
        limit=limit,
    )
    write_requested_events = latest_events_by_type(
        state_db,
        event_type="memory_write_requested",
        limit=limit * 3,
    )
    write_succeeded_events = latest_events_by_type(
        state_db,
        event_type="memory_write_succeeded",
        limit=limit * 3,
    )
    findings: list[MemoryDoctorFinding] = []
    scanned_delete_turns = 0
    scanned_multi_delete_turns = 0
    seen_delete_messages: set[str] = set()

    for event in influence_events:
        if str(event.get("component") or "") != "researcher_bridge":
            continue
        facts = event.get("facts_json") if isinstance(event.get("facts_json"), dict) else {}
        message_text = _memory_doctor_message_text(facts)
        if not message_text:
            continue
        expected_deletions = detect_telegram_generic_deletions(message_text)
        if not expected_deletions:
            continue
        message_key = _normalized_message_key(message_text)
        if message_key in seen_delete_messages:
            continue
        seen_delete_messages.add(message_key)
        scanned_delete_turns += 1
        if len(expected_deletions) < 2:
            continue
        scanned_multi_delete_turns += 1
        request_id = str(event.get("request_id") or "").strip()
        requested_count = _delete_write_request_count(write_requested_events, request_id=request_id)
        accepted_count = _accepted_delete_write_count(write_succeeded_events, request_id=request_id)
        expected_count = len(expected_deletions)
        ok = requested_count >= expected_count and accepted_count >= expected_count
        if not ok:
            findings.append(
                MemoryDoctorFinding(
                    name="memory_delete_intent_integrity",
                    ok=False,
                    severity="high",
                    detail=(
                        f"multi-delete turn expected {expected_count} delete write(s), "
                        f"requested {requested_count}, accepted {accepted_count}"
                    ),
                    request_id=request_id or None,
                    expected_delete_count=expected_count,
                    requested_delete_count=requested_count,
                    accepted_delete_count=accepted_count,
                )
            )

    if not findings:
        findings.append(
            MemoryDoctorFinding(
                name="memory_delete_intent_integrity",
                ok=True,
                severity="high",
                detail="No partial multi-delete writes detected.",
            )
        )
    return MemoryDoctorReport(
        findings=findings,
        scanned_delete_turns=scanned_delete_turns,
        scanned_multi_delete_turns=scanned_multi_delete_turns,
    )


def _memory_doctor_message_text(facts: dict[str, object]) -> str:
    for key in ("detected_generic_memory_deletion", "detected_generic_memory_observation"):
        payload = facts.get(key)
        if isinstance(payload, dict):
            message_text = str(payload.get("message_text") or "").strip()
            if message_text:
                return message_text
    payloads = facts.get("detected_generic_memory_deletions")
    if isinstance(payloads, list):
        for payload in payloads:
            if not isinstance(payload, dict):
                continue
            message_text = str(payload.get("message_text") or payload.get("evidence_text") or "").strip()
            if message_text:
                return message_text
    return ""


def _normalized_message_key(value: str) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _request_id_matches(event_request_id: object, *, request_id: str) -> bool:
    candidate = str(event_request_id or "").strip()
    if not candidate or not request_id:
        return False
    return candidate == request_id or candidate.startswith(f"{request_id}:delete-")


def _delete_write_request_count(events: list[dict[str, object]], *, request_id: str) -> int:
    count = 0
    for event in events:
        if str(event.get("component") or "") != "memory_orchestrator":
            continue
        if not _request_id_matches(event.get("request_id"), request_id=request_id):
            continue
        facts = event.get("facts_json") if isinstance(event.get("facts_json"), dict) else {}
        if str(facts.get("operation") or "") == "delete":
            count += 1
    return count


def _accepted_delete_write_count(events: list[dict[str, object]], *, request_id: str) -> int:
    count = 0
    for event in events:
        if str(event.get("component") or "") != "memory_orchestrator":
            continue
        if not _request_id_matches(event.get("request_id"), request_id=request_id):
            continue
        facts = event.get("facts_json") if isinstance(event.get("facts_json"), dict) else {}
        if str(facts.get("operation") or "") != "delete":
            continue
        count += int(facts.get("accepted_count") or 0)
    return count
