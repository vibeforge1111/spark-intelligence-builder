from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from spark_intelligence.attachments import attachment_status, build_attachment_context
from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.state.db import StateDB


_SYSTEM_ROLE_HINTS: dict[str, str] = {
    "spark_intelligence_builder": "Runtime owner for delivery, operator controls, attachment state, persona state, and final user-visible replies.",
    "spark_researcher": "Primary intelligence core behind provider-backed advisory and normal reasoning.",
    "spark_swarm": "Deep-work escalation and collective coordination surface for parallel or multi-node work.",
    "spark_browser": "Governed browser/search surface for page inspection, source capture, and browse workflows.",
    "spark_voice": "Speech I/O surface for transcription and synthesis around the Builder-owned personality.",
    "spark_memory": "Persistent local memory and observability state used across sessions and runtime surfaces.",
}

_SYSTEM_CAPABILITY_HINTS: dict[str, list[str]] = {
    "spark_intelligence_builder": ["delivery", "operator_controls", "attachments", "persona_state", "routing"],
    "spark_researcher": ["provider_advisory", "reasoning", "conversation_support"],
    "spark_swarm": ["escalation", "collective_coordination", "autoloops"],
    "spark_browser": ["web_search", "page_inspection", "source_capture"],
    "spark_voice": ["speech_to_text", "text_to_speech"],
    "spark_memory": ["runtime_memory", "observability", "state_lookup"],
}

_KNOWN_CHIP_ROLE_HINTS: dict[str, str] = {
    "startup-yc": "Founder/operator doctrine chip for decisive startup guidance when active.",
    "spark-browser": "Governed browser and search chip for page inspection, browse flows, and source capture.",
    "spark-personality-chip-labs": "Baseline personality import chip; it seeds Builder, but Builder owns live style state after import.",
    "spark-swarm": "Swarm bridge and identity/escalation chip for collective or deep-work execution.",
    "domain-chip-voice-comms": "Speech I/O chip for STT and TTS around the Builder-owned personality.",
}


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


def build_system_registry(config_manager: ConfigManager, state_db: StateDB) -> SystemRegistrySnapshot:
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
            _build_chip_backed_system_record(
                system_key="spark_browser",
                chip_key="spark-browser",
                active_chip_keys=active_chip_keys,
                attachment_context=attachment_context,
            ),
            _build_chip_backed_system_record(
                system_key="spark_voice",
                chip_key="domain-chip-voice-comms",
                active_chip_keys=active_chip_keys,
                attachment_context=attachment_context,
            ),
            _build_memory_record(),
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
        records.append(
            SystemRegistryRecord(
                record_id=f"chip:{key}",
                kind="chip",
                key=key,
                label=str(item.get("label") or key),
                role=str(item.get("description") or "").strip() or _KNOWN_CHIP_ROLE_HINTS.get(key, "Attached chip."),
                status=status,
                attached=True,
                active=active,
                pinned=pinned,
                available=True,
                degraded=False,
                requires_restart=False,
                capabilities=[str(cap) for cap in (item.get("capabilities") or []) if str(cap)],
                limitations=[],
                metadata={
                    "hook_names": [str(hook) for hook in (item.get("hook_names") or []) if str(hook)],
                    "repo_root": item.get("repo_root"),
                    "attachment_mode": mode,
                },
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
        records.append(
            SystemRegistryRecord(
                record_id=f"path:{key}",
                kind="path",
                key=key,
                label=str(item.get("label") or key),
                role="Attached specialization path.",
                status="active" if active else mode,
                attached=True,
                active=active,
                pinned=False,
                available=True,
                degraded=False,
                requires_restart=False,
                capabilities=[str(cap) for cap in (item.get("capabilities") or []) if str(cap)],
                limitations=[],
                metadata={
                    "hook_names": [str(hook) for hook in (item.get("hook_names") or []) if str(hook)],
                    "repo_root": item.get("repo_root"),
                    "attachment_mode": mode,
                },
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
    if adapters:
        capabilities.append(f"messaging on {', '.join(adapters)}")
    if providers:
        capabilities.append(f"LLM/provider execution on {', '.join(providers)}")
    if active_chips:
        capabilities.append(f"active chips: {', '.join(active_chips)}")
    if active_paths:
        capabilities.append(f"active specialization paths: {', '.join(active_paths)}")
    return capabilities


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


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
