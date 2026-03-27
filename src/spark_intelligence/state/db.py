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
    CREATE TABLE IF NOT EXISTS builder_runs (
        run_id TEXT PRIMARY KEY,
        run_kind TEXT NOT NULL,
        origin_surface TEXT NOT NULL,
        parent_run_id TEXT,
        request_id TEXT,
        trace_ref TEXT,
        channel_id TEXT,
        session_id TEXT,
        human_id TEXT,
        agent_id TEXT,
        actor_id TEXT,
        status TEXT NOT NULL,
        close_reason TEXT,
        summary_json TEXT,
        opened_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
        closed_at TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS builder_events (
        event_id TEXT PRIMARY KEY,
        event_type TEXT NOT NULL,
        truth_kind TEXT NOT NULL,
        target_surface TEXT NOT NULL,
        component TEXT NOT NULL,
        run_id TEXT,
        parent_event_id TEXT,
        correlation_id TEXT,
        request_id TEXT,
        trace_ref TEXT,
        channel_id TEXT,
        session_id TEXT,
        human_id TEXT,
        agent_id TEXT,
        actor_id TEXT,
        evidence_lane TEXT NOT NULL,
        severity TEXT NOT NULL,
        status TEXT NOT NULL,
        summary TEXT NOT NULL,
        reason_code TEXT,
        provenance_json TEXT,
        facts_json TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS event_log (
        event_id TEXT PRIMARY KEY,
        event_type TEXT NOT NULL,
        recorded_at TEXT NOT NULL,
        workspace_id TEXT,
        trace_ref TEXT,
        request_id TEXT,
        run_id TEXT,
        session_id TEXT,
        surface_kind TEXT,
        channel_kind TEXT,
        actor_kind TEXT,
        actor_id TEXT,
        status TEXT,
        severity TEXT,
        payload_json TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS run_registry (
        run_id TEXT PRIMARY KEY,
        run_kind TEXT NOT NULL,
        parent_run_id TEXT,
        session_id TEXT,
        surface_kind TEXT,
        status TEXT NOT NULL,
        opened_at TEXT NOT NULL,
        closed_at TEXT,
        freshness_deadline TEXT,
        closure_reason TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS delivery_registry (
        delivery_id TEXT PRIMARY KEY,
        trace_ref TEXT,
        run_id TEXT,
        session_id TEXT,
        channel_kind TEXT,
        target_ref TEXT,
        message_ref TEXT,
        attempted_at TEXT NOT NULL,
        acked_at TEXT,
        status TEXT NOT NULL,
        failure_family TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS config_mutation_audit (
        mutation_id TEXT PRIMARY KEY,
        target_document TEXT NOT NULL,
        target_path TEXT NOT NULL,
        actor_id TEXT NOT NULL,
        actor_type TEXT NOT NULL,
        reason_code TEXT NOT NULL,
        request_source TEXT NOT NULL,
        status TEXT NOT NULL,
        rollback_ref TEXT NOT NULL,
        before_hash TEXT,
        after_hash TEXT,
        before_summary_json TEXT,
        after_summary_json TEXT,
        rollback_payload_json TEXT,
        error_message TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS config_mutation_log (
        mutation_id TEXT PRIMARY KEY,
        recorded_at TEXT NOT NULL,
        actor_kind TEXT,
        actor_id TEXT,
        source_surface TEXT,
        target_path TEXT NOT NULL,
        before_hash TEXT,
        after_hash TEXT,
        semantic_diff_json TEXT,
        validation_verdict TEXT,
        rollback_ref TEXT,
        status TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS provenance_mutation_log (
        mutation_id TEXT PRIMARY KEY,
        recorded_at TEXT NOT NULL,
        surface TEXT NOT NULL,
        mutation_type TEXT NOT NULL,
        source_kind TEXT NOT NULL,
        source_id TEXT NOT NULL,
        trace_ref TEXT,
        input_ref TEXT,
        output_ref TEXT,
        trust_level TEXT NOT NULL,
        quarantined INTEGER NOT NULL DEFAULT 0,
        reason_code TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS runtime_environment_snapshots (
        snapshot_id TEXT PRIMARY KEY,
        surface TEXT NOT NULL,
        run_id TEXT,
        request_id TEXT,
        summary TEXT NOT NULL,
        provider_id TEXT,
        provider_model TEXT,
        provider_base_url TEXT,
        provider_execution_transport TEXT,
        runtime_root TEXT,
        config_path TEXT,
        python_executable TEXT,
        config_hash TEXT,
        env_refs_json TEXT,
        facts_json TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS attachment_state_snapshots (
        snapshot_id TEXT PRIMARY KEY,
        workspace_id TEXT,
        snapshot_path TEXT NOT NULL,
        chip_source TEXT,
        path_source TEXT,
        active_chip_keys_json TEXT NOT NULL,
        pinned_chip_keys_json TEXT NOT NULL,
        active_path_key TEXT,
        warning_count INTEGER NOT NULL DEFAULT 0,
        record_count INTEGER NOT NULL DEFAULT 0,
        generated_at TEXT NOT NULL,
        summary_json TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS personality_trait_profiles (
        human_id TEXT PRIMARY KEY,
        deltas_json TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS personality_observations (
        observation_id TEXT PRIMARY KEY,
        human_id TEXT NOT NULL,
        observed_at TEXT NOT NULL,
        user_state TEXT NOT NULL,
        confidence REAL NOT NULL DEFAULT 0,
        traits_json TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS personality_evolution_events (
        evolution_id TEXT PRIMARY KEY,
        human_id TEXT NOT NULL,
        evolved_at TEXT NOT NULL,
        deltas_json TEXT NOT NULL,
        state_weights_json TEXT NOT NULL,
        observation_count INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS quarantine_records (
        quarantine_id TEXT PRIMARY KEY,
        event_id TEXT,
        run_id TEXT,
        request_id TEXT,
        source_kind TEXT NOT NULL,
        source_ref TEXT,
        policy_domain TEXT NOT NULL,
        reason_code TEXT NOT NULL,
        status TEXT NOT NULL,
        payload_hash TEXT,
        payload_preview TEXT,
        provenance_json TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS contradiction_records (
        contradiction_id TEXT PRIMARY KEY,
        contradiction_key TEXT NOT NULL UNIQUE,
        component TEXT NOT NULL,
        reason_code TEXT NOT NULL,
        status TEXT NOT NULL,
        severity TEXT NOT NULL,
        summary TEXT NOT NULL,
        detail TEXT,
        first_event_id TEXT,
        last_event_id TEXT,
        first_seen_at TEXT NOT NULL,
        last_seen_at TEXT NOT NULL,
        resolved_at TEXT,
        occurrence_count INTEGER NOT NULL DEFAULT 1,
        evidence_json TEXT,
        facts_json TEXT,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
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
    "CREATE INDEX IF NOT EXISTS idx_builder_events_run_id ON builder_events(run_id, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_builder_events_type ON builder_events(event_type, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_builder_runs_status ON builder_runs(status, opened_at)",
    "CREATE INDEX IF NOT EXISTS idx_event_log_type_recorded_at ON event_log(event_type, recorded_at)",
    "CREATE INDEX IF NOT EXISTS idx_event_log_trace_ref ON event_log(trace_ref, recorded_at)",
    "CREATE INDEX IF NOT EXISTS idx_event_log_run_id ON event_log(run_id, recorded_at)",
    "CREATE INDEX IF NOT EXISTS idx_event_log_session_id ON event_log(session_id, recorded_at)",
    "CREATE INDEX IF NOT EXISTS idx_run_registry_status ON run_registry(status, opened_at)",
    "CREATE INDEX IF NOT EXISTS idx_delivery_registry_status_attempted_at ON delivery_registry(status, attempted_at)",
    "CREATE INDEX IF NOT EXISTS idx_delivery_registry_run_id ON delivery_registry(run_id, attempted_at)",
    "CREATE INDEX IF NOT EXISTS idx_config_mutation_audit_created_at ON config_mutation_audit(created_at)",
    "CREATE INDEX IF NOT EXISTS idx_config_mutation_log_recorded_at ON config_mutation_log(recorded_at)",
    "CREATE INDEX IF NOT EXISTS idx_provenance_mutation_log_recorded_at ON provenance_mutation_log(recorded_at)",
    "CREATE INDEX IF NOT EXISTS idx_provenance_mutation_log_surface ON provenance_mutation_log(surface, recorded_at)",
    "CREATE INDEX IF NOT EXISTS idx_runtime_environment_snapshots_surface ON runtime_environment_snapshots(surface, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_attachment_state_snapshots_generated_at ON attachment_state_snapshots(generated_at, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_personality_observations_human_id ON personality_observations(human_id, observed_at, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_personality_evolution_events_human_id ON personality_evolution_events(human_id, evolved_at, created_at)",
    "CREATE INDEX IF NOT EXISTS idx_quarantine_records_created_at ON quarantine_records(created_at)",
    "CREATE INDEX IF NOT EXISTS idx_contradiction_records_status ON contradiction_records(status, last_seen_at)",
    "CREATE INDEX IF NOT EXISTS idx_contradiction_records_reason_code ON contradiction_records(reason_code, last_seen_at)",
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
            conn.execute(
                """
                INSERT INTO job_records(job_id, job_kind, status, schedule_expr)
                VALUES ('auth:oauth-refresh-maintenance', 'oauth_refresh_maintenance', 'scheduled', 'builtin:oauth_refresh_maintenance')
                ON CONFLICT(job_id) DO UPDATE SET
                    job_kind=excluded.job_kind,
                    status=excluded.status,
                    schedule_expr=excluded.schedule_expr,
                    updated_at=CURRENT_TIMESTAMP
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
