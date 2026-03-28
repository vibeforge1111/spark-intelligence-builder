from __future__ import annotations

from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

from spark_intelligence.config.loader import ConfigManager

BROWSER_STATUS_HOOK = "browser.status"
BROWSER_PAGE_SNAPSHOT_HOOK = "browser.page.snapshot"
SCHEMA_VERSION = "spark-browser-hook.v1"
DEFAULT_BROWSER_FAMILY = "brave"
DEFAULT_PROFILE_KEY = "spark-default"
DEFAULT_PROFILE_MODE = "dedicated"
DEFAULT_AGENT_ID = "agent:local-operator"


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


def build_browser_page_snapshot_payload(
    *,
    config_manager: ConfigManager,
    origin: str,
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
        "target": _build_target(
            browser_family=browser_family,
            profile_key=profile_key,
            profile_mode=profile_mode,
            origin=origin,
        ),
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


def render_browser_status(result: dict[str, Any]) -> str:
    browser = result.get("browser") if isinstance(result.get("browser"), dict) else {}
    profile = result.get("profile") if isinstance(result.get("profile"), dict) else {}
    extension = result.get("extension") if isinstance(result.get("extension"), dict) else {}
    native_host = result.get("native_host") if isinstance(result.get("native_host"), dict) else {}
    lines = ["Browser status"]
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
