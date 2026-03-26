from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from spark_intelligence.state.db import StateDB


@dataclass(frozen=True)
class OAuthCallbackStateRecord:
    callback_id: str
    provider_id: str
    auth_profile_id: str | None
    flow_kind: str
    oauth_state: str
    pkce_verifier: str | None
    redirect_uri: str | None
    expected_issuer: str | None
    status: str
    expires_at: str
    consumed_at: str | None
    created_at: str

    @property
    def is_expired(self) -> bool:
        return _parse_timestamp(self.expires_at) <= datetime.now(UTC)


def issue_oauth_callback_state(
    *,
    state_db: StateDB,
    provider_id: str,
    auth_profile_id: str | None,
    flow_kind: str,
    redirect_uri: str | None,
    expected_issuer: str | None,
    pkce_verifier: str | None,
    ttl_seconds: int = 600,
) -> OAuthCallbackStateRecord:
    now = datetime.now(UTC)
    expires_at = now + timedelta(seconds=ttl_seconds)
    callback_id = str(uuid.uuid4())
    oauth_state = secrets.token_urlsafe(24)
    with state_db.connect() as conn:
        conn.execute(
            """
            INSERT INTO oauth_callback_states(
                callback_id,
                provider_id,
                auth_profile_id,
                flow_kind,
                oauth_state,
                pkce_verifier,
                redirect_uri,
                expected_issuer,
                status,
                expires_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
            """,
            (
                callback_id,
                provider_id,
                auth_profile_id,
                flow_kind,
                oauth_state,
                pkce_verifier,
                redirect_uri,
                expected_issuer,
                _format_timestamp(expires_at),
            ),
        )
        row = conn.execute(
            "SELECT * FROM oauth_callback_states WHERE callback_id = ? LIMIT 1",
            (callback_id,),
        ).fetchone()
        conn.commit()
    return _row_to_record(row)


def consume_oauth_callback_state(
    *,
    state_db: StateDB,
    provider_id: str,
    oauth_state: str,
    redirect_uri: str | None = None,
    expected_issuer: str | None = None,
) -> OAuthCallbackStateRecord:
    now = datetime.now(UTC)
    with state_db.connect() as conn:
        row = conn.execute(
            "SELECT * FROM oauth_callback_states WHERE oauth_state = ? LIMIT 1",
            (oauth_state,),
        ).fetchone()
        if not row:
            raise ValueError("OAuth callback state was not found.")
        record = _row_to_record(row)
        if record.provider_id != provider_id:
            raise ValueError("OAuth callback state provider mismatch.")
        if record.status != "pending" or record.consumed_at:
            raise ValueError("OAuth callback state was already consumed.")
        if redirect_uri and record.redirect_uri and redirect_uri != record.redirect_uri:
            raise ValueError("OAuth callback redirect URI mismatch.")
        if expected_issuer and record.expected_issuer and expected_issuer != record.expected_issuer:
            raise ValueError("OAuth callback issuer mismatch.")
        if _parse_timestamp(record.expires_at) <= now:
            conn.execute(
                """
                UPDATE oauth_callback_states
                SET status = 'expired'
                WHERE callback_id = ?
                """,
                (record.callback_id,),
            )
            conn.commit()
            raise ValueError("OAuth callback state expired.")
        consumed_at = _format_timestamp(now)
        conn.execute(
            """
            UPDATE oauth_callback_states
            SET status = 'consumed', consumed_at = ?
            WHERE callback_id = ?
            """,
            (consumed_at, record.callback_id),
        )
        updated = conn.execute(
            "SELECT * FROM oauth_callback_states WHERE callback_id = ? LIMIT 1",
            (record.callback_id,),
        ).fetchone()
        conn.commit()
    return _row_to_record(updated)


def get_oauth_callback_state(
    *,
    state_db: StateDB,
    oauth_state: str,
) -> OAuthCallbackStateRecord | None:
    with state_db.connect() as conn:
        row = conn.execute(
            "SELECT * FROM oauth_callback_states WHERE oauth_state = ? LIMIT 1",
            (oauth_state,),
        ).fetchone()
    if not row:
        return None
    return _row_to_record(row)


def expire_stale_oauth_callback_states(*, state_db: StateDB, now: datetime | None = None) -> int:
    effective_now = now or datetime.now(UTC)
    with state_db.connect() as conn:
        cursor = conn.execute(
            """
            UPDATE oauth_callback_states
            SET status = 'expired'
            WHERE status = 'pending' AND expires_at <= ?
            """,
            (_format_timestamp(effective_now),),
        )
        conn.commit()
        return int(cursor.rowcount or 0)


def _row_to_record(row: object) -> OAuthCallbackStateRecord:
    return OAuthCallbackStateRecord(
        callback_id=str(row["callback_id"]),
        provider_id=str(row["provider_id"]),
        auth_profile_id=str(row["auth_profile_id"]) if row["auth_profile_id"] else None,
        flow_kind=str(row["flow_kind"]),
        oauth_state=str(row["oauth_state"]),
        pkce_verifier=str(row["pkce_verifier"]) if row["pkce_verifier"] else None,
        redirect_uri=str(row["redirect_uri"]) if row["redirect_uri"] else None,
        expected_issuer=str(row["expected_issuer"]) if row["expected_issuer"] else None,
        status=str(row["status"]),
        expires_at=str(row["expires_at"]),
        consumed_at=str(row["consumed_at"]) if row["consumed_at"] else None,
        created_at=str(row["created_at"]),
    )


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
