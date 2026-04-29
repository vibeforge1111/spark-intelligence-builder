from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from spark_intelligence.memory.test_batch_runner import main as run_memory_test_batch
from spark_intelligence.memory.test_batches import (
    get_memory_test_batch,
    list_memory_test_batches,
    memory_test_batch_command,
    missing_memory_test_targets,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_memory_test_batches_cover_current_architecture_tracks() -> None:
    batches = {batch.batch_id: batch for batch in list_memory_test_batches()}

    assert "fast-contract" in batches
    assert "telegram-memory-unit" in batches
    assert "architecture-promotion" in batches
    assert "diagnostics-ledgers" in batches
    assert "full-memory-local" in batches

    fast_targets = set(batches["fast-contract"].pytest_targets)
    assert "tests/test_session_summaries.py" in fast_targets
    assert "tests/test_memory_orchestrator.py" in fast_targets
    assert "tests/test_context_capsule.py" in fast_targets
    assert "episodic_summary" in batches["fast-contract"].covered_tracks

    telegram_targets = set(batches["telegram-memory-unit"].pytest_targets)
    assert "tests/test_telegram_generic_memory.py" in telegram_targets
    assert "stale_current_conflict" in batches["telegram-memory-unit"].covered_tracks


def test_memory_test_batch_targets_exist() -> None:
    for batch in list_memory_test_batches():
        assert missing_memory_test_targets(batch.batch_id, repo_root=REPO_ROOT) == ()


def test_memory_test_batch_command_builds_pytest_invocation() -> None:
    command = memory_test_batch_command(
        "fast-contract",
        python_executable="python",
        extra_args=("--maxfail=1",),
    )

    assert command[:3] == ("python", "-m", "pytest")
    assert "-q" in command
    assert "tests/test_session_summaries.py" in command
    assert command[-1] == "--maxfail=1"


def test_memory_test_batch_runner_print_only_does_not_execute(capsys) -> None:
    with patch("spark_intelligence.memory.test_batch_runner.pytest.main") as run:
        exit_code = run_memory_test_batch(
            ["--batch", "fast-contract", "--print-only", "--repo-root", str(REPO_ROOT), "--", "--maxfail=1"]
        )

    assert exit_code == 0
    assert not run.called
    stdout = capsys.readouterr().out
    assert "-m pytest" in stdout
    assert "tests/test_session_summaries.py" in stdout
    assert "-- --maxfail=1" not in stdout
    assert "--maxfail=1" in stdout


def test_memory_test_batch_runner_fails_on_missing_target(tmp_path, capsys) -> None:
    exit_code = run_memory_test_batch(["--batch", "fast-contract", "--repo-root", str(tmp_path)])

    assert exit_code == 2
    stderr = capsys.readouterr().err
    assert "missing pytest targets" in stderr


def test_unknown_memory_test_batch_names_known_batches() -> None:
    try:
        get_memory_test_batch("nope")
    except ValueError as exc:
        message = str(exc)
    else:
        raise AssertionError("unknown batch should raise ValueError")

    assert "unknown_memory_test_batch:nope" in message
    assert "fast-contract" in message
