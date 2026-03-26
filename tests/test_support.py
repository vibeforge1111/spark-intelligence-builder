from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from spark_intelligence.channel.service import add_channel
from spark_intelligence.cli import main
from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.state.db import StateDB


def make_telegram_update(
    *,
    update_id: int,
    user_id: str,
    text: str,
    username: str | None = None,
    chat_id: str | None = None,
    chat_type: str = "private",
) -> dict[str, object]:
    resolved_chat_id = chat_id or user_id
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id * 10,
            "text": text,
            "chat": {
                "id": resolved_chat_id,
                "type": chat_type,
            },
            "from": {
                "id": user_id,
                "username": username,
            },
        },
    }


class SparkTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.home = Path(self._tempdir.name)
        self.config_manager = ConfigManager.from_home(str(self.home))
        self.config_manager.bootstrap()
        self.state_db = StateDB(self.config_manager.paths.state_db)
        self.state_db.initialize()
        self.config_manager.set_path("spark.researcher.enabled", False)

    def tearDown(self) -> None:
        self._tempdir.cleanup()

    def add_telegram_channel(self, *, pairing_mode: str = "pairing", allowed_users: list[str] | None = None) -> None:
        add_channel(
            config_manager=self.config_manager,
            state_db=self.state_db,
            channel_kind="telegram",
            bot_token=None,
            allowed_users=allowed_users or [],
            pairing_mode=pairing_mode,
        )

    def run_cli(self, *argv: str) -> tuple[int, str, str]:
        stdout_buffer = io.StringIO()
        stderr_buffer = io.StringIO()
        with redirect_stdout(stdout_buffer), redirect_stderr(stderr_buffer):
            exit_code = main(list(argv))
        return exit_code, stdout_buffer.getvalue(), stderr_buffer.getvalue()

    def read_json(self, text: str) -> dict[str, object]:
        return json.loads(text)
