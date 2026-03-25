from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from spark_intelligence.attachments import attachment_status
from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.researcher_bridge import discover_researcher_runtime_root, resolve_researcher_config_path
from spark_intelligence.state.db import StateDB
from spark_intelligence.swarm_bridge import swarm_status


@dataclass
class DoctorCheck:
    name: str
    ok: bool
    detail: str


@dataclass
class DoctorReport:
    checks: list[DoctorCheck]

    @property
    def ok(self) -> bool:
        return all(check.ok for check in self.checks)

    def to_json(self) -> str:
        payload = {
            "ok": self.ok,
            "checks": [{"name": c.name, "ok": c.ok, "detail": c.detail} for c in self.checks],
        }
        return json.dumps(payload, indent=2)

    def to_text(self) -> str:
        lines = [f"Doctor status: {'ok' if self.ok else 'degraded'}"]
        for check in self.checks:
            marker = "ok" if check.ok else "fail"
            lines.append(f"- [{marker}] {check.name}: {check.detail}")
        return "\n".join(lines)


def run_doctor(config_manager: ConfigManager, state_db: StateDB) -> DoctorReport:
    checks: list[DoctorCheck] = []
    paths = config_manager.paths

    checks.append(DoctorCheck("home", paths.home.exists(), str(paths.home)))
    checks.append(DoctorCheck("config.yaml", paths.config_yaml.exists(), str(paths.config_yaml)))
    checks.append(DoctorCheck(".env", paths.env_file.exists(), str(paths.env_file)))
    checks.append(DoctorCheck("state.db", paths.state_db.exists(), str(paths.state_db)))

    try:
        config = config_manager.load()
        checks.append(DoctorCheck("config-load", True, f"loaded workspace {config.get('workspace', {}).get('id', 'unknown')}"))
    except Exception as exc:  # pragma: no cover - defensive
        checks.append(DoctorCheck("config-load", False, str(exc)))

    try:
        with state_db.connect() as conn:
            conn.execute("SELECT 1 FROM schema_info LIMIT 1").fetchone()
        checks.append(DoctorCheck("state-schema", True, "schema initialized"))
    except sqlite3.Error as exc:
        checks.append(DoctorCheck("state-schema", False, str(exc)))

    try:
        with state_db.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM workspace_roles WHERE human_id = 'local-operator' AND role = 'operator_admin' LIMIT 1"
            ).fetchone()
        checks.append(DoctorCheck("operator-authority", bool(row), "local operator present"))
    except sqlite3.Error as exc:
        checks.append(DoctorCheck("operator-authority", False, str(exc)))

    env_map = config_manager.read_env_map()
    provider_records = config_manager.load().get("providers", {}).get("records", {})
    if provider_records:
        missing_refs = []
        for provider_id, record in provider_records.items():
            env_key = record.get("api_key_env")
            if env_key and env_key not in env_map:
                missing_refs.append(f"{provider_id}:{env_key}")
        checks.append(
            DoctorCheck(
                "provider-secrets",
                not missing_refs,
                "all provider env refs resolved" if not missing_refs else ", ".join(missing_refs),
            )
        )
    else:
        checks.append(DoctorCheck("provider-secrets", True, "no providers configured yet"))

    researcher_root, researcher_source = discover_researcher_runtime_root(config_manager)
    if researcher_root:
        researcher_config_path = resolve_researcher_config_path(config_manager, researcher_root)
        checks.append(
            DoctorCheck(
                "researcher-bridge",
                researcher_config_path.exists(),
                (
                    f"{researcher_source}:{researcher_root} config={researcher_config_path}"
                    if researcher_config_path.exists()
                    else f"{researcher_source}:{researcher_root} missing config at {researcher_config_path}"
                ),
            )
        )
    else:
        checks.append(
            DoctorCheck(
                "researcher-bridge",
                True,
                "not connected yet; advisory bridge will stay in stub mode",
            )
        )

    swarm = swarm_status(config_manager)
    swarm_hosted_fields = [swarm.api_url, swarm.workspace_id, swarm.access_token_env]
    hosted_field_count = sum(1 for field in swarm_hosted_fields if field)

    if not swarm.configured:
        checks.append(DoctorCheck("swarm-bridge", True, "not connected yet; Swarm sync is optional"))
    elif swarm.runtime_root and hosted_field_count == 0:
        checks.append(
            DoctorCheck(
                "swarm-bridge",
                True,
                f"local swarm repo connected at {swarm.runtime_root}; hosted sync not configured yet",
            )
        )
    elif not swarm.researcher_ready:
        checks.append(DoctorCheck("swarm-bridge", False, "configured but Spark Researcher is not ready for payload export"))
    elif 0 < hosted_field_count < 3:
        checks.append(
            DoctorCheck(
                "swarm-bridge",
                False,
                "configured but missing api_url, workspace_id, or access_token_env",
            )
        )
    elif not swarm.api_ready:
        checks.append(
            DoctorCheck(
                "swarm-bridge",
                True,
                "API config present; upload readiness depends on access token resolution and latest payload availability",
            )
        )
    else:
        checks.append(
            DoctorCheck(
                "swarm-bridge",
                True,
                f"ready api={swarm.api_url} workspace={swarm.workspace_id}",
            )
        )

    attachments = attachment_status(config_manager)
    attachment_count = len(attachments.records)
    if attachments.warnings:
        checks.append(
            DoctorCheck(
                "attachments",
                True,
                f"{attachment_count} discovered with {len(attachments.warnings)} warning(s)",
            )
        )
    else:
        checks.append(
            DoctorCheck(
                "attachments",
                True,
                f"{attachment_count} discovered ({len([r for r in attachments.records if r.kind == 'chip'])} chips, {len([r for r in attachments.records if r.kind == 'path'])} paths)",
            )
        )

    return DoctorReport(checks=checks)
