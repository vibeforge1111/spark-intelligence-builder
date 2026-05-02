from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from spark_intelligence.config.loader import ConfigManager


@dataclass(frozen=True)
class LiveTelegramRegressionCadenceResult:
    payload: dict[str, Any]

    def to_json(self) -> str:
        return json.dumps(self.payload, indent=2)

    def to_text(self) -> str:
        summary = self.payload.get("summary") if isinstance(self.payload.get("summary"), dict) else {}
        latest = self.payload.get("latest_evidence") if isinstance(self.payload.get("latest_evidence"), dict) else {}
        runbook = self.payload.get("operator_runbook") if isinstance(self.payload.get("operator_runbook"), dict) else {}
        lines = [
            "Spark live Telegram regression cadence",
            f"- status: {self.payload.get('status') or 'unknown'}",
            f"- matrix_cases: {summary.get('case_count', 0)}",
            f"- suites: {summary.get('suite_count', 0)}",
            f"- latest_evidence: {self.payload.get('latest_evidence_status') or 'missing'}",
            f"- report_path: {self.payload.get('report_path') or 'not_written'}",
            "- authority: observability_non_authoritative",
        ]
        runbook_path = str(runbook.get("path") or "").strip()
        if runbook_path:
            lines.append(f"- prompt_runbook: {runbook_path}")
        if latest:
            if str(latest.get("missing") or "").strip():
                lines.append(f"- latest_missing: {latest.get('missing')}")
            lines.append(f"- eligible_runtime_traces: {latest.get('scanned_runtime_traces', 0)}")
            next_action = str(latest.get("next_action") or "").strip()
            if next_action:
                lines.append(f"- next_action: {next_action}")
        commands = self.payload.get("commands") if isinstance(self.payload.get("commands"), dict) else {}
        prompt_command = str(commands.get("print_prompts") or "").strip()
        verifier_command = str(commands.get("verify_live_traces") or "").strip()
        if prompt_command:
            lines.append(f"- print_prompts: {prompt_command}")
        if verifier_command:
            lines.append(f"- verify: {verifier_command}")
        return "\n".join(lines)


def build_live_telegram_regression_cadence(
    *,
    config_manager: ConfigManager,
    matrix_path: str | Path | None = None,
    scenario_path: str | Path | None = None,
    verifier_script_path: str | Path | None = None,
    write_report: bool = True,
) -> LiveTelegramRegressionCadenceResult:
    repo_root = _repo_root()
    matrix = Path(matrix_path) if matrix_path else repo_root / "ops" / "natural-language-live-commands.json"
    scenario = Path(scenario_path) if scenario_path else repo_root / "scenario-packs" / "telegram-live-self-awareness-wiki.txt"
    verifier = (
        Path(verifier_script_path)
        if verifier_script_path
        else repo_root / "scripts" / "run_live_telegram_self_awareness_wiki_probe.ps1"
    )
    missing_files = [str(path) for path in (matrix, scenario, verifier) if not path.exists()]
    matrix_payload = _load_json(matrix) if matrix.exists() else {}
    cases = [case for case in matrix_payload.get("cases") or [] if isinstance(case, dict)]
    suites = _suite_rows(cases)
    prompt_pack = _prompt_pack(scenario)
    evidence_dir = config_manager.paths.home / "artifacts" / "live-telegram-regression"
    prompt_runbook_path = evidence_dir / "prompt-pack-latest.txt"
    latest_evidence = _latest_evidence(evidence_dir)
    latest_evidence_status = _latest_evidence_status(latest_evidence)
    status = "blocked" if missing_files else "evidence_present" if latest_evidence_status == "passed" else "needs_live_evidence"
    commands = {
        "print_prompts": _powershell_command(verifier=verifier, spark_home=config_manager.paths.home, flags=["-PrintPromptsOnly"]),
        "verify_live_traces": _powershell_command(
            verifier=verifier,
            spark_home=config_manager.paths.home,
            flags=["-OutputDir", str(evidence_dir), "-Json"],
        ),
    }
    payload = {
        "kind": "live_telegram_regression_cadence",
        "checked_at": _utc_timestamp(),
        "status": status,
        "healthy": status == "evidence_present",
        "summary": {
            "case_count": len(cases),
            "suite_count": len(suites),
            "prompt_count": len(prompt_pack),
            "missing_file_count": len(missing_files),
        },
        "matrix": {
            "path": str(matrix),
            "matrix_id": str(matrix_payload.get("matrix_id") or ""),
            "purpose": str(matrix_payload.get("purpose") or ""),
            "case_count": len(cases),
            "guardrails": list(matrix_payload.get("guardrails") or []),
        },
        "suites": suites,
        "prompt_pack": {
            "path": str(scenario),
            "prompt_count": len(prompt_pack),
            "prompts": prompt_pack,
        },
        "operator_runbook": {
            "path": str(prompt_runbook_path),
            "written": bool(write_report and not missing_files),
            "purpose": "Manual live-bot prompt pack for collecting real Telegram runtime traces.",
            "completion_checklist": [
                "Send every prompt to the real Spark Telegram bot in order.",
                "Wait for each bot reply before sending the next prompt.",
                "Rerun verify_live_traces after the final reply.",
                "Do not treat simulation, soak, or CLI traces as live release evidence.",
            ],
        },
        "commands": commands,
        "artifact_contract": {
            "output_dir": str(evidence_dir),
            "latest_report": str(evidence_dir / "latest.json"),
            "required_fields": [
                "ok",
                "spark_home",
                "scanned_traces",
                "scanned_runtime_traces",
                "matched",
                "expected",
                "trace_eligibility",
                "traces",
            ],
            "trace_requirements": [
                "simulation=false",
                "origin_surface=telegram_runtime",
                "request_id starts with telegram:",
                "bridge_mode and routing_decision match matrix expectations",
            ],
        },
        "latest_evidence_status": latest_evidence_status,
        "latest_evidence": latest_evidence,
        "missing_files": missing_files,
        "authority": "observability_non_authoritative",
        "memory_policy": "typed_report_not_chat_memory",
        "promotion_gate": "self_awareness_releases_need_live_telegram_evidence_before_confident_release_claims",
        "warnings": _warnings(missing_files=missing_files, latest_evidence_status=latest_evidence_status),
    }
    if write_report:
        report_path = _report_path(config_manager=config_manager, checked_at=str(payload["checked_at"]))
        payload["report_path"] = str(report_path)
        payload["report_written"] = True
        _write_report(config_manager=config_manager, report_path=report_path, payload=payload)
        if not missing_files:
            _write_prompt_runbook(path=prompt_runbook_path, prompts=prompt_pack, commands=commands)
    else:
        payload["report_path"] = ""
        payload["report_written"] = False
    return LiveTelegramRegressionCadenceResult(payload=payload)


def _suite_rows(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_suite: dict[str, list[dict[str, Any]]] = {}
    for case in cases:
        suite = str(case.get("suite") or _suite_for_case(str(case.get("id") or ""))).strip()
        by_suite.setdefault(suite, []).append(case)
    rows: list[dict[str, Any]] = []
    for suite, suite_cases in sorted(by_suite.items()):
        rows.append(
            {
                "suite": suite,
                "case_count": len(suite_cases),
                "case_ids": [str(case.get("id") or "") for case in suite_cases],
                "surfaces": sorted({str(case.get("surface") or "unknown") for case in suite_cases}),
                "evidence_required": "live_telegram_trace",
            }
        )
    return rows


def _suite_for_case(case_id: str) -> str:
    lowered = case_id.casefold()
    if "wiki" in lowered:
        return "llm_wiki"
    if "memory" in lowered:
        return "governed_memory"
    if "build_quality" in lowered:
        return "build_quality"
    if "route_explanation" in lowered:
        return "route_explanation"
    if "self" in lowered:
        return "self_awareness"
    if lowered.startswith("telegram_slash"):
        return "telegram_commands"
    return "misc"


def _prompt_pack(path: Path) -> list[str]:
    if not path.exists():
        return []
    prompts: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        prompts.append(stripped)
    return prompts


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _dict_value(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _latest_evidence(evidence_dir: Path) -> dict[str, Any]:
    latest = evidence_dir / "latest.json"
    if not latest.exists():
        return {}
    payload = _load_json(latest)
    if not payload:
        return {"path": str(latest), "readable": False}
    return {
        "path": str(latest),
        "readable": True,
        "ok": bool(payload.get("ok")),
        "scanned_traces": int(payload.get("scanned_traces") or 0),
        "scanned_runtime_traces": int(payload.get("scanned_runtime_traces") or 0),
        "matched": int(payload.get("matched") or 0),
        "expected": int(payload.get("expected") or 0),
        "missing": str(payload.get("missing") or ""),
        "spark_home": str(payload.get("spark_home") or ""),
        "recorded_at": str(payload.get("recorded_at") or payload.get("checked_at") or ""),
        "trace_eligibility": _dict_value(payload.get("trace_eligibility")),
        "next_action": str(payload.get("next_action") or ""),
    }


def _latest_evidence_status(latest_evidence: dict[str, Any]) -> str:
    if not latest_evidence:
        return "missing"
    if not latest_evidence.get("readable"):
        return "unreadable"
    if bool(latest_evidence.get("ok")) and int(latest_evidence.get("matched") or 0) >= int(latest_evidence.get("expected") or 1):
        return "passed"
    return "failed_or_incomplete"


def _warnings(*, missing_files: list[str], latest_evidence_status: str) -> list[str]:
    warnings: list[str] = []
    if missing_files:
        warnings.append("live_telegram_cadence_files_missing")
    if latest_evidence_status == "missing":
        warnings.append("live_telegram_evidence_missing")
    if latest_evidence_status == "failed_or_incomplete":
        warnings.append("live_telegram_evidence_failed_or_incomplete")
    if latest_evidence_status == "unreadable":
        warnings.append("live_telegram_evidence_unreadable")
    return warnings


def _powershell_command(*, verifier: Path, spark_home: Path, flags: list[str]) -> str:
    parts = [
        "powershell",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(verifier),
        "-SparkHome",
        str(spark_home),
        *flags,
    ]
    return " ".join(_quote(part) for part in parts)


def _quote(value: str | Path) -> str:
    text = str(value)
    if not text or any(char.isspace() for char in text):
        return f'"{text}"'
    return text


def _report_path(*, config_manager: ConfigManager, checked_at: str) -> Path:
    reports_dir = config_manager.paths.home / "artifacts" / "live-telegram-regression"
    reports_dir.mkdir(parents=True, exist_ok=True)
    timestamp = checked_at.replace(":", "").replace("+", "Z")
    return reports_dir / f"cadence-{timestamp}.json"


def _write_report(*, config_manager: ConfigManager, report_path: Path, payload: dict[str, Any]) -> None:
    report_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    latest_path = config_manager.paths.home / "artifacts" / "live-telegram-regression" / "cadence-latest.json"
    latest_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _write_prompt_runbook(*, path: Path, prompts: list[str], commands: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "Spark live Telegram self-awareness/wiki prompt pack",
        "",
        "Send these prompts to the real Spark Telegram bot, in order.",
        "Wait for each bot reply before sending the next prompt.",
        "Simulation, soak, and CLI traces do not count as live release evidence.",
        "",
        "Prompts:",
    ]
    lines.extend(f"{index}. {prompt}" for index, prompt in enumerate(prompts, start=1))
    lines.extend(
        [
            "",
            "After sending all prompts, run verify_live_traces:",
            commands.get("verify_live_traces", ""),
            "",
            "Reference command for printing prompts:",
            commands.get("print_prompts", ""),
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _utc_timestamp() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()
