from __future__ import annotations

import json

from spark_intelligence.gateway.guardrails import set_runtime_state_value
from spark_intelligence.jobs.service import OAUTH_MAINTENANCE_JOB_ID
from spark_intelligence.mission_control import (
    build_mission_control_prompt_context,
    build_mission_control_snapshot,
    looks_like_mission_control_query,
)

from tests.test_support import SparkTestCase, create_fake_hook_chip


class MissionControlTests(SparkTestCase):
    def test_build_mission_control_snapshot_summarizes_active_runtime_and_actions(self) -> None:
        create_fake_hook_chip(self.home, chip_key="startup-yc")
        create_fake_hook_chip(self.home, chip_key="spark-browser")
        create_fake_hook_chip(self.home, chip_key="domain-chip-voice-comms")
        self.config_manager.set_path("spark.chips.roots", [str(self.home)])
        self.config_manager.set_path(
            "spark.chips.active_keys",
            ["startup-yc", "spark-browser", "domain-chip-voice-comms"],
        )
        self.config_manager.set_path("spark.chips.pinned_keys", ["startup-yc"])
        self.config_manager.set_path("spark.swarm.enabled", False)
        self.add_telegram_channel(bot_token="123456:ABC", allowed_users=["42"])

        set_runtime_state_value(
            state_db=self.state_db,
            state_key="telegram:auth_state",
            value=json.dumps(
                {
                    "status": "ok",
                    "checked_at": "2026-04-09T12:00:00Z",
                    "bot_username": "sparkbot",
                    "error": None,
                },
                sort_keys=True,
            ),
            component="test",
        )
        set_runtime_state_value(
            state_db=self.state_db,
            state_key="telegram:poll_state",
            value=json.dumps(
                {
                    "last_ok_at": "2026-04-09T12:00:00Z",
                    "last_failure_at": "2026-04-09T12:05:00Z",
                    "last_failure_type": "http_error",
                    "last_failure_message": "HTTP 500",
                    "consecutive_failures": 2,
                    "last_backoff_seconds": 30,
                },
                sort_keys=True,
            ),
            component="test",
        )
        with self.state_db.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO job_records (job_id, job_kind, status, schedule_expr, last_run_at, last_result)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    OAUTH_MAINTENANCE_JOB_ID,
                    "oauth_refresh_maintenance",
                    "scheduled",
                    "*/15 * * * *",
                    None,
                    None,
                ),
            )
            conn.commit()

        snapshot = build_mission_control_snapshot(self.config_manager, self.state_db)
        payload = snapshot.to_payload()
        summary = payload["summary"]

        self.assertIn("Spark Intelligence Builder", summary["active_systems"])
        self.assertIn("telegram", summary["active_channels"])
        self.assertIn(f"job:{OAUTH_MAINTENANCE_JOB_ID}", summary["active_loops"])
        self.assertIn("Telegram polling", summary["degraded_surfaces"])
        self.assertIn(
            "Inspect Telegram poll failures in gateway traces before trusting live delivery health.",
            summary["recommended_actions"],
        )

    def test_build_mission_control_prompt_context_covers_runtime_health_questions(self) -> None:
        with self.state_db.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO job_records (job_id, job_kind, status, schedule_expr, last_run_at, last_result)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    OAUTH_MAINTENANCE_JOB_ID,
                    "oauth_refresh_maintenance",
                    "scheduled",
                    "*/15 * * * *",
                    None,
                    None,
                ),
            )
            conn.commit()

        prompt_context = build_mission_control_prompt_context(
            config_manager=self.config_manager,
            state_db=self.state_db,
            user_message="What is degraded right now and what jobs are running?",
        )

        self.assertIn("[Spark mission control]", prompt_context)
        self.assertIn("state=", prompt_context)
        self.assertIn(f"active_loops=job:{OAUTH_MAINTENANCE_JOB_ID}", prompt_context)
        self.assertIn("[Reply rule]", prompt_context)

    def test_build_mission_control_snapshot_does_not_flag_healthy_recurring_jobs_as_degraded(self) -> None:
        with self.state_db.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO job_records (job_id, job_kind, status, schedule_expr, last_run_at, last_result)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    OAUTH_MAINTENANCE_JOB_ID,
                    "oauth_refresh_maintenance",
                    "scheduled",
                    "builtin:oauth_refresh_maintenance",
                    "2026-04-20T12:26:34.152041Z",
                    "scanned=0 due=0 refreshed=0 failed=0 skipped=0",
                ),
            )
            conn.commit()

        snapshot = build_mission_control_snapshot(self.config_manager, self.state_db)
        summary = snapshot.to_payload()["summary"]

        self.assertIn(f"job:{OAUTH_MAINTENANCE_JOB_ID}", summary["active_loops"])
        self.assertNotIn("Scheduled maintenance pending", summary["degraded_surfaces"])
        self.assertNotIn("Run `spark-intelligence jobs tick` to execute due maintenance work.", summary["recommended_actions"])

    def test_mission_control_query_detection_catches_runtime_health_language(self) -> None:
        self.assertTrue(looks_like_mission_control_query("What is degraded right now?"))
        self.assertTrue(looks_like_mission_control_query("What jobs are running?"))
        self.assertTrue(looks_like_mission_control_query("Show me mission control."))
        self.assertFalse(looks_like_mission_control_query("Write me a cold outbound email."))
