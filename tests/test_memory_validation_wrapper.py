from __future__ import annotations

import json
import subprocess
from pathlib import Path


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
