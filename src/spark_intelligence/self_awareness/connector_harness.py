from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from spark_intelligence.observability.policy import looks_secret_like


CONNECTOR_HARNESS_SCHEMA_VERSION = "spark.connector_harness.v1"

_SURFACE_RULES = (
    ("email", ("email", "emails", "gmail", "inbox"), "email_metadata_redacted"),
    ("calendar", ("calendar", "meeting", "event"), "calendar_metadata_redacted"),
    ("voice", ("voice", "speech", "audio"), "synthetic_phrase_only"),
    ("browser", ("browser", "browse", "web page", "website access"), "origin_allowlist_snapshot"),
    ("files", ("files", "filesystem", "project files", "folders"), "approved_test_directory_only"),
    (
        "workflow",
        ("daily report", "daily reports", "schedule", "scheduled", "automation", "reminder", "notification"),
        "dry_run_delivery_suppressed",
    ),
    ("api", ("api", "webhook", "integration"), "status_endpoint_only"),
)

_SURFACE_PERMISSIONS = {
    "email": ["email_account_access", "message_read_scope"],
    "calendar": ["calendar_account_access", "event_read_scope"],
    "voice": ["voice_provider_access", "telegram_voice_delivery"],
    "browser": ["browser_runtime_access"],
    "files": ["filesystem_read_scope"],
    "workflow": ["scheduler_write_scope", "delivery_channel_access"],
    "api": ["external_api_status_scope"],
}

_SENSITIVE_KEYS = {
    "access_token",
    "api_key",
    "authorization",
    "body",
    "client_secret",
    "content",
    "cookie",
    "description",
    "details",
    "email",
    "from",
    "message",
    "password",
    "phone",
    "recipient",
    "refresh_token",
    "sender",
    "secret",
    "snippet",
    "subject",
    "text",
    "title",
    "to",
    "token",
}


@dataclass(frozen=True)
class ConnectorHarnessEnvelope:
    connector_key: str
    dry_run_probe: str
    redaction_policy: str
    permissions_required: list[str]
    approval_prompt: str
    live_access_blocked_until: str
    blocked_live_actions: list[str]
    trace_fields: list[str]
    authority_stage: str = "proposal_only"
    schema_version: str = CONNECTOR_HARNESS_SCHEMA_VERSION

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "authority_stage": self.authority_stage,
            "connector_key": self.connector_key,
            "permissions_required": self.permissions_required,
            "dry_run_probe": self.dry_run_probe,
            "redaction_policy": self.redaction_policy,
            "approval_prompt": self.approval_prompt,
            "live_access_blocked_until": self.live_access_blocked_until,
            "blocked_live_actions": self.blocked_live_actions,
            "trace_fields": self.trace_fields,
            "truth_boundary": "connector_harness_is_not_live_access_or_activation_proof",
        }


def build_connector_harness_envelope(
    *,
    goal: str,
    implementation_route: str,
    permissions_required: list[str] | None = None,
) -> ConnectorHarnessEnvelope | None:
    surface = _detect_surface(goal, permissions_required or [])
    if not surface and implementation_route not in {"capability_connector", "workflow_automation"}:
        return None
    connector_key = surface or ("workflow" if implementation_route == "workflow_automation" else "api")
    permissions = _dedupe([*_SURFACE_PERMISSIONS.get(connector_key, []), *(permissions_required or [])])
    return ConnectorHarnessEnvelope(
        connector_key=connector_key,
        permissions_required=permissions,
        dry_run_probe=_dry_run_probe(connector_key),
        redaction_policy=_redaction_policy(connector_key),
        approval_prompt=_approval_prompt(connector_key, permissions),
        live_access_blocked_until=(
            "human_approval_recorded_and_probe_eval_passed_in_capability_ledger"
        ),
        blocked_live_actions=_blocked_live_actions(connector_key),
        trace_fields=[
            "capability_ledger_key",
            "connector_key",
            "probe_ref",
            "approval_ref",
            "eval_ref",
            "redaction_policy",
        ],
    )


def redact_connector_probe_sample(*, connector_key: str, sample: Any) -> Any:
    if isinstance(sample, dict):
        return {
            str(key): _redact_value(connector_key=connector_key, key=str(key), value=value)
            for key, value in sample.items()
        }
    if isinstance(sample, list):
        return [redact_connector_probe_sample(connector_key=connector_key, sample=item) for item in sample]
    if isinstance(sample, str) and looks_secret_like(sample):
        return "<redacted secret-like value>"
    return sample


def _redact_value(*, connector_key: str, key: str, value: Any) -> Any:
    normalized_key = re.sub(r"[^a-z0-9]+", "_", key.casefold()).strip("_")
    if normalized_key in _SENSITIVE_KEYS or _key_contains_sensitive_part(normalized_key):
        return _redaction_label(connector_key, normalized_key)
    if isinstance(value, (dict, list)):
        return redact_connector_probe_sample(connector_key=connector_key, sample=value)
    if isinstance(value, str) and looks_secret_like(value):
        return "<redacted secret-like value>"
    return value


def _detect_surface(goal: str, permissions: list[str]) -> str | None:
    lowered = str(goal or "").casefold()
    joined_permissions = " ".join(permissions).casefold()
    for surface, needles, _policy in _SURFACE_RULES:
        if any(needle in lowered for needle in needles):
            return surface
        if surface in joined_permissions:
            return surface
    if "message_read_scope" in joined_permissions:
        return "email"
    if "event_read_scope" in joined_permissions:
        return "calendar"
    if "filesystem_read_scope" in joined_permissions:
        return "files"
    if "scheduler_write_scope" in joined_permissions:
        return "workflow"
    return None


def _dry_run_probe(connector_key: str) -> str:
    return {
        "email": "Check provider/auth status, then fetch at most one metadata-only message sample with subject/body/addresses redacted.",
        "calendar": "Check calendar auth status, then fetch at most one upcoming-event metadata sample with attendee/title/details redacted.",
        "voice": "Check voice provider status, then synthesize a short fixed test phrase without changing global reply mode.",
        "browser": "Check browser session and origin permission status before any page read; snapshot only an approved origin.",
        "files": "List only an operator-approved test directory and return filenames/counts, not file contents.",
        "workflow": "Render the schedule and produce one dry-run report with delivery suppressed.",
        "api": "Check connector configuration and call only a status or schema endpoint with secrets omitted.",
    }.get(connector_key, "Run a read-only status probe with secrets and private content omitted.")


def _redaction_policy(connector_key: str) -> str:
    policy = next((item[2] for item in _SURFACE_RULES if item[0] == connector_key), "metadata_only")
    return f"{policy}; redact secrets, account identifiers, message contents, and private free text before model exposure"


def _approval_prompt(connector_key: str, permissions: list[str]) -> str:
    permission_text = ", ".join(permissions) if permissions else "the requested connector permissions"
    return (
        f"Record human approval for {connector_key} connector activation only after reviewing the dry-run probe, "
        f"the requested scopes ({permission_text}), and the rollback path."
    )


def _blocked_live_actions(connector_key: str) -> list[str]:
    return {
        "email": ["read_message_body", "send_email", "persist_message_content", "train_on_inbox"],
        "calendar": ["create_event", "modify_event", "persist_event_details", "invite_attendees"],
        "voice": ["enable_default_voice_replies", "record_microphone_audio", "change_global_voice_provider"],
        "browser": ["submit_forms", "click_sensitive_controls", "read_unapproved_origins", "persist_cookies"],
        "files": ["read_file_contents", "write_files", "delete_files", "index_private_directories"],
        "workflow": ["enable_delivery", "write_schedule", "send_notifications", "persist_report_contents"],
        "api": ["write_remote_state", "store_credentials", "fetch_bulk_private_data"],
    }.get(connector_key, ["perform_live_action", "persist_private_data"])


def _key_contains_sensitive_part(normalized_key: str) -> bool:
    parts = {part for part in normalized_key.split("_") if part}
    return bool(parts & _SENSITIVE_KEYS)


def _redaction_label(connector_key: str, key: str) -> str:
    return f"<redacted {connector_key} {key}>"


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for item in items:
        text = str(item or "").strip()
        if text and text not in seen:
            seen.add(text)
            deduped.append(text)
    return deduped
