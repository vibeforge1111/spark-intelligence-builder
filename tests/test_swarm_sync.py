from __future__ import annotations

import base64
import io
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

from spark_intelligence.observability.store import latest_events_by_type
from spark_intelligence.swarm_bridge.sync import (
    SwarmSyncResult,
    _normalize_collective_payload,
    _normalize_runtime_source,
    _record_swarm_sync_state,
    _record_swarm_failure_state,
    evaluate_swarm_escalation,
    swarm_status,
    sync_swarm_collective,
)

from tests.test_support import SparkTestCase


class SwarmSyncTests(SparkTestCase):
    def _make_jwt(self, *, expires_in_seconds: int) -> str:
        header = {"alg": "none", "typ": "JWT"}
        payload = {
            "iss": "https://sfjcvvyvdwjdvphefggg.supabase.co/auth/v1",
            "exp": int((datetime.now(timezone.utc) + timedelta(seconds=expires_in_seconds)).timestamp()),
        }
        encode = lambda value: base64.urlsafe_b64encode(json.dumps(value).encode("utf-8")).decode("ascii").rstrip("=")
        return f"{encode(header)}.{encode(payload)}."

    def test_normalize_runtime_source_injects_required_swarm_fields(self) -> None:
        payload = {
            "agentId": "agent:spark-researcher",
            "emittedAt": "2026-03-27T10:00:00+00:00",
            "runtimeSource": {
                "kind": "spark_researcher",
                "version": "0.1.0",
                "loopKind": "generalist",
                "chipKey": None,
                "chipLabel": None,
            },
        }

        changed = _normalize_runtime_source(payload)

        self.assertTrue(changed)
        self.assertEqual(payload["runtimeSource"]["sourceInstanceId"], "agent:spark-researcher")
        self.assertEqual(
            payload["runtimeSource"]["sourceRunId"],
            "spark-researcher:2026-03-27T10:00:00+00:00",
        )

    def test_normalize_runtime_source_preserves_existing_values(self) -> None:
        payload = {
            "agentId": "agent:spark-researcher",
            "emittedAt": "2026-03-27T10:00:00+00:00",
            "runtimeSource": {
                "kind": "spark_researcher",
                "version": "0.1.0",
                "loopKind": "generalist",
                "sourceInstanceId": "agent:custom",
                "sourceRunId": "spark-researcher:custom-run",
            },
        }

        changed = _normalize_runtime_source(payload)

        self.assertFalse(changed)
        self.assertEqual(payload["runtimeSource"]["sourceInstanceId"], "agent:custom")
        self.assertEqual(payload["runtimeSource"]["sourceRunId"], "spark-researcher:custom-run")

    def test_normalize_collective_payload_defaults_missing_contradiction_status(self) -> None:
        payload = {
            "agentId": "agent:spark-researcher",
            "emittedAt": "2026-03-27T10:00:00+00:00",
            "runtimeSource": {
                "kind": "spark_researcher",
            },
            "contradictions": [
                {
                    "id": "contradiction:1",
                    "targetType": "upgrade",
                    "targetId": "upgrade:1",
                    "severity": "warn",
                    "summary": "regressed",
                    "sourceRef": "train.log",
                    "createdAt": "2026-03-27T10:00:00+00:00",
                }
            ],
        }

        changed = _normalize_collective_payload(payload)

        self.assertTrue(changed)
        self.assertEqual(payload["contradictions"][0]["status"], "open")
        self.assertEqual(payload["runtimeSource"]["sourceInstanceId"], "agent:spark-researcher")

    def test_normalize_collective_payload_preserves_existing_contradiction_status(self) -> None:
        payload = {
            "agentId": "agent:spark-researcher",
            "emittedAt": "2026-03-27T10:00:00+00:00",
            "runtimeSource": {
                "kind": "spark_researcher",
                "sourceInstanceId": "agent:spark-researcher",
                "sourceRunId": "spark-researcher:2026-03-27T10:00:00+00:00",
            },
            "contradictions": [
                {
                    "id": "contradiction:1",
                    "targetType": "upgrade",
                    "targetId": "upgrade:1",
                    "severity": "warn",
                    "status": "resolved",
                    "summary": "regressed",
                    "sourceRef": "train.log",
                    "createdAt": "2026-03-27T10:00:00+00:00",
                }
            ],
        }

        changed = _normalize_collective_payload(payload)

        self.assertFalse(changed)
        self.assertEqual(payload["contradictions"][0]["status"], "resolved")

    def test_evaluate_swarm_escalation_respects_disabled_auto_recommend_policy(self) -> None:
        self.config_manager.set_path("spark.swarm.routing.auto_recommend_enabled", False)

        result = evaluate_swarm_escalation(
            config_manager=self.config_manager,
            state_db=self.state_db,
            task="Please delegate this as parallel multi-agent work and research deeply.",
        )

        self.assertTrue(result.ok)
        self.assertFalse(result.escalate)
        self.assertEqual(result.mode, "hold_local")
        self.assertIn("explicit_swarm", result.triggers)
        self.assertIn("parallel_work", result.triggers)

    def test_evaluate_swarm_escalation_respects_custom_long_task_threshold(self) -> None:
        self.config_manager.set_path("spark.swarm.routing.long_task_word_count", 3)

        result = evaluate_swarm_escalation(
            config_manager=self.config_manager,
            state_db=self.state_db,
            task="one two three",
        )

        self.assertTrue(result.ok)
        self.assertTrue(result.escalate)
        self.assertEqual(result.mode, "manual_recommended")
        self.assertIn("long_task", result.triggers)

    def test_evaluate_swarm_escalation_records_typed_decision_and_attachment_provenance(self) -> None:
        self.config_manager.set_path("spark.chips.active_keys", ["startup-yc", "quality-gate"])
        self.config_manager.set_path("spark.specialization_paths.active_path_key", "startup-operator")

        result = evaluate_swarm_escalation(
            config_manager=self.config_manager,
            state_db=self.state_db,
            task="delegate this as parallel work",
            request_id="req-swarm-decision",
            channel_id="telegram",
            human_id="human:test",
            agent_id="agent:test",
        )

        self.assertIn(result.mode, {"manual_recommended", "unavailable", "hold_local"})
        decision_events = latest_events_by_type(self.state_db, event_type="tool_result_received", limit=20)
        decision_events = [
            event
            for event in decision_events
            if str(event.get("component") or "") == "swarm_bridge"
            and str((event.get("facts_json") or {}).get("swarm_operation") or "") == "decision"
        ]
        self.assertTrue(decision_events)
        influence_events = latest_events_by_type(self.state_db, event_type="plugin_or_chip_influence_recorded", limit=20)
        influence_events = [
            event
            for event in influence_events
            if str(event.get("component") or "") == "swarm_bridge"
        ]
        self.assertTrue(influence_events)
        self.assertEqual(influence_events[0]["facts_json"]["keepability"], "ephemeral_context")

    def test_record_swarm_failure_state_persists_http_error_response_body(self) -> None:
        _record_swarm_failure_state(
            self.state_db,
            kind="sync",
            result=SwarmSyncResult(
                ok=False,
                mode="http_error",
                message="Swarm API rejected the sync with HTTP 401.",
                payload_path="payload.json",
                api_url="https://sparkswarm.ai",
                workspace_id="ws_123",
                accepted=False,
                response_body={"error": "authentication_required"},
            ),
        )

        with self.state_db.connect() as conn:
            row = conn.execute(
                "SELECT value FROM runtime_state WHERE state_key = 'swarm:last_failure' LIMIT 1"
            ).fetchone()

        self.assertIsNotNone(row)
        payload = json.loads(str(row["value"]))
        self.assertEqual(payload["mode"], "http_error")
        self.assertEqual(payload["response_body"]["error"], "authentication_required")

    def test_record_swarm_sync_state_clears_last_failure_on_success(self) -> None:
        _record_swarm_failure_state(
            self.state_db,
            kind="sync",
            result=SwarmSyncResult(
                ok=False,
                mode="http_error",
                message="Swarm API rejected the sync with HTTP 500.",
                payload_path="payload.json",
                api_url="https://sparkswarm.ai",
                workspace_id="ws_123",
                accepted=False,
                response_body={"error": "collective_sync_failed"},
            ),
        )

        _record_swarm_sync_state(
            self.state_db,
            mode="uploaded",
            payload_path="payload.json",
            api_url="https://sparkswarm.ai",
            workspace_id="ws_123",
            accepted=True,
        )

        with self.state_db.connect() as conn:
            row = conn.execute(
                "SELECT value FROM runtime_state WHERE state_key = 'swarm:last_failure' LIMIT 1"
            ).fetchone()

        self.assertIsNone(row)

    def test_swarm_status_marks_expired_token_refreshable_when_refresh_path_exists(self) -> None:
        self.config_manager.set_path("spark.swarm.api_url", "https://api-production-6ea6.up.railway.app")
        self.config_manager.set_path("spark.swarm.workspace_id", "ws_123")
        self.config_manager.set_path("spark.swarm.auth_client_key_env", "SPARK_SWARM_AUTH_CLIENT_KEY")
        self.config_manager.upsert_env_secret("SPARK_SWARM_ACCESS_TOKEN", self._make_jwt(expires_in_seconds=-300))
        self.config_manager.upsert_env_secret("SPARK_SWARM_REFRESH_TOKEN", "refresh-token")
        self.config_manager.upsert_env_secret("SPARK_SWARM_AUTH_CLIENT_KEY", "anon-key")

        status = swarm_status(self.config_manager, self.state_db)

        self.assertEqual(status.auth_state, "refreshable")
        self.assertEqual(status.refresh_token_env, "SPARK_SWARM_REFRESH_TOKEN")
        self.assertEqual(status.auth_client_key_env, "SPARK_SWARM_AUTH_CLIENT_KEY")
        self.assertTrue(status.access_token_expires_at)

    def test_sync_refreshes_expired_access_token_before_upload(self) -> None:
        researcher_root = self.home / "spark-researcher"
        researcher_root.mkdir()
        researcher_config = researcher_root / "spark-researcher.project.json"
        researcher_config.write_text("{}", encoding="utf-8")
        self.config_manager.set_path("spark.researcher.enabled", True)
        self.config_manager.set_path("spark.researcher.runtime_root", str(researcher_root))
        self.config_manager.set_path("spark.researcher.config_path", str(researcher_config))
        self.config_manager.set_path("spark.swarm.api_url", "https://api-production-6ea6.up.railway.app")
        self.config_manager.set_path("spark.swarm.workspace_id", "ws_123")
        self.config_manager.upsert_env_secret("SPARK_SWARM_ACCESS_TOKEN", self._make_jwt(expires_in_seconds=-300))
        self.config_manager.upsert_env_secret("SPARK_SWARM_REFRESH_TOKEN", "refresh-token")
        self.config_manager.upsert_env_secret("SPARK_SWARM_AUTH_CLIENT_KEY", "anon-key")

        with patch(
            "spark_intelligence.swarm_bridge.sync._researcher_has_ledger",
            return_value=True,
        ), patch(
            "spark_intelligence.swarm_bridge.sync._build_collective_payload",
            return_value=({"agentId": "agent:spark-researcher"}, self.home / "payload.json"),
        ), patch(
            "spark_intelligence.swarm_bridge.sync._refresh_swarm_access_token",
        ) as refresh_mock, patch(
            "spark_intelligence.swarm_bridge.sync._post_collective_payload",
            return_value={"accepted": True},
        ) as post_mock:
            refresh_mock.return_value = type(
                "Session",
                (),
                {
                    "access_token": "fresh-access",
                    "refresh_token": "refresh-token-rotated",
                    "refresh_token_env": "SPARK_SWARM_REFRESH_TOKEN",
                    "auth_client_key": "anon-key",
                    "auth_client_key_env": "SPARK_SWARM_AUTH_CLIENT_KEY",
                    "supabase_url": "https://sfjcvvyvdwjdvphefggg.supabase.co",
                    "access_token_env": "SPARK_SWARM_ACCESS_TOKEN",
                    "auth_state": "configured",
                    "access_token_expires_at": None,
                },
            )()

            result = sync_swarm_collective(config_manager=self.config_manager, state_db=self.state_db, dry_run=False)

        self.assertTrue(result.ok)
        refresh_mock.assert_called_once()
        post_mock.assert_called_once()
        self.assertEqual(post_mock.call_args.kwargs["access_token"], "fresh-access")

    def test_sync_retries_once_after_authentication_required_with_refreshable_session(self) -> None:
        researcher_root = self.home / "spark-researcher"
        researcher_root.mkdir()
        researcher_config = researcher_root / "spark-researcher.project.json"
        researcher_config.write_text("{}", encoding="utf-8")
        self.config_manager.set_path("spark.researcher.enabled", True)
        self.config_manager.set_path("spark.researcher.runtime_root", str(researcher_root))
        self.config_manager.set_path("spark.researcher.config_path", str(researcher_config))
        self.config_manager.set_path("spark.swarm.api_url", "https://api-production-6ea6.up.railway.app")
        self.config_manager.set_path("spark.swarm.workspace_id", "ws_123")
        self.config_manager.upsert_env_secret("SPARK_SWARM_ACCESS_TOKEN", self._make_jwt(expires_in_seconds=3600))
        self.config_manager.upsert_env_secret("SPARK_SWARM_REFRESH_TOKEN", "refresh-token")
        self.config_manager.upsert_env_secret("SPARK_SWARM_AUTH_CLIENT_KEY", "anon-key")

        auth_error = HTTPError(
            url="https://api-production-6ea6.up.railway.app/api/workspaces/ws_123/collective/sync",
            code=401,
            msg="Unauthorized",
            hdrs=None,
            fp=io.BytesIO(b'{"error":"authentication_required"}'),
        )
        refreshed_session = type(
            "Session",
            (),
            {
                "access_token": "fresh-access",
                "refresh_token": "refresh-token-rotated",
                "refresh_token_env": "SPARK_SWARM_REFRESH_TOKEN",
                "auth_client_key": "anon-key",
                "auth_client_key_env": "SPARK_SWARM_AUTH_CLIENT_KEY",
                "supabase_url": "https://sfjcvvyvdwjdvphefggg.supabase.co",
                "access_token_env": "SPARK_SWARM_ACCESS_TOKEN",
                "auth_state": "configured",
                "access_token_expires_at": None,
            },
        )()

        with patch(
            "spark_intelligence.swarm_bridge.sync._researcher_has_ledger",
            return_value=True,
        ), patch(
            "spark_intelligence.swarm_bridge.sync._build_collective_payload",
            return_value=({"agentId": "agent:spark-researcher"}, self.home / "payload.json"),
        ), patch(
            "spark_intelligence.swarm_bridge.sync._post_collective_payload",
            side_effect=[auth_error, {"accepted": True}],
        ) as post_mock, patch(
            "spark_intelligence.swarm_bridge.sync._refresh_swarm_access_token",
            return_value=refreshed_session,
        ) as refresh_mock:
            result = sync_swarm_collective(config_manager=self.config_manager, state_db=self.state_db, dry_run=False)

        self.assertTrue(result.ok)
        self.assertIn("after refreshing the session", result.message)
        self.assertEqual(post_mock.call_count, 2)
        self.assertEqual(post_mock.call_args.kwargs["access_token"], "fresh-access")
        refresh_mock.assert_called_once()
