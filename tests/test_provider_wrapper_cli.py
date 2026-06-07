"""Coverage for argv-arity + required-env preconditions in provider_wrapper.

The wrapper's ``main`` is the entrypoint for ``python -m
spark_intelligence.llm.provider_wrapper``, but its argv length check and the
``_required_env`` validator are only exercised today through the integration
path that supplies a full env and three valid paths. These tests lock the
guard rails so a future refactor cannot silently strip the usage message or
the explicit env-var error.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from spark_intelligence.llm import provider_wrapper


_REQUIRED_ENV_VARS = (
    "SPARK_INTELLIGENCE_PROVIDER_ID",
    "SPARK_INTELLIGENCE_PROVIDER_KIND",
    "SPARK_INTELLIGENCE_PROVIDER_AUTH_METHOD",
    "SPARK_INTELLIGENCE_PROVIDER_API_MODE",
    "SPARK_INTELLIGENCE_PROVIDER_BASE_URL",
    "SPARK_INTELLIGENCE_PROVIDER_MODEL",
    "SPARK_INTELLIGENCE_PROVIDER_SECRET",
)


@pytest.fixture(autouse=True)
def _scrub_provider_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _REQUIRED_ENV_VARS + (
        "SPARK_INTELLIGENCE_STATE_DB_PATH",
        "SPARK_INTELLIGENCE_RUN_ID",
        "SPARK_INTELLIGENCE_REQUEST_ID",
        "SPARK_INTELLIGENCE_TRACE_REF",
    ):
        monkeypatch.delenv(name, raising=False)


class TestMainArity:
    def test_too_few_arguments_returns_two(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = provider_wrapper.main(["only", "two"])
        assert rc == 2
        captured = capsys.readouterr()
        assert "Usage:" in captured.err

    def test_too_many_arguments_returns_two(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = provider_wrapper.main(["a", "b", "c", "d"])
        assert rc == 2
        assert "Usage:" in capsys.readouterr().err

    def test_empty_argv_returns_two(self, capsys: pytest.CaptureFixture[str]) -> None:
        rc = provider_wrapper.main([])
        assert rc == 2
        assert "provider_wrapper" in capsys.readouterr().err

    def test_default_argv_uses_sys_argv(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(sys, "argv", ["provider_wrapper.py", "only_one"])
        rc = provider_wrapper.main(None)
        assert rc == 2
        assert "Usage:" in capsys.readouterr().err


class TestRequiredEnv:
    def test_missing_env_var_raises_runtime_error(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Provide three valid file paths so arity check passes, but no env.
        system_path = tmp_path / "system.txt"
        user_path = tmp_path / "user.txt"
        response_path = tmp_path / "response.json"
        system_path.write_text("sys", encoding="utf-8")
        user_path.write_text("user", encoding="utf-8")

        with pytest.raises(RuntimeError, match="SPARK_INTELLIGENCE_PROVIDER_ID"):
            provider_wrapper.main(
                [str(system_path), str(user_path), str(response_path)]
            )

    def test_blank_env_var_treated_as_missing(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # All but PROVIDER_MODEL populated; the blank value should still trip the guard.
        for name in _REQUIRED_ENV_VARS:
            monkeypatch.setenv(name, "filled")
        monkeypatch.setenv("SPARK_INTELLIGENCE_PROVIDER_MODEL", "   ")

        system_path = tmp_path / "system.txt"
        user_path = tmp_path / "user.txt"
        response_path = tmp_path / "response.json"
        system_path.write_text("sys", encoding="utf-8")
        user_path.write_text("user", encoding="utf-8")

        with pytest.raises(RuntimeError, match="SPARK_INTELLIGENCE_PROVIDER_MODEL"):
            provider_wrapper.main(
                [str(system_path), str(user_path), str(response_path)]
            )

    def test_direct_helper_strips_whitespace(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PROBE_VAR", "  value  ")
        assert provider_wrapper._required_env("PROBE_VAR") == "value"

    def test_direct_helper_rejects_empty_string(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PROBE_VAR", "")
        with pytest.raises(RuntimeError, match="PROBE_VAR"):
            provider_wrapper._required_env("PROBE_VAR")


class TestGovernanceFromEnv:
    def test_governance_none_when_state_db_path_blank(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from spark_intelligence.llm.direct_provider import DirectProviderRequest

        provider = DirectProviderRequest(
            provider_id="custom",
            provider_kind="custom",
            auth_method="api_key_env",
            api_mode="chat_completions",
            base_url="https://example.com",
            model="m",
            secret_value="s",
        )
        # No SPARK_INTELLIGENCE_STATE_DB_PATH set in env (fixture scrubbed it).
        assert provider_wrapper._governance_from_env(provider) is None

    def test_governance_populated_when_state_db_path_present(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        from spark_intelligence.llm.direct_provider import DirectProviderRequest

        provider = DirectProviderRequest(
            provider_id="custom",
            provider_kind="custom",
            auth_method="api_key_env",
            api_mode="chat_completions",
            base_url="https://example.com",
            model="m",
            secret_value="s",
        )
        monkeypatch.setenv("SPARK_INTELLIGENCE_STATE_DB_PATH", str(tmp_path / "state.db"))
        monkeypatch.setenv("SPARK_INTELLIGENCE_RUN_ID", "run-1")
        gov = provider_wrapper._governance_from_env(provider)
        assert gov is not None
        assert gov.state_db_path == str(tmp_path / "state.db")
        assert gov.run_id == "run-1"
