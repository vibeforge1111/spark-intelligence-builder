from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _load_run_summary_from_pointer(pointer_path: Path) -> dict[str, Any]:
    pointer = _load_json(pointer_path)
    return _load_json(Path(pointer["run_summary"]))


def _load_run_summary(source_path: Path) -> dict[str, Any]:
    payload = _load_json(source_path)
    if "run_summary" in payload:
        return _load_json(Path(payload["run_summary"]))
    return payload


def _maybe_load_soak(run_summary: dict[str, Any]) -> dict[str, Any] | None:
    soak_dir = run_summary.get("soak_output_dir")
    if not soak_dir:
        return None
    soak_path = Path(str(soak_dir)) / "telegram-memory-architecture-soak.json"
    if not soak_path.exists():
        return None
    return _load_json(soak_path)


def _maybe_load_regression(run_summary: dict[str, Any]) -> dict[str, Any] | None:
    regression_dir = run_summary.get("regression_output_dir")
    if not regression_dir:
        return None
    regression_path = Path(str(regression_dir)) / "telegram-memory-regression.json"
    if not regression_path.exists():
        return None
    return _load_json(regression_path)


def _fmt_list(items: list[str]) -> str:
    return ", ".join(items) if items else "none"


def render_delta(*, latest_pointer: Path, previous_source: Path) -> str:
    latest_summary = _load_run_summary(latest_pointer)
    previous_summary = _load_run_summary(previous_source)
    latest_soak = _maybe_load_soak(latest_summary)
    previous_soak = _maybe_load_soak(previous_summary)
    latest_regression = _maybe_load_regression(latest_summary)
    previous_regression = _maybe_load_regression(previous_summary)

    lines: list[str] = []
    lines.append("# Memory Validation Delta")
    lines.append("")
    lines.append(f"- latest full run: `{latest_summary.get('output_root')}`")
    lines.append(f"- previous full run: `{previous_summary.get('output_root')}`")
    lines.append("")
    lines.append("## Runtime")
    lines.append("")
    lines.append(f"- latest runtime architecture: `{latest_summary.get('offline_runtime_architecture') or 'unknown'}`")
    lines.append(f"- previous runtime architecture: `{previous_summary.get('offline_runtime_architecture') or 'unknown'}`")
    lines.append(f"- latest offline leaders: `{_fmt_list(list(latest_summary.get('offline_product_memory_leaders') or []))}`")
    lines.append(f"- previous offline leaders: `{_fmt_list(list(previous_summary.get('offline_product_memory_leaders') or []))}`")
    lines.append("")
    lines.append("## Live Regression")
    lines.append("")
    lines.append(f"- latest: `{latest_summary.get('live_regression') or 'unknown'}`")
    lines.append(f"- previous: `{previous_summary.get('live_regression') or 'unknown'}`")
    lines.append(f"- latest leaders: `{_fmt_list(list(latest_summary.get('live_regression_leaders') or []))}`")
    lines.append(f"- previous leaders: `{_fmt_list(list(previous_summary.get('live_regression_leaders') or []))}`")
    lines.append("")
    lines.append("## Live Soak")
    lines.append("")
    lines.append(f"- latest completion: `{latest_summary.get('live_soak_completion') or 'unknown'}`")
    lines.append(f"- previous completion: `{previous_summary.get('live_soak_completion') or 'unknown'}`")
    lines.append(f"- latest leaders: `{_fmt_list(list(latest_summary.get('live_soak_leaders') or []))}`")
    lines.append(f"- previous leaders: `{_fmt_list(list(previous_summary.get('live_soak_leaders') or []))}`")
    lines.append(f"- latest top two: `{_fmt_list(list(latest_summary.get('live_soak_recommended_top_two') or []))}`")
    lines.append(f"- previous top two: `{_fmt_list(list(previous_summary.get('live_soak_recommended_top_two') or []))}`")

    if latest_soak and previous_soak:
        latest_rows = {row["baseline_name"]: row for row in latest_soak.get("selection_aggregate_results", [])}
        previous_rows = {row["baseline_name"]: row for row in previous_soak.get("selection_aggregate_results", [])}
        lines.append("")
        lines.append("## Selector Aggregate")
        lines.append("")
        for baseline_name in sorted(set(latest_rows) | set(previous_rows)):
            latest_row = latest_rows.get(baseline_name, {})
            previous_row = previous_rows.get(baseline_name, {})
            latest_total = latest_row.get("total")
            previous_total = previous_row.get("total")
            latest_matched = latest_row.get("matched")
            previous_matched = previous_row.get("matched")
            lines.append(
                f"- `{baseline_name}`: latest `{latest_matched}/{latest_total}`, previous `{previous_matched}/{previous_total}`"
            )

    if latest_regression and previous_regression:
        latest_summary_payload = latest_regression.get("summary", {})
        previous_summary_payload = previous_regression.get("summary", {})
        latest_mismatches = list(latest_summary_payload.get("mismatched_case_ids") or [])
        previous_mismatches = list(previous_summary_payload.get("mismatched_case_ids") or [])
        lines.append("")
        lines.append("## Mismatch Delta")
        lines.append("")
        lines.append(f"- latest mismatches: `{_fmt_list(latest_mismatches)}`")
        lines.append(f"- previous mismatches: `{_fmt_list(previous_mismatches)}`")

    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Render a delta between two full memory validation runs.")
    default_root = Path.home() / ".spark-intelligence" / "artifacts" / "memory-validation-runs"
    parser.add_argument("--latest", default=str(default_root / "latest-full-run.json"), help="Pointer to the latest full run")
    parser.add_argument("--previous", required=True, help="Pointer or run-summary path for the previous full run")
    parser.add_argument("--write", help="Optional output markdown path")
    args = parser.parse_args()

    latest_path = Path(args.latest)
    previous_path = Path(args.previous)
    markdown = render_delta(latest_pointer=latest_path, previous_source=previous_path)
    if args.write:
        Path(args.write).write_text(markdown, encoding="utf-8")
    else:
        print(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
