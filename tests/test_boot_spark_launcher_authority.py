from __future__ import annotations

from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "boot-spark.sh"


def _script_text() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_launcher_does_not_depend_on_a_literal_windows_user_desktop() -> None:
    text = _script_text()

    assert "/c/Users/USER/Desktop" not in text
    assert 'BUILDER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.."' in text


def test_launcher_uses_the_canonical_spawner_repository_name() -> None:
    text = _script_text()

    assert "vibeship-spawner-ui" in text
    assert 'SPARK_SPAWNER_DIR:-' in text
    assert 'SPARK_TELEGRAM_BOT_DIR:-' in text


def test_telegram_poller_launch_is_explicit_opt_in() -> None:
    text = _script_text()

    assert "WITH_BOT=0" in text
    assert "--with-bot) WITH_BOT=1" in text
    assert "skipped (default; pass --with-bot to request a local launch)" in text
