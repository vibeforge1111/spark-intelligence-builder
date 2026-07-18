from __future__ import annotations

from types import SimpleNamespace

from spark_intelligence.adapters.telegram.runtime import _render_direct_chip_execution_reply


def test_generic_chip_completion_keeps_unicode_dense_and_reads_naturally() -> None:
    execution = SimpleNamespace(
        ok=True,
        chip_key="domain-chip-launcher",
        output={"result": {"status": "ready", "message": "🚀 launched — café"}},
    )

    reply = _render_direct_chip_execution_reply(
        execution=execution,
        hook="launch",
        payload_mode="explicit_payload",
    )

    assert reply.startswith("✨ domain-chip-launcher finished `launch`.")
    assert "🚀 launched — café" in reply
    assert "\\ud83d" not in reply
    assert "Input mode:" not in reply
    assert "Output:" not in reply


def test_unicode_preview_uses_the_visible_budget_for_payload_not_ascii_escapes() -> None:
    execution = SimpleNamespace(
        ok=True,
        chip_key="domain-chip-launcher",
        output={"result": {"status": "ready", "message": "🚀" * 400}},
    )

    reply = _render_direct_chip_execution_reply(
        execution=execution,
        hook="launch",
        payload_mode="explicit_payload",
    )

    assert reply.count("🚀") == 400
    assert "\\ud83d\\ude80" not in reply


def test_long_unicode_preview_marks_truncation_without_breaking_the_reply_shape() -> None:
    execution = SimpleNamespace(
        ok=True,
        chip_key="domain-chip-launcher",
        output={"result": {"status": "ready", "message": "🚀" * 2000}},
    )

    reply = _render_direct_chip_execution_reply(
        execution=execution,
        hook="launch",
        payload_mode="explicit_payload",
    )

    preview = reply.split("\n\n", 1)[1]
    assert len(preview) == 1500
    assert preview.endswith("…")
    assert "\\ud83d" not in preview


def test_generic_chip_failure_is_one_plain_attention_line() -> None:
    execution = SimpleNamespace(
        ok=False,
        chip_key="domain-chip-launcher",
        output={"error": "provider is still warming up"},
        stderr="",
        stdout="",
    )

    reply = _render_direct_chip_execution_reply(
        execution=execution,
        hook="launch",
        payload_mode="explicit_payload",
    )

    assert reply == "⚠️ domain-chip-launcher couldn’t finish `launch`. Provider is still warming up."
    assert "\n" not in reply
