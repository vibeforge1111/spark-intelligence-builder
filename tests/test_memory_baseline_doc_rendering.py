from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _load_module(module_name: str, script_path: Path):
    spec = importlib.util.spec_from_file_location(module_name, script_path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"Unable to load module from {script_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_render_memory_baseline_docs_updates_marked_sections(tmp_path: Path) -> None:
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "render_memory_baseline_docs.py"
    module = _load_module("render_memory_baseline_docs_test", script_path)

    readme = tmp_path / "README.md"
    live_results = tmp_path / "MEMORY_LIVE_VALIDATION_RESULTS_2026-04-11.md"
    handoff = tmp_path / "MEMORY_BENCHMARK_HANDOFF_2026-04-11.md"
    pointer = tmp_path / "latest-full-run.json"
    run_summary = tmp_path / "run-summary.json"
    regression_dir = tmp_path / "telegram-memory-regression"
    soak_dir = tmp_path / "telegram-memory-architecture-soak"
    regression_dir.mkdir()
    soak_dir.mkdir()

    readme.write_text(
        "\n".join(
            [
                "prefix",
                "<!-- AUTO_MEMORY_BASELINE_README_START -->",
                "old",
                "<!-- AUTO_MEMORY_BASELINE_README_END -->",
                "suffix",
            ]
        ),
        encoding="utf-8",
    )
    live_results.write_text(
        "\n".join(
            [
                "prefix",
                "<!-- AUTO_MEMORY_BASELINE_LIVE_RESULTS_START -->",
                "old",
                "<!-- AUTO_MEMORY_BASELINE_LIVE_RESULTS_END -->",
                "suffix",
            ]
        ),
        encoding="utf-8",
    )
    handoff.write_text(
        "\n".join(
            [
                "prefix",
                "<!-- AUTO_MEMORY_BASELINE_HANDOFF_START -->",
                "old",
                "<!-- AUTO_MEMORY_BASELINE_HANDOFF_END -->",
                "suffix",
            ]
        ),
        encoding="utf-8",
    )

    pointer.write_text(
        json.dumps(
            {
                "output_root": str(tmp_path / "20260412-013326"),
                "run_summary": str(run_summary),
                "updated_at": "2026-04-12T01:33:26+04:00",
            }
        ),
        encoding="utf-8",
    )
    run_summary.write_text(
        json.dumps(
            {
                "output_root": str(tmp_path / "20260412-013326"),
                "benchmark_duration_seconds": 12.348,
                "regression_duration_seconds": 23.045,
                "soak_duration_seconds": 348.233,
                "total_duration_seconds": 383.853,
                "offline_runtime_architecture": "summary_synthesis_memory",
                "offline_product_memory_leaders": [
                    "summary_synthesis_memory",
                    "dual_store_event_calendar_hybrid",
                ],
                "live_regression": "34/34",
                "live_soak_completion": "14/14",
                "live_soak_leaders": ["summary_synthesis_memory"],
                "live_soak_recommended_top_two": [
                    "summary_synthesis_memory",
                    "dual_store_event_calendar_hybrid",
                ],
                "regression_output_dir": str(regression_dir),
                "soak_output_dir": str(soak_dir),
            }
        ),
        encoding="utf-8",
    )
    (regression_dir / "telegram-memory-regression.json").write_text(
        json.dumps(
            {
                "summary": {
                    "matched_case_count": 34,
                    "case_count": 34,
                    "kb_has_probe_coverage": True,
                    "kb_current_state_hits": 38,
                    "kb_current_state_total": 38,
                    "kb_evidence_hits": 38,
                    "kb_evidence_total": 38,
                }
            }
        ),
        encoding="utf-8",
    )
    (soak_dir / "telegram-memory-architecture-soak.json").write_text(
        json.dumps(
            {
                "summary": {
                    "completed_runs": 14,
                    "requested_runs": 14,
                    "failed_runs": 0,
                },
                "aggregate_results": [
                    {"baseline_name": "summary_synthesis_memory", "matched": 92, "total": 92},
                    {"baseline_name": "dual_store_event_calendar_hybrid", "matched": 89, "total": 92},
                ],
                "selection_aggregate_results": [
                    {"baseline_name": "summary_synthesis_memory", "matched": 64, "total": 64},
                    {"baseline_name": "dual_store_event_calendar_hybrid", "matched": 61, "total": 64},
                ],
            }
        ),
        encoding="utf-8",
    )

    module.README_PATH = readme
    module.LIVE_RESULTS_PATH = live_results
    module.HANDOFF_PATH = handoff
    module.LATEST_POINTER = pointer

    module.render_docs(latest_run_path=pointer)

    readme_text = readme.read_text(encoding="utf-8")
    assert ".spark-intelligence\\artifacts\\memory-validation-runs\\latest-full-run.json" in readme_text
    assert "20260412-013326" in readme_text
    assert "12.348s" in readme_text

    live_results_text = live_results.read_text(encoding="utf-8")
    assert "38/38" in live_results_text
    assert "92/92" in live_results_text
    assert "64/64" in live_results_text

    handoff_text = handoff.read_text(encoding="utf-8")
    assert str(pointer) in handoff_text
    assert "14/14" in handoff_text
    assert "383.853s" in handoff_text
