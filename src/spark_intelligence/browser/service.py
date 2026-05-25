from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from subprocess import TimeoutExpired
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.execution.governed import run_governed_command

BROWSER_STATUS_HOOK = "browser.status"
BROWSER_NAVIGATE_HOOK = "browser.navigate"
BROWSER_PAGE_INTERACTIVES_LIST_HOOK = "browser.page.interactives.list"
BROWSER_TAB_WAIT_HOOK = "browser.tab.wait"
BROWSER_PAGE_DOM_EXTRACT_HOOK = "browser.page.dom_extract"
BROWSER_PAGE_TEXT_EXTRACT_HOOK = "browser.page.text_extract"
BROWSER_PAGE_SNAPSHOT_HOOK = "browser.page.snapshot"
SCHEMA_VERSION = "spark-browser-hook.v1"
DEFAULT_BROWSER_FAMILY = "brave"
DEFAULT_PROFILE_KEY = "spark-default"
DEFAULT_PROFILE_MODE = "dedicated"
DEFAULT_AGENT_ID = "agent:local-operator"
LEGACY_BROWSER_BACKEND_KIND = "legacy_browser_extension"
LEGACY_BROWSER_BACKEND_LABEL = "Legacy Browser Extension"
LEGACY_BROWSER_DEFAULT_SURFACE = "advanced"
LEGACY_BROWSER_REPLACEMENT_SURFACE = "spark-cli browser-use agent"
BROWSER_USE_BACKEND_KIND = "browser_use_adapter"
BROWSER_USE_BACKEND_LABEL = "Browser-use Adapter"
BROWSER_USE_STATUS_ENV = "SPARK_BROWSER_USE_STATUS_PATH"
BROWSER_USE_DOCTOR_TIMEOUT_SECONDS = 20
BROWSER_USE_SMOKE_TIMEOUT_SECONDS = 30
BROWSER_USE_SMOKE_URL = "https://example.com"
_BROWSER_USE_SMOKE_SCREENSHOT_NAME = "smoke-screenshot.png"
_BROWSER_USE_REQUIRED_DOCTOR_CHECKS = {"browser", "network", "package"}
_BROWSER_USE_REQUIRED_SMOKE_PROOFS = {"public_page_open", "state_read"}
_BROWSER_USE_READY_STATUSES = {
    "ready",
    "running",
    "healthy",
    "available",
    "ok",
    "success",
    "completed",
}
_BROWSER_USE_FAILURE_STATUSES = {"failed", "failure", "error", "unavailable", "degraded"}
_BROWSER_USE_REQUIRED_PROOFS = {"doctor", "public_page_open", "screenshot_capture", "state_read"}
_BROWSER_USE_PROOF_TTL_SECONDS = 15 * 60


def build_browser_status_payload(
    *,
    config_manager: ConfigManager,
    browser_family: str | None = None,
    profile_key: str | None = None,
    profile_mode: str | None = None,
    agent_id: str | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "request_id": request_id or f"browser-status:{uuid4().hex[:12]}",
        "hook_name": BROWSER_STATUS_HOOK,
        "agent_id": agent_id or DEFAULT_AGENT_ID,
        "workspace_id": _workspace_id(config_manager),
        "risk_class": "read_only",
        "approval_mode": "not_required",
        "approval_id": None,
        "target": _build_target(
            browser_family=browser_family,
            profile_key=profile_key,
            profile_mode=profile_mode,
            origin=None,
        ),
        "arguments": {},
        "policy_context": {
            "allowed_domains": [],
            "sensitive_domain": False,
            "operator_required": False,
        },
    }


def collect_browser_use_adapter_status(config_manager: ConfigManager) -> dict[str, Any] | None:
    config = config_manager.load()
    browser_use_config = _browser_use_config(config)
    status_path = _browser_use_status_path(config_manager, browser_use_config)
    status_doc = _read_browser_use_status(status_path)
    package_available = importlib.util.find_spec("browser_use") is not None
    cli_path = _browser_use_cli_path()
    receipt_package_available = status_doc.get("package_available") is True
    receipt_cli_path = _string_or_none(status_doc.get("cli_path"))
    effective_package_available = package_available or receipt_package_available
    effective_cli_path = cli_path or receipt_cli_path
    configured = bool(browser_use_config) or status_path is not None or effective_package_available or bool(effective_cli_path)
    if not configured and not status_doc:
        return None

    raw_status = _browser_use_raw_status(status_doc)
    proof_tokens = _browser_use_proofs(status_doc)
    proof_fresh = _browser_use_proof_is_fresh(status_doc)
    proof_complete = _BROWSER_USE_REQUIRED_PROOFS.issubset(set(proof_tokens))
    screenshot_ok = _browser_use_screenshot_ok(status_doc)
    ready_signal = raw_status in _BROWSER_USE_READY_STATUSES or bool(status_doc.get("ready") is True)
    ready = ready_signal and proof_complete and proof_fresh and screenshot_ok
    failed = raw_status in _BROWSER_USE_FAILURE_STATUSES or bool(status_doc.get("ready") is False)
    status = "completed" if ready else "failed" if failed else "configured"
    failure_reason = _browser_use_failure_reason(
        status_doc,
        status=status,
        ready_signal=ready_signal,
        proof_complete=proof_complete,
        proof_fresh=proof_fresh,
        screenshot_ok=screenshot_ok,
    )
    summary = _browser_use_summary(
        status=status,
        raw_status=raw_status,
        package_available=effective_package_available,
        cli_path=effective_cli_path,
        status_path=status_path,
        proofs=status_doc.get("proofs"),
        failure_reason=failure_reason,
    )
    return {
        "status": status,
        "backend_kind": BROWSER_USE_BACKEND_KIND,
        "backend_label": BROWSER_USE_BACKEND_LABEL,
        "adapter_status": raw_status or status,
        "configured": bool(configured),
        "package_available": bool(effective_package_available),
        "cli_path": effective_cli_path,
        "status_path": str(status_path) if status_path else None,
        "last_success_at": _string_or_none(status_doc.get("last_success_at")),
        "last_failure_at": _string_or_none(status_doc.get("last_failure_at")),
        "last_failure_reason": failure_reason,
        "proofs": proof_tokens,
        "proof_fresh": proof_fresh,
        "required_proofs": sorted(_BROWSER_USE_REQUIRED_PROOFS),
        "error_code": _string_or_none(status_doc.get("error_code")),
        "error_message": failure_reason,
        "evidence_summary": summary,
        "provenance": {
            "source": "browser_use_adapter_status",
            "status_path": str(status_path) if status_path else None,
        },
    }


def collect_browser_use_probe_contract(config_manager: ConfigManager) -> dict[str, Any]:
    config = config_manager.load()
    browser_use_config = _browser_use_config(config)
    status_path = _browser_use_status_path(config_manager, browser_use_config) or _browser_use_default_status_path(config_manager)
    cli_path = _browser_use_cli_path()
    if cli_path:
        _refresh_browser_use_status_from_cli(status_path=status_path, cli_path=cli_path)

    status = collect_browser_use_adapter_status(config_manager)
    if status is not None:
        return status

    status_path = _browser_use_default_status_path(config_manager)
    package_available = importlib.util.find_spec("browser_use") is not None
    cli_path = _browser_use_cli_path()
    failure_reason = "browser-use adapter status source is not ready."
    return {
        "status": "missing_status",
        "backend_kind": BROWSER_USE_BACKEND_KIND,
        "backend_label": BROWSER_USE_BACKEND_LABEL,
        "adapter_status": "missing_status",
        "configured": False,
        "package_available": bool(package_available),
        "cli_path": cli_path,
        "status_path": str(status_path),
        "last_success_at": None,
        "last_failure_at": None,
        "last_failure_reason": failure_reason,
        "error_code": "BROWSER_USE_STATUS_MISSING",
        "error_message": failure_reason,
        "evidence_summary": _browser_use_summary(
            status="missing_status",
            raw_status="missing_status",
            package_available=package_available,
            cli_path=cli_path,
            status_path=status_path,
            proofs={},
            failure_reason=failure_reason,
        ),
        "provenance": {
            "source": "browser_use_probe_contract",
            "status_path": str(status_path),
        },
    }


def build_browser_page_snapshot_payload(
    *,
    config_manager: ConfigManager,
    origin: str,
    tab_id: str | None = None,
    browser_family: str | None = None,
    profile_key: str | None = None,
    profile_mode: str | None = None,
    agent_id: str | None = None,
    request_id: str | None = None,
    max_text_characters: int = 500,
    max_controls: int = 10,
    allowed_domains: list[str] | None = None,
    sensitive_domain: bool = False,
    operator_required: bool = False,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "request_id": request_id or f"browser-page-snapshot:{uuid4().hex[:12]}",
        "hook_name": BROWSER_PAGE_SNAPSHOT_HOOK,
        "agent_id": agent_id or DEFAULT_AGENT_ID,
        "workspace_id": _workspace_id(config_manager),
        "risk_class": "read_only",
        "approval_mode": "not_required",
        "approval_id": None,
        "target": {
            **_build_target(
                browser_family=browser_family,
                profile_key=profile_key,
                profile_mode=profile_mode,
                origin=origin,
            ),
            "tab_id": tab_id,
        },
        "arguments": {
            "max_text_characters": max_text_characters,
            "max_controls": max_controls,
        },
        "policy_context": {
            "allowed_domains": _normalize_allowed_domains(origin, allowed_domains or []),
            "sensitive_domain": bool(sensitive_domain),
            "operator_required": bool(operator_required),
        },
    }


def build_browser_navigate_payload(
    *,
    config_manager: ConfigManager,
    url: str,
    disposition: str = "new_background_tab",
    browser_family: str | None = None,
    profile_key: str | None = None,
    profile_mode: str | None = None,
    agent_id: str | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    origin = _normalize_origin(url)
    return {
        "schema_version": SCHEMA_VERSION,
        "request_id": request_id or f"browser-navigate:{uuid4().hex[:12]}",
        "hook_name": BROWSER_NAVIGATE_HOOK,
        "agent_id": agent_id or DEFAULT_AGENT_ID,
        "workspace_id": _workspace_id(config_manager),
        "risk_class": "read_only",
        "approval_mode": "not_required",
        "approval_id": None,
        "target": _build_target(
            browser_family=browser_family,
            profile_key=profile_key,
            profile_mode=profile_mode,
            origin=origin,
        ),
        "arguments": {
            "url": url,
            "disposition": disposition,
        },
        "policy_context": {
            "allowed_domains": _normalize_allowed_domains(url, []),
            "sensitive_domain": False,
            "operator_required": False,
        },
    }


def build_browser_page_interactives_list_payload(
    *,
    config_manager: ConfigManager,
    origin: str,
    tab_id: str | None = None,
    browser_family: str | None = None,
    profile_key: str | None = None,
    profile_mode: str | None = None,
    agent_id: str | None = None,
    request_id: str | None = None,
    max_items: int = 12,
    allowed_domains: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "request_id": request_id or f"browser-page-interactives-list:{uuid4().hex[:12]}",
        "hook_name": BROWSER_PAGE_INTERACTIVES_LIST_HOOK,
        "agent_id": agent_id or DEFAULT_AGENT_ID,
        "workspace_id": _workspace_id(config_manager),
        "risk_class": "read_only",
        "approval_mode": "not_required",
        "approval_id": None,
        "target": {
            **_build_target(
                browser_family=browser_family,
                profile_key=profile_key,
                profile_mode=profile_mode,
                origin=origin,
            ),
            "tab_id": tab_id,
        },
        "arguments": {
            "max_items": max_items,
        },
        "policy_context": {
            "allowed_domains": _normalize_allowed_domains(origin, allowed_domains or []),
            "sensitive_domain": False,
            "operator_required": False,
        },
    }


def build_browser_tab_wait_payload(
    *,
    config_manager: ConfigManager,
    origin: str,
    tab_id: str,
    browser_family: str | None = None,
    profile_key: str | None = None,
    profile_mode: str | None = None,
    agent_id: str | None = None,
    request_id: str | None = None,
    wait_until: str = "complete",
    timeout_ms: int = 4000,
    poll_interval_ms: int = 100,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "request_id": request_id or f"browser-tab-wait:{uuid4().hex[:12]}",
        "hook_name": BROWSER_TAB_WAIT_HOOK,
        "agent_id": agent_id or DEFAULT_AGENT_ID,
        "workspace_id": _workspace_id(config_manager),
        "risk_class": "read_only",
        "approval_mode": "not_required",
        "approval_id": None,
        "target": {
            **_build_target(
                browser_family=browser_family,
                profile_key=profile_key,
                profile_mode=profile_mode,
                origin=origin,
            ),
            "tab_id": tab_id,
        },
        "arguments": {
            "wait_until": wait_until,
            "timeout_ms": timeout_ms,
            "poll_interval_ms": poll_interval_ms,
        },
        "policy_context": {
            "allowed_domains": _normalize_allowed_domains(origin, []),
            "sensitive_domain": False,
            "operator_required": False,
        },
    }


def build_browser_page_dom_extract_payload(
    *,
    config_manager: ConfigManager,
    origin: str,
    tab_id: str | None = None,
    browser_family: str | None = None,
    profile_key: str | None = None,
    profile_mode: str | None = None,
    agent_id: str | None = None,
    request_id: str | None = None,
    max_nodes: int = 12,
    max_text_characters_per_node: int = 160,
    max_headings: int = 6,
    max_landmarks: int = 6,
    allowed_domains: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "request_id": request_id or f"browser-page-dom-extract:{uuid4().hex[:12]}",
        "hook_name": BROWSER_PAGE_DOM_EXTRACT_HOOK,
        "agent_id": agent_id or DEFAULT_AGENT_ID,
        "workspace_id": _workspace_id(config_manager),
        "risk_class": "read_only",
        "approval_mode": "not_required",
        "approval_id": None,
        "target": {
            **_build_target(
                browser_family=browser_family,
                profile_key=profile_key,
                profile_mode=profile_mode,
                origin=origin,
            ),
            "tab_id": tab_id,
        },
        "arguments": {
            "max_nodes": max_nodes,
            "max_text_characters_per_node": max_text_characters_per_node,
            "max_headings": max_headings,
            "max_landmarks": max_landmarks,
        },
        "policy_context": {
            "allowed_domains": _normalize_allowed_domains(origin, allowed_domains or []),
            "sensitive_domain": False,
            "operator_required": False,
        },
    }


def build_browser_page_text_extract_payload(
    *,
    config_manager: ConfigManager,
    origin: str,
    tab_id: str | None = None,
    browser_family: str | None = None,
    profile_key: str | None = None,
    profile_mode: str | None = None,
    agent_id: str | None = None,
    request_id: str | None = None,
    max_text_characters: int = 1600,
    max_controls: int = 10,
    allowed_domains: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "request_id": request_id or f"browser-page-text-extract:{uuid4().hex[:12]}",
        "hook_name": BROWSER_PAGE_TEXT_EXTRACT_HOOK,
        "agent_id": agent_id or DEFAULT_AGENT_ID,
        "workspace_id": _workspace_id(config_manager),
        "risk_class": "read_only",
        "approval_mode": "not_required",
        "approval_id": None,
        "target": {
            **_build_target(
                browser_family=browser_family,
                profile_key=profile_key,
                profile_mode=profile_mode,
                origin=origin,
            ),
            "tab_id": tab_id,
        },
        "arguments": {
            "max_text_characters": max_text_characters,
            "max_controls": max_controls,
        },
        "policy_context": {
            "allowed_domains": _normalize_allowed_domains(origin, allowed_domains or []),
            "sensitive_domain": False,
            "operator_required": False,
        },
    }


def render_browser_status(result: dict[str, Any]) -> str:
    browser = result.get("browser") if isinstance(result.get("browser"), dict) else {}
    profile = result.get("profile") if isinstance(result.get("profile"), dict) else {}
    extension = result.get("extension") if isinstance(result.get("extension"), dict) else {}
    native_host = result.get("native_host") if isinstance(result.get("native_host"), dict) else {}
    backend_label = str(result.get("backend_label") or LEGACY_BROWSER_BACKEND_LABEL)
    lines = [f"{backend_label} status"]
    lines.append(f"- browser family: {browser.get('family') or 'unknown'}")
    lines.append(f"- profile: {profile.get('key') or 'unknown'} ({profile.get('mode') or 'unknown'})")
    lines.append(f"- extension running: {'yes' if extension.get('running') else 'no'}")
    lines.append(f"- extension version: {extension.get('version') or 'unknown'}")
    lines.append(f"- native host: {native_host.get('connectivity') or 'unknown'}")
    lines.append(f"- native host version: {native_host.get('version') or 'unknown'}")
    if browser.get("page_scope"):
        lines.append(f"- page scope: {browser.get('page_scope')}")
    return "\n".join(lines)


def render_browser_page_snapshot(result: dict[str, Any]) -> str:
    visible_text = result.get("visible_text") if isinstance(result.get("visible_text"), dict) else {}
    forms_summary = result.get("forms_summary") if isinstance(result.get("forms_summary"), dict) else {}
    controls = result.get("important_controls") if isinstance(result.get("important_controls"), list) else []
    sensitive_surface_hints = (
        result.get("sensitive_surface_hints")
        if isinstance(result.get("sensitive_surface_hints"), dict)
        else {}
    )
    lines = ["Browser page snapshot"]
    lines.append(f"- title: {result.get('title') or 'unknown'}")
    lines.append(f"- origin: {result.get('origin') or 'unknown'}")
    if visible_text.get("summary"):
        lines.append(f"- summary: {visible_text.get('summary')}")
    lines.append(f"- forms: {int(forms_summary.get('form_count') or 0)}")
    lines.append(f"- important controls: {len(controls)}")
    lines.append(
        f"- sensitive surface: {'yes' if sensitive_surface_hints.get('likely_sensitive_domain') else 'no'}"
    )
    lines.append(f"- text redacted: {'yes' if visible_text.get('redacted') else 'no'}")
    return "\n".join(lines)


def _workspace_id(config_manager: ConfigManager) -> str:
    return str(config_manager.get_path("workspace.id", default="default"))


def _build_target(
    *,
    browser_family: str | None,
    profile_key: str | None,
    profile_mode: str | None,
    origin: str | None,
) -> dict[str, Any]:
    return {
        "backend_kind": LEGACY_BROWSER_BACKEND_KIND,
        "backend_label": LEGACY_BROWSER_BACKEND_LABEL,
        "default_surface": LEGACY_BROWSER_DEFAULT_SURFACE,
        "replacement_surface": LEGACY_BROWSER_REPLACEMENT_SURFACE,
        "browser_family": browser_family or DEFAULT_BROWSER_FAMILY,
        "profile_key": profile_key or DEFAULT_PROFILE_KEY,
        "profile_mode": profile_mode or DEFAULT_PROFILE_MODE,
        "tab_id": None,
        "window_id": None,
        "origin": origin,
    }


def _normalize_allowed_domains(origin: str, allowed_domains: list[str]) -> list[str]:
    cleaned = [str(domain).strip() for domain in allowed_domains if str(domain).strip()]
    if cleaned:
        return cleaned
    parsed = urlparse(origin)
    if parsed.hostname:
        return [parsed.hostname]
    return []


def _normalize_origin(url: str) -> str | None:
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc}"


def _browser_use_config(config: dict[str, Any]) -> dict[str, Any]:
    spark = config.get("spark") if isinstance(config.get("spark"), dict) else {}
    direct = spark.get("browser_use") if isinstance(spark.get("browser_use"), dict) else {}
    browser = spark.get("browser") if isinstance(spark.get("browser"), dict) else {}
    nested = browser.get("use") if isinstance(browser.get("use"), dict) else {}
    merged = {**nested, **direct}
    return {str(key): value for key, value in merged.items() if value not in (None, "", [], {})}


def _browser_use_status_path(config_manager: ConfigManager, config: dict[str, Any]) -> Path | None:
    explicit = os.environ.get(BROWSER_USE_STATUS_ENV) or str(config.get("status_path") or "").strip()
    candidates: list[Path] = []
    if explicit:
        candidates.append(ConfigManager.normalize_runtime_path(explicit) or Path(explicit).expanduser())
    candidates.extend(
        [
            _browser_use_default_status_path(config_manager),
            config_manager.paths.home / "state" / "browser-use" / "status.json",
            config_manager.paths.home / "state" / "spark-browser-use" / "status.json",
            config_manager.paths.home / "artifacts" / "browser-use" / "status.json",
            config_manager.paths.home / "diagnostics" / "browser-use-status.json",
        ]
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0] if explicit else None


def _browser_use_cli_path() -> str | None:
    discovered = shutil.which("browser-use") or shutil.which("browser_use")
    if discovered:
        return discovered

    executable_dir = Path(sys.executable).resolve(strict=False).parent
    candidates = [
        executable_dir / "browser-use.exe",
        executable_dir / "browser_use.exe",
        executable_dir / "browser-use",
        executable_dir / "browser_use",
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return None


def _refresh_browser_use_status_from_cli(*, status_path: Path, cli_path: str) -> dict[str, Any]:
    checked_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    try:
        completed = run_governed_command(
            command=[cli_path, "--json", "doctor"],
            cwd=status_path.parent,
            encoding="utf-8",
            errors="replace",
            timeout_seconds=BROWSER_USE_DOCTOR_TIMEOUT_SECONDS,
        )
    except (OSError, TimeoutExpired) as exc:
        status_doc = _browser_use_doctor_failure_doc(
            checked_at=checked_at,
            cli_path=cli_path,
            reason=f"browser-use doctor failed to run: {exc}",
            error_code="BROWSER_USE_DOCTOR_UNAVAILABLE",
        )
        _write_browser_use_status(status_path, status_doc)
        return status_doc

    stdout = (completed.stdout or "").strip()
    try:
        doctor = json.loads(stdout) if stdout else {}
    except json.JSONDecodeError:
        doctor = {}

    if not isinstance(doctor, dict):
        doctor = {}

    if completed.exit_code != 0 or not doctor:
        stderr = (completed.stderr or "").strip()
        reason = stderr or stdout or f"browser-use doctor exited with code {completed.exit_code}"
        status_doc = _browser_use_doctor_failure_doc(
            checked_at=checked_at,
            cli_path=cli_path,
            reason=reason,
            error_code="BROWSER_USE_DOCTOR_FAILED",
        )
        _write_browser_use_status(status_path, status_doc)
        return status_doc

    status_doc = _browser_use_status_from_doctor(doctor, checked_at=checked_at, cli_path=cli_path)
    if bool(status_doc.get("ready")):
        _attach_browser_use_smoke_proofs(status_doc, status_path=status_path, cli_path=cli_path, checked_at=checked_at)
    _write_browser_use_status(status_path, status_doc)
    return status_doc


def _browser_use_status_from_doctor(doctor: dict[str, Any], *, checked_at: str, cli_path: str) -> dict[str, Any]:
    checks = doctor.get("checks") if isinstance(doctor.get("checks"), dict) else {}
    missing_required = [
        name
        for name in sorted(_BROWSER_USE_REQUIRED_DOCTOR_CHECKS)
        if not _browser_use_doctor_check_ok(checks.get(name))
    ]
    limitations = [
        _browser_use_doctor_check_message(name, check)
        for name, check in sorted(checks.items())
        if name not in _BROWSER_USE_REQUIRED_DOCTOR_CHECKS and not _browser_use_doctor_check_ok(check)
    ]
    limitations = [limitation for limitation in limitations if limitation]
    ready = not missing_required
    failure_reason = None
    if not ready:
        failure_reason = "browser-use doctor required checks failed: " + ", ".join(missing_required)
    return {
        "status": "ready" if ready else "failed",
        "ready": ready,
        "checked_at": checked_at,
        "last_success_at": checked_at if ready else None,
        "last_failure_at": None if ready else checked_at,
        "last_failure_reason": failure_reason,
        "error_code": None if ready else "BROWSER_USE_DOCTOR_REQUIRED_CHECK_FAILED",
        "cli_path": cli_path,
        "doctor_status": _string_or_none(doctor.get("status")),
        "doctor_summary": _string_or_none(doctor.get("summary")),
        "checks": checks,
        "limitations": limitations,
        "proofs": {
            "doctor": {
                "status": "success" if ready else "failure",
                "checked_at": checked_at,
                "summary": _string_or_none(doctor.get("summary")),
            }
        },
        "provenance": {
            "source": "browser-use --json doctor",
            "cli_path": cli_path,
        },
    }


def _attach_browser_use_smoke_proofs(
    status_doc: dict[str, Any],
    *,
    status_path: Path,
    cli_path: str,
    checked_at: str,
) -> None:
    proofs = status_doc.get("proofs") if isinstance(status_doc.get("proofs"), dict) else {}
    screenshot_path = status_path.with_name(_BROWSER_USE_SMOKE_SCREENSHOT_NAME)
    proof_steps = [
        ("public_page_open", [cli_path, "--json", "open", BROWSER_USE_SMOKE_URL]),
        ("state_read", [cli_path, "--json", "state"]),
        ("screenshot_capture", [cli_path, "--json", "screenshot", str(screenshot_path)]),
    ]
    for proof_key, command in proof_steps:
        result = _run_browser_use_json(command, timeout=BROWSER_USE_SMOKE_TIMEOUT_SECONDS)
        proofs[proof_key] = _browser_use_smoke_proof_from_result(
            proof_key,
            result,
            checked_at=checked_at,
            screenshot_path=screenshot_path if proof_key == "screenshot_capture" else None,
        )
    status_doc["proofs"] = proofs

    failed_required = [
        proof_key
        for proof_key in sorted(_BROWSER_USE_REQUIRED_SMOKE_PROOFS)
        if not _browser_use_proof_succeeded(proofs.get(proof_key))
    ]
    failed_optional = [
        proof_key
        for proof_key, proof in sorted(proofs.items())
        if proof_key not in _BROWSER_USE_REQUIRED_SMOKE_PROOFS
        and proof_key != "doctor"
        and not _browser_use_proof_succeeded(proof)
    ]
    limitations = list(status_doc.get("limitations") or [])
    limitations.extend(f"{proof_key}: browser-use smoke proof failed" for proof_key in failed_optional)
    status_doc["limitations"] = [str(item) for item in limitations if str(item).strip()]

    if failed_required:
        reason = "browser-use smoke proofs failed: " + ", ".join(failed_required)
        status_doc["status"] = "failed"
        status_doc["ready"] = False
        status_doc["last_success_at"] = None
        status_doc["last_failure_at"] = checked_at
        status_doc["last_failure_reason"] = reason
        status_doc["error_code"] = "BROWSER_USE_SMOKE_REQUIRED_PROOF_FAILED"
    else:
        status_doc["smoke_url"] = BROWSER_USE_SMOKE_URL
        status_doc["last_success_at"] = checked_at


def _run_browser_use_json(command: list[str], *, timeout: int) -> dict[str, Any]:
    try:
        completed = run_governed_command(
            command=command,
            cwd=Path.cwd(),
            encoding="utf-8",
            errors="replace",
            timeout_seconds=timeout,
        )
    except (OSError, TimeoutExpired) as exc:
        return {"success": False, "error": str(exc)}

    stdout = (completed.stdout or "").strip()
    try:
        payload = json.loads(stdout) if stdout else {}
    except json.JSONDecodeError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    if completed.exit_code != 0:
        payload["success"] = False
        payload["error"] = (completed.stderr or "").strip() or stdout or f"browser-use exited with code {completed.exit_code}"
    return payload


def _browser_use_smoke_proof_from_result(
    proof_key: str,
    result: dict[str, Any],
    *,
    checked_at: str,
    screenshot_path: Path | None,
) -> dict[str, Any]:
    succeeded = bool(result.get("success") is True)
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    proof: dict[str, Any] = {
        "status": "success" if succeeded else "failure",
        "checked_at": checked_at,
        "request_id": _string_or_none(result.get("id")),
    }
    if proof_key == "public_page_open":
        proof["url"] = _string_or_none(data.get("url")) or BROWSER_USE_SMOKE_URL
    elif proof_key == "state_read":
        raw_text = _string_or_none(data.get("_raw_text")) or ""
        proof["contains_example_domain"] = "Example Domain" in raw_text
        proof["text_excerpt"] = raw_text[:240]
        succeeded = succeeded and bool(proof["contains_example_domain"])
        proof["status"] = "success" if succeeded else "failure"
    elif proof_key == "screenshot_capture" and screenshot_path is not None:
        saved_path = Path(str(data.get("saved") or screenshot_path)).expanduser()
        proof["path"] = str(saved_path)
        proof["bytes"] = saved_path.stat().st_size if saved_path.exists() else 0
        succeeded = succeeded and int(proof["bytes"] or 0) > 0
        proof["status"] = "success" if succeeded else "failure"
    if proof["status"] != "success":
        proof["failure_reason"] = _string_or_none(result.get("error")) or "browser-use smoke proof failed"
    return proof


def _browser_use_proof_succeeded(proof: object) -> bool:
    return isinstance(proof, dict) and str(proof.get("status") or "") == "success"


def _browser_use_doctor_check_ok(check: object) -> bool:
    if not isinstance(check, dict):
        return False
    return str(check.get("status") or "").strip().lower() in {"ok", "ready", "success", "passed"}


def _browser_use_doctor_check_message(name: str, check: object) -> str | None:
    if not isinstance(check, dict):
        return f"{name}: missing check payload"
    message = _string_or_none(check.get("message")) or _string_or_none(check.get("fix"))
    if not message:
        return None
    return f"{name}: {message}"


def _browser_use_doctor_failure_doc(*, checked_at: str, cli_path: str, reason: str, error_code: str) -> dict[str, Any]:
    return {
        "status": "failed",
        "ready": False,
        "checked_at": checked_at,
        "last_success_at": None,
        "last_failure_at": checked_at,
        "last_failure_reason": reason,
        "error_code": error_code,
        "cli_path": cli_path,
        "provenance": {
            "source": "browser-use --json doctor",
            "cli_path": cli_path,
        },
    }


def _write_browser_use_status(path: Path, status_doc: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(status_doc, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except OSError:
        return


def _browser_use_default_status_path(config_manager: ConfigManager) -> Path:
    return _spark_runtime_root(config_manager.paths.home) / "state" / "browser-use" / "status.json"


def _spark_runtime_root(home: Path) -> Path:
    resolved = home.resolve(strict=False)
    if resolved.name == "spark-intelligence" and resolved.parent.name == "state":
        return resolved.parent.parent
    return resolved


def _read_browser_use_status(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {"status": "error", "last_failure_reason": f"could not read browser-use status file: {path}"}
    if isinstance(data, dict):
        return data
    return {"status": "error", "last_failure_reason": "browser-use status file is not a JSON object"}


def _browser_use_raw_status(status_doc: dict[str, Any]) -> str:
    for key in ("status", "state", "health"):
        value = str(status_doc.get(key) or "").strip().lower()
        if value:
            return value
    return ""


def _browser_use_failure_reason(
    status_doc: dict[str, Any],
    *,
    status: str,
    ready_signal: bool = False,
    proof_complete: bool = True,
    proof_fresh: bool = True,
    screenshot_ok: bool = True,
) -> str | None:
    for key in ("last_failure_reason", "failure_reason", "error_message"):
        value = _string_or_none(status_doc.get(key))
        if value:
            return value
    error = status_doc.get("error")
    if isinstance(error, dict):
        return _string_or_none(error.get("message")) or _string_or_none(error.get("code"))
    if isinstance(error, str) and error.strip():
        return error.strip()
    if ready_signal and not proof_complete:
        missing = sorted(_BROWSER_USE_REQUIRED_PROOFS.difference(_browser_use_proofs(status_doc)))
        return "browser-use status is ready, but proof receipt is incomplete: missing " + ", ".join(missing)
    if ready_signal and not proof_fresh:
        return "browser-use proof receipt is stale; rerun the browser route probe."
    if ready_signal and not screenshot_ok:
        return "browser-use screenshot proof artifact is missing."
    if status == "configured":
        return "browser-use adapter configured, but no passing status proof has been recorded."
    return None


def _browser_use_proofs(status_doc: dict[str, Any]) -> list[str]:
    proofs = status_doc.get("proofs")
    if isinstance(proofs, list):
        return sorted({str(item).strip() for item in proofs if str(item).strip()})
    if isinstance(proofs, dict):
        return sorted(
            str(name).strip()
            for name, proof in proofs.items()
            if str(name).strip() and _browser_use_proof_succeeded(proof)
        )
    steps = status_doc.get("steps")
    if isinstance(steps, dict):
        return sorted(
            str(name).strip()
            for name, step in steps.items()
            if str(name).strip() and (step is True or (isinstance(step, dict) and step.get("status") in {"ok", "success", "passed"}))
        )
    return []


def _browser_use_proof_is_fresh(status_doc: dict[str, Any]) -> bool:
    timestamp = _string_or_none(status_doc.get("last_success_at") or status_doc.get("recorded_at") or status_doc.get("checked_at"))
    if not timestamp:
        return False
    try:
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return False
    return (datetime.now(UTC) - parsed.astimezone(UTC)).total_seconds() <= _BROWSER_USE_PROOF_TTL_SECONDS


def _browser_use_screenshot_ok(status_doc: dict[str, Any]) -> bool:
    screenshot = _string_or_none(status_doc.get("screenshot_path") or status_doc.get("screenshot"))
    proofs = status_doc.get("proofs") if isinstance(status_doc.get("proofs"), dict) else {}
    screenshot_proof = proofs.get("screenshot_capture") if isinstance(proofs, dict) else {}
    if not screenshot and isinstance(screenshot_proof, dict):
        screenshot = _string_or_none(screenshot_proof.get("path") or screenshot_proof.get("screenshot_path"))
    if not screenshot:
        return "screenshot_capture" not in set(_browser_use_proofs(status_doc))
    path = ConfigManager.normalize_runtime_path(screenshot) or Path(screenshot).expanduser()
    return path.exists()


def _browser_use_summary(
    *,
    status: str,
    raw_status: str,
    package_available: bool,
    cli_path: str | None,
    status_path: Path | None,
    proofs: object,
    failure_reason: str | None,
) -> str:
    parts = [
        f"browser-use adapter status={raw_status or status}",
        f"package_available={package_available}",
        f"cli_available={bool(cli_path)}",
    ]
    if status_path:
        parts.append(f"status_path={status_path}")
        parts.append(f"exists={status_path.exists()}")
    successful_proofs: list[str] = []
    if isinstance(proofs, dict):
        successful_proofs = [
            proof_key
            for proof_key, proof in sorted(proofs.items())
            if isinstance(proof, dict) and str(proof.get("status") or "") == "success"
        ]
    elif isinstance(proofs, list):
        successful_proofs = sorted({str(proof).strip() for proof in proofs if str(proof).strip()})
    if successful_proofs:
        parts.append(f"proofs={','.join(successful_proofs)}")
    if failure_reason and status != "completed":
        parts.append(f"reason={failure_reason}")
    return " ".join(parts)


def _string_or_none(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None
