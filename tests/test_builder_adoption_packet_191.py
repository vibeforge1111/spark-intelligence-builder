from __future__ import annotations

import os
from pathlib import Path
import subprocess
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from spark_intelligence.gateway.runtime import gateway_start
from spark_intelligence.adapters.telegram.runtime import read_telegram_runtime_health
from spark_intelligence.llm.direct_provider import DirectProviderRequest, execute_direct_provider_prompt
from spark_intelligence.security.redaction import redact_text
from spark_intelligence.self_awareness.system_map_read_model import _freshness_from_generated_at
from spark_intelligence.spawner_payload_drift import _extract_repo_references
from spark_intelligence.swarm_bridge.sync import (
    _import_researcher_symbol,
    _resolve_specialization_default_mutation_target_path,
)

from tests.test_support import SparkTestCase


def test_system_map_freshness_rejects_invalid_and_future_timestamps() -> None:
    assert _freshness_from_generated_at(None) == "unknown"
    assert _freshness_from_generated_at("not-a-time") == "unparseable_timestamp"
    assert _freshness_from_generated_at("2000-01-01T00:00:00Z") == "stale"
    assert _freshness_from_generated_at("2099-01-01T00:00:00Z") == "future_timestamp"


def test_researcher_dynamic_imports_are_allowlisted(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="allowed researcher module list"):
        _import_researcher_symbol(tmp_path, "attacker.module", "payload")


def test_manifest_mutation_target_must_stay_inside_repo(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    inside = _resolve_specialization_default_mutation_target_path(
        repo_root,
        {"templates": [{"destination": "generated/candidate.json"}]},
    )
    escaped = _resolve_specialization_default_mutation_target_path(
        repo_root,
        {"templates": [{"destination": "../outside.json"}]},
    )

    assert inside == repo_root / "generated" / "candidate.json"
    assert escaped is None


def test_recursive_payload_scan_bounds_both_descent_paths() -> None:
    deeply_nested: object = "spark-intelligence-builder"
    for _ in range(80):
        deeply_nested = [deeply_nested]

    assert _extract_repo_references({"repo": deeply_nested}) == []


def test_redaction_hides_local_home_paths() -> None:
    rendered = redact_text(
        "failure at /Users/alice/private/auth.json and C:\\Users\\Alice\\private\\cache.json"
    )

    assert rendered.count("<redacted local path>") == 2
    assert "alice" not in rendered.lower()


def test_anthropic_long_system_prompt_uses_cacheable_system_block() -> None:
    captured: dict[str, object] = {}
    provider = DirectProviderRequest(
        provider_id="anthropic",
        provider_kind="anthropic",
        auth_method="api_key_env",
        api_mode="anthropic_messages",
        base_url="https://api.anthropic.com",
        model="claude-sonnet-4-6",
        secret_value="test-secret",
    )

    def fake_post(url, *, headers, payload, provider):
        captured["payload"] = payload
        return {"content": [{"type": "text", "text": "ok"}]}

    with patch("spark_intelligence.llm.direct_provider._post_json", side_effect=fake_post):
        execute_direct_provider_prompt(
            provider=provider,
            system_prompt="s" * 5000,
            user_prompt="hello",
        )

    payload = captured["payload"]
    assert isinstance(payload, dict)
    assert payload["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert payload["messages"] == [{"role": "user", "content": "hello"}]


def test_windows_only_kill_script_fails_closed_on_other_hosts(tmp_path: Path) -> None:
    script = Path(__file__).parents[1] / "scripts" / "kill-spark.sh"
    result = subprocess.run(
        ["bash", str(script)],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "PATH": "/usr/bin:/bin"},
    )

    assert result.returncode == 1
    assert "requires Windows + Git Bash" in result.stderr
    assert "spark stop" in result.stderr


class BuilderAdoptionGatewayTests(SparkTestCase):
    def test_gateway_without_runnable_channel_fails_honestly(self) -> None:
        report = gateway_start(self.config_manager, self.state_db, once=True)

        self.assertFalse(report.ok)
        self.assertIn("No runnable foreground channel", report.text)

    def test_all_failed_outbound_sends_count_as_poll_failure(self) -> None:
        self.add_telegram_channel(bot_token="test-token")

        class AuthenticatedClient:
            def __init__(self, token: str):
                self.token = token

            def get_me(self) -> dict[str, object]:
                return {"result": {"username": "sparkbot"}}

        poll_result = SimpleNamespace(
            failed_send_count=1,
            sent_count=0,
            processed_count=1,
            to_text=lambda: "processed=1 failed_sends=1",
        )
        with (
            patch("spark_intelligence.gateway.runtime.TelegramBotApiClient", AuthenticatedClient),
            patch("spark_intelligence.gateway.runtime.poll_telegram_updates_once", return_value=poll_result),
        ):
            report = gateway_start(self.config_manager, self.state_db, once=True)

        health = read_telegram_runtime_health(self.state_db)
        self.assertFalse(report.ok)
        self.assertEqual(health.last_failure_type, "outbound_send_failed")
        self.assertEqual(health.consecutive_failures, 1)
        self.assertIn("failed_sends=1", report.text)
