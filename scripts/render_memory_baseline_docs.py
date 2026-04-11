from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
LATEST_POINTER = Path.home() / ".spark-intelligence" / "artifacts" / "memory-validation-runs" / "latest-full-run.json"

README_PATH = ROOT / "README.md"
LIVE_RESULTS_PATH = ROOT / "docs" / "MEMORY_LIVE_VALIDATION_RESULTS_2026-04-11.md"
HANDOFF_PATH = ROOT / "docs" / "MEMORY_BENCHMARK_HANDOFF_2026-04-11.md"


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _fmt_seconds(value: Any) -> str:
    if value is None or value == "":
        return "unknown"
    return f"{float(value):0.3f}s"


def _load_regression_and_soak(run_summary: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    regression_path = Path(str(run_summary["regression_output_dir"])) / "telegram-memory-regression.json"
    soak_path = Path(str(run_summary["soak_output_dir"])) / "telegram-memory-architecture-soak.json"
    return _load_json(regression_path), _load_json(soak_path)


def _row_by_name(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(row["baseline_name"]): row for row in rows}


def _replace_marked_block(content: str, start_marker: str, end_marker: str, replacement_body: str) -> str:
    pattern = re.compile(
        rf"(?P<start>{re.escape(start_marker)}\n)(?P<body>.*?)(?P<end>\n{re.escape(end_marker)})",
        re.DOTALL,
    )
    match = pattern.search(content)
    if not match:
        raise ValueError(f"Missing markers: {start_marker} / {end_marker}")
    return content[: match.start("body")] + replacement_body.rstrip("\n") + content[match.start("end") :]


def _build_readme_block(run_summary: dict[str, Any], pointer_path: Path) -> str:
    return "\n".join(
        [
            f"- current clean full-run baseline:",
            f"  `.spark-intelligence\\artifacts\\memory-validation-runs\\{Path(str(run_summary['output_root'])).name}`",
            f"- current canonical full-run pointer:",
            f"  `.spark-intelligence\\artifacts\\memory-validation-runs\\{pointer_path.name}`",
            f"- expected full validation cost from the latest clean run:",
            f"  - benchmark: `{_fmt_seconds(run_summary.get('benchmark_duration_seconds'))}`",
            f"  - regression: `{_fmt_seconds(run_summary.get('regression_duration_seconds'))}`",
            f"  - soak: `{_fmt_seconds(run_summary.get('soak_duration_seconds'))}`",
            f"  - total: `{_fmt_seconds(run_summary.get('total_duration_seconds'))}`",
        ]
    )


def _build_live_results_block(run_summary: dict[str, Any], regression_payload: dict[str, Any], soak_payload: dict[str, Any]) -> str:
    regression_summary = regression_payload["summary"]
    soak_summary = soak_payload["summary"]
    aggregate_rows = _row_by_name(list(soak_payload.get("aggregate_results") or []))
    selector_rows = _row_by_name(list(soak_payload.get("selection_aggregate_results") or []))
    ssm_aggregate = aggregate_rows.get("summary_synthesis_memory", {})
    dsech_aggregate = aggregate_rows.get("dual_store_event_calendar_hybrid", {})
    ssm_selector = selector_rows.get("summary_synthesis_memory", {})
    dsech_selector = selector_rows.get("dual_store_event_calendar_hybrid", {})
    kb_valid = "valid" if regression_summary.get("kb_has_probe_coverage") else "missing probe coverage"
    return "\n".join(
        [
            f"- Latest clean full validation root: `{run_summary['output_root']}`",
            f"- Stable latest full-run pointer: `{LATEST_POINTER}`",
            f"- Stable previous full-run pointer: `{LATEST_POINTER.with_name('previous-full-run.json')}`",
            f"- Live Telegram regression: `{run_summary.get('live_regression') or 'unknown'}` matched",
            f"- KB compile: {kb_valid}",
            f"- KB probe coverage: `{regression_summary.get('kb_current_state_hits', 'unknown')}/{regression_summary.get('kb_current_state_total', 'unknown')}` current-state and `{regression_summary.get('kb_evidence_hits', 'unknown')}/{regression_summary.get('kb_evidence_total', 'unknown')}` evidence hits",
            f"- Clean Telegram soak status: `{soak_summary.get('completed_runs', 'unknown')}/{soak_summary.get('requested_runs', 'unknown')}` completed, `{soak_summary.get('failed_runs', 'unknown')}` failed",
            f"- Latest clean whole-suite soak leader: `{', '.join(run_summary.get('live_soak_leaders') or ['unknown'])}`",
            f"- Latest clean whole-suite aggregate: `{ssm_aggregate.get('matched', 'unknown')}/{ssm_aggregate.get('total', 'unknown')}` for `summary_synthesis_memory` vs `{dsech_aggregate.get('matched', 'unknown')}/{dsech_aggregate.get('total', 'unknown')}` for `dual_store_event_calendar_hybrid`",
            f"- Latest clean selector-pack aggregate: `{ssm_selector.get('matched', 'unknown')}/{ssm_selector.get('total', 'unknown')}` for `summary_synthesis_memory` vs `{dsech_selector.get('matched', 'unknown')}/{dsech_selector.get('total', 'unknown')}` for `dual_store_event_calendar_hybrid`",
            f"- Offline ProductMemory result: tied at `1156/1266` between `summary_synthesis_memory` and `dual_store_event_calendar_hybrid`",
            f"- Current runtime selector: `summary_synthesis_memory`",
            f"- Latest clean timed validation cost:",
            f"  - benchmark: `{_fmt_seconds(run_summary.get('benchmark_duration_seconds'))}`",
            f"  - regression: `{_fmt_seconds(run_summary.get('regression_duration_seconds'))}`",
            f"  - soak: `{_fmt_seconds(run_summary.get('soak_duration_seconds'))}`",
            f"  - total: `{_fmt_seconds(run_summary.get('total_duration_seconds'))}`",
        ]
    )


def _build_handoff_block(run_summary: dict[str, Any], soak_payload: dict[str, Any]) -> str:
    output_root = Path(str(run_summary["output_root"]))
    delta_path = output_root / "validation-delta.md"
    soak_summary = soak_payload["summary"]
    return "\n".join(
        [
            f"- latest clean full validation root:",
            f"  `{run_summary['output_root']}`",
            f"- latest full-run pointer:",
            f"  `{LATEST_POINTER}`",
            f"- previous full-run pointer:",
            f"  `{LATEST_POINTER.with_name('previous-full-run.json')}`",
            f"- validation delta for the latest run:",
            f"  `{delta_path}`",
            f"- offline ProductMemory leaders:",
            f"  `summary_synthesis_memory`, `dual_store_event_calendar_hybrid`",
            f"- live regression:",
            f"  `{run_summary.get('live_regression') or 'unknown'}`",
            f"- live soak:",
            f"  `{run_summary.get('live_soak_completion') or 'unknown'}`, `{soak_summary.get('failed_runs', 'unknown')}` failed",
            f"- live soak leader:",
            f"  `{', '.join(run_summary.get('live_soak_leaders') or ['unknown'])}`",
            f"- measured validation cost:",
            f"  - benchmark: `{_fmt_seconds(run_summary.get('benchmark_duration_seconds'))}`",
            f"  - regression: `{_fmt_seconds(run_summary.get('regression_duration_seconds'))}`",
            f"  - soak: `{_fmt_seconds(run_summary.get('soak_duration_seconds'))}`",
            f"  - total: `{_fmt_seconds(run_summary.get('total_duration_seconds'))}`",
        ]
    )


def render_docs(*, latest_run_path: Path) -> None:
    latest_pointer = _load_json(latest_run_path)
    run_summary = _load_json(Path(str(latest_pointer["run_summary"])))
    regression_payload, soak_payload = _load_regression_and_soak(run_summary)

    readme = README_PATH.read_text(encoding="utf-8")
    readme = _replace_marked_block(
        readme,
        "<!-- AUTO_MEMORY_BASELINE_README_START -->",
        "<!-- AUTO_MEMORY_BASELINE_README_END -->",
        _build_readme_block(run_summary, latest_run_path),
    )
    README_PATH.write_text(readme, encoding="utf-8")

    live_results = LIVE_RESULTS_PATH.read_text(encoding="utf-8")
    live_results = _replace_marked_block(
        live_results,
        "<!-- AUTO_MEMORY_BASELINE_LIVE_RESULTS_START -->",
        "<!-- AUTO_MEMORY_BASELINE_LIVE_RESULTS_END -->",
        _build_live_results_block(run_summary, regression_payload, soak_payload),
    )
    LIVE_RESULTS_PATH.write_text(live_results, encoding="utf-8")

    handoff = HANDOFF_PATH.read_text(encoding="utf-8")
    handoff = _replace_marked_block(
        handoff,
        "<!-- AUTO_MEMORY_BASELINE_HANDOFF_START -->",
        "<!-- AUTO_MEMORY_BASELINE_HANDOFF_END -->",
        _build_handoff_block(run_summary, soak_payload),
    )
    HANDOFF_PATH.write_text(handoff, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Refresh memory baseline docs from the latest full validation run.")
    parser.add_argument("--latest-run", default=str(LATEST_POINTER), help="Path to latest-full-run.json")
    args = parser.parse_args()
    render_docs(latest_run_path=Path(args.latest_run))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
