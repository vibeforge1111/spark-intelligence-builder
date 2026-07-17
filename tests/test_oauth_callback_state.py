from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from unittest.mock import patch

import spark_intelligence.auth.oauth_state as oauth_state_module
from spark_intelligence.auth.oauth_state import (
    OAuthCallbackStateRecord,
    consume_oauth_callback_state,
    expire_stale_oauth_callback_states,
    issue_oauth_callback_state,
)

from tests.test_support import SparkTestCase


class OAuthCallbackStateTests(SparkTestCase):
    def test_issue_and_consume_oauth_callback_state(self) -> None:
        issued = issue_oauth_callback_state(
            state_db=self.state_db,
            provider_id="openai",
            auth_profile_id="openai:default",
            flow_kind="oauth_login",
            redirect_uri="http://127.0.0.1:1455/auth/callback",
            expected_issuer="https://auth.openai.com",
            pkce_verifier="verifier-123",
        )

        self.assertEqual(issued.provider_id, "openai")
        self.assertEqual(issued.status, "pending")
        self.assertEqual(issued.auth_profile_id, "openai:default")
        self.assertIsNone(issued.consumed_at)

        consumed = consume_oauth_callback_state(
            state_db=self.state_db,
            provider_id="openai",
            oauth_state=issued.oauth_state,
            redirect_uri="http://127.0.0.1:1455/auth/callback",
            expected_issuer="https://auth.openai.com",
        )

        self.assertEqual(consumed.callback_id, issued.callback_id)
        self.assertEqual(consumed.status, "consumed")
        self.assertIsNotNone(consumed.consumed_at)

    def test_concurrent_consume_allows_exactly_one_callback_exchange(self) -> None:
        issued = issue_oauth_callback_state(
            state_db=self.state_db,
            provider_id="openai",
            auth_profile_id="openai:default",
            flow_kind="oauth_login",
            redirect_uri="http://127.0.0.1:1455/auth/callback",
            expected_issuer="https://auth.openai.com",
            pkce_verifier="verifier-123",
        )
        both_read_pending = Barrier(2)
        original_row_to_record = oauth_state_module._row_to_record

        def synchronize_after_pending_read(row: object) -> OAuthCallbackStateRecord:
            record = original_row_to_record(row)
            if record.oauth_state == issued.oauth_state and record.status == "pending":
                both_read_pending.wait(timeout=5)
            return record

        def consume() -> OAuthCallbackStateRecord | Exception:
            try:
                return consume_oauth_callback_state(
                    state_db=self.state_db,
                    provider_id="openai",
                    oauth_state=issued.oauth_state,
                    redirect_uri="http://127.0.0.1:1455/auth/callback",
                    expected_issuer="https://auth.openai.com",
                )
            except Exception as exc:
                return exc

        with patch.object(
            oauth_state_module,
            "_row_to_record",
            side_effect=synchronize_after_pending_read,
        ), ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(consume) for _ in range(2)]
            results = [future.result(timeout=15) for future in futures]

        successes = [result for result in results if isinstance(result, OAuthCallbackStateRecord)]
        failures = [result for result in results if isinstance(result, Exception)]
        self.assertEqual(len(successes), 1)
        self.assertEqual(successes[0].status, "consumed")
        self.assertEqual(len(failures), 1)
        self.assertIsInstance(failures[0], ValueError)
        self.assertIn("already consumed", str(failures[0]))

    def test_consume_rejects_provider_mismatch(self) -> None:
        issued = issue_oauth_callback_state(
            state_db=self.state_db,
            provider_id="openai",
            auth_profile_id=None,
            flow_kind="oauth_login",
            redirect_uri=None,
            expected_issuer=None,
            pkce_verifier=None,
        )

        with self.assertRaisesRegex(ValueError, "provider mismatch"):
            consume_oauth_callback_state(
                state_db=self.state_db,
                provider_id="anthropic",
                oauth_state=issued.oauth_state,
            )

    def test_expired_state_is_marked_and_rejected(self) -> None:
        issued = issue_oauth_callback_state(
            state_db=self.state_db,
            provider_id="openai",
            auth_profile_id=None,
            flow_kind="oauth_login",
            redirect_uri=None,
            expected_issuer=None,
            pkce_verifier=None,
            ttl_seconds=-1,
        )

        with self.assertRaisesRegex(ValueError, "expired"):
            consume_oauth_callback_state(
                state_db=self.state_db,
                provider_id="openai",
                oauth_state=issued.oauth_state,
            )

        with self.state_db.connect() as conn:
            row = conn.execute(
                "SELECT status FROM oauth_callback_states WHERE callback_id = ? LIMIT 1",
                (issued.callback_id,),
            ).fetchone()
        self.assertEqual(row["status"], "expired")

    def test_expire_stale_callback_states_marks_pending_rows(self) -> None:
        issued = issue_oauth_callback_state(
            state_db=self.state_db,
            provider_id="openai",
            auth_profile_id=None,
            flow_kind="oauth_login",
            redirect_uri=None,
            expected_issuer=None,
            pkce_verifier=None,
            ttl_seconds=60,
        )

        expired = expire_stale_oauth_callback_states(
            state_db=self.state_db,
            now=datetime.now(UTC) + timedelta(minutes=5),
        )

        self.assertEqual(expired, 1)
        with self.state_db.connect() as conn:
            row = conn.execute(
                "SELECT status FROM oauth_callback_states WHERE callback_id = ? LIMIT 1",
                (issued.callback_id,),
            ).fetchone()
        self.assertEqual(row["status"], "expired")
