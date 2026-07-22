from __future__ import annotations

import json
from unittest.mock import patch

from spark_intelligence.channel.service import inspect_telegram_bot_token
from spark_intelligence.cli import build_parser
from spark_intelligence.gateway.guardrails import apply_inbound_rate_limit, set_runtime_state_value
from spark_intelligence.jobs.service import jobs_tick
from spark_intelligence.self_awareness.capsule import _build_capability_evidence
from spark_intelligence.self_awareness.handoff_check import build_handoff_freshness_check

from tests.test_support import SparkTestCase


class BuilderReliabilityAdoptionTests(SparkTestCase):
    def test_jobs_tick_runs_later_jobs_before_propagating_first_failure(self) -> None:
        with self.state_db.connect() as conn:
            conn.executemany(
                "INSERT INTO job_records(job_id, job_kind, status) VALUES (?, ?, 'scheduled')",
                [
                    ("a-failing-job", "test_failure"),
                    ("b-healthy-job", "test_success"),
                ],
            )
            conn.commit()

        invoked: list[str] = []

        def run_job_side_effect(**kwargs: object) -> str:
            job_id = str(kwargs["job_id"])
            invoked.append(job_id)
            if job_id == "a-failing-job":
                raise RuntimeError("first job failed")
            return "job completed"

        with patch("spark_intelligence.jobs.service._run_job", side_effect=run_job_side_effect):
            with self.assertRaisesRegex(RuntimeError, "first job failed"):
                jobs_tick(self.config_manager, self.state_db)

        self.assertIn("a-failing-job", invoked)
        self.assertIn("b-healthy-job", invoked)
        self.assertLess(invoked.index("a-failing-job"), invoked.index("b-healthy-job"))

    def test_telegram_token_check_rejects_missing_bot_flag(self) -> None:
        def transport(method: str, payload: dict[str, object] | None) -> dict[str, object]:
            self.assertEqual(method, "getMe")
            return {"ok": True, "result": {"id": 123, "username": "spark_test"}}

        with self.assertRaisesRegex(RuntimeError, "non-bot account"):
            inspect_telegram_bot_token("test-token", transport=transport)

    def test_rate_limit_recovers_from_torn_runtime_state(self) -> None:
        state_key = "telegram:rate_limit:user-1"
        set_runtime_state_value(
            state_db=self.state_db,
            state_key=state_key,
            value=json.dumps(
                {
                    "timestamps": [True, 995, {"recorded_at": 996}],
                    "last_notice_at": {"recorded_at": 997},
                }
            ),
        )

        with patch("spark_intelligence.gateway.guardrails.time.time", return_value=1000):
            result = apply_inbound_rate_limit(
                state_db=self.state_db,
                channel_id="telegram",
                external_user_id="user-1",
                limit_per_minute=3,
                notice_cooldown_seconds=30,
            )

        self.assertTrue(result["allowed"])
        with self.state_db.connect() as conn:
            row = conn.execute(
                "SELECT value FROM runtime_state WHERE state_key = ?",
                (state_key,),
            ).fetchone()
        persisted = json.loads(str(row["value"]))
        self.assertEqual(persisted["timestamps"], [995, 1000])
        self.assertEqual(persisted["last_notice_at"], 0)

    def test_handoff_freshness_blocks_when_git_enumeration_is_unavailable(self) -> None:
        with patch(
            "spark_intelligence.self_awareness.handoff_check.subprocess.run",
            side_effect=OSError("git unavailable"),
        ):
            result = build_handoff_freshness_check(
                config_manager=self.config_manager,
                write_report=False,
            ).payload

        self.assertEqual(result["status"], "blocked")
        self.assertFalse(result["healthy"])
        self.assertFalse(result["summary"]["git_changed_paths_available"])
        self.assertIn("handoff_git_changed_paths_unavailable", result["warnings"])

    def test_auth_login_rejects_callback_url_with_listener_mode(self) -> None:
        with self.assertRaises(SystemExit):
            build_parser().parse_args(
                [
                    "auth",
                    "login",
                    "openai",
                    "--callback-url",
                    "http://127.0.0.1:1455/auth/callback?code=test",
                    "--listen",
                ]
            )

    def test_capability_evidence_surfaces_recent_event_sensor_failure(self) -> None:
        with patch(
            "spark_intelligence.self_awareness.capsule.latest_events_by_type",
            side_effect=OSError("private database path"),
        ):
            evidence = _build_capability_evidence(self.state_db)

        sensor = next(item for item in evidence if item.capability_key == "self_awareness_event_sensor")
        self.assertEqual(sensor.source, "observability.store.latest_events_by_type")
        self.assertIn("tool_result_received", sensor.last_failure_reason or "")
        self.assertIn("dispatch_failed", sensor.last_failure_reason or "")
        self.assertNotIn("private database path", sensor.last_failure_reason or "")
        self.assertFalse(sensor.can_claim_confidently)
