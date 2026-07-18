from __future__ import annotations

import json
import os
import stat
from unittest.mock import patch

from spark_intelligence.auth.runtime import resolve_runtime_provider
from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.state.db import StateDB

from tests.test_support import SparkTestCase


_ENVELOPE_PREFIX = "spark.oauth.v1:"
_KEY_FILE_NAME = ".spark-oauth-token.key"


class OAuthTokenAtRestAuthorityTests(SparkTestCase):
    def _complete_login(
        self,
        *,
        access_token: str = "oauth-access-secret",
        refresh_token: str = "oauth-refresh-secret",
    ) -> None:
        start_exit, start_stdout, start_stderr = self.run_cli(
            "auth",
            "login",
            "openai-codex",
            "--home",
            str(self.home),
            "--json",
        )
        assert start_exit == 0, start_stderr
        callback_state = json.loads(start_stdout)["callback_state"]
        callback_url = (
            "http://127.0.0.1:1455/auth/callback"
            f"?state={callback_state}&code=test-oauth-code"
        )
        with patch(
            "spark_intelligence.auth.service.exchange_oauth_authorization_code",
            return_value={
                "access_token": access_token,
                "refresh_token": refresh_token,
                "scope": "openid profile",
                "expires_in": 3600,
                "refresh_token_expires_in": 7200,
            },
        ):
            exit_code, _, stderr = self.run_cli(
                "auth",
                "login",
                "openai-codex",
                "--home",
                str(self.home),
                "--callback-url",
                callback_url,
                "--json",
            )
        assert exit_code == 0, stderr

    def _raw_tokens(self) -> tuple[str, str]:
        with self.state_db.connect() as conn:
            row = conn.execute(
                """
                SELECT access_token_ciphertext, refresh_token_ciphertext
                FROM oauth_credentials
                WHERE auth_profile_id = 'openai-codex:default'
                """
            ).fetchone()
        assert row is not None
        return str(row["access_token_ciphertext"]), str(row["refresh_token_ciphertext"])

    def test_login_persists_versioned_ciphertext_and_survives_restart(self) -> None:
        self._complete_login()

        access_value, refresh_value = self._raw_tokens()
        assert access_value.startswith(_ENVELOPE_PREFIX)
        assert refresh_value.startswith(_ENVELOPE_PREFIX)
        assert "oauth-access-secret" not in access_value
        assert "oauth-refresh-secret" not in refresh_value

        key_path = self.home / _KEY_FILE_NAME
        assert key_path.is_file()
        assert not key_path.is_symlink()
        if os.name != "nt":
            assert stat.S_IMODE(key_path.stat().st_mode) == 0o600

        restarted_config = ConfigManager.from_home(str(self.home))
        restarted_state = StateDB(restarted_config.paths.state_db)
        restarted_state.initialize()
        resolution = resolve_runtime_provider(
            config_manager=restarted_config,
            state_db=restarted_state,
        )
        assert resolution.secret_value == "oauth-access-secret"

    def test_refresh_rotation_reencrypts_both_tokens(self) -> None:
        self._complete_login()
        before = self._raw_tokens()

        with patch(
            "spark_intelligence.auth.service.exchange_oauth_refresh_token",
            return_value={
                "access_token": "oauth-access-rotated",
                "refresh_token": "oauth-refresh-rotated",
                "expires_in": 3600,
                "refresh_token_expires_in": 7200,
            },
        ):
            exit_code, _, stderr = self.run_cli(
                "auth",
                "refresh",
                "openai-codex",
                "--home",
                str(self.home),
                "--json",
            )

        assert exit_code == 0, stderr
        after = self._raw_tokens()
        assert after != before
        assert all(value.startswith(_ENVELOPE_PREFIX) for value in after)
        assert all("rotated" not in value for value in after)
        assert resolve_runtime_provider(
            config_manager=self.config_manager,
            state_db=self.state_db,
        ).secret_value == "oauth-access-rotated"

    def test_restart_atomically_migrates_legacy_plaintext_rows(self) -> None:
        self._complete_login()
        key_path = self.home / _KEY_FILE_NAME
        key_path.unlink(missing_ok=True)
        with self.state_db.connect() as conn:
            conn.execute(
                """
                UPDATE oauth_credentials
                SET access_token_ciphertext = ?, refresh_token_ciphertext = ?
                WHERE auth_profile_id = 'openai-codex:default'
                """,
                ("legacy-access-secret", "legacy-refresh-secret"),
            )
            conn.commit()

        StateDB(self.config_manager.paths.state_db).initialize()

        access_value, refresh_value = self._raw_tokens()
        assert access_value.startswith(_ENVELOPE_PREFIX)
        assert refresh_value.startswith(_ENVELOPE_PREFIX)
        assert "legacy" not in access_value
        assert "legacy" not in refresh_value
        assert resolve_runtime_provider(
            config_manager=self.config_manager,
            state_db=self.state_db,
        ).secret_value == "legacy-access-secret"

    def test_encrypted_rows_fail_closed_when_key_is_missing(self) -> None:
        self._complete_login()
        (self.home / _KEY_FILE_NAME).unlink(missing_ok=True)

        with self.assertRaisesRegex(
            RuntimeError,
            "OAuth token encryption key is missing",
        ) as raised:
            StateDB(self.config_manager.paths.state_db).initialize()

        assert raised.exception.__cause__ is None

    def test_encrypted_rows_fail_closed_when_key_is_wrong(self) -> None:
        self._complete_login()
        key_path = self.home / _KEY_FILE_NAME
        key_path.write_bytes(b"x" * 32)
        if os.name != "nt":
            key_path.chmod(0o600)

        with self.assertRaisesRegex(
            RuntimeError,
            "OAuth token store could not be decrypted safely",
        ) as raised:
            StateDB(self.config_manager.paths.state_db).initialize()

        assert raised.exception.__cause__ is None

    def test_tampered_ciphertext_never_returns_token_or_decoder_lineage(self) -> None:
        self._complete_login()
        access_value, _ = self._raw_tokens()
        tampered = access_value[:-1] + ("A" if access_value[-1] != "A" else "B")
        with self.state_db.connect() as conn:
            conn.execute(
                """
                UPDATE oauth_credentials
                SET access_token_ciphertext = ?
                WHERE auth_profile_id = 'openai-codex:default'
                """,
                (tampered,),
            )
            conn.commit()

        with self.assertRaisesRegex(
            RuntimeError,
            "OAuth token store could not be decrypted safely",
        ) as raised:
            resolve_runtime_provider(
                config_manager=self.config_manager,
                state_db=self.state_db,
            )

        rendered = str(raised.exception)
        assert "oauth-access-secret" not in rendered
        assert tampered not in rendered
        assert raised.exception.__cause__ is None

