from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from spark_intelligence.auth.service import run_oauth_refresh_maintenance
from spark_intelligence.auth.runtime import AuthStatusReport, build_auth_status_report
from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.memory.orchestrator import run_memory_sdk_maintenance
from spark_intelligence.observability.store import close_run, open_run, record_environment_snapshot
from spark_intelligence.state.db import StateDB


OAUTH_MAINTENANCE_JOB_ID = "auth:oauth-refresh-maintenance"
MEMORY_MAINTENANCE_JOB_ID = "memory:sdk-maintenance"
OAUTH_MAINTENANCE_STALE_SECONDS = 900


@dataclass(frozen=True)
class JobRecord:
    job_id: str
    job_kind: str
    status: str
    schedule_expr: str | None
    last_run_at: str | None
    last_result: str | None


def jobs_tick(config_manager: ConfigManager, state_db: StateDB) -> str:
    record_environment_snapshot(
        state_db,
        surface="jobs_tick",
        summary="Jobs tick environment snapshot recorded.",
        provider_id=str(config_manager.get_path("providers.default_provider")) if config_manager.get_path("providers.default_provider") else None,
        runtime_root=str(config_manager.get_path("spark.researcher.runtime_root")) if config_manager.get_path("spark.researcher.runtime_root") else None,
        config_path=str(config_manager.get_path("spark.researcher.config_path")) if config_manager.get_path("spark.researcher.config_path") else None,
        env_refs={"jobs_scheduler_enabled": bool(config_manager.get_path("jobs.scheduler.enabled", default=True))},
        facts={"origin_surface": "jobs_tick"},
    )
    with state_db.connect() as conn:
        jobs = conn.execute(
            "SELECT job_id, job_kind, status FROM job_records WHERE status = 'scheduled' ORDER BY job_id"
        ).fetchall()
    if not jobs:
        return "No scheduled jobs due. Scheduler harness is healthy."
    lines = [f"Ran {len(jobs)} scheduled job(s)."]
    for job in jobs:
        result = _run_job(
            config_manager=config_manager,
            state_db=state_db,
            job_id=str(job["job_id"]),
            job_kind=str(job["job_kind"]),
        )
        lines.append(f"- {job['job_id']} kind={job['job_kind']} result={result}")
    return "\n".join(lines)


def jobs_list(state_db: StateDB) -> str:
    jobs = list_job_records(state_db)
    if not jobs:
        return "No jobs configured."

    lines = ["Jobs:"]
    for job in jobs:
        lines.append(
            f"- {job.job_id} kind={job.job_kind} status={job.status} "
            f"schedule={job.schedule_expr or 'none'} last_run={job.last_run_at or 'never'} "
            f"last_result={job.last_result or 'none'}"
        )
    return "\n".join(lines)


def list_job_records(state_db: StateDB) -> list[JobRecord]:
    with state_db.connect() as conn:
        rows = conn.execute(
            "SELECT job_id, job_kind, status, schedule_expr, last_run_at, last_result FROM job_records ORDER BY job_id"
        ).fetchall()
    return [
        JobRecord(
            job_id=str(row["job_id"]),
            job_kind=str(row["job_kind"]),
            status=str(row["status"]),
            schedule_expr=str(row["schedule_expr"]) if row["schedule_expr"] else None,
            last_run_at=str(row["last_run_at"]) if row["last_run_at"] else None,
            last_result=str(row["last_result"]) if row["last_result"] else None,
        )
        for row in rows
    ]


def oauth_maintenance_health(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    stale_seconds: int = OAUTH_MAINTENANCE_STALE_SECONDS,
) -> tuple[bool, str]:
    auth_report = build_auth_status_report(config_manager=config_manager, state_db=state_db)
    return oauth_maintenance_health_from_report(
        state_db=state_db,
        auth_report=auth_report,
        stale_seconds=stale_seconds,
    )


def oauth_maintenance_health_from_report(
    *,
    state_db: StateDB,
    auth_report: AuthStatusReport,
    stale_seconds: int = OAUTH_MAINTENANCE_STALE_SECONDS,
) -> tuple[bool, str]:
    oauth_providers = [provider for provider in auth_report.providers if provider.auth_method == "oauth"]
    if not oauth_providers:
        return True, "no oauth providers configured"

    expiring_soon = [provider.provider_id for provider in oauth_providers if provider.status == "expiring_soon"]
    if not expiring_soon:
        job = _get_job_record(state_db=state_db, job_id=OAUTH_MAINTENANCE_JOB_ID)
        if not job:
            return False, "oauth maintenance job is missing"
        return True, f"operator-driven via jobs tick last_run={job.last_run_at or 'never'}"

    job = _get_job_record(state_db=state_db, job_id=OAUTH_MAINTENANCE_JOB_ID)
    if not job:
        return False, f"oauth maintenance job is missing; expiring_soon={','.join(expiring_soon)}"
    if not job.last_run_at:
        return False, f"oauth maintenance has never run; expiring_soon={','.join(expiring_soon)}"
    if _timestamp_is_stale(job.last_run_at, stale_seconds=stale_seconds):
        return False, (
            f"oauth maintenance is stale last_run={job.last_run_at}; "
            f"expiring_soon={','.join(expiring_soon)}"
        )
    return True, (
        f"operator-driven via jobs tick last_run={job.last_run_at}; "
        f"expiring_soon={','.join(expiring_soon)}"
    )


def _run_job(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    job_id: str,
    job_kind: str,
) -> str:
    run = open_run(
        state_db,
        run_kind=f"job:{job_kind}",
        origin_surface="jobs_tick",
        summary=f"Job run opened for {job_id}.",
        request_id=job_id,
        actor_id="jobs_tick",
        reason_code="scheduled_job_execution",
        facts={"job_id": job_id, "job_kind": job_kind},
    )
    try:
        if job_kind == "oauth_refresh_maintenance":
            payload = run_oauth_refresh_maintenance(config_manager=config_manager, state_db=state_db)
            result = (
                f"scanned={payload['scanned']} due={payload['due']} "
                f"refreshed={len(payload['refreshed'])} failed={len(payload['failed'])} "
                f"skipped={len(payload['skipped'])}"
            )
            _record_job_result(state_db=state_db, job_id=job_id, result=result)
            close_run(
                state_db,
                run_id=run.run_id,
                status="closed",
                close_reason="job_completed",
                summary=f"Job {job_id} completed.",
                facts={"job_id": job_id, "job_kind": job_kind, "result": result},
            )
            return result
        if job_kind == "memory_sdk_maintenance":
            payload = run_memory_sdk_maintenance(
                config_manager=config_manager,
                state_db=state_db,
                actor_id="jobs_tick",
            )
            result = (
                f"status={payload.status} "
                f"before={payload.maintenance.get('manual_observations_before', 0)} "
                f"after={payload.maintenance.get('manual_observations_after', 0)} "
                f"deletions={payload.maintenance.get('active_deletion_count', 0)} "
                f"still_current={payload.maintenance.get('active_state_still_current_count', 0)} "
                f"stale_preserved={payload.maintenance.get('active_state_stale_preserved_count', 0)} "
                f"superseded={payload.maintenance.get('active_state_superseded_count', 0)} "
                f"archived={payload.maintenance.get('active_state_archived_count', 0)}"
            )
            _record_job_result(state_db=state_db, job_id=job_id, result=result)
            close_run(
                state_db,
                run_id=run.run_id,
                status="closed" if payload.status == "succeeded" else "stalled",
                close_reason="job_completed" if payload.status == "succeeded" else "memory_maintenance_abstained",
                summary=f"Job {job_id} completed.",
                facts={
                    "job_id": job_id,
                    "job_kind": job_kind,
                    "result": result,
                    "maintenance_status": payload.status,
                    "maintenance": payload.maintenance,
                    "reason": payload.reason,
                },
            )
            return result
        result = "unsupported_job_kind"
        _record_job_result(state_db=state_db, job_id=job_id, result=result)
        close_run(
            state_db,
            run_id=run.run_id,
            status="stalled",
            close_reason="unsupported_job_kind",
            summary=f"Job {job_id} could not be executed.",
            facts={"job_id": job_id, "job_kind": job_kind, "result": result},
        )
        return result
    except Exception as exc:
        close_run(
            state_db,
            run_id=run.run_id,
            status="failed",
            close_reason="job_exception",
            summary=f"Job {job_id} raised an exception.",
            facts={"job_id": job_id, "job_kind": job_kind, "error": str(exc)},
        )
        raise


def _record_job_result(*, state_db: StateDB, job_id: str, result: str) -> None:
    with state_db.connect() as conn:
        conn.execute(
            """
            UPDATE job_records
            SET last_run_at = ?, last_result = ?, updated_at = CURRENT_TIMESTAMP
            WHERE job_id = ?
            """,
            (_utc_now_iso(), result, job_id),
        )
        conn.commit()


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _get_job_record(*, state_db: StateDB, job_id: str) -> JobRecord | None:
    with state_db.connect() as conn:
        row = conn.execute(
            """
            SELECT job_id, job_kind, status, schedule_expr, last_run_at, last_result
            FROM job_records
            WHERE job_id = ?
            LIMIT 1
            """,
            (job_id,),
        ).fetchone()
    if not row:
        return None
    return JobRecord(
        job_id=str(row["job_id"]),
        job_kind=str(row["job_kind"]),
        status=str(row["status"]),
        schedule_expr=str(row["schedule_expr"]) if row["schedule_expr"] else None,
        last_run_at=str(row["last_run_at"]) if row["last_run_at"] else None,
        last_result=str(row["last_result"]) if row["last_result"] else None,
    )


def _timestamp_is_stale(value: str, *, stale_seconds: int) -> bool:
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return True
    return timestamp <= datetime.now(UTC) - timedelta(seconds=stale_seconds)
