from __future__ import annotations

import importlib
import json
import os
import sqlite3
import tomllib
from dataclasses import dataclass
from pathlib import Path

from spark_intelligence.attachments import attachment_status
from spark_intelligence.attachments.snapshot import sync_attachment_snapshot
from spark_intelligence.adapters.discord.runtime import build_discord_runtime_summary
from spark_intelligence.adapters.telegram.runtime import build_telegram_runtime_summary, read_telegram_runtime_health
from spark_intelligence.adapters.whatsapp.runtime import build_whatsapp_runtime_summary
from spark_intelligence.auth.providers import get_provider_spec
from spark_intelligence.auth.runtime import build_auth_status_report, runtime_provider_health
from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.jobs.service import oauth_maintenance_health
from spark_intelligence.memory.doctor import run_memory_doctor
from spark_intelligence.observability.checks import evaluate_stop_ship_issues
from spark_intelligence.observability.store import (
    build_watchtower_snapshot,
    record_environment_snapshot,
    tool_call_ledger_surface_counts,
    repair_foreground_browser_hook_failures,
    repair_memory_lane_artifact_lanes,
    repair_missing_memory_lane_records,
    repair_non_promotable_chip_hook_dispositions,
)
from spark_intelligence.researcher_bridge import discover_researcher_runtime_root, researcher_bridge_status, resolve_researcher_config_path
from spark_intelligence.state.db import StateDB
from spark_intelligence.swarm_bridge import swarm_status


EXPECTED_TOOL_CALL_LEDGER_SURFACES = ("builder", "spark_cli", "telegram", "spawner")


def harness_core_runtime_status() -> dict[str, object]:
    from spark_intelligence import harness_contract

    if harness_contract.HARNESS_CORE_AVAILABLE:
        return {"ok": True, "detail": "spark_harness_core importable", "repair_hint": ""}
    detail = harness_contract.HARNESS_CORE_IMPORT_ERROR or "spark_harness_core unavailable"
    return {
        "ok": False,
        "detail": detail,
        "repair_hint": "declare spark-harness-core in needs.modules and ensure its source path is importable",
    }


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
            "follow_up_surfaces": self.follow_up_surfaces(),
        }
        return json.dumps(payload, indent=2)

    def to_text(self) -> str:
        lines = [f"Doctor status: {'ok' if self.ok else 'degraded'}"]
        for check in self.checks:
            marker = "ok" if check.ok else "fail"
            lines.append(f"- [{marker}] {check.name}: {check.detail}")
        if not self.ok:
            lines.append(
                "- follow-up: use `spark-intelligence status` for repair hints and "
                "`spark-intelligence operator security` for operator/security triage"
            )
        return "\n".join(lines)

    def follow_up_surfaces(self) -> list[str]:
        if self.ok:
            return []
        return [
            "spark-intelligence status",
            "spark-intelligence operator security",
        ]


def run_doctor(config_manager: ConfigManager, state_db: StateDB) -> DoctorReport:
    checks: list[DoctorCheck] = []
    paths = config_manager.paths
    sync_attachment_snapshot(config_manager=config_manager, state_db=state_db)
    record_environment_snapshot(
        state_db,
        surface="doctor_cli",
        summary="Doctor CLI environment snapshot recorded.",
        provider_id=str(config_manager.get_path("providers.default_provider")) if config_manager.get_path("providers.default_provider") else None,
        runtime_root=str(config_manager.get_path("spark.researcher.runtime_root")) if config_manager.get_path("spark.researcher.runtime_root") else None,
        config_path=str(config_manager.get_path("spark.researcher.config_path")) if config_manager.get_path("spark.researcher.config_path") else None,
        env_refs={"home": str(paths.home)},
        facts={"surface": "doctor_cli"},
    )
    repair_non_promotable_chip_hook_dispositions(state_db)
    repair_foreground_browser_hook_failures(state_db)
    repair_missing_memory_lane_records(state_db)
    repair_memory_lane_artifact_lanes(state_db)

    checks.append(DoctorCheck("home", paths.home.exists(), str(paths.home)))
    checks.append(DoctorCheck("config.yaml", paths.config_yaml.exists(), str(paths.config_yaml)))
    checks.append(DoctorCheck(".env", paths.env_file.exists(), str(paths.env_file)))
    checks.append(DoctorCheck("state.db", paths.state_db.exists(), str(paths.state_db)))
    env_permissions_ok, env_permissions_detail = config_manager.env_file_permission_status()
    checks.append(DoctorCheck(".env-permissions", env_permissions_ok, env_permissions_detail))

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

    harness_core = harness_core_runtime_status()
    harness_core_detail = str(harness_core.get("detail") or "unknown")
    harness_core_repair = str(harness_core.get("repair_hint") or "").strip()
    if harness_core_repair:
        harness_core_detail = f"{harness_core_detail}; repair={harness_core_repair}"
    checks.append(DoctorCheck("harness-core", bool(harness_core.get("ok")), harness_core_detail))
    checks.append(_builder_source_truth_check(config_manager))
    checks.append(_python_import_source_check(config_manager))

    try:
        ledger_surface_counts = tool_call_ledger_surface_counts(state_db)
        ledger_total = sum(ledger_surface_counts.values())
        surface_detail = ", ".join(
            f"{surface}={count}" for surface, count in ledger_surface_counts.items()
        ) or "none"
        if ledger_total <= 0:
            checks.append(
                DoctorCheck(
                    "tool-call-ledger-adoption",
                    True,
                    "total=0 surfaces=none; no canonical governed tool-call ledgers persisted yet",
                )
            )
        else:
            missing_surfaces = [
                surface for surface in EXPECTED_TOOL_CALL_LEDGER_SURFACES
                if ledger_surface_counts.get(surface, 0) <= 0
            ]
            missing_detail = (
                f"; missing_expected_surfaces={', '.join(missing_surfaces)}"
                if missing_surfaces
                else "; all_expected_surfaces_present"
            )
            checks.append(
                DoctorCheck(
                    "tool-call-ledger-adoption",
                    True,
                    f"total={ledger_total} surfaces={surface_detail}{missing_detail}",
                )
            )
    except sqlite3.Error as exc:
        checks.append(DoctorCheck("tool-call-ledger-adoption", False, str(exc)))

    auth_report = build_auth_status_report(config_manager=config_manager, state_db=state_db)
    if auth_report.providers:
        unresolved = [
            (
                f"{provider.provider_id}:{provider.status}"
                if provider.auth_method == "oauth"
                else f"{provider.provider_id}:{provider.secret_ref.ref_id if provider.secret_ref else 'missing'}"
            )
            for provider in auth_report.providers
            if not provider.secret_present
        ]
        checks.append(
            DoctorCheck(
                "provider-auth",
                auth_report.ok,
                "all provider auth profiles resolved" if not unresolved else ", ".join(unresolved),
            )
        )
    else:
        checks.append(DoctorCheck("provider-auth", True, "no providers configured yet"))
    provider_runtime_ok, provider_runtime_detail = runtime_provider_health(
        config_manager=config_manager,
        state_db=state_db,
    )
    checks.append(DoctorCheck("provider-runtime", provider_runtime_ok, provider_runtime_detail))
    oauth_maintenance_ok, oauth_maintenance_detail = oauth_maintenance_health(
        config_manager=config_manager,
        state_db=state_db,
    )
    checks.append(DoctorCheck("oauth-maintenance", oauth_maintenance_ok, oauth_maintenance_detail))
    provider_execution_ok, provider_execution_detail = provider_execution_health(
        config_manager=config_manager,
        state_db=state_db,
        auth_report=auth_report,
    )
    checks.append(DoctorCheck("provider-execution", provider_execution_ok, provider_execution_detail))

    researcher_enabled = bool(config_manager.get_path("spark.researcher.enabled", default=True))
    researcher_root, researcher_source = discover_researcher_runtime_root(config_manager)
    if not researcher_enabled:
        checks.append(
            DoctorCheck(
                "researcher-bridge",
                True,
                "disabled by operator",
            )
        )
    elif researcher_root:
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

    swarm = swarm_status(config_manager, state_db)
    swarm_hosted_fields = [swarm.api_url, swarm.workspace_id, swarm.access_token_env]
    hosted_field_count = sum(1 for field in swarm_hosted_fields if field)

    if not swarm.enabled:
        checks.append(DoctorCheck("swarm-bridge", True, "disabled by operator"))
    elif not swarm.configured:
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

    checks.append(_telegram_runtime_check(config_manager=config_manager, state_db=state_db))
    checks.append(_discord_runtime_check(config_manager=config_manager, state_db=state_db))
    checks.append(_whatsapp_runtime_check(config_manager=config_manager, state_db=state_db))
    memory_doctor_report = run_memory_doctor(state_db)
    memory_doctor_detail = (
        memory_doctor_report.findings[0].detail
        if memory_doctor_report.findings
        else "No memory doctor findings recorded."
    )
    checks.append(DoctorCheck("memory-doctor", memory_doctor_report.ok, memory_doctor_detail))
    stop_ship_issues = evaluate_stop_ship_issues(config_manager=config_manager, state_db=state_db, emit_contradictions=True)
    checks.extend(_watchtower_health_checks(config_manager=config_manager, state_db=state_db))
    for issue in stop_ship_issues:
        checks.append(DoctorCheck(issue.name, issue.ok, issue.detail))

    return DoctorReport(checks=checks)


def _builder_source_truth_check(config_manager: ConfigManager) -> DoctorCheck:
    roots = _module_registry_roots(config_manager)
    if not roots:
        return DoctorCheck("builder-source-truth", True, "module registry not configured")

    records = _builder_install_records(roots)
    backlog_records = _builder_desktop_backlog_records(config_manager, records)
    backlog_summary = _builder_desktop_backlog_summary(backlog_records, records)
    if not records:
        root_detail = ", ".join(str(root) for root in roots)
        detail = f"no Builder installs under {root_detail}"
        if backlog_summary:
            detail = f"{detail}; {backlog_summary}"
        return DoctorCheck("builder-source-truth", True, detail)

    install_records = [record for record in records if record.get("canonical") is not False]
    mirror_records = [record for record in records if record.get("canonical") is False]
    if not install_records:
        mirror_summary = _builder_install_summary(mirror_records)
        detail = "no canonical Builder installs"
        if mirror_summary:
            detail = f"{detail}; mirrors={mirror_summary}"
        return DoctorCheck("builder-source-truth", False, detail)

    issues: list[str] = []
    commits = {record["commit"] for record in install_records if record["commit"] != "unknown"}
    if len(commits) > 1:
        issues.append("commit_drift=" + ",".join(sorted(commits)))

    bad_license = [
        f"{record['name']}:{record['license'] or 'missing'}"
        for record in records
        if record["license"] != "AGPL-3.0-only"
    ]
    if bad_license:
        issues.append("license_mismatch=" + ",".join(bad_license))

    missing_harness_dep = [
        str(record["name"])
        for record in install_records
        if "spark-harness-core" not in record["needs_modules"]
    ]
    if missing_harness_dep:
        issues.append("missing_harness_dep=" + ",".join(missing_harness_dep))

    missing_core_deps: list[str] = []
    required_deps = ("jsonschema", "referencing")
    for record in install_records:
        deps = record["dependencies"]
        missing = [dep for dep in required_deps if not any(str(item).startswith(dep) for item in deps)]
        if missing:
            missing_core_deps.append(f"{record['name']}:{'+'.join(missing)}")
    if missing_core_deps:
        issues.append("missing_python_deps=" + ",".join(missing_core_deps))

    summary = _builder_install_summary(install_records)
    mirror_summary = _builder_install_summary(mirror_records)
    detail_suffix = f"installs={summary}"
    if mirror_summary:
        detail_suffix = f"{detail_suffix}; mirrors={mirror_summary}"
    if backlog_summary:
        detail_suffix = f"{detail_suffix}; {backlog_summary}"
    if issues:
        return DoctorCheck("builder-source-truth", False, f"{'; '.join(issues)}; {detail_suffix}")
    return DoctorCheck("builder-source-truth", True, detail_suffix)


def _python_import_source_check(config_manager: ConfigManager) -> DoctorCheck:
    expected = _expected_python_import_src_roots(config_manager)
    imported = _imported_package_src_roots()
    issues: list[str] = []
    details: list[str] = []
    for package_name, imported_root in imported.items():
        candidates = expected.get(package_name, [])
        details.append(f"{package_name}={imported_root}")
        if candidates and not _path_matches_any(imported_root, candidates):
            expected_text = "|".join(str(path) for path in candidates)
            issues.append(f"{package_name}={imported_root} expected={expected_text}")
    if issues:
        return DoctorCheck("python-import-source", False, "; ".join(issues))
    return DoctorCheck("python-import-source", True, "; ".join(details))


def _expected_python_import_src_roots(config_manager: ConfigManager) -> dict[str, list[Path]]:
    roots = _module_registry_roots(config_manager)
    current_builder_src = Path(__file__).resolve().parents[2]
    return {
        "spark_intelligence": _dedupe_paths(
            [
                current_builder_src,
                *[root / "spark-intelligence-builder" / "source" / "src" for root in roots],
            ]
        ),
        "spark_harness_core": _dedupe_paths(
            [root / "spark-harness-core" / "source" / "src" for root in roots]
        ),
    }


def _imported_package_src_roots() -> dict[str, Path]:
    roots: dict[str, Path] = {}
    for package_name in ("spark_intelligence", "spark_harness_core"):
        module = importlib.import_module(package_name)
        module_file = getattr(module, "__file__", None)
        if module_file:
            roots[package_name] = Path(str(module_file)).resolve().parents[1]
    return roots


def _path_matches_any(path: Path, candidates: list[Path]) -> bool:
    try:
        resolved = path.resolve()
    except OSError:
        return False
    for candidate in candidates:
        try:
            if resolved == candidate.resolve():
                return True
        except OSError:
            continue
    return False


def _dedupe_paths(paths: list[Path]) -> list[Path]:
    seen: set[str] = set()
    deduped: list[Path] = []
    for path in paths:
        try:
            key = str(path.resolve())
        except OSError:
            key = str(path)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(path)
    return deduped


def _module_registry_roots(config_manager: ConfigManager) -> list[Path]:
    roots: list[Path] = []
    configured = config_manager.get_path("spark.diagnostics.module_search_roots", default=[])
    if isinstance(configured, str):
        configured = [configured]
    if isinstance(configured, list):
        for raw in configured:
            _append_existing_root(roots, Path(str(raw)).expanduser())

    home = config_manager.paths.home
    _append_existing_root(roots, home / "modules")
    _append_existing_root(roots, home.parent / "modules")
    _append_existing_root(roots, home.parent / ".spark" / "modules")
    for parent in home.parents:
        if parent.name == ".spark":
            _append_existing_root(roots, parent / "modules")
            break

    spark_home = str(os.environ.get("SPARK_HOME") or "").strip()
    if spark_home:
        _append_existing_root(roots, Path(spark_home).expanduser() / "modules")
    return roots


def _append_existing_root(roots: list[Path], path: Path) -> None:
    try:
        resolved = path.resolve()
    except OSError:
        return
    if not resolved.exists() or not resolved.is_dir():
        return
    if resolved not in roots:
        roots.append(resolved)


def _builder_install_records(roots: list[Path]) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for root in roots:
        for manifest in sorted(root.glob("spark-intelligence-builder*/source/spark.toml")):
            records.append(_builder_source_record(manifest.parent, name=manifest.parent.parent.name))
    return records


def _builder_source_record(source_root: Path, *, name: str) -> dict[str, object]:
    manifest_payload = _read_toml(source_root / "spark.toml")
    module = manifest_payload.get("module") if isinstance(manifest_payload.get("module"), dict) else {}
    needs = manifest_payload.get("needs") if isinstance(manifest_payload.get("needs"), dict) else {}
    source_truth = (
        manifest_payload.get("source_truth") if isinstance(manifest_payload.get("source_truth"), dict) else {}
    )
    pyproject = _read_toml(source_root / "pyproject.toml")
    project = pyproject.get("project") if isinstance(pyproject.get("project"), dict) else {}
    return {
        "name": name,
        "path": source_root,
        "commit": _git_head_short(source_root),
        "license": str(module.get("license") or ""),
        "needs_modules": tuple(str(item) for item in (needs.get("modules") or []) if item),
        "dependencies": tuple(str(item) for item in (project.get("dependencies") or []) if item),
        "canonical": source_truth.get("canonical") is not False,
        "source_truth_marked": "canonical" in source_truth,
        "mirror_of": str(source_truth.get("mirror_of") or ""),
    }


def _builder_install_summary(records: list[dict[str, object]]) -> str:
    return "; ".join(
        f"{record['name']} commit={record['commit']} license={record['license'] or 'missing'}"
        for record in records
    )


def _builder_desktop_backlog_records(
    config_manager: ConfigManager,
    install_records: list[dict[str, object]],
) -> list[dict[str, object]]:
    candidates: list[Path] = []
    configured = config_manager.get_path("spark.diagnostics.builder_backlog_roots", default=[])
    if isinstance(configured, str):
        configured = [configured]
    if isinstance(configured, list):
        for raw in configured:
            _append_existing_builder_source(candidates, Path(str(raw)).expanduser())
    _append_existing_builder_source(candidates, Path.home() / "Desktop" / "spark-intelligence-builder")

    install_paths = [record["path"] for record in install_records if isinstance(record.get("path"), Path)]
    backlog_records: list[dict[str, object]] = []
    for source_root in candidates:
        if _path_matches_any(source_root, install_paths):
            continue
        backlog_records.append(_builder_source_record(source_root, name=source_root.name))
    return backlog_records


def _append_existing_builder_source(candidates: list[Path], path: Path) -> None:
    try:
        resolved = path.resolve()
    except OSError:
        return
    if not resolved.is_dir() or not (resolved / "spark.toml").exists():
        return
    if resolved not in candidates:
        candidates.append(resolved)


def _builder_desktop_backlog_summary(
    backlog_records: list[dict[str, object]],
    install_records: list[dict[str, object]],
) -> str:
    if not backlog_records:
        return ""
    install_commits = {str(record.get("commit") or "") for record in install_records if record.get("commit") != "unknown"}
    parts: list[str] = []
    for record in backlog_records:
        flags: list[str] = []
        commit = str(record.get("commit") or "unknown")
        if install_commits and commit not in install_commits:
            flags.append("commit_drift")
        if record.get("license") != "AGPL-3.0-only":
            flags.append(f"license={record.get('license') or 'missing'}")
        if "spark-harness-core" not in record.get("needs_modules", ()):
            flags.append("missing_harness_dep")
        label = "desktop_backlog" if record.get("source_truth_marked") and record.get("canonical") is False else "desktop_backlog_unmarked"
        suffix = " ".join(flags) if flags else "backlog_only"
        parts.append(f"{label}={record['name']} commit={commit} {suffix}")
    return ", ".join(parts)


def _read_toml(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def _git_head_short(repo_root: Path) -> str:
    git_path = repo_root / ".git"
    if git_path.is_file():
        try:
            content = git_path.read_text(encoding="utf-8").strip()
        except OSError:
            return "unknown"
        if content.startswith("gitdir:"):
            raw_git_dir = content.removeprefix("gitdir:").strip()
            git_path = (repo_root / raw_git_dir).resolve()
    head_path = git_path / "HEAD"
    try:
        head = head_path.read_text(encoding="utf-8").strip()
    except OSError:
        return "unknown"
    if head.startswith("ref:"):
        ref_path = git_path / head.removeprefix("ref:").strip()
        try:
            head = ref_path.read_text(encoding="utf-8").strip()
        except OSError:
            return "unknown"
    return head[:7] if head else "unknown"


def _telegram_runtime_check(*, config_manager: ConfigManager, state_db: StateDB) -> DoctorCheck:
    summary = build_telegram_runtime_summary(config_manager, state_db)
    if not summary.configured:
        return DoctorCheck("telegram-runtime", True, "not configured")

    health = read_telegram_runtime_health(state_db)
    if not summary.auth_ref:
        return DoctorCheck("telegram-runtime", False, "configured but auth_ref is missing")
    if health.auth_status in {"failed", "missing"}:
        return DoctorCheck(
            "telegram-runtime",
            False,
            f"auth={health.auth_status} error={health.auth_error or 'unknown'}",
        )
    if health.consecutive_failures > 0 and health.last_failure_at:
        return DoctorCheck(
            "telegram-runtime",
            False,
            (
                f"poll_failures={health.consecutive_failures} "
                f"last_type={health.last_failure_type or 'unknown'} "
                f"backoff={health.last_backoff_seconds}s"
            ),
        )
    detail_parts = [
        f"status={summary.status or 'unknown'}",
        f"pairing_mode={summary.pairing_mode or 'unknown'}",
        f"auth_ref={summary.auth_ref}",
        f"auth={health.auth_status or 'not_checked'}",
        *summary.allowlist_detail_parts(),
    ]
    if health.bot_username:
        detail_parts.append(f"bot=@{health.bot_username}")
    return DoctorCheck("telegram-runtime", True, " ".join(detail_parts))


def _discord_runtime_check(*, config_manager: ConfigManager, state_db: StateDB) -> DoctorCheck:
    summary = build_discord_runtime_summary(config_manager, state_db)
    if not summary.configured:
        return DoctorCheck("discord-runtime", True, "not configured")

    detail_parts = [
        f"status={summary.status or 'unknown'}",
        f"pairing_mode={summary.pairing_mode or 'unknown'}",
        f"auth_ref={summary.auth_ref or 'missing'}",
        f"allowed_users={summary.allowed_user_count}",
        f"ingress={summary.ingress_mode()}",
    ]
    if summary.interaction_public_key_configured:
        detail_parts.append("interaction_public_key=configured")
    if summary.legacy_message_webhook_enabled:
        detail_parts.append(f"webhook_auth_ref={summary.webhook_auth_ref or 'missing'}")
    if summary.ingress_ready():
        return DoctorCheck("discord-runtime", True, " ".join(detail_parts))
    if summary.legacy_message_webhook_enabled:
        return DoctorCheck("discord-runtime", False, " ".join(detail_parts))
    return DoctorCheck(
        "discord-runtime",
        False,
        (
            "no signed interaction public key configured and "
            "legacy message webhook compatibility is disabled"
        ),
    )


def _whatsapp_runtime_check(*, config_manager: ConfigManager, state_db: StateDB) -> DoctorCheck:
    summary = build_whatsapp_runtime_summary(config_manager, state_db)
    if not summary.configured:
        return DoctorCheck("whatsapp-runtime", True, "not configured")

    detail_parts = [
        f"status={summary.status or 'unknown'}",
        f"pairing_mode={summary.pairing_mode or 'unknown'}",
        f"auth_ref={summary.auth_ref or 'missing'}",
        f"allowed_users={summary.allowed_user_count}",
        f"ingress={summary.ingress_mode()}",
    ]
    if summary.webhook_auth_ref:
        detail_parts.append(f"webhook_auth_ref={summary.webhook_auth_ref}")
    if summary.webhook_verify_token_ref:
        detail_parts.append(f"webhook_verify_token_ref={summary.webhook_verify_token_ref}")
    if summary.ingress_ready():
        return DoctorCheck("whatsapp-runtime", True, " ".join(detail_parts))
    return DoctorCheck(
        "whatsapp-runtime",
        False,
        " ".join(detail_parts),
    )


def provider_execution_health(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    auth_report: object,
) -> tuple[bool, str]:
    providers = getattr(auth_report, "providers", [])
    if not providers:
        return True, "no providers configured yet"
    researcher = researcher_bridge_status(config_manager=config_manager, state_db=state_db)
    blocked: list[str] = []
    ready: list[str] = []
    for provider in providers:
        spec = get_provider_spec(provider.provider_id)
        if spec.execution_transport == "external_cli_wrapper":
            if not researcher.available:
                blocked.append(
                    f"{provider.provider_id}:{spec.execution_transport}:researcher_{researcher.mode}"
                )
            else:
                ready.append(f"{provider.provider_id}:{spec.execution_transport}")
        else:
            ready.append(f"{provider.provider_id}:{spec.execution_transport}")
    if blocked:
        return False, ", ".join(blocked)
    return True, ", ".join(ready) if ready else "no provider execution transports active"


def _watchtower_health_checks(*, config_manager: ConfigManager, state_db: StateDB) -> list[DoctorCheck]:
    snapshot = build_watchtower_snapshot(state_db)
    dimensions = snapshot.get("health_dimensions") or {}
    mapping = (
        ("watchtower-ingress", "ingress_health"),
        ("watchtower-execution", "execution_health"),
        ("watchtower-delivery", "delivery_health"),
        ("watchtower-freshness", "scheduler_freshness"),
        ("watchtower-parity", "environment_parity"),
    )
    checks: list[DoctorCheck] = []
    failing_states = {"execution_impaired", "delivery_impaired", "stalled", "parity_broken", "degraded"}
    for check_name, dimension_key in mapping:
        dimension = dimensions.get(dimension_key) or {}
        state = str(dimension.get("state") or "unknown")
        detail = str(dimension.get("detail") or "No detail recorded.")
        ok = state not in failing_states
        if check_name == "watchtower-freshness" and state == "degraded":
            ok = True
        checks.append(
            DoctorCheck(
                check_name,
                ok,
                f"state={state} {detail}",
            )
        )
    contradictions = snapshot.get("contradictions") or {}
    open_count = int(((contradictions.get("counts") or {}).get("open")) or 0)
    checks.append(
        DoctorCheck(
            "watchtower-contradictions",
            open_count == 0,
            f"open={open_count} resolved={int(((contradictions.get('counts') or {}).get('resolved')) or 0)}",
        )
    )
    memory_shadow = (snapshot.get("panels") or {}).get("memory_shadow") or {}
    memory_counts = memory_shadow.get("counts") or {}
    read_requests = int(memory_counts.get("read_requests") or 0)
    read_hits = int(memory_counts.get("read_hits") or 0)
    shadow_only_reads = int(memory_counts.get("shadow_only_reads") or 0)
    if read_requests == 0:
        checks.append(
            DoctorCheck(
                "watchtower-memory-shadow",
                True,
                "no memory-shadow traffic recorded yet",
            )
        )
    elif read_hits == 0 and shadow_only_reads == 0:
        checks.append(
            DoctorCheck(
                "watchtower-memory-shadow",
                False,
                f"state=memory_abstaining read_requests={read_requests} read_hits=0 shadow_only_reads=0",
            )
        )
    else:
        checks.append(
            DoctorCheck(
                "watchtower-memory-shadow",
                True,
                (
                    f"read_requests={read_requests} read_hits={read_hits} "
                    f"shadow_only_reads={shadow_only_reads}"
                ),
            )
        )
    contract_violations = int(memory_counts.get("contract_violations") or 0)
    invalid_role_events = int(memory_counts.get("invalid_role_events") or 0)
    checks.append(
        DoctorCheck(
            "watchtower-memory-contract",
            contract_violations == 0,
            f"contract_violations={contract_violations} invalid_role_events={invalid_role_events}",
        )
    )
    observer_incidents = (snapshot.get("panels") or {}).get("observer_incidents") or {}
    observer_counts = observer_incidents.get("counts") or {}
    observer_total = int(observer_counts.get("total") or 0)
    actionable_value = observer_counts.get("actionable_total")
    actionable_observer_total = int(actionable_value) if actionable_value is not None else observer_total
    observer_classes = observer_incidents.get("counts_by_class") or {}
    observer_severities = observer_incidents.get("counts_by_severity") or {}
    observer_packets = (snapshot.get("panels") or {}).get("observer_packets") or {}
    packet_counts = observer_packets.get("counts") or {}
    packet_kinds = observer_packets.get("counts_by_kind") or {}
    managed_promotion_blocks = (
        actionable_observer_total > 0
        and int(observer_classes.get("promotion_contamination") or 0) == actionable_observer_total
        and int(observer_severities.get("medium") or 0) == actionable_observer_total
        and int(packet_kinds.get("repair_plan") or 0) >= actionable_observer_total
    )
    checks.append(
        DoctorCheck(
            "watchtower-observer-incidents",
            actionable_observer_total == 0 or managed_promotion_blocks,
            (
                f"total={observer_total} "
                f"actionable={actionable_observer_total} "
                f"distinct_classes={int(observer_counts.get('distinct_classes') or 0)}"
                f"{' managed_by_repair_plans=yes' if managed_promotion_blocks else ''}"
            ),
        )
    )
    packet_total = int(packet_counts.get("total") or 0)
    checks.append(
        DoctorCheck(
            "watchtower-observer-packets",
            packet_total >= observer_total,
            f"packets={packet_total} incidents={observer_total}",
        )
    )
    checks.append(
        DoctorCheck(
            "watchtower-observer-packet-kinds",
            packet_total == 0 or int(packet_counts.get("distinct_kinds") or 0) >= 3,
            (
                f"distinct_kinds={int(packet_counts.get('distinct_kinds') or 0)} "
                f"self_observation={int(packet_kinds.get('self_observation') or 0)} "
                f"incident_report={int(packet_kinds.get('incident_report') or 0)} "
                f"repair_plan={int(packet_kinds.get('repair_plan') or 0)} "
                f"reflection_digest={int(packet_kinds.get('reflection_digest') or 0)}"
            ),
        )
    )
    observer_handoffs = (snapshot.get("panels") or {}).get("observer_handoffs") or {}
    handoff_counts = observer_handoffs.get("counts") or {}
    checks.append(
        DoctorCheck(
            "watchtower-observer-handoffs",
            int(handoff_counts.get("problematic") or 0) == 0,
            (
                f"total={int(handoff_counts.get('total') or 0)} "
                f"completed={int(handoff_counts.get('completed') or 0)} "
                f"failed={int(handoff_counts.get('failed') or 0)} "
                f"blocked={int(handoff_counts.get('blocked') or 0)} "
                f"stalled={int(handoff_counts.get('stalled') or 0)}"
            ),
        )
    )
    personality_panel = (snapshot.get("panels") or {}).get("personality") or {}
    personality_counts = personality_panel.get("counts") or {}
    checks.append(
        DoctorCheck(
            "watchtower-personality-mirrors",
            int(personality_counts.get("mirror_drift") or 0) == 0,
            (
                f"trait_profiles={int(personality_counts.get('trait_profiles') or 0)} "
                f"observation_rows={int(personality_counts.get('observation_rows') or 0)} "
                f"evolution_rows={int(personality_counts.get('evolution_rows') or 0)} "
                f"mirror_drift={int(personality_counts.get('mirror_drift') or 0)}"
            ),
        )
    )
    personality_import = personality_panel.get("personality_import") or {}
    identity_panel = (snapshot.get("panels") or {}).get("agent_identity") or {}
    identity_counts = identity_panel.get("counts") or {}
    personality_enabled = bool(config_manager.get_path("spark.personality.enabled", default=True))
    personality_import_ready = bool(personality_import.get("ready"))
    canonical_agent_count = int(identity_counts.get("canonical_agents") or 0)
    personality_import_required = personality_enabled and canonical_agent_count > 0
    checks.append(
        DoctorCheck(
            "watchtower-personality-import",
            (not personality_import_required) or personality_import_ready,
            (
                f"enabled={'yes' if personality_enabled else 'no'} "
                f"required={'yes' if personality_import_required else 'no'} "
                f"canonical_agents={canonical_agent_count} "
                f"personality_import_ready={'yes' if personality_import_ready else 'no'} "
                f"available_personality_hook_chips={len(personality_import.get('available_chip_keys') or [])} "
                f"active_personality_hook_chips={len(personality_import.get('active_chip_keys') or [])}"
            ),
        )
    )
    checks.append(
        DoctorCheck(
            "watchtower-agent-identity",
            int(identity_counts.get("identity_conflicts") or 0) == 0,
            (
                f"canonical_agents={int(identity_counts.get('canonical_agents') or 0)} "
                f"builder_local={int(identity_counts.get('builder_local') or 0)} "
                f"spark_swarm={int(identity_counts.get('spark_swarm') or 0)} "
                f"aliases={int(identity_counts.get('aliases') or 0)} "
                f"identity_conflicts={int(identity_counts.get('identity_conflicts') or 0)}"
            ),
        )
    )
    identity_import = identity_panel.get("identity_import") or {}
    builder_local_count = int(identity_counts.get("builder_local") or 0)
    identity_import_ready = bool(identity_import.get("ready"))
    checks.append(
        DoctorCheck(
            "watchtower-agent-identity-import",
            builder_local_count == 0 or identity_import_ready,
            (
                f"builder_local={builder_local_count} "
                f"identity_import_ready={'yes' if identity_import_ready else 'no'} "
                f"available_identity_hook_chips={len(identity_import.get('available_chip_keys') or [])} "
                f"active_identity_hook_chips={len(identity_import.get('active_chip_keys') or [])}"
            ),
        )
    )
    return checks
