from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.state.db import StateDB


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

    return DoctorReport(checks=checks)
