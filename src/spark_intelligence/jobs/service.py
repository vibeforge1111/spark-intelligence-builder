from __future__ import annotations

from spark_intelligence.state.db import StateDB


def jobs_tick(state_db: StateDB) -> str:
    with state_db.connect() as conn:
        jobs = conn.execute(
            "SELECT job_id, job_kind, status FROM job_records WHERE status = 'scheduled' ORDER BY job_id"
        ).fetchall()
    if not jobs:
        return "No scheduled jobs due. Scheduler harness is healthy."
    return f"Found {len(jobs)} scheduled job(s). Execution wiring lands in a later phase."


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
