from types import SimpleNamespace

from spark_intelligence.loops import runner


def test_run_chip_autoloop_fails_when_all_candidate_evaluations_error(monkeypatch, tmp_path):
    def fake_run_chip_hook(config_manager, *, chip_key, hook, payload, governor_decision):
        if hook == "suggest":
            return SimpleNamespace(
                ok=True,
                output={"suggestions": [{"mutations": {"axis": "one"}}]},
                exit_code=0,
                stderr="",
            )
        return SimpleNamespace(
            ok=False,
            output={},
            exit_code=1,
            stderr="candidate evaluation crashed",
        )

    monkeypatch.setattr(runner, "run_chip_hook", fake_run_chip_hook)

    result = runner.run_chip_autoloop(
        config_manager=object(),
        chip_key="domain-chip-test",
        rounds=1,
        suggest_limit=1,
        artifacts_root=tmp_path,
        suggest_governor_decision={"allowed": True},
        evaluate_governor_decision={"allowed": True},
    )

    assert result.ok is False
    assert result.rounds_completed == 0
    assert "evaluate failed for all 1 candidate" in (result.error or "")
    assert result.history[0]["best_metric"] is None
    assert (tmp_path / "domain-chip-test.status.json").exists()


def test_run_chip_autoloop_preserves_useful_evaluate_error_tail(monkeypatch, tmp_path):
    long_stderr = "x" * 2400 + "actual traceback tail"

    def fake_run_chip_hook(config_manager, *, chip_key, hook, payload, governor_decision):
        if hook == "suggest":
            return SimpleNamespace(
                ok=True,
                output={"suggestions": [{"mutations": {"axis": "one"}}]},
                exit_code=0,
                stdout="",
                stderr="",
            )
        return SimpleNamespace(
            ok=False,
            output={},
            exit_code=1,
            stdout="helpful stdout tail",
            stderr=long_stderr,
        )

    monkeypatch.setattr(runner, "run_chip_hook", fake_run_chip_hook)

    result = runner.run_chip_autoloop(
        config_manager=object(),
        chip_key="domain-chip-test",
        rounds=1,
        suggest_limit=1,
        artifacts_root=tmp_path,
        suggest_governor_decision={"allowed": True},
        evaluate_governor_decision={"allowed": True},
    )

    assert result.ok is False
    error = result.history[0]["evaluations"][0]["error"]
    assert "actual traceback tail" in error
    assert "helpful stdout tail" in error
    assert len(error) < 2300


def test_run_chip_autoloop_reads_nested_verdict_and_arbitrary_metric(monkeypatch, tmp_path):
    def fake_run_chip_hook(config_manager, *, chip_key, hook, payload, governor_decision):
        if hook == "suggest":
            return SimpleNamespace(
                ok=True,
                output={"suggestions": [{"mutations": {"axis": "one"}}]},
                exit_code=0,
                stderr="",
            )
        return SimpleNamespace(
            ok=True,
            output={
                "metrics": {"coverage_breadth": 0.3, "approval_ready_safety_score": 58},
                "result": {"verdict": "defer"},
            },
            exit_code=0,
            stderr="",
        )

    monkeypatch.setattr(runner, "run_chip_hook", fake_run_chip_hook)

    result = runner.run_chip_autoloop(
        config_manager=object(),
        chip_key="domain-chip-test",
        rounds=1,
        suggest_limit=1,
        artifacts_root=tmp_path,
        suggest_governor_decision={"allowed": True},
        evaluate_governor_decision={"allowed": True},
    )

    assert result.ok is True
    assert result.rounds_completed == 1
    assert result.history[0]["best_verdict"] == "defer"
    assert result.history[0]["best_metric"] == 58.0


def test_run_chip_autoloop_passes_recent_mutations_to_suggest(monkeypatch, tmp_path):
    suggest_payloads = []

    def fake_run_chip_hook(config_manager, *, chip_key, hook, payload, governor_decision):
        if hook == "suggest":
            suggest_payloads.append(payload)
            recent = payload.get("recent_mutations") or []
            if not recent:
                suggestion = {"mutations": {"axis": "first"}}
            else:
                suggestion = {"mutations": {"axis": "second"}}
            return SimpleNamespace(
                ok=True,
                output={"suggestions": [suggestion]},
                exit_code=0,
                stderr="",
            )
        metric = 51 if payload["candidate"]["mutations"]["axis"] == "first" else 63
        return SimpleNamespace(
            ok=True,
            output={"metrics": {"quality_score": metric}, "result": {"verdict": "defer"}},
            exit_code=0,
            stderr="",
        )

    monkeypatch.setattr(runner, "run_chip_hook", fake_run_chip_hook)

    result = runner.run_chip_autoloop(
        config_manager=object(),
        chip_key="domain-chip-test",
        rounds=2,
        suggest_limit=1,
        artifacts_root=tmp_path,
        suggest_governor_decision={"allowed": True},
        evaluate_governor_decision={"allowed": True},
    )

    assert result.ok is True
    assert suggest_payloads[0]["recent_mutations"] == []
    assert suggest_payloads[1]["recent_mutations"][0]["mutations"] == {"axis": "first"}
    assert result.history[1]["evaluations"][0]["candidate"]["mutations"] == {"axis": "second"}
    assert result.history[1]["best_metric"] == 63.0


def test_run_chip_autoloop_binds_loop_evidence_to_chip_reports(monkeypatch, tmp_path):
    chip_root = tmp_path / "domain-chip-test"
    reports = chip_root / "reports"
    reports.mkdir(parents=True)
    (reports / "long-loop-trend.json").write_text("{}", encoding="utf-8")
    (reports / "qa-evidence-lane-packet.json").write_text("{}", encoding="utf-8")
    (reports / "proof-capsule-starter.json").write_text("{}", encoding="utf-8")

    def fake_resolve_chip_record(config_manager, *, chip_key):
        return SimpleNamespace(repo_root=str(chip_root))

    def fake_run_chip_hook(config_manager, *, chip_key, hook, payload, governor_decision):
        if hook == "suggest":
            return SimpleNamespace(
                ok=True,
                output={"suggestions": [{"mutations": {"axis": "one"}}]},
                exit_code=0,
                stderr="",
            )
        return SimpleNamespace(
            ok=True,
            output={"metrics": {"quality_score": 57}, "result": {"verdict": "defer"}},
            exit_code=0,
            stderr="",
        )

    monkeypatch.setattr(runner, "resolve_chip_record", fake_resolve_chip_record)
    monkeypatch.setattr(runner, "run_chip_hook", fake_run_chip_hook)

    result = runner.run_chip_autoloop(
        config_manager=object(),
        chip_key="domain-chip-test",
        rounds=1,
        suggest_limit=1,
        artifacts_root=tmp_path / "loops",
        suggest_governor_decision={"allowed": True},
        evaluate_governor_decision={"allowed": True},
    )

    assert result.ok is True
    evidence = runner._read_json_dict(reports / "loop-runner-evidence.json")
    assert evidence["schema_version"] == "spark-domain-chip.loop_runner_evidence.v1"
    assert evidence["best_metric_by_round"] == [57.0]
    assert evidence["eval_errors_by_round"] == [0]
    assert evidence["promotion_blocked"] is True
    assert evidence["network_absorbable"] is False
    assert "self_improvement_proven" in evidence["claims_not_supported"]

    long_loop = runner._read_json_dict(reports / "long-loop-trend.json")
    assert long_loop["loop_runner_binding"]["evidence_ref"] == "reports/loop-runner-evidence.json"
    assert long_loop["loop_runner_binding"]["self_improvement_proven"] is False
    qa_packet = runner._read_json_dict(reports / "qa-evidence-lane-packet.json")
    assert qa_packet["loop_runner_evidence"]["candidate_trend_only"] is True
    proof = runner._read_json_dict(reports / "proof-capsule-starter.json")
    assert proof["proof"]["loop_runner_evidence"]["status"] == "candidate_trend_bound"
