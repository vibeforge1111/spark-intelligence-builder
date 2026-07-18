from __future__ import annotations

import os
from pathlib import Path
import subprocess


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


def test_plan_resolves_explicit_paths_without_creating_log_directory(
    tmp_path: Path,
) -> None:
    builder = tmp_path / "builder"
    spawner = tmp_path / "spawner"
    telegram = tmp_path / "telegram"
    logs = tmp_path / "logs"
    env = {
        **os.environ,
        "SPARK_BUILDER_DIR": str(builder),
        "SPARK_SPAWNER_DIR": str(spawner),
        "SPARK_TELEGRAM_BOT_DIR": str(telegram),
        "SPARK_BOOT_LOG_DIR": str(logs),
    }

    result = subprocess.run(
        ["bash", str(SCRIPT), "--plan"],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )

    assert f"Builder: {builder}" in result.stdout
    assert f"Spawner: {spawner}" in result.stdout
    assert f"Telegram: {telegram}" in result.stdout
    assert "Telegram launch: skipped (default" in result.stdout
    assert not logs.exists()


def test_plan_records_explicit_telegram_request_without_launching(
    tmp_path: Path,
) -> None:
    logs = tmp_path / "logs"
    result = subprocess.run(
        ["bash", str(SCRIPT), "--with-bot", "--plan"],
        check=True,
        capture_output=True,
        text=True,
        env={**os.environ, "SPARK_BOOT_LOG_DIR": str(logs)},
    )

    assert "Telegram launch: explicitly requested" in result.stdout
    assert not logs.exists()


def test_status_is_read_only_even_when_log_path_does_not_exist(
    tmp_path: Path,
) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_curl = bin_dir / "curl"
    fake_curl.write_text("#!/usr/bin/env bash\nprintf '000'\n", encoding="utf-8")
    fake_curl.chmod(0o755)
    logs = tmp_path / "logs"

    result = subprocess.run(
        ["bash", str(SCRIPT), "--status"],
        check=True,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "SPARK_BOOT_LOG_DIR": str(logs),
        },
    )

    assert result.stdout.count("-> down") == 4
    assert not logs.exists()


def test_unknown_option_fails_without_side_effects(tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    result = subprocess.run(
        ["bash", str(SCRIPT), "--surprise"],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "SPARK_BOOT_LOG_DIR": str(logs)},
    )

    assert result.returncode == 2
    assert "Unknown option" in result.stderr
    assert not logs.exists()


def test_launcher_has_valid_bash_syntax() -> None:
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)
