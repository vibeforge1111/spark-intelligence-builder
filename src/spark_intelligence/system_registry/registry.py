from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from spark_intelligence.attachments import (
    attachment_status,
    build_attachment_context,
    run_first_active_chip_hook,
)
from spark_intelligence.browser.service import build_browser_status_payload
from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.state.db import StateDB


_SYSTEM_ROLE_HINTS: dict[str, str] = {
    "spark_intelligence_builder": "Runtime owner for delivery, operator controls, attachment state, persona state, routing, and final user-visible replies.",
    "spark_researcher": "Primary intelligence core behind provider-backed advisory and normal reasoning.",
    "spark_swarm": "Deep-work escalation and collective coordination surface for parallel or multi-node work.",
    "spark_browser": "Governed browser/search surface for page inspection, source capture, and browse workflows.",
    "spark_voice": "Speech I/O surface for transcription and synthesis around the Builder-owned personality.",
    "spark_memory": "Persistent local memory and observability state used across sessions and runtime surfaces.",
    "spark_spawner": "Mission, schedule, Kanban, and workflow execution surface for operator-governed work.",
    "spark_local_work": "Operator-governed local project/file work through Codex and Spawner workflows.",
}

_SYSTEM_CAPABILITY_HINTS: dict[str, list[str]] = {
    "spark_intelligence_builder": ["delivery", "operator_controls", "attachments", "persona_state", "routing"],
    "spark_researcher": ["provider_advisory", "reasoning", "conversation_support"],
    "spark_swarm": ["escalation", "collective_coordination", "autoloops"],
    "spark_browser": ["web_search", "page_inspection", "source_capture"],
    "spark_voice": ["speech_to_text", "text_to_speech"],
    "spark_memory": ["runtime_memory", "observability", "state_lookup"],
    "spark_spawner": ["mission_control", "kanban_board", "schedules", "workflow_execution"],
    "spark_local_work": ["local_file_inspection", "repo_inspection", "codex_supervised_work", "tests_and_commits"],
}

_KNOWN_CHIP_ROLE_HINTS: dict[str, str] = {
    "startup-yc": "Founder/operator doctrine chip for decisive startup guidance when active.",
    "spark-browser": "Governed browser and search chip for page inspection, browse flows, and source capture.",
    "spark-personality-chip-labs": "Baseline personality import chip; it seeds Builder, but Builder owns live style state after import.",
    "spark-swarm": "Swarm bridge and identity/escalation chip for collective or deep-work execution.",
    "domain-chip-voice-comms": "Speech I/O chip for STT and TTS around the Builder-owned personality.",
}
_BROWSER_STATUS_HOOK = "browser.status"


@dataclass(frozen=True)
class SystemRegistryRecord:
    record_id: str
    kind: str
    key: str
    label: str
    role: str
    status: str
    attached: bool
    active: bool
    pinned: bool
    available: bool
    degraded: bool
    requires_restart: bool
    capabilities: list[str]
    limitations: list[str]
    metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "record_id": self.record_id,
            "kind": self.kind,
            "key": self.key,
            "label": self.label,
            "role": self.role,
            "status": self.status,
            "attached": self.attached,
            "active": self.active,
            "pinned": self.pinned,
            "available": self.available,
            "degraded": self.degraded,
            "requires_restart": self.requires_restart,
            "capabilities": self.capabilities,
            "limitations": self.limitations,
            "metadata": self.metadata,
        }


@dataclass(frozen=True)
class SystemRegistrySnapshot:
    generated_at: str
    workspace_id: str
    records: list[SystemRegistryRecord]

    def to_payload(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "workspace_id": self.workspace_id,
            "record_count": len(self.records),
            "records": [record.to_dict() for record in self.records],
            "summary": {
                "system_count": len([record for record in self.records if record.kind == "system"]),
                "adapter_count": len([record for record in self.records if record.kind == "adapter"]),
                "provider_count": len([record for record in self.records if record.kind == "provider"]),
                "chip_count": len([record for record in self.records if record.kind == "chip"]),
                "path_count": len([record for record in self.records if record.kind == "path"]),
                "onboarding_contract_count": len(
                    [
                        record
                        for record in self.records
                        if record.kind in {"chip", "path"} and isinstance(record.metadata.get("onboarding"), dict)
                    ]
                ),
                "current_capabilities": _derive_current_capabilities(self.records),
            },
        }

    def to_json(self) -> str:
        return json.dumps(self.to_payload(), indent=2)


def looks_like_system_registry_query(message: str) -> bool:
    lowered_message = str(message or "").strip().lower()
    if not lowered_message:
        return False
    direct_signals = (
        "spark swarm",
        "spark researcher",
        "spark intelligence builder",
        "what chips",
        "which chips",
        "active chips",
        "attached chips",
        "connected chips",
        "what systems",
        "which systems",
        "what adapters",
        "what tools",
        "what backends",
        "what providers",
        "what models",
        "what llms",
        "what terminals",
        "what can you do",
        "what can spark do",
        "what are you connected to",
        "what is connected",
        "around you right now",
        "know about yourself",
        "self-awareness",
        "surroundings",
    )
    return any(signal in lowered_message for signal in direct_signals)


def build_system_registry(config_manager: ConfigManager, state_db: StateDB, *, probe_browser: bool = True) -> SystemRegistrySnapshot:
    from spark_intelligence.auth.runtime import build_auth_status_report
    from spark_intelligence.gateway.runtime import gateway_status
    from spark_intelligence.researcher_bridge import researcher_bridge_status
    from spark_intelligence.swarm_bridge import swarm_status

    attachment_context = build_attachment_context(config_manager)
    attachment_scan = attachment_status(config_manager)
    gateway = gateway_status(config_manager, state_db)
    researcher = researcher_bridge_status(config_manager=config_manager, state_db=state_db)
    swarm = swarm_status(config_manager, state_db)
    auth_report = build_auth_status_report(config_manager=config_manager, state_db=state_db)
    browser = _collect_browser_registry_payload(config_manager) if probe_browser else None
    config = config_manager.load()
    active_chip_keys = set(str(item) for item in (attachment_context.get("active_chip_keys") or []) if str(item))
    pinned_chip_keys = set(str(item) for item in (attachment_context.get("pinned_chip_keys") or []) if str(item))
    active_path_key = str(attachment_context.get("active_path_key") or "").strip() or None
    channel_records = config.get("channels", {}).get("records", {}) or {}
    workspace_id = str(config_manager.get_path("workspace.id", default="default"))

    records: list[SystemRegistryRecord] = []
    records.extend(
        [
            _build_builder_record(gateway=gateway),
            _build_researcher_record(researcher=researcher),
            _build_swarm_record(swarm=swarm, attachment_context=attachment_context),
            _build_browser_record(
                active_chip_keys=active_chip_keys,
                attachment_context=attachment_context,
                browser_payload=browser,
            ),
            _build_chip_backed_system_record(
                system_key="spark_voice",
                chip_key="domain-chip-voice-comms",
                active_chip_keys=active_chip_keys,
                attachment_context=attachment_context,
            ),
            _build_memory_record(),
            _build_spawner_record(config_manager=config_manager),
            _build_local_work_record(config_manager=config_manager),
        ]
    )
    records.extend(_build_adapter_records(channel_records=channel_records))
    records.extend(_build_provider_records(auth_report=auth_report))
    records.extend(
        _build_chip_records(
            attachment_context=attachment_context,
            active_chip_keys=active_chip_keys,
            pinned_chip_keys=pinned_chip_keys,
        )
    )
    records.extend(
        _build_path_records(
            attachment_context=attachment_context,
            active_path_key=active_path_key,
        )
    )
    records.sort(key=lambda item: (item.kind, item.key))
    if attachment_scan.warnings:
        records.append(
            SystemRegistryRecord(
                record_id="registry:health:attachments",
                kind="health",
                key="attachment_inventory",
                label="Attachment inventory",
                role="Attachment discovery and registry hydration state.",
                status="degraded",
                attached=True,
                active=True,
                pinned=False,
                available=True,
                degraded=True,
                requires_restart=False,
                capabilities=["attachment_discovery"],
                limitations=list(attachment_scan.warnings[:8]),
                metadata={"warning_count": len(attachment_scan.warnings)},
            )
        )
    return SystemRegistrySnapshot(
        generated_at=_now_iso(),
        workspace_id=workspace_id,
        records=records,
    )


def build_system_registry_prompt_context(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    user_message: str,
) -> str:
    if not looks_like_system_registry_query(user_message):
        return ""
    snapshot = build_system_registry(config_manager, state_db)
    payload = snapshot.to_payload()
    lines = ["[Spark system registry]"]
    for record in payload["records"]:
        if not isinstance(record, dict) or str(record.get("kind") or "") != "system":
            continue
        capabilities = [str(item) for item in (record.get("capabilities") or []) if str(item)]
        line = f"- {record['label']}: status={record['status']} role={record['role']}"
        if capabilities:
            line += f" caps={','.join(capabilities[:6])}"
        lines.append(line)
    adapter_lines = _prompt_section_lines(payload["records"], kind="adapter", title="[Messaging adapters]")
    if adapter_lines:
        lines.extend(adapter_lines)
    provider_lines = _prompt_section_lines(payload["records"], kind="provider", title="[Provider backends]")
    if provider_lines:
        lines.extend(provider_lines)
    onboarding_lines = _prompt_onboarding_lines(payload["records"])
    if onboarding_lines:
        lines.extend(onboarding_lines)
    capability_lines = payload.get("summary", {}).get("current_capabilities") or []
    if capability_lines:
        lines.append("[Current capabilities]")
        lines.extend(f"- {str(item)}" for item in capability_lines[:10])
    lines.extend(
        [
            "[Reply rule]",
            "When the user asks what Spark is, what it is connected to, what it can do, which chips are active, or which system should handle work, answer from this system registry and the attached inventory. Do not claim missing verified knowledge when the runtime registry already answers the question.",
        ]
    )
    return "\n".join(lines)


def build_system_registry_direct_reply(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    user_message: str,
) -> str:
    if not looks_like_system_registry_query(user_message):
        return ""
    payload = build_system_registry(config_manager, state_db).to_payload()
    records = {
        str(record.get("key") or ""): record
        for record in (payload.get("records") or [])
        if isinstance(record, dict) and str(record.get("kind") or "") == "system"
    }
    adapter_labels = [
        str(record.get("label") or "").strip()
        for record in (payload.get("records") or [])
        if isinstance(record, dict)
        and str(record.get("kind") or "") == "adapter"
        and bool(record.get("active"))
        and str(record.get("label") or "").strip()
    ]
    provider_labels = [
        str(record.get("label") or "").strip()
        for record in (payload.get("records") or [])
        if isinstance(record, dict)
        and str(record.get("kind") or "") == "provider"
        and bool(record.get("active"))
        and str(record.get("label") or "").strip()
    ]
    active_chips = [
        str(record.get("key") or "").strip()
        for record in (payload.get("records") or [])
        if isinstance(record, dict)
        and str(record.get("kind") or "") == "chip"
        and bool(record.get("active"))
        and str(record.get("key") or "").strip()
    ]
    active_paths = [
        str(record.get("key") or "").strip()
        for record in (payload.get("records") or [])
        if isinstance(record, dict)
        and str(record.get("kind") or "") == "path"
        and bool(record.get("active"))
        and str(record.get("key") or "").strip()
    ]

    lines = ["Here's what is connected right now:"]
    for system_key in (
        "spark_intelligence_builder",
        "spark_researcher",
        "spark_swarm",
        "spark_browser",
        "spark_voice",
        "spark_memory",
        "spark_spawner",
        "spark_local_work",
    ):
        record = records.get(system_key)
        if not isinstance(record, dict):
            continue
        lines.append(_format_system_registry_direct_reply_line(record))
    if adapter_labels:
        lines.append(f"Channels: {', '.join(adapter_labels)}.")
    if provider_labels:
        lines.append(f"Providers: {', '.join(provider_labels)}.")
    if active_chips:
        lines.append(f"Active chips: {', '.join(active_chips[:10])}.")
    if active_paths:
        lines.append(f"Active paths: {', '.join(active_paths[:5])}.")
    return "\n".join(lines)


def _build_builder_record(*, gateway: Any) -> SystemRegistryRecord:
    status = "ready" if bool(getattr(gateway, "ready", False)) else "degraded"
    return SystemRegistryRecord(
        record_id="system:spark_intelligence_builder",
        kind="system",
        key="spark_intelligence_builder",
        label="Spark Intelligence Builder",
        role=_SYSTEM_ROLE_HINTS["spark_intelligence_builder"],
        status=status,
        attached=True,
        active=True,
        pinned=False,
        available=True,
        degraded=status == "degraded",
        requires_restart=False,
        capabilities=_SYSTEM_CAPABILITY_HINTS["spark_intelligence_builder"],
        limitations=[] if status == "ready" else ["Gateway/provider/channel readiness is not fully green yet."],
        metadata={
            "configured_channels": list(getattr(gateway, "configured_channels", []) or []),
            "configured_providers": list(getattr(gateway, "configured_providers", []) or []),
        },
    )


def _build_researcher_record(*, researcher: Any) -> SystemRegistryRecord:
    if bool(getattr(researcher, "available", False)):
        status = "ready"
    elif bool(getattr(researcher, "configured", False)):
        status = "configured"
    elif bool(getattr(researcher, "enabled", False)):
        status = "degraded"
    else:
        status = "missing"
    return SystemRegistryRecord(
        record_id="system:spark_researcher",
        kind="system",
        key="spark_researcher",
        label="Spark Researcher",
        role=_SYSTEM_ROLE_HINTS["spark_researcher"],
        status=status,
        attached=True,
        active=bool(getattr(researcher, "available", False)),
        pinned=False,
        available=status in {"ready", "configured", "degraded"},
        degraded=status == "degraded",
        requires_restart=False,
        capabilities=_SYSTEM_CAPABILITY_HINTS["spark_researcher"],
        limitations=[] if status == "ready" else ["Researcher runtime or config is not fully available."],
        metadata={
            "mode": getattr(researcher, "mode", None),
            "runtime_root": getattr(researcher, "runtime_root", None),
            "config_path": getattr(researcher, "config_path", None),
        },
    )


def _build_swarm_record(*, swarm: Any, attachment_context: dict[str, Any]) -> SystemRegistryRecord:
    attached_chip_keys = set(str(item) for item in (attachment_context.get("attached_chip_keys") or []) if str(item))
    if bool(getattr(swarm, "api_ready", False)):
        status = "ready"
    elif bool(getattr(swarm, "payload_ready", False)):
        status = "configured"
    elif bool(getattr(swarm, "configured", False)) or "spark-swarm" in attached_chip_keys:
        status = "available"
    else:
        status = "missing"
    return SystemRegistryRecord(
        record_id="system:spark_swarm",
        kind="system",
        key="spark_swarm",
        label="Spark Swarm",
        role=_SYSTEM_ROLE_HINTS["spark_swarm"],
        status=status,
        attached="spark-swarm" in attached_chip_keys,
        active=bool(getattr(swarm, "payload_ready", False) or getattr(swarm, "api_ready", False)),
        pinned=False,
        available=status in {"ready", "configured", "available"},
        degraded=status == "degraded",
        requires_restart=False,
        capabilities=_SYSTEM_CAPABILITY_HINTS["spark_swarm"],
        limitations=[] if status in {"ready", "configured"} else ["Swarm API or payload path is not fully active."],
        metadata={
            "api_ready": bool(getattr(swarm, "api_ready", False)),
            "payload_ready": bool(getattr(swarm, "payload_ready", False)),
            "auth_state": getattr(swarm, "auth_state", None),
            "api_url": getattr(swarm, "api_url", None),
        },
    )


def _build_chip_backed_system_record(
    *,
    system_key: str,
    chip_key: str,
    active_chip_keys: set[str],
    attachment_context: dict[str, Any],
) -> SystemRegistryRecord:
    attached_chip_keys = set(str(item) for item in (attachment_context.get("attached_chip_keys") or []) if str(item))
    attached = chip_key in attached_chip_keys
    active = chip_key in active_chip_keys
    status = "ready" if active else "available" if attached else "missing"
    limitations = [] if attached else [f"Chip '{chip_key}' is not attached in this workspace."]
    return SystemRegistryRecord(
        record_id=f"system:{system_key}",
        kind="system",
        key=system_key,
        label={
            "spark_browser": "Spark Browser",
            "spark_voice": "Spark Voice",
        }[system_key],
        role=_SYSTEM_ROLE_HINTS[system_key],
        status=status,
        attached=attached,
        active=active,
        pinned=False,
        available=attached,
        degraded=False,
        requires_restart=False,
        capabilities=_SYSTEM_CAPABILITY_HINTS[system_key],
        limitations=limitations,
        metadata={"chip_key": chip_key},
    )


def _build_browser_record(
    *,
    active_chip_keys: set[str],
    attachment_context: dict[str, Any],
    browser_payload: dict[str, Any] | None,
) -> SystemRegistryRecord:
    attached_chip_keys = set(str(item) for item in (attachment_context.get("attached_chip_keys") or []) if str(item))
    attached = "spark-browser" in attached_chip_keys
    if isinstance(browser_payload, dict) and browser_payload:
        error_code = str(browser_payload.get("error_code") or "").strip()
        error_message = str(browser_payload.get("error_message") or "").strip()
        if str(browser_payload.get("status") or "") == "completed":
            status = "ready"
            active = True
            available = True
            degraded = False
            limitations: list[str] = []
        elif error_code == "BROWSER_SESSION_STALE":
            status = "standby"
            active = False
            available = attached
            degraded = False
            limitations = [
                error_message or "Live browser session is not currently connected.",
                "Reconnect the Spark Browser extension session to restore live search and page inspection.",
            ]
        else:
            status = "degraded"
            active = False
            available = attached
            degraded = True
            limitations = [error_message or "Browser hook failed or is unavailable."]
        return SystemRegistryRecord(
            record_id="system:spark_browser",
            kind="system",
            key="spark_browser",
            label="Spark Browser",
            role=_SYSTEM_ROLE_HINTS["spark_browser"],
            status=status,
            attached=attached,
            active=active,
            pinned=False,
            available=available,
            degraded=degraded,
            requires_restart=False,
            capabilities=_SYSTEM_CAPABILITY_HINTS["spark_browser"],
            limitations=limitations,
            metadata=browser_payload,
        )
    return _build_chip_backed_system_record(
        system_key="spark_browser",
        chip_key="spark-browser",
        active_chip_keys=active_chip_keys,
        attachment_context=attachment_context,
    )


def _build_memory_record() -> SystemRegistryRecord:
    return SystemRegistryRecord(
        record_id="system:spark_memory",
        kind="system",
        key="spark_memory",
        label="Spark Memory",
        role=_SYSTEM_ROLE_HINTS["spark_memory"],
        status="ready",
        attached=True,
        active=True,
        pinned=False,
        available=True,
        degraded=False,
        requires_restart=False,
        capabilities=_SYSTEM_CAPABILITY_HINTS["spark_memory"],
        limitations=[],
        metadata={},
    )


def _build_spawner_record(*, config_manager: ConfigManager) -> SystemRegistryRecord:
    api_url = str(config_manager.get_path("spark.spawner.api_url", default="http://127.0.0.1:5173") or "").strip()
    return SystemRegistryRecord(
        record_id="system:spark_spawner",
        kind="system",
        key="spark_spawner",
        label="Spark Spawner",
        role=_SYSTEM_ROLE_HINTS["spark_spawner"],
        status="configured" if api_url else "available",
        attached=True,
        active=bool(api_url),
        pinned=False,
        available=True,
        degraded=False,
        requires_restart=False,
        capabilities=_SYSTEM_CAPABILITY_HINTS["spark_spawner"],
        limitations=[
            "Health is verified by diagnostics/status probes; this registry row records the configured workflow surface.",
        ],
        metadata={"api_url": api_url or None},
    )


def _build_local_work_record(*, config_manager: ConfigManager) -> SystemRegistryRecord:
    workspace_home = str(config_manager.get_path("workspace.home", default=config_manager.paths.home) or "").strip()
    return SystemRegistryRecord(
        record_id="system:spark_local_work",
        kind="system",
        key="spark_local_work",
        label="Spark Local Work",
        role=_SYSTEM_ROLE_HINTS["spark_local_work"],
        status="available",
        attached=True,
        active=True,
        pinned=False,
        available=True,
        degraded=False,
        requires_restart=False,
        capabilities=_SYSTEM_CAPABILITY_HINTS["spark_local_work"],
        limitations=[
            "Use operator-governed Codex/Spawner workflows for local repo/file inspection; do not imply raw Telegram filesystem access.",
            "Confirm the target repo/component before file-writing or build-quality claims.",
        ],
        metadata={"workspace_home": workspace_home or None},
    )


def _build_adapter_records(*, channel_records: dict[str, Any]) -> list[SystemRegistryRecord]:
    records: list[SystemRegistryRecord] = []
    for channel_id in sorted(channel_records):
        record = channel_records.get(channel_id) or {}
        status = str(record.get("status") or "enabled")
        enabled = status in {"enabled", "configured"}
        records.append(
            SystemRegistryRecord(
                record_id=f"adapter:{channel_id}",
                kind="adapter",
                key=str(channel_id),
                label=f"{str(channel_id).capitalize()} adapter",
                role=f"Messaging adapter for {channel_id}.",
                status="ready" if enabled else status,
                attached=True,
                active=enabled,
                pinned=False,
                available=True,
                degraded=not enabled,
                requires_restart=False,
                capabilities=["message_ingress", "message_delivery"],
                limitations=[] if enabled else [f"{channel_id} is not currently enabled."],
                metadata={
                    "auth_ref": record.get("auth_ref"),
                    "paired_human_id": record.get("paired_human_id"),
                },
            )
        )
    return records


def _build_provider_records(*, auth_report: Any) -> list[SystemRegistryRecord]:
    records: list[SystemRegistryRecord] = []
    for provider in list(getattr(auth_report, "providers", []) or []):
        secret_present = bool(getattr(provider, "secret_present", False))
        token_expired = bool(getattr(provider, "token_expired", False))
        status = "ready" if secret_present and not token_expired else "degraded"
        limitations: list[str] = []
        if not secret_present:
            limitations.append("Provider secret or token is not currently available.")
        if token_expired:
            limitations.append("OAuth access token is expired.")
        records.append(
            SystemRegistryRecord(
                record_id=f"provider:{provider.provider_id}",
                kind="provider",
                key=str(provider.provider_id),
                label=str(provider.provider_id),
                role=f"{provider.provider_kind} provider backend.",
                status=status,
                attached=True,
                active=bool(getattr(provider, "is_default_provider", False)),
                pinned=False,
                available=secret_present,
                degraded=status == "degraded",
                requires_restart=False,
                capabilities=["llm_inference"],
                limitations=limitations,
                metadata={
                    "provider_kind": provider.provider_kind,
                    "auth_method": provider.auth_method,
                    "default_model": provider.default_model,
                    "base_url": provider.base_url,
                    "is_default_provider": provider.is_default_provider,
                    "is_default_profile": provider.is_default_profile,
                },
            )
        )
    return records


def _build_chip_records(
    *,
    attachment_context: dict[str, Any],
    active_chip_keys: set[str],
    pinned_chip_keys: set[str],
) -> list[SystemRegistryRecord]:
    records: list[SystemRegistryRecord] = []
    for item in list(attachment_context.get("attached_chip_records") or []):
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        if not key:
            continue
        active = key in active_chip_keys
        pinned = key in pinned_chip_keys
        mode = str(item.get("attachment_mode") or "available")
        status = "pinned" if pinned else "active" if active else mode
        onboarding = item.get("onboarding") if isinstance(item.get("onboarding"), dict) else None
        metadata = {
            "hook_names": [str(hook) for hook in (item.get("hook_names") or []) if str(hook)],
            "repo_root": item.get("repo_root"),
            "attachment_mode": mode,
        }
        if onboarding is not None:
            metadata["onboarding"] = onboarding
        records.append(
            SystemRegistryRecord(
                record_id=f"chip:{key}",
                kind="chip",
                key=key,
                label=str(item.get("label") or key),
                role=(
                    str(onboarding.get("role") or "").strip()
                    if onboarding is not None and str(onboarding.get("role") or "").strip()
                    else str(item.get("description") or "").strip() or _KNOWN_CHIP_ROLE_HINTS.get(key, "Attached chip.")
                ),
                status=status,
                attached=True,
                active=active,
                pinned=pinned,
                available=True,
                degraded=False,
                requires_restart=False,
                capabilities=[str(cap) for cap in (item.get("capabilities") or []) if str(cap)],
                limitations=[str(entry) for entry in ((onboarding or {}).get("limitations") or []) if str(entry)],
                metadata=metadata,
            )
        )
    return records


def _build_path_records(
    *,
    attachment_context: dict[str, Any],
    active_path_key: str | None,
) -> list[SystemRegistryRecord]:
    records: list[SystemRegistryRecord] = []
    for item in list(attachment_context.get("attached_path_records") or []):
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        if not key:
            continue
        active = key == active_path_key
        mode = str(item.get("attachment_mode") or "available")
        onboarding = item.get("onboarding") if isinstance(item.get("onboarding"), dict) else None
        metadata = {
            "hook_names": [str(hook) for hook in (item.get("hook_names") or []) if str(hook)],
            "repo_root": item.get("repo_root"),
            "attachment_mode": mode,
        }
        if onboarding is not None:
            metadata["onboarding"] = onboarding
        records.append(
            SystemRegistryRecord(
                record_id=f"path:{key}",
                kind="path",
                key=key,
                label=str(item.get("label") or key),
                role=(
                    str(onboarding.get("role") or "").strip()
                    if onboarding is not None and str(onboarding.get("role") or "").strip()
                    else "Attached specialization path."
                ),
                status="active" if active else mode,
                attached=True,
                active=active,
                pinned=False,
                available=True,
                degraded=False,
                requires_restart=False,
                capabilities=[str(cap) for cap in (item.get("capabilities") or []) if str(cap)],
                limitations=[str(entry) for entry in ((onboarding or {}).get("limitations") or []) if str(entry)],
                metadata=metadata,
            )
        )
    return records


def _derive_current_capabilities(records: list[SystemRegistryRecord]) -> list[str]:
    capabilities: list[str] = ["1:1 conversational work through Builder"]
    active_systems = {record.key for record in records if record.kind == "system" and record.status in {"ready", "configured", "available"}}
    adapters = sorted(record.key for record in records if record.kind == "adapter" and record.active)
    providers = sorted(record.key for record in records if record.kind == "provider" and record.available)
    active_chips = sorted(record.key for record in records if record.kind == "chip" and record.active)
    active_paths = sorted(record.key for record in records if record.kind == "path" and record.active)

    if "spark_researcher" in active_systems:
        capabilities.append("provider-backed advisory through Spark Researcher")
    if "spark_swarm" in active_systems:
        capabilities.append("Swarm escalation and collective execution")
    if "spark_browser" in active_systems:
        capabilities.append("governed browser search and page inspection")
    if "spark_voice" in active_systems:
        capabilities.append("voice transcription and speech replies")
    if "spark_spawner" in active_systems:
        capabilities.append("Spawner mission, schedule, Kanban, and workflow control")
    if "spark_local_work" in active_systems:
        capabilities.append("operator-governed local repo/file inspection through Codex or Spawner workflows")
    if adapters:
        capabilities.append(f"messaging on {', '.join(adapters)}")
    if providers:
        capabilities.append(f"LLM/provider execution on {', '.join(providers)}")
    if active_chips:
        capabilities.append(f"active chips: {', '.join(active_chips)}")
    if active_paths:
        capabilities.append(f"active specialization paths: {', '.join(active_paths)}")
    return capabilities


def _collect_browser_registry_payload(config_manager: ConfigManager) -> dict[str, Any] | None:
    payload = build_browser_status_payload(
        config_manager=config_manager,
        browser_family="brave",
        profile_key="spark-default",
        profile_mode="dedicated",
        agent_id=None,
    )
    try:
        execution = run_first_active_chip_hook(config_manager, hook=_BROWSER_STATUS_HOOK, payload=payload)
    except ValueError as exc:
        return {
            "status": "unavailable",
            "chip_key": "browser",
            "error_code": "BROWSER_STATUS_INVALID",
            "error_message": str(exc),
        }
    if execution is None:
        return None
    hook_output = execution.output if isinstance(execution.output, dict) else {}
    hook_status = _normalize_browser_hook_status(hook_output)
    hook_error = hook_output.get("error") if isinstance(hook_output.get("error"), dict) else {}
    hook_failed = (not execution.ok) or bool(hook_error) or (
        hook_status is not None and hook_status not in {"succeeded", "completed", "ok", "success"}
    )
    return {
        "status": "failed" if hook_failed else "completed",
        "chip_key": execution.chip_key,
        "hook_status": hook_status or ("failed" if hook_failed else "succeeded"),
        "approval_state": hook_output.get("approval_state") if isinstance(hook_output.get("approval_state"), str) else None,
        "error_code": str(hook_error.get("code") or "").strip() or None,
        "error_message": str(hook_error.get("message") or "").strip() or None,
        "provenance": hook_output.get("provenance") if isinstance(hook_output.get("provenance"), dict) else {},
    }


def _format_system_registry_direct_reply_line(record: dict[str, Any]) -> str:
    label = str(record.get("label") or record.get("key") or "system")
    key = str(record.get("key") or "")
    status = str(record.get("status") or "unknown")
    limitations = [str(item).strip() for item in (record.get("limitations") or []) if str(item).strip()]
    if key == "spark_intelligence_builder":
        detail = "Telegram delivery, routing, and operator controls are live." if status == "ready" else limitations[:1]
    elif key == "spark_researcher":
        detail = "Provider-backed advisory is available." if status == "ready" else limitations[:1]
    elif key == "spark_swarm":
        detail = "Escalation and collective sync are available." if status in {"ready", "configured"} else limitations[:1]
    elif key == "spark_browser":
        if status == "ready":
            detail = "Live search and page inspection are available."
        elif status == "standby":
            detail = limitations[:1] or ["Reconnect the extension session to restore live browser grounding."]
        else:
            detail = limitations[:1]
    elif key == "spark_voice":
        detail = "Speech I/O is attached." if status in {"ready", "available"} else limitations[:1]
    elif key == "spark_memory":
        detail = "Persistent memory and observability are active."
    elif key == "spark_spawner":
        detail = "Mission, schedule, Kanban, and workflow control are configured."
    elif key == "spark_local_work":
        detail = "Local repo/file inspection is available through operator-governed Codex or Spawner workflows."
    else:
        detail = limitations[:1]
    suffix = ""
    if isinstance(detail, list) and detail:
        suffix = f"; {detail[0]}"
    elif isinstance(detail, str) and detail:
        suffix = f"; {detail}"
    return f"- {label}: {status}{suffix}"


def _normalize_browser_hook_status(hook_output: dict[str, Any]) -> str | None:
    value = hook_output.get("status")
    if value is None:
        return None
    normalized = str(value).strip().lower()
    return normalized or None


def _prompt_section_lines(records: list[dict[str, Any]], *, kind: str, title: str) -> list[str]:
    section_lines: list[str] = []
    for record in records:
        if not isinstance(record, dict) or str(record.get("kind") or "") != kind:
            continue
        line = f"- {record['label']}: status={record['status']}"
        metadata = record.get("metadata") or {}
        if kind == "provider":
            model = str(metadata.get("default_model") or "").strip()
            auth_method = str(metadata.get("auth_method") or "").strip()
            if model:
                line += f" model={model}"
            if auth_method:
                line += f" auth={auth_method}"
        section_lines.append(line)
    if not section_lines:
        return []
    return [title, *section_lines]


def _prompt_onboarding_lines(records: list[dict[str, Any]]) -> list[str]:
    section_lines: list[str] = []
    for record in records:
        if not isinstance(record, dict) or str(record.get("kind") or "") not in {"chip", "path"}:
            continue
        metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
        onboarding = metadata.get("onboarding") if isinstance(metadata.get("onboarding"), dict) else {}
        if not onboarding:
            continue
        line = f"- {record['key']}:"
        harnesses = [str(item) for item in (onboarding.get("harnesses") or []) if str(item)]
        surfaces = [str(item) for item in (onboarding.get("surfaces") or []) if str(item)]
        permissions = [str(item) for item in (onboarding.get("permissions") or []) if str(item)]
        example_intents = [str(item) for item in (onboarding.get("example_intents") or []) if str(item)]
        if harnesses:
            line += f" harnesses={','.join(harnesses[:4])}"
        if surfaces:
            line += f" surfaces={','.join(surfaces[:4])}"
        if permissions:
            line += f" permissions={','.join(permissions[:4])}"
        if example_intents:
            line += f" intents={'; '.join(example_intents[:2])}"
        section_lines.append(line)
    if not section_lines:
        return []
    return ["[Onboarded contracts]", *section_lines]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
