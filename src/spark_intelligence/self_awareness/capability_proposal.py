from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from spark_intelligence.self_awareness.connector_harness import build_connector_harness_envelope


IMPLEMENTATION_ROUTES = {
    "domain_chip",
    "runtime_patch",
    "capability_connector",
    "mission_artifact",
    "workflow_automation",
}


@dataclass(frozen=True)
class CapabilityProposalPacket:
    capability_goal: str
    recipient: str
    implementation_route: str
    owner_system: str
    permissions_required: list[str]
    safe_probe: str
    human_approval_boundary: str
    rollback_path: str
    activation_path: str
    eval_or_smoke_test: str
    capability_ledger_key: str
    claim_boundary: str
    source_intent: str
    connector_harness: dict[str, Any] | None = None
    status: str = "proposal_plan_only"
    schema_version: str = "spark.capability_proposal.v1"

    def to_payload(self) -> dict[str, Any]:
        payload = {
            "schema_version": self.schema_version,
            "status": self.status,
            "capability_goal": self.capability_goal,
            "recipient": self.recipient,
            "implementation_route": self.implementation_route,
            "owner_system": self.owner_system,
            "permissions_required": self.permissions_required,
            "safe_probe": self.safe_probe,
            "human_approval_boundary": self.human_approval_boundary,
            "rollback_path": self.rollback_path,
            "activation_path": self.activation_path,
            "eval_or_smoke_test": self.eval_or_smoke_test,
            "capability_ledger_key": self.capability_ledger_key,
            "claim_boundary": self.claim_boundary,
            "source_intent": self.source_intent,
        }
        if self.connector_harness:
            payload["connector_harness"] = self.connector_harness
        return payload


def build_capability_proposal_packet(*, goal: str, user_message: str = "") -> CapabilityProposalPacket:
    source_intent = _compact(user_message or goal)
    capability_goal = _normalize_goal(goal or user_message)
    lowered = f"{goal} {user_message}".casefold()
    implementation_route = _implementation_route(lowered)
    owner_system = _owner_system(implementation_route)
    recipient = _recipient(lowered)
    permissions = _permissions_required(lowered, implementation_route)
    ledger_key = f"{implementation_route}:{_slug(capability_goal)}"
    connector_harness = build_connector_harness_envelope(
        goal=capability_goal,
        implementation_route=implementation_route,
        permissions_required=permissions,
    )
    return CapabilityProposalPacket(
        capability_goal=capability_goal,
        recipient=recipient,
        implementation_route=implementation_route,
        owner_system=owner_system,
        permissions_required=permissions,
        safe_probe=_safe_probe(implementation_route, lowered),
        human_approval_boundary=_approval_boundary(permissions, implementation_route),
        rollback_path=_rollback_path(implementation_route),
        activation_path=_activation_path(implementation_route),
        eval_or_smoke_test=_eval_or_smoke_test(implementation_route, lowered),
        capability_ledger_key=ledger_key,
        claim_boundary=_claim_boundary(implementation_route),
        source_intent=source_intent,
        connector_harness=connector_harness.to_payload() if connector_harness else None,
    )


def _normalize_goal(text: str) -> str:
    compact = _compact(text).strip(" .?!")
    prefix = "Improve Spark capability safely:"
    if compact.casefold().startswith(prefix.casefold()):
        compact = compact[len(prefix):].strip()
    marker = "Treat this as a capability proposal:"
    marker_index = compact.casefold().find(marker.casefold())
    if marker_index >= 0:
        compact = compact[:marker_index].strip(" .")
    return compact or "Improve Spark capability safely"


def _implementation_route(lowered: str) -> str:
    if _has_any(lowered, ("dashboard", "app", "website", "site", "portal", "viewer", "panel")) and not _has_any(
        lowered,
        ("so spark can", "so you can", "for you", "for spark to", "lets you", "lets spark"),
    ):
        return "mission_artifact"
    if _has_any(lowered, ("daily report", "daily reports", "schedule", "scheduled", "automate", "automation", "reminder", "notification")):
        return "workflow_automation"
    if _has_any(lowered, ("email", "emails", "gmail", "inbox", "calendar", "voice", "speech", "browser", "browse", "files", "filesystem", "api", "webhook")):
        return "capability_connector"
    if _has_any(lowered, ("domain chip", "domain-chip", "chip", "skill", "watchtower", "evaluate", "suggest")):
        return "domain_chip"
    if _has_any(lowered, ("brain", "runtime", "route", "routing", "memory", "workflow", "self-awareness", "self awareness")):
        return "runtime_patch"
    return "runtime_patch"


def _owner_system(route: str) -> str:
    return {
        "domain_chip": "Spark Intelligence Builder + domain chip attachment runtime",
        "runtime_patch": "Spark Intelligence Builder",
        "capability_connector": "Spark Intelligence Builder + connector chip/harness",
        "mission_artifact": "Spark Spawner / Mission Control",
        "workflow_automation": "Spark Intelligence Builder + scheduler/Mission Control",
    }.get(route, "Spark Intelligence Builder")


def _recipient(lowered: str) -> str:
    if _has_any(lowered, ("spark users", "users of spark")):
        return "spark_users"
    if _has_any(lowered, ("for you", "for spark", "spark can", "you can", "yourself", "my spark", "our spark")):
        return "spark"
    if _has_any(lowered, ("email", "gmail", "calendar", "api", "webhook")):
        return "external_system"
    return "spark"


def _permissions_required(lowered: str, route: str) -> list[str]:
    permissions: list[str] = []
    if _has_any(lowered, ("email", "emails", "gmail", "inbox")):
        permissions.extend(["email_account_access", "message_read_scope"])
    if "calendar" in lowered:
        permissions.extend(["calendar_account_access", "event_read_scope"])
    if _has_any(lowered, ("files", "filesystem", "project files")):
        permissions.extend(["filesystem_read_scope"])
    if _has_any(lowered, ("voice", "speech")):
        permissions.extend(["voice_provider_access", "telegram_voice_delivery"])
    if _has_any(lowered, ("browser", "browse")):
        permissions.extend(["browser_runtime_access"])
    if _has_any(lowered, ("notification", "reminder", "schedule", "daily report", "daily reports")):
        permissions.extend(["scheduler_write_scope", "delivery_channel_access"])
    if route in {"runtime_patch", "domain_chip", "capability_connector"}:
        permissions.append("operator_approval_to_activate")
    if not permissions:
        permissions.append("operator_approval_to_plan")
    return _dedupe(permissions)


def _safe_probe(route: str, lowered: str) -> str:
    if "email" in lowered or "gmail" in lowered or "inbox" in lowered:
        return "Run a read-only provider/auth status probe, then fetch at most one redacted metadata-only message sample in dry-run mode."
    if "calendar" in lowered:
        return "Run a read-only calendar auth/status probe, then fetch one redacted upcoming-event metadata sample in dry-run mode."
    if "files" in lowered or "filesystem" in lowered:
        return "Run a scoped filesystem listing probe against an operator-approved test directory."
    if "voice" in lowered or "speech" in lowered:
        return "Run voice.status, then synthesize a short test phrase without changing global voice settings."
    if route == "domain_chip":
        return "Scaffold or inspect the chip manifest, then run its health/status hook with a synthetic payload."
    if route == "workflow_automation":
        return "Render the schedule and run one dry-run report without sending or persisting live changes."
    if route == "mission_artifact":
        return "Create the Spawner mission in dry-run or preview mode, then verify expected artifacts before activation."
    return "Run the smallest status/eval probe for the target route and record success, failure, latency, and trace id."


def _approval_boundary(permissions: list[str], route: str) -> str:
    if any(permission.endswith("_access") or permission.endswith("_scope") for permission in permissions):
        return "Human approval required before connecting accounts, reading private data, scheduling delivery, or activating new tool authority."
    if route == "mission_artifact":
        return "Human approval required before publishing or wiring the artifact into Spark runtime surfaces."
    return "Human approval required before raising capability confidence or changing runtime behavior."


def _rollback_path(route: str) -> str:
    return {
        "domain_chip": "Deactivate or unpin the chip, remove it from active_keys, and keep the repo for inspection.",
        "runtime_patch": "Revert the bounded code/config change and rerun the previous passing probe.",
        "capability_connector": "Disable connector credentials/config, deactivate any connector chip, and clear scheduled jobs.",
        "mission_artifact": "Pause/archive the Spawner mission and leave Spark runtime settings unchanged.",
        "workflow_automation": "Pause the schedule, preserve dry-run artifacts, and remove delivery permissions.",
    }.get(route, "Return to the previous config and rerun the last known-good probe.")


def _activation_path(route: str) -> str:
    return {
        "domain_chip": "Attach the chip through spark-chip.json, activate or pin it deliberately, then record a successful hook invocation.",
        "runtime_patch": "Land the bounded patch, restart affected Spark services, then record a successful route/eval probe.",
        "capability_connector": "Attach connector config, run health and smoke probes, then enable the scoped route for approved users.",
        "mission_artifact": "Build through Spawner/Mission Control, verify artifacts, then expose links or optional runtime integration.",
        "workflow_automation": "Create the schedule disabled or dry-run first, verify output, then enable delivery with owner approval.",
    }.get(route, "Activate only after the probe and eval pass.")


def _eval_or_smoke_test(route: str, lowered: str) -> str:
    if route == "domain_chip":
        return "Chip manifest discovery test plus one router-invokable synthetic hook test."
    if route == "capability_connector":
        return "Connector health test plus one redacted dry-run invocation with no broad data access."
    if route == "workflow_automation":
        return "Schedule rendering test plus one dry-run output snapshot and delivery suppression assertion."
    if route == "mission_artifact":
        return "Spawner mission creation test plus artifact/link verification."
    if "route" in lowered or "routing" in lowered:
        return "Route-selection regression test covering positive and negative natural-language prompts."
    return "Focused regression test for the changed runtime path plus a safe live-status probe."


def _claim_boundary(route: str) -> str:
    return (
        "This packet is a plan, not proof of a live capability. Spark may claim the capability only after "
        f"the {route} activation path and eval_or_smoke_test pass and the capability ledger records recent success."
    )


def _compact(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _slug(text: str) -> str:
    slug = "-".join(re.findall(r"[a-z0-9]+", text.casefold())[:8])
    return slug or "custom-capability"


def _has_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(needle in text for needle in needles)


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            deduped.append(item)
    return deduped
