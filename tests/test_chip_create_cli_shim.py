import os
import pytest
from pathlib import Path


def _make_cli_file(tmp_path: Path) -> Path:
    src_dir = tmp_path / "src" / "my_chip"
    src_dir.mkdir(parents=True)
    cli = src_dir / "cli.py"
    cli.write_text(
        "from ..lab_hooks import something\ndef main(): pass\n",
        encoding="utf-8",
    )
    return cli


def test_chip_labs_src_unset_raises_runtime_error(tmp_path, monkeypatch):
    monkeypatch.delenv("CHIP_LABS_SRC", raising=False)
    from spark_intelligence.chip_create.pipeline import _patch_generated_cli
    _make_cli_file(tmp_path)
    with pytest.raises(RuntimeError, match="CHIP_LABS_SRC"):
        _patch_generated_cli(tmp_path, Path("/fake/chip_labs_root"))


def test_chip_labs_src_set_no_hardcoded_path_leak(tmp_path, monkeypatch):
    fake_src = tmp_path / "chip_labs_src"
    fake_src.mkdir()
    monkeypatch.setenv("CHIP_LABS_SRC", str(fake_src))
    from spark_intelligence.chip_create.pipeline import _patch_generated_cli
    cli = _make_cli_file(tmp_path)
    _patch_generated_cli(tmp_path, Path("/fake/chip_labs_root"))
    patched = cli.read_text(encoding="utf-8")
    assert "CHIP_LABS_SRC" in patched
    assert "/fake/chip_labs_root" not in patched
    assert r"{chip_labs_src}" not in patched


def test_no_brace_template_leaks_into_shim(tmp_path, monkeypatch):
    fake_src = tmp_path / "chip_labs_src"
    fake_src.mkdir()
    monkeypatch.setenv("CHIP_LABS_SRC", str(fake_src))
    from spark_intelligence.chip_create.pipeline import _patch_generated_cli
    cli = _make_cli_file(tmp_path)
    _patch_generated_cli(tmp_path, Path("/fake"))
    patched = cli.read_text(encoding="utf-8")
    assert r"{chip_labs_src}" not in patched


def test_error_message_names_env_var(tmp_path, monkeypatch):
    monkeypatch.delenv("CHIP_LABS_SRC", raising=False)
    from spark_intelligence.chip_create.pipeline import _patch_generated_cli
    _make_cli_file(tmp_path)
    with pytest.raises(RuntimeError) as exc_info:
        _patch_generated_cli(tmp_path, Path("/fake"))
    assert "CHIP_LABS_SRC" in str(exc_info.value)