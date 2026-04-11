from __future__ import annotations

import json
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
