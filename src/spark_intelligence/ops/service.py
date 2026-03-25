from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from spark_intelligence.identity.service import LOCAL_OPERATOR_HUMAN_ID
from spark_intelligence.state.db import StateDB


@dataclass
class OperatorEventReport:
    rows: list[dict[str, Any]]

    def to_json(self) -> str:
        return json.dumps({"rows": self.rows}, indent=2)

    def to_text(self) -> str:
        if not self.rows:
            return "No operator events recorded."
        lines = ["Operator history:"]
        for row in self.rows:
            lines.append(
                f"- {row['created_at']} actor={row['actor_human_id']} action={row['action']} "
                f"target={row['target_kind']}:{row['target_ref']} "
                f"reason={row['reason'] or 'none'}"
            )
        return "\n".join(lines)


def log_operator_event(
    *,
    state_db: StateDB,
    action: str,
    target_kind: str,
    target_ref: str,
    reason: str | None = None,
    details: dict[str, Any] | None = None,
    actor_human_id: str = LOCAL_OPERATOR_HUMAN_ID,
) -> None:
    details_json = json.dumps(details, sort_keys=True) if details else None
    with state_db.connect() as conn:
        conn.execute(
            """
            INSERT INTO operator_events(actor_human_id, action, target_kind, target_ref, reason, details_json)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (actor_human_id, action, target_kind, target_ref, reason, details_json),
        )
        conn.commit()


def list_operator_events(state_db: StateDB, *, limit: int = 20) -> OperatorEventReport:
    with state_db.connect() as conn:
        rows = conn.execute(
            """
            SELECT event_id, actor_human_id, action, target_kind, target_ref, reason, details_json, created_at
            FROM operator_events
            ORDER BY event_id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    payload: list[dict[str, Any]] = []
    for row in rows:
        details = None
        if row["details_json"]:
            try:
                details = json.loads(row["details_json"])
            except json.JSONDecodeError:
                details = {"raw": row["details_json"]}
        payload.append(
            {
                "event_id": row["event_id"],
                "actor_human_id": row["actor_human_id"],
                "action": row["action"],
                "target_kind": row["target_kind"],
                "target_ref": row["target_ref"],
                "reason": row["reason"],
                "details": details,
                "created_at": row["created_at"],
            }
        )
    return OperatorEventReport(rows=payload)
