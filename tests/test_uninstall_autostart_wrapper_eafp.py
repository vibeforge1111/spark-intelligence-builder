from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from spark_intelligence.cli import handle_uninstall_autostart


def test_unlink_missing_ok_on_already_removed_wrapper_is_noop(tmp_path: Path) -> None:
    """Race scenario unit test: the Windows startup wrapper file was already removed by an
    external uninstaller / antivirus quarantine / parallel `spark-intelligence cli
    uninstall-autostart`. Pre-patch the exists()-then-unlink LBYL pair raised
    FileNotFoundError if a parallel cleanup won between the two syscalls. Post-patch
    unlink(missing_ok=True) is atomic and a no-op on a missing path.

    Driven directly against pathlib.Path -- we don't drive the end-to-end Windows path
    construction on Linux (handle_uninstall_autostart short-circuits on os.name != 'nt',
    and Path() instantiates as PosixPath here; the unit assertion is that the idiom the
    fix introduces behaves the same on both POSIX and Windows pathlib backends).
    """
    target = tmp_path / "Spark Intelligence Gateway.cmd"
    assert not target.exists()
    target.unlink(missing_ok=True)  # post-patch idiom
    assert not target.exists()


def test_unlink_missing_ok_on_present_wrapper_removes_it(tmp_path: Path) -> None:
    """Happy-path unit test: when the wrapper exists, unlink(missing_ok=True) removes it
    (byte-identical with the pre-patch unlink())."""
    target = tmp_path / "Spark Intelligence Gateway.cmd"
    target.write_text("@echo off\nspark-intelligence start\n", encoding="utf-8")
    assert target.exists()
    target.unlink(missing_ok=True)
    assert not target.exists()


def test_handle_uninstall_autostart_non_windows_returns_one() -> None:
    """Regression: on non-Windows, handle_uninstall_autostart prints the unsupported
    message and returns 1 without touching the autostart code path that the fix lives in.
    The fix only changes the body of the windows_startup_folder branch and does not alter
    this early-out."""
    args = argparse.Namespace(home=None, task_name=None)
    rc = handle_uninstall_autostart(args)
    assert rc == 1
