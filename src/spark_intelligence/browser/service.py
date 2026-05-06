from __future__ import annotations

import importlib.util
import json
import os
import shutil
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

from spark_intelligence.config.loader import ConfigManager

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
    cli_path = shutil.which("browser-use") or shutil.which("browser_use")
    configured = bool(browser_use_config) or status_path is not None or package_available or bool(cli_path)
    if not configured and not status_doc:
        return None

    raw_status = _browser_use_raw_status(status_doc)
    ready = raw_status in _BROWSER_USE_READY_STATUSES or bool(status_doc.get("ready") is True)
    failed = raw_status in _BROWSER_USE_FAILURE_STATUSES or bool(status_doc.get("ready") is False)
    status = "completed" if ready else "failed" if failed else "configured"
    failure_reason = _browser_use_failure_reason(status_doc, status=status)
    summary = _browser_use_summary(
        status=status,
        raw_status=raw_status,
        package_available=package_available,
        cli_path=cli_path,
        status_path=status_path,
        failure_reason=failure_reason,
    )
    return {
        "status": status,
        "backend_kind": BROWSER_USE_BACKEND_KIND,
        "backend_label": BROWSER_USE_BACKEND_LABEL,
        "adapter_status": raw_status or status,
        "configured": bool(configured),
        "package_available": bool(package_available),
        "cli_path": cli_path,
        "status_path": str(status_path) if status_path else None,
        "last_success_at": _string_or_none(status_doc.get("last_success_at")),
        "last_failure_at": _string_or_none(status_doc.get("last_failure_at")),
        "last_failure_reason": failure_reason,
        "error_code": _string_or_none(status_doc.get("error_code")),
        "error_message": failure_reason,
        "evidence_summary": summary,
        "provenance": {
            "source": "browser_use_adapter_status",
            "status_path": str(status_path) if status_path else None,
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


def _browser_use_failure_reason(status_doc: dict[str, Any], *, status: str) -> str | None:
    for key in ("last_failure_reason", "failure_reason", "error_message"):
        value = _string_or_none(status_doc.get(key))
        if value:
            return value
    error = status_doc.get("error")
    if isinstance(error, dict):
        return _string_or_none(error.get("message")) or _string_or_none(error.get("code"))
    if isinstance(error, str) and error.strip():
        return error.strip()
    if status == "configured":
        return "browser-use adapter configured, but no passing status proof has been recorded."
    return None


def _browser_use_summary(
    *,
    status: str,
    raw_status: str,
    package_available: bool,
    cli_path: str | None,
    status_path: Path | None,
    failure_reason: str | None,
) -> str:
    parts = [
        f"browser-use adapter status={raw_status or status}",
        f"package_available={package_available}",
        f"cli_available={bool(cli_path)}",
    ]
    if status_path:
        parts.append(f"status_path={status_path}")
    if failure_reason and status != "completed":
        parts.append(f"reason={failure_reason}")
    return " ".join(parts)


def _string_or_none(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None
