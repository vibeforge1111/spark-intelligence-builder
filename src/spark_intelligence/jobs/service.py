from __future__ import annotations

from datetime import UTC, datetime

from spark_intelligence.auth.service import run_oauth_refresh_maintenance
from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.state.db import StateDB


def jobs_tick(config_manager: ConfigManager, state_db: StateDB) -> str:
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
    with state_db.connect() as conn:
        jobs = conn.execute(
            "SELECT job_id, job_kind, status, schedule_expr, last_run_at FROM job_records ORDER BY job_id"
        ).fetchall()
    if not jobs:
        return "No jobs configured."

    lines = ["Jobs:"]
    for job in jobs:
        lines.append(
            f"- {job['job_id']} kind={job['job_kind']} status={job['status']} "
            f"schedule={job['schedule_expr'] or 'none'} last_run={job['last_run_at'] or 'never'}"
        )
    return "\n".join(lines)


def _run_job(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    job_id: str,
    job_kind: str,
) -> str:
    if job_kind == "oauth_refresh_maintenance":
        payload = run_oauth_refresh_maintenance(config_manager=config_manager, state_db=state_db)
        result = (
            f"scanned={payload['scanned']} due={payload['due']} "
            f"refreshed={len(payload['refreshed'])} failed={len(payload['failed'])} "
            f"skipped={len(payload['skipped'])}"
        )
        _record_job_result(state_db=state_db, job_id=job_id, result=result)
        return result
    result = "unsupported_job_kind"
    _record_job_result(state_db=state_db, job_id=job_id, result=result)
    return result


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
