from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

from spark_intelligence.auth.service import connect_provider
from spark_intelligence.channel.service import add_channel
from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.doctor.checks import run_doctor
from spark_intelligence.gateway.runtime import gateway_simulate_telegram_update, gateway_start, gateway_status, gateway_trace_view
from spark_intelligence.identity.service import (
    agent_inspect,
    approve_pairing,
    list_pairings,
    list_sessions,
    revoke_pairing,
    revoke_session,
)
from spark_intelligence.jobs.service import jobs_list, jobs_tick
from spark_intelligence.state.db import StateDB
from spark_intelligence.swarm_bridge import swarm_status, sync_swarm_collective


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="spark-intelligence")
    subparsers = parser.add_subparsers(dest="command", required=True)

    setup_parser = subparsers.add_parser("setup", help="Bootstrap config and state")
    setup_parser.add_argument("--home", help="Override Spark Intelligence home directory")

    doctor_parser = subparsers.add_parser("doctor", help="Run environment and state checks")
    doctor_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    doctor_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")

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
    gateway_simulate_parser = gateway_subparsers.add_parser(
        "simulate-telegram-update",
        help="Simulate one Telegram update through normalization and authorization routing",
    )
    gateway_simulate_parser.add_argument("update_file", help="Path to a Telegram update JSON file")
    gateway_simulate_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    gateway_simulate_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")
    gateway_traces_parser = gateway_subparsers.add_parser("traces", help="Show recent gateway traces")
    gateway_traces_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    gateway_traces_parser.add_argument("--limit", type=int, default=20, help="Number of trace events to show")
    gateway_traces_parser.add_argument("--json", action="store_true", help="Emit machine-readable output")

    channel_parser = subparsers.add_parser("channel", help="Manage channel adapters")
    channel_subparsers = channel_parser.add_subparsers(dest="channel_command", required=True)
    channel_add_parser = channel_subparsers.add_parser("add", help="Add a channel adapter")
    channel_add_parser.add_argument("channel_kind", choices=["telegram"], help="Adapter kind")
    channel_add_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    channel_add_parser.add_argument("--bot-token", help="Telegram bot token")
    channel_add_parser.add_argument("--allowed-user", action="append", default=[], help="Allowed Telegram user id")
    channel_add_parser.add_argument(
        "--pairing-mode",
        choices=["allowlist", "pairing"],
        default="pairing",
        help="Inbound DM authorization mode",
    )

    auth_parser = subparsers.add_parser("auth", help="Manage model providers")
    auth_subparsers = auth_parser.add_subparsers(dest="auth_command", required=True)
    auth_connect_parser = auth_subparsers.add_parser("connect", help="Connect a model provider")
    auth_connect_parser.add_argument("provider", choices=["openai", "anthropic", "openrouter", "custom"])
    auth_connect_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    auth_connect_parser.add_argument("--api-key", help="API key for the provider")
    auth_connect_parser.add_argument("--model", help="Default model id")
    auth_connect_parser.add_argument("--base-url", help="Custom provider base URL")

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

    jobs_parser = subparsers.add_parser("jobs", help="Inspect and execute jobs")
    jobs_subparsers = jobs_parser.add_subparsers(dest="jobs_command", required=True)
    jobs_tick_parser = jobs_subparsers.add_parser("tick", help="Run due scheduled work once")
    jobs_tick_parser.add_argument("--home", help="Override Spark Intelligence home directory")
    jobs_list_parser = jobs_subparsers.add_parser("list", help="List known jobs")
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
    print(f"Spark Intelligence home: {config_manager.paths.home}")
    if created:
        print("Created config, env, and state bootstrap.")
    else:
        print("Existing config and env preserved; verified state bootstrap.")
    print("Next steps:")
    print("  1. spark-intelligence auth connect openai --api-key <key> --model <model>")
    print("  2. spark-intelligence channel add telegram --bot-token <token> --allowed-user <id>")
    print("  3. spark-intelligence doctor")
    print("  4. spark-intelligence gateway start")
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
    print(report)
    return 0


def handle_gateway_status(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    status = gateway_status(config_manager, state_db)
    if args.json:
        print(status.to_json())
    else:
        print(status.to_text())
    return 0 if status.ready else 1


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


def handle_gateway_traces(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    config_manager.bootstrap()
    print(gateway_trace_view(config_manager, limit=args.limit, as_json=args.json))
    return 0


def handle_channel_add(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    result = add_channel(
        config_manager=config_manager,
        state_db=state_db,
        channel_kind=args.channel_kind,
        bot_token=args.bot_token,
        allowed_users=args.allowed_user,
        pairing_mode=args.pairing_mode,
    )
    print(result)
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
        model=args.model,
        base_url=args.base_url,
    )
    print(result)
    return 0


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
    config_manager.bootstrap()
    status = swarm_status(config_manager)
    print(status.to_json() if args.json else status.to_text())
    return 0 if status.payload_ready else 1


def handle_swarm_configure(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    config_manager.bootstrap()
    if args.api_url:
        config_manager.set_path("spark.swarm.api_url", args.api_url.rstrip("/"))
    if args.workspace_id:
        config_manager.set_path("spark.swarm.workspace_id", args.workspace_id)
        config_manager.upsert_env_secret("SPARK_SWARM_WORKSPACE_ID", args.workspace_id)
    if args.runtime_root:
        config_manager.set_path("spark.swarm.runtime_root", str(Path(args.runtime_root).expanduser()))
    if args.access_token:
        config_manager.upsert_env_secret(args.access_token_env, args.access_token)
        config_manager.set_path("spark.swarm.access_token_env", args.access_token_env)
    elif args.access_token_env:
        config_manager.set_path("spark.swarm.access_token_env", args.access_token_env)
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


def handle_jobs_tick(args: argparse.Namespace) -> int:
    config_manager = ConfigManager.from_home(args.home)
    state_db = StateDB(config_manager.paths.state_db)
    config_manager.bootstrap()
    state_db.initialize()
    print(jobs_tick(state_db))
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
    if args.command == "gateway" and args.gateway_command == "start":
        return handle_gateway_start(args)
    if args.command == "gateway" and args.gateway_command == "status":
        return handle_gateway_status(args)
    if args.command == "gateway" and args.gateway_command == "simulate-telegram-update":
        return handle_gateway_simulate_telegram_update(args)
    if args.command == "gateway" and args.gateway_command == "traces":
        return handle_gateway_traces(args)
    if args.command == "channel" and args.channel_command == "add":
        return handle_channel_add(args)
    if args.command == "auth" and args.auth_command == "connect":
        return handle_auth_connect(args)
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
