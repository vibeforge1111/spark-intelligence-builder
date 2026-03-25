from __future__ import annotations

import json
from dataclasses import dataclass

from spark_intelligence.state.db import StateDB


LOCAL_OPERATOR_HUMAN_ID = "local-operator"


@dataclass
class IdentityReport:
    payload: dict

    def to_text(self) -> str:
        lines = [
            "Spark Intelligence agent inspect",
            f"- workspace owner: {self.payload['workspace_owner']}",
            f"- operators: {self.payload['operator_count']}",
            f"- humans: {self.payload['human_count']}",
            f"- agents: {self.payload['agent_count']}",
            f"- pairings: {self.payload['pairing_count']}",
            f"- active sessions: {self.payload['active_session_count']}",
            f"- providers: {', '.join(self.payload['providers']) if self.payload['providers'] else 'none'}",
            f"- channels: {', '.join(self.payload['channels']) if self.payload['channels'] else 'none'}",
        ]
        return "\n".join(lines)

    def to_json(self) -> str:
        return json.dumps(self.payload, indent=2)


def _require_operator(state_db: StateDB, human_id: str = LOCAL_OPERATOR_HUMAN_ID) -> None:
    with state_db.connect() as conn:
        row = conn.execute(
            "SELECT 1 FROM workspace_roles WHERE human_id = ? AND role = 'operator_admin' LIMIT 1",
            (human_id,),
        ).fetchone()
    if not row:
        raise RuntimeError(f"human '{human_id}' does not have operator_admin authority")


def _canonical_human_id(channel_id: str, external_user_id: str) -> str:
    return f"human:{channel_id}:{external_user_id}"


def _canonical_agent_id(human_id: str) -> str:
    return f"agent:{human_id}"


def _canonical_channel_account_id(channel_id: str, external_user_id: str) -> str:
    return f"acct:{channel_id}:{external_user_id}"


def _canonical_surface_id(channel_id: str, external_user_id: str) -> str:
    return f"surface:{channel_id}:dm:{external_user_id}"


def _canonical_session_id(channel_id: str, external_user_id: str) -> str:
    return f"session:{channel_id}:dm:{external_user_id}"


def approve_pairing(
    *,
    state_db: StateDB,
    channel_id: str,
    external_user_id: str,
    display_name: str | None = None,
    approved_by: str = LOCAL_OPERATOR_HUMAN_ID,
) -> str:
    _require_operator(state_db, approved_by)
    human_id = _canonical_human_id(channel_id, external_user_id)
    agent_id = _canonical_agent_id(human_id)
    account_id = _canonical_channel_account_id(channel_id, external_user_id)
    surface_id = _canonical_surface_id(channel_id, external_user_id)
    session_id = _canonical_session_id(channel_id, external_user_id)
    pairing_id = f"pairing:{channel_id}:{external_user_id}"

    resolved_name = display_name or f"{channel_id} user {external_user_id}"

    with state_db.connect() as conn:
        conn.execute(
            """
            INSERT INTO humans(human_id, display_name, status)
            VALUES (?, ?, 'active')
            ON CONFLICT(human_id) DO UPDATE SET display_name=excluded.display_name, status='active', updated_at=CURRENT_TIMESTAMP
            """,
            (human_id, resolved_name),
        )
        conn.execute(
            """
            INSERT INTO agent_identities(agent_id, human_id, spark_profile, status)
            VALUES (?, ?, 'default', 'active')
            ON CONFLICT(agent_id) DO UPDATE SET status='active', updated_at=CURRENT_TIMESTAMP
            """,
            (agent_id, human_id),
        )
        conn.execute(
            """
            INSERT INTO channel_accounts(account_id, channel_id, external_user_id, external_username, status)
            VALUES (?, ?, ?, ?, 'active')
            ON CONFLICT(account_id) DO UPDATE SET external_username=excluded.external_username, status='active', updated_at=CURRENT_TIMESTAMP
            """,
            (account_id, channel_id, external_user_id, resolved_name),
        )
        conn.execute(
            """
            INSERT INTO identity_bindings(binding_id, human_id, account_id, verified, status)
            VALUES (?, ?, ?, 1, 'active')
            ON CONFLICT(binding_id) DO UPDATE SET verified=1, status='active', updated_at=CURRENT_TIMESTAMP
            """,
            (f"binding:{channel_id}:{external_user_id}", human_id, account_id),
        )
        conn.execute(
            """
            INSERT INTO conversation_surfaces(surface_id, channel_id, surface_kind, external_surface_id, status)
            VALUES (?, ?, 'dm', ?, 'active')
            ON CONFLICT(surface_id) DO UPDATE SET status='active', updated_at=CURRENT_TIMESTAMP
            """,
            (surface_id, channel_id, external_user_id),
        )
        conn.execute(
            """
            INSERT INTO session_bindings(session_id, agent_id, surface_id, channel_id, external_user_id, session_mode, status)
            VALUES (?, ?, ?, ?, ?, 'dm', 'active')
            ON CONFLICT(session_id) DO UPDATE SET status='active', updated_at=CURRENT_TIMESTAMP
            """,
            (session_id, agent_id, surface_id, channel_id, external_user_id),
        )
        conn.execute(
            """
            INSERT INTO pairing_records(pairing_id, channel_id, external_user_id, human_id, status, approved_by)
            VALUES (?, ?, ?, ?, 'approved', ?)
            ON CONFLICT(pairing_id) DO UPDATE SET
                human_id=excluded.human_id,
                status='approved',
                approved_by=excluded.approved_by,
                approved_at=CURRENT_TIMESTAMP,
                updated_at=CURRENT_TIMESTAMP
            """,
            (pairing_id, channel_id, external_user_id, human_id, approved_by),
        )
        conn.execute(
            """
            INSERT INTO allowlist_entries(channel_id, external_user_id, role)
            VALUES (?, ?, 'paired_user')
            """,
            (channel_id, external_user_id),
        )
        conn.commit()

    return f"Approved pairing for {channel_id}:{external_user_id} -> {human_id}"


def revoke_pairing(*, state_db: StateDB, channel_id: str, external_user_id: str, revoked_by: str = LOCAL_OPERATOR_HUMAN_ID) -> str:
    _require_operator(state_db, revoked_by)
    human_id = _canonical_human_id(channel_id, external_user_id)
    session_id = _canonical_session_id(channel_id, external_user_id)
    pairing_id = f"pairing:{channel_id}:{external_user_id}"

    with state_db.connect() as conn:
        conn.execute(
            "UPDATE pairing_records SET status='revoked', updated_at=CURRENT_TIMESTAMP WHERE pairing_id = ?",
            (pairing_id,),
        )
        conn.execute(
            "UPDATE session_bindings SET status='revoked', updated_at=CURRENT_TIMESTAMP WHERE session_id = ?",
            (session_id,),
        )
        conn.execute(
            "DELETE FROM allowlist_entries WHERE channel_id = ? AND external_user_id = ?",
            (channel_id, external_user_id),
        )
        conn.execute(
            "UPDATE humans SET status='revoked', updated_at=CURRENT_TIMESTAMP WHERE human_id = ?",
            (human_id,),
        )
        conn.commit()

    return f"Revoked pairing for {channel_id}:{external_user_id}"


def list_pairings(state_db: StateDB) -> str:
    with state_db.connect() as conn:
        rows = conn.execute(
            """
            SELECT pairing_id, channel_id, external_user_id, human_id, status, approved_by, approved_at
            FROM pairing_records
            ORDER BY channel_id, external_user_id
            """
        ).fetchall()
    if not rows:
        return "No pairings recorded."
    lines = ["Pairings:"]
    for row in rows:
        lines.append(
            f"- {row['channel_id']}:{row['external_user_id']} "
            f"human={row['human_id']} status={row['status']} approved_by={row['approved_by']} "
            f"approved_at={row['approved_at']}"
        )
    return "\n".join(lines)


def list_sessions(state_db: StateDB) -> str:
    with state_db.connect() as conn:
        rows = conn.execute(
            """
            SELECT session_id, channel_id, external_user_id, session_mode, status
            FROM session_bindings
            ORDER BY session_id
            """
        ).fetchall()
    if not rows:
        return "No sessions recorded."
    lines = ["Sessions:"]
    for row in rows:
        lines.append(
            f"- {row['session_id']} channel={row['channel_id']} external_user={row['external_user_id']} "
            f"mode={row['session_mode']} status={row['status']}"
        )
    return "\n".join(lines)


def revoke_session(*, state_db: StateDB, session_id: str, revoked_by: str = LOCAL_OPERATOR_HUMAN_ID) -> str:
    _require_operator(state_db, revoked_by)
    with state_db.connect() as conn:
        conn.execute(
            "UPDATE session_bindings SET status='revoked', updated_at=CURRENT_TIMESTAMP WHERE session_id = ?",
            (session_id,),
        )
        conn.commit()
    return f"Revoked session {session_id}"


def agent_inspect(*, state_db: StateDB, workspace_owner: str) -> IdentityReport:
    with state_db.connect() as conn:
        operator_count = conn.execute(
            "SELECT COUNT(*) AS c FROM workspace_roles WHERE role = 'operator_admin'"
        ).fetchone()["c"]
        human_count = conn.execute(
            "SELECT COUNT(*) AS c FROM humans WHERE human_id != ?",
            (LOCAL_OPERATOR_HUMAN_ID,),
        ).fetchone()["c"]
        agent_count = conn.execute("SELECT COUNT(*) AS c FROM agent_identities").fetchone()["c"]
        pairing_count = conn.execute(
            "SELECT COUNT(*) AS c FROM pairing_records WHERE status = 'approved'"
        ).fetchone()["c"]
        active_session_count = conn.execute(
            "SELECT COUNT(*) AS c FROM session_bindings WHERE status = 'active'"
        ).fetchone()["c"]
        providers = [row["provider_id"] for row in conn.execute("SELECT provider_id FROM provider_records ORDER BY provider_id")]
        channels = [row["channel_id"] for row in conn.execute("SELECT channel_id FROM channel_installations ORDER BY channel_id")]
    payload = {
        "workspace_owner": workspace_owner,
        "operator_count": operator_count,
        "human_count": human_count,
        "agent_count": agent_count,
        "pairing_count": pairing_count,
        "active_session_count": active_session_count,
        "providers": providers,
        "channels": channels,
    }
    return IdentityReport(payload=payload)
