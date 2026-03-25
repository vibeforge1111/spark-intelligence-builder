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
        attachments = self.payload.get("attachments")
        if isinstance(attachments, dict):
            lines.append(
                "- attachments: "
                f"{attachments.get('chip_count', 0)} chips ({attachments.get('chip_source', 'unknown')}), "
                f"{attachments.get('path_count', 0)} paths ({attachments.get('path_source', 'unknown')}), "
                f"warnings={attachments.get('warning_count', 0)}"
            )
            lines.append(
                "- attachment state: "
                f"active_chips={', '.join(attachments.get('active_chip_keys', [])) if attachments.get('active_chip_keys') else 'none'}, "
                f"pinned_chips={', '.join(attachments.get('pinned_chip_keys', [])) if attachments.get('pinned_chip_keys') else 'none'}, "
                f"active_path={attachments.get('active_path_key') or 'none'}"
            )
            lines.append(f"- attachment snapshot: {attachments.get('snapshot_path') or 'missing'}")
        return "\n".join(lines)

    def to_json(self) -> str:
        return json.dumps(self.payload, indent=2)


@dataclass
class InboundResolution:
    allowed: bool
    decision: str
    human_id: str | None
    agent_id: str | None
    session_id: str | None
    response_text: str


@dataclass
class PairingQueueReport:
    rows: list[dict]

    def to_text(self) -> str:
        if not self.rows:
            return "No pending or held pairings."
        lines = ["Pairing review queue:"]
        for row in self.rows:
            lines.append(
                f"- {row['channel_id']}:{row['external_user_id']} status={row['status']} "
                f"human={row['human_id']} approved_by={row['approved_by'] or 'none'} "
                f"updated_at={row['updated_at']}"
            )
        return "\n".join(lines)

    def to_json(self) -> str:
        return json.dumps({"rows": self.rows}, indent=2)


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
            "DELETE FROM allowlist_entries WHERE channel_id = ? AND external_user_id = ? AND role = 'paired_user'",
            (channel_id, external_user_id),
        )
        conn.execute(
            "INSERT INTO allowlist_entries(channel_id, external_user_id, role) VALUES (?, ?, 'paired_user')",
            (channel_id, external_user_id),
        )
        conn.commit()

    return f"Approved pairing for {channel_id}:{external_user_id} -> {human_id}"


def resolve_inbound_dm(
    *,
    state_db: StateDB,
    channel_id: str,
    external_user_id: str,
    display_name: str,
) -> InboundResolution:
    session_id = _canonical_session_id(channel_id, external_user_id)
    human_id = _canonical_human_id(channel_id, external_user_id)
    agent_id = _canonical_agent_id(human_id)

    with state_db.connect() as conn:
        channel_row = conn.execute(
            "SELECT channel_id, pairing_mode, status FROM channel_installations WHERE channel_id = ? LIMIT 1",
            (channel_id,),
        ).fetchone()
        if not channel_row:
            return InboundResolution(
                allowed=False,
                decision="blocked",
                human_id=None,
                agent_id=None,
                session_id=None,
                response_text="Channel is not configured.",
            )

        channel_status = str(channel_row["status"] or "enabled")
        if channel_status == "disabled":
            return InboundResolution(
                allowed=False,
                decision="channel_disabled",
                human_id=None,
                agent_id=None,
                session_id=None,
                response_text="This channel is disabled by the operator.",
            )
        if channel_status == "paused":
            return InboundResolution(
                allowed=False,
                decision="channel_paused",
                human_id=None,
                agent_id=None,
                session_id=None,
                response_text="This channel is temporarily paused by the operator.",
            )

        allow_row = conn.execute(
            """
            SELECT 1
            FROM allowlist_entries
            WHERE channel_id = ? AND external_user_id = ? AND role = 'paired_user'
            LIMIT 1
            """,
            (channel_id, external_user_id),
        ).fetchone()

        session_row = conn.execute(
            """
            SELECT session_id
            FROM session_bindings
            WHERE session_id = ? AND status = 'active'
            LIMIT 1
            """,
            (session_id,),
        ).fetchone()

        pairing_mode = channel_row["pairing_mode"]
        pairing_row = conn.execute(
            """
            SELECT status
            FROM pairing_records
            WHERE pairing_id = ?
            LIMIT 1
            """,
            (f"pairing:{channel_id}:{external_user_id}",),
        ).fetchone()
        if allow_row and session_row:
            return InboundResolution(
                allowed=True,
                decision="allowed",
                human_id=human_id,
                agent_id=agent_id,
                session_id=session_id,
                response_text="Authorized DM routed to canonical session.",
            )

        if allow_row and not session_row:
            approve_pairing(
                state_db=state_db,
                channel_id=channel_id,
                external_user_id=external_user_id,
                display_name=display_name,
            )
            return InboundResolution(
                allowed=True,
                decision="allowed",
                human_id=human_id,
                agent_id=agent_id,
                session_id=session_id,
                response_text="Authorized DM restored its canonical session.",
            )

        if pairing_mode == "pairing":
            if pairing_row and pairing_row["status"] == "held":
                return InboundResolution(
                    allowed=False,
                    decision="held",
                    human_id=human_id,
                    agent_id=None,
                    session_id=None,
                    response_text="Pairing request is currently on hold pending operator review.",
                )
            if pairing_row and pairing_row["status"] == "revoked":
                return InboundResolution(
                    allowed=False,
                    decision="revoked",
                    human_id=human_id,
                    agent_id=None,
                    session_id=None,
                    response_text="Access for this pairing has been revoked by the operator.",
                )
            pairing_id = f"pairing:{channel_id}:{external_user_id}"
            conn.execute(
                """
                INSERT INTO pairing_records(pairing_id, channel_id, external_user_id, human_id, status, approved_by)
                VALUES (?, ?, ?, ?, 'pending', NULL)
                ON CONFLICT(pairing_id) DO UPDATE SET status='pending', updated_at=CURRENT_TIMESTAMP
                """,
                (pairing_id, channel_id, external_user_id, human_id),
            )
            conn.commit()
            return InboundResolution(
                allowed=False,
                decision="pending_pairing",
                human_id=human_id,
                agent_id=None,
                session_id=None,
                response_text="Unauthorized DM. Pairing approval is required before this agent will respond.",
            )

        return InboundResolution(
            allowed=False,
            decision="blocked",
            human_id=None,
            agent_id=None,
            session_id=None,
            response_text="Unauthorized DM. This channel requires explicit allowlist access.",
        )


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


def hold_pairing(*, state_db: StateDB, channel_id: str, external_user_id: str, held_by: str = LOCAL_OPERATOR_HUMAN_ID) -> str:
    _require_operator(state_db, held_by)
    human_id = _canonical_human_id(channel_id, external_user_id)
    pairing_id = f"pairing:{channel_id}:{external_user_id}"
    with state_db.connect() as conn:
        conn.execute(
            """
            INSERT INTO pairing_records(pairing_id, channel_id, external_user_id, human_id, status, approved_by)
            VALUES (?, ?, ?, ?, 'held', ?)
            ON CONFLICT(pairing_id) DO UPDATE SET
                human_id=excluded.human_id,
                status='held',
                approved_by=excluded.approved_by,
                updated_at=CURRENT_TIMESTAMP
            """,
            (pairing_id, channel_id, external_user_id, human_id, held_by),
        )
        conn.execute(
            "DELETE FROM allowlist_entries WHERE channel_id = ? AND external_user_id = ?",
            (channel_id, external_user_id),
        )
        conn.commit()
    return f"Held pairing for {channel_id}:{external_user_id}"


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


def review_pairings(state_db: StateDB) -> PairingQueueReport:
    with state_db.connect() as conn:
        rows = conn.execute(
            """
            SELECT pairing_id, channel_id, external_user_id, human_id, status, approved_by, approved_at, updated_at
            FROM pairing_records
            WHERE status IN ('pending', 'held')
            ORDER BY
                CASE status WHEN 'pending' THEN 0 WHEN 'held' THEN 1 ELSE 2 END,
                updated_at DESC,
                channel_id,
                external_user_id
            """
        ).fetchall()
    return PairingQueueReport(rows=[dict(row) for row in rows])


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
