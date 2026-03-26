from __future__ import annotations

import sqlite3
from pathlib import Path


SCHEMA_STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS schema_info (
        version INTEGER PRIMARY KEY,
        applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS provider_records (
        provider_id TEXT PRIMARY KEY,
        provider_kind TEXT NOT NULL,
        default_model TEXT,
        base_url TEXT,
        api_key_env TEXT,
        default_auth_profile_id TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS auth_profiles (
        auth_profile_id TEXT PRIMARY KEY,
        provider_id TEXT NOT NULL,
        auth_method TEXT NOT NULL,
        display_label TEXT NOT NULL,
        subject_hint TEXT,
        status TEXT NOT NULL,
        is_default INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS auth_profile_static_refs (
        auth_profile_id TEXT PRIMARY KEY,
        ref_source TEXT NOT NULL,
        ref_provider TEXT,
        ref_id TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS oauth_credentials (
        auth_profile_id TEXT PRIMARY KEY,
        issuer TEXT,
        account_subject TEXT,
        scope TEXT,
        access_token_ciphertext TEXT,
        refresh_token_ciphertext TEXT,
        access_expires_at TEXT,
        refresh_expires_at TEXT,
        last_refresh_at TEXT,
        last_refresh_error TEXT,
        status TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS oauth_callback_states (
        callback_id TEXT PRIMARY KEY,
        provider_id TEXT NOT NULL,
        auth_profile_id TEXT,
        flow_kind TEXT NOT NULL,
        oauth_state TEXT NOT NULL UNIQUE,
        pkce_verifier TEXT,
        redirect_uri TEXT,
        expected_issuer TEXT,
        status TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        consumed_at TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS provider_runtime_events (
        event_id INTEGER PRIMARY KEY AUTOINCREMENT,
        provider_id TEXT NOT NULL,
        auth_profile_id TEXT,
        event_kind TEXT NOT NULL,
        detail TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS humans (
        human_id TEXT PRIMARY KEY,
        display_name TEXT NOT NULL,
        status TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS workspace_roles (
        human_id TEXT NOT NULL,
        role TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (human_id, role)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS agent_identities (
        agent_id TEXT PRIMARY KEY,
        human_id TEXT NOT NULL,
        spark_profile TEXT NOT NULL,
        status TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS channel_installations (
        channel_id TEXT PRIMARY KEY,
        channel_kind TEXT NOT NULL,
        status TEXT NOT NULL,
        pairing_mode TEXT NOT NULL,
        auth_ref TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS allowlist_entries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        channel_id TEXT NOT NULL,
        external_user_id TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'paired_user',
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(channel_id, external_user_id, role)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS channel_accounts (
        account_id TEXT PRIMARY KEY,
        channel_id TEXT NOT NULL,
        external_user_id TEXT NOT NULL,
        external_username TEXT,
        status TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS identity_bindings (
        binding_id TEXT PRIMARY KEY,
        human_id TEXT NOT NULL,
        account_id TEXT NOT NULL,
        verified INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS conversation_surfaces (
        surface_id TEXT PRIMARY KEY,
        channel_id TEXT NOT NULL,
        surface_kind TEXT NOT NULL,
        external_surface_id TEXT NOT NULL,
        status TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS session_bindings (
        session_id TEXT PRIMARY KEY,
        agent_id TEXT NOT NULL,
        surface_id TEXT NOT NULL,
        channel_id TEXT NOT NULL,
        external_user_id TEXT NOT NULL,
        session_mode TEXT NOT NULL,
        status TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS pairing_records (
        pairing_id TEXT PRIMARY KEY,
        channel_id TEXT NOT NULL,
        external_user_id TEXT NOT NULL,
        human_id TEXT NOT NULL,
        status TEXT NOT NULL,
        approved_by TEXT,
        approved_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS runtime_state (
        state_key TEXT PRIMARY KEY,
        value TEXT,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS job_records (
        job_id TEXT PRIMARY KEY,
        job_kind TEXT NOT NULL,
        status TEXT NOT NULL,
        schedule_expr TEXT,
        last_run_at TEXT,
        last_result TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS operator_events (
        event_id INTEGER PRIMARY KEY AUTOINCREMENT,
        actor_human_id TEXT NOT NULL,
        action TEXT NOT NULL,
        target_kind TEXT NOT NULL,
        target_ref TEXT NOT NULL,
        reason TEXT,
        details_json TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
]


class ClosingConnection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        try:
            if exc_type is None:
                self.commit()
            else:
                self.rollback()
        finally:
            self.close()
        return False


class StateDB:
    def __init__(self, path: Path):
        self.path = path

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            for statement in SCHEMA_STATEMENTS:
                conn.execute(statement)
            self._ensure_column(conn, "provider_records", "default_auth_profile_id", "TEXT")
            conn.execute("INSERT OR IGNORE INTO schema_info(version) VALUES (1)")
            conn.execute(
                """
                INSERT INTO humans(human_id, display_name, status)
                VALUES ('local-operator', 'Local Operator', 'active')
                ON CONFLICT(human_id) DO NOTHING
                """
            )
            conn.execute(
                """
                INSERT INTO workspace_roles(human_id, role)
                VALUES ('local-operator', 'operator_admin')
                ON CONFLICT(human_id, role) DO NOTHING
                """
            )
            conn.commit()

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, factory=ClosingConnection)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
        columns = {
            str(row["name"])
            for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column in columns:
            return
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
