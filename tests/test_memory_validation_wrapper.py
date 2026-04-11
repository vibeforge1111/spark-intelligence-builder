from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path


def _git_revision(repo_root: Path) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    )
    return completed.stdout.strip()


def _copy_file(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def test_memory_validation_wrapper_writes_manifest_and_latest_pointer_on_noop(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    script_path = repo_root / "scripts" / "run_memory_two_contender_validation.ps1"
    spark_home = tmp_path / "spark-home"
    output_root = tmp_path / "validation-run"
    spark_home.mkdir()

    completed = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script_path),
            "-SparkHome",
            str(spark_home),
            "-OutputRoot",
            str(output_root),
            "-SkipBenchmark",
            "-SkipRegression",
            "-SkipSoak",
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert "Validation output root:" in completed.stdout
    assert "source baseline: unavailable" in completed.stdout
    assert "baseline staleness: unknown" in completed.stdout

    run_summary_path = output_root / "run-summary.json"
    assert run_summary_path.exists()
    run_summary = json.loads(run_summary_path.read_text(encoding="utf-8-sig"))
    assert run_summary["spark_home"] == str(spark_home)
    assert run_summary["output_root"] == str(output_root)
    assert run_summary["skipped_steps"] == ["benchmark", "regression", "soak"]
    assert run_summary["benchmark_output_dir"] is None
    assert run_summary["regression_output_dir"] is None
    assert run_summary["soak_output_dir"] is None

    latest_run_path = spark_home / "artifacts" / "memory-validation-runs" / "latest-run.json"
    assert latest_run_path.exists()
    latest_run = json.loads(latest_run_path.read_text(encoding="utf-8-sig"))
    assert latest_run["output_root"] == str(output_root)
    assert latest_run["run_summary"] == str(run_summary_path)


def test_memory_validation_wrapper_recovers_latest_full_run_pointer_from_existing_runs(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    script_path = repo_root / "scripts" / "run_memory_two_contender_validation.ps1"
    spark_home = tmp_path / "spark-home"
    output_root = tmp_path / "validation-run"
    validation_runs_root = spark_home / "artifacts" / "memory-validation-runs"
    prior_full_run_root = validation_runs_root / "20260412-013326"
    prior_full_run_root.mkdir(parents=True)

    prior_run_summary_path = prior_full_run_root / "run-summary.json"
    prior_run_summary_path.write_text(
        json.dumps(
            {
                "output_root": str(prior_full_run_root),
                "benchmark_output_dir": str(prior_full_run_root / "memory-architecture-benchmark"),
                "regression_output_dir": str(prior_full_run_root / "telegram-memory-regression"),
                "soak_output_dir": str(prior_full_run_root / "telegram-memory-architecture-soak"),
            }
        ),
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script_path),
            "-SparkHome",
            str(spark_home),
            "-OutputRoot",
            str(output_root),
            "-SkipBenchmark",
            "-SkipRegression",
            "-SkipSoak",
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout

    latest_full_run_path = validation_runs_root / "latest-full-run.json"
    assert latest_full_run_path.exists()
    latest_full_run = json.loads(latest_full_run_path.read_text(encoding="utf-8-sig"))
    assert latest_full_run["output_root"] == str(prior_full_run_root)
    assert latest_full_run["run_summary"] == str(prior_run_summary_path)


def test_memory_validation_wrapper_reports_clean_expected_baseline_banner(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    script_path = repo_root / "scripts" / "run_memory_two_contender_validation.ps1"
    chip_repo_root = repo_root.parent / "domain-chip-memory"
    spark_home = tmp_path / "spark-home"
    output_root = tmp_path / "validation-run"
    validation_runs_root = spark_home / "artifacts" / "memory-validation-runs"
    baseline_root = validation_runs_root / "20260412-013326"
    baseline_root.mkdir(parents=True)

    latest_full_run_path = validation_runs_root / "latest-full-run.json"
    baseline_run_summary_path = baseline_root / "run-summary.json"
    builder_revision = _git_revision(repo_root)
    chip_revision = _git_revision(chip_repo_root)

    baseline_run_summary_path.write_text(
        json.dumps(
            {
                "output_root": str(baseline_root),
                "builder_repo_commit": builder_revision,
                "domain_chip_repo_commit": chip_revision,
                "benchmark_duration_seconds": 12.348,
                "regression_duration_seconds": 23.045,
                "soak_duration_seconds": 348.233,
                "total_duration_seconds": 383.853,
                "benchmark_output_dir": str(baseline_root / "memory-architecture-benchmark"),
                "regression_output_dir": str(baseline_root / "telegram-memory-regression"),
                "soak_output_dir": str(baseline_root / "telegram-memory-architecture-soak"),
            }
        ),
        encoding="utf-8",
    )
    latest_full_run_path.write_text(
        json.dumps(
            {
                "output_root": str(baseline_root),
                "run_summary": str(baseline_run_summary_path),
            }
        ),
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script_path),
            "-SparkHome",
            str(spark_home),
            "-OutputRoot",
            str(output_root),
            "-SkipBenchmark",
            "-SkipRegression",
            "-SkipSoak",
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert f"source baseline: {baseline_root}" in completed.stdout
    assert "benchmark: 12.348s" in completed.stdout
    assert "regression: 23.045s" in completed.stdout
    assert "soak: 348.233s" in completed.stdout
    assert "total: 383.853s" in completed.stdout
    assert "baseline staleness: clean" in completed.stdout


def test_memory_validation_wrapper_reports_stale_expected_baseline_banner(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    script_path = repo_root / "scripts" / "run_memory_two_contender_validation.ps1"
    spark_home = tmp_path / "spark-home"
    output_root = tmp_path / "validation-run"
    validation_runs_root = spark_home / "artifacts" / "memory-validation-runs"
    baseline_root = validation_runs_root / "20260412-013326"
    baseline_root.mkdir(parents=True)

    latest_full_run_path = validation_runs_root / "latest-full-run.json"
    baseline_run_summary_path = baseline_root / "run-summary.json"

    baseline_run_summary_path.write_text(
        json.dumps(
            {
                "output_root": str(baseline_root),
                "builder_repo_commit": "builder-old-sha",
                "domain_chip_repo_commit": "chip-old-sha",
                "benchmark_duration_seconds": 12.348,
                "regression_duration_seconds": 23.045,
                "soak_duration_seconds": 348.233,
                "total_duration_seconds": 383.853,
                "benchmark_output_dir": str(baseline_root / "memory-architecture-benchmark"),
                "regression_output_dir": str(baseline_root / "telegram-memory-regression"),
                "soak_output_dir": str(baseline_root / "telegram-memory-architecture-soak"),
            }
        ),
        encoding="utf-8",
    )
    latest_full_run_path.write_text(
        json.dumps(
            {
                "output_root": str(baseline_root),
                "run_summary": str(baseline_run_summary_path),
            }
        ),
        encoding="utf-8",
    )

    completed = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script_path),
            "-SparkHome",
            str(spark_home),
            "-OutputRoot",
            str(output_root),
            "-SkipBenchmark",
            "-SkipRegression",
            "-SkipSoak",
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert f"source baseline: {baseline_root}" in completed.stdout
    assert "baseline staleness: warning" in completed.stdout
    assert "builder baseline commit: builder-old-sha" in completed.stdout
    assert "domain-chip baseline commit: chip-old-sha" in completed.stdout


def test_memory_validation_wrapper_full_run_updates_pointers_delta_and_docs_with_fake_cli(tmp_path: Path) -> None:
    real_builder_root = Path(__file__).resolve().parents[1]
    real_chip_root = real_builder_root.parent / "domain-chip-memory"

    workspace_root = tmp_path / "workspace"
    builder_root = workspace_root / "spark-intelligence-builder"
    chip_root = workspace_root / "domain-chip-memory"
    spark_home = tmp_path / "spark-home"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    _copy_file(
        real_builder_root / "scripts" / "run_memory_two_contender_validation.ps1",
        builder_root / "scripts" / "run_memory_two_contender_validation.ps1",
    )
    _copy_file(
        real_builder_root / "scripts" / "render_memory_failure_ledger.py",
        builder_root / "scripts" / "render_memory_failure_ledger.py",
    )
    _copy_file(
        real_builder_root / "scripts" / "render_memory_baseline_docs.py",
        builder_root / "scripts" / "render_memory_baseline_docs.py",
    )
    _copy_file(
        real_builder_root / "scripts" / "render_memory_validation_delta.py",
        builder_root / "scripts" / "render_memory_validation_delta.py",
    )
    _copy_file(
        real_chip_root / "scripts" / "render_builder_baseline_docs.py",
        chip_root / "scripts" / "render_builder_baseline_docs.py",
    )

    _write_text(
        builder_root / "README.md",
        "\n".join(
            [
                "prefix",
                "<!-- AUTO_MEMORY_BASELINE_README_START -->",
                "old",
                "<!-- AUTO_MEMORY_BASELINE_README_END -->",
                "suffix",
            ]
        ),
    )
    _write_text(
        builder_root / "docs" / "MEMORY_LIVE_VALIDATION_RESULTS_2026-04-11.md",
        "\n".join(
            [
                "prefix",
                "<!-- AUTO_MEMORY_BASELINE_LIVE_RESULTS_START -->",
                "old",
                "<!-- AUTO_MEMORY_BASELINE_LIVE_RESULTS_END -->",
                "suffix",
            ]
        ),
    )
    _write_text(
        builder_root / "docs" / "MEMORY_BENCHMARK_HANDOFF_2026-04-11.md",
        "\n".join(
            [
                "prefix",
                "<!-- AUTO_MEMORY_BASELINE_HANDOFF_START -->",
                "old",
                "<!-- AUTO_MEMORY_BASELINE_HANDOFF_END -->",
                "suffix",
            ]
        ),
    )
    _write_text(
        chip_root / "README.md",
        "\n".join(
            [
                "prefix",
                "<!-- AUTO_BUILDER_BASELINE_README_START -->",
                "old",
                "<!-- AUTO_BUILDER_BASELINE_README_END -->",
                "suffix",
            ]
        ),
    )
    _write_text(
        chip_root / "docs" / "NEXT_PHASE_SPARK_MEMORY_KB_BENCHMARK_PROGRAM_2026-04-10.md",
        "\n".join(
            [
                "prefix",
                "<!-- AUTO_BUILDER_BASELINE_NEXT_PHASE_START -->",
                "old",
                "<!-- AUTO_BUILDER_BASELINE_NEXT_PHASE_END -->",
                "suffix",
            ]
        ),
    )
    _write_text(
        chip_root / "docs" / "CURRENT_STATUS_BENCHMARKS_AND_KB_2026-04-09.md",
        "\n".join(
            [
                "prefix",
                "<!-- AUTO_BUILDER_BASELINE_CURRENT_STATUS_START -->",
                "old",
                "<!-- AUTO_BUILDER_BASELINE_CURRENT_STATUS_END -->",
                "suffix",
            ]
        ),
    )

    fake_cli_py = bin_dir / "fake_spark_intelligence.py"
    _write_text(
        fake_cli_py,
        """
from __future__ import annotations

import json
import sys
from pathlib import Path


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


args = sys.argv[1:]
output_dir = Path(args[args.index("--output-dir") + 1])
output_dir.mkdir(parents=True, exist_ok=True)
command = tuple(args[:2])

if command == ("memory", "benchmark-architectures"):
    _write_json(
        output_dir / "memory-architecture-benchmark.json",
        {
            "summary": {
                "runtime_memory_architecture": "summary_synthesis_memory",
                "product_memory_leader_names": [
                    "summary_synthesis_memory",
                    "dual_store_event_calendar_hybrid",
                ],
            }
        },
    )
elif command == ("memory", "run-telegram-regression"):
    _write_json(
        output_dir / "telegram-memory-regression.json",
        {
            "summary": {
                "matched_case_count": 34,
                "case_count": 34,
                "live_architecture_leaders": ["summary_synthesis_memory"],
                "kb_has_probe_coverage": True,
                "kb_current_state_hits": 38,
                "kb_current_state_total": 38,
                "kb_evidence_hits": 38,
                "kb_evidence_total": 38,
                "mismatched_case_ids": [],
            }
        },
    )
elif command == ("memory", "soak-architectures"):
    _write_json(
        output_dir / "telegram-memory-architecture-soak.json",
        {
            "summary": {
                "completed_runs": 14,
                "requested_runs": 14,
                "failed_runs": 0,
                "overall_leader_names": ["summary_synthesis_memory"],
                "recommended_top_two": [
                    "summary_synthesis_memory",
                    "dual_store_event_calendar_hybrid",
                ],
                "selector_packs_requiring_work": [],
            },
            "aggregate_results": [
                {"baseline_name": "summary_synthesis_memory", "matched": 92, "total": 92},
                {"baseline_name": "dual_store_event_calendar_hybrid", "matched": 89, "total": 92},
            ],
            "selection_aggregate_results": [
                {"baseline_name": "summary_synthesis_memory", "matched": 64, "total": 64},
                {"baseline_name": "dual_store_event_calendar_hybrid", "matched": 61, "total": 64},
            ],
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
else:
    raise SystemExit(f"unexpected args: {args}")
""".strip(),
    )
    fake_cli_cmd = bin_dir / "spark-intelligence.cmd"
    _write_text(
        fake_cli_cmd,
        f'@echo off\r\n"{Path(subprocess.list2cmdline([Path(os.sys.executable)]).strip(chr(34)))}" "%~dp0fake_spark_intelligence.py" %*\r\n',
    )

    validation_runs_root = spark_home / "artifacts" / "memory-validation-runs"
    prior_run_root = validation_runs_root / "20260412-000000"
    prior_benchmark_dir = prior_run_root / "memory-architecture-benchmark"
    prior_regression_dir = prior_run_root / "telegram-memory-regression"
    prior_soak_dir = prior_run_root / "telegram-memory-architecture-soak"
    prior_benchmark_dir.mkdir(parents=True)
    prior_regression_dir.mkdir()
    prior_soak_dir.mkdir()
    prior_run_summary_path = prior_run_root / "run-summary.json"
    prior_run_summary_path.write_text(
        json.dumps(
            {
                "output_root": str(prior_run_root),
                "builder_repo_commit": "prior-builder-sha",
                "domain_chip_repo_commit": "prior-chip-sha",
                "benchmark_duration_seconds": 13.153,
                "regression_duration_seconds": 25.528,
                "soak_duration_seconds": 350.805,
                "total_duration_seconds": 389.672,
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
                "benchmark_output_dir": str(prior_benchmark_dir),
                "regression_output_dir": str(prior_regression_dir),
                "soak_output_dir": str(prior_soak_dir),
            }
        ),
        encoding="utf-8",
    )
    (prior_regression_dir / "telegram-memory-regression.json").write_text(
        json.dumps({"summary": {"mismatched_case_ids": []}}),
        encoding="utf-8",
    )
    (prior_soak_dir / "telegram-memory-architecture-soak.json").write_text(
        json.dumps(
            {
                "summary": {
                    "completed_runs": 14,
                    "requested_runs": 14,
                    "failed_runs": 0,
                    "overall_leader_names": ["summary_synthesis_memory"],
                    "recommended_top_two": [
                        "summary_synthesis_memory",
                        "dual_store_event_calendar_hybrid",
                    ],
                    "selector_packs_requiring_work": [],
                },
                "aggregate_results": [
                    {"baseline_name": "summary_synthesis_memory", "matched": 92, "total": 92},
                    {"baseline_name": "dual_store_event_calendar_hybrid", "matched": 89, "total": 92},
                ],
                "selection_aggregate_results": [
                    {"baseline_name": "summary_synthesis_memory", "matched": 64, "total": 64},
                    {"baseline_name": "dual_store_event_calendar_hybrid", "matched": 61, "total": 64},
                ],
                "benchmark_pack_results": [
                    {
                        "pack_id": "temporal_conflict_gauntlet",
                        "selection_role": "selector",
                        "leader_names": ["summary_synthesis_memory"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    latest_full_run_path = validation_runs_root / "latest-full-run.json"
    latest_full_run_path.parent.mkdir(parents=True, exist_ok=True)
    latest_full_run_path.write_text(
        json.dumps(
            {
                "output_root": str(prior_run_root),
                "run_summary": str(prior_run_summary_path),
            }
        ),
        encoding="utf-8",
    )

    env = os.environ.copy()
    env["PATH"] = str(bin_dir) + os.pathsep + env["PATH"]

    completed = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(builder_root / "scripts" / "run_memory_two_contender_validation.ps1"),
            "-SparkHome",
            str(spark_home),
        ],
        cwd=builder_root,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert "Validation verdict:" in completed.stdout
    assert "live regression: 34/34" in completed.stdout
    assert "live soak: 14/14 completed" in completed.stdout

    latest_run = json.loads((validation_runs_root / "latest-run.json").read_text(encoding="utf-8-sig"))
    latest_full_run = json.loads(latest_full_run_path.read_text(encoding="utf-8-sig"))
    previous_full_run = json.loads((validation_runs_root / "previous-full-run.json").read_text(encoding="utf-8-sig"))
    assert latest_full_run["output_root"] == latest_run["output_root"]
    assert previous_full_run["output_root"] == str(prior_run_root)

    run_summary = json.loads(Path(latest_full_run["run_summary"]).read_text(encoding="utf-8-sig"))
    assert run_summary["offline_runtime_architecture"] == "summary_synthesis_memory"
    assert run_summary["live_regression"] == "34/34"
    assert run_summary["live_soak_completion"] == "14/14"
    assert run_summary["live_soak_leaders"] == ["summary_synthesis_memory"]

    output_root = Path(run_summary["output_root"])
    assert (output_root / "validation-delta.md").exists()

    ledger_text = (builder_root / "docs" / "MEMORY_FAILURE_LEDGER_2026-04-11.md").read_text(encoding="utf-8")
    assert "- open live mismatches: `0`" in ledger_text

    builder_readme = (builder_root / "README.md").read_text(encoding="utf-8")
    assert output_root.name in builder_readme

    chip_readme = (chip_root / "README.md").read_text(encoding="utf-8")
    assert output_root.name in chip_readme


def test_memory_validation_wrapper_full_run_succeeds_without_chip_repo(tmp_path: Path) -> None:
    real_builder_root = Path(__file__).resolve().parents[1]

    workspace_root = tmp_path / "workspace"
    builder_root = workspace_root / "spark-intelligence-builder"
    spark_home = tmp_path / "spark-home"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    _copy_file(
        real_builder_root / "scripts" / "run_memory_two_contender_validation.ps1",
        builder_root / "scripts" / "run_memory_two_contender_validation.ps1",
    )
    _copy_file(
        real_builder_root / "scripts" / "render_memory_failure_ledger.py",
        builder_root / "scripts" / "render_memory_failure_ledger.py",
    )
    _copy_file(
        real_builder_root / "scripts" / "render_memory_baseline_docs.py",
        builder_root / "scripts" / "render_memory_baseline_docs.py",
    )
    _copy_file(
        real_builder_root / "scripts" / "render_memory_validation_delta.py",
        builder_root / "scripts" / "render_memory_validation_delta.py",
    )

    _write_text(
        builder_root / "README.md",
        "\n".join(
            [
                "prefix",
                "<!-- AUTO_MEMORY_BASELINE_README_START -->",
                "old",
                "<!-- AUTO_MEMORY_BASELINE_README_END -->",
                "suffix",
            ]
        ),
    )
    _write_text(
        builder_root / "docs" / "MEMORY_LIVE_VALIDATION_RESULTS_2026-04-11.md",
        "\n".join(
            [
                "prefix",
                "<!-- AUTO_MEMORY_BASELINE_LIVE_RESULTS_START -->",
                "old",
                "<!-- AUTO_MEMORY_BASELINE_LIVE_RESULTS_END -->",
                "suffix",
            ]
        ),
    )
    _write_text(
        builder_root / "docs" / "MEMORY_BENCHMARK_HANDOFF_2026-04-11.md",
        "\n".join(
            [
                "prefix",
                "<!-- AUTO_MEMORY_BASELINE_HANDOFF_START -->",
                "old",
                "<!-- AUTO_MEMORY_BASELINE_HANDOFF_END -->",
                "suffix",
            ]
        ),
    )

    fake_cli_py = bin_dir / "fake_spark_intelligence.py"
    _write_text(
        fake_cli_py,
        """
from __future__ import annotations

import json
import sys
from pathlib import Path


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


args = sys.argv[1:]
output_dir = Path(args[args.index("--output-dir") + 1])
output_dir.mkdir(parents=True, exist_ok=True)
command = tuple(args[:2])

if command == ("memory", "benchmark-architectures"):
    _write_json(
        output_dir / "memory-architecture-benchmark.json",
        {
            "summary": {
                "runtime_memory_architecture": "summary_synthesis_memory",
                "product_memory_leader_names": [
                    "summary_synthesis_memory",
                    "dual_store_event_calendar_hybrid",
                ],
            }
        },
    )
elif command == ("memory", "run-telegram-regression"):
    _write_json(
        output_dir / "telegram-memory-regression.json",
        {
            "summary": {
                "matched_case_count": 34,
                "case_count": 34,
                "live_architecture_leaders": ["summary_synthesis_memory"],
                "kb_has_probe_coverage": True,
                "kb_current_state_hits": 38,
                "kb_current_state_total": 38,
                "kb_evidence_hits": 38,
                "kb_evidence_total": 38,
                "mismatched_case_ids": [],
            }
        },
    )
elif command == ("memory", "soak-architectures"):
    _write_json(
        output_dir / "telegram-memory-architecture-soak.json",
        {
            "summary": {
                "completed_runs": 14,
                "requested_runs": 14,
                "failed_runs": 0,
                "overall_leader_names": ["summary_synthesis_memory"],
                "recommended_top_two": [
                    "summary_synthesis_memory",
                    "dual_store_event_calendar_hybrid",
                ],
                "selector_packs_requiring_work": [],
            },
            "aggregate_results": [
                {"baseline_name": "summary_synthesis_memory", "matched": 92, "total": 92},
                {"baseline_name": "dual_store_event_calendar_hybrid", "matched": 89, "total": 92},
            ],
            "selection_aggregate_results": [
                {"baseline_name": "summary_synthesis_memory", "matched": 64, "total": 64},
                {"baseline_name": "dual_store_event_calendar_hybrid", "matched": 61, "total": 64},
            ],
            "benchmark_pack_results": [
                {
                    "pack_id": "temporal_conflict_gauntlet",
                    "selection_role": "selector",
                    "leader_names": ["summary_synthesis_memory"],
                }
            ],
        },
    )
else:
    raise SystemExit(f"unexpected args: {args}")
""".strip(),
    )
    fake_cli_cmd = bin_dir / "spark-intelligence.cmd"
    _write_text(
        fake_cli_cmd,
        f'@echo off\r\n"{Path(subprocess.list2cmdline([Path(os.sys.executable)]).strip(chr(34)))}" "%~dp0fake_spark_intelligence.py" %*\r\n',
    )

    env = os.environ.copy()
    env["PATH"] = str(bin_dir) + os.pathsep + env["PATH"]

    completed = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(builder_root / "scripts" / "run_memory_two_contender_validation.ps1"),
            "-SparkHome",
            str(spark_home),
        ],
        cwd=builder_root,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr or completed.stdout
    assert "chip baseline docs:" not in completed.stdout

    validation_runs_root = spark_home / "artifacts" / "memory-validation-runs"
    latest_full_run = json.loads((validation_runs_root / "latest-full-run.json").read_text(encoding="utf-8-sig"))
    run_summary = json.loads(Path(latest_full_run["run_summary"]).read_text(encoding="utf-8-sig"))
    output_root = Path(run_summary["output_root"])
    assert (output_root / "validation-delta.md").exists()
    assert (builder_root / "docs" / "MEMORY_FAILURE_LEDGER_2026-04-11.md").exists()

    builder_readme = (builder_root / "README.md").read_text(encoding="utf-8")
    assert output_root.name in builder_readme
