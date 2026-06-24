from __future__ import annotations

import json
import re
from hashlib import sha256
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from spark_intelligence.config.loader import ConfigManager

SENSITIVE_TEXT_PATTERNS = [
    re.compile(r"\b(?:bot)?\d{7,12}:[A-Za-z0-9_-]{30,}\b"),
    re.compile(r"\b(?:sk|sk-proj|sk-ant|gho|ghp|glpat|xoxb|xoxp|AIza)[A-Za-z0-9_\-]{16,}\b"),
    re.compile(r"(?i)(api[_-]?key|bot[_-]?token|token|secret|password|authorization)(\s*[:=]\s*)([^\s,;\"']+)"),
    re.compile(r"(?i)(bearer\s+)([A-Za-z0-9._\-]{16,})"),
]
SENSITIVE_KEY_PATTERN = re.compile(r"(?i)(api[_-]?key|bot[_-]?token|token|secret|password|authorization)")
RAW_ID_KEY_REPLACEMENTS = {
    "chat_id": "chat_ref",
    "chatId": "chat_ref",
    "external_user_id": "external_user_ref",
    "externalUserId": "external_user_ref",
    "user_id": "user_ref",
    "userId": "user_ref",
    "from_id": "from_ref",
    "fromId": "from_ref",
    "telegram_user_id": "telegram_user_ref",
    "telegramUserId": "telegram_user_ref",
}
PATH_LIKE_PATTERNS = [
    re.compile(r"/Users/\S+"),
    re.compile(r"/var/folders/\S+"),
    re.compile(r"file://\S+"),
    re.compile(r"[A-Za-z]:\\\S+"),
]
POLICY_REASON_PATTERN = re.compile(
    r"\b(?:tool_not_allowed_by_policy|owner_mismatch|route_not_selected_by_turn_envelope|"
    r"governor_outcome_deny|harness_core:[A-Za-z0-9_-]+)\b",
    re.IGNORECASE,
)
TELEGRAM_SESSION_ID_PATTERN = re.compile(r"^(session:telegram:[^:]+:)(.+)$")
HARNESS_PROOF_CAPSULE_SCHEMA = "spark.harness_proof.v1"
HARNESS_PROOF_REF_PATTERN = re.compile(r"^turn:sha256:[a-f0-9]{16}$")


def trace_log_path(config_manager: ConfigManager) -> Path:
    return config_manager.paths.logs_dir / "gateway-trace.jsonl"


def outbound_log_path(config_manager: ConfigManager) -> Path:
    return config_manager.paths.logs_dir / "gateway-outbound.jsonl"


def append_gateway_trace(config_manager: ConfigManager, record: dict[str, Any]) -> None:
    path = trace_log_path(config_manager)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = ensure_gateway_trace_proof_continuity(redact_trace_payload({"recorded_at": _utc_now_iso(), **record}))
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=True) + "\n")


def append_outbound_audit(config_manager: ConfigManager, record: dict[str, Any]) -> None:
    path = outbound_log_path(config_manager)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = redact_trace_payload({"recorded_at": _utc_now_iso(), **record})
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=True) + "\n")


def read_gateway_traces(config_manager: ConfigManager, *, limit: int = 20) -> list[dict[str, Any]]:
    path = trace_log_path(config_manager)
    if not path.exists():
        return []
    traces: list[dict[str, Any]] = []
    for line in _tail_lines(path, limit):
        if not line.strip():
            continue
        try:
            traces.append(redact_trace_payload(json.loads(line)))
        except (json.JSONDecodeError, ValueError):
            continue
    return traces


def read_outbound_audit(config_manager: ConfigManager, *, limit: int = 20) -> list[dict[str, Any]]:
    path = outbound_log_path(config_manager)
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in _tail_lines(path, limit):
        if not line.strip():
            continue
        try:
            records.append(redact_trace_payload(json.loads(line)))
        except (json.JSONDecodeError, ValueError):
            continue
    return records


def redact_gateway_trace_log(
    config_manager: ConfigManager,
    *,
    backup: bool = True,
) -> dict[str, Any]:
    path = trace_log_path(config_manager)
    result: dict[str, Any] = {
        "ok": True,
        "path": str(path),
        "backup_path": None,
        "rows_read": 0,
        "rows_written": 0,
        "parse_errors": 0,
    }
    if not path.exists():
        result["ok"] = False
        result["error"] = "trace_log_missing"
        return result

    original = path.read_text(encoding="utf-8")
    redacted_lines: list[str] = []
    for line in original.splitlines():
        if not line.strip():
            continue
        result["rows_read"] = int(result["rows_read"]) + 1
        try:
            payload = redact_trace_payload(json.loads(line))
        except (json.JSONDecodeError, ValueError):
            result["parse_errors"] = int(result["parse_errors"]) + 1
            continue
        redacted_lines.append(json.dumps(payload, ensure_ascii=True))

    if backup:
        backup_path = path.with_name(f"{path.name}.raw-backup")
        backup_path.write_text(original, encoding="utf-8")
        result["backup_path"] = str(backup_path)

    path.write_text(("\n".join(redacted_lines) + "\n") if redacted_lines else "", encoding="utf-8")
    result["rows_written"] = len(redacted_lines)
    result["ok"] = int(result["parse_errors"]) == 0
    return result


def repair_gateway_trace_proof_continuity(
    config_manager: ConfigManager,
    *,
    backup: bool = True,
    dry_run: bool = False,
) -> dict[str, Any]:
    path = trace_log_path(config_manager)
    result: dict[str, Any] = {
        "ok": True,
        "path": str(path),
        "backup_path": None,
        "dry_run": dry_run,
        "rows_read": 0,
        "rows_written": 0,
        "parse_errors": 0,
        "gap_capsules_added": 0,
        "not_execution_marked": 0,
        "already_had_proof": 0,
        "changed_rows": 0,
    }
    if not path.exists():
        result["ok"] = False
        result["error"] = "trace_log_missing"
        return result

    original = path.read_text(encoding="utf-8")
    repaired_lines: list[str] = []
    for line in original.splitlines():
        if not line.strip():
            continue
        result["rows_read"] = int(result["rows_read"]) + 1
        try:
            before_payload = redact_trace_payload(json.loads(line))
        except (json.JSONDecodeError, ValueError):
            result["parse_errors"] = int(result["parse_errors"]) + 1
            repaired_lines.append(line)
            continue
        before = _stable_json(before_payload)
        already_had_proof = _has_harness_proof(before_payload)
        after_payload = ensure_gateway_trace_proof_continuity(
            before_payload,
            storage="legacy_gap_capsule",
            join_source="builder_gateway_trace_legacy_repair",
        )
        if already_had_proof:
            result["already_had_proof"] = int(result["already_had_proof"]) + 1
        elif _has_harness_proof(after_payload):
            result["gap_capsules_added"] = int(result["gap_capsules_added"]) + 1
        elif _is_proof_not_applicable(after_payload):
            result["not_execution_marked"] = int(result["not_execution_marked"]) + 1
        if _stable_json(after_payload) != before:
            result["changed_rows"] = int(result["changed_rows"]) + 1
        repaired_lines.append(json.dumps(after_payload, ensure_ascii=True))

    if not dry_run:
        if backup:
            backup_path = _next_backup_path(path, ".proof-backup")
            backup_path.write_text(original, encoding="utf-8")
            result["backup_path"] = str(backup_path)
        path.write_text(("\n".join(repaired_lines) + "\n") if repaired_lines else "", encoding="utf-8")
    result["rows_written"] = len(repaired_lines)
    result["ok"] = int(result["parse_errors"]) == 0
    return result


def ensure_gateway_trace_proof_continuity(
    payload: Any,
    *,
    storage: str = "source_gap_capsule",
    join_source: str = "builder_gateway_trace_writer",
) -> Any:
    if not isinstance(payload, dict):
        return payload
    result = dict(payload)
    if _has_harness_proof(result):
        return result
    result.pop("harnessProofRef", None)
    result.pop("harness_proof_ref", None)
    if _gateway_trace_requires_proof_gap(result):
        proof_capsule = _build_gateway_trace_missing_authority_capsule(result)
        result["harnessProofRef"] = proof_capsule["turnRef"]
        result["proofCapsule"] = proof_capsule
        result["proofStatus"] = "missing_harness_authority"
        result["proofStorage"] = storage
        result["proofJoinSource"] = join_source
        return result
    result.setdefault("proofStatus", "not_execution_proof")
    result.setdefault("proofStorage", "not_applicable")
    result.setdefault("proofJoinSource", join_source)
    return result


def _tail_lines(path: Path, n: int) -> list[str]:
    """Read the last *n* lines from a file without loading it entirely into memory."""
    if n <= 0:
        return []
    buf_size = 8192
    lines: list[str] = []
    with path.open("rb") as f:
        f.seek(0, 2)
        remaining = f.tell()
        block_end = remaining
        blocks: list[bytes] = []
        while remaining > 0 and len(lines) <= n:
            read_size = min(buf_size, remaining)
            remaining -= read_size
            f.seek(remaining)
            blocks.append(f.read(read_size))
            lines = b"".join(reversed(blocks)).split(b"\n")
        if lines and lines[-1] == b"":
            lines = lines[:-1]
        return [l.decode("utf-8", errors="replace") for l in lines[-n:]]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def redact_trace_payload(value: Any) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            replacement_key = RAW_ID_KEY_REPLACEMENTS.get(key_text)
            if replacement_key is not None:
                if item not in (None, "", [], {}):
                    result[replacement_key] = trace_identity_ref(replacement_key.replace("_ref", ""), item)
                continue
            if key_text == "session_id" and isinstance(item, str):
                result[key_text] = redact_session_id(item)
                continue
            if SENSITIVE_KEY_PATTERN.search(key_text) and item not in (None, "", [], {}):
                result[key_text] = "[REDACTED]"
            else:
                result[key_text] = redact_trace_payload(item)
        return result
    if isinstance(value, list):
        return [redact_trace_payload(item) for item in value]
    if isinstance(value, str):
        redacted = value
        for pattern in SENSITIVE_TEXT_PATTERNS:
            if pattern.pattern.startswith("(?i)(api"):
                redacted = pattern.sub(lambda match: f"{match.group(1)}{match.group(2)}[REDACTED]", redacted)
            elif pattern.pattern.startswith("(?i)(bearer"):
                redacted = pattern.sub(lambda match: f"{match.group(1)}[REDACTED]", redacted)
            else:
                redacted = pattern.sub("[REDACTED]", redacted)
        for pattern in PATH_LIKE_PATTERNS:
            redacted = pattern.sub("<path>", redacted)
        redacted = POLICY_REASON_PATTERN.sub("internal policy reason", redacted)
        return redacted
    return value


def _gateway_trace_requires_proof_gap(record: dict[str, Any]) -> bool:
    has_request = any(key in record for key in ("request_id", "requestId", "request_ref", "requestRef"))
    has_trace = any(key in record for key in ("trace_ref", "traceRef", "trace_id", "traceId"))
    if not has_request or not has_trace:
        return False
    event = str(record.get("event") or "").strip()
    if event == "telegram_update_processed":
        return True
    if event.endswith("_processed") and record.get("routing_decision"):
        return True
    return bool(record.get("routing_decision") and record.get("delivery_ok") is True)


def _build_gateway_trace_missing_authority_capsule(record: dict[str, Any]) -> dict[str, Any]:
    event = str(record.get("event") or "gateway_trace").strip() or "gateway_trace"
    route = _safe_token(str(record.get("routing_decision") or record.get("bridge_mode") or event), "gateway_trace")
    seed = ":".join(
        str(value)
        for value in (
            record.get("trace_ref") or record.get("traceRef"),
            record.get("request_id") or record.get("requestId") or record.get("request_ref") or record.get("requestRef"),
            record.get("update_id"),
            record.get("recorded_at"),
            event,
        )
        if value not in (None, "")
    ) or _stable_json(record)
    delivered = bool(record.get("delivery_ok"))
    return {
        "schema": HARNESS_PROOF_CAPSULE_SCHEMA,
        "turnRef": _stable_trace_ref("turn", seed),
        "route": route,
        "owner": "spark-intelligence-builder",
        "intent": {
            "kind": route,
            "confidence": "medium",
            "noExecution": False,
        },
        "authority": {
            "decision": "downgraded",
            "contract": "none",
            "riskTier": "read",
            "reasonSummary": (
                "Builder gateway trace row has request and trace continuity, but no fresh Harness proof metadata. "
                "Treat this as an inspectable proof gap, not authorization."
            ),
        },
        "governor": {
            "decision": "not_applicable",
            "verified": False,
        },
        "execution": {
            "status": "completed" if delivered else "failed",
            "tool": "builder.gateway_trace",
            "mutationClass": "read_only",
        },
        "reply": {
            "delivered": delivered,
            "shape": "natural" if int(record.get("response_length") or 0) > 0 else "none",
            "rawReasonsHidden": True,
        },
        "joins": {
            "telegram": "joined" if str(record.get("channel_id") or "") == "telegram" else "not_applicable",
            "builder": "joined",
            "spawner": "not_applicable",
            "provider": "not_applicable",
            "memory": "not_applicable",
            "voice": "not_applicable",
        },
    }


def _has_harness_proof(record: Any) -> bool:
    if not isinstance(record, dict):
        return False
    return (
        _valid_harness_proof_ref(record.get("harnessProofRef"))
        or _valid_harness_proof_ref(record.get("harness_proof_ref"))
        or _proof_capsule_like(record.get("proofCapsule"))
        or _proof_capsule_like(record.get("proof_capsule"))
    )


def _valid_harness_proof_ref(value: Any) -> bool:
    return isinstance(value, str) and HARNESS_PROOF_REF_PATTERN.match(value.strip()) is not None


def _proof_capsule_like(value: Any) -> bool:
    return isinstance(value, dict) and value.get("schema") == HARNESS_PROOF_CAPSULE_SCHEMA


def _is_proof_not_applicable(record: Any) -> bool:
    if not isinstance(record, dict):
        return False
    values = " ".join(
        str(record.get(key) or "").lower()
        for key in ("proofStatus", "proof_status", "proofStorage", "proof_storage")
    )
    return "not_execution_proof" in values or "not_applicable" in values


def redact_session_id(value: str) -> str:
    match = TELEGRAM_SESSION_ID_PATTERN.match(str(value or ""))
    if not match:
        return value
    return f"{match.group(1)}{trace_identity_ref('telegram_session', match.group(2))}"


def _stable_trace_ref(label: str, value: Any) -> str:
    digest = sha256(str(value or "").encode("utf-8")).hexdigest()[:16]
    return f"{label}:sha256:{digest}"


def trace_identity_ref(label: str, value: Any) -> str:
    return _stable_trace_ref(label, value)


def _safe_token(value: str, fallback: str) -> str:
    token = str(value or "").strip()
    if not token:
        return fallback
    normalized = re.sub(r"[^A-Za-z0-9_.:-]+", "_", token)[:120]
    return normalized or fallback


def _stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=True)


def _next_backup_path(path: Path, suffix: str) -> Path:
    backup_path = path.with_name(f"{path.name}{suffix}")
    if not backup_path.exists():
        return backup_path
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds").replace(":", "-")
    digest = sha256(f"{path}:{stamp}".encode("utf-8")).hexdigest()[:8]
    return path.with_name(f"{path.name}{suffix}-{stamp}-{digest}")
