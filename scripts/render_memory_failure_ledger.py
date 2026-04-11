from __future__ import annotations

import argparse
import json
from pathlib import Path


WATCHLIST_BUCKETS: dict[str, str] = {
    "contradiction_and_recency": "overwrite_history_retrieval",
    "temporal_conflict_gauntlet": "event_history_chronology",
    "event_calendar_lineage_proxy": "event_history_chronology",
    "core_profile_baseline": "routing",
    "provenance_audit": "explanation_provenance",
    "interleaved_noise_resilience": "identity_synthesis",
    "quality_lane_gauntlet": "routing",
    "explanation_pressure_suite": "explanation_provenance",
}


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _render_list(items: list[str], indent: str = "- ") -> list[str]:
    if not items:
        return [f"{indent}none"]
    return [f"{indent}{item}" for item in items]


def render_failure_ledger(*, latest_run_path: Path) -> str:
    latest = _load_json(latest_run_path)
    run_summary_path = Path(latest["run_summary"])
    run_summary = _load_json(run_summary_path)
    soak_path = Path(run_summary["soak_output_dir"]) / "telegram-memory-architecture-soak.json"
    regression_path = Path(run_summary["regression_output_dir"]) / "telegram-memory-regression.json"
    soak_payload = _load_json(soak_path)
    regression_payload = _load_json(regression_path)

    soak_summary = soak_payload["summary"]
    regression_summary = regression_payload["summary"]
    pack_rows = soak_payload["benchmark_pack_results"]

    selector_rows = [row for row in pack_rows if row.get("selection_role") != "health_gate"]
    health_gate_rows = [row for row in pack_rows if row.get("selection_role") == "health_gate"]
    open_mismatches = list(regression_summary.get("mismatched_case_ids") or [])
    open_selector_gaps = list(soak_summary.get("selector_packs_requiring_work") or [])

    lines: list[str] = []
    lines.append("# Memory Failure Ledger")
    lines.append("")
    lines.append("Date: 2026-04-11")
    lines.append("Status: generated from the latest validation run")
    lines.append("")
    lines.append("## Baseline run")
    lines.append("")
    lines.append(f"- wrapper output root: `{run_summary['output_root']}`")
    lines.append(f"- manifest: `{run_summary_path}`")
    lines.append(f"- stable pointer: `{latest_run_path}`")
    lines.append("")
    lines.append("Pinned code state behind this run:")
    lines.append("")
    lines.append(f"- Builder: `{run_summary.get('builder_repo_commit') or 'unknown'}`")
    lines.append(f"- domain-chip-memory: `{run_summary.get('domain_chip_repo_commit') or 'unknown'}`")
    lines.append("")
    lines.append("## Baseline verdict")
    lines.append("")
    lines.append(f"- offline runtime architecture: `{run_summary.get('offline_runtime_architecture') or 'unknown'}`")
    lines.append("- offline ProductMemory leaders:")
    lines.extend(_render_list(list(run_summary.get("offline_product_memory_leaders") or []), indent="  - "))
    lines.append(f"- live regression: `{run_summary.get('live_regression') or 'unknown'}`")
    lines.append("- live regression leader:")
    lines.extend(_render_list(list(run_summary.get("live_regression_leaders") or []), indent="  - "))
    lines.append(f"- live soak: `{run_summary.get('live_soak_completion') or 'unknown'}`")
    lines.append("- live soak leader:")
    lines.extend(_render_list(list(run_summary.get("live_soak_leaders") or []), indent="  - "))
    lines.append("- live soak recommended top two:")
    lines.extend(_render_list(list(run_summary.get("live_soak_recommended_top_two") or []), indent="  - "))
    lines.append("")
    lines.append("## Open failures")
    lines.append("")
    lines.append("Current honest count:")
    lines.append("")
    lines.append(f"- open live mismatches: `{len(open_mismatches)}`")
    lines.append(f"- open selector-pack gaps requiring work: `{len(open_selector_gaps)}`")
    lines.append("")
    if not open_mismatches and not open_selector_gaps:
        lines.append("That means there is no current artifact-backed reason to claim a broken live memory surface on the active Builder/Telegram suite.")
    else:
        lines.append("Open issues:")
        lines.append("")
        if open_mismatches:
            lines.append("- live mismatches:")
            lines.extend(_render_list(open_mismatches, indent="  - "))
        if open_selector_gaps:
            lines.append("- selector-pack gaps:")
            lines.extend(_render_list(open_selector_gaps, indent="  - "))
    lines.append("")
    lines.append("## Current watchlist")
    lines.append("")
    lines.append("These are not current failures. They are the live separator packs and health gates that should keep being monitored during the 48-hour cycle.")
    lines.append("")
    lines.append("### Selector packs")
    lines.append("")
    for row in selector_rows:
        pack_id = str(row.get("pack_id") or "unknown")
        leader_names = list(row.get("leader_names") or [])
        state = "honest tie" if len(leader_names) > 1 else (leader_names[0] if leader_names else "unresolved")
        lines.append(f"- `{pack_id}`")
        if len(leader_names) > 1:
            lines.append(f"  - current state: `{state}`")
        else:
            lines.append(f"  - current leader: `{state}`")
        lines.append(f"  - bucket if it regresses: `{WATCHLIST_BUCKETS.get(pack_id, 'routing')}`")
    lines.append("")
    lines.append("### Health gates")
    lines.append("")
    for row in health_gate_rows:
        lines.append(f"- `{row.get('pack_id')}`")
    lines.append("")
    lines.append("These must stay green, but they should not be treated as runtime-selection votes unless they stop tying honestly.")
    lines.append("")
    lines.append("## What counts as a real new issue")
    lines.append("")
    lines.append("Do not add an item to this ledger unless one of these is true:")
    lines.append("")
    lines.append("1. live regression produces a real mismatch")
    lines.append("2. full soak produces a real failed run")
    lines.append("3. a selector pack changes leader or loses correctness on a rerun")
    lines.append("4. offline ProductMemory ceases to tie on the current two-contender head-to-head")
    lines.append("5. the harness or wrapper stops producing trustworthy artifacts")
    lines.append("")
    lines.append("## Next action from this ledger")
    lines.append("")
    if not open_mismatches and not open_selector_gaps:
        lines.append("Because there are no open live failures right now, the next work should not be speculative architecture changes.")
        lines.append("")
        lines.append("The next valid actions are:")
        lines.append("")
        lines.append("1. keep rerunning the full wrapper as the cycle baseline")
        lines.append("2. only patch behavior when a real artifact-backed miss appears")
        lines.append("3. improve operator reliability and artifact quality where that reduces ambiguity")
    else:
        lines.append("Prioritize the current artifact-backed issues before any new architecture exploration.")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Render the memory failure ledger from the latest validation run.")
    parser.add_argument(
        "--latest-run",
        default=str(Path.home() / ".spark-intelligence" / "artifacts" / "memory-validation-runs" / "latest-full-run.json"),
        help="Path to latest-full-run.json",
    )
    parser.add_argument("--write", help="Optional output path for the rendered markdown ledger")
    args = parser.parse_args()

    markdown = render_failure_ledger(latest_run_path=Path(args.latest_run))
    if args.write:
        Path(args.write).write_text(markdown, encoding="utf-8")
    else:
        print(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
