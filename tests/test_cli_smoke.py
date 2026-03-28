from __future__ import annotations

import json
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import URLError

from spark_intelligence.channel.service import TelegramBotProfile
from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.gateway.discord_webhook import DISCORD_WEBHOOK_PATH, handle_discord_webhook
from spark_intelligence.gateway.whatsapp_webhook import WHATSAPP_WEBHOOK_PATH, handle_whatsapp_webhook
from spark_intelligence.identity.service import approve_pairing
from spark_intelligence.observability.store import record_event
from spark_intelligence.personality.loader import detect_and_persist_nl_preferences, record_observation

from tests.test_support import SparkTestCase


class CliSmokeTests(SparkTestCase):
    def test_memory_direct_smoke_runs_in_process_domain_chip_bridge(self) -> None:
        exit_code, stdout, stderr = self.run_cli(
            "memory",
            "direct-smoke",
            "--home",
            str(self.home),
            "--json",
        )

        self.assertEqual(exit_code, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["sdk_module"], "domain_chip_memory")
        self.assertTrue(payload["shadow_only_eval"])
        self.assertGreaterEqual(payload["write_result"]["accepted_count"], 1)
        self.assertTrue(payload["read_result"]["records"])
        self.assertEqual(payload["read_result"]["records"][0]["predicate"], "system.memory.smoke")
        self.assertEqual(payload["read_result"]["records"][0]["value"], "ok")
        self.assertGreaterEqual(payload["cleanup_result"]["accepted_count"], 1)

    def test_memory_status_reports_runtime_counts_and_last_smoke(self) -> None:
        with self.state_db.connect() as conn:
            conn.execute(
                """
                INSERT INTO builder_events(
                    event_id, event_type, truth_kind, target_surface, component, request_id, session_id, human_id,
                    actor_id, evidence_lane, severity, status, summary, created_at, facts_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "evt-mem-status-write",
                    "memory_write_succeeded",
                    "fact",
                    "spark_intelligence_builder",
                    "memory_orchestrator",
                    "turn-status-write",
                    "session-status",
                    "human:test",
                    "memory_cli",
                    "realworld_validated",
                    "medium",
                    "recorded",
                    "Memory write completed.",
                    "2026-03-28T09:00:00Z",
                    json.dumps(
                        {
                            "accepted_count": 2,
                            "rejected_count": 0,
                            "skipped_count": 0,
                            "memory_role": "current_state",
                        }
                    ),
                ),
            )
            conn.execute(
                """
                INSERT INTO builder_events(
                    event_id, event_type, truth_kind, target_surface, component, request_id, session_id, human_id,
                    actor_id, evidence_lane, severity, status, summary, created_at, facts_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "evt-mem-status-read",
                    "memory_read_succeeded",
                    "fact",
                    "spark_intelligence_builder",
                    "memory_orchestrator",
                    "turn-status-read",
                    "session-status",
                    "human:test",
                    "memory_cli",
                    "realworld_validated",
                    "medium",
                    "recorded",
                    "Memory read completed.",
                    "2026-03-28T09:01:00Z",
                    json.dumps(
                        {
                            "record_count": 1,
                            "shadow_only": 0,
                            "memory_role": "current_state",
                        }
                    ),
                ),
            )
            conn.execute(
                """
                INSERT INTO builder_events(
                    event_id, event_type, truth_kind, target_surface, component, actor_id, evidence_lane, severity,
                    status, summary, created_at, facts_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "evt-mem-status-smoke",
                    "memory_smoke_succeeded",
                    "fact",
                    "spark_intelligence_builder",
                    "memory_orchestrator",
                    "memory_cli",
                    "realworld_validated",
                    "medium",
                    "recorded",
                    "Spark direct memory smoke completed.",
                    "2026-03-28T09:02:00Z",
                    json.dumps(
                        {
                            "sdk_module": "domain_chip_memory",
                            "subject": "human:smoke:test",
                            "predicate": "system.memory.smoke",
                        }
                    ),
                ),
            )
            conn.execute(
                """
                INSERT INTO builder_events(
                    event_id, event_type, truth_kind, target_surface, component, actor_id, evidence_lane, severity,
                    status, summary, created_at, facts_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "evt-mem-status-fail",
                    "memory_read_abstained",
                    "fact",
                    "spark_intelligence_builder",
                    "memory_orchestrator",
                    "memory_cli",
                    "realworld_validated",
                    "high",
                    "abstained",
                    "Spark memory read abstained.",
                    "2026-03-28T09:03:00Z",
                    json.dumps({"reason": "sdk_unavailable"}),
                ),
            )
            conn.commit()

        exit_code, stdout, stderr = self.run_cli(
            "memory",
            "status",
            "--home",
            str(self.home),
            "--json",
        )

        self.assertEqual(exit_code, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["runtime"]["configured_module"], "domain_chip_memory")
        self.assertTrue(payload["runtime"]["ready"])
        self.assertEqual(payload["counts"]["accepted_observations"], 2)
        self.assertEqual(payload["counts"]["read_hits"], 1)
        self.assertEqual(payload["last_smoke"]["event_type"], "memory_smoke_succeeded")
        self.assertEqual(payload["last_smoke"]["predicate"], "system.memory.smoke")
        self.assertEqual(payload["recent_failures"][0]["event_type"], "memory_read_abstained")
        self.assertEqual(payload["recent_failures"][0]["reason"], "sdk_unavailable")

    def test_memory_lookup_current_state_reads_written_fact(self) -> None:
        smoke_exit, _, smoke_stderr = self.run_cli(
            "memory",
            "direct-smoke",
            "--home",
            str(self.home),
            "--subject",
            "human:lookup:test",
            "--predicate",
            "system.memory.lookup",
            "--value",
            "ok",
            "--no-cleanup",
            "--json",
        )
        self.assertEqual(smoke_exit, 0, smoke_stderr)

        exit_code, stdout, stderr = self.run_cli(
            "memory",
            "lookup-current-state",
            "--home",
            str(self.home),
            "--subject",
            "human:lookup:test",
            "--predicate",
            "system.memory.lookup",
            "--json",
        )

        self.assertEqual(exit_code, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["sdk_module"], "domain_chip_memory")
        self.assertTrue(payload["runtime"]["ready"])
        self.assertEqual(payload["read_result"]["records"][0]["predicate"], "system.memory.lookup")
        self.assertEqual(payload["read_result"]["records"][0]["value"], "ok")

    def test_memory_inspect_human_reports_current_facts_and_recent_events(self) -> None:
        for predicate, value in (("system.memory.one", "alpha"), ("system.memory.two", "beta")):
            smoke_exit, _, smoke_stderr = self.run_cli(
                "memory",
                "direct-smoke",
                "--home",
                str(self.home),
                "--subject",
                "human:inspect:test",
                "--predicate",
                predicate,
                "--value",
                value,
                "--no-cleanup",
                "--json",
            )
            self.assertEqual(smoke_exit, 0, smoke_stderr)

        exit_code, stdout, stderr = self.run_cli(
            "memory",
            "inspect-human",
            "--home",
            str(self.home),
            "--human-id",
            "inspect:test",
            "--json",
        )

        self.assertEqual(exit_code, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["sdk_module"], "domain_chip_memory")
        self.assertEqual(payload["human_id"], "inspect:test")
        self.assertTrue(payload["runtime"]["ready"])
        predicates = {str(record.get("predicate") or "") for record in payload["current_state"]["records"]}
        self.assertEqual(predicates, {"system.memory.one", "system.memory.two"})
        self.assertTrue(payload["recent_events"])

    def test_memory_export_shadow_replay_writes_contract_shaped_json(self) -> None:
        with self.state_db.connect() as conn:
            conn.execute(
                """
                INSERT INTO builder_events(
                    event_id, event_type, truth_kind, target_surface, component, run_id, request_id, channel_id,
                    session_id, human_id, agent_id, actor_id, evidence_lane, severity, status, summary, facts_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "evt-user-cli",
                    "intent_committed",
                    "fact",
                    "spark_intelligence_builder",
                    "telegram_runtime",
                    "run-cli",
                    "turn-cli",
                    "telegram",
                    "session-cli",
                    "human:test",
                    "agent:test",
                    "telegram_runtime",
                    "realworld_validated",
                    "medium",
                    "recorded",
                    "Turn committed.",
                    json.dumps({"message_text": "I moved to Dubai."}),
                ),
            )
            conn.execute(
                """
                INSERT INTO builder_events(
                    event_id, event_type, truth_kind, target_surface, component, run_id, request_id, channel_id,
                    session_id, human_id, agent_id, actor_id, evidence_lane, severity, status, summary, facts_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "evt-assistant-cli",
                    "delivery_succeeded",
                    "delivery",
                    "spark_intelligence_builder",
                    "telegram_runtime",
                    "run-cli",
                    "turn-cli",
                    "telegram",
                    "session-cli",
                    "human:test",
                    "agent:test",
                    "telegram_runtime",
                    "realworld_validated",
                    "medium",
                    "ok",
                    "Delivery succeeded.",
                    json.dumps({"delivered_text": "Noted.", "ack_ref": "telegram:cli"}),
                ),
            )
            conn.execute(
                """
                INSERT INTO builder_events(
                    event_id, event_type, truth_kind, target_surface, component, request_id, session_id, human_id,
                    actor_id, evidence_lane, severity, status, summary, facts_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "evt-mem-req-cli",
                    "memory_write_requested",
                    "fact",
                    "spark_intelligence_builder",
                    "memory_orchestrator",
                    "turn-cli",
                    "session-cli",
                    "human:test",
                    "personality_loader",
                    "realworld_validated",
                    "medium",
                    "recorded",
                    "Memory write requested.",
                    json.dumps(
                        {
                            "observations": [
                                {
                                    "subject": "human:human:test",
                                    "predicate": "user.location",
                                    "value": "Dubai",
                                    "operation": "update",
                                    "memory_role": "current_state",
                                }
                            ]
                        }
                    ),
                ),
            )
            conn.execute(
                """
                INSERT INTO builder_events(
                    event_id, event_type, truth_kind, target_surface, component, request_id, session_id, human_id,
                    actor_id, evidence_lane, severity, status, summary, facts_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "evt-mem-ok-cli",
                    "memory_write_succeeded",
                    "fact",
                    "spark_intelligence_builder",
                    "memory_orchestrator",
                    "turn-cli",
                    "session-cli",
                    "human:test",
                    "personality_loader",
                    "realworld_validated",
                    "medium",
                    "recorded",
                    "Memory write succeeded.",
                    json.dumps({"accepted_count": 1}),
                ),
            )
            conn.commit()

        output_path = self.home / "artifacts" / "shadow-replay.json"
        exit_code, stdout, stderr = self.run_cli(
            "memory",
            "export-shadow-replay",
            "--home",
            str(self.home),
            "--write",
            str(output_path),
            "--skip-validate",
            "--json",
        )

        self.assertEqual(exit_code, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["conversation_count"], 1)
        self.assertEqual(payload["turn_count"], 2)
        self.assertEqual(payload["probe_count"], 2)
        exported = json.loads(output_path.read_text(encoding="utf-8"))
        self.assertEqual(exported["writable_roles"], ["user"])
        self.assertEqual(exported["conversations"][0]["conversation_id"], "session-cli")
        self.assertEqual(exported["conversations"][0]["turns"][0]["content"], "I moved to Dubai.")

    def test_memory_export_shadow_replay_batch_writes_contract_shaped_directory(self) -> None:
        with self.state_db.connect() as conn:
            for index in range(1, 3):
                conn.execute(
                    """
                    INSERT INTO builder_events(
                        event_id, event_type, truth_kind, target_surface, component, run_id, request_id, channel_id,
                        session_id, human_id, agent_id, actor_id, evidence_lane, severity, status, summary, facts_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"evt-user-batch-{index}",
                        "intent_committed",
                        "fact",
                        "spark_intelligence_builder",
                        "telegram_runtime",
                        f"run-batch-{index}",
                        f"turn-batch-{index}",
                        "telegram",
                        f"session-batch-{index}",
                        "human:test",
                        "agent:test",
                        "telegram_runtime",
                        "realworld_validated",
                        "medium",
                        "recorded",
                        "Turn committed.",
                        json.dumps({"message_text": f"Batch turn {index}"}),
                    ),
                )
            conn.commit()

        output_dir = self.home / "artifacts" / "shadow-replay-batch"
        exit_code, stdout, stderr = self.run_cli(
            "memory",
            "export-shadow-replay-batch",
            "--home",
            str(self.home),
            "--output-dir",
            str(output_dir),
            "--conversations-per-file",
            "1",
            "--skip-validate",
            "--json",
        )

        self.assertEqual(exit_code, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["file_count"], 2)
        self.assertEqual(payload["conversation_count"], 2)
        self.assertEqual(payload["turn_count"], 2)
        files = sorted(output_dir.glob("*.json"))
        self.assertEqual(len(files), 2)
        first_export = json.loads(files[0].read_text(encoding="utf-8"))
        self.assertEqual(first_export["writable_roles"], ["user"])
        self.assertEqual(len(first_export["conversations"]), 1)

    def test_memory_export_sdk_maintenance_replay_writes_contract_shaped_json(self) -> None:
        with self.state_db.connect() as conn:
            conn.execute(
                """
                INSERT INTO builder_events(
                    event_id, event_type, truth_kind, target_surface, component, request_id, session_id, human_id,
                    actor_id, evidence_lane, severity, status, summary, created_at, facts_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "evt-user-maint-cli",
                    "intent_committed",
                    "fact",
                    "spark_intelligence_builder",
                    "telegram_runtime",
                    "turn-maint-cli",
                    "session-maint-cli",
                    "human:test",
                    "telegram_runtime",
                    "realworld_validated",
                    "medium",
                    "recorded",
                    "Turn committed.",
                    "2026-03-27T11:00:00Z",
                    json.dumps({"message_text": "I moved to Dubai."}),
                ),
            )
            conn.execute(
                """
                INSERT INTO builder_events(
                    event_id, event_type, truth_kind, target_surface, component, request_id, session_id, human_id,
                    actor_id, evidence_lane, severity, status, summary, created_at, facts_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "evt-mem-req-maint-cli",
                    "memory_write_requested",
                    "fact",
                    "spark_intelligence_builder",
                    "memory_orchestrator",
                    "turn-maint-cli",
                    "session-maint-cli",
                    "human:test",
                    "personality_loader",
                    "realworld_validated",
                    "medium",
                    "recorded",
                    "Memory write requested.",
                    "2026-03-27T11:00:01Z",
                    json.dumps(
                        {
                            "operation": "update",
                            "method": "write_observation",
                            "observations": [
                                {
                                    "subject": "human:human:test",
                                    "predicate": "profile.city",
                                    "value": "Dubai",
                                    "operation": "update",
                                    "memory_role": "current_state",
                                }
                            ],
                        }
                    ),
                ),
            )
            conn.execute(
                """
                INSERT INTO builder_events(
                    event_id, event_type, truth_kind, target_surface, component, request_id, session_id, human_id,
                    actor_id, evidence_lane, severity, status, summary, created_at, facts_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "evt-mem-ok-maint-cli",
                    "memory_write_succeeded",
                    "fact",
                    "spark_intelligence_builder",
                    "memory_orchestrator",
                    "turn-maint-cli",
                    "session-maint-cli",
                    "human:test",
                    "personality_loader",
                    "realworld_validated",
                    "medium",
                    "recorded",
                    "Memory write succeeded.",
                    "2026-03-27T11:00:02Z",
                    json.dumps({"accepted_count": 1, "rejected_count": 0, "skipped_count": 0}),
                ),
            )
            conn.commit()

        output_path = self.home / "artifacts" / "sdk-maintenance-replay.json"
        exit_code, stdout, stderr = self.run_cli(
            "memory",
            "export-sdk-maintenance-replay",
            "--home",
            str(self.home),
            "--write",
            str(output_path),
            "--json",
        )

        self.assertEqual(exit_code, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["write_count"], 1)
        exported = json.loads(output_path.read_text(encoding="utf-8"))
        self.assertEqual(exported["writes"][0]["text"], "I moved to Dubai.")
        self.assertEqual(exported["checks"]["current_state"][0]["predicate"], "profile.city")

    def test_setup_creates_bootstrap_and_doctor_and_status_report_clean_temp_home(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            home = Path(tempdir)

            setup_exit, setup_stdout, setup_stderr = self.run_cli("setup", "--home", str(home))
            self.assertEqual(setup_exit, 0, setup_stderr)
            self.assertIn("Spark Intelligence home:", setup_stdout)
            self.assertTrue((home / "config.yaml").exists())
            self.assertTrue((home / ".env").exists())
            self.assertTrue((home / "state.db").exists())

            doctor_exit, doctor_stdout, doctor_stderr = self.run_cli("doctor", "--home", str(home))
            self.assertEqual(doctor_exit, 0, doctor_stderr)
            self.assertIn("Doctor status: ok", doctor_stdout)

            status_exit, status_stdout, status_stderr = self.run_cli("status", "--home", str(home))
            self.assertEqual(status_exit, 0, status_stderr)
            self.assertIn("Spark Intelligence status", status_stdout)
            self.assertIn("- doctor: ok", status_stdout)

    def test_bootstrap_telegram_agent_configures_supported_profile(self) -> None:
        researcher_root = self.home / "spark-researcher"
        researcher_root.mkdir()
        researcher_config = researcher_root / "spark-researcher.project.json"
        researcher_config.write_text("{}", encoding="utf-8")

        exit_code, stdout, stderr = self.run_cli(
            "bootstrap",
            "telegram-agent",
            "--home",
            str(self.home),
            "--researcher-root",
            str(researcher_root),
            "--researcher-config",
            str(researcher_config),
            "--provider",
            "custom",
            "--api-key",
            "minimax-secret",
            "--model",
            "MiniMax-M2.7",
            "--base-url",
            "https://api.minimax.io/v1",
            "--bot-token",
            "telegram-test-token",
            "--allowed-user",
            "12345",
            "--skip-validate",
        )

        self.assertEqual(exit_code, 0, stderr)
        self.assertIn("Spark Intelligence bootstrap: telegram-agent", stdout)
        self.assertIn("- provider: custom", stdout)
        self.assertIn("- gateway_ready: yes", stdout)
        self.assertIn("gateway start --home", stdout)
        self.assertEqual(self.config_manager.get_path("runtime.install.profile"), "telegram-agent")
        self.assertEqual(self.config_manager.get_path("runtime.run.default_gateway_mode"), "continuous")
        self.assertEqual(self.config_manager.get_path("providers.default_provider"), "custom")
        self.assertEqual(self.config_manager.get_path("channels.records.telegram.pairing_mode"), "pairing")

    def test_bootstrap_telegram_agent_imports_process_env_secrets(self) -> None:
        researcher_root = self.home / "spark-researcher"
        researcher_root.mkdir()
        researcher_config = researcher_root / "spark-researcher.project.json"
        researcher_config.write_text("{}", encoding="utf-8")

        with patch.dict(
            "os.environ",
            {"CUSTOM_API_KEY": "minimax-secret", "BOOTSTRAP_TELEGRAM_TOKEN": "telegram-test-token"},
            clear=False,
        ):
            exit_code, stdout, stderr = self.run_cli(
                "bootstrap",
                "telegram-agent",
                "--home",
                str(self.home),
                "--researcher-root",
                str(researcher_root),
                "--researcher-config",
                str(researcher_config),
                "--provider",
                "custom",
                "--api-key-env",
                "CUSTOM_API_KEY",
                "--model",
                "MiniMax-M2.7",
                "--base-url",
                "https://api.minimax.io/v1",
                "--bot-token-env",
                "BOOTSTRAP_TELEGRAM_TOKEN",
                "--skip-validate",
            )

        self.assertEqual(exit_code, 0, stderr)
        env_map = self.config_manager.read_env_map()
        self.assertEqual(env_map["CUSTOM_API_KEY"], "minimax-secret")
        self.assertEqual(env_map["BOOTSTRAP_TELEGRAM_TOKEN"], "telegram-test-token")
        self.assertEqual(env_map["TELEGRAM_BOT_TOKEN"], "telegram-test-token")
        self.assertIn("- gateway_ready: yes", stdout)

    def test_install_autostart_registers_windows_task_scheduler_wrapper(self) -> None:
        researcher_root = self.home / "spark-researcher"
        researcher_root.mkdir()
        researcher_config = researcher_root / "spark-researcher.project.json"
        researcher_config.write_text("{}", encoding="utf-8")

        bootstrap_exit, _, bootstrap_stderr = self.run_cli(
            "bootstrap",
            "telegram-agent",
            "--home",
            str(self.home),
            "--researcher-root",
            str(researcher_root),
            "--researcher-config",
            str(researcher_config),
            "--provider",
            "custom",
            "--api-key",
            "minimax-secret",
            "--model",
            "MiniMax-M2.7",
            "--base-url",
            "https://api.minimax.io/v1",
            "--bot-token",
            "telegram-test-token",
            "--skip-validate",
        )
        self.assertEqual(bootstrap_exit, 0, bootstrap_stderr)

        with patch(
            "spark_intelligence.cli.gateway_status",
            return_value=SimpleNamespace(ready=True, repair_hints=[]),
        ), patch("spark_intelligence.cli.subprocess.run") as run_mock:
            run_mock.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="ok", stderr="")
            exit_code, stdout, stderr = self.run_cli(
                "install-autostart",
                "--home",
                str(self.home),
                "--task-name",
                "Spark Intelligence Test Task",
            )

        self.assertEqual(exit_code, 0, stderr)
        self.assertIn("Installed autostart.", stdout)
        self.assertIn("Spark Intelligence Test Task", stdout)
        self.assertTrue(self.config_manager.get_path("runtime.autostart.enabled"))
        self.assertEqual(self.config_manager.get_path("runtime.autostart.platform"), "windows_task_scheduler")
        self.assertEqual(self.config_manager.get_path("runtime.autostart.task_name"), "Spark Intelligence Test Task")
        command = self.config_manager.get_path("runtime.autostart.command")
        self.assertIn("gateway", command)
        self.assertIn("--continuous", command)
        task_args = run_mock.call_args.args[0]
        self.assertEqual(task_args[:4], ["schtasks", "/Create", "/TN", "Spark Intelligence Test Task"])
        self.assertIn("/RL", task_args)
        self.assertIn("LIMITED", task_args)
        self.assertIn("/RU", task_args)

    def test_uninstall_autostart_clears_windows_task_scheduler_wrapper(self) -> None:
        self.config_manager.set_path("runtime.autostart.enabled", True)
        self.config_manager.set_path("runtime.autostart.platform", "windows_task_scheduler")
        self.config_manager.set_path("runtime.autostart.task_name", "Spark Intelligence Test Task")
        self.config_manager.set_path("runtime.autostart.command", "python -m spark_intelligence.cli gateway start")

        with patch("spark_intelligence.cli.subprocess.run") as run_mock:
            run_mock.return_value = subprocess.CompletedProcess(args=[], returncode=0, stdout="ok", stderr="")
            exit_code, stdout, stderr = self.run_cli(
                "uninstall-autostart",
                "--home",
                str(self.home),
            )

        self.assertEqual(exit_code, 0, stderr)
        self.assertIn("Removed autostart.", stdout)
        self.assertFalse(self.config_manager.get_path("runtime.autostart.enabled"))
        self.assertIsNone(self.config_manager.get_path("runtime.autostart.platform"))
        self.assertIsNone(self.config_manager.get_path("runtime.autostart.task_name"))
        self.assertIsNone(self.config_manager.get_path("runtime.autostart.command"))
        task_args = run_mock.call_args.args[0]
        self.assertEqual(task_args[:4], ["schtasks", "/Delete", "/TN", "Spark Intelligence Test Task"])

    def test_install_autostart_falls_back_to_startup_folder_when_task_scheduler_is_denied(self) -> None:
        researcher_root = self.home / "spark-researcher"
        researcher_root.mkdir()
        researcher_config = researcher_root / "spark-researcher.project.json"
        researcher_config.write_text("{}", encoding="utf-8")

        bootstrap_exit, _, bootstrap_stderr = self.run_cli(
            "bootstrap",
            "telegram-agent",
            "--home",
            str(self.home),
            "--researcher-root",
            str(researcher_root),
            "--researcher-config",
            str(researcher_config),
            "--provider",
            "custom",
            "--api-key",
            "minimax-secret",
            "--model",
            "MiniMax-M2.7",
            "--base-url",
            "https://api.minimax.io/v1",
            "--bot-token",
            "telegram-test-token",
            "--skip-validate",
        )
        self.assertEqual(bootstrap_exit, 0, bootstrap_stderr)

        startup_root = self.home / "AppData" / "Roaming"
        with patch.dict("os.environ", {"APPDATA": str(startup_root)}, clear=False), patch(
            "spark_intelligence.config.loader.ConfigManager.harden_env_file_permissions",
            return_value=None,
        ), patch(
            "spark_intelligence.cli.gateway_status",
            return_value=SimpleNamespace(ready=True, repair_hints=[]),
        ), patch("spark_intelligence.cli.subprocess.run") as run_mock:
            run_mock.side_effect = subprocess.CalledProcessError(
                1,
                ["schtasks"],
                stderr="ERROR: Access is denied.",
            )
            exit_code, stdout, stderr = self.run_cli(
                "install-autostart",
                "--home",
                str(self.home),
                "--task-name",
                "Spark Intelligence Test Task",
            )

        self.assertEqual(exit_code, 0, stderr)
        self.assertIn("Installed autostart.", stdout)
        self.assertIn("- platform: windows_startup_folder", stdout)
        self.assertEqual(self.config_manager.get_path("runtime.autostart.platform"), "windows_startup_folder")
        wrapper_dir = startup_root / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
        wrapper_files = list(wrapper_dir.glob("*.cmd"))
        self.assertEqual(len(wrapper_files), 1)
        self.assertIn("--continuous", wrapper_files[0].read_text(encoding="utf-8"))

    def test_connect_status_surfaces_phase_plan_on_clean_home(self) -> None:
        exit_code, stdout, stderr = self.run_cli("connect", "status", "--home", str(self.home))

        self.assertEqual(exit_code, 0, stderr)
        self.assertIn("Spark Intelligence connection plan", stdout)
        self.assertIn("- current phase: phase-a-telegram-core Lock Telegram core", stdout)
        self.assertIn("- phase-a-telegram-core: blocked Lock Telegram core", stdout)
        self.assertIn("next: spark-intelligence channel telegram-onboard", stdout)

    def test_connect_status_keeps_phase_c_current_when_swarm_auth_is_rejected(self) -> None:
        self.config_manager.set_path("spark.chips.active_keys", ["startup-yc"])
        self.config_manager.set_path("spark.specialization_paths.active_path_key", "startup-operator")
        with patch(
            "spark_intelligence.cli.gateway_status",
            return_value=SimpleNamespace(
                configured_channels=["telegram"],
                configured_providers=["custom"],
                ready=True,
                provider_runtime_ok=True,
                provider_execution_ok=True,
                repair_hints=[],
            ),
        ), patch(
            "spark_intelligence.cli.researcher_bridge_status",
            return_value=SimpleNamespace(
                available=True,
                last_provider_execution_transport="direct_http",
                last_routing_decision="provider_execution",
            ),
        ), patch(
            "spark_intelligence.cli.attachment_status",
            return_value=SimpleNamespace(
                records=[
                    SimpleNamespace(kind="chip"),
                    SimpleNamespace(kind="path"),
                ]
            ),
        ), patch(
            "spark_intelligence.cli.swarm_status",
            return_value=SimpleNamespace(
                payload_ready=True,
                api_ready=True,
                api_url="https://sparkswarm.ai",
                workspace_id="ws_123",
                access_token_env="SPARK_SWARM_ACCESS_TOKEN",
                runtime_root="C:\\spark-swarm",
                last_failure={
                    "mode": "http_error",
                    "response_body": {"error": "authentication_required"},
                },
            ),
        ):
            exit_code, stdout, stderr = self.run_cli("connect", "status", "--home", str(self.home))

        self.assertEqual(exit_code, 0, stderr)
        self.assertIn("- current phase: phase-c-swarm-api Connect Spark Swarm", stdout)
        self.assertIn("check: api_auth=auth_rejected", stdout)
        self.assertIn("hosted API rejected the current access token or session", stdout)
        self.assertIn("next: spark-intelligence swarm configure --access-token <fresh-token>", stdout)

    def test_connect_route_policy_surfaces_bridge_and_swarm_contract(self) -> None:
        self.config_manager.set_path("spark.researcher.routing.conversational_fallback_max_chars", 123)
        self.config_manager.set_path("spark.swarm.routing.long_task_word_count", 55)
        with self.state_db.connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO runtime_state(state_key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
                ("researcher:last_routing_decision", "provider_fallback_chat"),
            )
            conn.execute(
                "INSERT OR REPLACE INTO runtime_state(state_key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
                ("researcher:last_active_chip_key", "startup-yc"),
            )
            conn.execute(
                "INSERT OR REPLACE INTO runtime_state(state_key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
                ("researcher:last_active_chip_task_type", "diagnostic_questioning"),
            )
            conn.execute(
                "INSERT OR REPLACE INTO runtime_state(state_key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
                ("researcher:last_active_chip_evaluate_used", "1"),
            )
            conn.execute(
                "INSERT OR REPLACE INTO runtime_state(state_key, value, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)",
                ("swarm:last_decision", json.dumps({"mode": "manual_recommended"})),
            )
            conn.commit()

        exit_code, stdout, stderr = self.run_cli("connect", "route-policy", "--home", str(self.home))

        self.assertEqual(exit_code, 0, stderr)
        self.assertIn("Spark Intelligence routing contract", stdout)
        self.assertIn("- conversational fallback policy: enabled (max_chars=123)", stdout)
        self.assertIn("- swarm recommendation policy: enabled (long_task_word_count=55)", stdout)
        self.assertIn("- last bridge route: provider_fallback_chat", stdout)
        self.assertIn("- last active chip route: startup-yc:diagnostic_questioning used=yes", stdout)
        self.assertIn("- last swarm decision: manual_recommended", stdout)
        self.assertIn("- provider_fallback_chat:", stdout)
        self.assertIn("- manual_recommended:", stdout)

    def test_connect_route_policy_surfaces_swarm_auth_rejection(self) -> None:
        with patch(
            "spark_intelligence.cli.gateway_status",
            return_value=SimpleNamespace(provider_runtime_ok=True, provider_execution_ok=True),
        ), patch(
            "spark_intelligence.cli.researcher_bridge_status",
            return_value=SimpleNamespace(
                enabled=True,
                available=True,
                last_routing_decision="provider_execution",
                last_active_chip_key=None,
                last_active_chip_task_type=None,
                last_active_chip_evaluate_used=False,
                last_provider_execution_transport="direct_http",
            ),
        ), patch(
            "spark_intelligence.cli.swarm_status",
            return_value=SimpleNamespace(
                enabled=True,
                payload_ready=True,
                api_ready=True,
                last_decision={"mode": "hold_local"},
                last_failure={
                    "mode": "http_error",
                    "response_body": {"error": "authentication_required"},
                },
            ),
        ):
            exit_code, stdout, stderr = self.run_cli("connect", "route-policy", "--home", str(self.home))

        self.assertEqual(exit_code, 0, stderr)
        self.assertIn("- swarm api auth: auth_rejected", stdout)
        self.assertIn("- last swarm failure: http_error", stdout)
        self.assertIn("- last swarm failure error: authentication_required", stdout)

    def test_connect_status_surfaces_refreshable_swarm_session(self) -> None:
        self.config_manager.set_path("spark.chips.active_keys", ["startup-yc"])
        self.config_manager.set_path("spark.specialization_paths.active_path_key", "startup-operator")
        with patch(
            "spark_intelligence.cli.gateway_status",
            return_value=SimpleNamespace(
                configured_channels=["telegram"],
                configured_providers=["custom"],
                ready=True,
                provider_runtime_ok=True,
                provider_execution_ok=True,
                repair_hints=[],
            ),
        ), patch(
            "spark_intelligence.cli.researcher_bridge_status",
            return_value=SimpleNamespace(
                available=True,
                last_provider_execution_transport="direct_http",
                last_routing_decision="provider_execution",
            ),
        ), patch(
            "spark_intelligence.cli.attachment_status",
            return_value=SimpleNamespace(
                records=[SimpleNamespace(kind="chip"), SimpleNamespace(kind="path")]
            ),
        ), patch(
            "spark_intelligence.cli.swarm_status",
            return_value=SimpleNamespace(
                payload_ready=True,
                api_ready=True,
                auth_state="refreshable",
                api_url="https://sparkswarm.ai",
                workspace_id="ws_123",
                access_token_env="SPARK_SWARM_ACCESS_TOKEN",
                refresh_token_env="SPARK_SWARM_REFRESH_TOKEN",
                runtime_root="C:\\spark-swarm",
                last_failure={},
            ),
        ):
            exit_code, stdout, stderr = self.run_cli("connect", "status", "--home", str(self.home))

        self.assertEqual(exit_code, 0, stderr)
        self.assertIn("check: api_auth=refreshable", stdout)
        self.assertIn("local session is refreshable", stdout)
        self.assertIn("next: spark-intelligence swarm sync", stdout)

    def test_connect_set_route_policy_updates_operator_knobs(self) -> None:
        exit_code, stdout, stderr = self.run_cli(
            "connect",
            "set-route-policy",
            "--home",
            str(self.home),
            "--conversational-fallback",
            "off",
            "--conversational-max-chars",
            "111",
            "--swarm-auto-recommend",
            "off",
            "--swarm-long-task-word-count",
            "77",
        )

        self.assertEqual(exit_code, 0, stderr)
        self.assertIn("spark.researcher.routing.conversational_fallback_enabled=false", stdout)
        self.assertIn("spark.researcher.routing.conversational_fallback_max_chars=111", stdout)
        self.assertIn("spark.swarm.routing.auto_recommend_enabled=false", stdout)
        self.assertIn("spark.swarm.routing.long_task_word_count=77", stdout)
        self.assertFalse(
            self.config_manager.get_path("spark.researcher.routing.conversational_fallback_enabled", default=True)
        )
        self.assertEqual(
            self.config_manager.get_path("spark.researcher.routing.conversational_fallback_max_chars"),
            111,
        )
        self.assertFalse(
            self.config_manager.get_path("spark.swarm.routing.auto_recommend_enabled", default=True)
        )
        self.assertEqual(self.config_manager.get_path("spark.swarm.routing.long_task_word_count"), 77)

    def test_connect_status_marks_telegram_core_ready_when_live_prereqs_exist(self) -> None:
        researcher_root = self.home / "spark-researcher"
        researcher_root.mkdir()
        researcher_config = researcher_root / "spark-researcher.project.json"
        researcher_config.write_text("{}", encoding="utf-8")
        self.config_manager.set_path("spark.researcher.enabled", True)
        self.config_manager.set_path("spark.researcher.runtime_root", str(researcher_root))
        self.config_manager.set_path("spark.researcher.config_path", str(researcher_config))
        self.add_telegram_channel(bot_token="good-token")
        self.config_manager.upsert_env_secret("CUSTOM_API_KEY", "minimax-test-key")

        connect_exit, _, connect_stderr = self.run_cli(
            "auth",
            "connect",
            "custom",
            "--home",
            str(self.home),
            "--api-key-env",
            "CUSTOM_API_KEY",
            "--base-url",
            "https://api.minimax.io/v1",
            "--model",
            "MiniMax-M2.7",
        )
        self.assertEqual(connect_exit, 0, connect_stderr)

        exit_code, stdout, stderr = self.run_cli("connect", "status", "--home", str(self.home), "--json")

        self.assertEqual(exit_code, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["overall_status"], "in_progress")
        self.assertEqual(payload["current_phase"], "phase-b-specialization")
        self.assertEqual(payload["phases"][0]["phase_id"], "phase-a-telegram-core")
        self.assertEqual(payload["phases"][0]["status"], "ready")
        self.assertEqual(payload["phases"][1]["phase_id"], "phase-b-specialization")
        self.assertIn(payload["phases"][1]["status"], {"blocked", "in_progress"})

    def test_operator_security_surfaces_telegram_poll_failure_in_json(self) -> None:
        self.add_telegram_channel(bot_token="good-token")

        class PollFailingClient:
            def __init__(self, token: str):
                self.token = token

            def get_me(self) -> dict[str, object]:
                return {"result": {"username": "sparkbot"}}

            def get_updates(self, *, offset: int | None = None, timeout_seconds: int = 5) -> list[dict[str, object]]:
                raise URLError("offline")

            def send_message(self, *, chat_id: str, text: str) -> dict[str, object]:
                return {"ok": True}

        with patch("spark_intelligence.gateway.runtime.TelegramBotApiClient", PollFailingClient):
            gateway_exit, gateway_stdout, gateway_stderr = self.run_cli(
                "gateway",
                "start",
                "--home",
                str(self.home),
                "--once",
            )

        self.assertEqual(gateway_exit, 1, gateway_stderr)
        self.assertIn("Telegram polling failure", gateway_stdout)

        security_exit, security_stdout, security_stderr = self.run_cli(
            "operator",
            "security",
            "--home",
            str(self.home),
            "--json",
        )

        self.assertEqual(security_exit, 0, security_stderr)
        payload = json.loads(security_stdout)
        self.assertEqual(payload["counts"]["channel_alerts"], 1)
        self.assertEqual(payload["counts"]["bridge_alerts"], 1)
        self.assertEqual(payload["channel_alerts"][0]["status"], "poll_failure")
        self.assertEqual(payload["recent"]["duplicates"], [])
        self.assertEqual(payload["recent"]["rate_limited"], [])

    def test_operator_security_surfaces_discord_webhook_auth_rejections(self) -> None:
        setup_exit, _, setup_stderr = self.run_cli(
            "channel",
            "add",
            "discord",
            "--home",
            str(self.home),
            "--allow-legacy-message-webhook",
            "--webhook-secret",
            "discord-webhook-secret",
        )
        self.assertEqual(setup_exit, 0, setup_stderr)

        response = handle_discord_webhook(
            config_manager=self.config_manager,
            state_db=self.state_db,
            path=DISCORD_WEBHOOK_PATH,
            method="POST",
            content_type="application/json",
            headers={},
            body=b"{}",
        )
        self.assertEqual(response.status_code, 401)

        security_exit, security_stdout, security_stderr = self.run_cli(
            "operator",
            "security",
            "--home",
            str(self.home),
            "--json",
        )

        self.assertEqual(security_exit, 0, security_stderr)
        payload = json.loads(security_stdout)
        self.assertEqual(payload["counts"]["webhook_alerts"], 1)
        self.assertEqual(payload["webhook_alerts"][0]["event"], "discord_webhook_auth_failed")
        self.assertIn("Discord webhook auth rejected 1 time(s)", payload["webhook_alerts"][0]["summary"])
        self.assertEqual(
            payload["webhook_alerts"][0]["recommended_command"],
            "spark-intelligence gateway traces --event discord_webhook_auth_failed --limit 20",
        )

    def test_operator_can_list_and_export_observer_packets(self) -> None:
        record_event(
            self.state_db,
            event_type="plugin_or_chip_influence_recorded",
            component="researcher_bridge",
            summary="unlabeled provenance mutation",
            request_id="req-cli-observer-packets",
            actor_id="researcher_bridge",
            facts={"keepability": "ephemeral_context"},
            provenance={},
        )

        list_exit, list_stdout, list_stderr = self.run_cli(
            "operator",
            "observer-packets",
            "--home",
            str(self.home),
            "--json",
        )

        self.assertEqual(list_exit, 0, list_stderr)
        list_payload = json.loads(list_stdout)
        self.assertTrue(list_payload["active_only"])
        self.assertIsNone(list_payload["packet_kind"])
        self.assertGreaterEqual(len(list_payload["rows"]), 1)
        self.assertTrue(all(row["active"] for row in list_payload["rows"]))
        self.assertIn("self_observation", {row["packet_kind"] for row in list_payload["rows"]})

        export_path = self.home / "artifacts" / "observer-packets-export.json"
        export_exit, export_stdout, export_stderr = self.run_cli(
            "operator",
            "export-observer-packets",
            "--home",
            str(self.home),
            "--write",
            str(export_path),
            "--reason",
            "external observer handoff",
            "--json",
        )

        self.assertEqual(export_exit, 0, export_stderr)
        export_payload = json.loads(export_stdout)
        self.assertEqual(export_payload["write_path"], str(export_path))
        self.assertGreaterEqual(export_payload["packet_count"], 1)
        self.assertTrue(export_path.exists())
        written_payload = json.loads(export_path.read_text(encoding="utf-8"))
        self.assertEqual(written_payload["packet_count"], export_payload["packet_count"])
        self.assertEqual(written_payload["counts_by_kind"], export_payload["counts_by_kind"])
        self.assertEqual(written_payload["packets"], export_payload["packets"])

        history_exit, history_stdout, history_stderr = self.run_cli(
            "operator",
            "history",
            "--home",
            str(self.home),
            "--action",
            "export_observer_packets",
            "--json",
        )

        self.assertEqual(history_exit, 0, history_stderr)
        history_payload = json.loads(history_stdout)
        self.assertEqual(history_payload["rows"][0]["target_ref"], str(export_path))
        self.assertEqual(history_payload["rows"][0]["reason"], "external observer handoff")

    def test_operator_personality_reports_overview_and_human_state(self) -> None:
        deltas = detect_and_persist_nl_preferences(
            human_id="human:test",
            user_message="be more direct and stop hedging",
            state_db=self.state_db,
        )
        self.assertIsNotNone(deltas)
        record_observation(
            human_id="human:test",
            user_message="this is broken and confusing",
            traits_active={
                "warmth": 0.5,
                "directness": 0.8,
                "playfulness": 0.5,
                "pacing": 0.5,
                "assertiveness": 0.7,
            },
            state_db=self.state_db,
        )

        overview_exit, overview_stdout, overview_stderr = self.run_cli(
            "operator",
            "personality",
            "--home",
            str(self.home),
            "--json",
        )

        self.assertEqual(overview_exit, 0, overview_stderr)
        overview_payload = json.loads(overview_stdout)
        self.assertEqual(overview_payload["overview"]["counts"]["trait_profiles"], 1)
        self.assertEqual(overview_payload["overview"]["counts"]["active_profiles"], 1)
        self.assertEqual(overview_payload["overview"]["counts"]["mirror_drift"], 0)

        detail_exit, detail_stdout, detail_stderr = self.run_cli(
            "operator",
            "personality",
            "--home",
            str(self.home),
            "--human-id",
            "human:test",
            "--json",
        )

        self.assertEqual(detail_exit, 0, detail_stderr)
        detail_payload = json.loads(detail_stdout)
        self.assertEqual(detail_payload["human_id"], "human:test")
        self.assertTrue(detail_payload["enabled"])
        self.assertEqual(detail_payload["agent_identity"]["agent_id"], "agent:human:test")
        self.assertEqual(detail_payload["agent_identity"]["preferred_source"], "builder_local")
        self.assertIn("directness", detail_payload["user_deltas"])
        self.assertEqual(detail_payload["observations"][0]["user_state"], "frustrated")
        self.assertEqual(detail_payload["observation_states"]["frustrated"], 1)

    def test_operator_security_surfaces_discord_ingress_missing(self) -> None:
        setup_exit, _, setup_stderr = self.run_cli(
            "channel",
            "add",
            "discord",
            "--home",
            str(self.home),
        )
        self.assertEqual(setup_exit, 0, setup_stderr)

        security_exit, security_stdout, security_stderr = self.run_cli(
            "operator",
            "security",
            "--home",
            str(self.home),
            "--json",
        )

        self.assertEqual(security_exit, 0, security_stderr)
        payload = json.loads(security_stdout)
        self.assertEqual(payload["counts"]["channel_alerts"], 1)
        self.assertEqual(payload["channel_alerts"][0]["channel_id"], "discord")
        self.assertEqual(payload["channel_alerts"][0]["status"], "ingress_missing")
        self.assertIn("Discord ingress is not ready", payload["channel_alerts"][0]["summary"])
        self.assertEqual(
            payload["channel_alerts"][0]["recommended_command"],
            "spark-intelligence channel add discord --interaction-public-key <public-key>",
        )

    def test_agent_inspect_human_surfaces_canonical_identity_detail(self) -> None:
        approve_pairing(
            state_db=self.state_db,
            channel_id="telegram",
            external_user_id="111",
            display_name="Alice",
        )

        exit_code, stdout, stderr = self.run_cli(
            "agent",
            "inspect",
            "--home",
            str(self.home),
            "--human-id",
            "human:telegram:111",
            "--json",
        )

        self.assertEqual(exit_code, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["identity"]["agent_id"], "agent:human:telegram:111")
        self.assertEqual(payload["identity"]["agent_name"], "Alice")
        self.assertEqual(payload["identity"]["preferred_source"], "builder_local")
        self.assertEqual(payload["sessions"][0]["session_id"], "session:telegram:dm:111")

    def test_agent_rename_and_link_swarm_commands_update_canonical_identity(self) -> None:
        approve_pairing(
            state_db=self.state_db,
            channel_id="telegram",
            external_user_id="111",
            display_name="Alice",
        )

        rename_exit, rename_stdout, rename_stderr = self.run_cli(
            "agent",
            "rename",
            "--home",
            str(self.home),
            "--human-id",
            "human:telegram:111",
            "--name",
            "Atlas",
            "--json",
        )
        self.assertEqual(rename_exit, 0, rename_stderr)
        rename_payload = json.loads(rename_stdout)
        self.assertEqual(rename_payload["agent_id"], "agent:human:telegram:111")
        self.assertEqual(rename_payload["agent_name"], "Atlas")

        link_exit, link_stdout, link_stderr = self.run_cli(
            "agent",
            "link-swarm",
            "--home",
            str(self.home),
            "--human-id",
            "human:telegram:111",
            "--swarm-agent-id",
            "swarm-agent:atlas",
            "--agent-name",
            "Atlas",
            "--json",
        )
        self.assertEqual(link_exit, 0, link_stderr)
        link_payload = json.loads(link_stdout)
        self.assertEqual(link_payload["agent_id"], "swarm-agent:atlas")
        self.assertEqual(link_payload["preferred_source"], "spark_swarm")
        self.assertIn("agent:human:telegram:111", link_payload["alias_agent_ids"])

    def test_agent_migrate_legacy_personality_command_moves_overlay_into_agent_base(self) -> None:
        approve_pairing(
            state_db=self.state_db,
            channel_id="telegram",
            external_user_id="111",
            display_name="Alice",
        )
        detect_and_persist_nl_preferences(
            human_id="human:telegram:111",
            user_message="be more direct and stop hedging",
            state_db=self.state_db,
        )

        exit_code, stdout, stderr = self.run_cli(
            "agent",
            "migrate-legacy-personality",
            "--home",
            str(self.home),
            "--human-id",
            "human:telegram:111",
            "--json",
        )

        self.assertEqual(exit_code, 0, stderr)
        payload = json.loads(stdout)
        self.assertEqual(payload["status"], "migrated")
        self.assertTrue(payload["cleared_overlay"])
        self.assertGreater(payload["migrated_traits"]["directness"], 0.5)
        self.assertGreater(payload["migrated_traits"]["assertiveness"], 0.5)

    def test_operator_security_escalates_sustained_discord_webhook_auth_rejections(self) -> None:
        setup_exit, _, setup_stderr = self.run_cli(
            "channel",
            "add",
            "discord",
            "--home",
            str(self.home),
            "--allow-legacy-message-webhook",
            "--webhook-secret",
            "discord-webhook-secret",
        )
        self.assertEqual(setup_exit, 0, setup_stderr)

        for _ in range(3):
            response = handle_discord_webhook(
                config_manager=self.config_manager,
                state_db=self.state_db,
                path=DISCORD_WEBHOOK_PATH,
                method="POST",
                content_type="application/json",
                headers={},
                body=b"{}",
            )
            self.assertEqual(response.status_code, 401)

        security_exit, security_stdout, security_stderr = self.run_cli(
            "operator",
            "security",
            "--home",
            str(self.home),
            "--json",
        )

        self.assertEqual(security_exit, 0, security_stderr)
        payload = json.loads(security_stdout)
        self.assertEqual(payload["counts"]["webhook_alerts"], 1)
        alert = payload["webhook_alerts"][0]
        self.assertEqual(alert["event"], "discord_webhook_auth_failed")
        self.assertEqual(alert["status"], "sustained_rejections")
        self.assertEqual(alert["severity"], "critical")
        self.assertEqual(alert["count"], 3)
        self.assertIn("sustained discord webhook auth rejected detected", alert["summary"].lower())
        webhook_items = [
            item
            for item in payload["items"]
            if item["recommended_command"] == "spark-intelligence gateway traces --event discord_webhook_auth_failed --limit 20"
        ]
        self.assertEqual(len(webhook_items), 1)
        self.assertEqual(webhook_items[0]["priority"], "high")

    def test_operator_security_cools_down_old_discord_webhook_auth_rejections(self) -> None:
        setup_exit, _, setup_stderr = self.run_cli(
            "channel",
            "add",
            "discord",
            "--home",
            str(self.home),
            "--allow-legacy-message-webhook",
            "--webhook-secret",
            "discord-webhook-secret",
        )
        self.assertEqual(setup_exit, 0, setup_stderr)

        for _ in range(3):
            response = handle_discord_webhook(
                config_manager=self.config_manager,
                state_db=self.state_db,
                path=DISCORD_WEBHOOK_PATH,
                method="POST",
                content_type="application/json",
                headers={},
                body=b"{}",
            )
            self.assertEqual(response.status_code, 401)

        with patch(
            "spark_intelligence.ops.service._utc_now",
            return_value=datetime.now(timezone.utc) + timedelta(minutes=16),
        ):
            security_exit, security_stdout, security_stderr = self.run_cli(
                "operator",
                "security",
                "--home",
                str(self.home),
                "--json",
            )

        self.assertEqual(security_exit, 0, security_stderr)
        payload = json.loads(security_stdout)
        self.assertEqual(payload["counts"]["webhook_alerts"], 0)
        self.assertEqual(payload["webhook_alerts"], [])

    def test_operator_can_snooze_discord_webhook_alert(self) -> None:
        setup_exit, _, setup_stderr = self.run_cli(
            "channel",
            "add",
            "discord",
            "--home",
            str(self.home),
            "--allow-legacy-message-webhook",
            "--webhook-secret",
            "discord-webhook-secret",
        )
        self.assertEqual(setup_exit, 0, setup_stderr)

        response = handle_discord_webhook(
            config_manager=self.config_manager,
            state_db=self.state_db,
            path=DISCORD_WEBHOOK_PATH,
            method="POST",
            content_type="application/json",
            headers={},
            body=b"{}",
        )
        self.assertEqual(response.status_code, 401)

        snooze_exit, snooze_stdout, snooze_stderr = self.run_cli(
            "operator",
            "snooze-webhook-alert",
            "discord_webhook_auth_failed",
            "--minutes",
            "30",
            "--home",
            str(self.home),
            "--reason",
            "known noisy source",
        )
        self.assertEqual(snooze_exit, 0, snooze_stderr)
        self.assertIn("Snoozed webhook alert 'discord_webhook_auth_failed'", snooze_stdout)

        security_exit, security_stdout, security_stderr = self.run_cli(
            "operator",
            "security",
            "--home",
            str(self.home),
            "--json",
        )
        self.assertEqual(security_exit, 0, security_stderr)
        payload = json.loads(security_stdout)
        self.assertEqual(payload["counts"]["webhook_alerts"], 0)
        self.assertEqual(payload["counts"]["webhook_snoozes"], 1)
        self.assertEqual(payload["counts"]["active_suppressed_webhook_snoozes"], 1)
        self.assertEqual(payload["webhook_alerts"], [])
        self.assertEqual(len(payload["webhook_snoozes"]), 1)
        self.assertEqual(payload["webhook_snoozes"][0]["event"], "discord_webhook_auth_failed")
        self.assertIsInstance(payload["webhook_snoozes"][0]["snoozed_at"], str)
        self.assertEqual(payload["webhook_snoozes"][0]["reason"], "known noisy source")
        self.assertEqual(payload["webhook_snoozes"][0]["suppressed_recent_count"], 1)
        self.assertEqual(payload["webhook_snoozes"][0]["latest_suppressed_reason"], "Discord webhook secret header is missing.")
        self.assertIsInstance(payload["webhook_snoozes"][0]["latest_suppressed_at"], str)
        snooze_items = [
            item
            for item in payload["items"]
            if item["recommended_command"] == "spark-intelligence operator clear-webhook-alert-snooze discord_webhook_auth_failed"
        ]
        self.assertEqual(len(snooze_items), 1)
        self.assertEqual(snooze_items[0]["priority"], "info")
        self.assertEqual(
            snooze_items[0]["clear_command"],
            "spark-intelligence operator clear-webhook-alert-snooze discord_webhook_auth_failed",
        )
        self.assertIn("was snoozed at", snooze_items[0]["summary"])
        self.assertIn("Reason: known noisy source.", snooze_items[0]["summary"])
        self.assertIn(
            "Suppressed 1 recent rejection(s); latest reason: Discord webhook secret header is missing.",
            snooze_items[0]["summary"],
        )
        self.assertIn("Last suppressed at:", snooze_items[0]["summary"])

        history_exit, history_stdout, history_stderr = self.run_cli(
            "operator",
            "history",
            "--home",
            str(self.home),
            "--action",
            "snooze_webhook_alert",
            "--json",
        )
        self.assertEqual(history_exit, 0, history_stderr)
        history_payload = json.loads(history_stdout)
        self.assertEqual(len(history_payload["rows"]), 1)
        self.assertEqual(history_payload["rows"][0]["target_ref"], "discord_webhook_auth_failed")
        self.assertEqual(history_payload["rows"][0]["reason"], "known noisy source")

    def test_operator_security_escalates_sustained_suppressed_discord_webhook_traffic(self) -> None:
        setup_exit, _, setup_stderr = self.run_cli(
            "channel",
            "add",
            "discord",
            "--home",
            str(self.home),
            "--allow-legacy-message-webhook",
            "--webhook-secret",
            "discord-webhook-secret",
        )
        self.assertEqual(setup_exit, 0, setup_stderr)

        for _ in range(3):
            response = handle_discord_webhook(
                config_manager=self.config_manager,
                state_db=self.state_db,
                path=DISCORD_WEBHOOK_PATH,
                method="POST",
                content_type="application/json",
                headers={},
                body=b"{}",
            )
            self.assertEqual(response.status_code, 401)

        snooze_exit, _, snooze_stderr = self.run_cli(
            "operator",
            "snooze-webhook-alert",
            "discord_webhook_auth_failed",
            "--minutes",
            "30",
            "--home",
            str(self.home),
        )
        self.assertEqual(snooze_exit, 0, snooze_stderr)

        security_exit, security_stdout, security_stderr = self.run_cli(
            "operator",
            "security",
            "--home",
            str(self.home),
            "--json",
        )
        self.assertEqual(security_exit, 0, security_stderr)
        payload = json.loads(security_stdout)
        self.assertEqual(payload["counts"]["webhook_alerts"], 0)
        self.assertEqual(payload["counts"]["webhook_snoozes"], 1)
        self.assertEqual(payload["counts"]["active_suppressed_webhook_snoozes"], 1)
        self.assertEqual(payload["webhook_snoozes"][0]["status"], "sustained_rejections_suppressed")
        self.assertEqual(payload["webhook_snoozes"][0]["severity"], "warning")
        self.assertEqual(payload["webhook_snoozes"][0]["suppressed_recent_count"], 3)
        self.assertIsInstance(payload["webhook_snoozes"][0]["latest_suppressed_at"], str)
        snooze_items = [
            item
            for item in payload["items"]
            if item.get("clear_command") == "spark-intelligence operator clear-webhook-alert-snooze discord_webhook_auth_failed"
        ]
        self.assertEqual(len(snooze_items), 1)
        self.assertEqual(snooze_items[0]["priority"], "medium")
        self.assertEqual(
            snooze_items[0]["recommended_command"],
            "spark-intelligence gateway traces --event discord_webhook_auth_failed --limit 20",
        )
        self.assertIn("Snooze is still masking sustained ingress traffic.", snooze_items[0]["summary"])

        list_exit, list_stdout, list_stderr = self.run_cli(
            "operator",
            "webhook-alert-snoozes",
            "--home",
            str(self.home),
            "--json",
        )
        self.assertEqual(list_exit, 0, list_stderr)
        list_payload = json.loads(list_stdout)
        self.assertEqual(len(list_payload["rows"]), 1)
        self.assertEqual(list_payload["rows"][0]["status"], "sustained_rejections_suppressed")
        self.assertEqual(list_payload["rows"][0]["severity"], "warning")
        self.assertIsInstance(list_payload["rows"][0]["latest_suppressed_at"], str)
        self.assertEqual(
            list_payload["rows"][0]["recommended_command"],
            "spark-intelligence gateway traces --event discord_webhook_auth_failed --limit 20",
        )
        self.assertEqual(
            list_payload["rows"][0]["clear_command"],
            "spark-intelligence operator clear-webhook-alert-snooze discord_webhook_auth_failed",
        )

    def test_operator_can_list_and_clear_discord_webhook_alert_snooze(self) -> None:
        setup_exit, _, setup_stderr = self.run_cli(
            "channel",
            "add",
            "discord",
            "--home",
            str(self.home),
            "--allow-legacy-message-webhook",
            "--webhook-secret",
            "discord-webhook-secret",
        )
        self.assertEqual(setup_exit, 0, setup_stderr)

        response = handle_discord_webhook(
            config_manager=self.config_manager,
            state_db=self.state_db,
            path=DISCORD_WEBHOOK_PATH,
            method="POST",
            content_type="application/json",
            headers={},
            body=b"{}",
        )
        self.assertEqual(response.status_code, 401)

        snooze_exit, _, snooze_stderr = self.run_cli(
            "operator",
            "snooze-webhook-alert",
            "discord_webhook_auth_failed",
            "--minutes",
            "30",
            "--home",
            str(self.home),
        )
        self.assertEqual(snooze_exit, 0, snooze_stderr)

        list_exit, list_stdout, list_stderr = self.run_cli(
            "operator",
            "webhook-alert-snoozes",
            "--home",
            str(self.home),
            "--json",
        )
        self.assertEqual(list_exit, 0, list_stderr)
        list_payload = json.loads(list_stdout)
        self.assertEqual(len(list_payload["rows"]), 1)
        self.assertEqual(list_payload["rows"][0]["event"], "discord_webhook_auth_failed")
        self.assertIsInstance(list_payload["rows"][0]["snoozed_at"], str)
        self.assertGreaterEqual(list_payload["rows"][0]["remaining_minutes"], 1)
        self.assertIsNone(list_payload["rows"][0]["reason"])
        self.assertEqual(list_payload["rows"][0]["suppressed_recent_count"], 1)
        self.assertEqual(list_payload["rows"][0]["latest_suppressed_reason"], "Discord webhook secret header is missing.")
        self.assertIsInstance(list_payload["rows"][0]["latest_suppressed_at"], str)
        self.assertEqual(list_payload["rows"][0]["status"], "snoozed")
        self.assertEqual(list_payload["rows"][0]["severity"], "info")
        self.assertEqual(
            list_payload["rows"][0]["recommended_command"],
            "spark-intelligence operator clear-webhook-alert-snooze discord_webhook_auth_failed",
        )
        self.assertEqual(
            list_payload["rows"][0]["clear_command"],
            "spark-intelligence operator clear-webhook-alert-snooze discord_webhook_auth_failed",
        )

        clear_exit, clear_stdout, clear_stderr = self.run_cli(
            "operator",
            "clear-webhook-alert-snooze",
            "discord_webhook_auth_failed",
            "--home",
            str(self.home),
            "--reason",
            "noise resolved",
        )
        self.assertEqual(clear_exit, 0, clear_stderr)
        self.assertIn("Cleared webhook alert snooze for 'discord_webhook_auth_failed'", clear_stdout)
        self.assertIn("set at", clear_stdout)
        self.assertIn("until", clear_stdout)

        security_exit, security_stdout, security_stderr = self.run_cli(
            "operator",
            "security",
            "--home",
            str(self.home),
            "--json",
        )
        self.assertEqual(security_exit, 0, security_stderr)
        payload = json.loads(security_stdout)
        self.assertEqual(payload["counts"]["webhook_alerts"], 1)
        self.assertEqual(payload["webhook_alerts"][0]["event"], "discord_webhook_auth_failed")

        history_exit, history_stdout, history_stderr = self.run_cli(
            "operator",
            "history",
            "--home",
            str(self.home),
            "--action",
            "clear_webhook_alert_snooze",
            "--json",
        )
        self.assertEqual(history_exit, 0, history_stderr)
        history_payload = json.loads(history_stdout)
        self.assertEqual(len(history_payload["rows"]), 1)
        self.assertEqual(history_payload["rows"][0]["target_ref"], "discord_webhook_auth_failed")
        self.assertEqual(history_payload["rows"][0]["reason"], "noise resolved")
        self.assertEqual(history_payload["rows"][0]["details"]["removed"], True)
        self.assertEqual(history_payload["rows"][0]["details"]["cleared_snooze"]["event"], "discord_webhook_auth_failed")
        self.assertIsInstance(history_payload["rows"][0]["details"]["cleared_snooze"]["snoozed_at"], str)
        self.assertIsInstance(history_payload["rows"][0]["details"]["cleared_snooze"]["snooze_until"], str)
        self.assertIsNone(history_payload["rows"][0]["details"]["cleared_snooze"]["reason"])

    def test_operator_webhook_alert_snoozes_prioritize_sustained_masked_traffic(self) -> None:
        discord_exit, _, discord_stderr = self.run_cli(
            "channel",
            "add",
            "discord",
            "--home",
            str(self.home),
            "--allow-legacy-message-webhook",
            "--webhook-secret",
            "discord-webhook-secret",
        )
        self.assertEqual(discord_exit, 0, discord_stderr)

        whatsapp_exit, _, whatsapp_stderr = self.run_cli(
            "channel",
            "add",
            "whatsapp",
            "--home",
            str(self.home),
            "--bot-token",
            "whatsapp-token",
            "--webhook-secret",
            "whatsapp-webhook-secret",
            "--webhook-verify-token",
            "whatsapp-verify-token",
        )
        self.assertEqual(whatsapp_exit, 0, whatsapp_stderr)

        discord_response = handle_discord_webhook(
            config_manager=self.config_manager,
            state_db=self.state_db,
            path=DISCORD_WEBHOOK_PATH,
            method="POST",
            content_type="application/json",
            headers={},
            body=b"{}",
        )
        self.assertEqual(discord_response.status_code, 401)

        for _ in range(3):
            whatsapp_response = handle_whatsapp_webhook(
                config_manager=self.config_manager,
                state_db=self.state_db,
                path=WHATSAPP_WEBHOOK_PATH,
                method="GET",
                content_type=None,
                headers={},
                body=b"",
                query_params={
                    "hub.mode": "subscribe",
                    "hub.verify_token": "wrong-token",
                    "hub.challenge": "challenge-code",
                },
            )
            self.assertEqual(whatsapp_response.status_code, 401)

        discord_snooze_exit, _, discord_snooze_stderr = self.run_cli(
            "operator",
            "snooze-webhook-alert",
            "discord_webhook_auth_failed",
            "--minutes",
            "5",
            "--home",
            str(self.home),
        )
        self.assertEqual(discord_snooze_exit, 0, discord_snooze_stderr)

        whatsapp_snooze_exit, _, whatsapp_snooze_stderr = self.run_cli(
            "operator",
            "snooze-webhook-alert",
            "whatsapp_webhook_verification_failed",
            "--minutes",
            "30",
            "--home",
            str(self.home),
        )
        self.assertEqual(whatsapp_snooze_exit, 0, whatsapp_snooze_stderr)

        list_exit, list_stdout, list_stderr = self.run_cli(
            "operator",
            "webhook-alert-snoozes",
            "--home",
            str(self.home),
            "--json",
        )
        self.assertEqual(list_exit, 0, list_stderr)
        list_payload = json.loads(list_stdout)
        self.assertEqual(
            [row["event"] for row in list_payload["rows"]],
            ["whatsapp_webhook_verification_failed", "discord_webhook_auth_failed"],
        )
        self.assertEqual(list_payload["rows"][0]["status"], "sustained_rejections_suppressed")
        self.assertEqual(list_payload["rows"][0]["severity"], "warning")
        self.assertEqual(list_payload["rows"][1]["status"], "snoozed")
        self.assertEqual(list_payload["rows"][1]["severity"], "info")

    def test_operator_webhook_alert_snoozes_prunes_expired_runtime_state(self) -> None:
        setup_exit, _, setup_stderr = self.run_cli(
            "channel",
            "add",
            "discord",
            "--home",
            str(self.home),
            "--allow-legacy-message-webhook",
            "--webhook-secret",
            "discord-webhook-secret",
        )
        self.assertEqual(setup_exit, 0, setup_stderr)

        response = handle_discord_webhook(
            config_manager=self.config_manager,
            state_db=self.state_db,
            path=DISCORD_WEBHOOK_PATH,
            method="POST",
            content_type="application/json",
            headers={},
            body=b"{}",
        )
        self.assertEqual(response.status_code, 401)

        snooze_exit, _, snooze_stderr = self.run_cli(
            "operator",
            "snooze-webhook-alert",
            "discord_webhook_auth_failed",
            "--minutes",
            "30",
            "--home",
            str(self.home),
        )
        self.assertEqual(snooze_exit, 0, snooze_stderr)

        with patch(
            "spark_intelligence.ops.service._utc_now",
            return_value=datetime.now(timezone.utc) + timedelta(minutes=31),
        ):
            list_exit, list_stdout, list_stderr = self.run_cli(
                "operator",
                "webhook-alert-snoozes",
                "--home",
                str(self.home),
                "--json",
            )

        self.assertEqual(list_exit, 0, list_stderr)
        list_payload = json.loads(list_stdout)
        self.assertEqual(list_payload["rows"], [])
        with self.state_db.connect() as conn:
            row = conn.execute(
                "SELECT value FROM runtime_state WHERE state_key = ? LIMIT 1",
                ("ops:webhook_alert_snooze:discord_webhook_auth_failed",),
            ).fetchone()
        self.assertIsNone(row)

    def test_operator_inbox_surfaces_whatsapp_verification_rejections(self) -> None:
        setup_exit, _, setup_stderr = self.run_cli(
            "channel",
            "add",
            "whatsapp",
            "--home",
            str(self.home),
            "--bot-token",
            "whatsapp-token",
            "--webhook-secret",
            "whatsapp-webhook-secret",
            "--webhook-verify-token",
            "whatsapp-verify-token",
        )
        self.assertEqual(setup_exit, 0, setup_stderr)

        response = handle_whatsapp_webhook(
            config_manager=self.config_manager,
            state_db=self.state_db,
            path=WHATSAPP_WEBHOOK_PATH,
            method="GET",
            content_type=None,
            headers={},
            body=b"",
            query_params={
                "hub.mode": "subscribe",
                "hub.verify_token": "wrong-token",
                "hub.challenge": "challenge-code",
            },
        )
        self.assertEqual(response.status_code, 401)

        inbox_exit, inbox_stdout, inbox_stderr = self.run_cli(
            "operator",
            "inbox",
            "--home",
            str(self.home),
            "--json",
        )

        self.assertEqual(inbox_exit, 0, inbox_stderr)
        payload = json.loads(inbox_stdout)
        self.assertEqual(payload["counts"]["webhook_alerts"], 1)
        self.assertEqual(payload["webhooks"][0]["event"], "whatsapp_webhook_verification_failed")
        webhook_items = [item for item in payload["items"] if item["kind"] == "webhook"]
        self.assertEqual(len(webhook_items), 1)
        self.assertIn("WhatsApp webhook verification rejected 1 time(s)", webhook_items[0]["summary"])
        self.assertEqual(
            webhook_items[0]["recommended_command"],
            "spark-intelligence gateway traces --event whatsapp_webhook_verification_failed --limit 20",
        )

    def test_operator_inbox_surfaces_whatsapp_ingress_missing(self) -> None:
        setup_exit, _, setup_stderr = self.run_cli(
            "channel",
            "add",
            "whatsapp",
            "--home",
            str(self.home),
            "--bot-token",
            "whatsapp-token",
        )
        self.assertEqual(setup_exit, 0, setup_stderr)

        inbox_exit, inbox_stdout, inbox_stderr = self.run_cli(
            "operator",
            "inbox",
            "--home",
            str(self.home),
            "--json",
        )

        self.assertEqual(inbox_exit, 0, inbox_stderr)
        payload = json.loads(inbox_stdout)
        self.assertEqual(payload["counts"]["channel_alerts"], 1)
        self.assertEqual(payload["channels"][0]["channel_id"], "whatsapp")
        self.assertEqual(payload["channels"][0]["status"], "ingress_missing")
        self.assertIn("WhatsApp ingress is not ready", payload["channels"][0]["summary"])
        channel_items = [item for item in payload["items"] if item["kind"] == "channel"]
        self.assertEqual(len(channel_items), 1)
        self.assertEqual(
            channel_items[0]["recommended_command"],
            "spark-intelligence channel add whatsapp --webhook-secret <secret> --webhook-verify-token <token>",
        )

    def test_operator_inbox_escalates_sustained_whatsapp_verification_rejections(self) -> None:
        setup_exit, _, setup_stderr = self.run_cli(
            "channel",
            "add",
            "whatsapp",
            "--home",
            str(self.home),
            "--bot-token",
            "whatsapp-token",
            "--webhook-secret",
            "whatsapp-webhook-secret",
            "--webhook-verify-token",
            "whatsapp-verify-token",
        )
        self.assertEqual(setup_exit, 0, setup_stderr)

        for _ in range(3):
            response = handle_whatsapp_webhook(
                config_manager=self.config_manager,
                state_db=self.state_db,
                path=WHATSAPP_WEBHOOK_PATH,
                method="GET",
                content_type=None,
                headers={},
                body=b"",
                query_params={
                    "hub.mode": "subscribe",
                    "hub.verify_token": "wrong-token",
                    "hub.challenge": "challenge-code",
                },
            )
            self.assertEqual(response.status_code, 401)

        inbox_exit, inbox_stdout, inbox_stderr = self.run_cli(
            "operator",
            "inbox",
            "--home",
            str(self.home),
            "--json",
        )

        self.assertEqual(inbox_exit, 0, inbox_stderr)
        payload = json.loads(inbox_stdout)
        self.assertEqual(payload["counts"]["webhook_alerts"], 1)
        alert = payload["webhooks"][0]
        self.assertEqual(alert["event"], "whatsapp_webhook_verification_failed")
        self.assertEqual(alert["status"], "sustained_rejections")
        self.assertEqual(alert["severity"], "critical")
        self.assertEqual(alert["count"], 3)
        webhook_items = [item for item in payload["items"] if item["kind"] == "webhook"]
        self.assertEqual(len(webhook_items), 1)
        self.assertEqual(webhook_items[0]["priority"], "high")

    def test_operator_inbox_cools_down_old_whatsapp_verification_rejections(self) -> None:
        setup_exit, _, setup_stderr = self.run_cli(
            "channel",
            "add",
            "whatsapp",
            "--home",
            str(self.home),
            "--bot-token",
            "whatsapp-token",
            "--webhook-secret",
            "whatsapp-webhook-secret",
            "--webhook-verify-token",
            "whatsapp-verify-token",
        )
        self.assertEqual(setup_exit, 0, setup_stderr)

        for _ in range(3):
            response = handle_whatsapp_webhook(
                config_manager=self.config_manager,
                state_db=self.state_db,
                path=WHATSAPP_WEBHOOK_PATH,
                method="GET",
                content_type=None,
                headers={},
                body=b"",
                query_params={
                    "hub.mode": "subscribe",
                    "hub.verify_token": "wrong-token",
                    "hub.challenge": "challenge-code",
                },
            )
            self.assertEqual(response.status_code, 401)

        with patch(
            "spark_intelligence.ops.service._utc_now",
            return_value=datetime.now(timezone.utc) + timedelta(minutes=16),
        ):
            inbox_exit, inbox_stdout, inbox_stderr = self.run_cli(
                "operator",
                "inbox",
                "--home",
                str(self.home),
                "--json",
            )

        self.assertEqual(inbox_exit, 0, inbox_stderr)
        payload = json.loads(inbox_stdout)
        self.assertEqual(payload["counts"]["webhook_alerts"], 0)
        self.assertEqual(payload["webhooks"], [])

    def test_operator_can_snooze_whatsapp_verification_alert(self) -> None:
        setup_exit, _, setup_stderr = self.run_cli(
            "channel",
            "add",
            "whatsapp",
            "--home",
            str(self.home),
            "--bot-token",
            "whatsapp-token",
            "--webhook-secret",
            "whatsapp-webhook-secret",
            "--webhook-verify-token",
            "whatsapp-verify-token",
        )
        self.assertEqual(setup_exit, 0, setup_stderr)

        response = handle_whatsapp_webhook(
            config_manager=self.config_manager,
            state_db=self.state_db,
            path=WHATSAPP_WEBHOOK_PATH,
            method="GET",
            content_type=None,
            headers={},
            body=b"",
            query_params={
                "hub.mode": "subscribe",
                "hub.verify_token": "wrong-token",
                "hub.challenge": "challenge-code",
            },
        )
        self.assertEqual(response.status_code, 401)

        snooze_exit, snooze_stdout, snooze_stderr = self.run_cli(
            "operator",
            "snooze-webhook-alert",
            "whatsapp_webhook_verification_failed",
            "--minutes",
            "30",
            "--home",
            str(self.home),
        )
        self.assertEqual(snooze_exit, 0, snooze_stderr)
        self.assertIn("Snoozed webhook alert 'whatsapp_webhook_verification_failed'", snooze_stdout)

        inbox_exit, inbox_stdout, inbox_stderr = self.run_cli(
            "operator",
            "inbox",
            "--home",
            str(self.home),
            "--json",
        )
        self.assertEqual(inbox_exit, 0, inbox_stderr)
        payload = json.loads(inbox_stdout)
        self.assertEqual(payload["counts"]["webhook_alerts"], 0)
        self.assertEqual(payload["counts"]["webhook_snoozes"], 1)
        self.assertEqual(payload["counts"]["active_suppressed_webhook_snoozes"], 1)
        self.assertEqual(payload["webhooks"], [])
        self.assertEqual(len(payload["webhook_snoozes"]), 1)
        self.assertEqual(payload["webhook_snoozes"][0]["event"], "whatsapp_webhook_verification_failed")
        self.assertIsInstance(payload["webhook_snoozes"][0]["snoozed_at"], str)
        self.assertIsNone(payload["webhook_snoozes"][0]["reason"])
        self.assertEqual(payload["webhook_snoozes"][0]["suppressed_recent_count"], 1)
        self.assertEqual(payload["webhook_snoozes"][0]["latest_suppressed_reason"], "WhatsApp webhook verify token is invalid.")
        self.assertIsInstance(payload["webhook_snoozes"][0]["latest_suppressed_at"], str)
        snooze_items = [item for item in payload["items"] if item["kind"] == "webhook_snooze"]
        self.assertEqual(len(snooze_items), 1)
        self.assertIn("was snoozed at", snooze_items[0]["summary"])
        self.assertEqual(
            snooze_items[0]["clear_command"],
            "spark-intelligence operator clear-webhook-alert-snooze whatsapp_webhook_verification_failed",
        )
        self.assertIn(
            "Suppressed 1 recent rejection(s); latest reason: WhatsApp webhook verify token is invalid.",
            snooze_items[0]["summary"],
        )
        self.assertIn("Last suppressed at:", snooze_items[0]["summary"])
        self.assertEqual(
            snooze_items[0]["recommended_command"],
            "spark-intelligence operator clear-webhook-alert-snooze whatsapp_webhook_verification_failed",
        )

    def test_doctor_degrades_after_telegram_poll_failure(self) -> None:
        self.add_telegram_channel(bot_token="good-token")

        class PollFailingClient:
            def __init__(self, token: str):
                self.token = token

            def get_me(self) -> dict[str, object]:
                return {"result": {"username": "sparkbot"}}

            def get_updates(self, *, offset: int | None = None, timeout_seconds: int = 5) -> list[dict[str, object]]:
                raise URLError("offline")

            def send_message(self, *, chat_id: str, text: str) -> dict[str, object]:
                return {"ok": True}

        with patch("spark_intelligence.gateway.runtime.TelegramBotApiClient", PollFailingClient):
            self.run_cli(
                "gateway",
                "start",
                "--home",
                str(self.home),
                "--once",
            )

        doctor_exit, doctor_stdout, doctor_stderr = self.run_cli(
            "doctor",
            "--home",
            str(self.home),
        )

        self.assertEqual(doctor_exit, 1, doctor_stderr)
        self.assertIn("Doctor status: degraded", doctor_stdout)
        self.assertIn("[fail] telegram-runtime: poll_failures=1", doctor_stdout)

    def test_gateway_status_is_not_ready_when_telegram_is_configured_without_provider(self) -> None:
        self.add_telegram_channel()

        exit_code, stdout, stderr = self.run_cli(
            "gateway",
            "status",
            "--home",
            str(self.home),
        )

        self.assertEqual(exit_code, 1, stderr)
        self.assertIn("Gateway ready: no", stdout)
        self.assertIn("- channels: telegram", stdout)
        self.assertIn("- providers: none", stdout)

    def test_gateway_status_lists_auth_profile_backed_provider_when_secret_is_missing(self) -> None:
        self.add_telegram_channel()
        connect_exit, _, connect_stderr = self.run_cli(
            "auth",
            "connect",
            "openai",
            "--home",
            str(self.home),
            "--api-key-env",
            "MISSING_OPENAI_KEY",
            "--model",
            "gpt-5.4",
        )
        self.assertEqual(connect_exit, 0, connect_stderr)

        exit_code, stdout, stderr = self.run_cli(
            "gateway",
            "status",
            "--home",
            str(self.home),
        )

        self.assertEqual(exit_code, 1, stderr)
        self.assertIn("Gateway ready: no", stdout)
        self.assertIn("- channels: telegram", stdout)
        self.assertIn("- providers: openai", stdout)
        self.assertIn(
            "- provider-runtime: degraded Provider 'openai' is missing secret value for env ref 'MISSING_OPENAI_KEY'.",
            stdout,
        )
        self.assertIn("- provider-execution: ok openai:direct_http", stdout)
        self.assertIn("- oauth-maintenance: ok no oauth providers configured", stdout)
        self.assertIn(
            "- provider=openai method=api_key_env status=pending_secret transport=direct_http exec_ready=yes dependency=direct_http",
            stdout,
        )

    def test_gateway_status_surfaces_repair_hint_for_paused_channel(self) -> None:
        self.add_telegram_channel(bot_token="good-token")
        connect_exit, _, connect_stderr = self.run_cli(
            "auth",
            "connect",
            "openai",
            "--home",
            str(self.home),
            "--api-key",
            "openai-secret",
            "--model",
            "gpt-5.4",
        )
        self.assertEqual(connect_exit, 0, connect_stderr)

        pause_exit, _, pause_stderr = self.run_cli(
            "operator",
            "set-channel",
            "telegram",
            "paused",
            "--home",
            str(self.home),
        )
        self.assertEqual(pause_exit, 0, pause_stderr)

        gateway_exit, gateway_stdout, gateway_stderr = self.run_cli(
            "gateway",
            "status",
            "--home",
            str(self.home),
        )
        self.assertEqual(gateway_exit, 1, gateway_stderr)
        self.assertIn("- repair-hint: spark-intelligence operator set-channel telegram enabled", gateway_stdout)

        status_exit, status_stdout, status_stderr = self.run_cli(
            "status",
            "--home",
            str(self.home),
        )
        self.assertEqual(status_exit, 0, status_stderr)
        self.assertIn("- repair hint: spark-intelligence operator set-channel telegram enabled", status_stdout)

    def test_status_json_includes_auth_report(self) -> None:
        connect_exit, _, connect_stderr = self.run_cli(
            "auth",
            "connect",
            "anthropic",
            "--home",
            str(self.home),
            "--api-key",
            "anthropic-secret",
            "--model",
            "claude-opus-4-6",
        )
        self.assertEqual(connect_exit, 0, connect_stderr)

        exit_code, stdout, stderr = self.run_cli(
            "status",
            "--home",
            str(self.home),
            "--json",
        )

        self.assertEqual(exit_code, 0, stderr)
        payload = json.loads(stdout)
        self.assertIn("auth", payload)
        self.assertTrue(payload["auth"]["ok"])
        self.assertEqual(payload["auth"]["default_provider"], "anthropic")
        self.assertEqual(payload["auth"]["providers"][0]["auth_profile_id"], "anthropic:default")
        self.assertIn("gateway", payload)
        self.assertTrue(payload["gateway"]["oauth_maintenance_ok"])
        self.assertEqual(payload["gateway"]["oauth_maintenance_detail"], "no oauth providers configured")
        self.assertTrue(payload["gateway"]["provider_runtime_ok"])
        self.assertEqual(payload["gateway"]["provider_runtime_detail"], "anthropic:anthropic:default:direct_http")
        self.assertTrue(payload["gateway"]["provider_execution_ok"])
        self.assertEqual(payload["gateway"]["provider_execution_detail"], "anthropic:direct_http")
        self.assertIn(
            "provider=anthropic method=api_key_env status=active transport=direct_http exec_ready=yes dependency=direct_http",
            payload["gateway"]["provider_lines"],
        )

    def test_gateway_status_surfaces_codex_transport_and_oauth_maintenance_degradation(self) -> None:
        self.add_telegram_channel()
        start_exit, start_stdout, start_stderr = self.run_cli(
            "auth",
            "login",
            "openai-codex",
            "--home",
            str(self.home),
            "--json",
        )
        self.assertEqual(start_exit, 0, start_stderr)
        start_payload = json.loads(start_stdout)
        callback_url = (
            "http://127.0.0.1:1455/auth/callback"
            f"?state={start_payload['callback_state']}&code=test-oauth-code"
        )

        with patch(
            "spark_intelligence.auth.service.exchange_oauth_authorization_code",
            return_value={
                "access_token": "oauth-access-token",
                "refresh_token": "oauth-refresh-token",
                "expires_in": 60,
            },
        ):
            complete_exit, _, complete_stderr = self.run_cli(
                "auth",
                "login",
                "openai-codex",
                "--home",
                str(self.home),
                "--callback-url",
                callback_url,
            )
        self.assertEqual(complete_exit, 0, complete_stderr)

        exit_code, stdout, stderr = self.run_cli(
            "gateway",
            "status",
            "--home",
            str(self.home),
        )

        self.assertEqual(exit_code, 1, stderr)
        self.assertIn("- provider-runtime: ok openai-codex:openai-codex:default:external_cli_wrapper", stdout)
        self.assertIn("- provider-execution: degraded openai-codex:external_cli_wrapper:researcher_disabled", stdout)
        self.assertIn("- oauth-maintenance: degraded oauth maintenance has never run; expiring_soon=openai-codex", stdout)
        self.assertIn("- repair-hint: spark-intelligence jobs tick", stdout)
        self.assertIn("- repair-hint: spark-intelligence researcher status", stdout)
        self.assertIn(
            "- provider=openai-codex method=oauth status=expiring_soon transport=external_cli_wrapper exec_ready=no dependency=researcher_disabled",
            stdout,
        )

        status_exit, status_stdout, status_stderr = self.run_cli(
            "status",
            "--home",
            str(self.home),
        )
        self.assertEqual(status_exit, 1, status_stderr)
        self.assertIn("- oauth maintenance detail: oauth maintenance has never run; expiring_soon=openai-codex", status_stdout)
        self.assertIn("- repair hint: spark-intelligence jobs tick", status_stdout)
        self.assertIn("- repair hint: spark-intelligence researcher status", status_stdout)

    def test_gateway_start_fails_closed_when_runtime_provider_is_unresolved(self) -> None:
        self.add_telegram_channel(bot_token="good-token")
        connect_exit, _, connect_stderr = self.run_cli(
            "auth",
            "connect",
            "openai",
            "--home",
            str(self.home),
            "--api-key-env",
            "MISSING_OPENAI_KEY",
            "--model",
            "gpt-5.4",
        )
        self.assertEqual(connect_exit, 0, connect_stderr)

        exit_code, stdout, stderr = self.run_cli(
            "gateway",
            "start",
            "--home",
            str(self.home),
            "--once",
        )

        self.assertEqual(exit_code, 1, stderr)
        self.assertIn("Provider runtime readiness is degraded. Gateway did not start polling.", stdout)
        self.assertNotIn("Telegram bot authenticated:", stdout)

    def test_gateway_start_fails_closed_when_provider_execution_is_unavailable(self) -> None:
        self.add_telegram_channel(bot_token="good-token")
        start_exit, start_stdout, start_stderr = self.run_cli(
            "auth",
            "login",
            "openai-codex",
            "--home",
            str(self.home),
            "--json",
        )
        self.assertEqual(start_exit, 0, start_stderr)
        start_payload = json.loads(start_stdout)
        callback_url = (
            "http://127.0.0.1:1455/auth/callback"
            f"?state={start_payload['callback_state']}&code=test-oauth-code"
        )

        with patch(
            "spark_intelligence.auth.service.exchange_oauth_authorization_code",
            return_value={
                "access_token": "oauth-access-token",
                "refresh_token": "oauth-refresh-token",
                "expires_in": 3600,
            },
        ):
            complete_exit, _, complete_stderr = self.run_cli(
                "auth",
                "login",
                "openai-codex",
                "--home",
                str(self.home),
                "--callback-url",
                callback_url,
            )
        self.assertEqual(complete_exit, 0, complete_stderr)

        exit_code, stdout, stderr = self.run_cli(
            "gateway",
            "start",
            "--home",
            str(self.home),
            "--once",
        )

        self.assertEqual(exit_code, 1, stderr)
        self.assertIn("Provider execution readiness is degraded. Gateway did not start polling.", stdout)
        self.assertNotIn("Telegram bot authenticated:", stdout)

    def test_gateway_start_surfaces_repair_hint_for_paused_channel(self) -> None:
        self.add_telegram_channel(bot_token="good-token")
        connect_exit, _, connect_stderr = self.run_cli(
            "auth",
            "connect",
            "openai",
            "--home",
            str(self.home),
            "--api-key",
            "openai-secret",
            "--model",
            "gpt-5.4",
        )
        self.assertEqual(connect_exit, 0, connect_stderr)

        pause_exit, _, pause_stderr = self.run_cli(
            "operator",
            "set-channel",
            "telegram",
            "paused",
            "--home",
            str(self.home),
        )
        self.assertEqual(pause_exit, 0, pause_stderr)

        exit_code, stdout, stderr = self.run_cli(
            "gateway",
            "start",
            "--home",
            str(self.home),
            "--once",
        )

        self.assertEqual(exit_code, 0, stderr)
        self.assertIn("Telegram adapter is paused by operator. Gateway did not start polling.", stdout)
        self.assertIn("- repair-hint: spark-intelligence operator set-channel telegram enabled", stdout)
        self.assertNotIn("Telegram bot authenticated:", stdout)

    def test_doctor_degrades_when_codex_wrapper_transport_lacks_researcher_bridge(self) -> None:
        start_exit, start_stdout, start_stderr = self.run_cli(
            "auth",
            "login",
            "openai-codex",
            "--home",
            str(self.home),
            "--json",
        )
        self.assertEqual(start_exit, 0, start_stderr)
        start_payload = json.loads(start_stdout)
        callback_url = (
            "http://127.0.0.1:1455/auth/callback"
            f"?state={start_payload['callback_state']}&code=test-oauth-code"
        )

        with patch(
            "spark_intelligence.auth.service.exchange_oauth_authorization_code",
            return_value={
                "access_token": "oauth-access-token",
                "refresh_token": "oauth-refresh-token",
                "expires_in": 3600,
            },
        ):
            complete_exit, _, complete_stderr = self.run_cli(
                "auth",
                "login",
                "openai-codex",
                "--home",
                str(self.home),
                "--callback-url",
                callback_url,
            )
        self.assertEqual(complete_exit, 0, complete_stderr)

        doctor_exit, doctor_stdout, doctor_stderr = self.run_cli(
            "doctor",
            "--home",
            str(self.home),
        )

        self.assertEqual(doctor_exit, 1, doctor_stderr)
        self.assertIn("[fail] provider-execution: openai-codex:external_cli_wrapper:researcher_disabled", doctor_stdout)
        self.assertIn("spark-intelligence status", doctor_stdout)
        self.assertIn("spark-intelligence operator security", doctor_stdout)

    def test_setup_with_swarm_access_token_persists_named_env_ref(self) -> None:
        with tempfile.TemporaryDirectory() as tempdir:
            home = Path(tempdir)
            env_key = "CUSTOM_SWARM_TOKEN"

            exit_code, stdout, stderr = self.run_cli(
                "setup",
                "--home",
                str(home),
                "--swarm-api-url",
                "https://swarm.example",
                "--swarm-workspace-id",
                "ws-test",
                "--swarm-access-token",
                "secret-token",
                "--swarm-access-token-env",
                env_key,
            )

            self.assertEqual(exit_code, 0, stderr)
            self.assertIn("Setup integrations:", stdout)
            config_manager = ConfigManager.from_home(str(home))
            self.assertEqual(config_manager.read_env_map()[env_key], "secret-token")
            self.assertEqual(config_manager.get_path("spark.swarm.api_url"), "https://swarm.example")
            self.assertEqual(config_manager.get_path("spark.swarm.workspace_id"), "ws-test")
            self.assertEqual(config_manager.get_path("spark.swarm.access_token_env"), env_key)

    def test_swarm_configure_persists_refresh_token_and_auth_client_key_refs(self) -> None:
        exit_code, stdout, stderr = self.run_cli(
            "swarm",
            "configure",
            "--home",
            str(self.home),
            "--api-url",
            "https://api-production-6ea6.up.railway.app",
            "--supabase-url",
            "https://sfjcvvyvdwjdvphefggg.supabase.co",
            "--workspace-id",
            "ws-test",
            "--access-token",
            "secret-access-token",
            "--refresh-token",
            "secret-refresh-token",
            "--auth-client-key",
            "secret-client-key",
        )

        self.assertEqual(exit_code, 0, stderr)
        env_map = self.config_manager.read_env_map()
        self.assertEqual(env_map["SPARK_SWARM_ACCESS_TOKEN"], "secret-access-token")
        self.assertEqual(env_map["SPARK_SWARM_REFRESH_TOKEN"], "secret-refresh-token")
        self.assertEqual(env_map["SPARK_SWARM_AUTH_CLIENT_KEY"], "secret-client-key")
        self.assertEqual(
            self.config_manager.get_path("spark.swarm.supabase_url"),
            "https://sfjcvvyvdwjdvphefggg.supabase.co",
        )
        self.assertEqual(self.config_manager.get_path("spark.swarm.refresh_token_env"), "SPARK_SWARM_REFRESH_TOKEN")
        self.assertEqual(self.config_manager.get_path("spark.swarm.auth_client_key_env"), "SPARK_SWARM_AUTH_CLIENT_KEY")
        self.assertIn("Spark Swarm bridge config updated.", stdout)

    def test_telegram_onboard_preserves_existing_allowlist_and_pairing_mode_on_token_rotation(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"], bot_token="old-token")

        with patch(
            "spark_intelligence.cli.inspect_telegram_bot_token",
            return_value=TelegramBotProfile(
                bot_id="42",
                username="sparkbot",
                first_name="Spark Bot",
                can_join_groups=True,
                can_read_all_group_messages=False,
                supports_inline_queries=False,
            ),
        ):
            exit_code, stdout, stderr = self.run_cli(
                "channel",
                "telegram-onboard",
                "--home",
                str(self.home),
                "--bot-token",
                "new-token",
            )

        self.assertEqual(exit_code, 0, stderr)
        config_manager = ConfigManager.from_home(str(self.home))
        record = config_manager.get_path("channels.records.telegram")
        self.assertEqual(record["pairing_mode"], "allowlist")
        self.assertEqual(record["allowed_users"], ["111"])
        self.assertEqual(config_manager.read_env_map()["TELEGRAM_BOT_TOKEN"], "new-token")
        self.assertEqual(record["status"], "enabled")
        self.assertIn("Configured channel 'telegram' with pairing mode 'allowlist' status 'enabled'.", stdout)

    def test_telegram_onboard_preserves_existing_channel_status_on_token_rotation(self) -> None:
        self.add_telegram_channel(pairing_mode="pairing", bot_token="old-token")
        status_exit, _, status_stderr = self.run_cli(
            "operator",
            "set-channel",
            "telegram",
            "paused",
            "--home",
            str(self.home),
        )
        self.assertEqual(status_exit, 0, status_stderr)

        with patch(
            "spark_intelligence.cli.inspect_telegram_bot_token",
            return_value=TelegramBotProfile(
                bot_id="42",
                username="sparkbot",
                first_name="Spark Bot",
                can_join_groups=True,
                can_read_all_group_messages=False,
                supports_inline_queries=False,
            ),
        ):
            exit_code, stdout, stderr = self.run_cli(
                "channel",
                "telegram-onboard",
                "--home",
                str(self.home),
                "--bot-token",
                "new-token",
            )

        self.assertEqual(exit_code, 0, stderr)
        config_manager = ConfigManager.from_home(str(self.home))
        record = config_manager.get_path("channels.records.telegram")
        self.assertEqual(record["status"], "paused")
        self.assertEqual(config_manager.read_env_map()["TELEGRAM_BOT_TOKEN"], "new-token")
        self.assertIn("Configured channel 'telegram' with pairing mode 'pairing' status 'paused'.", stdout)

    def test_channel_add_preserves_existing_auth_ref_and_status_without_new_token(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"], bot_token="old-token")
        status_exit, _, status_stderr = self.run_cli(
            "operator",
            "set-channel",
            "telegram",
            "paused",
            "--home",
            str(self.home),
        )
        self.assertEqual(status_exit, 0, status_stderr)

        exit_code, stdout, stderr = self.run_cli(
            "channel",
            "add",
            "telegram",
            "--home",
            str(self.home),
            "--pairing-mode",
            "allowlist",
            "--allowed-user",
            "111",
        )

        self.assertEqual(exit_code, 0, stderr)
        config_manager = ConfigManager.from_home(str(self.home))
        record = config_manager.get_path("channels.records.telegram")
        self.assertEqual(record["auth_ref"], "TELEGRAM_BOT_TOKEN")
        self.assertEqual(record["status"], "paused")
        self.assertEqual(config_manager.read_env_map()["TELEGRAM_BOT_TOKEN"], "old-token")
        with self.state_db.connect() as conn:
            row = conn.execute(
                "SELECT status, auth_ref FROM channel_installations WHERE channel_id = 'telegram' LIMIT 1"
            ).fetchone()
        self.assertEqual(row["status"], "paused")
        self.assertEqual(row["auth_ref"], "TELEGRAM_BOT_TOKEN")
        self.assertIn("Configured channel 'telegram' with pairing mode 'allowlist' status 'paused'.", stdout)

    def test_channel_add_preserves_existing_allowlist_on_token_rotation(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"], bot_token="old-token")

        with patch(
            "spark_intelligence.cli.inspect_telegram_bot_token",
            return_value=TelegramBotProfile(
                bot_id="42",
                username="sparkbot",
                first_name="Spark Bot",
                can_join_groups=True,
                can_read_all_group_messages=False,
                supports_inline_queries=False,
            ),
        ):
            exit_code, stdout, stderr = self.run_cli(
                "channel",
                "add",
                "telegram",
                "--home",
                str(self.home),
                "--bot-token",
                "new-token",
            )

        self.assertEqual(exit_code, 0, stderr)
        config_manager = ConfigManager.from_home(str(self.home))
        record = config_manager.get_path("channels.records.telegram")
        self.assertEqual(record["pairing_mode"], "allowlist")
        self.assertEqual(record["allowed_users"], ["111"])
        self.assertEqual(record["status"], "enabled")
        self.assertEqual(config_manager.read_env_map()["TELEGRAM_BOT_TOKEN"], "new-token")
        self.assertIn("Configured channel 'telegram' with pairing mode 'allowlist' status 'enabled'.", stdout)

    def test_channel_add_can_clear_existing_allowlist_explicitly(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111", "222"], bot_token="old-token")

        exit_code, stdout, stderr = self.run_cli(
            "channel",
            "add",
            "telegram",
            "--home",
            str(self.home),
            "--clear-allowed-users",
            "--pairing-mode",
            "allowlist",
        )

        self.assertEqual(exit_code, 0, stderr)
        config_manager = ConfigManager.from_home(str(self.home))
        record = config_manager.get_path("channels.records.telegram")
        self.assertEqual(record["allowed_users"], [])
        self.assertEqual(record["pairing_mode"], "allowlist")
        self.assertIn("Configured channel 'telegram' with pairing mode 'allowlist' status 'enabled'.", stdout)

    def test_telegram_onboard_can_clear_existing_allowlist_explicitly(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111", "222"], bot_token="old-token")

        with patch(
            "spark_intelligence.cli.inspect_telegram_bot_token",
            return_value=TelegramBotProfile(
                bot_id="42",
                username="sparkbot",
                first_name="Spark Bot",
                can_join_groups=True,
                can_read_all_group_messages=False,
                supports_inline_queries=False,
            ),
        ):
            exit_code, stdout, stderr = self.run_cli(
                "channel",
                "telegram-onboard",
                "--home",
                str(self.home),
                "--bot-token",
                "new-token",
                "--clear-allowed-users",
            )

        self.assertEqual(exit_code, 0, stderr)
        config_manager = ConfigManager.from_home(str(self.home))
        record = config_manager.get_path("channels.records.telegram")
        self.assertEqual(record["allowed_users"], [])
        self.assertEqual(config_manager.read_env_map()["TELEGRAM_BOT_TOKEN"], "new-token")
        self.assertIn("Configured channel 'telegram' with pairing mode 'allowlist' status 'enabled'.", stdout)

    def test_channel_add_preserves_existing_status_and_auth_for_discord(self) -> None:
        setup_exit, _, setup_stderr = self.run_cli(
            "channel",
            "add",
            "discord",
            "--home",
            str(self.home),
            "--bot-token",
            "discord-old-token",
            "--allowed-user",
            "111",
            "--pairing-mode",
            "allowlist",
        )
        self.assertEqual(setup_exit, 0, setup_stderr)

        status_exit, _, status_stderr = self.run_cli(
            "operator",
            "set-channel",
            "discord",
            "paused",
            "--home",
            str(self.home),
        )
        self.assertEqual(status_exit, 0, status_stderr)

        exit_code, stdout, stderr = self.run_cli(
            "channel",
            "add",
            "discord",
            "--home",
            str(self.home),
        )

        self.assertEqual(exit_code, 0, stderr)
        config_manager = ConfigManager.from_home(str(self.home))
        record = config_manager.get_path("channels.records.discord")
        self.assertEqual(record["pairing_mode"], "allowlist")
        self.assertEqual(record["allowed_users"], ["111"])
        self.assertEqual(record["status"], "paused")
        self.assertEqual(record["auth_ref"], "DISCORD_BOT_TOKEN")
        self.assertEqual(config_manager.read_env_map()["DISCORD_BOT_TOKEN"], "discord-old-token")
        self.assertIn("Configured channel 'discord' with pairing mode 'allowlist' status 'paused'.", stdout)

    def test_channel_add_can_clear_existing_allowlist_explicitly_for_discord(self) -> None:
        setup_exit, _, setup_stderr = self.run_cli(
            "channel",
            "add",
            "discord",
            "--home",
            str(self.home),
            "--bot-token",
            "discord-old-token",
            "--allowed-user",
            "111",
            "--allowed-user",
            "222",
            "--pairing-mode",
            "allowlist",
        )
        self.assertEqual(setup_exit, 0, setup_stderr)

        exit_code, stdout, stderr = self.run_cli(
            "channel",
            "add",
            "discord",
            "--home",
            str(self.home),
            "--clear-allowed-users",
            "--pairing-mode",
            "allowlist",
        )

        self.assertEqual(exit_code, 0, stderr)
        config_manager = ConfigManager.from_home(str(self.home))
        record = config_manager.get_path("channels.records.discord")
        self.assertEqual(record["allowed_users"], [])
        self.assertEqual(record["pairing_mode"], "allowlist")
        self.assertEqual(record["auth_ref"], "DISCORD_BOT_TOKEN")
        self.assertIn("Configured channel 'discord' with pairing mode 'allowlist' status 'enabled'.", stdout)

    def test_channel_add_persists_discord_webhook_secret_ref(self) -> None:
        exit_code, stdout, stderr = self.run_cli(
            "channel",
            "add",
            "discord",
            "--home",
            str(self.home),
            "--allow-legacy-message-webhook",
            "--webhook-secret",
            "discord-webhook-secret",
        )

        self.assertEqual(exit_code, 0, stderr)
        config_manager = ConfigManager.from_home(str(self.home))
        record = config_manager.get_path("channels.records.discord")
        self.assertEqual(record["webhook_auth_ref"], "DISCORD_WEBHOOK_SECRET")
        self.assertTrue(record["allow_legacy_message_webhook"])
        self.assertEqual(config_manager.read_env_map()["DISCORD_WEBHOOK_SECRET"], "discord-webhook-secret")
        self.assertIn("Configured channel 'discord' with pairing mode 'pairing' status 'enabled'.", stdout)

    def test_channel_add_rejects_discord_webhook_secret_without_explicit_legacy_opt_in(self) -> None:
        exit_code, stdout, stderr = self.run_cli(
            "channel",
            "add",
            "discord",
            "--home",
            str(self.home),
            "--webhook-secret",
            "discord-webhook-secret",
        )

        self.assertEqual(exit_code, 2)
        self.assertEqual(stdout, "")
        self.assertIn("Discord legacy message webhooks require --allow-legacy-message-webhook.", stderr)

    def test_channel_add_preserves_existing_discord_webhook_secret_ref(self) -> None:
        setup_exit, _, setup_stderr = self.run_cli(
            "channel",
            "add",
            "discord",
            "--home",
            str(self.home),
            "--allow-legacy-message-webhook",
            "--webhook-secret",
            "discord-webhook-secret",
            "--allowed-user",
            "111",
            "--pairing-mode",
            "allowlist",
        )
        self.assertEqual(setup_exit, 0, setup_stderr)

        exit_code, stdout, stderr = self.run_cli(
            "channel",
            "add",
            "discord",
            "--home",
            str(self.home),
        )

        self.assertEqual(exit_code, 0, stderr)
        config_manager = ConfigManager.from_home(str(self.home))
        record = config_manager.get_path("channels.records.discord")
        self.assertEqual(record["webhook_auth_ref"], "DISCORD_WEBHOOK_SECRET")
        self.assertTrue(record["allow_legacy_message_webhook"])
        self.assertEqual(config_manager.read_env_map()["DISCORD_WEBHOOK_SECRET"], "discord-webhook-secret")
        self.assertIn("Configured channel 'discord' with pairing mode 'allowlist' status 'enabled'.", stdout)

    def test_channel_add_can_disable_existing_discord_legacy_message_webhook(self) -> None:
        setup_exit, _, setup_stderr = self.run_cli(
            "channel",
            "add",
            "discord",
            "--home",
            str(self.home),
            "--allow-legacy-message-webhook",
            "--webhook-secret",
            "discord-webhook-secret",
        )
        self.assertEqual(setup_exit, 0, setup_stderr)

        exit_code, stdout, stderr = self.run_cli(
            "channel",
            "add",
            "discord",
            "--home",
            str(self.home),
            "--disable-legacy-message-webhook",
        )

        self.assertEqual(exit_code, 0, stderr)
        config_manager = ConfigManager.from_home(str(self.home))
        record = config_manager.get_path("channels.records.discord")
        self.assertFalse(record["allow_legacy_message_webhook"])
        self.assertIsNone(record["webhook_auth_ref"])
        self.assertIn("Configured channel 'discord' with pairing mode 'pairing' status 'enabled'.", stdout)

    def test_channel_add_persists_discord_interaction_public_key(self) -> None:
        exit_code, stdout, stderr = self.run_cli(
            "channel",
            "add",
            "discord",
            "--home",
            str(self.home),
            "--interaction-public-key",
            "abcdef123456",
        )

        self.assertEqual(exit_code, 0, stderr)
        config_manager = ConfigManager.from_home(str(self.home))
        record = config_manager.get_path("channels.records.discord")
        self.assertEqual(record["interaction_public_key"], "abcdef123456")
        self.assertIn("Configured channel 'discord' with pairing mode 'pairing' status 'enabled'.", stdout)

    def test_doctor_degrades_when_discord_has_no_ingress_mode(self) -> None:
        setup_exit, _, setup_stderr = self.run_cli(
            "channel",
            "add",
            "discord",
            "--home",
            str(self.home),
            "--bot-token",
            "discord-token",
        )
        self.assertEqual(setup_exit, 0, setup_stderr)

        doctor_exit, doctor_stdout, doctor_stderr = self.run_cli(
            "doctor",
            "--home",
            str(self.home),
        )

        self.assertEqual(doctor_exit, 1, doctor_stderr)
        self.assertIn(
            "[fail] discord-runtime: no signed interaction public key configured and legacy message webhook compatibility is disabled",
            doctor_stdout,
        )

    def test_doctor_reports_discord_legacy_ingress_mode(self) -> None:
        setup_exit, _, setup_stderr = self.run_cli(
            "channel",
            "add",
            "discord",
            "--home",
            str(self.home),
            "--allow-legacy-message-webhook",
            "--webhook-secret",
            "discord-webhook-secret",
        )
        self.assertEqual(setup_exit, 0, setup_stderr)

        doctor_exit, doctor_stdout, doctor_stderr = self.run_cli(
            "doctor",
            "--home",
            str(self.home),
        )

        self.assertEqual(doctor_exit, 0, doctor_stderr)
        self.assertIn(
            "[ok] discord-runtime: status=enabled pairing_mode=pairing auth_ref=missing allowed_users=0 ingress=legacy_message_webhook webhook_auth_ref=DISCORD_WEBHOOK_SECRET",
            doctor_stdout,
        )

    def test_gateway_status_reports_signed_discord_ingress_mode(self) -> None:
        setup_exit, _, setup_stderr = self.run_cli(
            "channel",
            "add",
            "discord",
            "--home",
            str(self.home),
            "--interaction-public-key",
            "abcdef123456",
        )
        self.assertEqual(setup_exit, 0, setup_stderr)

        exit_code, stdout, stderr = self.run_cli(
            "gateway",
            "status",
            "--home",
            str(self.home),
        )

        self.assertEqual(exit_code, 1, stderr)
        self.assertIn(
            "- discord: status=enabled pairing_mode=pairing auth_ref=missing allowed_users=0 ingress=signed_interactions",
            stdout,
        )

    def test_doctor_degrades_when_whatsapp_has_no_webhook_contract(self) -> None:
        setup_exit, _, setup_stderr = self.run_cli(
            "channel",
            "add",
            "whatsapp",
            "--home",
            str(self.home),
            "--bot-token",
            "whatsapp-token",
        )
        self.assertEqual(setup_exit, 0, setup_stderr)

        doctor_exit, doctor_stdout, doctor_stderr = self.run_cli(
            "doctor",
            "--home",
            str(self.home),
        )

        self.assertEqual(doctor_exit, 1, doctor_stderr)
        self.assertIn(
            "[fail] whatsapp-runtime: status=enabled pairing_mode=pairing auth_ref=WHATSAPP_BOT_TOKEN allowed_users=0 ingress=missing",
            doctor_stdout,
        )

    def test_doctor_reports_whatsapp_meta_webhook_ingress_mode(self) -> None:
        setup_exit, _, setup_stderr = self.run_cli(
            "channel",
            "add",
            "whatsapp",
            "--home",
            str(self.home),
            "--bot-token",
            "whatsapp-token",
            "--webhook-secret",
            "whatsapp-webhook-secret",
            "--webhook-verify-token",
            "whatsapp-verify-token",
        )
        self.assertEqual(setup_exit, 0, setup_stderr)

        doctor_exit, doctor_stdout, doctor_stderr = self.run_cli(
            "doctor",
            "--home",
            str(self.home),
        )

        self.assertEqual(doctor_exit, 0, doctor_stderr)
        self.assertIn(
            "[ok] whatsapp-runtime: status=enabled pairing_mode=pairing auth_ref=WHATSAPP_BOT_TOKEN allowed_users=0 ingress=meta_webhook webhook_auth_ref=WHATSAPP_WEBHOOK_SECRET webhook_verify_token_ref=WHATSAPP_WEBHOOK_VERIFY_TOKEN",
            doctor_stdout,
        )

    def test_gateway_status_reports_whatsapp_ingress_mode(self) -> None:
        setup_exit, _, setup_stderr = self.run_cli(
            "channel",
            "add",
            "whatsapp",
            "--home",
            str(self.home),
            "--bot-token",
            "whatsapp-token",
            "--webhook-secret",
            "whatsapp-webhook-secret",
            "--webhook-verify-token",
            "whatsapp-verify-token",
        )
        self.assertEqual(setup_exit, 0, setup_stderr)

        exit_code, stdout, stderr = self.run_cli(
            "gateway",
            "status",
            "--home",
            str(self.home),
        )

        self.assertEqual(exit_code, 1, stderr)
        self.assertIn(
            "- whatsapp: status=enabled pairing_mode=pairing auth_ref=WHATSAPP_BOT_TOKEN allowed_users=0 ingress=meta_webhook",
            stdout,
        )

    def test_channel_add_persists_whatsapp_verify_token_ref(self) -> None:
        exit_code, stdout, stderr = self.run_cli(
            "channel",
            "add",
            "whatsapp",
            "--home",
            str(self.home),
            "--bot-token",
            "whatsapp-token",
            "--webhook-secret",
            "whatsapp-webhook-secret",
            "--webhook-verify-token",
            "whatsapp-verify-token",
        )

        self.assertEqual(exit_code, 0, stderr)
        config_manager = ConfigManager.from_home(str(self.home))
        record = config_manager.get_path("channels.records.whatsapp")
        self.assertEqual(record["webhook_auth_ref"], "WHATSAPP_WEBHOOK_SECRET")
        self.assertEqual(record["webhook_verify_token_ref"], "WHATSAPP_WEBHOOK_VERIFY_TOKEN")
        env_map = config_manager.read_env_map()
        self.assertEqual(env_map["WHATSAPP_WEBHOOK_SECRET"], "whatsapp-webhook-secret")
        self.assertEqual(env_map["WHATSAPP_WEBHOOK_VERIFY_TOKEN"], "whatsapp-verify-token")
        self.assertIn("Configured channel 'whatsapp' with pairing mode 'pairing' status 'enabled'.", stdout)
