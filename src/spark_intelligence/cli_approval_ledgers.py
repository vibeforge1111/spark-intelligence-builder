from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from spark_intelligence.observability.store import persist_bound_ledger
from spark_intelligence.state.db import StateDB


DEFAULT_CLI_APPROVAL_LEDGER_KEEP_FILES = 1000


@dataclass(frozen=True)
class CliApprovalLedgerImportResult:
    ledger_dir: str
    imported: int
    skipped: int
    pruned: int
    errors: list[str]
    ledger_ids: list[str]

    def to_payload(self) -> dict[str, Any]:
        return {
            "ledger_dir": self.ledger_dir,
            "imported": self.imported,
            "skipped": self.skipped,
            "pruned": self.pruned,
            "errors": self.errors,
            "ledger_ids": self.ledger_ids,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_payload(), indent=2)

    def to_text(self) -> str:
        lines = ["Spark CLI approval ledger import"]
        lines.append(f"- ledger_dir: {self.ledger_dir}")
        lines.append(f"- imported: {self.imported}")
        lines.append(f"- skipped: {self.skipped}")
        lines.append(f"- pruned: {self.pruned}")
        lines.append(f"- errors: {len(self.errors)}")
        for ledger_id in self.ledger_ids[:10]:
            lines.append(f"- ledger: {ledger_id}")
        for error in self.errors[:5]:
            lines.append(f"- error: {error}")
        return "\n".join(lines)


def default_cli_approval_ledger_dir() -> Path:
    override = os.environ.get("SPARK_CLI_APPROVAL_LEDGER_DIR")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".spark" / "state" / "approval-ledgers"


def import_cli_approval_ledgers(
    state_db: StateDB,
    *,
    ledger_dir: Path | None = None,
    retention_cap: int | None = DEFAULT_CLI_APPROVAL_LEDGER_KEEP_FILES,
) -> CliApprovalLedgerImportResult:
    resolved_dir = (ledger_dir or default_cli_approval_ledger_dir()).expanduser()
    imported = 0
    skipped = 0
    pruned = 0
    errors: list[str] = []
    ledger_ids: list[str] = []
    if not resolved_dir.exists():
        return CliApprovalLedgerImportResult(
            ledger_dir=str(resolved_dir),
            imported=0,
            skipped=0,
            pruned=0,
            errors=[f"ledger dir not found: {resolved_dir}"],
            ledger_ids=[],
        )
    for path in sorted(resolved_dir.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            errors.append(f"{path.name}: {exc}")
            continue
        if not isinstance(payload, dict) or payload.get("schema_version") != "tool-call-ledger-v1":
            skipped += 1
            continue
        try:
            row = _row_from_cli_ledger(payload, source_path=path)
            ledger_id = persist_bound_ledger(state_db, row=row, component="spark_cli_approval_ledgers")
        except ValueError as exc:
            errors.append(f"{path.name}: {exc}")
            continue
        imported += 1
        ledger_ids.append(ledger_id)
    if retention_cap is not None:
        try:
            pruned = prune_cli_approval_ledger_dir(resolved_dir, keep_files=retention_cap)
        except OSError as exc:
            errors.append(f"approval ledger prune: {exc}")
    return CliApprovalLedgerImportResult(
        ledger_dir=str(resolved_dir),
        imported=imported,
        skipped=skipped,
        pruned=pruned,
        errors=errors,
        ledger_ids=ledger_ids,
    )


def prune_cli_approval_ledger_dir(
    ledger_dir: Path,
    *,
    keep_files: int = DEFAULT_CLI_APPROVAL_LEDGER_KEEP_FILES,
) -> int:
    if keep_files < 0:
        raise ValueError("keep_files must be non-negative")
    if not ledger_dir.exists():
        return 0
    ledger_files = [path for path in ledger_dir.glob("*.json") if path.is_file()]
    excess = len(ledger_files) - keep_files
    if excess <= 0:
        return 0
    pruned = 0
    for path in sorted(ledger_files, key=lambda candidate: (candidate.stat().st_mtime_ns, candidate.name))[:excess]:
        path.unlink()
        pruned += 1
    return pruned


def _row_from_cli_ledger(payload: dict[str, Any], *, source_path: Path) -> dict[str, Any]:
    authorization = payload.get("authorization") if isinstance(payload.get("authorization"), dict) else {}
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    trace = payload.get("trace") if isinstance(payload.get("trace"), dict) else {}
    row = {
        "ledger_id": _required(payload.get("ledger_id"), "ledger_id"),
        "turn_id": _required(payload.get("turn_id"), "turn_id"),
        "action_id": _required(payload.get("action_id"), "action_id"),
        "capability_id": _required(payload.get("capability_id"), "capability_id"),
        "authorization_decision_id": _required(authorization.get("decision_id"), "authorization.decision_id"),
        "tool_name": _text(payload.get("tool_name")),
        "owner_system": "spark-cli",
        "mutation_class": _text(payload.get("action_class") or authorization.get("risk_tier")),
        "outcome": _text(authorization.get("verdict")),
        "status": _text(result.get("status")),
        "surface": "spark_cli",
        "request_id": _text(payload.get("command_digest_ref")),
        "trace_ref": _text(trace.get("id")),
        "summary": _text(result.get("summary")),
        "ledger_json": payload,
        "created_at": _text(payload.get("created_at")),
    }
    row["ledger_json"]["source_path"] = str(source_path)
    return row


def _required(value: Any, field: str) -> str:
    text = _text(value)
    if text is None:
        raise ValueError(f"missing {field}")
    return text


def _text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None
