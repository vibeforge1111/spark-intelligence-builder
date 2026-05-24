from __future__ import annotations

from pathlib import Path


def test_runtime_source_keeps_browser_use_out_of_legacy_browser_lane() -> None:
    source_root = Path(__file__).resolve().parents[1] / "src"
    forbidden_patterns = (
        'target_system = "Spark Browser"',
        'owner_system="Spark Browser"',
        'label="Browser Grounding Harness"',
        'backend_kind="browser_bridge"',
        "Spark Browser extension session",
        "Spark Browser Extension",
    )
    failures: list[str] = []
    for path in sorted(source_root.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        relative = str(path.relative_to(source_root))
        for pattern in forbidden_patterns:
            if pattern in text:
                failures.append(f"{relative} contains {pattern!r}")
        if (
            "browser.grounded" in text
            and "browser_use_agent" in text
            and "BROWSER_USE_EXPLICIT_BRIDGE" not in text
        ):
            failures.append(f"{relative} mixes browser.grounded with browser_use_agent without BROWSER_USE_EXPLICIT_BRIDGE")

    assert failures == []


def test_docs_do_not_advertise_legacy_browser_extension_commands() -> None:
    docs_root = Path(__file__).resolve().parents[1] / "docs"
    forbidden_patterns = (
        "spark-intelligence browser status",
        "spark-intelligence browser page-snapshot",
        "spark-browser-extension remains",
        "spark-browser-extension is the governed browser runtime",
    )
    failures: list[str] = []
    for path in sorted(docs_root.rglob("*.md")):
        text = path.read_text(encoding="utf-8")
        relative = str(path.relative_to(docs_root))
        for pattern in forbidden_patterns:
            if pattern in text:
                failures.append(f"{relative} contains {pattern!r}")

    assert failures == []
