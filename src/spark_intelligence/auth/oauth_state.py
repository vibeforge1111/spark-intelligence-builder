from __future__ import annotations

import logging
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from spark_intelligence.state.db import StateDB

logger = logging.getLogger(__name__)


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
            raise ValueError(
                f"OAuth callback state was not found for provider '{provider_id}'. "
                "This usually means the in-flight auth state was already consumed, "
                "expired, or the state token was tampered with. Restart from "
                "`spark auth login`."
            )
        record = _row_to_record(row)
        if record.provider_id != provider_id:
            # Do not echo the stored provider id back to the caller (info-disclosure);
            # log it server-side for diagnosis.
            logger.debug("oauth callback provider mismatch: stored=%r requested=%r", record.provider_id, provider_id)
            raise ValueError(
                "OAuth callback state provider mismatch. This usually means a state token "
                "from a different provider's flow was replayed. Restart the correct "
                "provider's flow via `spark auth login`."
            )
        if record.status != "pending" or record.consumed_at:
            logger.debug("oauth callback state already consumed: status=%r consumed_at=%r", record.status, record.consumed_at)
            raise ValueError(
                "OAuth callback state was already consumed. OAuth state tokens are "
                "single-use. Restart the flow from `spark auth login` to get a fresh state."
            )
        if redirect_uri and record.redirect_uri and redirect_uri != record.redirect_uri:
            # Don't echo the configured redirect URI back to the caller.
            logger.debug("oauth callback redirect_uri mismatch: configured=%r received=%r", record.redirect_uri, redirect_uri)
            raise ValueError(
                "OAuth callback redirect URI mismatch. Check that the OAuth provider is "
                "configured with the exact redirect URI Spark uses; restart from "
                "`spark auth login` after fixing the provider config."
            )
        if expected_issuer and record.expected_issuer and expected_issuer != record.expected_issuer:
            # Don't echo the configured issuer back to the caller.
            logger.debug("oauth callback issuer mismatch: configured=%r received=%r", record.expected_issuer, expected_issuer)
            raise ValueError(
                "OAuth callback issuer mismatch. The provider's ID token issuer changed — "
                "verify the provider's `.well-known/openid-configuration` and update "
                "Spark's provider config."
            )
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
            logger.debug("oauth callback state expired: created_at=%r expires_at=%r", record.created_at, record.expires_at)
            raise ValueError(
                "OAuth callback state expired. Restart the flow from `spark auth login` "
                "and complete the browser handoff promptly — the state TTL is short on purpose."
            )
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
