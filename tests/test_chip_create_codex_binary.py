from types import SimpleNamespace

import pytest

from spark_intelligence.chip_create import pipeline


@pytest.mark.parametrize("configured", [None, "C:/Spark Tools/codex.exe"])
def test_brief_dispatch_uses_configured_binary_or_path(monkeypatch, configured):
    if configured is None:
        monkeypatch.delenv("CODEX_BIN", raising=False)
    else:
        monkeypatch.setenv("CODEX_BIN", configured)
    resolved = configured or "/usr/bin/codex"
    looked_up = []

    def which(name):
        looked_up.append(name)
        return resolved if name == (configured or "codex") else None

    monkeypatch.setattr(pipeline.shutil, "which", which)
    monkeypatch.setattr(pipeline, "_screen_chip_create_prompt", lambda **kwargs: None)
    captured = []

    def run(**kwargs):
        captured.append(kwargs)
        return SimpleNamespace(exit_code=1, stdout="", stderr="")

    monkeypatch.setattr(pipeline, "run_governed_command", run)
    with pytest.raises(pipeline.ChipCreateProviderExecutionError, match="codex_cli_nonzero_exit"):
        pipeline._parse_brief_via_codex_cli("test brief", provider=SimpleNamespace(default_model=""))
    assert looked_up == [configured or "codex"]
    assert captured[0]["command"][:2] == [resolved, "exec"]
    assert captured[0]["command"][captured[0]["command"].index("--sandbox") + 1] == "read-only"


def test_invalid_explicit_binary_does_not_fall_back_or_dispatch(monkeypatch):
    monkeypatch.setenv("CODEX_BIN", "C:/missing/codex.exe")
    looked_up = []

    def which(name):
        looked_up.append(name)
        return "/usr/bin/codex" if name == "codex" else None

    monkeypatch.setattr(pipeline.shutil, "which", which)
    monkeypatch.setattr(pipeline, "run_governed_command", lambda **kwargs: pytest.fail("must not dispatch"))
    with pytest.raises(pipeline.ChipCreateProviderExecutionError, match="codex_cli_missing"):
        pipeline._parse_brief_via_codex_cli("test brief", provider=SimpleNamespace(default_model=""))
    assert looked_up == ["C:/missing/codex.exe"]
