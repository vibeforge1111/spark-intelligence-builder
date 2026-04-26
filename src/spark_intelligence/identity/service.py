from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from spark_intelligence.state.db import StateDB
from spark_intelligence.state.hygiene import JSON_RICHNESS_MERGE_GUARD, upsert_runtime_state


LOCAL_OPERATOR_HUMAN_ID = "local-operator"
PUBLIC_AUTH_DENIED_RESPONSE = "Access is not authorized for this channel. Ask the operator to review access."


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
            f"- canonical agents: {self.payload.get('canonical_agent_count', 0)}",
            f"- swarm-linked agents: {self.payload.get('swarm_link_count', 0)}",
            f"- identity conflicts: {self.payload.get('identity_conflict_count', 0)}",
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
            summary = (
                f"- {row['channel_id']}:{row['external_user_id']} status={row['status']} "
                f"human={row['human_id']} approved_by={row['approved_by'] or 'none'} "
                f"updated_at={row['updated_at']}"
            )
            context = row.get("context") or {}
            detail_parts: list[str] = []
            if context.get("display_name"):
                detail_parts.append(f"display_name={context['display_name']}")
            if context.get("telegram_username"):
                detail_parts.append(f"telegram_username=@{context['telegram_username']}")
            if context.get("chat_id"):
                detail_parts.append(f"chat_id={context['chat_id']}")
            if context.get("last_message_text"):
                detail_parts.append(f"last_message={context['last_message_text']}")
            if detail_parts:
                summary = f"{summary} {' '.join(detail_parts)}"
            lines.append(summary)
        return "\n".join(lines)

    def to_json(self) -> str:
        return json.dumps({"rows": self.rows}, indent=2)


@dataclass
class PairingSummaryReport:
    channel_id: str
    counts: dict[str, int]
    latest_pending: dict[str, Any] | None
    latest_held: dict[str, Any] | None
    latest_approved: dict[str, Any] | None

    def to_text(self) -> str:
        lines = [f"Pairing summary: {self.channel_id}"]
        lines.append(
            "- counts: "
            f"pending={self.counts.get('pending', 0)} "
            f"held={self.counts.get('held', 0)} "
            f"approved={self.counts.get('approved', 0)} "
            f"revoked={self.counts.get('revoked', 0)}"
        )
        lines.extend(_format_pairing_summary_block("latest_pending", self.latest_pending))
        lines.extend(_format_pairing_summary_block("latest_held", self.latest_held))
        lines.extend(_format_pairing_summary_block("latest_approved", self.latest_approved))
        return "\n".join(lines)

    def to_json(self) -> str:
        return json.dumps(
            {
                "channel_id": self.channel_id,
                "counts": self.counts,
                "latest_pending": self.latest_pending,
                "latest_held": self.latest_held,
                "latest_approved": self.latest_approved,
            },
            indent=2,
        )


@dataclass
class CanonicalAgentReport:
    payload: dict[str, Any]

    def to_text(self) -> str:
        identity = self.payload.get("identity") or {}
        lines = ["Canonical agent identity:"]
        lines.append(f"- human_id: {identity.get('human_id') or 'unknown'}")
        lines.append(f"- agent_id: {identity.get('agent_id') or 'unknown'}")
        lines.append(f"- agent_name: {identity.get('agent_name') or 'unknown'}")
        lines.append(
            f"- source: {identity.get('preferred_source') or 'builder_local'} "
            f"origin={identity.get('origin') or 'builder_local'} "
            f"status={identity.get('status') or 'active'}"
        )
        if identity.get("external_system") or identity.get("external_agent_id"):
            lines.append(
                f"- external: system={identity.get('external_system') or 'none'} "
                f"agent_id={identity.get('external_agent_id') or 'none'}"
            )
        if identity.get("conflict_agent_id"):
            lines.append(
                f"- conflict: agent_id={identity.get('conflict_agent_id')} "
                f"reason={identity.get('conflict_reason') or 'unknown'}"
            )
        alias_ids = identity.get("alias_agent_ids") or []
        lines.append(f"- aliases: {', '.join(alias_ids) if alias_ids else 'none'}")
        sessions = self.payload.get("sessions") or []
        lines.append(f"- active_sessions: {len(sessions)}")
        for session in sessions[:5]:
            lines.append(
                f"  {session.get('session_id')} channel={session.get('channel_id')} "
                f"user={session.get('external_user_id')} status={session.get('status')}"
            )
        renames = self.payload.get("rename_history") or []
        if renames:
            lines.append("- rename_history:")
            for row in renames[:5]:
                lines.append(
                    f"  {row.get('created_at')} {row.get('old_name') or 'none'} -> {row.get('new_name')} "
                    f"via {row.get('source_surface')}"
                )
        return "\n".join(lines)

    def to_json(self) -> str:
        return json.dumps(self.payload, indent=2)


@dataclass
class CanonicalAgentState:
    human_id: str
    agent_id: str
    agent_name: str
    origin: str
    status: str
    preferred_source: str
    external_system: str | None = None
    external_agent_id: str | None = None
    conflict_agent_id: str | None = None
    conflict_reason: str | None = None
    alias_agent_ids: list[str] | None = None
    name_updated_at: str | None = None
    name_source: str | None = None

    @property
    def has_user_defined_name(self) -> bool:
        """True when the agent has a real, user-supplied name."""
        return bool(self.agent_name and self.agent_name.strip())

    def to_payload(self) -> dict[str, Any]:
        return {
            "human_id": self.human_id,
            "agent_id": self.agent_id,
            "agent_name": self.agent_name,
            "has_user_defined_name": self.has_user_defined_name,
            "origin": self.origin,
            "status": self.status,
            "preferred_source": self.preferred_source,
            "external_system": self.external_system,
            "external_agent_id": self.external_agent_id,
            "conflict_agent_id": self.conflict_agent_id,
            "conflict_reason": self.conflict_reason,
            "alias_agent_ids": list(self.alias_agent_ids or []),
            "name_updated_at": self.name_updated_at,
            "name_source": self.name_source,
        }


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


# ────────────────────────────────────────────────────────────────────
# Identity aliasing — one primary identity across multiple channels.
#
# When a user wants to talk to the same Spark from multiple surfaces
# (Telegram + TUI + Discord + …) without each surface spawning its
# own agent, we register an *alias*: a row in identity_aliases that
# maps a (channel, external_user) pair to a primary (channel, user)'s
# canonical human_id and agent_id.
#
# Resolution: when resolve_inbound_dm is asked about an aliased user,
# it returns the *primary* human_id and agent_id (so personality and
# memory are shared) but keeps the *alias* session_id (so conversation
# threads stay independent).
# ────────────────────────────────────────────────────────────────────


@dataclass
class IdentityAlias:
    """A row in identity_aliases."""

    alias_channel: str
    alias_external_user: str
    primary_channel: str
    primary_external_user: str
    primary_human_id: str
    primary_agent_id: str


def _resolve_alias(
    state_db: StateDB,
    channel_id: str,
    external_user_id: str,
) -> IdentityAlias | None:
    """Look up the alias row for a (channel, user) — None if not aliased."""
    with state_db.connect() as conn:
        row = conn.execute(
            """
            SELECT alias_channel, alias_external_user,
                   primary_channel, primary_external_user,
                   primary_human_id, primary_agent_id
            FROM identity_aliases
            WHERE alias_channel = ? AND alias_external_user = ?
            LIMIT 1
            """,
            (channel_id, external_user_id),
        ).fetchone()
    if row is None:
        return None
    return IdentityAlias(
        alias_channel=str(row["alias_channel"]),
        alias_external_user=str(row["alias_external_user"]),
        primary_channel=str(row["primary_channel"]),
        primary_external_user=str(row["primary_external_user"]),
        primary_human_id=str(row["primary_human_id"]),
        primary_agent_id=str(row["primary_agent_id"]),
    )


def link_identity_alias(
    *,
    state_db: StateDB,
    primary_channel: str,
    primary_external_user: str,
    alias_channel: str,
    alias_external_user: str,
    created_by: str = "system",
) -> IdentityAlias:
    """Make (alias_channel, alias_user) resolve to (primary_channel, primary_user)'s identity.

    The primary's canonical human_id and agent_id are stored in the alias row
    so future lookups don't need to recompute them. If a row already exists
    for this (alias_channel, alias_external_user) it is overwritten.

    Also updates any existing session_bindings for the alias surface to
    point at the primary agent_id, so a session created before linking
    immediately starts resolving to the shared agent on the next message.

    Idempotent. Returns the resulting IdentityAlias.
    """
    primary_human = _canonical_human_id(primary_channel, primary_external_user)
    primary_agent = _canonical_agent_id(primary_human)
    with state_db.connect() as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO identity_aliases
                (alias_channel, alias_external_user,
                 primary_channel, primary_external_user,
                 primary_human_id, primary_agent_id, created_by)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                alias_channel,
                alias_external_user,
                primary_channel,
                primary_external_user,
                primary_human,
                primary_agent,
                created_by,
            ),
        )
        # Re-point any existing session_bindings for the alias surface so
        # in-flight sessions immediately start using the primary agent.
        conn.execute(
            """
            UPDATE session_bindings
            SET agent_id = ?, updated_at = CURRENT_TIMESTAMP
            WHERE channel_id = ?
              AND external_user_id = ?
              AND agent_id != ?
            """,
            (primary_agent, alias_channel, alias_external_user, primary_agent),
        )
        conn.commit()
    return IdentityAlias(
        alias_channel=alias_channel,
        alias_external_user=alias_external_user,
        primary_channel=primary_channel,
        primary_external_user=primary_external_user,
        primary_human_id=primary_human,
        primary_agent_id=primary_agent,
    )


def unlink_identity_alias(
    *,
    state_db: StateDB,
    alias_channel: str,
    alias_external_user: str,
) -> bool:
    """Remove an alias. Returns True if a row was deleted."""
    with state_db.connect() as conn:
        cur = conn.execute(
            "DELETE FROM identity_aliases WHERE alias_channel = ? AND alias_external_user = ?",
            (alias_channel, alias_external_user),
        )
        conn.commit()
        return cur.rowcount > 0


def list_identity_aliases(state_db: StateDB) -> list[IdentityAlias]:
    """All registered aliases — for `spark identity list` and inspection."""
    with state_db.connect() as conn:
        rows = conn.execute(
            """
            SELECT alias_channel, alias_external_user,
                   primary_channel, primary_external_user,
                   primary_human_id, primary_agent_id
            FROM identity_aliases
            ORDER BY primary_human_id, alias_channel, alias_external_user
            """
        ).fetchall()
    return [
        IdentityAlias(
            alias_channel=str(r["alias_channel"]),
            alias_external_user=str(r["alias_external_user"]),
            primary_channel=str(r["primary_channel"]),
            primary_external_user=str(r["primary_external_user"]),
            primary_human_id=str(r["primary_human_id"]),
            primary_agent_id=str(r["primary_agent_id"]),
        )
        for r in rows
    ]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_iso_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _reanchor_builder_persona_rows(
    *,
    conn: Any,
    human_id: str,
    builder_agent_id: str,
    candidate_agent_ids: list[str],
) -> None:
    source_agent_ids: list[str] = []
    for candidate in candidate_agent_ids:
        normalized = str(candidate or "").strip()
        if normalized and normalized != builder_agent_id and normalized not in source_agent_ids:
            source_agent_ids.append(normalized)
    if not source_agent_ids:
        return

    placeholders = ",".join("?" for _ in source_agent_ids)
    target_row = conn.execute(
        """
        SELECT agent_id
        FROM agent_persona_profiles
        WHERE agent_id = ?
        LIMIT 1
        """,
        (builder_agent_id,),
    ).fetchone()
    source_row = conn.execute(
        f"""
        SELECT agent_id, persona_name, persona_summary, base_traits_json, behavioral_rules_json, provenance_json, updated_at, created_at
        FROM agent_persona_profiles
        WHERE agent_id IN ({placeholders})
        ORDER BY updated_at DESC, created_at DESC
        LIMIT 1
        """,
        tuple(source_agent_ids),
    ).fetchone()
    if target_row is None and source_row is not None:
        conn.execute(
            """
            INSERT INTO agent_persona_profiles(
                agent_id, persona_name, persona_summary, base_traits_json, behavioral_rules_json, provenance_json, updated_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                builder_agent_id,
                source_row["persona_name"],
                source_row["persona_summary"],
                source_row["base_traits_json"],
                source_row["behavioral_rules_json"],
                source_row["provenance_json"],
                source_row["updated_at"],
                source_row["created_at"],
            ),
        )

    conn.execute(
        f"""
        UPDATE agent_persona_mutations
        SET agent_id = ?
        WHERE human_id = ?
          AND agent_id IN ({placeholders})
        """,
        (builder_agent_id, human_id, *source_agent_ids),
    )
    conn.execute(
        f"""
        UPDATE operator_events
        SET target_ref = ?
        WHERE action = 'import_personality'
          AND target_kind = 'agent_persona'
          AND target_ref IN ({placeholders})
        """,
        (builder_agent_id, *source_agent_ids),
    )
    conn.execute(
        f"""
        DELETE FROM agent_persona_profiles
        WHERE agent_id IN ({placeholders})
        """,
        tuple(source_agent_ids),
    )


def _choose_agent_name(
    *,
    current_name: str | None,
    current_confirmed_at: str | None,
    current_source: str | None,
    incoming_name: str | None,
    incoming_confirmed_at: str | None,
    incoming_source: str,
) -> tuple[str | None, str | None, str | None]:
    resolved_incoming_name = _read_optional_text(incoming_name)
    if not resolved_incoming_name:
        return current_name, current_confirmed_at, current_source
    current_ts = _parse_iso_datetime(current_confirmed_at)
    incoming_ts = _parse_iso_datetime(incoming_confirmed_at)
    if current_name and current_ts is not None and incoming_ts is not None and incoming_ts < current_ts:
        return current_name, current_confirmed_at, current_source
    return (
        resolved_incoming_name,
        incoming_confirmed_at or current_confirmed_at or _utc_now_iso(),
        incoming_source,
    )


def _canonical_channel_account_id(channel_id: str, external_user_id: str) -> str:
    return f"acct:{channel_id}:{external_user_id}"


def _canonical_surface_id(channel_id: str, external_user_id: str) -> str:
    return f"surface:{channel_id}:dm:{external_user_id}"


def _canonical_session_id(channel_id: str, external_user_id: str) -> str:
    return f"session:{channel_id}:dm:{external_user_id}"


def _normalize_external_user_id(channel_id: str, external_user_id: str) -> str | None:
    value = str(external_user_id)
    if channel_id == "telegram":
        if value != value.strip():
            return None
        if not value.isdecimal():
            return None
        if int(value) <= 0:
            return None
    return value


def resolve_canonical_agent_identity(
    *,
    state_db: StateDB,
    human_id: str,
    display_name: str | None = None,
) -> CanonicalAgentState:
    local_agent_id = _canonical_agent_id(human_id)
    # Do not invent an agent name from the human display name or a default.
    # An empty agent_name means the agent still needs a user-defined name.
    resolved_name = (display_name or "").strip()

    with state_db.connect() as conn:
        link_row = conn.execute(
            """
            SELECT canonical_agent_id, preferred_source, status, conflict_agent_id, conflict_reason
            FROM canonical_agent_links
            WHERE human_id = ?
            LIMIT 1
            """,
            (human_id,),
        ).fetchone()

        if link_row is None:
            conn.execute(
                """
                INSERT INTO canonical_agent_links(
                    human_id, canonical_agent_id, preferred_source, status, conflict_agent_id, conflict_reason
                ) VALUES (?, ?, 'builder_local', 'active', NULL, NULL)
                """,
                (human_id, local_agent_id),
            )
            link_row = conn.execute(
                """
                SELECT canonical_agent_id, preferred_source, status, conflict_agent_id, conflict_reason
                FROM canonical_agent_links
                WHERE human_id = ?
                LIMIT 1
                """,
                (human_id,),
            ).fetchone()

        canonical_agent_id = str(link_row["canonical_agent_id"] or local_agent_id)
        profile_row = conn.execute(
            """
            SELECT agent_id, human_id, agent_name, origin, status, external_system, external_agent_id, name_updated_at, name_source
            FROM agent_profiles
            WHERE agent_id = ?
            LIMIT 1
            """,
            (canonical_agent_id,),
        ).fetchone()

        if profile_row is None:
            origin = "builder_local" if canonical_agent_id == local_agent_id else "spark_swarm"
            external_system = "spark_swarm" if canonical_agent_id != local_agent_id else None
            external_agent_id = canonical_agent_id if canonical_agent_id != local_agent_id else None
            name_updated_at = _utc_now_iso()
            conn.execute(
                """
                INSERT INTO agent_profiles(
                    agent_id, human_id, agent_name, origin, status, external_system, external_agent_id, name_updated_at, name_source
                ) VALUES (?, ?, ?, ?, 'active', ?, ?, ?, ?)
                """,
                (
                    canonical_agent_id,
                    human_id,
                    resolved_name,
                    origin,
                    external_system,
                    external_agent_id,
                    name_updated_at,
                    origin,
                ),
            )
            profile_row = conn.execute(
                """
                SELECT agent_id, human_id, agent_name, origin, status, external_system, external_agent_id, name_updated_at, name_source
                FROM agent_profiles
                WHERE agent_id = ?
                LIMIT 1
                """,
                (canonical_agent_id,),
            ).fetchone()
        elif canonical_agent_id == local_agent_id and not str(profile_row["agent_name"] or "").strip() and resolved_name:
            conn.execute(
                """
                UPDATE agent_profiles
                SET agent_name = ?, name_updated_at = ?, name_source = 'builder_local', updated_at = CURRENT_TIMESTAMP
                WHERE agent_id = ?
                """,
                (resolved_name, _utc_now_iso(), canonical_agent_id),
            )
            profile_row = conn.execute(
                """
                SELECT agent_id, human_id, agent_name, origin, status, external_system, external_agent_id, name_updated_at, name_source
                FROM agent_profiles
                WHERE agent_id = ?
                LIMIT 1
                """,
                (canonical_agent_id,),
            ).fetchone()

        conn.execute(
            """
            INSERT INTO agent_identities(agent_id, human_id, spark_profile, status)
            VALUES (?, ?, 'default', 'active')
            ON CONFLICT(agent_id) DO UPDATE SET
                human_id=excluded.human_id,
                status='active',
                updated_at=CURRENT_TIMESTAMP
            """,
            (canonical_agent_id, human_id),
        )
        conn.commit()

    return read_canonical_agent_state(state_db=state_db, human_id=human_id)


def read_canonical_agent_state(
    *,
    state_db: StateDB,
    human_id: str,
) -> CanonicalAgentState:
    with state_db.connect() as conn:
        link_row = conn.execute(
            """
            SELECT canonical_agent_id, preferred_source, status, conflict_agent_id, conflict_reason
            FROM canonical_agent_links
            WHERE human_id = ?
            LIMIT 1
            """,
            (human_id,),
        ).fetchone()
        if link_row is None:
            return resolve_canonical_agent_identity(state_db=state_db, human_id=human_id)

        canonical_agent_id = str(link_row["canonical_agent_id"])
        profile_row = conn.execute(
            """
            SELECT agent_id, human_id, agent_name, origin, status, external_system, external_agent_id, name_updated_at, name_source
            FROM agent_profiles
            WHERE agent_id = ?
            LIMIT 1
            """,
            (canonical_agent_id,),
        ).fetchone()
        if profile_row is None:
            return resolve_canonical_agent_identity(state_db=state_db, human_id=human_id)
        alias_rows = conn.execute(
            """
            SELECT alias_agent_id
            FROM agent_identity_aliases
            WHERE canonical_agent_id = ?
            ORDER BY alias_agent_id
            """,
            (canonical_agent_id,),
        ).fetchall()

    return CanonicalAgentState(
        human_id=str(profile_row["human_id"]),
        agent_id=str(profile_row["agent_id"]),
        agent_name=str(profile_row["agent_name"] or ""),
        origin=str(profile_row["origin"] or "builder_local"),
        status=str(link_row["status"] or profile_row["status"] or "active"),
        preferred_source=str(link_row["preferred_source"] or "builder_local"),
        external_system=_read_optional_text(profile_row["external_system"]),
        external_agent_id=_read_optional_text(profile_row["external_agent_id"]),
        conflict_agent_id=_read_optional_text(link_row["conflict_agent_id"]),
        conflict_reason=_read_optional_text(link_row["conflict_reason"]),
        alias_agent_ids=[str(row["alias_agent_id"]) for row in alias_rows],
        name_updated_at=_read_optional_text(profile_row["name_updated_at"]),
        name_source=_read_optional_text(profile_row["name_source"]),
    )


def link_spark_swarm_agent(
    *,
    state_db: StateDB,
    human_id: str,
    swarm_agent_id: str,
    agent_name: str,
    confirmed_at: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> CanonicalAgentState:
    if not swarm_agent_id.strip():
        raise ValueError("Spark Swarm agent_id must not be empty.")
    local_agent_id = _canonical_agent_id(human_id)
    incoming_name = agent_name.strip() or "Spark Agent"
    incoming_confirmed_at = confirmed_at or _utc_now_iso()
    existing = resolve_canonical_agent_identity(state_db=state_db, human_id=human_id)
    metadata_json = json.dumps(metadata, sort_keys=True) if metadata else None

    with state_db.connect() as conn:
        existing_swarm_row = conn.execute(
            """
            SELECT agent_name, name_updated_at, name_source
            FROM agent_profiles
            WHERE agent_id = ?
            LIMIT 1
            """,
            (swarm_agent_id,),
        ).fetchone()
        current_name = existing.agent_name
        current_confirmed_at = existing.name_updated_at
        current_source = existing.name_source
        if existing_swarm_row:
            current_name, current_confirmed_at, current_source = _choose_agent_name(
                current_name=current_name,
                current_confirmed_at=current_confirmed_at,
                current_source=current_source,
                incoming_name=existing_swarm_row["agent_name"],
                incoming_confirmed_at=existing_swarm_row["name_updated_at"],
                incoming_source=str(existing_swarm_row["name_source"] or "spark_swarm"),
            )
        chosen_name, chosen_confirmed_at, chosen_source = _choose_agent_name(
            current_name=current_name,
            current_confirmed_at=current_confirmed_at,
            current_source=current_source,
            incoming_name=incoming_name,
            incoming_confirmed_at=incoming_confirmed_at,
            incoming_source="spark_swarm",
        )
        conn.execute(
            """
            INSERT INTO agent_profiles(
                agent_id, human_id, agent_name, origin, status, external_system, external_agent_id, metadata_json, name_updated_at, name_source
            ) VALUES (?, ?, ?, 'spark_swarm', 'active', 'spark_swarm', ?, ?, ?, ?)
            ON CONFLICT(agent_id) DO UPDATE SET
                human_id=excluded.human_id,
                agent_name=excluded.agent_name,
                origin='spark_swarm',
                status='active',
                external_system='spark_swarm',
                external_agent_id=excluded.external_agent_id,
                metadata_json=COALESCE(excluded.metadata_json, agent_profiles.metadata_json),
                name_updated_at=excluded.name_updated_at,
                name_source=excluded.name_source,
                updated_at=CURRENT_TIMESTAMP
            """,
            (
                swarm_agent_id,
                human_id,
                chosen_name or "Spark Agent",
                swarm_agent_id,
                metadata_json,
                chosen_confirmed_at or incoming_confirmed_at,
                chosen_source or "spark_swarm",
            ),
        )
        conn.execute(
            """
            INSERT INTO agent_identities(agent_id, human_id, spark_profile, status)
            VALUES (?, ?, 'default', 'active')
            ON CONFLICT(agent_id) DO UPDATE SET
                human_id=excluded.human_id,
                status='active',
                updated_at=CURRENT_TIMESTAMP
            """,
            (swarm_agent_id, human_id),
        )

        next_status = "active"
        conflict_agent_id = None
        conflict_reason = None
        if existing.agent_id not in {local_agent_id, swarm_agent_id}:
            next_status = "identity_conflict"
            conflict_agent_id = existing.agent_id
            conflict_reason = "multiple_agent_ids_for_human"
        else:
            if existing.agent_id != swarm_agent_id:
                conn.execute(
                    """
                    INSERT INTO agent_identity_aliases(alias_agent_id, canonical_agent_id, alias_kind, reason_code)
                    VALUES (?, ?, 'superseded_local', 'spark_swarm_link')
                    ON CONFLICT(alias_agent_id) DO UPDATE SET
                        canonical_agent_id=excluded.canonical_agent_id,
                        alias_kind=excluded.alias_kind,
                        reason_code=excluded.reason_code
                    """,
                    (existing.agent_id, swarm_agent_id),
                )
                conn.execute(
                    """
                    UPDATE agent_profiles
                    SET status = 'linked', updated_at = CURRENT_TIMESTAMP
                    WHERE agent_id = ?
                    """,
                    (existing.agent_id,),
                )
                conn.execute(
                    """
                    UPDATE agent_identities
                    SET status = 'linked', updated_at = CURRENT_TIMESTAMP
                    WHERE agent_id = ?
                    """,
                    (existing.agent_id,),
                )
                conn.execute(
                    """
                    UPDATE session_bindings
                    SET agent_id = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE agent_id = ?
                    """,
                    (swarm_agent_id, existing.agent_id),
                )
        _reanchor_builder_persona_rows(
            conn=conn,
            human_id=human_id,
            builder_agent_id=local_agent_id,
            candidate_agent_ids=[existing.agent_id, swarm_agent_id],
        )

        conn.execute(
            """
            INSERT INTO canonical_agent_links(
                human_id, canonical_agent_id, preferred_source, status, conflict_agent_id, conflict_reason
            ) VALUES (?, ?, 'spark_swarm', ?, ?, ?)
            ON CONFLICT(human_id) DO UPDATE SET
                canonical_agent_id=excluded.canonical_agent_id,
                preferred_source=excluded.preferred_source,
                status=excluded.status,
                conflict_agent_id=excluded.conflict_agent_id,
                conflict_reason=excluded.conflict_reason,
                updated_at=CURRENT_TIMESTAMP
            """,
            (
                human_id,
                swarm_agent_id,
                next_status,
                conflict_agent_id,
                conflict_reason,
            ),
        )
        conn.commit()
    return read_canonical_agent_state(state_db=state_db, human_id=human_id)


def rename_agent_identity(
    *,
    state_db: StateDB,
    human_id: str,
    new_name: str,
    source_surface: str,
    source_ref: str | None = None,
) -> CanonicalAgentState:
    resolved_name = new_name.strip()
    if not resolved_name:
        raise ValueError("Agent name must not be empty.")
    state = resolve_canonical_agent_identity(state_db=state_db, human_id=human_id)
    if state.agent_name == resolved_name:
        return state
    rename_id = f"agent-rename-{uuid4().hex[:12]}"
    recorded_at = _utc_now_iso()
    with state_db.connect() as conn:
        conn.execute(
            """
            UPDATE agent_profiles
            SET agent_name = ?, name_updated_at = ?, name_source = ?, updated_at = CURRENT_TIMESTAMP
            WHERE agent_id = ?
            """,
            (resolved_name, recorded_at, source_surface, state.agent_id),
        )
        # Keep the persona profile's display name in sync with the
        # canonical agent_profiles row. Without this update the two
        # tables drift: rename hits agent_profiles but persona_profiles
        # keeps whatever was written during onboarding, so downstream
        # readers that still look at persona_name (TUI welcome banner,
        # some logging paths) display the stale value. The UPDATE only
        # runs if a persona profile row exists for this agent — agents
        # without an onboarded persona stay untouched.
        conn.execute(
            """
            UPDATE agent_persona_profiles
            SET persona_name = ?, updated_at = ?
            WHERE agent_id = ?
            """,
            (resolved_name, recorded_at, state.agent_id),
        )
        conn.execute(
            """
            INSERT INTO agent_rename_history(
                rename_id, agent_id, human_id, old_name, new_name, source_surface, source_ref, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                rename_id,
                state.agent_id,
                human_id,
                state.agent_name,
                resolved_name,
                source_surface,
                source_ref,
                recorded_at,
            ),
        )
        conn.commit()
    return read_canonical_agent_state(state_db=state_db, human_id=human_id)


def cancel_agent_onboarding(
    *,
    state_db: StateDB,
    human_id: str,
    source_ref: str | None = None,
) -> CanonicalAgentState:
    """Wipe the agent name back to the empty-string sentinel.

    This is the identity-layer half of the Q-E `/cancel` flow
    (docs/PERSONALITY_ONBOARDING_V2_DESIGN_2026-04-10.md §11, P2-2 in
    docs/PERSONALITY_PHASE2_PLAN_2026-04-10.md). The caller is also
    responsible for clearing the onboarding state blob from
    runtime_state (see personality.loader._delete_agent_onboarding_state)
    and for the user-facing cancel reply.

    Unlike rename_agent_identity, this function deliberately permits the
    empty-string target because Phase 1 uses "" as the sentinel for
    "no user-defined name yet" (CanonicalAgentState.has_user_defined_name
    depends on it). Persona profile is left untouched per Q-J default.

    No-op if the agent already has no name.
    """
    state = resolve_canonical_agent_identity(state_db=state_db, human_id=human_id)
    if not state.agent_name:
        return state
    rename_id = f"agent-rename-{uuid4().hex[:12]}"
    recorded_at = _utc_now_iso()
    source_surface = "onboarding_cancel"
    with state_db.connect() as conn:
        conn.execute(
            """
            UPDATE agent_profiles
            SET agent_name = '', name_updated_at = ?, name_source = ?, updated_at = CURRENT_TIMESTAMP
            WHERE agent_id = ?
            """,
            (recorded_at, source_surface, state.agent_id),
        )
        conn.execute(
            """
            INSERT INTO agent_rename_history(
                rename_id, agent_id, human_id, old_name, new_name, source_surface, source_ref, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                rename_id,
                state.agent_id,
                human_id,
                state.agent_name,
                "",
                source_surface,
                source_ref,
                recorded_at,
            ),
        )
        conn.commit()
    return read_canonical_agent_state(state_db=state_db, human_id=human_id)


def list_agent_rename_history(
    *,
    state_db: StateDB,
    human_id: str,
    limit: int = 20,
) -> list[dict[str, Any]]:
    with state_db.connect() as conn:
        rows = conn.execute(
            """
            SELECT rename_id, agent_id, human_id, old_name, new_name, source_surface, source_ref, created_at
            FROM agent_rename_history
            WHERE human_id = ?
            ORDER BY created_at DESC, rename_id DESC
            LIMIT ?
            """,
            (human_id, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def set_human_user_address(
    *,
    state_db: StateDB,
    human_id: str,
    user_address: str | None,
) -> str | None:
    normalized: str | None = (user_address or "").strip() or None
    with state_db.connect() as conn:
        conn.execute(
            "UPDATE humans SET user_address = ?, updated_at = CURRENT_TIMESTAMP WHERE human_id = ?",
            (normalized, human_id),
        )
        conn.commit()
    return normalized


def get_human_user_address(
    *,
    state_db: StateDB,
    human_id: str,
) -> str | None:
    with state_db.connect() as conn:
        row = conn.execute(
            "SELECT user_address FROM humans WHERE human_id = ? LIMIT 1",
            (human_id,),
        ).fetchone()
    if row is None:
        return None
    value = row["user_address"]
    if value in (None, ""):
        return None
    return str(value)


def inspect_canonical_agent(
    *,
    state_db: StateDB,
    human_id: str,
) -> CanonicalAgentReport:
    state = read_canonical_agent_state(state_db=state_db, human_id=human_id)
    with state_db.connect() as conn:
        session_rows = conn.execute(
            """
            SELECT session_id, channel_id, external_user_id, session_mode, status, updated_at
            FROM session_bindings
            WHERE agent_id = ?
            ORDER BY updated_at DESC, session_id DESC
            """,
            (state.agent_id,),
        ).fetchall()
    payload = {
        "identity": state.to_payload(),
        "sessions": [dict(row) for row in session_rows],
        "rename_history": list_agent_rename_history(state_db=state_db, human_id=human_id, limit=20),
    }
    return CanonicalAgentReport(payload=payload)


def build_spark_swarm_identity_import_payload(
    *,
    state_db: StateDB,
    human_id: str,
    workspace_id: str | None = None,
) -> dict[str, Any]:
    state = read_canonical_agent_state(state_db=state_db, human_id=human_id)
    with state_db.connect() as conn:
        session_rows = conn.execute(
            """
            SELECT session_id, channel_id, external_user_id, session_mode, status, updated_at
            FROM session_bindings
            WHERE agent_id = ?
            ORDER BY updated_at DESC, session_id DESC
            """,
            (state.agent_id,),
        ).fetchall()
        pairing_rows = conn.execute(
            """
            SELECT pairing_id, channel_id, external_user_id, status, approved_at, approved_by, updated_at
            FROM pairing_records
            WHERE human_id = ?
            ORDER BY updated_at DESC, pairing_id DESC
            """,
            (human_id,),
        ).fetchall()
    return {
        "schema_version": "spark-swarm-agent-import-request.v1",
        "hook": "identity",
        "requested_at": _utc_now_iso(),
        "workspace_id": workspace_id or "default",
        "human_id": human_id,
        "current_identity": state.to_payload(),
        "sessions": [dict(row) for row in session_rows],
        "pairings": [dict(row) for row in pairing_rows],
    }


def normalize_spark_swarm_identity_import(
    *,
    human_id: str,
    hook_output: dict[str, Any],
) -> dict[str, Any]:
    result = hook_output.get("result")
    if not isinstance(result, dict):
        raise ValueError("Spark Swarm identity hook must return a JSON object under result.")

    result_human_id = str(result.get("human_id") or human_id).strip() or human_id
    if result_human_id != human_id:
        raise ValueError(
            f"Spark Swarm identity hook returned human_id '{result_human_id}' but expected '{human_id}'."
        )

    external_system = str(result.get("external_system") or "spark_swarm").strip() or "spark_swarm"
    if external_system != "spark_swarm":
        raise ValueError(
            f"Spark Swarm identity hook returned unsupported external_system '{external_system}'."
        )

    swarm_agent_id = str(result.get("swarm_agent_id") or result.get("agent_id") or "").strip()
    if not swarm_agent_id:
        raise ValueError("Spark Swarm identity hook must return a non-empty swarm_agent_id.")

    agent_name = str(result.get("agent_name") or result.get("display_name") or "").strip() or "Spark Agent"
    confirmed_at = str(result.get("confirmed_at") or "").strip() or None
    metadata = result.get("metadata")
    if metadata is None:
        metadata_dict: dict[str, Any] = {}
    elif isinstance(metadata, dict):
        metadata_dict = dict(metadata)
    else:
        raise ValueError("Spark Swarm identity hook metadata must be a JSON object when provided.")

    return {
        "human_id": human_id,
        "swarm_agent_id": swarm_agent_id,
        "agent_name": agent_name,
        "confirmed_at": confirmed_at,
        "external_system": external_system,
        "metadata": metadata_dict,
    }


def _activate_channel_access(
    *,
    state_db: StateDB,
    channel_id: str,
    external_user_id: str,
    display_name: str,
) -> tuple[str, str, str]:
    # Identity aliasing: if (channel, user) is registered as an alias for
    # a primary identity, the human_id we use for the humans / bindings /
    # session_binding rows is the *primary's* human_id, not the canonical
    # per-channel one. This is what makes personality and memory shared
    # across surfaces — they all key off the same human_id.
    #
    # The session_id stays per-channel so independent threads remain
    # independent.
    alias = _resolve_alias(state_db, channel_id, external_user_id)
    if alias is not None:
        human_id = alias.primary_human_id
    else:
        human_id = _canonical_human_id(channel_id, external_user_id)
    account_id = _canonical_channel_account_id(channel_id, external_user_id)
    surface_id = _canonical_surface_id(channel_id, external_user_id)
    session_id = _canonical_session_id(channel_id, external_user_id)

    with state_db.connect() as conn:
        conn.execute(
            """
            INSERT INTO humans(human_id, display_name, status)
            VALUES (?, ?, 'active')
            ON CONFLICT(human_id) DO UPDATE SET display_name=excluded.display_name, status='active', updated_at=CURRENT_TIMESTAMP
            """,
            (human_id, display_name),
        )
        conn.execute(
            """
            INSERT INTO channel_accounts(account_id, channel_id, external_user_id, external_username, status)
            VALUES (?, ?, ?, ?, 'active')
            ON CONFLICT(account_id) DO UPDATE SET external_username=excluded.external_username, status='active', updated_at=CURRENT_TIMESTAMP
            """,
            (account_id, channel_id, external_user_id, display_name),
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
        conn.commit()

    # NOTE: intentionally do NOT pass display_name through to the agent
    # identity resolution. display_name here is the HUMAN's label (goes into
    # humans / channel_accounts above), not the agent's chosen name. Leaking
    # it into agent_name was Finding G of
    # docs/PERSONALITY_PHASE1_AUDIT_2026-04-10.md. The agent starts with an
    # empty agent_name and is named via onboarding (see
    # personality/loader.py:maybe_handle_agent_persona_onboarding_turn).
    agent_state = resolve_canonical_agent_identity(
        state_db=state_db,
        human_id=human_id,
    )
    agent_id = agent_state.agent_id

    with state_db.connect() as conn:
        conn.execute(
            """
            INSERT INTO session_bindings(session_id, agent_id, surface_id, channel_id, external_user_id, session_mode, status)
            VALUES (?, ?, ?, ?, ?, 'dm', 'active')
            ON CONFLICT(session_id) DO UPDATE SET status='active', updated_at=CURRENT_TIMESTAMP
            """,
            (session_id, agent_id, surface_id, channel_id, external_user_id),
        )
        conn.commit()

    return human_id, agent_id, session_id


def approve_pairing(
    *,
    state_db: StateDB,
    channel_id: str,
    external_user_id: str,
    display_name: str | None = None,
    approved_by: str = LOCAL_OPERATOR_HUMAN_ID,
) -> str:
    _require_operator(state_db, approved_by)
    pairing_id = f"pairing:{channel_id}:{external_user_id}"

    resolved_name = display_name or f"{channel_id} user {external_user_id}"
    human_id, _, _ = _activate_channel_access(
        state_db=state_db,
        channel_id=channel_id,
        external_user_id=external_user_id,
        display_name=resolved_name,
    )

    with state_db.connect() as conn:
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
        conn.execute(
            """
            INSERT INTO runtime_state(state_key, value)
            VALUES (?, '1')
            ON CONFLICT(state_key) DO UPDATE SET value='1', updated_at=CURRENT_TIMESTAMP
            """,
            (_pairing_welcome_state_key(channel_id, external_user_id),),
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
    normalized_external_user_id = _normalize_external_user_id(channel_id, external_user_id)
    if normalized_external_user_id is None:
        return InboundResolution(
            allowed=False,
            decision="blocked",
            human_id=None,
            agent_id=None,
            session_id=None,
            response_text=PUBLIC_AUTH_DENIED_RESPONSE,
        )
    external_user_id = normalized_external_user_id
    session_id = _canonical_session_id(channel_id, external_user_id)

    # Identity aliasing: if this (channel, user) is registered as an alias
    # for a primary identity, use the primary's human_id (and indirectly
    # the primary's agent_id via the session_binding row that
    # _activate_channel_access creates). The session_id stays per-channel
    # so independent threads remain independent.
    alias = _resolve_alias(state_db, channel_id, external_user_id)
    if alias is not None:
        human_id = alias.primary_human_id
    else:
        human_id = _canonical_human_id(channel_id, external_user_id)

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
                response_text=PUBLIC_AUTH_DENIED_RESPONSE,
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
            SELECT role
            FROM allowlist_entries
            WHERE channel_id = ? AND external_user_id = ? AND role IN ('paired_user', 'configured_user')
            LIMIT 1
            """,
            (channel_id, external_user_id),
        ).fetchone()

        session_row = conn.execute(
            """
            SELECT session_id, agent_id
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
                agent_id=str(session_row["agent_id"]),
                session_id=session_id,
                response_text="Authorized DM routed to canonical session.",
            )

        if allow_row and not session_row:
            allow_role = str(allow_row["role"])
            if allow_role == "paired_user":
                _, restored_agent_id, _ = _activate_channel_access(
                    state_db=state_db,
                    channel_id=channel_id,
                    external_user_id=external_user_id,
                    display_name=display_name,
                )
            else:
                _, restored_agent_id, _ = _activate_channel_access(
                    state_db=state_db,
                    channel_id=channel_id,
                    external_user_id=external_user_id,
                    display_name=display_name,
                )
            return InboundResolution(
                allowed=True,
                decision="allowed",
                human_id=human_id,
                agent_id=restored_agent_id,
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
                    response_text=PUBLIC_AUTH_DENIED_RESPONSE,
                )
            if pairing_row and pairing_row["status"] == "revoked":
                return InboundResolution(
                    allowed=False,
                    decision="revoked",
                    human_id=human_id,
                    agent_id=None,
                    session_id=None,
                    response_text=PUBLIC_AUTH_DENIED_RESPONSE,
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
                response_text=PUBLIC_AUTH_DENIED_RESPONSE,
            )

        return InboundResolution(
            allowed=False,
            decision="blocked",
            human_id=None,
            agent_id=None,
            session_id=None,
            response_text=PUBLIC_AUTH_DENIED_RESPONSE,
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
            "DELETE FROM allowlist_entries WHERE channel_id = ? AND external_user_id = ? AND role = 'paired_user'",
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
            "DELETE FROM allowlist_entries WHERE channel_id = ? AND external_user_id = ? AND role = 'paired_user'",
            (channel_id, external_user_id),
        )
        conn.commit()
    return f"Held pairing for {channel_id}:{external_user_id}"


def record_pairing_context(
    *,
    state_db: StateDB,
    channel_id: str,
    external_user_id: str,
    context: dict[str, Any],
) -> None:
    state_key = _pairing_context_state_key(channel_id, external_user_id)
    with state_db.connect() as conn:
        upsert_runtime_state(
            conn,
            state_key=state_key,
            value=json.dumps(context, sort_keys=True),
            component="pairing_context",
            guard_strategy=JSON_RICHNESS_MERGE_GUARD,
        )
        conn.commit()


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


def review_pairings(
    state_db: StateDB,
    *,
    channel_id: str | None = None,
    status: str | None = None,
    limit: int | None = None,
) -> PairingQueueReport:
    allowed_statuses = ("pending", "held")
    filters: list[str] = ["status IN ('pending', 'held')"]
    params: list[Any] = []
    if channel_id:
        filters.append("channel_id = ?")
        params.append(channel_id)
    if status:
        if status not in allowed_statuses:
            raise ValueError(f"Unsupported review pairing status '{status}'.")
        filters.append("status = ?")
        params.append(status)
    limit_clause = ""
    if limit is not None:
        limit_clause = "LIMIT ?"
        params.append(limit)
    with state_db.connect() as conn:
        rows = conn.execute(
            f"""
            SELECT pairing_id, channel_id, external_user_id, human_id, status, approved_by, approved_at, updated_at
            FROM pairing_records
            WHERE {' AND '.join(filters)}
            ORDER BY
                CASE status WHEN 'pending' THEN 0 WHEN 'held' THEN 1 ELSE 2 END,
                updated_at DESC,
                channel_id,
                external_user_id
            {limit_clause}
            """
            ,
            params,
        ).fetchall()
    payload: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["context"] = _load_pairing_context(
            state_db=state_db,
            channel_id=str(row["channel_id"]),
            external_user_id=str(row["external_user_id"]),
        )
        payload.append(item)
    return PairingQueueReport(rows=payload)


def pairing_summary(*, state_db: StateDB, channel_id: str) -> PairingSummaryReport:
    with state_db.connect() as conn:
        rows = conn.execute(
            """
            SELECT pairing_id, channel_id, external_user_id, human_id, status, approved_by, approved_at, updated_at
            FROM pairing_records
            WHERE channel_id = ?
            ORDER BY updated_at DESC, external_user_id DESC
            """,
            (channel_id,),
        ).fetchall()
    counts = {"pending": 0, "held": 0, "approved": 0, "revoked": 0}
    latest_pending: dict[str, Any] | None = None
    latest_held: dict[str, Any] | None = None
    latest_approved: dict[str, Any] | None = None
    for row in rows:
        status = str(row["status"])
        if status in counts:
            counts[status] += 1
        item = dict(row)
        item["context"] = _load_pairing_context(
            state_db=state_db,
            channel_id=str(row["channel_id"]),
            external_user_id=str(row["external_user_id"]),
        )
        if status == "pending" and latest_pending is None:
            latest_pending = item
        elif status == "held" and latest_held is None:
            latest_held = item
        elif status == "approved" and latest_approved is None:
            latest_approved = item
    return PairingSummaryReport(
        channel_id=channel_id,
        counts=counts,
        latest_pending=latest_pending,
        latest_held=latest_held,
        latest_approved=latest_approved,
    )


def approve_latest_pairing(
    *,
    state_db: StateDB,
    channel_id: str,
    display_name: str | None = None,
    approved_by: str = LOCAL_OPERATOR_HUMAN_ID,
) -> str:
    _require_operator(state_db, approved_by)
    external_user_id = peek_latest_pairing_external_user_id(
        state_db=state_db,
        channel_id=channel_id,
        statuses=("pending",),
    )
    context = _load_pairing_context(
        state_db=state_db,
        channel_id=channel_id,
        external_user_id=external_user_id,
    )
    resolved_display_name = display_name or _read_optional_text(context.get("display_name"))
    return approve_pairing(
        state_db=state_db,
        channel_id=channel_id,
        external_user_id=external_user_id,
        display_name=resolved_display_name,
        approved_by=approved_by,
    )


def hold_latest_pairing(
    *,
    state_db: StateDB,
    channel_id: str,
    held_by: str = LOCAL_OPERATOR_HUMAN_ID,
) -> str:
    _require_operator(state_db, held_by)
    external_user_id = peek_latest_pairing_external_user_id(
        state_db=state_db,
        channel_id=channel_id,
        statuses=("pending",),
    )
    return hold_pairing(
        state_db=state_db,
        channel_id=channel_id,
        external_user_id=external_user_id,
        held_by=held_by,
    )


def revoke_latest_pairing(
    *,
    state_db: StateDB,
    channel_id: str,
    revoked_by: str = LOCAL_OPERATOR_HUMAN_ID,
) -> str:
    _require_operator(state_db, revoked_by)
    external_user_id = peek_latest_pairing_external_user_id(
        state_db=state_db,
        channel_id=channel_id,
        statuses=("pending", "held"),
    )
    return revoke_pairing(
        state_db=state_db,
        channel_id=channel_id,
        external_user_id=external_user_id,
        revoked_by=revoked_by,
    )


def peek_latest_pairing_external_user_id(
    *,
    state_db: StateDB,
    channel_id: str,
    statuses: tuple[str, ...],
) -> str:
    if not statuses:
        raise ValueError("At least one pairing status must be provided.")
    placeholders = ", ".join("?" for _ in statuses)
    with state_db.connect() as conn:
        row = conn.execute(
            f"""
            SELECT external_user_id
            FROM pairing_records
            WHERE channel_id = ? AND status IN ({placeholders})
            ORDER BY
                CASE status WHEN 'pending' THEN 0 WHEN 'held' THEN 1 ELSE 2 END,
                updated_at DESC,
                external_user_id DESC
            LIMIT 1
            """,
            (channel_id, *statuses),
        ).fetchone()
    if not row:
        if statuses == ("pending",):
            raise ValueError(f"No pending pairing found for channel '{channel_id}'.")
        if statuses == ("pending", "held"):
            raise ValueError(f"No pending or held pairing found for channel '{channel_id}'.")
        raise ValueError(f"No matching pairing found for channel '{channel_id}' with statuses {statuses}.")
    return str(row["external_user_id"])


def consume_pairing_welcome(
    *,
    state_db: StateDB,
    channel_id: str,
    external_user_id: str,
) -> bool:
    state_key = _pairing_welcome_state_key(channel_id, external_user_id)
    with state_db.connect() as conn:
        row = conn.execute(
            "SELECT value FROM runtime_state WHERE state_key = ? LIMIT 1",
            (state_key,),
        ).fetchone()
        if not row or row["value"] != "1":
            return False
        conn.execute("DELETE FROM runtime_state WHERE state_key = ?", (state_key,))
        conn.commit()
    return True


def pairing_welcome_pending(
    *,
    state_db: StateDB,
    channel_id: str,
    external_user_id: str,
) -> bool:
    with state_db.connect() as conn:
        row = conn.execute(
            "SELECT value FROM runtime_state WHERE state_key = ? LIMIT 1",
            (_pairing_welcome_state_key(channel_id, external_user_id),),
        ).fetchone()
    return bool(row and row["value"] == "1")


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
        canonical_agent_count = conn.execute(
            "SELECT COUNT(*) AS c FROM canonical_agent_links WHERE status != 'superseded'"
        ).fetchone()["c"]
        swarm_link_count = conn.execute(
            "SELECT COUNT(*) AS c FROM canonical_agent_links WHERE preferred_source = 'spark_swarm'"
        ).fetchone()["c"]
        identity_conflict_count = conn.execute(
            "SELECT COUNT(*) AS c FROM canonical_agent_links WHERE status = 'identity_conflict'"
        ).fetchone()["c"]
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
        "canonical_agent_count": canonical_agent_count,
        "swarm_link_count": swarm_link_count,
        "identity_conflict_count": identity_conflict_count,
        "pairing_count": pairing_count,
        "active_session_count": active_session_count,
        "providers": providers,
        "channels": channels,
    }
    return IdentityReport(payload=payload)


def _pairing_context_state_key(channel_id: str, external_user_id: str) -> str:
    return f"pairing_context:{channel_id}:{external_user_id}"


def _pairing_welcome_state_key(channel_id: str, external_user_id: str) -> str:
    return f"pairing_welcome:{channel_id}:{external_user_id}"


def _load_pairing_context(*, state_db: StateDB, channel_id: str, external_user_id: str) -> dict[str, Any]:
    with state_db.connect() as conn:
        row = conn.execute(
            "SELECT value FROM runtime_state WHERE state_key = ? LIMIT 1",
            (_pairing_context_state_key(channel_id, external_user_id),),
        ).fetchone()
    if not row or row["value"] is None:
        return {}
    try:
        payload = json.loads(str(row["value"]))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _read_optional_text(value: object) -> str | None:
    if value in {None, ""}:
        return None
    return str(value)


def _format_pairing_summary_block(label: str, row: dict[str, Any] | None) -> list[str]:
    if not row:
        return [f"- {label}: none"]
    context = row.get("context") or {}
    parts = [
        f"- {label}: {row.get('channel_id')}:{row.get('external_user_id')}",
        f"status={row.get('status')}",
        f"human={row.get('human_id')}",
        f"updated_at={row.get('updated_at')}",
    ]
    if context.get("telegram_username"):
        parts.append(f"telegram_username=@{context['telegram_username']}")
    if context.get("chat_id"):
        parts.append(f"chat_id={context['chat_id']}")
    if context.get("last_message_text"):
        parts.append(f"last_message={context['last_message_text']}")
    return [" ".join(parts)]
