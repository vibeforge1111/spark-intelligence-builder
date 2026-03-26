from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

from spark_intelligence.attachments import (
    activate_chip,
    add_attachment_root,
    attachment_status,
    clear_active_path,
    deactivate_chip,
    list_attachments,
    pin_chip,
    set_active_path,
    sync_attachment_snapshot,
    unpin_chip,
)
from spark_intelligence.auth.providers import list_api_key_provider_ids, list_oauth_provider_ids, list_provider_specs
from spark_intelligence.auth.runtime import build_auth_status_report
from spark_intelligence.auth.service import complete_oauth_login, connect_provider, logout_provider, refresh_provider, start_oauth_login
from spark_intelligence.channel.service import (
    add_channel,
    inspect_telegram_bot_token,
    render_telegram_botfather_guide,
    set_channel_status,
    test_configured_telegram_channel,
)
from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.doctor.checks import run_doctor
from spark_intelligence.gateway.runtime import (
    gateway_outbound_view,
    gateway_simulate_discord_message,
    gateway_simulate_telegram_update,
    gateway_simulate_whatsapp_message,
    gateway_start,
    gateway_status,
    gateway_trace_view,
)
from spark_intelligence.gateway.oauth_callback import pending_oauth_redirect_uri, serve_gateway_oauth_callback
from spark_intelligence.identity.service import (
    agent_inspect,
    approve_latest_pairing,
    approve_pairing,
    hold_pairing,
    hold_latest_pairing,
    list_pairings,
    list_sessions,
    pairing_summary,
    peek_latest_pairing_external_user_id,
    revoke_latest_pairing,
    review_pairings,
    revoke_pairing,
    revoke_session,
)
from spark_intelligence.jobs.service import jobs_list, jobs_tick
from spark_intelligence.ops import build_operator_inbox, build_operator_security_report, list_operator_events, log_operator_event
from spark_intelligence.researcher_bridge import discover_researcher_runtime_root, resolve_researcher_config_path
from spark_intelligence.researcher_bridge import researcher_bridge_status
from spark_intelligence.state.db import StateDB
from spark_intelligence.swarm_bridge import evaluate_swarm_escalation, swarm_status, sync_swarm_collective


@dataclass
class SystemStatus:
    doctor_ok: bool
    gateway_ready: bool
    researcher_available: bool
    swarm_payload_ready: bool
    attachment_warning_count: int
    payload: dict

    def to_json(self) -> str:
        return json.dumps(self.payload, indent=2)

    def to_text(self) -> str:
        lines = ["Spark Intelligence status"]
        lines.append(f"- doctor: {'ok' if self.doctor_ok else 'degraded'}")
        lines.append(f"- gateway: {'ready' if self.gateway_ready else 'not_ready'}")
        lines.append(f"- researcher: {'available' if self.researcher_available else 'missing'}")
        lines.append(f"- swarm: {'payload_ready' if self.swarm_payload_ready else 'not_ready'}")
        lines.append(
            f"- attachments: {self.payload['attachments']['record_count']} records "
            f"warnings={self.attachment_warning_count}"
        )
        lines.append(
            f"- active chips: {', '.join(self.payload['attachments']['active_chip_keys']) if self.payload['attachments']['active_chip_keys'] else 'none'}"
        )
        lines.append(f"- active path: {self.payload['attachments']['active_path_key'] or 'none'}")
        lines.append(
            f"- providers: {', '.join(self.payload['gateway']['configured_providers']) if self.payload['gateway']['configured_providers'] else 'none'}"
        )
        lines.append(
            f"- channels: {', '.join(self.payload['gateway']['configured_channels']) if self.payload['gateway']['configured_channels'] else 'none'}"
        )
        lines.append(
            f"- provider runtime: {'ok' if self.payload['gateway'].get('provider_runtime_ok') else 'degraded'}"
        )
        if self.payload['gateway'].get('provider_runtime_detail'):
            lines.append(f"- provider runtime detail: {self.payload['gateway']['provider_runtime_detail']}")
        lines.append(
            f"- provider execution: {'ok' if self.payload['gateway'].get('provider_execution_ok') else 'degraded'}"
        )
        if self.payload['gateway'].get('provider_execution_detail'):
            lines.append(f"- provider execution detail: {self.payload['gateway']['provider_execution_detail']}")
        lines.append(
            f"- oauth maintenance: {'ok' if self.payload['gateway'].get('oauth_maintenance_ok') else 'degraded'}"
        )
        if self.payload['gateway'].get('oauth_maintenance_detail'):
            lines.append(f"- oauth maintenance detail: {self.payload['gateway']['oauth_maintenance_detail']}")
        if self.payload['researcher'].get('last_provider_execution_transport'):
            lines.append(
                f"- last provider transport: {self.payload['researcher']['last_provider_execution_transport']}"
            )
        lines.append(f"- last researcher trace: {self.payload['researcher'].get('last_trace_ref') or 'none'}")
        lines.append(f"- last swarm decision: {(self.payload['swarm'].get('last_decision') or {}).get('mode', 'none')}")
        lines.append(f"- last swarm sync: {(self.payload['swarm'].get('last_sync') or {}).get('mode', 'none')}")
        return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="spark-intelligence")
    subparsers = parser.add_subparsers(dest="command", required=True)

    setup_parser = subparsers.add_parser("setup", help="Bootstrap config and state")
    setup_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    setup_parser.add_argument("--researcher-root", help="Connect a local spark-researcher repo")
    setup_parser.add_argument("--researcher-config", help="Override spark-researcher config path")
    setup_parser.add_argument("--swarm-runtime-root", help="Connect a local spark-swarm repo")
    setup_parser.add_argument("--swarm-api-url", help="Set the Spark Swarm API base URL")
    setup_parser.add_argument("--swarm-workspace-id", help="Set the Spark Swarm workspace id")
    setup_parser.add_argument("--swarm-access-token", help="Store a Spark Swarm access token")
    setup_parser.add_argument(
        "--swarm-access-token-env",
        default="SPARK_SWARM_ACCESS_TOKEN",
        help="Env var name used to store the Spark Swarm access token",
    )

    doctor_parser = subparsers.add_parser("doctor", help="Run environment and state checks")
    doctor_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    doctor_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")

    status_parser = subparsers.add_parser("status", help="Show unified runtime, bridge, and attachment state")
    status_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    status_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")

    operator_parser = subparsers.add_parser("operator", help="Safe operator controls for bridges and pairing review")
    operator_subparsers = operator_parser.add_subparsers(dest="operator_command", required=True)
    operator_set_bridge_parser = operator_subparsers.add_parser("set-bridge", help="Enable or disable a bridge")
    operator_set_bridge_parser.add_argument("bridge", choices=["researcher", "swarm"])
    operator_set_bridge_parser.add_argument("mode", choices=["enabled", "disabled"])
    operator_set_bridge_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    operator_set_bridge_parser.add_argument("--reason", help="Short audit reason for this change")
    operator_review_pairings_parser = operator_subparsers.add_parser("review-pairings", help="Show pending and held pairing requests")
    operator_review_pairings_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    operator_review_pairings_parser.add_argument("--channel-id", choices=["telegram", "discord", "whatsapp"], help="Filter review queue to one channel")
    operator_review_pairings_parser.add_argument("--status", choices=["pending", "held"], help="Filter review queue to one status")
    operator_review_pairings_parser.add_argument("--limit", type=int, help="Limit the number of rows shown")
    operator_review_pairings_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")
    operator_pairing_summary_parser = operator_subparsers.add_parser(
        "pairing-summary",
        help="Show compact pairing state for one channel",
    )
    operator_pairing_summary_parser.add_argument("channel_id", choices=["telegram", "discord", "whatsapp"])
    operator_pairing_summary_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    operator_pairing_summary_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")
    operator_approve_pairing_parser = operator_subparsers.add_parser("approve-pairing", help="Approve a pending or held pairing")
    operator_approve_pairing_parser.add_argument("channel_id")
    operator_approve_pairing_parser.add_argument("external_user_id")
    operator_approve_pairing_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    operator_approve_pairing_parser.add_argument("--display-name", help="Friendly display name")
    operator_approve_pairing_parser.add_argument("--reason", help="Short audit reason for this approval")
    operator_approve_latest_parser = operator_subparsers.add_parser(
        "approve-latest",
        help="Approve the newest pending pairing for a channel",
    )
    operator_approve_latest_parser.add_argument("channel_id", choices=["telegram", "discord", "whatsapp"])
    operator_approve_latest_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    operator_approve_latest_parser.add_argument("--display-name", help="Friendly display name override")
    operator_approve_latest_parser.add_argument("--reason", help="Short audit reason for this approval")
    operator_hold_latest_parser = operator_subparsers.add_parser(
        "hold-latest",
        help="Hold the newest pending pairing for a channel",
    )
    operator_hold_latest_parser.add_argument("channel_id", choices=["telegram", "discord", "whatsapp"])
    operator_hold_latest_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    operator_hold_latest_parser.add_argument("--reason", help="Short audit reason for holding this request")
    operator_revoke_latest_parser = operator_subparsers.add_parser(
        "revoke-latest",
        help="Revoke the newest pending or held pairing for a channel",
    )
    operator_revoke_latest_parser.add_argument("channel_id", choices=["telegram", "discord", "whatsapp"])
    operator_revoke_latest_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    operator_revoke_latest_parser.add_argument("--reason", help="Short audit reason for revoking this request")
    operator_hold_pairing_parser = operator_subparsers.add_parser("hold-pairing", help="Mark a pairing request as held")
    operator_hold_pairing_parser.add_argument("channel_id")
    operator_hold_pairing_parser.add_argument("external_user_id")
    operator_hold_pairing_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    operator_hold_pairing_parser.add_argument("--reason", help="Short audit reason for holding this request")
    operator_set_channel_parser = operator_subparsers.add_parser("set-channel", help="Set channel ingress state")
    operator_set_channel_parser.add_argument("channel_id")
    operator_set_channel_parser.add_argument("mode", choices=["enabled", "paused", "disabled"])
    operator_set_channel_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    operator_set_channel_parser.add_argument("--reason", help="Short audit reason for this change")
    operator_history_parser = operator_subparsers.add_parser("history", help="Show recent operator actions")
    operator_history_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    operator_history_parser.add_argument("--limit", type=int, default=20, help="Number of events to show")
    operator_history_parser.add_argument("--action", help="Filter history to one action")
    operator_history_parser.add_argument("--target-kind", help="Filter history to one target kind")
    operator_history_parser.add_argument("--contains", help="Filter history by target, reason, or details substring")
    operator_history_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")
    operator_inbox_parser = operator_subparsers.add_parser("inbox", help="Show actionable operator items")
    operator_inbox_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    operator_inbox_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")
    operator_security_parser = operator_subparsers.add_parser("security", help="Show recent security-relevant operator state")
    operator_security_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    operator_security_parser.add_argument("--limit", type=int, default=100, help="Number of recent trace/audit events to scan")
    operator_security_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")

    gateway_parser = subparsers.add_parser("gateway", help="Gateway operations")
    gateway_subparsers = gateway_parser.add_subparsers(dest="gateway_command", required=True)
    gateway_start_parser = gateway_subparsers.add_parser("start", help="Start foreground gateway")
    gateway_start_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    gateway_start_parser.add_argument("--once", action="store_true", help="Run one poll cycle and exit")
    gateway_start_parser.add_argument(
        "--continuous",
        action="store_true",
        help="Keep polling in the foreground until interrupted or --max-cycles is reached",
    )
    gateway_start_parser.add_argument("--max-cycles", type=int, help="Limit gateway poll cycles")
    gateway_start_parser.add_argument(
        "--poll-timeout-seconds",
        type=int,
        default=5,
        help="Telegram polling timeout in seconds",
    )
    gateway_status_parser = gateway_subparsers.add_parser("status", help="Inspect gateway readiness")
    gateway_status_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    gateway_status_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")
    gateway_oauth_callback_parser = gateway_subparsers.add_parser(
        "oauth-callback",
        help="Serve one loopback OAuth callback through the gateway surface and complete login automatically",
    )
    gateway_oauth_callback_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    gateway_oauth_callback_parser.add_argument("--redirect-uri", help="Explicit redirect URI to bind for the callback")
    gateway_oauth_callback_parser.add_argument("--provider", choices=list_oauth_provider_ids(), help="Restrict callback completion to one provider")
    gateway_oauth_callback_parser.add_argument("--timeout-seconds", type=int, default=120, help="Maximum time to wait for the OAuth callback")
    gateway_oauth_callback_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")
    gateway_simulate_parser = gateway_subparsers.add_parser(
        "simulate-telegram-update",
        help="Simulate one Telegram update through normalization and authorization routing",
    )
    gateway_simulate_parser.add_argument("update_file", help="Path to a Telegram update JSON file")
    gateway_simulate_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    gateway_simulate_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")
    gateway_simulate_discord_parser = gateway_subparsers.add_parser(
        "simulate-discord-message",
        help="Simulate one Discord DM message through normalization and authorization routing",
    )
    gateway_simulate_discord_parser.add_argument("message_file", help="Path to a Discord message JSON file")
    gateway_simulate_discord_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    gateway_simulate_discord_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")
    gateway_simulate_whatsapp_parser = gateway_subparsers.add_parser(
        "simulate-whatsapp-message",
        help="Simulate one WhatsApp DM message through normalization and authorization routing",
    )
    gateway_simulate_whatsapp_parser.add_argument("message_file", help="Path to a WhatsApp message JSON file")
    gateway_simulate_whatsapp_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    gateway_simulate_whatsapp_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")
    gateway_traces_parser = gateway_subparsers.add_parser("traces", help="Show recent gateway traces")
    gateway_traces_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    gateway_traces_parser.add_argument("--limit", type=int, default=20, help="Number of trace events to show")
    gateway_traces_parser.add_argument("--channel-id", help="Filter trace events by channel id")
    gateway_traces_parser.add_argument("--event", help="Filter trace events by event name")
    gateway_traces_parser.add_argument("--user", help="Filter trace events by user id or chat id")
    gateway_traces_parser.add_argument("--decision", help="Filter trace events by decision")
    gateway_traces_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")
    gateway_outbound_parser = gateway_subparsers.add_parser("outbound", help="Show recent outbound audit records")
    gateway_outbound_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    gateway_outbound_parser.add_argument("--limit", type=int, default=20, help="Number of outbound events to show")
    gateway_outbound_parser.add_argument("--channel-id", help="Filter outbound events by channel id")
    gateway_outbound_parser.add_argument("--event", help="Filter outbound events by event name")
    gateway_outbound_parser.add_argument("--user", help="Filter outbound events by user id or chat id")
    gateway_outbound_parser.add_argument("--decision", help="Filter outbound events by decision")
    gateway_outbound_parser.add_argument(
        "--delivery",
        choices=["ok", "failed"],
        help="Filter outbound events by delivery result",
    )
    gateway_outbound_parser.add_argument("--contains", help="Filter outbound preview text by substring")
    gateway_outbound_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")

    channel_parser = subparsers.add_parser("channel", help="Manage channel adapters")
    channel_subparsers = channel_parser.add_subparsers(dest="channel_command", required=True)
    channel_add_parser = channel_subparsers.add_parser("add", help="Add a channel adapter")
    channel_add_parser.add_argument("channel_kind", choices=["telegram", "discord", "whatsapp"], help="Adapter kind")
    channel_add_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    channel_add_parser.add_argument("--bot-token", help="Adapter bot token")
    channel_add_parser.add_argument("--webhook-secret", help="Static webhook secret for adapter HTTP ingress")
    channel_add_parser.add_argument("--webhook-secret-env", help="Env var name used to store the webhook secret")
    channel_add_parser.add_argument("--allowed-user", action="append", default=[], help="Allowed adapter user id")
    channel_add_parser.add_argument(
        "--clear-allowed-users",
        action="store_true",
        help="Clear any existing configured allowed users before applying this update.",
    )
    channel_add_parser.add_argument(
        "--pairing-mode",
        choices=["allowlist", "pairing"],
        default=None,
        help="Inbound DM authorization mode",
    )
    channel_add_parser.add_argument(
        "--skip-validate",
        action="store_true",
        help="Skip remote token validation. Useful only for offline setup or scripted recovery.",
    )
    channel_telegram_onboard_parser = channel_subparsers.add_parser(
        "telegram-onboard",
        help="Guide or complete BotFather-based Telegram setup",
    )
    channel_telegram_onboard_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    channel_telegram_onboard_parser.add_argument("--bot-token", help="Telegram bot token from BotFather")
    channel_telegram_onboard_parser.add_argument("--allowed-user", action="append", default=[], help="Allowed Telegram user id")
    channel_telegram_onboard_parser.add_argument(
        "--clear-allowed-users",
        action="store_true",
        help="Clear any existing configured allowed users before applying this update.",
    )
    channel_telegram_onboard_parser.add_argument(
        "--pairing-mode",
        choices=["allowlist", "pairing"],
        default=None,
        help="Inbound DM authorization mode",
    )
    channel_telegram_onboard_parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Validate the Telegram token without storing channel config",
    )
    channel_telegram_onboard_parser.add_argument(
        "--skip-validate",
        action="store_true",
        help="Skip remote token validation. Useful only for offline setup or scripted recovery.",
    )
    channel_test_parser = channel_subparsers.add_parser(
        "test",
        help="Run lightweight diagnostics for one configured channel",
    )
    channel_test_parser.add_argument("channel_kind", choices=["telegram"], help="Configured channel to test")
    channel_test_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    channel_test_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")

    attachments_parser = subparsers.add_parser("attachments", help="Inspect and manage chip/path attachment roots")
    attachments_subparsers = attachments_parser.add_subparsers(dest="attachments_command", required=True)
    attachments_status_parser = attachments_subparsers.add_parser("status", help="Scan chip and path attachments")
    attachments_status_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    attachments_status_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")
    attachments_list_parser = attachments_subparsers.add_parser("list", help="List discovered chip/path attachments")
    attachments_list_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    attachments_list_parser.add_argument("--kind", choices=["all", "chip", "path"], default="all")
    attachments_list_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")
    attachments_add_root_parser = attachments_subparsers.add_parser("add-root", help="Add a chip or path root")
    attachments_add_root_parser.add_argument("target", choices=["chips", "paths"], help="Which attachment root list to update")
    attachments_add_root_parser.add_argument("root", help="Root path to add")
    attachments_add_root_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    attachments_snapshot_parser = attachments_subparsers.add_parser("snapshot", help="Build and persist the attachment snapshot")
    attachments_snapshot_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    attachments_snapshot_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")
    attachments_activate_chip_parser = attachments_subparsers.add_parser("activate-chip", help="Mark a chip active")
    attachments_activate_chip_parser.add_argument("chip_key", help="Chip key to activate")
    attachments_activate_chip_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    attachments_deactivate_chip_parser = attachments_subparsers.add_parser("deactivate-chip", help="Remove a chip from active and pinned state")
    attachments_deactivate_chip_parser.add_argument("chip_key", help="Chip key to deactivate")
    attachments_deactivate_chip_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    attachments_pin_chip_parser = attachments_subparsers.add_parser("pin-chip", help="Pin a chip so it remains active")
    attachments_pin_chip_parser.add_argument("chip_key", help="Chip key to pin")
    attachments_pin_chip_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    attachments_unpin_chip_parser = attachments_subparsers.add_parser("unpin-chip", help="Remove a chip from pinned state")
    attachments_unpin_chip_parser.add_argument("chip_key", help="Chip key to unpin")
    attachments_unpin_chip_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    attachments_set_path_parser = attachments_subparsers.add_parser("set-path", help="Set the active specialization path")
    attachments_set_path_parser.add_argument("path_key", help="Specialization path key")
    attachments_set_path_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    attachments_clear_path_parser = attachments_subparsers.add_parser("clear-path", help="Clear the active specialization path")
    attachments_clear_path_parser.add_argument("--home", help="Override Spark Intelligence home directory")

    auth_parser = subparsers.add_parser("auth", help="Manage model providers")
    auth_subparsers = auth_parser.add_subparsers(dest="auth_command", required=True)
    auth_providers_parser = auth_subparsers.add_parser("providers", help="List supported provider/auth options")
    auth_providers_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")
    auth_connect_parser = auth_subparsers.add_parser("connect", help="Connect a model provider")
    auth_connect_parser.add_argument("provider", choices=list_api_key_provider_ids())
    auth_connect_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    auth_connect_parser.add_argument("--api-key", help="API key for the provider")
    auth_connect_parser.add_argument("--api-key-env", help="Existing or target env var name for the provider secret")
    auth_connect_parser.add_argument("--model", help="Default model id")
    auth_connect_parser.add_argument("--base-url", help="Custom provider base URL")
    auth_login_parser = auth_subparsers.add_parser("login", help="Start or complete an OAuth provider login or reconnect flow")
    auth_login_parser.add_argument("provider", choices=list_oauth_provider_ids())
    auth_login_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    auth_login_parser.add_argument("--redirect-uri", help="Override the default OAuth callback URI")
    auth_login_parser.add_argument("--callback-url", help="Full callback URL captured after OAuth approval")
    auth_login_parser.add_argument("--listen", action="store_true", help="Wait for the loopback OAuth callback and complete login automatically")
    auth_login_parser.add_argument("--timeout-seconds", type=int, default=120, help="Maximum time to wait for a loopback OAuth callback")
    auth_login_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")
    auth_logout_parser = auth_subparsers.add_parser("logout", help="Revoke locally stored OAuth credentials for a provider")
    auth_logout_parser.add_argument("provider", choices=list_oauth_provider_ids())
    auth_logout_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    auth_logout_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")
    auth_refresh_parser = auth_subparsers.add_parser("refresh", help="Refresh locally stored OAuth credentials for a provider immediately")
    auth_refresh_parser.add_argument("provider", choices=list_oauth_provider_ids())
    auth_refresh_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    auth_refresh_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")
    auth_status_parser = auth_subparsers.add_parser("status", help="Show configured auth profiles, expiry state, and secret readiness")
    auth_status_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    auth_status_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")

    researcher_parser = subparsers.add_parser("researcher", help="Inspect Spark Researcher bridge state")
    researcher_subparsers = researcher_parser.add_subparsers(dest="researcher_command", required=True)
    researcher_status_parser = researcher_subparsers.add_parser("status", help="Show Spark Researcher bridge readiness")
    researcher_status_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    researcher_status_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")

    config_parser = subparsers.add_parser("config", help="Inspect and update config values")
    config_subparsers = config_parser.add_subparsers(dest="config_command", required=True)
    config_show_parser = config_subparsers.add_parser("show", help="Show config or one config path")
    config_show_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    config_show_parser.add_argument("--path", help="Dot-path under config.yaml")
    config_show_parser.add_argument("--json", action="store_true", help="Emit JSON output")
    config_set_parser = config_subparsers.add_parser("set", help="Set one config value")
    config_set_parser.add_argument("path", help="Dot-path under config.yaml")
    config_set_parser.add_argument("value", help="YAML-parsed value to store")
    config_set_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    config_unset_parser = config_subparsers.add_parser("unset", help="Remove one config value")
    config_unset_parser.add_argument("path", help="Dot-path under config.yaml")
    config_unset_parser.add_argument("--home", help="Override Spark Intelligence home directory")

    swarm_parser = subparsers.add_parser("swarm", help="Inspect and sync Spark Swarm bridge state")
    swarm_subparsers = swarm_parser.add_subparsers(dest="swarm_command", required=True)
    swarm_status_parser = swarm_subparsers.add_parser("status", help="Show Spark Swarm bridge readiness")
    swarm_status_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    swarm_status_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")
    swarm_configure_parser = swarm_subparsers.add_parser("configure", help="Configure Spark Swarm API settings")
    swarm_configure_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    swarm_configure_parser.add_argument("--api-url", help="Base URL for the Spark Swarm API")
    swarm_configure_parser.add_argument("--workspace-id", help="Workspace id used by Spark Swarm")
    swarm_configure_parser.add_argument("--access-token", help="Access token for the Spark Swarm API")
    swarm_configure_parser.add_argument(
        "--access-token-env",
        default="SPARK_SWARM_ACCESS_TOKEN",
        help="Env var name used to store the Spark Swarm access token",
    )
    swarm_configure_parser.add_argument("--runtime-root", help="Override local spark-swarm repo path")
    swarm_sync_parser = swarm_subparsers.add_parser(
        "sync",
        help="Build the latest Spark Researcher collective payload and sync it to Spark Swarm",
    )
    swarm_sync_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    swarm_sync_parser.add_argument("--dry-run", action="store_true", help="Build the payload without uploading it")
    swarm_sync_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")
    swarm_evaluate_parser = swarm_subparsers.add_parser(
        "evaluate",
        help="Evaluate whether a task should be escalated to Spark Swarm",
    )
    swarm_evaluate_parser.add_argument("task", help="Task description to evaluate")
    swarm_evaluate_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    swarm_evaluate_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")

    jobs_parser = subparsers.add_parser("jobs", help="Inspect and run operator-driven maintenance jobs")
    jobs_subparsers = jobs_parser.add_subparsers(dest="jobs_command", required=True)
    jobs_tick_parser = jobs_subparsers.add_parser("tick", help="Run due maintenance work once, including OAuth refresh repair")
    jobs_tick_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    jobs_list_parser = jobs_subparsers.add_parser("list", help="List known jobs and the latest maintenance result")
    jobs_list_parser.add_argument("--home", help="Override Spark Intelligence home directory")

    agent_parser = subparsers.add_parser("agent", help="Inspect agent and workspace state")
    agent_subparsers = agent_parser.add_subparsers(dest="agent_command", required=True)
    agent_inspect_parser = agent_subparsers.add_parser("inspect", help="Inspect current workspace identity state")
    agent_inspect_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    agent_inspect_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")

    pairing_parser = subparsers.add_parser("pairings", help="Manage pairings")
    pairing_subparsers = pairing_parser.add_subparsers(dest="pairings_command", required=True)
    pairing_list_parser = pairing_subparsers.add_parser("list", help="List pairings")
    pairing_list_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    pairing_approve_parser = pairing_subparsers.add_parser("approve", help="Approve a pairing")
    pairing_approve_parser.add_argument("channel_id", help="Channel installation id")
    pairing_approve_parser.add_argument("external_user_id", help="External user id")
    pairing_approve_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    pairing_approve_parser.add_argument("--display-name", help="Friendly display name")
    pairing_revoke_parser = pairing_subparsers.add_parser("revoke", help="Revoke a pairing")
    pairing_revoke_parser.add_argument("channel_id", help="Channel installation id")
    pairing_revoke_parser.add_argument("external_user_id", help="External user id")
    pairing_revoke_parser.add_argument("--home", help="Override Spark Intelligence home directory")

    sessions_parser = subparsers.add_parser("sessions", help="Inspect and revoke session bindings")
    sessions_subparsers = sessions_parser.add_subparsers(dest="sessions_command", required=True)
    sessions_list_parser = sessions_subparsers.add_parser("list", help="List sessions")
    sessions_list_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    sessions_revoke_parser = sessions_subparsers.add_parser("revoke", help="Revoke a session")
    sessions_revoke_parser.add_argument("session_id", help="Canonical session id")
    sessions_revoke_parser.add_argument("--home", help="Override Spark Intelligence home directory")

    return parser


def handle_setup(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    created = config_manager.bootstrap()
    state_db = StateDB(config_manager.paths.state_db)
    state_db.initialize()
    setup_notes = _apply_setup_integrations(config_manager, args)
    print(f"Spark Intelligence home: {config_manager.paths.home}")
    if created:
        print("Created config, env, and state bootstrap.")
    else:
        print("Existing config and env preserved; verified state bootstrap.")
    if setup_notes:
        print("Setup integrations:")
        for note in setup_notes:
            print(f"  - {note}")
    print("Next steps:")
    print("  1. spark-intelligence auth connect openai --api-key <key> --model <model>")
    print("  2. spark-intelligence channel telegram-onboard")
    print("  3. spark-intelligence doctor")
    print("  4. spark-intelligence gateway start")
    print("Optional Spark hookups:")
    print("  - spark-intelligence swarm status")
    print("  - spark-intelligence swarm sync --dry-run")
    return 0


def handle_doctor(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    report = run_doctor(config_manager, state_db)
    if args.json:
        print(report.to_json())
    else:
        print(report.to_text())
    return 0 if report.ok else 1


def handle_operator_set_bridge(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    config_manager.set_path(f"spark.{args.bridge}.enabled", args.mode == "enabled")
    log_operator_event(
        state_db=state_db,
        action="set_bridge",
        target_kind="bridge",
        target_ref=args.bridge,
        reason=args.reason,
        details={"enabled": args.mode == "enabled"},
    )
    print(f"Set spark.{args.bridge}.enabled = {json.dumps(args.mode == 'enabled')}")
    return 0


def handle_operator_review_pairings(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    report = review_pairings(
        state_db,
        channel_id=args.channel_id,
        status=args.status,
        limit=args.limit,
    )
    print(report.to_json() if args.json else report.to_text())
    return 0


def handle_operator_pairing_summary(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    report = pairing_summary(state_db=state_db, channel_id=args.channel_id)
    print(report.to_json() if args.json else report.to_text())
    return 0


def handle_operator_approve_pairing(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    result = approve_pairing(
        state_db=state_db,
        channel_id=args.channel_id,
        external_user_id=args.external_user_id,
        display_name=args.display_name,
    )
    log_operator_event(
        state_db=state_db,
        action="approve_pairing",
        target_kind="pairing",
        target_ref=f"{args.channel_id}:{args.external_user_id}",
        reason=args.reason,
        details={"display_name": args.display_name},
    )
    print(result)
    return 0


def handle_operator_approve_latest(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    external_user_id = peek_latest_pairing_external_user_id(
        state_db=state_db,
        channel_id=args.channel_id,
        statuses=("pending",),
    )
    result = approve_latest_pairing(
        state_db=state_db,
        channel_id=args.channel_id,
        display_name=args.display_name,
    )
    log_operator_event(
        state_db=state_db,
        action="approve_latest_pairing",
        target_kind="pairing",
        target_ref=f"{args.channel_id}:{external_user_id}",
        reason=args.reason,
        details={"display_name": args.display_name},
    )
    print(result)
    return 0


def handle_operator_hold_latest(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    external_user_id = peek_latest_pairing_external_user_id(
        state_db=state_db,
        channel_id=args.channel_id,
        statuses=("pending",),
    )
    result = hold_latest_pairing(
        state_db=state_db,
        channel_id=args.channel_id,
    )
    log_operator_event(
        state_db=state_db,
        action="hold_latest_pairing",
        target_kind="pairing",
        target_ref=f"{args.channel_id}:{external_user_id}",
        reason=args.reason,
    )
    print(result)
    return 0


def handle_operator_revoke_latest(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    external_user_id = peek_latest_pairing_external_user_id(
        state_db=state_db,
        channel_id=args.channel_id,
        statuses=("pending", "held"),
    )
    result = revoke_latest_pairing(
        state_db=state_db,
        channel_id=args.channel_id,
    )
    log_operator_event(
        state_db=state_db,
        action="revoke_latest_pairing",
        target_kind="pairing",
        target_ref=f"{args.channel_id}:{external_user_id}",
        reason=args.reason,
    )
    print(result)
    return 0


def handle_operator_hold_pairing(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    result = hold_pairing(state_db=state_db, channel_id=args.channel_id, external_user_id=args.external_user_id)
    log_operator_event(
        state_db=state_db,
        action="hold_pairing",
        target_kind="pairing",
        target_ref=f"{args.channel_id}:{args.external_user_id}",
        reason=args.reason,
    )
    print(result)
    return 0


def handle_operator_set_channel(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    result = set_channel_status(
        config_manager=config_manager,
        state_db=state_db,
        channel_id=args.channel_id,
        status=args.mode,
    )
    log_operator_event(
        state_db=state_db,
        action="set_channel",
        target_kind="channel",
        target_ref=args.channel_id,
        reason=args.reason,
        details={"status": args.mode},
    )
    print(result)
    return 0


def handle_operator_history(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    report = list_operator_events(
        state_db,
        limit=args.limit,
        action=args.action,
        target_kind=args.target_kind,
        contains=args.contains,
    )
    print(report.to_json() if args.json else report.to_text())
    return 0


def handle_operator_inbox(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    report = build_operator_inbox(config_manager=config_manager, state_db=state_db)
    print(report.to_json() if args.json else report.to_text())
    return 0


def handle_operator_security(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    report = build_operator_security_report(config_manager=config_manager, state_db=state_db, limit=args.limit)
    print(report.to_json() if args.json else report.to_text())
    return 0


def handle_status(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()

    doctor_report = run_doctor(config_manager, state_db)
    auth_report = build_auth_status_report(config_manager=config_manager, state_db=state_db)
    gateway = gateway_status(config_manager, state_db)
    researcher = researcher_bridge_status(config_manager=config_manager, state_db=state_db)
    swarm = swarm_status(config_manager, state_db)
    attachments = attachment_status(config_manager)
    active_chip_keys = config_manager.get_path("spark.chips.active_keys", default=[]) or []
    active_path_key = config_manager.get_path("spark.specialization_paths.active_path_key")

    payload = {
        "doctor": {"ok": doctor_report.ok, "checks": [{"name": check.name, "ok": check.ok, "detail": check.detail} for check in doctor_report.checks]},
        "auth": json.loads(auth_report.to_json()),
        "gateway": json.loads(gateway.to_json()),
        "researcher": json.loads(researcher.to_json()),
        "swarm": json.loads(swarm.to_json()),
        "attachments": {
            "record_count": len(attachments.records),
            "warning_count": len(attachments.warnings),
            "chip_count": len([record for record in attachments.records if record.kind == "chip"]),
            "path_count": len([record for record in attachments.records if record.kind == "path"]),
            "active_chip_keys": active_chip_keys,
            "active_path_key": active_path_key,
            "snapshot_path": str(config_manager.paths.home / "attachments.snapshot.json"),
        },
    }
    status = SystemStatus(
        doctor_ok=doctor_report.ok,
        gateway_ready=gateway.ready,
        researcher_available=researcher.available,
        swarm_payload_ready=swarm.payload_ready,
        attachment_warning_count=len(attachments.warnings),
        payload=payload,
    )
    print(status.to_json() if args.json else status.to_text())
    return 0 if doctor_report.ok else 1


def handle_gateway_start(args: argparse.Namespace) -> int:
    if args.once and args.continuous:
        print("Choose either --once or --continuous, not both.", file=sys.stderr)
        return 2
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    report = gateway_start(
        config_manager,
        state_db,
        once=args.once,
        continuous=args.continuous,
        max_cycles=args.max_cycles,
        poll_timeout_seconds=args.poll_timeout_seconds,
    )
    print(report.text)
    return 0 if report.ok else 1


def handle_gateway_status(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    status = gateway_status(config_manager, state_db)
    if args.json:
        print(status.to_json())
    else:
        print(status.to_text())
    return 0 if status.ready else 1


def handle_gateway_oauth_callback(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    redirect_uri = args.redirect_uri or pending_oauth_redirect_uri(
        state_db=state_db,
        provider_id=args.provider,
    )
    if not redirect_uri:
        print("No pending OAuth callback redirect URI was found. Start auth login first or pass --redirect-uri.", file=sys.stderr)
        return 1
    try:
        result = serve_gateway_oauth_callback(
            config_manager=config_manager,
            state_db=state_db,
            redirect_uri=redirect_uri,
            expected_provider=args.provider,
            timeout_seconds=args.timeout_seconds,
        )
    except (RuntimeError, ValueError, TimeoutError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(result.to_json() if args.json else result.to_text())
    return 0


def handle_gateway_simulate_telegram_update(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    print(
        gateway_simulate_telegram_update(
            config_manager,
            state_db,
            Path(args.update_file),
            as_json=args.json,
        )
    )
    return 0


def handle_gateway_simulate_discord_message(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    print(
        gateway_simulate_discord_message(
            config_manager,
            state_db,
            Path(args.message_file),
            as_json=args.json,
        )
    )
    return 0


def handle_gateway_simulate_whatsapp_message(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    print(
        gateway_simulate_whatsapp_message(
            config_manager,
            state_db,
            Path(args.message_file),
            as_json=args.json,
        )
    )
    return 0


def handle_gateway_traces(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    config_manager.bootstrap()
    print(
        gateway_trace_view(
            config_manager,
            limit=args.limit,
            channel_id=args.channel_id,
            event=args.event,
            user=args.user,
            decision=args.decision,
            as_json=args.json,
        )
    )
    return 0


def handle_gateway_outbound(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    config_manager.bootstrap()
    print(
        gateway_outbound_view(
            config_manager,
            limit=args.limit,
            channel_id=args.channel_id,
            event=args.event,
            user=args.user,
            decision=args.decision,
            delivery=args.delivery,
            contains=args.contains,
            as_json=args.json,
        )
    )
    return 0


def handle_channel_add(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    if args.clear_allowed_users and args.allowed_user:
        print("Cannot combine --clear-allowed-users with --allowed-user.", file=sys.stderr)
        return 2
    existing_record = config_manager.get_path(f"channels.records.{args.channel_kind}", default={}) or {}
    existing_allowed_users = existing_record.get("allowed_users") if isinstance(existing_record, dict) else []
    existing_pairing_mode = existing_record.get("pairing_mode") if isinstance(existing_record, dict) else None
    if args.clear_allowed_users:
        effective_allowed_users: list[str] = []
    else:
        effective_allowed_users = args.allowed_user or (existing_allowed_users if isinstance(existing_allowed_users, list) else [])
    effective_pairing_mode = args.pairing_mode or (str(existing_pairing_mode) if existing_pairing_mode else "pairing")
    metadata: dict[str, object] | None = None
    validation_note: str | None = None
    if args.webhook_secret:
        if args.channel_kind == "discord":
            env_key = args.webhook_secret_env or "DISCORD_WEBHOOK_SECRET"
        elif args.channel_kind == "whatsapp":
            env_key = args.webhook_secret_env or "WHATSAPP_WEBHOOK_SECRET"
        else:
            print("--webhook-secret is only supported for webhook-based adapters.", file=sys.stderr)
            return 2
        config_manager.upsert_env_secret(env_key, args.webhook_secret)
        metadata = {**(metadata or {}), "webhook_auth_ref": env_key}
    if args.channel_kind == "telegram" and args.bot_token and not args.skip_validate:
        try:
            profile = inspect_telegram_bot_token(args.bot_token)
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            print("", file=sys.stderr)
            print(
                render_telegram_botfather_guide(
                    allowed_users=effective_allowed_users,
                    pairing_mode=effective_pairing_mode,
                ),
                file=sys.stderr,
            )
            return 1
        metadata = {"bot_profile": profile.to_dict()}
        validation_note = (
            f"Validated Telegram bot @{profile.username or 'unknown'} "
            f"(id={profile.bot_id}, first_name={profile.first_name or 'unknown'})."
        )
    result = add_channel(
        config_manager=config_manager,
        state_db=state_db,
        channel_kind=args.channel_kind,
        bot_token=args.bot_token,
        allowed_users=effective_allowed_users,
        pairing_mode=effective_pairing_mode,
        metadata=metadata,
    )
    if validation_note:
        print(validation_note)
    print(result)
    if args.channel_kind == "telegram":
        print("Next Telegram steps:")
        print("  1. Open Telegram and send /start to the bot from the account you want to pair.")
        print("  2. Run spark-intelligence gateway start")
        print("  3. Use spark-intelligence operator review-pairings if pairing approval is required.")
    return 0


def handle_channel_telegram_onboard(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    if args.clear_allowed_users and args.allowed_user:
        print("Cannot combine --clear-allowed-users with --allowed-user.", file=sys.stderr)
        return 2
    existing_record = config_manager.get_path("channels.records.telegram", default={}) or {}
    existing_allowed_users = existing_record.get("allowed_users") if isinstance(existing_record, dict) else []
    existing_pairing_mode = existing_record.get("pairing_mode") if isinstance(existing_record, dict) else None
    existing_status = existing_record.get("status") if isinstance(existing_record, dict) else None
    if args.clear_allowed_users:
        effective_allowed_users: list[str] = []
    else:
        effective_allowed_users = args.allowed_user or (existing_allowed_users if isinstance(existing_allowed_users, list) else [])
    effective_pairing_mode = args.pairing_mode or (str(existing_pairing_mode) if existing_pairing_mode else "pairing")
    effective_status = str(existing_status) if existing_status else "enabled"

    if not args.bot_token:
        print(
            render_telegram_botfather_guide(
                allowed_users=effective_allowed_users,
                pairing_mode=effective_pairing_mode,
            )
        )
        return 0

    if args.skip_validate:
        profile = None
    else:
        try:
            profile = inspect_telegram_bot_token(args.bot_token)
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr)
            print("", file=sys.stderr)
            print(
                render_telegram_botfather_guide(
                    allowed_users=effective_allowed_users,
                    pairing_mode=effective_pairing_mode,
                ),
                file=sys.stderr,
            )
            return 1

    if profile:
        print(
            f"Validated Telegram bot @{profile.username or 'unknown'} "
            f"(id={profile.bot_id}, first_name={profile.first_name or 'unknown'})."
        )
        if args.validate_only:
            print("Token validation passed. No config was changed.")
            return 0
    elif args.validate_only:
        print("Cannot use --validate-only together with --skip-validate.", file=sys.stderr)
        return 2

    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    result = add_channel(
        config_manager=config_manager,
        state_db=state_db,
        channel_kind="telegram",
        bot_token=args.bot_token,
        allowed_users=effective_allowed_users,
        pairing_mode=effective_pairing_mode,
        status=effective_status,
        metadata={"bot_profile": profile.to_dict()} if profile else None,
    )
    print(result)
    print("Telegram onboarding next steps:")
    print("  1. Open Telegram and send /start to the bot.")
    if effective_allowed_users:
        print("  2. Confirm the listed allowed user ids are the accounts you want paired first.")
    else:
        print("  2. Run spark-intelligence operator review-pairings after the first DM arrives.")
    print("  3. Run spark-intelligence gateway start")
    return 0


def handle_channel_test(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    if args.channel_kind == "telegram":
        report = test_configured_telegram_channel(
            config_manager=config_manager,
            state_db=state_db,
        )
        print(report.to_json() if args.json else report.to_text())
        return 0 if report.ok else 1
    print(f"Unsupported channel test target: {args.channel_kind}", file=sys.stderr)
    return 2


def handle_attachments_status(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    config_manager.bootstrap()
    result = attachment_status(config_manager)
    print(result.to_json() if args.json else result.to_text())
    return 0


def handle_attachments_list(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    config_manager.bootstrap()
    result = list_attachments(config_manager, kind=args.kind)
    print(result.to_json() if args.json else result.to_text())
    return 0


def handle_attachments_add_root(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    values = add_attachment_root(config_manager, target=args.target, root=args.root)
    sync_attachment_snapshot(config_manager=config_manager, state_db=state_db)
    dotted_path = "spark.chips.roots" if args.target == "chips" else "spark.specialization_paths.roots"
    print(f"Updated {dotted_path}:")
    for value in values:
        print(f"- {value}")
    return 0


def handle_attachments_snapshot(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    snapshot = sync_attachment_snapshot(config_manager=config_manager, state_db=state_db)
    print(snapshot.to_json() if args.json else snapshot.to_text())
    return 0


def handle_attachments_activate_chip(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    active_keys = activate_chip(config_manager, chip_key=args.chip_key)
    snapshot = sync_attachment_snapshot(config_manager=config_manager, state_db=state_db)
    print(f"Active chips: {', '.join(active_keys) if active_keys else 'none'}")
    print(f"Snapshot: {snapshot.snapshot_path}")
    return 0


def handle_attachments_deactivate_chip(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    active_keys = deactivate_chip(config_manager, chip_key=args.chip_key)
    snapshot = sync_attachment_snapshot(config_manager=config_manager, state_db=state_db)
    print(f"Active chips: {', '.join(active_keys) if active_keys else 'none'}")
    print(f"Snapshot: {snapshot.snapshot_path}")
    return 0


def handle_attachments_pin_chip(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    active_keys = pin_chip(config_manager, chip_key=args.chip_key)
    snapshot = sync_attachment_snapshot(config_manager=config_manager, state_db=state_db)
    print(f"Active chips: {', '.join(active_keys) if active_keys else 'none'}")
    print(f"Snapshot: {snapshot.snapshot_path}")
    return 0


def handle_attachments_unpin_chip(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    pinned_keys = unpin_chip(config_manager, chip_key=args.chip_key)
    snapshot = sync_attachment_snapshot(config_manager=config_manager, state_db=state_db)
    print(f"Pinned chips: {', '.join(pinned_keys) if pinned_keys else 'none'}")
    print(f"Snapshot: {snapshot.snapshot_path}")
    return 0


def handle_attachments_set_path(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    path_key = set_active_path(config_manager, path_key=args.path_key)
    snapshot = sync_attachment_snapshot(config_manager=config_manager, state_db=state_db)
    print(f"Active path: {path_key}")
    print(f"Snapshot: {snapshot.snapshot_path}")
    return 0


def handle_attachments_clear_path(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    clear_active_path(config_manager)
    snapshot = sync_attachment_snapshot(config_manager=config_manager, state_db=state_db)
    print("Active path: none")
    print(f"Snapshot: {snapshot.snapshot_path}")
    return 0


def handle_auth_connect(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    result = connect_provider(
        config_manager=config_manager,
        state_db=state_db,
        provider=args.provider,
        api_key=args.api_key,
        api_key_env=args.api_key_env,
        model=args.model,
        base_url=args.base_url,
    )
    print(result)
    return 0


def handle_auth_providers(args: argparse.Namespace) -> int:
    payload = {
        "providers": [
            {
                "id": spec.id,
                "display_name": spec.display_name,
                "auth_methods": list(spec.auth_methods),
                "default_model": spec.default_model,
                "default_base_url": spec.default_base_url,
                "default_api_key_env": spec.default_api_key_env,
                "execution_transport": spec.execution_transport,
                "oauth_redirect_uri": spec.oauth.redirect_uri if spec.oauth else None,
            }
            for spec in list_provider_specs()
        ]
    }
    if args.json:
        print(json.dumps(payload, indent=2))
        return 0

    print("Supported providers")
    for provider in payload["providers"]:
        methods = ", ".join(provider["auth_methods"])
        print(f"- {provider['id']}: {provider['display_name']} ({methods})")
        if provider["default_model"]:
            print(f"  default_model={provider['default_model']}")
        if provider["default_base_url"]:
            print(f"  default_base_url={provider['default_base_url']}")
        if provider["default_api_key_env"]:
            print(f"  default_api_key_env={provider['default_api_key_env']}")
        print(f"  execution_transport={provider['execution_transport']}")
        if provider["oauth_redirect_uri"]:
            print(f"  oauth_redirect_uri={provider['oauth_redirect_uri']}")
    return 0


def handle_auth_login(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()

    if args.listen and args.callback_url:
        print("Cannot combine --listen with --callback-url.", file=sys.stderr)
        return 2

    if args.callback_url:
        result = complete_oauth_login(
            config_manager=config_manager,
            state_db=state_db,
            provider=args.provider,
            callback_url=args.callback_url,
        )
        print(result.to_json() if args.json else result.to_text())
        return 0

    start = start_oauth_login(
        config_manager=config_manager,
        state_db=state_db,
        provider=args.provider,
        redirect_uri=args.redirect_uri,
    )

    if not args.listen:
        print(start.to_json() if args.json else start.to_text())
        return 0

    if not args.json:
        print(start.to_text(), flush=True)
        print("", flush=True)
        print(
            f"Waiting up to {args.timeout_seconds} seconds for OAuth callback on {start.redirect_uri} ...",
            flush=True,
        )
        print("", flush=True)

    try:
        gateway_result = serve_gateway_oauth_callback(
            config_manager=config_manager,
            state_db=state_db,
            redirect_uri=start.redirect_uri,
            expected_provider=args.provider,
            timeout_seconds=args.timeout_seconds,
        )
    except (RuntimeError, ValueError, TimeoutError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    if args.json:
        print(
            json.dumps(
                {
                    "start": json.loads(start.to_json()),
                    "callback": {
                        "callback_url": gateway_result.callback_url,
                        "path": gateway_result.path,
                        "query": gateway_result.query,
                    },
                    "result": json.loads(gateway_result.to_json()),
                },
                indent=2,
            )
        )
        return 0

    print(gateway_result.to_text())
    return 0


def handle_auth_logout(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    result = logout_provider(
        config_manager=config_manager,
        state_db=state_db,
        provider=args.provider,
    )
    print(result.to_json() if args.json else result.to_text())
    return 0


def handle_auth_refresh(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    try:
        result = refresh_provider(
            config_manager=config_manager,
            state_db=state_db,
            provider=args.provider,
        )
    except (RuntimeError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    print(result.to_json() if args.json else result.to_text())
    return 0


def handle_auth_status(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    report = build_auth_status_report(config_manager=config_manager, state_db=state_db)
    print(report.to_json() if args.json else report.to_text())
    return 0 if report.ok else 1


def handle_researcher_status(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    status = researcher_bridge_status(config_manager=config_manager, state_db=state_db)
    print(status.to_json() if args.json else status.to_text())
    return 0 if status.available else 1


def handle_config_show(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    config_manager.bootstrap()
    value = config_manager.get_path(args.path) if args.path else config_manager.load()
    if args.json:
        print(json.dumps(value, indent=2))
    elif isinstance(value, (str, int, float, bool)) or value is None:
        print("null" if value is None else value)
    else:
        print(yaml.safe_dump(value, sort_keys=False).rstrip())
    return 0


def handle_config_set(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    config_manager.bootstrap()
    parsed_value = yaml.safe_load(args.value)
    config_manager.set_path(args.path, parsed_value)
    print(f"Set {args.path} = {json.dumps(parsed_value)}")
    return 0


def handle_config_unset(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    config_manager.bootstrap()
    removed = config_manager.unset_path(args.path)
    if removed:
        print(f"Removed {args.path}")
        return 0
    print(f"Config path not found: {args.path}", file=sys.stderr)
    return 1


def handle_swarm_status(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    status = swarm_status(config_manager, state_db)
    print(status.to_json() if args.json else status.to_text())
    return 0 if status.payload_ready else 1


def handle_swarm_configure(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    config_manager.bootstrap()
    _configure_swarm(
        config_manager,
        api_url=args.api_url,
        workspace_id=args.workspace_id,
        runtime_root=args.runtime_root,
        access_token=args.access_token,
        access_token_env=args.access_token_env,
    )
    print("Spark Swarm bridge config updated.")
    print("Recommended checks:")
    print("  1. spark-intelligence swarm status")
    print("  2. spark-intelligence swarm sync --dry-run")
    print("  3. spark-intelligence swarm sync")
    return 0


def handle_swarm_sync(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    result = sync_swarm_collective(config_manager=config_manager, state_db=state_db, dry_run=args.dry_run)
    print(result.to_json() if args.json else result.to_text())
    return 0 if result.ok else 1


def handle_swarm_evaluate(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    result = evaluate_swarm_escalation(config_manager=config_manager, state_db=state_db, task=args.task)
    print(result.to_json() if args.json else result.to_text())
    return 0 if result.ok else 1


def _apply_setup_integrations(config_manager: ConfigManager, args: argparse.Namespace) -> list[str]:
    notes: list[str] = []

    researcher_root_value = args.researcher_root
    if not researcher_root_value:
        current_root = config_manager.get_path("spark.researcher.runtime_root")
        discovered_root, source = discover_researcher_runtime_root(config_manager)
        if not current_root and discovered_root and source == "autodiscovered":
            researcher_root_value = str(discovered_root)
            notes.append(f"autoconnected spark-researcher at {discovered_root}")

    if researcher_root_value:
        normalized_root = str(Path(researcher_root_value).expanduser())
        config_manager.set_path("spark.researcher.runtime_root", normalized_root)
        notes.append(f"configured spark.researcher.runtime_root = {normalized_root}")
        if args.researcher_config:
            normalized_config = str(Path(args.researcher_config).expanduser())
            config_manager.set_path("spark.researcher.config_path", normalized_config)
            notes.append(f"configured spark.researcher.config_path = {normalized_config}")
        else:
            resolved = resolve_researcher_config_path(config_manager, Path(normalized_root))
            if resolved.exists():
                config_manager.set_path("spark.researcher.config_path", str(resolved))
                notes.append(f"discovered spark-researcher config at {resolved}")

    swarm_runtime_root = args.swarm_runtime_root
    if not swarm_runtime_root:
        current_swarm_root = config_manager.get_path("spark.swarm.runtime_root")
        default_swarm_root = Path.home() / "Desktop" / "spark-swarm"
        if not current_swarm_root and default_swarm_root.exists():
            swarm_runtime_root = str(default_swarm_root)
            notes.append(f"autoconnected spark-swarm at {default_swarm_root}")

    if any(
        [
            swarm_runtime_root,
            args.swarm_api_url,
            args.swarm_workspace_id,
            args.swarm_access_token,
        ]
    ):
        _configure_swarm(
            config_manager,
            api_url=args.swarm_api_url,
            workspace_id=args.swarm_workspace_id,
            runtime_root=swarm_runtime_root,
            access_token=args.swarm_access_token,
            access_token_env=args.swarm_access_token_env if args.swarm_access_token else None,
        )
        if args.swarm_api_url:
            notes.append(f"configured spark.swarm.api_url = {args.swarm_api_url.rstrip('/')}")
        if args.swarm_workspace_id:
            notes.append(f"configured spark.swarm.workspace_id = {args.swarm_workspace_id}")
        if swarm_runtime_root:
            notes.append(f"configured spark.swarm.runtime_root = {Path(swarm_runtime_root).expanduser()}")
        if args.swarm_access_token:
            notes.append(f"stored spark.swarm access token in {args.swarm_access_token_env}")

    attachment_scan = attachment_status(config_manager)
    if attachment_scan.chip_source == "autodiscovered" and attachment_scan.chip_roots:
        config_manager.set_path("spark.chips.roots", attachment_scan.chip_roots)
        notes.append(f"autoconnected {len(attachment_scan.chip_roots)} chip root(s)")
    if attachment_scan.path_source == "autodiscovered" and attachment_scan.path_roots:
        config_manager.set_path("spark.specialization_paths.roots", attachment_scan.path_roots)
        notes.append(f"autoconnected {len(attachment_scan.path_roots)} specialization path root(s)")

    state_db = StateDB(config_manager.paths.state_db)
    state_db.initialize()
    snapshot = sync_attachment_snapshot(config_manager=config_manager, state_db=state_db)
    notes.append(f"wrote attachment snapshot to {snapshot.snapshot_path}")

    return notes


def _configure_swarm(
    config_manager: ConfigManager,
    *,
    api_url: str | None,
    workspace_id: str | None,
    runtime_root: str | None,
    access_token: str | None,
    access_token_env: str | None,
) -> None:
    if api_url:
        config_manager.set_path("spark.swarm.api_url", api_url.rstrip("/"))
    if workspace_id:
        config_manager.set_path("spark.swarm.workspace_id", workspace_id)
        config_manager.upsert_env_secret("SPARK_SWARM_WORKSPACE_ID", workspace_id)
    if runtime_root:
        config_manager.set_path("spark.swarm.runtime_root", str(Path(runtime_root).expanduser()))
    if access_token:
        env_name = access_token_env or "SPARK_SWARM_ACCESS_TOKEN"
        config_manager.upsert_env_secret(env_name, access_token)
        config_manager.set_path("spark.swarm.access_token_env", env_name)
    elif access_token_env:
        config_manager.set_path("spark.swarm.access_token_env", access_token_env)


def handle_jobs_tick(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    print(jobs_tick(config_manager, state_db))
    return 0


def handle_jobs_list(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    print(jobs_list(state_db))
    return 0


def handle_agent_inspect(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    owner = config_manager.load().get("workspace", {}).get("owner_human_id", "local-operator")
    report = agent_inspect(state_db=state_db, workspace_owner=owner)
    attachments = attachment_status(config_manager)
    report.payload["attachments"] = {
        "chip_source": attachments.chip_source,
        "path_source": attachments.path_source,
        "chip_count": len([record for record in attachments.records if record.kind == "chip"]),
        "path_count": len([record for record in attachments.records if record.kind == "path"]),
        "warning_count": len(attachments.warnings),
        "active_chip_keys": config_manager.get_path("spark.chips.active_keys", default=[]) or [],
        "pinned_chip_keys": config_manager.get_path("spark.chips.pinned_keys", default=[]) or [],
        "active_path_key": config_manager.get_path("spark.specialization_paths.active_path_key"),
        "snapshot_path": str(config_manager.paths.home / "attachments.snapshot.json"),
    }
    if args.json:
        print(report.to_json())
    else:
        print(report.to_text())
    return 0


def handle_pairings_list(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    print(list_pairings(state_db))
    return 0


def handle_pairings_approve(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    print(
        approve_pairing(
            state_db=state_db,
            channel_id=args.channel_id,
            external_user_id=args.external_user_id,
            display_name=args.display_name,
        )
    )
    return 0


def handle_pairings_revoke(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    print(revoke_pairing(state_db=state_db, channel_id=args.channel_id, external_user_id=args.external_user_id))
    return 0


def handle_sessions_list(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    print(list_sessions(state_db))
    return 0


def handle_sessions_revoke(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    print(revoke_session(state_db=state_db, session_id=args.session_id))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "setup":
        return handle_setup(args)
    if args.command == "doctor":
        return handle_doctor(args)
    if args.command == "status":
        return handle_status(args)
    if args.command == "operator" and args.operator_command == "set-bridge":
        return handle_operator_set_bridge(args)
    if args.command == "operator" and args.operator_command == "review-pairings":
        return handle_operator_review_pairings(args)
    if args.command == "operator" and args.operator_command == "pairing-summary":
        return handle_operator_pairing_summary(args)
    if args.command == "operator" and args.operator_command == "approve-pairing":
        return handle_operator_approve_pairing(args)
    if args.command == "operator" and args.operator_command == "approve-latest":
        return handle_operator_approve_latest(args)
    if args.command == "operator" and args.operator_command == "hold-latest":
        return handle_operator_hold_latest(args)
    if args.command == "operator" and args.operator_command == "revoke-latest":
        return handle_operator_revoke_latest(args)
    if args.command == "operator" and args.operator_command == "hold-pairing":
        return handle_operator_hold_pairing(args)
    if args.command == "operator" and args.operator_command == "set-channel":
        return handle_operator_set_channel(args)
    if args.command == "operator" and args.operator_command == "history":
        return handle_operator_history(args)
    if args.command == "operator" and args.operator_command == "inbox":
        return handle_operator_inbox(args)
    if args.command == "operator" and args.operator_command == "security":
        return handle_operator_security(args)
    if args.command == "gateway" and args.gateway_command == "start":
        return handle_gateway_start(args)
    if args.command == "gateway" and args.gateway_command == "status":
        return handle_gateway_status(args)
    if args.command == "gateway" and args.gateway_command == "oauth-callback":
        return handle_gateway_oauth_callback(args)
    if args.command == "gateway" and args.gateway_command == "simulate-telegram-update":
        return handle_gateway_simulate_telegram_update(args)
    if args.command == "gateway" and args.gateway_command == "simulate-discord-message":
        return handle_gateway_simulate_discord_message(args)
    if args.command == "gateway" and args.gateway_command == "simulate-whatsapp-message":
        return handle_gateway_simulate_whatsapp_message(args)
    if args.command == "gateway" and args.gateway_command == "traces":
        return handle_gateway_traces(args)
    if args.command == "gateway" and args.gateway_command == "outbound":
        return handle_gateway_outbound(args)
    if args.command == "channel" and args.channel_command == "add":
        return handle_channel_add(args)
    if args.command == "channel" and args.channel_command == "telegram-onboard":
        return handle_channel_telegram_onboard(args)
    if args.command == "channel" and args.channel_command == "test":
        return handle_channel_test(args)
    if args.command == "attachments" and args.attachments_command == "status":
        return handle_attachments_status(args)
    if args.command == "attachments" and args.attachments_command == "list":
        return handle_attachments_list(args)
    if args.command == "attachments" and args.attachments_command == "add-root":
        return handle_attachments_add_root(args)
    if args.command == "attachments" and args.attachments_command == "snapshot":
        return handle_attachments_snapshot(args)
    if args.command == "attachments" and args.attachments_command == "activate-chip":
        return handle_attachments_activate_chip(args)
    if args.command == "attachments" and args.attachments_command == "deactivate-chip":
        return handle_attachments_deactivate_chip(args)
    if args.command == "attachments" and args.attachments_command == "pin-chip":
        return handle_attachments_pin_chip(args)
    if args.command == "attachments" and args.attachments_command == "unpin-chip":
        return handle_attachments_unpin_chip(args)
    if args.command == "attachments" and args.attachments_command == "set-path":
        return handle_attachments_set_path(args)
    if args.command == "attachments" and args.attachments_command == "clear-path":
        return handle_attachments_clear_path(args)
    if args.command == "auth" and args.auth_command == "providers":
        return handle_auth_providers(args)
    if args.command == "auth" and args.auth_command == "connect":
        return handle_auth_connect(args)
    if args.command == "auth" and args.auth_command == "login":
        return handle_auth_login(args)
    if args.command == "auth" and args.auth_command == "logout":
        return handle_auth_logout(args)
    if args.command == "auth" and args.auth_command == "refresh":
        return handle_auth_refresh(args)
    if args.command == "auth" and args.auth_command == "status":
        return handle_auth_status(args)
    if args.command == "researcher" and args.researcher_command == "status":
        return handle_researcher_status(args)
    if args.command == "config" and args.config_command == "show":
        return handle_config_show(args)
    if args.command == "config" and args.config_command == "set":
        return handle_config_set(args)
    if args.command == "config" and args.config_command == "unset":
        return handle_config_unset(args)
    if args.command == "swarm" and args.swarm_command == "status":
        return handle_swarm_status(args)
    if args.command == "swarm" and args.swarm_command == "configure":
        return handle_swarm_configure(args)
    if args.command == "swarm" and args.swarm_command == "sync":
        return handle_swarm_sync(args)
    if args.command == "swarm" and args.swarm_command == "evaluate":
        return handle_swarm_evaluate(args)
    if args.command == "jobs" and args.jobs_command == "tick":
        return handle_jobs_tick(args)
    if args.command == "jobs" and args.jobs_command == "list":
        return handle_jobs_list(args)
    if args.command == "agent" and args.agent_command == "inspect":
        return handle_agent_inspect(args)
    if args.command == "pairings" and args.pairings_command == "list":
        return handle_pairings_list(args)
    if args.command == "pairings" and args.pairings_command == "approve":
        return handle_pairings_approve(args)
    if args.command == "pairings" and args.pairings_command == "revoke":
        return handle_pairings_revoke(args)
    if args.command == "sessions" and args.sessions_command == "list":
        return handle_sessions_list(args)
    if args.command == "sessions" and args.sessions_command == "revoke":
        return handle_sessions_revoke(args)

    parser.error("Unknown command")
    return 2


if __name__ == "__main__":
    sys.exit(main())
