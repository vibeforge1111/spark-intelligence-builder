from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from spark_intelligence.llm.provider_wrapper import main as provider_wrapper_main


def _wrapper_paths(tmp_path: Path) -> tuple[Path, Path, Path]:
    system_path = tmp_path / "system.txt"
    user_path = tmp_path / "user.txt"
    response_path = tmp_path / "response.json"
    system_path.write_text("System instructions", encoding="utf-8")
    user_path.write_text("User task", encoding="utf-8")
    return system_path, user_path, response_path


def _provider_env(secret: str) -> dict[str, str]:
    return {
        "SPARK_INTELLIGENCE_PROVIDER_ID": "custom",
        "SPARK_INTELLIGENCE_PROVIDER_KIND": "custom",
        "SPARK_INTELLIGENCE_PROVIDER_AUTH_METHOD": "api_key_env",
        "SPARK_INTELLIGENCE_PROVIDER_API_MODE": "chat_completions",
        "SPARK_INTELLIGENCE_PROVIDER_BASE_URL": "https://api.example.com/v1",
        "SPARK_INTELLIGENCE_PROVIDER_MODEL": "model",
        "SPARK_INTELLIGENCE_PROVIDER_SECRET": secret,
    }


@pytest.mark.parametrize(
    "secret",
    [
        "changeme",
        "REPLACE-ME",
        "your_key_here",
        "<YOUR SECRET HERE>",
        "sk-placeholder",
        "dummy",
        "xxxx",
    ],
)
def test_wrapper_rejects_known_placeholder_secret_before_provider_execution(
    tmp_path: Path,
    secret: str,
) -> None:
    system_path, user_path, response_path = _wrapper_paths(tmp_path)

    with patch.dict(os.environ, _provider_env(secret), clear=False), patch(
        "spark_intelligence.llm.provider_wrapper.execute_direct_provider_prompt",
        return_value={"ok": True},
    ) as execute:
        with pytest.raises(RuntimeError, match="placeholder provider credential") as raised:
            provider_wrapper_main([str(system_path), str(user_path), str(response_path)])

    execute.assert_not_called()
    assert secret not in str(raised.value)
    assert not response_path.exists()


def test_wrapper_accepts_real_secret_containing_placeholder_words(tmp_path: Path) -> None:
    system_path, user_path, response_path = _wrapper_paths(tmp_path)
    secret = "prod-example-test-fake-token-7f03c8d89e"

    with patch.dict(os.environ, _provider_env(secret), clear=False), patch(
        "spark_intelligence.llm.provider_wrapper.execute_direct_provider_prompt",
        return_value={"ok": True},
    ) as execute:
        assert provider_wrapper_main([str(system_path), str(user_path), str(response_path)]) == 0

    assert execute.call_args.kwargs["provider"].secret_value == secret
    assert response_path.exists()
