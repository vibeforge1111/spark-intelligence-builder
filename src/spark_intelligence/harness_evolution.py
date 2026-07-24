from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
from typing import Any

from spark_intelligence.harness_contract import _ensure_harness_core_importable
from spark_intelligence.observability.store import latest_events_by_type, record_event
from spark_intelligence.state.db import StateDB


_MAX_LEDGER_LIMIT = 100
_MAX_MANIFESTS = 20
_MAX_MANIFEST_BYTES = 1024 * 1024
_RELEASE_BLOCKERS = (
    "sandbox_execution_missing",
    "evaluation_pack_missing",
    "human_approval_missing",
    "supervised_live_qa_missing",
    "rollback_proof_missing",
    "publication_approval_missing",
)


def build_harness_self_evolution_snapshot(
    state_db: StateDB,
    *,
    limit: int = 20,
    persist_event: bool = True,
) -> dict[str, Any]:
    """Build an idempotent observe-only snapshot from canonical Builder events."""

    return _build_evolution_packet(
        state_db,
        mode="observe",
        limit=limit,
        manifests=[],
        manifest_hashes=[],
        persist_event=persist_event,
    )


def review_harness_change_manifests(
    state_db: StateDB,
    *,
    manifest_paths: list[str],
    limit: int = 20,
    persist_event: bool = True,
) -> dict[str, Any]:
    """Validate and review proposals without executing commands or emitting promotion."""

    manifests, manifest_hashes = _load_change_manifests(manifest_paths)
    return _build_evolution_packet(
        state_db,
        mode="propose",
        limit=limit,
        manifests=manifests,
        manifest_hashes=manifest_hashes,
        persist_event=persist_event,
    )


def _build_evolution_packet(
    state_db: StateDB,
    *,
    mode: str,
    limit: int,
    manifests: list[dict[str, Any]],
    manifest_hashes: list[str],
    persist_event: bool,
) -> dict[str, Any]:
    _ensure_harness_core_importable()
    from spark_harness_core import HarnessKernel, artifact_ref, evidence_ref

    ledgers = _recent_canonical_tool_ledgers(state_db, limit=limit)
    status_counts = Counter(str(item.get("status") or "unknown") for item in ledgers)
    surface_counts = Counter(str(item.get("surface") or "unknown") for item in ledgers)
    evidence_digest = _evidence_digest(
        mode=mode,
        ledger_event_ids=[str(item["event_id"]) for item in ledgers],
        manifest_hashes=manifest_hashes,
    )
    evidence = [
        evidence_ref(
            "runtime_state",
            "state.db:tool_call_ledger",
            f"Canonical governed tool ledgers available: {len(ledgers)}.",
            confidence=1.0 if ledgers else 0.0,
        )
    ]
    if manifests:
        evidence.append(
            evidence_ref(
                "policy",
                "builder:harness-change-manifest-review",
                f"Schema-valid change manifests supplied for review: {len(manifests)}.",
                confidence=1.0,
            )
        )
    kernel = HarnessKernel(surface="builder", actor_id_ref="system:spark-intelligence-builder")
    entries = [
        kernel.experience_entry(
            entry_type="tool_ledger",
            summary=(
                f"{item.get('surface') or 'unknown'} governed ledger "
                f"{item.get('ledger_id') or 'unknown'} recorded "
                f"{item.get('tool_name') or 'unknown tool'} as {item.get('status') or 'unknown'}."
            ),
            artifact=artifact_ref(
                "tool_ledger",
                f"state.db:tool_call_ledger/{item['ledger_id']}",
                "Canonical governed tool-call ledger.",
            ),
            tags=[
                f"surface:{_tag_value(item.get('surface'))}",
                f"status:{_tag_value(item.get('status'))}",
            ],
        )
        for item in ledgers
    ]
    entries.extend(
        kernel.experience_entry(
            entry_type="route_decision",
            summary=f"Change manifest {manifest.get('change_id') or 'unknown'} is queued for human review only.",
            artifact=artifact_ref(
                "change_manifest",
                f"harness:change_manifest/{manifest.get('change_id') or 'unknown'}",
                "Schema-valid change proposal; no execution or promotion authority attached.",
            ),
            tags=["mode:propose", f"verdict:{_tag_value(manifest.get('verdict'))}"],
        )
        for manifest in manifests
    )
    experience_index = kernel.experience_index(entries=entries)
    category_names = (
        "execution",
        "tools",
        "context",
        "lifecycle",
        "observability",
        "verification",
        "governance",
    )
    category_scores = {name: 0.0 for name in category_names}
    if ledgers:
        category_scores["observability"] = 0.5
        category_scores["tools"] = 0.25
    if manifests:
        category_scores["lifecycle"] = 0.25
        category_scores["governance"] = 0.2
    readiness_score = kernel.readiness_score(
        target_kind="capability",
        target_id="capability:spark:harness:self-evolution",
        owner_repo="spark-intelligence-builder",
        category_scores=category_scores,
        category_evidence={name: evidence for name in category_names},
        category_blockers={name: list(_RELEASE_BLOCKERS) for name in category_names},
        promotion_gates={
            "telegram_live_proven": False,
            "startup_benchmark_proven": False,
            "performance_budget_proven": False,
            "governance_rulesets_proven": False,
            "zero_high_agency_legacy_local_gates": False,
        },
        summary="Observe/propose evidence only; execution, promotion, rollback, and publication remain blocked.",
    )
    evolution_run = kernel.self_evolution_run(
        mode=mode,
        experience_index=experience_index,
        readiness_score=readiness_score,
        commands=["review-only: no commands executed"],
        change_manifests=manifests,
        verdict="not_ready",
        summary="Evidence packet requires sandbox proof, human approval, supervised QA, and rollback proof.",
        live_surface_required=True,
    )
    event_type = "harness_self_evolution_observed" if mode == "observe" else "harness_change_manifest_reviewed"
    safe_event_facts = {
        "mode": mode,
        "evidence_digest": evidence_digest,
        "ledger_count": len(ledgers),
        "status_counts": dict(status_counts),
        "surface_counts": dict(surface_counts),
        "manifest_count": len(manifests),
        "change_ids": [str(item.get("change_id") or "unknown") for item in manifests],
        "manifest_sha256": manifest_hashes,
        "promotion_verdict": "not_ready",
        "commands_executed": False,
        "release_blockers": list(_RELEASE_BLOCKERS),
    }
    event_id = _persist_idempotent_event(
        state_db,
        event_type=event_type,
        evidence_digest=evidence_digest,
        facts=safe_event_facts,
    ) if persist_event else None
    requested_commands = list(
        dict.fromkeys(
            str(command).strip()
            for manifest in manifests
            for command in (manifest.get("required_tests") if isinstance(manifest.get("required_tests"), list) else [])
            if str(command).strip()
        )
    )
    return {
        "ok": True,
        "mode": mode,
        "event_id": event_id,
        "evidence_digest": evidence_digest,
        "ledger_count": len(ledgers),
        "status_counts": dict(status_counts),
        "surface_counts": dict(surface_counts),
        "manifest_count": len(manifests),
        "manifest_sha256": manifest_hashes,
        "requested_commands": requested_commands,
        "commands_executed": False,
        "release_blockers": list(_RELEASE_BLOCKERS),
        "authority": {
            "telegram_live_proven": False,
            "human_approval_present": False,
            "sandbox_execution_present": False,
            "promotion_allowed": False,
            "rollback_allowed": False,
            "publication_allowed": False,
        },
        "experience_index": experience_index,
        "readiness_score": readiness_score,
        "self_evolution_run": evolution_run,
    }


def _recent_canonical_tool_ledgers(state_db: StateDB, *, limit: int) -> list[dict[str, Any]]:
    bounded_limit = max(1, min(int(limit), _MAX_LEDGER_LIMIT))
    with state_db.connect() as conn:
        rows = conn.execute(
            """
            SELECT ledger_id, tool_name, surface, status, ledger_json, created_at
            FROM tool_call_ledger
            ORDER BY created_at DESC, ledger_id DESC
            LIMIT ?
            """,
            (bounded_limit,),
        ).fetchall()
    ledgers: list[dict[str, Any]] = []
    for row in rows:
        try:
            ledger = json.loads(str(row["ledger_json"] or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            ledger = {}
        if not isinstance(ledger, dict):
            ledger = {}
        result = ledger.get("result") if isinstance(ledger.get("result"), dict) else {}
        ledger_id = str(row["ledger_id"])
        ledgers.append({
            "event_id": ledger_id,
            "ledger_id": ledger_id,
            "tool_name": str(row["tool_name"] or ledger.get("tool_name") or "unknown"),
            "surface": str(row["surface"] or ledger.get("surface") or "unknown"),
            "status": str(row["status"] or result.get("status") or "unknown"),
        })
    return ledgers


def _load_change_manifests(paths: list[str]) -> tuple[list[dict[str, Any]], list[str]]:
    if not paths:
        raise ValueError("At least one change manifest is required")
    _ensure_harness_core_importable()
    from spark_harness_core import validate_instance

    manifests: list[dict[str, Any]] = []
    hashes: list[str] = []
    for raw_path in paths:
        path = Path(raw_path).expanduser()
        if path.is_symlink():
            raise ValueError(f"Change manifest must not be a symlink: {path}")
        path = path.resolve(strict=True)
        if not path.is_file():
            raise ValueError(f"Change manifest is not a file: {path}")
        raw = path.read_bytes()
        if len(raw) > _MAX_MANIFEST_BYTES:
            raise ValueError(f"Change manifest exceeds {_MAX_MANIFEST_BYTES} bytes: {path}")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"Change manifest is not valid UTF-8 JSON: {path}") from exc
        items = payload if isinstance(payload, list) else [payload]
        for item in items:
            if not isinstance(item, dict):
                raise ValueError(f"Change manifest must contain JSON objects: {path}")
            validate_instance("change-manifest-v1", item)
            manifests.append(item)
            hashes.append(hashlib.sha256(json.dumps(item, sort_keys=True, separators=(",", ":")).encode()).hexdigest())
            if len(manifests) > _MAX_MANIFESTS:
                raise ValueError(f"At most {_MAX_MANIFESTS} change manifests may be reviewed at once")
    return manifests, hashes


def _evidence_digest(*, mode: str, ledger_event_ids: list[str], manifest_hashes: list[str]) -> str:
    payload = json.dumps(
        {"mode": mode, "ledger_event_ids": ledger_event_ids, "manifest_sha256": manifest_hashes},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _persist_idempotent_event(
    state_db: StateDB,
    *,
    event_type: str,
    evidence_digest: str,
    facts: dict[str, Any],
) -> str:
    for event in latest_events_by_type(state_db, event_type=event_type, limit=20):
        event_facts = event.get("facts_json") if isinstance(event.get("facts_json"), dict) else {}
        if event_facts.get("evidence_digest") == evidence_digest:
            return str(event["event_id"])
    return record_event(
        state_db,
        event_type=event_type,
        component="harness",
        summary=(
            "Harness self-evolution evidence snapshot recorded without execution."
            if event_type == "harness_self_evolution_observed"
            else "Harness change manifests reviewed without execution or promotion."
        ),
        correlation_id=f"harness-evolution:{evidence_digest[:16]}",
        reason_code="observe_propose_only",
        facts=facts,
        provenance={"source_kind": "spark_harness_core_review", "source_ref": f"sha256:{evidence_digest}"},
    )


def _tag_value(value: Any) -> str:
    return str(value or "unknown").strip().replace(" ", "_") or "unknown"
