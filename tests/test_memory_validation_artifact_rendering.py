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


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_render_memory_validation_delta_reports_runtime_timings_and_mismatches(tmp_path: Path) -> None:
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "render_memory_validation_delta.py"
    module = _load_module("render_memory_validation_delta_test", script_path)

    latest_pointer = tmp_path / "latest-full-run.json"
    previous_pointer = tmp_path / "previous-full-run.json"
    latest_summary = tmp_path / "latest-run-summary.json"
    previous_summary = tmp_path / "previous-run-summary.json"
    latest_regression_dir = tmp_path / "latest-regression"
    previous_regression_dir = tmp_path / "previous-regression"
    latest_soak_dir = tmp_path / "latest-soak"
    previous_soak_dir = tmp_path / "previous-soak"
    latest_regression_dir.mkdir()
    previous_regression_dir.mkdir()
    latest_soak_dir.mkdir()
    previous_soak_dir.mkdir()

    _write_json(
        latest_pointer,
        {
            "output_root": str(tmp_path / "20260412-013326"),
            "run_summary": str(latest_summary),
        },
    )
    _write_json(
        previous_pointer,
        {
            "output_root": str(tmp_path / "20260412-001858"),
            "run_summary": str(previous_summary),
        },
    )
    _write_json(
        latest_summary,
        {
            "output_root": str(tmp_path / "20260412-013326"),
            "offline_runtime_architecture": "summary_synthesis_memory",
            "offline_product_memory_leaders": [
                "summary_synthesis_memory",
                "dual_store_event_calendar_hybrid",
            ],
            "benchmark_duration_seconds": 12.348,
            "regression_duration_seconds": 23.045,
            "soak_duration_seconds": 348.233,
            "total_duration_seconds": 383.853,
            "live_regression": "34/34",
            "live_regression_leaders": ["summary_synthesis_memory"],
            "live_soak_completion": "14/14",
            "live_soak_leaders": ["summary_synthesis_memory"],
            "live_soak_recommended_top_two": [
                "summary_synthesis_memory",
                "dual_store_event_calendar_hybrid",
            ],
            "regression_output_dir": str(latest_regression_dir),
            "soak_output_dir": str(latest_soak_dir),
        },
    )
    _write_json(
        previous_summary,
        {
            "output_root": str(tmp_path / "20260412-001858"),
            "offline_runtime_architecture": "dual_store_event_calendar_hybrid",
            "offline_product_memory_leaders": ["dual_store_event_calendar_hybrid"],
            "benchmark_duration_seconds": 13.153,
            "regression_duration_seconds": 25.528,
            "soak_duration_seconds": 350.805,
            "total_duration_seconds": 389.672,
            "live_regression": "33/34",
            "live_regression_leaders": ["dual_store_event_calendar_hybrid"],
            "live_soak_completion": "13/14",
            "live_soak_leaders": ["dual_store_event_calendar_hybrid"],
            "live_soak_recommended_top_two": [
                "dual_store_event_calendar_hybrid",
                "summary_synthesis_memory",
            ],
            "regression_output_dir": str(previous_regression_dir),
            "soak_output_dir": str(previous_soak_dir),
        },
    )
    _write_json(
        latest_regression_dir / "telegram-memory-regression.json",
        {"summary": {"mismatched_case_ids": []}},
    )
    _write_json(
        previous_regression_dir / "telegram-memory-regression.json",
        {"summary": {"mismatched_case_ids": ["city_history_query_after_overwrite"]}},
    )
    _write_json(
        latest_soak_dir / "telegram-memory-architecture-soak.json",
        {
            "selection_aggregate_results": [
                {"baseline_name": "summary_synthesis_memory", "matched": 64, "total": 64},
                {"baseline_name": "dual_store_event_calendar_hybrid", "matched": 61, "total": 64},
            ]
        },
    )
    _write_json(
        previous_soak_dir / "telegram-memory-architecture-soak.json",
        {
            "selection_aggregate_results": [
                {"baseline_name": "summary_synthesis_memory", "matched": 62, "total": 64},
                {"baseline_name": "dual_store_event_calendar_hybrid", "matched": 64, "total": 64},
            ]
        },
    )

    markdown = module.render_delta(latest_pointer=latest_pointer, previous_source=previous_pointer)

    assert "# Memory Validation Delta" in markdown
    assert "latest runtime architecture: `summary_synthesis_memory`" in markdown
    assert "previous runtime architecture: `dual_store_event_calendar_hybrid`" in markdown
    assert "latest benchmark duration: `12.348s`" in markdown
    assert "previous total duration: `389.672s`" in markdown
    assert "`summary_synthesis_memory`: latest `64/64`, previous `62/64`" in markdown
    assert "latest mismatches: `none`" in markdown
    assert "previous mismatches: `city_history_query_after_overwrite`" in markdown


def test_render_memory_validation_delta_accepts_direct_previous_run_summary_and_unknown_timings(tmp_path: Path) -> None:
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "render_memory_validation_delta.py"
    module = _load_module("render_memory_validation_delta_direct_summary_test", script_path)

    latest_pointer = tmp_path / "latest-full-run.json"
    latest_summary = tmp_path / "latest-run-summary.json"
    previous_summary = tmp_path / "previous-run-summary.json"
    latest_regression_dir = tmp_path / "latest-regression"
    latest_soak_dir = tmp_path / "latest-soak"
    latest_regression_dir.mkdir()
    latest_soak_dir.mkdir()

    _write_json(
        latest_pointer,
        {
            "output_root": str(tmp_path / "20260412-014855"),
            "run_summary": str(latest_summary),
        },
    )
    _write_json(
        latest_summary,
        {
            "output_root": str(tmp_path / "20260412-014855"),
            "offline_runtime_architecture": "summary_synthesis_memory",
            "offline_product_memory_leaders": ["summary_synthesis_memory"],
            "benchmark_duration_seconds": 15.816,
            "regression_duration_seconds": 26.001,
            "soak_duration_seconds": 335.587,
            "total_duration_seconds": 377.593,
            "live_regression": "34/34",
            "live_regression_leaders": ["summary_synthesis_memory"],
            "live_soak_completion": "14/14",
            "live_soak_leaders": ["summary_synthesis_memory"],
            "live_soak_recommended_top_two": [
                "summary_synthesis_memory",
                "dual_store_event_calendar_hybrid",
            ],
            "regression_output_dir": str(latest_regression_dir),
            "soak_output_dir": str(latest_soak_dir),
        },
    )
    _write_json(
        previous_summary,
        {
            "output_root": str(tmp_path / "20260412-001418"),
            "offline_runtime_architecture": "dual_store_event_calendar_hybrid",
            "offline_product_memory_leaders": ["dual_store_event_calendar_hybrid"],
            "benchmark_duration_seconds": None,
            "regression_duration_seconds": "",
            "soak_duration_seconds": None,
            "total_duration_seconds": "",
            "live_regression": "unknown",
            "live_regression_leaders": [],
            "live_soak_completion": "unknown",
            "live_soak_leaders": [],
            "live_soak_recommended_top_two": [],
        },
    )
    _write_json(
        latest_regression_dir / "telegram-memory-regression.json",
        {"summary": {"mismatched_case_ids": ["mission_explanation"]}},
    )
    _write_json(
        latest_soak_dir / "telegram-memory-architecture-soak.json",
        {
            "selection_aggregate_results": [
                {"baseline_name": "summary_synthesis_memory", "matched": 64, "total": 64},
            ]
        },
    )

    markdown = module.render_delta(latest_pointer=latest_pointer, previous_source=previous_summary)

    assert f"- previous full run: `{tmp_path / '20260412-001418'}`" in markdown
    assert "previous benchmark duration: `unknown`" in markdown
    assert "previous regression duration: `unknown`" in markdown
    assert "previous soak duration: `unknown`" in markdown
    assert "previous total duration: `unknown`" in markdown
    assert "previous leaders: `none`" in markdown
    assert "## Mismatch Delta" not in markdown


def test_render_memory_failure_ledger_reports_clean_baseline_and_watchlist(tmp_path: Path) -> None:
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "render_memory_failure_ledger.py"
    module = _load_module("render_memory_failure_ledger_test", script_path)

    latest_pointer = tmp_path / "latest-full-run.json"
    run_summary = tmp_path / "run-summary.json"
    regression_dir = tmp_path / "telegram-memory-regression"
    soak_dir = tmp_path / "telegram-memory-architecture-soak"
    regression_dir.mkdir()
    soak_dir.mkdir()

    _write_json(
        latest_pointer,
        {
            "output_root": str(tmp_path / "20260412-013326"),
            "run_summary": str(run_summary),
        },
    )
    _write_json(
        run_summary,
        {
            "output_root": str(tmp_path / "20260412-013326"),
            "builder_repo_commit": "builder-sha",
            "domain_chip_repo_commit": "chip-sha",
            "offline_runtime_architecture": "summary_synthesis_memory",
            "offline_product_memory_leaders": [
                "summary_synthesis_memory",
                "dual_store_event_calendar_hybrid",
            ],
            "live_regression": "34/34",
            "live_regression_leaders": ["summary_synthesis_memory"],
            "live_soak_completion": "14/14",
            "live_soak_leaders": ["summary_synthesis_memory"],
            "live_soak_recommended_top_two": [
                "summary_synthesis_memory",
                "dual_store_event_calendar_hybrid",
            ],
            "regression_output_dir": str(regression_dir),
            "soak_output_dir": str(soak_dir),
        },
    )
    _write_json(
        regression_dir / "telegram-memory-regression.json",
        {
            "summary": {
                "mismatched_case_ids": [],
            }
        },
    )
    _write_json(
        soak_dir / "telegram-memory-architecture-soak.json",
        {
            "summary": {
                "selector_packs_requiring_work": [],
            },
            "benchmark_pack_results": [
                {
                    "pack_id": "temporal_conflict_gauntlet",
                    "selection_role": "selector",
                    "leader_names": ["summary_synthesis_memory"],
                },
                {
                    "pack_id": "identity_under_recency_pressure",
                    "selection_role": "health_gate",
                    "leader_names": [
                        "summary_synthesis_memory",
                        "dual_store_event_calendar_hybrid",
                    ],
                },
            ],
        },
    )

    markdown = module.render_failure_ledger(latest_run_path=latest_pointer)

    assert "# Memory Failure Ledger" in markdown
    assert "- open live mismatches: `0`" in markdown
    assert "- open selector-pack gaps requiring work: `0`" in markdown
    assert "there is no current artifact-backed reason to claim a broken live memory surface" in markdown.lower()
    assert "- `temporal_conflict_gauntlet`" in markdown
    assert "current leader: `summary_synthesis_memory`" in markdown
    assert "bucket if it regresses: `event_history_chronology`" in markdown
    assert "- `identity_under_recency_pressure`" in markdown
    assert "builder-sha" in markdown
    assert "chip-sha" in markdown


def test_render_memory_failure_ledger_reports_open_mismatches_and_selector_gaps(tmp_path: Path) -> None:
    script_path = Path(__file__).resolve().parents[1] / "scripts" / "render_memory_failure_ledger.py"
    module = _load_module("render_memory_failure_ledger_open_issues_test", script_path)

    latest_pointer = tmp_path / "latest-full-run.json"
    run_summary = tmp_path / "run-summary.json"
    regression_dir = tmp_path / "telegram-memory-regression"
    soak_dir = tmp_path / "telegram-memory-architecture-soak"
    regression_dir.mkdir()
    soak_dir.mkdir()

    _write_json(
        latest_pointer,
        {
            "output_root": str(tmp_path / "20260412-013326"),
            "run_summary": str(run_summary),
        },
    )
    _write_json(
        run_summary,
        {
            "output_root": str(tmp_path / "20260412-013326"),
            "builder_repo_commit": "builder-sha",
            "domain_chip_repo_commit": "chip-sha",
            "offline_runtime_architecture": "summary_synthesis_memory",
            "offline_product_memory_leaders": [
                "summary_synthesis_memory",
                "dual_store_event_calendar_hybrid",
            ],
            "live_regression": "32/34",
            "live_regression_leaders": ["summary_synthesis_memory"],
            "live_soak_completion": "13/14",
            "live_soak_leaders": ["summary_synthesis_memory"],
            "live_soak_recommended_top_two": [
                "summary_synthesis_memory",
                "dual_store_event_calendar_hybrid",
            ],
            "regression_output_dir": str(regression_dir),
            "soak_output_dir": str(soak_dir),
        },
    )
    _write_json(
        regression_dir / "telegram-memory-regression.json",
        {
            "summary": {
                "mismatched_case_ids": [
                    "city_history_query_after_overwrite",
                    "country_history_query_after_overwrite",
                ],
            }
        },
    )
    _write_json(
        soak_dir / "telegram-memory-architecture-soak.json",
        {
            "summary": {
                "selector_packs_requiring_work": [
                    "temporal_conflict_gauntlet",
                    "event_calendar_lineage_proxy",
                ],
            },
            "benchmark_pack_results": [
                {
                    "pack_id": "temporal_conflict_gauntlet",
                    "selection_role": "selector",
                    "leader_names": ["dual_store_event_calendar_hybrid"],
                },
                {
                    "pack_id": "identity_under_recency_pressure",
                    "selection_role": "health_gate",
                    "leader_names": [
                        "summary_synthesis_memory",
                        "dual_store_event_calendar_hybrid",
                    ],
                },
            ],
        },
    )

    markdown = module.render_failure_ledger(latest_run_path=latest_pointer)

    assert "- open live mismatches: `2`" in markdown
    assert "- open selector-pack gaps requiring work: `2`" in markdown
    assert "Open issues:" in markdown
    assert "Prioritize the current artifact-backed issues before any new architecture exploration." in markdown
    assert "city_history_query_after_overwrite" in markdown
    assert "country_history_query_after_overwrite" in markdown
    assert "temporal_conflict_gauntlet" in markdown
    assert "event_calendar_lineage_proxy" in markdown
