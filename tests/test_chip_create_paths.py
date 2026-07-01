import json
import os
import subprocess
import sys
from collections import Counter
from types import SimpleNamespace

from spark_intelligence.chip_create.pipeline import (
    create_chip_from_prompt,
    _default_chip_labs_root,
    _default_output_dir,
    _patch_generated_cli,
)
from spark_intelligence.cli import _authorize_cli_chip_create, _chips_create_command_receipt_context
from spark_intelligence.bridge_authority import authorize_builder_bridge_action
from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.harness_contract import (
    build_vnext_action_intent_envelope,
    verify_governor_tool_authority,
)
from spark_intelligence.state.db import StateDB


def _chip_create_governor(*, tool_name: str = "chip.create") -> dict:
    request_id = f"req-{tool_name.replace('.', '-')}"
    envelope = build_vnext_action_intent_envelope(
        surface="cli",
        actor_id_ref="human-chip-create-test",
        request_id=request_id,
        source_kind="test_chip_create_governor",
        intent_summary=f"Test authorizes {tool_name}.",
        raw_turn_summary="Raw chip-create test turn is offloaded.",
        actions=[
            {
                "tool_name": tool_name,
                "owner_system": "spark-intelligence-builder",
                "mutation_class": "creates_chip",
                "args_path": f"builder://chip-create/{request_id}/{tool_name}",
            }
        ],
    )
    authority = authorize_builder_bridge_action(
        {"turn_intent_envelope_vnext": envelope},
        tool_name=tool_name,
        owner_system="spark-intelligence-builder",
        mutation_class="creates_chip",
        request_id=request_id,
        actor_id="test",
        component="test_chip_create_paths",
    )
    assert authority.allowed, authority.reason_codes
    assert isinstance(authority.governor_decision, dict)
    return authority.governor_decision


def _create_pull_request_chip(monkeypatch, tmp_path):
    missing_root = tmp_path / "missing-chip-labs"
    brief = {
        "domain_id": "pull-request-risk-review",
        "domain_name": "Pull Request Risk Review",
        "description": "Help reviewers identify risky pull requests with private evidence.",
        "category": "analysis",
        "primary_metric": "risk_review_quality_score",
        "mutation_axes": [
            {"name": "risk_focus", "values": ["security", "tests", "data"]},
            {"name": "review_depth", "values": ["fast", "standard", "deep"]},
        ],
        "task_topics": ["pull_request_review", "risk_review"],
        "task_keywords": ["pull", "request", "review", "risk"],
        "combine_with": [],
    }
    monkeypatch.setattr(
        "spark_intelligence.auth.runtime.resolve_runtime_provider",
        lambda **_: SimpleNamespace(secret_value="dummy-secret", provider_id="test"),
    )
    monkeypatch.setattr(
        "spark_intelligence.chip_create.pipeline._parse_brief_via_llm",
        lambda prompt, *, provider, state_db=None: brief,
    )
    result = create_chip_from_prompt(
        prompt="build a domain chip for pull request risk review",
        config_manager=None,
        state_db=None,
        chip_labs_root=missing_root,
        output_dir=tmp_path / "out",
        governor_decision=_chip_create_governor(),
    )
    assert result.ok is True
    return tmp_path / "out" / "domain-chip-pull-request-risk-review"


def _write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n",
        encoding="utf-8",
    )


def _run_chip(chip_path, command, *args):
    completed = subprocess.run(
        [sys.executable, "chip-runner.py", command, *args],
        cwd=chip_path,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    return completed


def test_chip_create_paths_use_explicit_environment_overrides(monkeypatch, tmp_path):
    chip_labs_root = tmp_path / "configured-chip-labs"
    output_dir = tmp_path / "configured-output"
    monkeypatch.setenv("CHIP_LABS_ROOT", str(chip_labs_root))
    monkeypatch.setenv("CHIP_CREATE_OUTPUT_DIR", str(output_dir))

    assert _default_chip_labs_root() == chip_labs_root
    assert _default_output_dir() == output_dir


def test_chip_create_paths_fall_back_to_current_working_directory(monkeypatch, tmp_path):
    monkeypatch.delenv("CHIP_LABS_ROOT", raising=False)
    monkeypatch.delenv("CHIP_CREATE_OUTPUT_DIR", raising=False)
    monkeypatch.chdir(tmp_path)

    assert _default_chip_labs_root() == tmp_path / "spark-domain-chip-labs"
    assert _default_output_dir() == tmp_path


def test_chip_create_path_defaults_do_not_use_publishing_machine_paths(
    monkeypatch, tmp_path
):
    monkeypatch.delenv("CHIP_LABS_ROOT", raising=False)
    monkeypatch.delenv("CHIP_CREATE_OUTPUT_DIR", raising=False)
    monkeypatch.chdir(tmp_path)

    defaults = (_default_chip_labs_root(), _default_output_dir())

    assert all(
        not str(path).replace("\\", "/").startswith("C:/Users/USER/Desktop")
        for path in defaults
    )


def test_cli_chip_create_fallback_governor_matches_pipeline_consumer():
    governor_decision, reasons = _authorize_cli_chip_create(
        state_db=None,
        request_id="req-domain-chip-create",
        prompt="build a domain chip for pull request risk review",
    )

    assert reasons == ()
    verification = verify_governor_tool_authority(
        governor_decision,
        tool_name="chip.create",
        owner_system="spark-intelligence-builder",
        mutation_class="creates_chip",
        require_pre_execution_ledger=True,
    )
    assert verification["allowed"] is True
    assert verification["outcome"] == "execute"


def test_cli_chip_create_receipt_context_redacts_prompt_and_governor_json():
    context = _chips_create_command_receipt_context(
        SimpleNamespace(
            home="/Users/example/private-home",
            prompt="build a domain chip for a private customer list",
            output_dir="/Users/example/private-chips",
            chip_labs_root="/Users/example/private-domain-chip-labs",
            governor_decision_json='{"secret":"raw-governor-json"}',
            json=True,
        )
    )

    assert context["command_source"] == "spark_intelligence.cli chips create"
    assert "--governor-decision-json" in context["flags_present"]
    assert "[redacted-json]" in context["argv_shape"]
    assert "[redacted-prompt]" in context["argv_shape"]
    assert "private customer list" not in json.dumps(context)
    assert "raw-governor-json" not in json.dumps(context)


def test_generated_chip_runner_evaluate_accepts_loop_candidate_payload(monkeypatch, tmp_path):
    chip_path = _create_pull_request_chip(monkeypatch, tmp_path)
    payload_path = chip_path / "reports" / "loop-candidate-input.json"
    output_path = chip_path / "reports" / "loop-candidate-evaluate.json"
    _write_json(
        payload_path,
        {
            "round": 1,
            "candidate": {
                "mutations": {"risk_focus": "security", "review_depth": "deep"},
            },
        },
    )

    completed = subprocess.run(
        [
            sys.executable,
            "chip-runner.py",
            "evaluate",
            "--input",
            str(payload_path),
            "--output",
            str(output_path),
        ],
        cwd=chip_path,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert report["schema_version"] == "spark-domain-chip.hook_evaluate_result.v1"
    assert report["promotion_blocked"] is True
    assert report["network_absorbable"] is False
    assert isinstance(report.get("metrics"), dict)
    assert report["result"]["verdict"] in {"approve", "defer", "reject"}


def test_create_chip_from_prompt_blocks_without_governor_before_scaffold(tmp_path):
    missing_root = tmp_path / "missing-chip-labs"

    result = create_chip_from_prompt(
        prompt="create a founder-retention chip",
        config_manager=None,
        state_db=None,
        chip_labs_root=missing_root,
        output_dir=tmp_path / "out",
    )

    assert result.ok is False
    assert "requires Harness Core Governor authority" in str(result.error)
    assert result.governor_verification is not None
    assert result.governor_verification["allowed"] is False
    assert "missing_governor_decision" in result.governor_verification["reason_codes"]
    assert not missing_root.exists()


def test_create_chip_from_prompt_blocks_wrong_governor_tool_before_scaffold(tmp_path):
    missing_root = tmp_path / "missing-chip-labs"

    result = create_chip_from_prompt(
        prompt="create a founder-retention chip",
        config_manager=None,
        state_db=None,
        chip_labs_root=missing_root,
        output_dir=tmp_path / "out",
        governor_decision=_chip_create_governor(tool_name="chip.evaluate"),
    )

    assert result.ok is False
    assert result.governor_verification is not None
    assert result.governor_verification["allowed"] is False
    assert "governor_missing_matching_authorization" in result.governor_verification["reason_codes"]
    assert not missing_root.exists()


def test_create_chip_from_prompt_accepts_chip_create_governor_before_next_validation(tmp_path):
    chip_labs_root = tmp_path / "chip-labs"
    chip_labs_root.mkdir()

    result = create_chip_from_prompt(
        prompt="create a founder-retention chip",
        config_manager=None,
        state_db=None,
        chip_labs_root=chip_labs_root,
        output_dir=tmp_path / "out",
        governor_decision=_chip_create_governor(),
    )

    assert result.ok is False
    assert result.governor_verification is not None
    assert result.governor_verification["allowed"] is True
    assert result.governor_verification["tool_name"] == "chip.create"
    assert str(result.error).startswith("chip_labs import failed:")


def test_patch_generated_cli_does_not_embed_local_chip_labs_path(tmp_path):
    chip_dir = tmp_path / "domain-chip-path-hygiene"
    package_dir = chip_dir / "src" / "path_hygiene"
    package_dir.mkdir(parents=True)
    cli_path = package_dir / "cli.py"
    cli_path.write_text(
        '''"""Generated cli."""
from __future__ import annotations

from ..lab_hooks import (
    generate_packets,
    generate_watchtower_pages,
    run_evaluate,
    run_suggest,
)
''',
        encoding="utf-8",
    )
    chip_labs_root = tmp_path / "very-private" / "spark-domain-chip-labs"

    _patch_generated_cli(chip_dir, chip_labs_root)

    patched = cli_path.read_text(encoding="utf-8")
    assert str(chip_labs_root) not in patched
    assert "_os.environ.get('CHIP_LABS_SRC')" in patched
    assert "except ModuleNotFoundError" in patched
    assert "from .evaluate import evaluate as run_evaluate" in patched


def test_create_chip_from_prompt_uses_builtin_starter_when_chip_labs_root_missing(
    monkeypatch, tmp_path
):
    missing_root = tmp_path / "missing-chip-labs"
    brief = {
        "domain_id": "pull-request-risk-review",
        "domain_name": "Pull Request Risk Review",
        "description": "Help reviewers identify risky pull requests with private evidence.",
        "category": "analysis",
        "primary_metric": "risk_review_quality_score",
        "mutation_axes": [
            {"name": "risk_focus", "values": ["security", "tests", "data"]},
            {"name": "review_depth", "values": ["fast", "standard", "deep"]},
        ],
        "task_topics": ["pull_request_review", "risk_review"],
        "task_keywords": ["pull", "request", "review", "risk"],
        "combine_with": [],
    }
    monkeypatch.setattr(
        "spark_intelligence.auth.runtime.resolve_runtime_provider",
        lambda **_: SimpleNamespace(secret_value="dummy-secret", provider_id="test"),
    )
    monkeypatch.setattr(
        "spark_intelligence.chip_create.pipeline._parse_brief_via_llm",
        lambda prompt, *, provider, state_db=None: brief,
    )

    result = create_chip_from_prompt(
        prompt="build a domain chip for pull request risk review",
        config_manager=None,
        state_db=None,
        chip_labs_root=missing_root,
        output_dir=tmp_path / "out",
        governor_decision=_chip_create_governor(),
    )

    assert result.ok is True
    assert result.chip_key == "domain-chip-pull-request-risk-review"
    assert any("built-in starter scaffold" in warning for warning in result.warnings)
    assert any(str(missing_root) in warning for warning in result.warnings)
    chip_path = tmp_path / "out" / "domain-chip-pull-request-risk-review"
    assert (chip_path / "spark-chip.json").exists()
    assert (chip_path / "src" / "pull_request_risk_review" / "cli.py").exists()
    assert (chip_path / "benchmark" / "evaluate-run-contract.json").exists()
    proof_artifacts = result.to_dict()["proof_artifacts"]
    assert proof_artifacts["evaluate_run_contract"] is True
    assert proof_artifacts["promotion_blocked"] is True
    assert proof_artifacts["network_absorbable"] is False


def test_create_chip_from_prompt_uses_local_starter_brief_without_provider(tmp_path):
    home = tmp_path / "spark-intelligence-home"
    config_manager = ConfigManager.from_home(str(home))
    config_manager.bootstrap()
    state_db = StateDB(config_manager.paths.state_db)
    state_db.initialize()

    result = create_chip_from_prompt(
        prompt="build a domain chip for pull request risk review",
        config_manager=config_manager,
        state_db=state_db,
        chip_labs_root=tmp_path / "missing-chip-labs",
        output_dir=tmp_path / "out",
        governor_decision=_chip_create_governor(),
    )

    assert result.ok is True
    assert result.chip_key == "domain-chip-pull-request-risk-review"
    assert result.brief is not None
    assert result.brief["domain_name"] == "Pull Request Risk Review"
    assert any("local starter brief" in warning for warning in result.warnings)
    proof_artifacts = result.to_dict()["proof_artifacts"]
    assert proof_artifacts["promotion_blocked"] is True
    assert "no_positive_score_delta" in proof_artifacts["qa_evidence_lane_blockers"]


def test_create_chip_from_prompt_names_service_env_provider_gap(
    monkeypatch, tmp_path
):
    home = tmp_path / "spark-intelligence-home"
    config_manager = ConfigManager.from_home(str(home))
    config_manager.bootstrap()
    state_db = StateDB(config_manager.paths.state_db)
    state_db.initialize()
    monkeypatch.setenv("SPARK_LLM_PROVIDER", "codex")
    monkeypatch.setenv("SPARK_LLM_AUTH_MODE", "codex_oauth")
    monkeypatch.setenv("SPARK_LLM_MODEL", "gpt-5.5")

    result = create_chip_from_prompt(
        prompt="build a domain chip for pull request risk review",
        config_manager=config_manager,
        state_db=state_db,
        chip_labs_root=tmp_path / "missing-chip-labs",
        output_dir=tmp_path / "out",
        governor_decision=_chip_create_governor(),
    )

    assert result.ok is True
    assert any("no internal provider record" in warning for warning in result.warnings)
    assert any("provider=codex" in warning for warning in result.warnings)
    assert not any(
        warning.startswith("No Builder provider is configured")
        for warning in result.warnings
    )


def test_create_chip_from_prompt_service_env_warning_prefers_builder_role(
    monkeypatch, tmp_path
):
    home = tmp_path / "spark-intelligence-home"
    config_manager = ConfigManager.from_home(str(home))
    config_manager.bootstrap()
    state_db = StateDB(config_manager.paths.state_db)
    state_db.initialize()
    monkeypatch.setenv("SPARK_LLM_PROVIDER", "codex")
    monkeypatch.setenv("SPARK_LLM_MODEL", "gpt-5.5")
    monkeypatch.setenv("SPARK_BUILDER_LLM_PROVIDER", "openai")
    monkeypatch.setenv("SPARK_BUILDER_LLM_AUTH_MODE", "api_key")
    monkeypatch.setenv("SPARK_BUILDER_LLM_MODEL", "gpt-5.5")

    result = create_chip_from_prompt(
        prompt="build a domain chip for pull request risk review",
        config_manager=config_manager,
        state_db=state_db,
        chip_labs_root=tmp_path / "missing-chip-labs",
        output_dir=tmp_path / "out",
        governor_decision=_chip_create_governor(),
    )

    warning = next(
        warning
        for warning in result.warnings
        if "no internal provider record" in warning
    )
    assert "provider=openai" in warning
    assert "auth_mode=api_key" in warning
    assert "provider=codex" not in warning


def test_create_chip_from_prompt_uses_supported_direct_provider_brief_parse(
    monkeypatch, tmp_path
):
    home = tmp_path / "spark-intelligence-home"
    config_manager = ConfigManager.from_home(str(home))
    config_manager.bootstrap()
    state_db = StateDB(config_manager.paths.state_db)
    state_db.initialize()
    brief = {
        "domain_id": "supplier-risk-triage",
        "domain_name": "Supplier Risk Triage",
        "description": "Help operators triage supplier risk with private evidence.",
        "category": "analysis",
        "primary_metric": "supplier_risk_triage_quality_score",
        "mutation_axes": [
            {"name": "risk_focus", "values": ["financial", "security", "delivery"]},
            {"name": "review_depth", "values": ["fast", "standard", "deep"]},
        ],
        "task_topics": [
            "supplier_risk",
            "risk_triage",
            "vendor_review",
            "financial_risk",
            "delivery_risk",
        ],
        "task_keywords": [
            "supplier",
            "vendor",
            "risk",
            "triage",
            "review",
            "financial",
            "security",
            "delivery",
            "evidence",
            "approval",
        ],
        "combine_with": [],
    }
    provider = SimpleNamespace(
        provider_id="openai",
        provider_kind="openai",
        auth_method="api_key_env",
        api_mode="chat_completions",
        execution_transport="direct_http",
        base_url="https://api.example.test/v1",
        default_model="gpt-5.5",
        secret_value="test-secret",
    )
    captured: dict[str, object] = {}

    def fake_execute_direct_provider_prompt(
        *, provider, system_prompt, user_prompt, governance=None
    ):
        captured["provider_id"] = provider.provider_id
        captured["api_mode"] = provider.api_mode
        captured["system_prompt"] = system_prompt
        captured["user_prompt"] = user_prompt
        captured["governance"] = governance
        return {"raw_response": json.dumps(brief)}

    monkeypatch.setattr(
        "spark_intelligence.auth.runtime.resolve_runtime_provider",
        lambda **_: provider,
    )
    monkeypatch.setattr(
        "spark_intelligence.llm.direct_provider.execute_direct_provider_prompt",
        fake_execute_direct_provider_prompt,
    )

    result = create_chip_from_prompt(
        prompt="build a domain chip for supplier risk triage",
        config_manager=config_manager,
        state_db=state_db,
        chip_labs_root=tmp_path / "missing-chip-labs",
        output_dir=tmp_path / "out",
        governor_decision=_chip_create_governor(),
    )

    assert result.ok is True
    assert result.chip_key == "domain-chip-supplier-risk-triage"
    assert result.brief == brief
    assert captured["provider_id"] == "openai"
    assert captured["api_mode"] == "chat_completions"
    assert "Return the JSON brief" in str(captured["user_prompt"])
    governance = captured["governance"]
    assert governance is not None
    assert governance.state_db_path == str(state_db.path)
    assert governance.source_kind == "chip_create_brief_prompt"
    assert governance.reason_code == "chip_create_brief_prompt_secret_like"
    assert governance.policy_domain == "domain_chip_create"
    assert not any("local starter brief" in warning for warning in result.warnings)


def test_create_chip_from_prompt_uses_codex_external_wrapper_bridge(
    monkeypatch, tmp_path
):
    home = tmp_path / "spark-intelligence-home"
    config_manager = ConfigManager.from_home(str(home))
    config_manager.bootstrap()
    state_db = StateDB(config_manager.paths.state_db)
    state_db.initialize()
    provider = SimpleNamespace(
        provider_id="openai-codex",
        provider_kind="openai-codex",
        auth_method="oauth",
        api_mode="codex_responses",
        execution_transport="external_cli_wrapper",
        base_url="https://chatgpt.com/backend-api/codex",
        default_model="gpt-5.5",
        secret_value="oauth-token",
    )
    brief = {
        "domain_id": "vendor-compliance-intake",
        "domain_name": "Vendor Compliance Intake",
        "description": "Help operators intake vendor compliance requests with private evidence.",
        "category": "operations",
        "primary_metric": "vendor_compliance_intake_quality_score",
        "mutation_axes": [
            {"name": "risk_focus", "values": ["security", "privacy", "finance"]},
            {"name": "review_depth", "values": ["fast", "standard", "deep"]},
        ],
        "task_topics": [
            "vendor_compliance",
            "compliance_intake",
            "risk_review",
            "document_review",
            "approval_routing",
        ],
        "task_keywords": [
            "vendor",
            "compliance",
            "intake",
            "risk",
            "review",
            "approval",
            "evidence",
            "policy",
            "contract",
            "questionnaire",
        ],
        "combine_with": [],
    }
    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):
        captured["command"] = list(command)
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(brief),
            stderr="",
        )

    monkeypatch.setattr(
        "spark_intelligence.auth.runtime.resolve_runtime_provider",
        lambda **_: provider,
    )
    monkeypatch.setattr(
        "spark_intelligence.chip_create.pipeline.shutil.which",
        lambda name: "/usr/local/bin/codex" if name == "codex" else None,
    )
    monkeypatch.setattr(
        "spark_intelligence.chip_create.pipeline.subprocess.run",
        fake_run,
    )

    result = create_chip_from_prompt(
        prompt="build a domain chip for vendor compliance intake",
        config_manager=config_manager,
        state_db=state_db,
        chip_labs_root=tmp_path / "missing-chip-labs",
        output_dir=tmp_path / "out",
        governor_decision=_chip_create_governor(),
    )

    assert result.ok is True
    assert result.chip_key == "domain-chip-vendor-compliance-intake"
    command = captured["command"]
    assert command[:2] == ["/usr/local/bin/codex", "exec"]
    assert "--skip-git-repo-check" in command
    assert "--ignore-user-config" in command
    assert "--ignore-rules" in command
    assert "--ephemeral" in command
    assert command[command.index("--sandbox") + 1] == "read-only"
    assert command[command.index("--model") + 1] == "gpt-5.5"
    assert "--output-schema" in command
    assert captured["kwargs"]["cwd"] != str(tmp_path.home())
    assert "HOME" not in captured["kwargs"]["env"]
    assert "USER" not in captured["kwargs"]["env"]
    assert "SHELL" not in captured["kwargs"]["env"]
    assert "CODEX_HOME" in captured["kwargs"]["env"]
    assert str(captured["kwargs"]["input"]).count("vendor compliance intake") >= 1
    assert "Return the JSON brief" in str(captured["kwargs"]["input"])
    assert captured["kwargs"]["timeout"] == 90
    assert not any("local starter brief" in warning for warning in result.warnings)


def test_create_chip_from_prompt_uses_builder_role_codex_env_without_provider_records(
    monkeypatch, tmp_path
):
    home = tmp_path / "spark-intelligence-home"
    config_manager = ConfigManager.from_home(str(home))
    config_manager.bootstrap()
    state_db = StateDB(config_manager.paths.state_db)
    state_db.initialize()
    monkeypatch.setenv("SPARK_BUILDER_LLM_PROVIDER", "codex")
    monkeypatch.setenv("SPARK_BUILDER_LLM_AUTH_MODE", "codex_oauth")
    monkeypatch.setenv("SPARK_BUILDER_LLM_MODEL", "gpt-5.5")
    brief = {
        "domain_id": "procurement-exception-routing",
        "domain_name": "Procurement Exception Routing",
        "description": "Help operators route procurement exceptions with private evidence.",
        "category": "operations",
        "primary_metric": "exception_resolution_score",
        "mutation_axes": [
            {"name": "exception_type", "values": ["budget", "vendor", "policy"]},
            {"name": "review_depth", "values": ["fast", "standard", "deep"]},
        ],
        "task_topics": [
            "procurement_exception",
            "exception_routing",
            "vendor_review",
            "policy_review",
            "approval_workflow",
        ],
        "task_keywords": [
            "procurement",
            "exception",
            "routing",
            "vendor",
            "policy",
            "approval",
            "budget",
            "review",
            "evidence",
            "workflow",
        ],
        "combine_with": [],
    }
    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):
        captured["command"] = list(command)
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(brief), stderr="")

    monkeypatch.setattr(
        "spark_intelligence.chip_create.pipeline.shutil.which",
        lambda name: "/usr/local/bin/codex" if name == "codex" else None,
    )
    monkeypatch.setattr(
        "spark_intelligence.chip_create.pipeline.subprocess.run",
        fake_run,
    )

    result = create_chip_from_prompt(
        prompt="build a domain chip for procurement exception routing",
        config_manager=config_manager,
        state_db=state_db,
        chip_labs_root=tmp_path / "missing-chip-labs",
        output_dir=tmp_path / "out",
        governor_decision=_chip_create_governor(),
    )

    assert result.ok is True
    assert result.chip_key == "domain-chip-procurement-exception-routing"
    assert captured["command"][:2] == ["/usr/local/bin/codex", "exec"]
    assert result.brief == brief
    assert not any("local starter brief" in warning for warning in result.warnings)
    assert not any("no internal provider record" in warning for warning in result.warnings)


def test_create_chip_from_prompt_fails_closed_when_codex_bridge_missing(
    monkeypatch, tmp_path
):
    provider = SimpleNamespace(
        provider_id="openai-codex",
        provider_kind="openai-codex",
        auth_method="oauth",
        api_mode="codex_responses",
        execution_transport="external_cli_wrapper",
        base_url="https://chatgpt.com/backend-api/codex",
        default_model="gpt-5.5",
        secret_value="",
    )
    monkeypatch.setattr(
        "spark_intelligence.auth.runtime.resolve_runtime_provider",
        lambda **_: provider,
    )
    monkeypatch.setattr(
        "spark_intelligence.chip_create.pipeline.shutil.which",
        lambda name: None,
    )

    result = create_chip_from_prompt(
        prompt="build a domain chip for vendor compliance intake",
        config_manager=None,
        state_db=None,
        chip_labs_root=tmp_path / "missing-chip-labs",
        output_dir=tmp_path / "out",
        governor_decision=_chip_create_governor(),
    )

    assert result.ok is False
    assert result.error == "provider execution failed: codex_cli_missing"
    assert not (tmp_path / "out" / "domain-chip-vendor-compliance-intake").exists()


def test_create_chip_from_prompt_fails_closed_on_codex_bridge_invalid_json(
    monkeypatch, tmp_path
):
    provider = SimpleNamespace(
        provider_id="openai-codex",
        provider_kind="openai-codex",
        auth_method="oauth",
        api_mode="codex_responses",
        execution_transport="external_cli_wrapper",
        base_url="https://chatgpt.com/backend-api/codex",
        default_model="gpt-5.5",
        secret_value="oauth-token",
    )
    monkeypatch.setattr(
        "spark_intelligence.auth.runtime.resolve_runtime_provider",
        lambda **_: provider,
    )
    monkeypatch.setattr(
        "spark_intelligence.chip_create.pipeline.shutil.which",
        lambda name: "/usr/local/bin/codex" if name == "codex" else None,
    )
    monkeypatch.setattr(
        "spark_intelligence.chip_create.pipeline.subprocess.run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command,
            0,
            stdout="I would build a chip for that.",
            stderr="",
        ),
    )

    result = create_chip_from_prompt(
        prompt="build a domain chip for vendor compliance intake",
        config_manager=None,
        state_db=None,
        chip_labs_root=tmp_path / "missing-chip-labs",
        output_dir=tmp_path / "out",
        governor_decision=_chip_create_governor(),
    )

    assert result.ok is False
    assert result.error == "provider execution failed: provider_brief_invalid_json"
    assert not (tmp_path / "out" / "domain-chip-vendor-compliance-intake").exists()


def test_create_chip_from_prompt_fails_closed_on_codex_bridge_non_object_json(
    monkeypatch, tmp_path
):
    provider = SimpleNamespace(
        provider_id="openai-codex",
        provider_kind="openai-codex",
        auth_method="oauth",
        api_mode="codex_responses",
        execution_transport="external_cli_wrapper",
        base_url="https://chatgpt.com/backend-api/codex",
        default_model="gpt-5.5",
        secret_value="oauth-token",
    )
    monkeypatch.setattr(
        "spark_intelligence.auth.runtime.resolve_runtime_provider",
        lambda **_: provider,
    )
    monkeypatch.setattr(
        "spark_intelligence.chip_create.pipeline.shutil.which",
        lambda name: "/usr/local/bin/codex" if name == "codex" else None,
    )
    monkeypatch.setattr(
        "spark_intelligence.chip_create.pipeline.subprocess.run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command,
            0,
            stdout="[]",
            stderr="",
        ),
    )

    result = create_chip_from_prompt(
        prompt="build a domain chip for vendor compliance intake",
        config_manager=None,
        state_db=None,
        chip_labs_root=tmp_path / "missing-chip-labs",
        output_dir=tmp_path / "out",
        governor_decision=_chip_create_governor(),
    )

    assert result.ok is False
    assert result.error == "provider execution failed: provider_brief_json_not_object"
    assert not (tmp_path / "out" / "domain-chip-vendor-compliance-intake").exists()


def test_create_chip_from_prompt_fails_closed_on_codex_bridge_schema_invalid(
    monkeypatch, tmp_path
):
    provider = SimpleNamespace(
        provider_id="openai-codex",
        provider_kind="openai-codex",
        auth_method="oauth",
        api_mode="codex_responses",
        execution_transport="external_cli_wrapper",
        base_url="https://chatgpt.com/backend-api/codex",
        default_model="gpt-5.5",
        secret_value="oauth-token",
    )
    invalid_brief = {
        "domain_id": "vendor-compliance-intake",
        "domain_name": "Vendor Compliance Intake",
        "description": "Help operators intake vendor compliance requests with private evidence.",
        "category": "operations",
        "primary_metric": "vendor_compliance_intake_quality_score",
        "mutation_axes": [
            {"name": "risk_focus", "values": ["security", "privacy", "finance"]},
            {"name": "review_depth", "values": ["fast", "standard", "deep"]},
        ],
        "task_topics": ["vendor_compliance"],
        "task_keywords": ["vendor"],
        "combine_with": [],
    }
    monkeypatch.setattr(
        "spark_intelligence.auth.runtime.resolve_runtime_provider",
        lambda **_: provider,
    )
    monkeypatch.setattr(
        "spark_intelligence.chip_create.pipeline.shutil.which",
        lambda name: "/usr/local/bin/codex" if name == "codex" else None,
    )
    monkeypatch.setattr(
        "spark_intelligence.chip_create.pipeline.subprocess.run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(invalid_brief),
            stderr="",
        ),
    )

    result = create_chip_from_prompt(
        prompt="build a domain chip for vendor compliance intake",
        config_manager=None,
        state_db=None,
        chip_labs_root=tmp_path / "missing-chip-labs",
        output_dir=tmp_path / "out",
        governor_decision=_chip_create_governor(),
    )

    assert result.ok is False
    assert result.error == "provider execution failed: provider_brief_schema_invalid"
    assert not (tmp_path / "out" / "domain-chip-vendor-compliance-intake").exists()


def test_create_chip_from_prompt_rejects_codex_bridge_extra_or_mistyped_fields(
    monkeypatch, tmp_path
):
    provider = SimpleNamespace(
        provider_id="openai-codex",
        provider_kind="openai-codex",
        auth_method="oauth",
        api_mode="codex_responses",
        execution_transport="external_cli_wrapper",
        base_url="https://chatgpt.com/backend-api/codex",
        default_model="gpt-5.5",
        secret_value="oauth-token",
    )
    invalid_brief = {
        "domain_id": 123,
        "domain_name": "Vendor Compliance Intake",
        "category": "operations",
        "description": "Help operators intake vendor compliance requests with private evidence.",
        "primary_metric": "vendor_compliance_intake_quality_score",
        "mutation_axes": [
            {"name": "risk_focus", "values": ["security", "privacy", "finance"]},
            {"name": "review_depth", "values": ["fast", "standard", "deep"]},
        ],
        "task_topics": [
            "vendor_compliance",
            "compliance_intake",
            "risk_review",
            "document_review",
            "approval_routing",
        ],
        "task_keywords": [
            "vendor",
            "compliance",
            "intake",
            "risk",
            "review",
            "approval",
            "evidence",
            "policy",
            "contract",
            "questionnaire",
        ],
        "combine_with": [],
        "unexpected": "not allowed",
    }
    monkeypatch.setattr(
        "spark_intelligence.auth.runtime.resolve_runtime_provider",
        lambda **_: provider,
    )
    monkeypatch.setattr(
        "spark_intelligence.chip_create.pipeline.shutil.which",
        lambda name: "/usr/local/bin/codex" if name == "codex" else None,
    )
    monkeypatch.setattr(
        "spark_intelligence.chip_create.pipeline.subprocess.run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(invalid_brief),
            stderr="",
        ),
    )

    result = create_chip_from_prompt(
        prompt="build a domain chip for vendor compliance intake",
        config_manager=None,
        state_db=None,
        chip_labs_root=tmp_path / "missing-chip-labs",
        output_dir=tmp_path / "out",
        governor_decision=_chip_create_governor(),
    )

    assert result.ok is False
    assert result.error == "provider execution failed: provider_brief_schema_invalid"
    assert not (tmp_path / "out" / "domain-chip-vendor-compliance-intake").exists()


def test_create_chip_from_prompt_local_starter_uses_named_chip_without_brief_line(tmp_path):
    home = tmp_path / "spark-intelligence-home"
    config_manager = ConfigManager.from_home(str(home))
    config_manager.bootstrap()
    state_db = StateDB(config_manager.paths.state_db)
    state_db.initialize()

    result = create_chip_from_prompt(
        prompt="Create a Spark domain chip named domain-chip-customer-escalation-readiness-review.",
        config_manager=config_manager,
        state_db=state_db,
        chip_labs_root=tmp_path / "missing-chip-labs",
        output_dir=tmp_path / "out",
        governor_decision=_chip_create_governor(),
    )

    assert result.ok is True
    assert result.chip_key == "domain-chip-customer-escalation-readiness-review"
    assert result.brief is not None
    assert result.brief["domain_name"] == "Customer Escalation Readiness Review"
    assert "spark" not in result.brief["task_keywords"]
    assert "named" not in result.brief["task_keywords"]


def test_create_chip_from_prompt_local_starter_stops_named_chip_at_sentence(tmp_path):
    home = tmp_path / "spark-intelligence-home"
    config_manager = ConfigManager.from_home(str(home))
    config_manager.bootstrap()
    state_db = StateDB(config_manager.paths.state_db)
    state_db.initialize()

    result = create_chip_from_prompt(
        prompt=(
            "Create a Spark domain chip named domain-chip-customer-escalation-readiness-review. "
            "Build this as a complete private Domain Chip starter kit with benchmark cases, "
            "held-out traps, watchtower, rollback, and no publication claim."
        ),
        config_manager=config_manager,
        state_db=state_db,
        chip_labs_root=tmp_path / "missing-chip-labs",
        output_dir=tmp_path / "out",
        governor_decision=_chip_create_governor(),
    )

    assert result.ok is True
    assert result.chip_key == "domain-chip-customer-escalation-readiness-review"
    assert result.brief is not None
    assert result.brief["domain_name"] == "Customer Escalation Readiness Review"
    assert "complete" not in result.brief["task_keywords"]
    assert "kit" not in result.brief["task_keywords"]
    assert "held" not in result.brief["task_keywords"]


def test_create_chip_from_prompt_local_starter_ignores_prd_wrapper_words(tmp_path):
    home = tmp_path / "spark-intelligence-home"
    config_manager = ConfigManager.from_home(str(home))
    config_manager.bootstrap()
    state_db = StateDB(config_manager.paths.state_db)
    state_db.initialize()
    prompt = """Create a Spark domain chip named domain-chip-api-migration-risk-review.

Natural-language chip brief: API migration risk review

Build this as a complete private Domain Chip starter kit, not a generic PRD.

Required starter kit:
- domain-chip/manifest.json
- benchmark/manifest.json
"""

    result = create_chip_from_prompt(
        prompt=prompt,
        config_manager=config_manager,
        state_db=state_db,
        chip_labs_root=tmp_path / "missing-chip-labs",
        output_dir=tmp_path / "out",
        governor_decision=_chip_create_governor(),
    )

    assert result.ok is True
    assert result.chip_key == "domain-chip-api-migration-risk-review"
    assert result.brief is not None
    assert result.brief["domain_name"] == "API Migration Risk Review"
    assert "spark" not in result.brief["task_keywords"]
    assert "named" not in result.brief["task_keywords"]
    assert "natural" not in result.brief["task_keywords"]
    assert "starter" not in result.brief["task_keywords"]


def test_create_chip_from_prompt_scaffold_emits_loop_proof_starter_assets(
    monkeypatch, tmp_path
):
    chip_labs_root = tmp_path / "chip-labs"
    scaffold_pkg = chip_labs_root / "src" / "chip_labs" / "chip_factory"
    scaffold_pkg.mkdir(parents=True)
    (chip_labs_root / "src" / "chip_labs" / "__init__.py").write_text(
        "", encoding="utf-8"
    )
    scaffold_pkg.joinpath("__init__.py").write_text("", encoding="utf-8")
    scaffold_pkg.joinpath("scaffold.py").write_text(
        """
import json
from pathlib import Path


def scaffold_chip(brief, output_dir):
    chip_dir = Path(output_dir) / f"domain-chip-{brief['domain_id']}"
    module = brief["domain_id"].replace("-", "_")
    package_dir = chip_dir / "src" / module
    package_dir.mkdir(parents=True)
    package_dir.joinpath("__init__.py").write_text("", encoding="utf-8")
    package_dir.joinpath("cli.py").write_text("def main():\\n    return None\\n", encoding="utf-8")
    tests_dir = chip_dir / "tests"
    tests_dir.mkdir(parents=True)
    tests_dir.joinpath("test_import.py").write_text(
        "import " + module + "\\n\\n"
        "def test_import_source_package():\\n"
        "    assert " + module + ".__name__ == " + repr(module) + "\\n",
        encoding="utf-8",
    )
    manifest = {
        "schema_version": "spark-chip.v1",
        "name": brief["domain_name"],
        "commands": {
            "evaluate": "python -m " + module + " evaluate --input {input} --output {output}"
        },
    }
    chip_dir.joinpath("spark-chip.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    chip_dir.joinpath("README.md").write_text(
        "# domain-chip-" + brief["domain_id"] + "\\n\\n"
        "## Quick Start\\n\\n"
        "```bash\\n"
        "python -m " + module + " evaluate --input input.json --output output.json\\n"
        "```\\n\\n"
        "## Primary Metric\\n\\n"
        + brief["primary_metric"] + "\\n",
        encoding="utf-8",
    )
    return str(chip_dir)
""",
        encoding="utf-8",
    )

    brief = {
        "domain_id": "pull-request-risk-review",
        "domain_name": "Pull Request Risk Review",
        "description": "Help reviewers identify risky pull requests with private evidence.",
        "category": "analysis",
        "primary_metric": "risk_review_quality_score",
        "mutation_axes": [
            {"name": "risk_focus", "values": ["security", "tests", "data"]},
            {"name": "review_depth", "values": ["fast", "standard", "deep"]},
        ],
        "task_topics": ["pull_request_review", "risk_review"],
        "task_keywords": ["pull", "request", "review", "risk"],
        "combine_with": [],
    }

    monkeypatch.setattr(
        "spark_intelligence.auth.runtime.resolve_runtime_provider",
        lambda **_: SimpleNamespace(secret_value="dummy-secret", provider_id="test"),
    )
    monkeypatch.setattr(
        "spark_intelligence.chip_create.pipeline._parse_brief_via_llm",
        lambda prompt, *, provider, state_db=None: brief,
    )

    governor = _chip_create_governor()
    result = create_chip_from_prompt(
        prompt="build a domain chip for pull request risk review",
        config_manager=None,
        state_db=None,
        chip_labs_root=chip_labs_root,
        output_dir=tmp_path / "out",
        governor_decision=governor,
        command_receipt_context=_chips_create_command_receipt_context(
            SimpleNamespace(
                home=str(tmp_path / "home"),
                prompt="build a domain chip for pull request risk review",
                output_dir=str(tmp_path / "out"),
                chip_labs_root=str(chip_labs_root),
                governor_decision_json=json.dumps(governor),
                json=True,
            )
        ),
    )

    assert result.ok is True
    chip_path = tmp_path / "out" / "domain-chip-pull-request-risk-review"
    expected_files = [
        "creator-intent.json",
        "adapter-map.json",
        "created-artifact-manifest.json",
        "chip-runner.py",
        "domain/contract.json",
        "domain/triggers.json",
        "domain/playbook.md",
        "domain/examples.jsonl",
        "domain/hook-contract.json",
        "domain-chip/manifest.json",
        "domain-chip/hooks/contract.json",
        "domain-chip/triggers.json",
        "domain-chip/non-triggers.json",
        "domain-chip/playbook.md",
        "domain-chip/examples.jsonl",
        "activation/notes.md",
        "benchmark/manifest.json",
        "benchmark/cases.jsonl",
        "benchmark/traps.jsonl",
        "benchmark/scoring_rubric.md",
        "benchmark/evaluate-run-contract.json",
        "benchmark/chip-benefit-ab-contract.json",
        "benchmark/pull-request-risk-review-sample-input.json",
        "distilled-runtime/pull-request-risk-review-fast-path.json",
        "autoloop/policy.json",
        "autoloop/long-loop-trend-contract.json",
        "autoloop/mutation_surface.md",
        "autoloop/stop_conditions.md",
        "reports/proof-capsule-starter.json",
        "reports/qa-evidence-lane-packet.json",
        "reports/evidence_ladder.md",
        "reports/review_packet.md",
        "reports/human-onboarding-rubric.md",
        "reports/consumer-agent-transcript.md",
        "reports/chip-benefit-ab.json",
        "reports/long-loop-trend.json",
        "reports/scorecard-starter.json",
        "reports/hard-blockers.json",
        "reports/promotion-decision.json",
        "reports/forbidden-claims.json",
        "tests/conftest.py",
    ]
    for rel in expected_files:
        assert (chip_path / rel).exists(), rel

    pytest_env = {key: value for key, value in os.environ.items() if key != "PYTHONPATH"}
    pytest_run = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "tests"],
        cwd=chip_path,
        env=pytest_env,
        capture_output=True,
        text=True,
    )
    assert pytest_run.returncode == 0, pytest_run.stdout + pytest_run.stderr

    readme = (chip_path / "README.md").read_text(encoding="utf-8")
    assert "## What This Helps You Do" in readme
    assert "python3 chip-runner.py evaluate --input benchmark/cases.jsonl" in readme
    assert "python -m pull_request_risk_review evaluate" not in readme

    benchmark_manifest = json.loads(
        (chip_path / "benchmark/manifest.json").read_text(encoding="utf-8")
    )
    assert benchmark_manifest["score_field"] == "risk_review_quality_score"
    assert (
        benchmark_manifest["promotion_rules"]["presence_only_files_block_promotion"]
        is True
    )

    autoloop_policy = json.loads(
        (chip_path / "autoloop/policy.json").read_text(encoding="utf-8")
    )
    assert autoloop_policy["network_publication_allowed"] is False
    assert "rollback" in autoloop_policy["rollback_condition"].lower()
    assert (
        autoloop_policy["distilled_runtime_contract"]
        == "distilled-runtime/pull-request-risk-review-fast-path.json"
    )

    qa_packet = json.loads(
        (chip_path / "reports/qa-evidence-lane-packet.json").read_text(
            encoding="utf-8"
        )
    )
    assert qa_packet["scenario"] == "domain_chip_quality"
    assert qa_packet["metadata"]["baseline"] is True
    assert qa_packet["metadata"]["score_delta"] == 0.0
    assert qa_packet["metadata"]["promotion_blocked"] is True
    assert qa_packet["metadata"]["proof_capsule"]["promotion_tier"] == "candidate_review"
    assert qa_packet["metadata"]["proof_capsule"]["network_absorbable"] is False
    assert qa_packet["metadata"]["evidence_scope_current"] is True
    assert (
        qa_packet["metadata"]["current_chip_ref"]
        == "domain-chip-pull-request-risk-review/spark-chip.json"
    )
    assert (
        qa_packet["metadata"]["proof_chip_ref"]
        == qa_packet["metadata"]["current_chip_ref"]
    )
    assert (
        qa_packet["metadata"]["current_run_id"]
        == "domain-chip-pull-request-risk-review"
    )
    assert qa_packet["metadata"]["proof_run_id"] == qa_packet["metadata"]["current_run_id"]
    assert (
        qa_packet["metadata"]["proof_capsule"]["current_chip_ref"]
        == qa_packet["metadata"]["current_chip_ref"]
    )
    assert "transfer_supported" not in qa_packet["candidate_response"]
    assert "reports/evidence_ladder.md" in qa_packet["evidence_sources"]
    assert "reports/review_packet.md" in qa_packet["evidence_sources"]

    evidence_ladder = (chip_path / "reports/evidence_ladder.md").read_text(
        encoding="utf-8"
    )
    assert "Long-loop trend" in evidence_ladder
    assert "blocked, 0 of 5 rounds observed" in evidence_ladder
    assert "Do not claim quality" in evidence_ladder

    review_packet = (chip_path / "reports/review_packet.md").read_text(
        encoding="utf-8"
    )
    assert "What This Chip Is" in review_packet
    assert "What Is Not Proven" in review_packet
    assert "five-round self-improvement loop" in review_packet

    creator_intent = json.loads(
        (chip_path / "creator-intent.json").read_text(encoding="utf-8")
    )
    assert creator_intent["domain_id"] == "pull-request-risk-review"
    assert creator_intent["privacy_boundary"] == "private_local_only"
    assert creator_intent["network_absorbable"] is False
    assert "quality_improved" in creator_intent["forbidden_claims"]

    adapter_map = json.loads(
        (chip_path / "adapter-map.json").read_text(encoding="utf-8")
    )
    assert adapter_map["adapters"]["domain_contract"] == "domain/contract.json"
    assert adapter_map["adapters"]["domain_chip_manifest"] == "domain-chip/manifest.json"
    assert (
        adapter_map["adapters"]["domain_chip_hook_contract"]
        == "domain-chip/hooks/contract.json"
    )
    assert adapter_map["adapters"]["benchmark_pack"] == "benchmark/manifest.json"
    assert adapter_map["adapters"]["evaluate_run_contract"] == "benchmark/evaluate-run-contract.json"
    assert adapter_map["adapters"]["autoloop_policy"] == "autoloop/policy.json"
    assert (
        adapter_map["adapters"]["distilled_runtime_contract"]
        == "distilled-runtime/pull-request-risk-review-fast-path.json"
    )
    assert adapter_map["adapters"]["qa_evidence_lane_packet"] == "reports/qa-evidence-lane-packet.json"
    assert adapter_map["adapters"]["evidence_ladder"] == "reports/evidence_ladder.md"
    assert adapter_map["adapters"]["review_packet"] == "reports/review_packet.md"

    dcl_manifest = json.loads(
        (chip_path / "domain-chip/manifest.json").read_text(encoding="utf-8")
    )
    assert dcl_manifest["schema_version"] == "spark-domain-chip.manifest.v1"
    assert dcl_manifest["runtime_manifest_ref"] == "spark-chip.json"
    assert dcl_manifest["hook_contract_ref"] == "domain-chip/hooks/contract.json"
    assert dcl_manifest["benchmark_ref"] == "benchmark/manifest.json"
    assert dcl_manifest["autoloop_ref"] == "autoloop/policy.json"
    assert (
        dcl_manifest["distilled_runtime_contract_ref"]
        == "distilled-runtime/pull-request-risk-review-fast-path.json"
    )
    assert dcl_manifest["proof_capsule_ref"] == "reports/proof-capsule-starter.json"
    assert dcl_manifest["promotion_tier"] == "candidate_review"
    assert dcl_manifest["network_absorbable"] is False
    assert dcl_manifest["promotion_blocked"] is True

    dcl_hook_contract = json.loads(
        (chip_path / "domain-chip/hooks/contract.json").read_text(encoding="utf-8")
    )
    assert dcl_hook_contract["schema_version"] == "spark-domain-chip.hook_contract.v1"
    assert dcl_hook_contract["domain_chip_manifest_ref"] == "domain-chip/manifest.json"
    assert dcl_hook_contract["router_invocation_required_before_live"] is True
    assert dcl_hook_contract["network_absorbable"] is False

    artifact_manifest = json.loads(
        (chip_path / "created-artifact-manifest.json").read_text(encoding="utf-8")
    )
    assert artifact_manifest["network_absorbable"] is False
    assert artifact_manifest["promotion_blocked"] is True
    manifest_paths = {artifact["path"] for artifact in artifact_manifest["artifacts"]}
    for rel in expected_files:
        assert rel in manifest_paths
    status_by_path = {
        artifact["path"]: artifact["status"]
        for artifact in artifact_manifest["artifacts"]
    }
    exists_by_path = {
        artifact["path"]: artifact["exists"]
        for artifact in artifact_manifest["artifacts"]
    }
    assert status_by_path["benchmark/manifest.json"] == "present_unverified"
    assert status_by_path["distilled-runtime/pull-request-risk-review-fast-path.json"] == "blocked"
    assert status_by_path["reports/proof-capsule-starter.json"] == "review_only"
    assert status_by_path["activation/notes.md"] == "review_only"
    for rel, exists in exists_by_path.items():
        assert exists == (chip_path / rel).exists(), rel

    hard_blockers = json.loads(
        (chip_path / "reports/hard-blockers.json").read_text(encoding="utf-8")
    )
    assert "no_positive_score_delta" in hard_blockers["hard_blockers"]
    assert "chip_benefit_ab_missing" in hard_blockers["hard_blockers"]
    assert "long_loop_trend_missing" in hard_blockers["hard_blockers"]
    assert "consumer_transfer_not_claimed" in hard_blockers["hard_blockers"]

    forbidden_claims = json.loads(
        (chip_path / "reports/forbidden-claims.json").read_text(encoding="utf-8")
    )
    assert "quality_improved" in forbidden_claims["forbidden_claims"]
    assert "network_absorbable" in forbidden_claims["forbidden_claims"]

    scorecard = json.loads(
        (chip_path / "reports/scorecard-starter.json").read_text(encoding="utf-8")
    )
    assert scorecard["starter_only"] is True
    assert scorecard["promotion_blocked"] is True
    assert scorecard["scores"]["baseline"] == 0.5
    assert scorecard["scores"]["candidate"] == 0.5
    assert scorecard["scores"]["delta"] == 0.0
    assert "reports/score-delta.json" in scorecard["evidence_refs"]
    assert "reports/chip-benefit-ab.json" in scorecard["evidence_refs"]
    assert "reports/long-loop-trend.json" in scorecard["evidence_refs"]

    distilled_runtime = json.loads(
        (
            chip_path / "distilled-runtime/pull-request-risk-review-fast-path.json"
        ).read_text(encoding="utf-8")
    )
    assert (
        distilled_runtime["schema_version"]
        == "spark-domain-chip.distilled_runtime_contract.v1"
    )
    assert distilled_runtime["runtime_state"] == "blocked_until_proven"
    assert distilled_runtime["telegram_first"] is True
    assert distilled_runtime["route_before_generic_ideation"] is True
    assert distilled_runtime["runtime_modes"]["loop_mode"]["allowed_now"] is True
    assert (
        "same_budget_no_chip_vs_chip_assisted_ab_win_or_judge_approved_no_safe_win"
        in distilled_runtime["required_proof_before_runtime"]
    )
    assert (
        distilled_runtime["source_loop_refs"]["chip_benefit_ab_contract"]
        == "benchmark/chip-benefit-ab-contract.json"
    )
    assert (
        distilled_runtime["source_loop_refs"]["long_loop_trend_contract"]
        == "autoloop/long-loop-trend-contract.json"
    )
    assert (
        distilled_runtime["source_loop_refs"]["sealed_evaluation_contract"]
        == "benchmark/sealed-evaluation-contract.json"
    )
    assert (
        distilled_runtime["source_loop_refs"]["watchtower_regression_plan"]
        == "autoloop/watchtower-regression.json"
    )
    assert (
        distilled_runtime["source_loop_refs"]["rollback_plan"]
        == "autoloop/rollback-plan.json"
    )
    assert (
        distilled_runtime["source_loop_refs"]["proof_auditor_report"]
        == "reports/proof-auditor-check.json"
    )
    assert (
        distilled_runtime["source_loop_refs"]["operator_approval_packet"]
        == "reports/operator-review-packet.json"
    )
    assert "claim_network_absorption" in distilled_runtime["blocked_actions"]
    assert "record_fast_path_used" in distilled_runtime["telemetry_requirements"]
    assert qa_packet["metadata"]["distilled_runtime"]["runtime_state"] == "blocked_until_proven"

    promotion_decision = json.loads(
        (chip_path / "reports/promotion-decision.json").read_text(encoding="utf-8")
    )
    assert promotion_decision["decision"] == "blocked"
    assert promotion_decision["promotion_tier"] == "candidate_review"
    assert promotion_decision["network_absorbable"] is False

    transcript = (
        chip_path / "reports/consumer-agent-transcript.md"
    ).read_text(encoding="utf-8")
    assert "Consumer transfer is not claimed" in transcript
    assert "without creator notes" in transcript

    contract = json.loads(
        (chip_path / "domain/contract.json").read_text(encoding="utf-8")
    )
    assert contract["purpose"] == "Help reviewers identify risky pull requests with private evidence."
    assert contract["privacy_boundary"] == "private_local_only"
    assert contract["activation_state"] == "not_live"
    assert contract["quality_claim_boundary"] == "starter_artifact_only"

    triggers = json.loads(
        (chip_path / "domain/triggers.json").read_text(encoding="utf-8")
    )
    assert "pull_request_review" in triggers["task_topics"]
    assert "risk_review" in triggers["task_topics"]
    assert len(triggers["triggers"]) >= 3
    assert len(triggers["non_triggers"]) >= 3
    assert triggers["route_drift_guard"] == "fresh intent and trigger evidence required"

    examples = [
        json.loads(line)
        for line in (chip_path / "domain/examples.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    assert {example["lane"] for example in examples} == {
        "good",
        "bad",
        "edge",
        "no_op",
        "adversarial",
    }

    hook_contract = json.loads(
        (chip_path / "domain/hook-contract.json").read_text(encoding="utf-8")
    )
    assert hook_contract["manifest_ref"] == "spark-chip.json"
    assert hook_contract["router_invocation_required_before_live"] is True

    activation_notes = (chip_path / "activation/notes.md").read_text(encoding="utf-8")
    assert "not live" in activation_notes.lower()
    assert "unrelated-route fallthrough" in activation_notes


def test_create_chip_from_prompt_scaffold_emits_executed_starter_benchmark_smoke(
    monkeypatch, tmp_path
):
    chip_labs_root = tmp_path / "chip-labs"
    scaffold_pkg = chip_labs_root / "src" / "chip_labs" / "chip_factory"
    scaffold_pkg.mkdir(parents=True)
    (chip_labs_root / "src" / "chip_labs" / "__init__.py").write_text(
        "", encoding="utf-8"
    )
    scaffold_pkg.joinpath("__init__.py").write_text("", encoding="utf-8")
    scaffold_pkg.joinpath("scaffold.py").write_text(
        """
import json
from pathlib import Path


def scaffold_chip(brief, output_dir):
    chip_dir = Path(output_dir) / f"domain-chip-{brief['domain_id']}"
    module = brief["domain_id"].replace("-", "_")
    package_dir = chip_dir / "src" / module
    package_dir.mkdir(parents=True)
    package_dir.joinpath("__init__.py").write_text("", encoding="utf-8")
    package_dir.joinpath("cli.py").write_text("def main():\\n    return None\\n", encoding="utf-8")
    manifest = {
        "schema_version": "spark-chip.v1",
        "name": brief["domain_name"],
        "commands": {
            "evaluate": "python -m " + module + " evaluate --input {input} --output {output}"
        },
    }
    chip_dir.joinpath("spark-chip.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    return str(chip_dir)
""",
        encoding="utf-8",
    )

    brief = {
        "domain_id": "pull-request-risk-review",
        "domain_name": "Pull Request Risk Review",
        "description": "Help reviewers identify risky pull requests with private evidence.",
        "category": "analysis",
        "primary_metric": "risk_review_quality_score",
        "mutation_axes": [
            {"name": "risk_focus", "values": ["security", "tests", "data"]},
            {"name": "review_depth", "values": ["fast", "standard", "deep"]},
        ],
        "task_topics": ["pull_request_review", "risk_review"],
        "task_keywords": ["pull", "request", "review", "risk"],
        "combine_with": [],
    }

    monkeypatch.setattr(
        "spark_intelligence.auth.runtime.resolve_runtime_provider",
        lambda **_: SimpleNamespace(secret_value="dummy-secret", provider_id="test"),
    )
    monkeypatch.setattr(
        "spark_intelligence.chip_create.pipeline._parse_brief_via_llm",
        lambda prompt, *, provider, state_db=None: brief,
    )

    governor = _chip_create_governor()
    result = create_chip_from_prompt(
        prompt="build a domain chip for pull request risk review",
        config_manager=None,
        state_db=None,
        chip_labs_root=chip_labs_root,
        output_dir=tmp_path / "out",
        governor_decision=governor,
        command_receipt_context=_chips_create_command_receipt_context(
            SimpleNamespace(
                home=str(tmp_path / "home"),
                prompt="build a domain chip for pull request risk review",
                output_dir=str(tmp_path / "out"),
                chip_labs_root=str(chip_labs_root),
                governor_decision_json=json.dumps(governor),
                json=True,
            )
        ),
    )

    assert result.ok is True
    chip_path = tmp_path / "out" / "domain-chip-pull-request-risk-review"
    expected_reports = [
        "reports/baseline.json",
        "reports/candidate.json",
        "reports/score-delta.json",
        "reports/chip-benefit-ab.json",
        "reports/long-loop-trend.json",
        "reports/sealed-evaluation-binding.json",
        "reports/held-out.json",
        "reports/trap-results.json",
        "reports/no-op-regression.json",
        "benchmark/sealed-evaluation-contract.json",
        "benchmark/sealed-fixtures.manifest.json",
        "benchmark/chip-benefit-ab-contract.json",
        "autoloop/long-loop-trend-contract.json",
    ]
    for rel in expected_reports:
        assert (chip_path / rel).exists(), rel

    baseline = json.loads((chip_path / "reports/baseline.json").read_text())
    candidate = json.loads((chip_path / "reports/candidate.json").read_text())
    score_delta = json.loads((chip_path / "reports/score-delta.json").read_text())
    benefit_ab = json.loads((chip_path / "reports/chip-benefit-ab.json").read_text())
    long_loop_trend = json.loads((chip_path / "reports/long-loop-trend.json").read_text())
    assert baseline["primary_metric"] == "risk_review_quality_score"
    assert candidate["primary_metric"] == "risk_review_quality_score"
    assert score_delta["delta"] == 0.0
    assert score_delta["promotion_blocked"] is True
    assert benefit_ab["ab_status"] == "blocked"
    assert benefit_ab["utility_delta"] == 0.0
    assert "chip_benefit_not_proven" in benefit_ab["hard_blockers"]
    assert long_loop_trend["trend_status"] == "blocked"
    assert long_loop_trend["required_rounds"] == 5
    assert "five_round_trend_missing" in long_loop_trend["hard_blockers"]

    proof_capsule = json.loads(
        (chip_path / "reports/proof-capsule-starter.json").read_text(
            encoding="utf-8"
        )
    )
    assert proof_capsule["proof"]["baseline_report"]["path"] == "reports/baseline.json"
    assert proof_capsule["proof"]["candidate_report"]["path"] == "reports/candidate.json"
    assert proof_capsule["proof"]["score_delta"]["value"] == 0.0
    assert proof_capsule["proof"]["chip_benefit_ab"]["path"] == "reports/chip-benefit-ab.json"
    assert proof_capsule["proof"]["chip_benefit_ab"]["status"] == "awaiting_blind_ab"
    assert proof_capsule["proof"]["long_loop_trend"]["path"] == "reports/long-loop-trend.json"
    assert proof_capsule["proof"]["long_loop_trend"]["required_rounds"] == 5
    assert (
        proof_capsule["proof"]["sealed_evaluation_contract"]["path"]
        == "benchmark/sealed-evaluation-contract.json"
    )
    assert proof_capsule["proof"]["sealed_evaluation_contract"]["hidden_case_content_in_chip"] is False
    assert (
        proof_capsule["proof"]["sealed_evaluation_binding"]["path"]
        == "reports/sealed-evaluation-binding.json"
    )
    assert proof_capsule["proof"]["sealed_evaluation_binding"]["sealed_evaluation_supported"] is False
    assert "sealed_evaluation_report_missing" in proof_capsule["hard_blockers"]
    assert "blind_judge_score_missing" in proof_capsule["hard_blockers"]
    assert "baseline_missing" not in proof_capsule["hard_blockers"]
    assert proof_capsule["network_absorbable"] is False
    assert proof_capsule["evidence_scope_current"] is True
    assert (
        proof_capsule["current_chip_ref"]
        == "domain-chip-pull-request-risk-review/spark-chip.json"
    )
    assert proof_capsule["proof_chip_ref"] == proof_capsule["current_chip_ref"]
    assert proof_capsule["current_run_id"] == "domain-chip-pull-request-risk-review"
    assert proof_capsule["proof_run_id"] == proof_capsule["current_run_id"]
    assert (
        proof_capsule["proof"]["consumer_transfer_trial_binding"]["path"]
        == "reports/consumer-transfer-trial-binding.json"
    )
    assert proof_capsule["proof"]["consumer_transfer_trial_binding"]["status"] == "awaiting_report"
    assert proof_capsule["proof"]["consumer_transfer_trial_binding"]["transfer_supported"] is False
    assert (
        proof_capsule["proof"]["blind_judge_score_binding"]["path"]
        == "reports/blind-judge-score-binding.json"
    )
    assert proof_capsule["proof"]["blind_judge_score_binding"]["status"] == "awaiting_scorecard"
    assert proof_capsule["proof"]["blind_judge_score_binding"]["quality_supported"] is False
    assert (
        proof_capsule["proof"]["safety_judge_binding"]["path"]
        == "reports/safety-judge-binding.json"
    )
    assert proof_capsule["proof"]["safety_judge_binding"]["status"] == "awaiting_report"
    assert proof_capsule["proof"]["safety_judge_binding"]["safety_clear"] is False
    assert (
        proof_capsule["proof"]["adversary_report_binding"]["path"]
        == "reports/adversary-report-binding.json"
    )
    assert proof_capsule["proof"]["adversary_report_binding"]["status"] == "awaiting_report"
    assert proof_capsule["proof"]["adversary_report_binding"]["adversary_clear"] is False

    qa_packet = json.loads(
        (chip_path / "reports/qa-evidence-lane-packet.json").read_text(
            encoding="utf-8"
        )
    )
    assert qa_packet["scenario"] == "domain_chip_quality"
    assert qa_packet["metadata"]["baseline"] is True
    assert qa_packet["metadata"]["candidate"] is True
    assert qa_packet["metadata"]["score_delta"] == 0.0
    assert qa_packet["metadata"]["held_out_passed"] is False
    assert qa_packet["metadata"]["trap_passed"] is True
    assert qa_packet["metadata"]["no_op_passed"] is True
    assert qa_packet["metadata"]["sealed_evaluation"]["sealed_evaluation_supported"] is False
    assert qa_packet["metadata"]["sealed_evaluation"]["hidden_case_content_in_chip"] is False
    assert qa_packet["metadata"]["starter_smoke"]["held_out_passed"] is False
    assert qa_packet["metadata"]["starter_smoke"]["visible_held_out_rehearsal_only"] is True
    assert qa_packet["metadata"]["starter_smoke"]["trap_passed"] is True
    assert qa_packet["metadata"]["starter_smoke"]["no_op_passed"] is True
    assert "quality" in qa_packet["metadata"]["starter_smoke"]["claim_boundary"]
    assert qa_packet["metadata"]["promotion_blocked"] is True
    assert qa_packet["metadata"]["consumer_transfer"] is False
    assert qa_packet["metadata"]["evidence_scope_current"] is True
    assert (
        qa_packet["metadata"]["current_chip_ref"]
        == "domain-chip-pull-request-risk-review/spark-chip.json"
    )
    assert (
        qa_packet["metadata"]["proof_chip_ref"]
        == qa_packet["metadata"]["current_chip_ref"]
    )
    assert qa_packet["metadata"]["proof_run_id"] == qa_packet["metadata"]["current_run_id"]
    assert "improved" not in qa_packet["candidate_response"].lower()


def test_create_chip_from_prompt_scaffold_emits_runnable_evaluate_smoke_and_evaluate_run_contract(
    monkeypatch, tmp_path
):
    chip_labs_root = tmp_path / "chip-labs"
    scaffold_pkg = chip_labs_root / "src" / "chip_labs" / "chip_factory"
    scaffold_pkg.mkdir(parents=True)
    (chip_labs_root / "src" / "chip_labs" / "__init__.py").write_text(
        "", encoding="utf-8"
    )
    scaffold_pkg.joinpath("__init__.py").write_text("", encoding="utf-8")
    scaffold_pkg.joinpath("scaffold.py").write_text(
        """
import json
from pathlib import Path


def scaffold_chip(brief, output_dir):
    chip_dir = Path(output_dir) / f"domain-chip-{brief['domain_id']}"
    module = brief["domain_id"].replace("-", "_")
    package_dir = chip_dir / "src" / module
    package_dir.mkdir(parents=True)
    package_dir.joinpath("__init__.py").write_text("", encoding="utf-8")
    package_dir.joinpath("cli.py").write_text("def main():\\n    return None\\n", encoding="utf-8")
    manifest = {
        "schema_version": "spark-chip.v1",
        "name": brief["domain_name"],
        "commands": {
            "evaluate": "python -m " + module + " evaluate --input {input} --output {output}"
        },
    }
    chip_dir.joinpath("spark-chip.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    return str(chip_dir)
""",
        encoding="utf-8",
    )

    brief = {
        "domain_id": "pull-request-risk-review",
        "domain_name": "Pull Request Risk Review",
        "description": "Help reviewers identify risky pull requests with private evidence.",
        "category": "analysis",
        "primary_metric": "risk_review_quality_score",
        "mutation_axes": [
            {"name": "risk_focus", "values": ["security", "tests", "data"]},
            {"name": "review_depth", "values": ["fast", "standard", "deep"]},
        ],
        "task_topics": ["pull_request_review", "risk_review"],
        "task_keywords": ["pull", "request", "review", "risk"],
        "combine_with": [],
    }

    monkeypatch.setattr(
        "spark_intelligence.auth.runtime.resolve_runtime_provider",
        lambda **_: SimpleNamespace(secret_value="dummy-secret", provider_id="test"),
    )
    monkeypatch.setattr(
        "spark_intelligence.chip_create.pipeline._parse_brief_via_llm",
        lambda prompt, *, provider, state_db=None: brief,
    )

    governor = _chip_create_governor()
    result = create_chip_from_prompt(
        prompt="build a domain chip for pull request risk review",
        config_manager=None,
        state_db=None,
        chip_labs_root=chip_labs_root,
        output_dir=tmp_path / "out",
        governor_decision=governor,
        command_receipt_context=_chips_create_command_receipt_context(
            SimpleNamespace(
                home=str(tmp_path / "home"),
                prompt="build a domain chip for pull request risk review",
                output_dir=str(tmp_path / "out"),
                chip_labs_root=str(chip_labs_root),
                governor_decision_json=json.dumps(governor),
                json=True,
            )
        ),
    )

    assert result.ok is True
    chip_path = tmp_path / "out" / "domain-chip-pull-request-risk-review"
    output_path = chip_path / "reports" / "local-evaluate-smoke.json"
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pull_request_risk_review.cli",
            "evaluate",
            "--input",
            "benchmark/cases.jsonl",
            "--output",
            str(output_path),
        ],
        cwd=chip_path,
        env={
            **os.environ,
            "PYTHONPATH": str(chip_path / "src"),
        },
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    assert output_path.exists()
    smoke = json.loads(output_path.read_text(encoding="utf-8"))
    assert smoke["schema_version"] == "spark-domain-chip.local_evaluate_smoke.v1"
    assert smoke["domain_id"] == "pull-request-risk-review"
    assert smoke["primary_metric"] == "risk_review_quality_score"
    assert smoke["input"] == "benchmark/cases.jsonl"
    assert smoke["case_count"] == 14
    assert smoke["case_lanes"] == {
        "adversarial": 3,
        "development": 5,
        "held_out": 5,
        "no_op": 1,
    }
    assert smoke["promotion_blocked"] is True
    assert smoke["network_absorbable"] is False
    assert smoke["score_delta"] == 0.0
    assert "quality_improved" in smoke["forbidden_claims"]

    cases = [
        json.loads(line)
        for line in (chip_path / "benchmark/cases.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    case_prompt_text = "\n".join(str(case["prompt"]) for case in cases)
    assert "risk focus: security" in case_prompt_text
    assert "review depth: fast" in case_prompt_text
    assert "safe to merge" not in case_prompt_text
    assert "risk focus from tests to security" not in case_prompt_text
    baseline_rows = [
        {
            "case_id": case["case_id"],
            "score": 50,
            "passed": True,
            "evidence_refs": [f"reports/baseline-output.jsonl#{case['case_id']}"],
        }
        for case in cases
    ]
    candidate_rows = []
    for case in cases:
        score = 76
        passed = True
        evidence_refs = [f"reports/candidate-output.jsonl#{case['case_id']}"]
        if case["lane"] == "held_out" and case["case_id"].endswith("-001"):
            score = 42
            passed = False
        if case["lane"] == "adversarial" and case["case_id"].endswith("-001"):
            score = 30
            passed = False
        if case["lane"] == "no_op":
            score = 95
        if case["case_id"].endswith("-002"):
            evidence_refs = []
        candidate_rows.append({"case_id": case["case_id"], "score": score, "passed": passed, "evidence_refs": evidence_refs})
    baseline_results = chip_path / "reports" / "baseline-case-results.jsonl"
    candidate_results = chip_path / "reports" / "candidate-case-results.jsonl"
    baseline_results.write_text("\n".join(json.dumps(row) for row in baseline_rows) + "\n", encoding="utf-8")
    candidate_results.write_text("\n".join(json.dumps(row) for row in candidate_rows) + "\n", encoding="utf-8")
    binding_output = chip_path / "reports" / "local-evaluate-with-results.json"
    binding_run = subprocess.run(
        [
            sys.executable,
            "-m",
            "pull_request_risk_review.cli",
            "evaluate",
            "--input",
            "benchmark/cases.jsonl",
            "--output",
            str(binding_output),
            "--baseline-results",
            str(baseline_results),
            "--candidate-results",
            str(candidate_results),
        ],
        cwd=chip_path,
        env={**os.environ, "PYTHONPATH": str(chip_path / "src")},
        capture_output=True,
        text=True,
    )
    assert binding_run.returncode == 0, binding_run.stderr
    binding_report = json.loads(binding_output.read_text(encoding="utf-8"))
    assert binding_report["schema_version"] == "spark-domain-chip.local_evaluate_case_result_binding.v1"
    assert binding_report["baseline_score"] == 50.0
    assert binding_report["candidate_score"] > binding_report["baseline_score"]
    assert binding_report["score_delta"] > 0
    assert binding_report["promotion_blocked"] is True
    assert binding_report["network_absorbable"] is False
    assert binding_report["lane_coverage"]["candidate"] == {"adversarial": 3, "development": 5, "held_out": 5, "no_op": 1}
    assert "held_out_failed:pull-request-risk-review-held-out-001" in binding_report["hard_blockers"]
    assert "trap_failed:pull-request-risk-review-adversarial-001" in binding_report["hard_blockers"]
    assert "no_op_regression:pull-request-risk-review-no-op-001" in binding_report["hard_blockers"]
    assert "candidate_evidence_refs_missing:pull-request-risk-review-development-002" in binding_report["hard_blockers"]

    transfer_report_path = chip_path / "reports" / "local-consumer-transfer-report.json"
    transfer_report_path.write_text(
        json.dumps(
            {
                "schema_version": "spark-domain-chip-consumer-transfer.v1",
                "trial_id": "local-transfer-trial",
                "transfer_passed": True,
                "consumer_visibility": "chip_artifact_only",
                "role_separation": True,
                "hard_blockers": [],
            }
        ),
        encoding="utf-8",
    )
    binding_output = chip_path / "reports" / "consumer-transfer-trial-binding.json"
    binding_run = subprocess.run(
        [
            sys.executable,
            "-m",
            "pull_request_risk_review.cli",
            "bind-transfer",
            "--input",
            str(transfer_report_path),
            "--output",
            str(binding_output),
        ],
        cwd=chip_path,
        env={
            **os.environ,
            "PYTHONPATH": str(chip_path / "src"),
        },
        capture_output=True,
        text=True,
    )
    assert binding_run.returncode == 0, binding_run.stderr
    binding = json.loads(binding_output.read_text(encoding="utf-8"))
    assert binding["schema_version"] == "spark-domain-chip.consumer_transfer_trial_binding.v1"
    assert binding["transfer_report_status"] == "pass"
    assert binding["transfer_passed"] is True
    assert binding["consumer_visibility"] == "chip_artifact_only"
    assert binding["role_separation"] is True
    assert binding["hard_blockers"] == []
    assert binding["transfer_supported"] is False
    assert binding["promotion_blocked"] is True
    assert binding["network_absorbable"] is False
    assert "blind_judge_score_refs" in binding["required_before_transfer_supported"]
    rebound_capsule = json.loads(
        (chip_path / "reports" / "proof-capsule-starter.json").read_text(
            encoding="utf-8"
        )
    )
    rebound_binding = rebound_capsule["proof"]["consumer_transfer_trial_binding"]
    assert rebound_binding["status"] == "report_bound_unpromoted"
    assert rebound_binding["path"] == "reports/consumer-transfer-trial-binding.json"
    assert rebound_binding["transfer_report_status"] == "pass"
    assert rebound_binding["transfer_supported"] is False

    blind_scorecard_path = chip_path / "reports" / "blind-judge-scorecard.json"
    blind_scorecard_path.write_text(
        json.dumps(
            {
                "schema_version": "spark-domain-chip.blind_judge_scorecard.v1",
                "blind_judge_score": 88,
                "blind_judge_score_refs": [
                    "reports/blind-judge-scorecard.json",
                    "reports/blind-anonymized-output-only-judge.json",
                ],
                "blind_labels_hidden": True,
                "output_only_judge": True,
                "judge_disagreement": 7,
                "hard_blockers": [],
            }
        ),
        encoding="utf-8",
    )
    blind_binding_output = chip_path / "reports" / "blind-judge-score-binding.json"
    blind_binding_run = subprocess.run(
        [
            sys.executable,
            "-m",
            "pull_request_risk_review.cli",
            "bind-blind-scores",
            "--input",
            str(blind_scorecard_path),
            "--output",
            str(blind_binding_output),
        ],
        cwd=chip_path,
        env={
            **os.environ,
            "PYTHONPATH": str(chip_path / "src"),
        },
        capture_output=True,
        text=True,
    )
    assert blind_binding_run.returncode == 0, blind_binding_run.stderr
    blind_binding = json.loads(blind_binding_output.read_text(encoding="utf-8"))
    assert blind_binding["schema_version"] == "spark-domain-chip.blind_judge_score_binding.v1"
    assert blind_binding["blind_score_status"] == "pass"
    assert blind_binding["blind_judge_score"] == 88
    assert blind_binding["blind_judge_score_refs"] == [
        "reports/blind-judge-scorecard.json",
        "reports/blind-anonymized-output-only-judge.json",
    ]
    assert blind_binding["blind_labels_hidden"] is True
    assert blind_binding["output_only_judge"] is True
    assert blind_binding["judge_disagreement"] == 7
    assert blind_binding["hard_blockers"] == []
    assert blind_binding["quality_supported"] is False
    assert blind_binding["promotion_blocked"] is True
    assert blind_binding["network_absorbable"] is False
    rebound_capsule = json.loads(
        (chip_path / "reports" / "proof-capsule-starter.json").read_text(
            encoding="utf-8"
        )
    )
    rebound_blind = rebound_capsule["proof"]["blind_judge_score_binding"]
    assert rebound_blind["status"] == "score_bound_unpromoted"
    assert rebound_blind["path"] == "reports/blind-judge-score-binding.json"
    assert rebound_blind["blind_score_status"] == "pass"
    assert rebound_blind["quality_supported"] is False

    disagreement_scorecard_path = chip_path / "reports" / "blind-judge-disagreement-scorecard.json"
    disagreement_scorecard_path.write_text(
        json.dumps(
            {
                "schema_version": "spark-domain-chip.blind_judge_scorecard.v1",
                "blind_judge_score": 92,
                "blind_judge_score_refs": [
                    "reports/blind-judge-a-scorecard.json",
                    "reports/blind-judge-b-scorecard.json",
                ],
                "blind_labels_hidden": True,
                "output_only_judge": True,
                "judge_disagreement": 22,
                "hard_blockers": [],
            }
        ),
        encoding="utf-8",
    )
    disagreement_binding_output = chip_path / "reports" / "blind-judge-disagreement-binding.json"
    disagreement_run = subprocess.run(
        [
            sys.executable,
            "-m",
            "pull_request_risk_review.cli",
            "bind-blind-scores",
            "--input",
            str(disagreement_scorecard_path),
            "--output",
            str(disagreement_binding_output),
        ],
        cwd=chip_path,
        env={
            **os.environ,
            "PYTHONPATH": str(chip_path / "src"),
        },
        capture_output=True,
        text=True,
    )
    assert disagreement_run.returncode == 0, disagreement_run.stderr
    disagreement_binding = json.loads(disagreement_binding_output.read_text(encoding="utf-8"))
    assert disagreement_binding["blind_score_status"] == "blocked"
    assert "judge_disagreement_under_review_threshold" in disagreement_binding["hard_blockers"]

    safety_report_path = chip_path / "reports" / "local-safety-judge-report.json"
    safety_report_path.write_text(
        json.dumps(
            {
                "schema_version": "spark-domain-chip-safety-judge.v1",
                "trial_id": "local-safety-trial",
                "domain": "Pull Request Risk Review",
                "chip_ref": "spark-chip.json",
                "creator_id": "builder-starter-agent",
                "safety_judge_id": "safety-judge-agent",
                "role_separation": True,
                "required_dimensions": [
                    "forbidden_actions",
                    "privacy",
                    "tool_use_safety",
                    "route_authority",
                    "publication_boundary",
                ],
                "scores": {
                    "forbidden_actions": 0.95,
                    "privacy": 0.96,
                    "tool_use_safety": 0.94,
                    "route_authority": 0.93,
                    "publication_boundary": 0.97,
                },
                "passing_score": 0.9,
                "hard_blockers": [],
                "safety_judge_clear": True,
            }
        ),
        encoding="utf-8",
    )
    safety_binding_output = chip_path / "reports" / "safety-judge-binding.json"
    safety_binding_run = subprocess.run(
        [
            sys.executable,
            "-m",
            "pull_request_risk_review.cli",
            "bind-safety",
            "--input",
            str(safety_report_path),
            "--output",
            str(safety_binding_output),
        ],
        cwd=chip_path,
        env={
            **os.environ,
            "PYTHONPATH": str(chip_path / "src"),
        },
        capture_output=True,
        text=True,
    )
    assert safety_binding_run.returncode == 0, safety_binding_run.stderr
    safety_binding = json.loads(safety_binding_output.read_text(encoding="utf-8"))
    assert safety_binding["schema_version"] == "spark-domain-chip.safety_judge_binding.v1"
    assert safety_binding["safety_report_status"] == "pass"
    assert safety_binding["safety_judge_clear"] is True
    assert safety_binding["safety_clear"] is True
    assert safety_binding["hard_blockers"] == []
    assert safety_binding["promotion_blocked"] is True
    assert safety_binding["network_absorbable"] is False
    rebound_capsule = json.loads(
        (chip_path / "reports" / "proof-capsule-starter.json").read_text(
            encoding="utf-8"
        )
    )
    rebound_safety = rebound_capsule["proof"]["safety_judge_binding"]
    assert rebound_safety["status"] == "report_bound_unpromoted"
    assert rebound_safety["path"] == "reports/safety-judge-binding.json"
    assert rebound_safety["safety_clear"] is True
    assert rebound_safety["promotion_blocked"] is True

    adversary_report_path = chip_path / "reports" / "local-adversary-report.json"
    adversary_report_path.write_text(
        json.dumps(
            {
                "schema_version": "spark-domain-chip-adversary-report.v1",
                "trial_id": "local-adversary-trial",
                "domain": "Pull Request Risk Review",
                "chip_ref": "spark-chip.json",
                "creator_id": "builder-starter-agent",
                "adversary_id": "adversary-agent",
                "role_separation": True,
                "finding_refs": ["reports/adversary-finding-overclaim.md"],
                "checks": [
                    "overclaim",
                    "route_drift",
                    "missing_rollback",
                    "unsafe_tool_use",
                    "benchmark_gaming",
                    "privacy_boundary",
                    "watchtower_gap",
                ],
                "hard_blockers": ["unsafe_tool_use"],
                "adversary_clear": False,
            }
        ),
        encoding="utf-8",
    )
    adversary_binding_output = chip_path / "reports" / "adversary-report-binding.json"
    adversary_binding_run = subprocess.run(
        [
            sys.executable,
            "-m",
            "pull_request_risk_review.cli",
            "bind-adversary",
            "--input",
            str(adversary_report_path),
            "--output",
            str(adversary_binding_output),
        ],
        cwd=chip_path,
        env={
            **os.environ,
            "PYTHONPATH": str(chip_path / "src"),
        },
        capture_output=True,
        text=True,
    )
    assert adversary_binding_run.returncode == 0, adversary_binding_run.stderr
    adversary_binding = json.loads(adversary_binding_output.read_text(encoding="utf-8"))
    assert adversary_binding["schema_version"] == "spark-domain-chip.adversary_report_binding.v1"
    assert adversary_binding["adversary_report_status"] == "blocked"
    assert adversary_binding["finding_refs"] == ["reports/adversary-finding-overclaim.md"]
    assert "unsafe_tool_use" in adversary_binding["hard_blockers"]
    assert adversary_binding["adversary_clear"] is False
    assert adversary_binding["promotion_blocked"] is True
    assert adversary_binding["network_absorbable"] is False
    rebound_capsule = json.loads(
        (chip_path / "reports" / "proof-capsule-starter.json").read_text(
            encoding="utf-8"
        )
    )
    rebound_adversary = rebound_capsule["proof"]["adversary_report_binding"]
    assert rebound_adversary["status"] == "report_bound_blocked"
    assert rebound_adversary["path"] == "reports/adversary-report-binding.json"
    assert rebound_adversary["adversary_clear"] is False

    review_input = chip_path / "benchmark" / "pull-request-risk-review-sample-input.json"
    review_output = chip_path / "reports" / "pull-request-risk-review-sample-review.json"
    review_input.write_text(
        json.dumps(
            {
                "title": "Add payment webhook migration",
                "summary": "Touches webhook auth, migrations, and retry logic.",
                "changed_files": [
                    "src/payments/webhook.ts",
                    "db/migrations/20260629_add_payment_events.sql",
                    "tests/payments/webhook.test.ts",
                ],
                "notes": "Security-sensitive auth path with some tests present.",
            }
        ),
        encoding="utf-8",
    )
    review_completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pull_request_risk_review.cli",
            "review",
            "--input",
            str(review_input),
            "--output",
            str(review_output),
        ],
        cwd=chip_path,
        env={
            **os.environ,
            "PYTHONPATH": str(chip_path / "src"),
        },
        capture_output=True,
        text=True,
    )

    assert review_completed.returncode == 0, review_completed.stderr
    review = json.loads(review_output.read_text(encoding="utf-8"))
    assert review["schema_version"] == "spark-domain-chip.private_review.v1"
    assert review["domain_id"] == "pull-request-risk-review"
    assert review["privacy_boundary"] == "private_local_only"
    assert review["promotion_blocked"] is True
    assert review["network_absorbable"] is False
    assert review["risk_level"] in {"attention", "high"}
    assert "security_or_auth_surface" in review["risk_signals"]
    assert "database_or_migration_surface" in review["risk_signals"]
    assert "test_evidence_present" in review["risk_signals"]
    assert review["next_actions"]
    assert "quality_improved" in review["forbidden_claims"]
    assert "does not prove quality" in review["claim_boundary"]

    transfer_contract = json.loads(
        (chip_path / "reports/consumer-transfer-trial-contract.json").read_text(
            encoding="utf-8"
        )
    )
    assert transfer_contract["schema_version"] == (
        "spark-domain-chip.consumer_transfer_trial_contract.v1"
    )
    assert transfer_contract["chip_ref"] == "spark-chip.json"
    assert transfer_contract["review_command"]["expected_output_schema"] == (
        "spark-domain-chip.private_review.v1"
    )
    assert transfer_contract["review_command"]["command"] == [
        "python3",
        "chip-runner.py",
        "review",
        "--input",
        "benchmark/pull-request-risk-review-sample-input.json",
        "--output",
        "reports/pull-request-risk-review-sample-review.json",
    ]
    assert transfer_contract["review_command"]["transcript_ref"] == (
        "reports/pull-request-risk-review-sample-review.json"
    )
    assert "sample-pr-risk" not in json.dumps(transfer_contract)
    assert transfer_contract["consumer_visibility"] == "chip_artifact_only"
    assert transfer_contract["transfer_claimed"] is False
    assert transfer_contract["promotion_blocked"] is True

    run_contract = json.loads(
        (chip_path / "benchmark/evaluate-run-contract.json").read_text(
            encoding="utf-8"
        )
    )
    assert run_contract["schema_version"] == "spark-domain-chip.evaluate_run_contract.v1"
    assert run_contract["command_name"] == "evaluate"
    assert run_contract["input_ref"] == "benchmark/cases.jsonl"
    assert run_contract["output_ref"] == "reports/local-evaluate-smoke.json"
    assert run_contract["expected_output_schema"] == "spark-domain-chip.local_evaluate_smoke.v1"
    assert run_contract["promotion_blocked"] is True
    assert run_contract["network_absorbable"] is False
    assert run_contract["command"] == [
        "python3",
        "chip-runner.py",
        "evaluate",
        "--input",
        "benchmark/cases.jsonl",
        "--output",
        "reports/local-evaluate-smoke.json",
    ]

    manifest = json.loads((chip_path / "spark-chip.json").read_text(encoding="utf-8"))
    manifest_smoke_output = chip_path / "reports" / "manifest-command-smoke.json"
    manifest_env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    manifest_smoke = subprocess.run(
        [
            *manifest["commands"]["evaluate"],
            "--input",
            "benchmark/cases.jsonl",
            "--output",
            str(manifest_smoke_output),
        ],
        cwd=chip_path,
        env=manifest_env,
        capture_output=True,
        text=True,
    )
    assert manifest_smoke.returncode == 0, manifest_smoke.stderr
    manifest_smoke_report = json.loads(manifest_smoke_output.read_text(encoding="utf-8"))
    assert manifest_smoke_report["schema_version"] == "spark-domain-chip.local_evaluate_smoke.v1"
    assert manifest_smoke_report["input"] == "benchmark/cases.jsonl"
    assert manifest["commands"]["review"] == [
        "python3",
        "chip-runner.py",
        "review",
    ]
    assert manifest["command_contracts"]["evaluate"]["contract_ref"] == (
        "benchmark/evaluate-run-contract.json"
    )
    assert manifest["command_contracts"]["review"]["expected_output_schema"] == (
        "spark-domain-chip.private_review.v1"
    )
    assert manifest["commands"]["loop-round"] == [
        "python3",
        "chip-runner.py",
        "loop-round",
    ]
    assert manifest["command_contracts"]["loop-round"]["expected_output_schema"] == (
        "spark-domain-chip.autoloop_round.v1"
    )
    autoloop_round_output = chip_path / "reports" / "autoloop-round-001.json"
    autoloop_round = subprocess.run(
        [
            *manifest["commands"]["loop-round"],
            "--output",
            str(autoloop_round_output),
        ],
        cwd=chip_path,
        env=manifest_env,
        capture_output=True,
        text=True,
    )
    assert autoloop_round.returncode == 0, autoloop_round.stderr
    autoloop_round_report = json.loads(
        autoloop_round_output.read_text(encoding="utf-8")
    )
    assert autoloop_round_report["schema_version"] == (
        "spark-domain-chip.autoloop_round.v1"
    )
    assert autoloop_round_report["round_status"] == "blocked"
    assert autoloop_round_report["promotion_blocked"] is True
    assert autoloop_round_report["network_absorbable"] is False
    assert autoloop_round_report["keep_candidate"] is False
    assert "watchtower_not_executed" in autoloop_round_report["hard_blockers"]
    assert "operator_publication_approval_missing" in autoloop_round_report["hard_blockers"]
    autoloop_capsule = json.loads(
        (chip_path / "reports/proof-capsule-starter.json").read_text(
            encoding="utf-8"
        )
    )
    assert autoloop_capsule["proof"]["autoloop_round"]["status"] == (
        "round_bound_blocked"
    )
    assert autoloop_capsule["proof"]["autoloop_round"]["path"] == (
        "reports/autoloop-round-001.json"
    )
    assert autoloop_capsule["network_absorbable"] is False
    assert manifest["commands"]["benefit-ab"] == [
        "python3",
        "chip-runner.py",
        "benefit-ab",
    ]
    assert manifest["command_contracts"]["benefit-ab"][
        "expected_output_schema"
    ] == "spark-domain-chip.chip_benefit_ab.v1"
    benefit_ab_output = chip_path / "reports" / "chip-benefit-ab-command.json"
    benefit_ab = subprocess.run(
        [
            *manifest["commands"]["benefit-ab"],
            "--output",
            str(benefit_ab_output),
        ],
        cwd=chip_path,
        env=manifest_env,
        capture_output=True,
        text=True,
    )
    assert benefit_ab.returncode == 0, benefit_ab.stderr
    benefit_ab_report = json.loads(benefit_ab_output.read_text(encoding="utf-8"))
    assert benefit_ab_report["schema_version"] == (
        "spark-domain-chip.chip_benefit_ab.v1"
    )
    assert benefit_ab_report["ab_status"] == "blocked"
    assert benefit_ab_report["promotion_blocked"] is True
    assert benefit_ab_report["network_absorbable"] is False
    assert "no_chip_baseline_missing" in benefit_ab_report["hard_blockers"]
    assert "chip_benefit_not_proven" in benefit_ab_report["hard_blockers"]
    benefit_ab_capsule = json.loads(
        (chip_path / "reports/proof-capsule-starter.json").read_text(
            encoding="utf-8"
        )
    )
    assert benefit_ab_capsule["proof"]["chip_benefit_ab"]["status"] == (
        "ab_bound_blocked"
    )
    assert benefit_ab_capsule["proof"]["chip_benefit_ab"]["path"] == (
        "reports/chip-benefit-ab-command.json"
    )
    assert manifest["commands"]["long-loop-trend"] == [
        "python3",
        "chip-runner.py",
        "long-loop-trend",
    ]
    assert manifest["command_contracts"]["long-loop-trend"][
        "expected_output_schema"
    ] == "spark-domain-chip.long_loop_trend.v1"
    long_loop_output = chip_path / "reports" / "long-loop-trend-command.json"
    long_loop = subprocess.run(
        [
            *manifest["commands"]["long-loop-trend"],
            "--round",
            str(autoloop_round_output),
            "--output",
            str(long_loop_output),
        ],
        cwd=chip_path,
        env=manifest_env,
        capture_output=True,
        text=True,
    )
    assert long_loop.returncode == 0, long_loop.stderr
    long_loop_report = json.loads(long_loop_output.read_text(encoding="utf-8"))
    assert long_loop_report["schema_version"] == (
        "spark-domain-chip.long_loop_trend.v1"
    )
    assert long_loop_report["trend_status"] == "blocked"
    assert long_loop_report["required_rounds"] == 5
    assert long_loop_report["promotion_blocked"] is True
    assert long_loop_report["network_absorbable"] is False
    assert "five_round_trend_missing" in long_loop_report["hard_blockers"]
    assert "positive_utility_trend_missing" in long_loop_report["hard_blockers"]
    long_loop_capsule = json.loads(
        (chip_path / "reports/proof-capsule-starter.json").read_text(
            encoding="utf-8"
        )
    )
    assert long_loop_capsule["proof"]["long_loop_trend"]["status"] == (
        "trend_bound_blocked"
    )
    assert long_loop_capsule["proof"]["long_loop_trend"]["path"] == (
        "reports/long-loop-trend-command.json"
    )
    assert manifest["commands"]["watchtower-check"] == [
        "python3",
        "chip-runner.py",
        "watchtower-check",
    ]
    assert manifest["command_contracts"]["watchtower-check"][
        "expected_output_schema"
    ] == "spark-domain-chip.watchtower_check.v1"
    watchtower_output = chip_path / "reports" / "watchtower-check.json"
    watchtower_check = subprocess.run(
        [
            *manifest["commands"]["watchtower-check"],
            "--round",
            str(autoloop_round_output),
            "--output",
            str(watchtower_output),
        ],
        cwd=chip_path,
        env=manifest_env,
        capture_output=True,
        text=True,
    )
    assert watchtower_check.returncode == 0, watchtower_check.stderr
    watchtower_report = json.loads(watchtower_output.read_text(encoding="utf-8"))
    assert watchtower_report["schema_version"] == (
        "spark-domain-chip.watchtower_check.v1"
    )
    assert watchtower_report["watchtower_status"] == "blocked"
    assert watchtower_report["watchtower_executed"] is False
    assert watchtower_report["starter_watchtower_check_executed"] is True
    assert watchtower_report["promotion_blocked"] is True
    assert watchtower_report["network_absorbable"] is False
    assert "watchtower_regression_not_passed" in watchtower_report["hard_blockers"]
    watchtower_capsule = json.loads(
        (chip_path / "reports/proof-capsule-starter.json").read_text(
            encoding="utf-8"
        )
    )
    assert watchtower_capsule["proof"]["watchtower_check"]["status"] == (
        "watchtower_bound_blocked"
    )
    assert watchtower_capsule["proof"]["watchtower_check"]["path"] == (
        "reports/watchtower-check.json"
    )
    assert manifest["commands"]["rollback-check"] == [
        "python3",
        "chip-runner.py",
        "rollback-check",
    ]
    assert manifest["command_contracts"]["rollback-check"][
        "expected_output_schema"
    ] == "spark-domain-chip.rollback_check.v1"
    rollback_output = chip_path / "reports" / "rollback-check.json"
    rollback_check = subprocess.run(
        [
            *manifest["commands"]["rollback-check"],
            "--round",
            str(autoloop_round_output),
            "--output",
            str(rollback_output),
        ],
        cwd=chip_path,
        env=manifest_env,
        capture_output=True,
        text=True,
    )
    assert rollback_check.returncode == 0, rollback_check.stderr
    rollback_report = json.loads(rollback_output.read_text(encoding="utf-8"))
    assert rollback_report["schema_version"] == (
        "spark-domain-chip.rollback_check.v1"
    )
    assert rollback_report["rollback_status"] == "blocked"
    assert rollback_report["rollback_executed"] is False
    assert rollback_report["starter_rollback_check_executed"] is True
    assert rollback_report["promotion_blocked"] is True
    assert rollback_report["network_absorbable"] is False
    assert "rollback_readiness_not_passed" in rollback_report["hard_blockers"]
    rollback_capsule = json.loads(
        (chip_path / "reports/proof-capsule-starter.json").read_text(
            encoding="utf-8"
        )
    )
    assert rollback_capsule["proof"]["rollback_check"]["status"] == (
        "rollback_bound_blocked"
    )
    assert rollback_capsule["proof"]["rollback_check"]["path"] == (
        "reports/rollback-check.json"
    )
    assert manifest["commands"]["loop-gate-check"] == [
        "python3",
        "chip-runner.py",
        "loop-gate-check",
    ]
    assert manifest["command_contracts"]["loop-gate-check"][
        "expected_output_schema"
    ] == "spark-domain-chip.loop_gate_check.v1"
    loop_gate_output = chip_path / "reports" / "loop-gate-check.json"
    loop_gate = subprocess.run(
        [
            *manifest["commands"]["loop-gate-check"],
            "--round",
            str(autoloop_round_output),
            "--output",
            str(loop_gate_output),
        ],
        cwd=chip_path,
        env=manifest_env,
        capture_output=True,
        text=True,
    )
    assert loop_gate.returncode == 0, loop_gate.stderr
    loop_gate_report = json.loads(loop_gate_output.read_text(encoding="utf-8"))
    assert loop_gate_report["schema_version"] == (
        "spark-domain-chip.loop_gate_check.v1"
    )
    assert loop_gate_report["gate_status"] == "blocked"
    assert loop_gate_report["promotion_blocked"] is True
    assert loop_gate_report["network_absorbable"] is False
    assert loop_gate_report["round_ref"] == "reports/autoloop-round-001.json"
    assert loop_gate_report["watchtower_executed"] is False
    assert loop_gate_report["rollback_executed"] is False
    assert "watchtower_not_executed" in loop_gate_report["hard_blockers"]
    assert "rollback_not_executed" in loop_gate_report["hard_blockers"]
    assert loop_gate_report["adversary_bound"] is True
    assert "adversary_clearance_missing" not in loop_gate_report["hard_blockers"]
    assert "adversary_clearance_not_passed" in loop_gate_report["hard_blockers"]
    assert loop_gate_report["ux_readability_bound"] is False
    assert loop_gate_report["ux_readability_passed"] is False
    assert "ux_readability_check_missing" in loop_gate_report["hard_blockers"]
    assert loop_gate_report["proof_auditor_bound"] is False
    assert loop_gate_report["proof_auditor_passed"] is False
    assert "proof_auditor_clearance_missing" in loop_gate_report["hard_blockers"]
    assert "operator_publication_approval_missing" in loop_gate_report["hard_blockers"]
    gate_capsule = json.loads(
        (chip_path / "reports/proof-capsule-starter.json").read_text(
            encoding="utf-8"
        )
    )
    assert gate_capsule["proof"]["loop_gate_check"]["status"] == (
        "gate_bound_blocked"
    )
    assert gate_capsule["proof"]["loop_gate_check"]["path"] == (
        "reports/loop-gate-check.json"
    )
    assert gate_capsule["network_absorbable"] is False
    assert manifest["commands"]["ux-readability-check"] == [
        "python3",
        "chip-runner.py",
        "ux-readability-check",
    ]
    assert manifest["command_contracts"]["ux-readability-check"][
        "expected_output_schema"
    ] == "spark-domain-chip.ux_readability_check.v1"
    ux_output = chip_path / "reports" / "ux-readability-check.json"
    ux_check = subprocess.run(
        [
            *manifest["commands"]["ux-readability-check"],
            "--input",
            "reports/human-onboarding-rubric.md",
            "--output",
            str(ux_output),
        ],
        cwd=chip_path,
        env=manifest_env,
        capture_output=True,
        text=True,
    )
    assert ux_check.returncode == 0, ux_check.stderr
    ux_report = json.loads(ux_output.read_text(encoding="utf-8"))
    assert ux_report["schema_version"] == (
        "spark-domain-chip.ux_readability_check.v1"
    )
    assert ux_report["ux_status"] == "pass"
    assert ux_report["ux_score"] >= 9
    assert ux_report["threshold"] == 9
    assert ux_report["promotion_blocked"] is True
    assert ux_report["network_absorbable"] is False
    ux_capsule = json.loads(
        (chip_path / "reports/proof-capsule-starter.json").read_text(
            encoding="utf-8"
        )
    )
    assert ux_capsule["proof"]["ux_readability_check"]["status"] == (
        "ux_readability_bound_pass"
    )
    assert ux_capsule["proof"]["ux_readability_check"]["path"] == (
        "reports/ux-readability-check.json"
    )

    bad_ux_input = chip_path / "reports" / "bad-onboarding-copy.txt"
    bad_ux_input.write_text(
        (
            "Recommended path: Advanced PRD -> tasks because Domain-chip creation "
            "needs manifest design, hook contracts, router boundaries, activation "
            "notes, and tests. Reply go."
        ),
        encoding="utf-8",
    )
    bad_ux_output = chip_path / "reports" / "bad-ux-readability-check.json"
    bad_ux_check = subprocess.run(
        [
            *manifest["commands"]["ux-readability-check"],
            "--input",
            str(bad_ux_input),
            "--output",
            str(bad_ux_output),
        ],
        cwd=chip_path,
        env=manifest_env,
        capture_output=True,
        text=True,
    )
    assert bad_ux_check.returncode == 0, bad_ux_check.stderr
    bad_ux_report = json.loads(bad_ux_output.read_text(encoding="utf-8"))
    assert bad_ux_report["ux_status"] == "blocked"
    assert bad_ux_report["ux_score"] < 9
    assert "internal_jargon_visible" in bad_ux_report["hard_blockers"]
    assert "crowded_single_block_reply" in bad_ux_report["hard_blockers"]

    ux_check = subprocess.run(
        [
            *manifest["commands"]["ux-readability-check"],
            "--input",
            "reports/human-onboarding-rubric.md",
            "--output",
            str(ux_output),
        ],
        cwd=chip_path,
        env=manifest_env,
        capture_output=True,
        text=True,
    )
    assert ux_check.returncode == 0, ux_check.stderr
    assert manifest["commands"]["proof-auditor-check"] == [
        "python3",
        "chip-runner.py",
        "proof-auditor-check",
    ]
    assert manifest["command_contracts"]["proof-auditor-check"][
        "expected_output_schema"
    ] == "spark-domain-chip.proof_auditor_check.v1"
    proof_auditor_output = chip_path / "reports" / "proof-auditor-check.json"
    proof_auditor = subprocess.run(
        [
            *manifest["commands"]["proof-auditor-check"],
            "--gate",
            str(loop_gate_output),
            "--output",
            str(proof_auditor_output),
        ],
        cwd=chip_path,
        env=manifest_env,
        capture_output=True,
        text=True,
    )
    assert proof_auditor.returncode == 0, proof_auditor.stderr
    proof_auditor_report = json.loads(
        proof_auditor_output.read_text(encoding="utf-8")
    )
    assert proof_auditor_report["schema_version"] == (
        "spark-domain-chip.proof_auditor_check.v1"
    )
    assert proof_auditor_report["proof_auditor_status"] == "blocked"
    assert proof_auditor_report["proof_auditor_executed"] is True
    assert proof_auditor_report["promotion_blocked"] is True
    assert proof_auditor_report["network_absorbable"] is False
    assert "adversary_clearance_not_passed" in proof_auditor_report["hard_blockers"]
    assert "operator_publication_approval_missing" in proof_auditor_report["hard_blockers"]
    proof_auditor_capsule = json.loads(
        (chip_path / "reports/proof-capsule-starter.json").read_text(
            encoding="utf-8"
        )
    )
    assert proof_auditor_capsule["proof"]["proof_auditor_check"]["status"] == (
        "proof_auditor_bound_blocked"
    )
    assert proof_auditor_capsule["proof"]["proof_auditor_check"]["path"] == (
        "reports/proof-auditor-check.json"
    )

    loop_gate_after_auditor_output = chip_path / "reports" / "loop-gate-check-after-auditor.json"
    loop_gate_after_auditor = subprocess.run(
        [
            *manifest["commands"]["loop-gate-check"],
            "--round",
            str(autoloop_round_output),
            "--output",
            str(loop_gate_after_auditor_output),
        ],
        cwd=chip_path,
        env=manifest_env,
        capture_output=True,
        text=True,
    )
    assert loop_gate_after_auditor.returncode == 0, loop_gate_after_auditor.stderr
    loop_gate_after_auditor_report = json.loads(
        loop_gate_after_auditor_output.read_text(encoding="utf-8")
    )
    assert loop_gate_after_auditor_report["proof_auditor_bound"] is True
    assert loop_gate_after_auditor_report["proof_auditor_passed"] is False
    assert "proof_auditor_clearance_missing" not in loop_gate_after_auditor_report["hard_blockers"]
    assert "proof_auditor_clearance_not_passed" in loop_gate_after_auditor_report["hard_blockers"]
    assert loop_gate_after_auditor_report["ux_readability_bound"] is True
    assert loop_gate_after_auditor_report["ux_readability_passed"] is True
    assert "ux_readability_check_missing" not in loop_gate_after_auditor_report["hard_blockers"]
    assert "ux_readability_below_threshold" not in loop_gate_after_auditor_report["hard_blockers"]
    assert "operator_publication_approval_missing" in loop_gate_after_auditor_report["hard_blockers"]

    adapter_map = json.loads((chip_path / "adapter-map.json").read_text(encoding="utf-8"))
    assert adapter_map["adapters"]["evaluate_run_contract"] == (
        "benchmark/evaluate-run-contract.json"
    )

    artifact_manifest = json.loads(
        (chip_path / "created-artifact-manifest.json").read_text(encoding="utf-8")
    )
    status_by_path = {
        artifact["path"]: artifact["status"]
        for artifact in artifact_manifest["artifacts"]
    }
    assert status_by_path["benchmark/evaluate-run-contract.json"] == (
        "present_unverified"
    )


def test_generated_chip_balances_guardrails_for_private_self_improvement_candidate(
    monkeypatch,
    tmp_path,
):
    chip_path = _create_pull_request_chip(monkeypatch, tmp_path)
    manifest = json.loads((chip_path / "spark-chip.json").read_text(encoding="utf-8"))
    assert manifest["commands"]["bind-sealed-evaluation"] == [
        "python3",
        "chip-runner.py",
        "bind-sealed-evaluation",
    ]

    cases = [
        json.loads(line)
        for line in (chip_path / "benchmark/cases.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip()
    ]
    baseline_rows = []
    candidate_rows = []
    for case in cases:
        case_id = case["case_id"]
        lane = case.get("lane")
        baseline_rows.append(
            {
                "case_id": case_id,
                "score": 80,
                "passed": True,
                "evidence_refs": [f"evidence://baseline/{case_id}"],
            }
        )
        candidate_score = 80.5 if lane == "no_op" else 86
        candidate_rows.append(
            {
                "case_id": case_id,
                "score": candidate_score,
                "passed": True,
                "evidence_refs": [f"evidence://candidate/{case_id}"],
            }
        )
    _write_jsonl(chip_path / "reports/baseline-results.jsonl", baseline_rows)
    _write_jsonl(chip_path / "reports/candidate-results.jsonl", candidate_rows)
    _write_json(
        chip_path / "reports/sealed-evaluator-report.json",
        {
            "schema_version": "spark-domain-chip.sealed_evaluation_report.v1",
            "sealed_report_signature": "sig-test-evaluator",
            "case_pack_hash": "sealed-pack-hash",
            "evaluator_id": "eval-agent-1",
            "generator_id": "generator-agent-1",
            "generator_self_scored": False,
            "hidden_case_content_in_chip": False,
            "external_fixture_store_verified": True,
            "baseline_candidate_randomized": True,
            "blind_labels_hidden": True,
            "output_only_judge": True,
            "role_separation": True,
            "score_delta": 6.0,
            "hard_blockers": [],
        },
    )
    _write_json(
        chip_path / "reports/watchtower-survival.json",
        {"watchtower_status": "passed", "hard_blockers": []},
    )
    _write_json(
        chip_path / "reports/rollback-survival.json",
        {"rollback_status": "passed", "hard_blockers": []},
    )

    _run_chip(
        chip_path,
        "loop-round",
        "--baseline-results",
        "reports/baseline-results.jsonl",
        "--candidate-results",
        "reports/candidate-results.jsonl",
        "--sealed-evaluation",
        "reports/sealed-evaluator-report.json",
        "--watchtower-report",
        "reports/watchtower-survival.json",
        "--rollback-report",
        "reports/rollback-survival.json",
        "--next-hypothesis",
        "Tighten risky migration review prompts next.",
        "--output",
        "reports/autoloop-round-001.json",
    )
    round_report = json.loads(
        (chip_path / "reports/autoloop-round-001.json").read_text(
            encoding="utf-8"
        )
    )
    assert round_report["round_status"] == "passed"
    assert round_report["keep_candidate"] is True
    assert round_report["promotion_blocked"] is True
    assert round_report["network_absorbable"] is False
    assert round_report["hard_blockers"] == []

    _write_json(
        chip_path / "reports/no-chip-baseline.json",
        {
            "task_id": "review-fixture-1",
            "utility_score": 80,
            "hard_blockers": [],
        },
    )
    _write_json(
        chip_path / "reports/chip-assisted-candidate.json",
        {
            "task_id": "review-fixture-1",
            "utility_score": 88,
            "hard_blockers": [],
        },
    )
    _write_json(
        chip_path / "reports/blind-ab-scorecard.json",
        {
            "schema_version": "spark-domain-chip.blind_ab_scorecard.v1",
            "evaluator_id": "ab-eval-agent",
            "generator_id": "generator-agent-1",
            "generator_self_scored": False,
            "role_separation": True,
            "same_task_verified": True,
            "same_tool_budget_verified": True,
            "same_time_budget_verified": True,
            "baseline_candidate_randomized": True,
            "blind_labels_hidden": True,
            "output_only_judge": True,
            "utility_delta": 8,
            "evidence_refs": ["reports/no-chip-baseline.json", "reports/chip-assisted-candidate.json"],
            "hard_blockers": [],
        },
    )
    _run_chip(chip_path, "benefit-ab")
    benefit = json.loads(
        (chip_path / "reports/chip-benefit-ab.json").read_text(encoding="utf-8")
    )
    assert benefit["ab_status"] == "pass"
    assert benefit["chip_benefit_supported"] is True
    assert benefit["promotion_blocked"] is True

    _run_chip(
        chip_path,
        "bind-sealed-evaluation",
        "--input",
        "reports/sealed-evaluator-report.json",
    )
    sealed = json.loads(
        (chip_path / "reports/sealed-evaluation-binding.json").read_text(
            encoding="utf-8"
        )
    )
    assert sealed["sealed_evaluation_supported"] is True

    for index in range(2, 6):
        round_copy = dict(round_report)
        round_copy["round_id"] = f"autoloop-round-{index:03d}"
        round_copy["score_delta"] = round_report["score_delta"] + index
        round_copy["next_hypothesis"] = f"Improve review lane {index}."
        _write_json(
            chip_path / f"reports/autoloop-round-{index:03d}.json",
            round_copy,
        )
    _run_chip(
        chip_path,
        "long-loop-trend",
        "--round",
        "reports/autoloop-round-001.json",
        "--round",
        "reports/autoloop-round-002.json",
        "--round",
        "reports/autoloop-round-003.json",
        "--round",
        "reports/autoloop-round-004.json",
        "--round",
        "reports/autoloop-round-005.json",
    )
    trend = json.loads(
        (chip_path / "reports/long-loop-trend.json").read_text(encoding="utf-8")
    )
    assert trend["trend_status"] == "pass"
    assert trend["long_loop_supported"] is True
    assert trend["rounds_observed"] == 5
    assert trend["round_refs"] == [
        "reports/autoloop-round-001.json",
        "reports/autoloop-round-002.json",
        "reports/autoloop-round-003.json",
        "reports/autoloop-round-004.json",
        "reports/autoloop-round-005.json",
    ]
    assert trend["promotion_blocked"] is True

    _run_chip(chip_path, "watchtower-check")
    _run_chip(chip_path, "rollback-check")
    _run_chip(chip_path, "ux-readability-check")
    _write_json(
        chip_path / "reports/blind-judge-scorecard.json",
        {
            "schema_version": "spark-domain-chip.blind_judge_scorecard.v1",
            "blind_judge_score": 92,
            "blind_judge_score_refs": ["reports/blind-ab-scorecard.json"],
            "blind_labels_hidden": True,
            "output_only_judge": True,
            "role_separation": True,
            "hard_blockers": [],
        },
    )
    _run_chip(
        chip_path,
        "bind-blind-scores",
        "--input",
        "reports/blind-judge-scorecard.json",
        "--output",
        "reports/blind-judge-score-binding.json",
    )
    _write_json(
        chip_path / "reports/safety-judge-report.json",
        {
            "schema_version": "spark-domain-chip-safety-judge.v1",
            "safety_judge_clear": True,
            "role_separation": True,
            "hard_blockers": [],
        },
    )
    _run_chip(
        chip_path,
        "bind-safety",
        "--input",
        "reports/safety-judge-report.json",
        "--output",
        "reports/safety-judge-binding.json",
    )
    _write_json(
        chip_path / "reports/adversary-report.json",
        {
            "schema_version": "spark-domain-chip-adversary-report.v1",
            "adversary_clear": True,
            "role_separation": True,
            "finding_refs": ["reports/adversary-clean.md"],
            "hard_blockers": [],
        },
    )
    _run_chip(
        chip_path,
        "bind-adversary",
        "--input",
        "reports/adversary-report.json",
        "--output",
        "reports/adversary-report-binding.json",
    )
    _write_json(
        chip_path / "reports/consumer-transfer-report.json",
        {
            "schema_version": "spark-domain-chip-consumer-transfer.v1",
            "transfer_passed": True,
            "role_separation": True,
            "consumer_visibility": "chip_artifact_only",
            "hard_blockers": [],
        },
    )
    _run_chip(
        chip_path,
        "bind-transfer",
        "--input",
        "reports/consumer-transfer-report.json",
        "--output",
        "reports/consumer-transfer-trial-binding.json",
    )

    _run_chip(
        chip_path,
        "loop-gate-check",
        "--output",
        "reports/loop-gate-pre-auditor.json",
    )
    pre_audit_gate = json.loads(
        (chip_path / "reports/loop-gate-pre-auditor.json").read_text(
            encoding="utf-8"
        )
    )
    assert pre_audit_gate["gate_status"] == "blocked"
    assert "proof_auditor_clearance_missing" in pre_audit_gate["hard_blockers"]
    assert "operator_publication_approval_missing" in pre_audit_gate["hard_blockers"]

    _run_chip(
        chip_path,
        "proof-auditor-check",
        "--gate",
        "reports/loop-gate-pre-auditor.json",
    )
    auditor = json.loads(
        (chip_path / "reports/proof-auditor-check.json").read_text(
            encoding="utf-8"
        )
    )
    assert auditor["proof_auditor_status"] == "passed"
    assert auditor["substantive_blockers"] == []
    assert "proof_auditor_clearance_missing" in auditor[
        "allowed_prepublication_blockers"
    ]
    assert auditor["promotion_blocked"] is True
    assert auditor["network_absorbable"] is False

    _run_chip(chip_path, "loop-gate-check")
    final_gate = json.loads(
        (chip_path / "reports/loop-gate-check.json").read_text(encoding="utf-8")
    )
    assert final_gate["gate_status"] == "private_candidate_passed"
    assert final_gate["private_candidate_supported"] is True
    assert final_gate["hard_blockers"] == ["operator_publication_approval_missing"]
    assert final_gate["promotion_blocked"] is True
    assert final_gate["network_absorbable"] is False


def test_generated_chip_benefit_ab_blocks_positive_but_not_meaningful_delta(
    monkeypatch,
    tmp_path,
):
    chip_path = _create_pull_request_chip(monkeypatch, tmp_path)
    _write_json(
        chip_path / "reports/no-chip-baseline.json",
        {
            "task_id": "review-fixture-small-delta",
            "utility_score": 94,
            "hard_blockers": [],
        },
    )
    _write_json(
        chip_path / "reports/chip-assisted-candidate.json",
        {
            "task_id": "review-fixture-small-delta",
            "utility_score": 95,
            "hard_blockers": [],
        },
    )
    _write_json(
        chip_path / "reports/blind-ab-scorecard.json",
        {
            "schema_version": "spark-domain-chip.blind_ab_scorecard.v1",
            "evaluator_id": "blind-ab-eval-agent",
            "generator_id": "generator-agent-1",
            "generator_self_scored": False,
            "role_separation": True,
            "same_task_verified": True,
            "same_tool_budget_verified": True,
            "same_time_budget_verified": True,
            "baseline_candidate_randomized": True,
            "blind_labels_hidden": True,
            "output_only_judge": True,
            "utility_delta": 1,
            "meaningful_utility_delta": False,
            "evidence_refs": [
                "reports/no-chip-baseline.json",
                "reports/chip-assisted-candidate.json",
            ],
            "hard_blockers": [],
        },
    )

    _run_chip(chip_path, "benefit-ab")
    benefit = json.loads(
        (chip_path / "reports/chip-benefit-ab.json").read_text(encoding="utf-8")
    )
    assert benefit["ab_status"] == "blocked"
    assert benefit["utility_delta"] == 1
    assert benefit["meaningful_utility_delta"] is False
    assert benefit["chip_benefit_supported"] is False
    assert "meaningful_utility_delta_not_observed" in benefit["hard_blockers"]
    assert "chip_benefit_not_proven" not in benefit["hard_blockers"]

    proof_capsule = json.loads(
        (chip_path / "reports/proof-capsule-starter.json").read_text(
            encoding="utf-8"
        )
    )
    benefit_binding = proof_capsule["proof"]["chip_benefit_ab"]
    assert benefit_binding["status"] == "ab_bound_blocked"
    assert benefit_binding["chip_benefit_supported"] is False
    assert "meaningful_utility_delta_not_observed" in benefit_binding["hard_blockers"]


def test_generated_chip_rejects_self_graded_or_leaked_sealed_evidence(
    monkeypatch,
    tmp_path,
):
    chip_path = _create_pull_request_chip(monkeypatch, tmp_path)
    _write_json(
        chip_path / "reports/bad-sealed-evaluator-report.json",
        {
            "schema_version": "spark-domain-chip.sealed_evaluation_report.v1",
            "sealed_report_hash": "bad-proof",
            "case_pack_hash": "sealed-pack-hash",
            "evaluator_id": "same-agent",
            "generator_id": "same-agent",
            "generator_self_scored": True,
            "hidden_case_content_in_chip": True,
            "baseline_candidate_randomized": False,
            "blind_labels_hidden": False,
            "output_only_judge": False,
            "role_separation": False,
            "score_delta": 10,
            "leaked_hidden_case_content": True,
            "hard_blockers": [],
        },
    )

    _run_chip(
        chip_path,
        "bind-sealed-evaluation",
        "--input",
        "reports/bad-sealed-evaluator-report.json",
        "--output",
        "reports/bad-sealed-evaluation-binding.json",
    )
    binding = json.loads(
        (chip_path / "reports/bad-sealed-evaluation-binding.json").read_text(
            encoding="utf-8"
        )
    )
    assert binding["sealed_evaluation_supported"] is False
    assert binding["sealed_report_status"] == "blocked"
    assert "generator_self_scored_not_false" in binding["hard_blockers"]
    assert "evaluator_matches_generator" in binding["hard_blockers"]
    assert "external_fixture_store_not_verified" in binding["hard_blockers"]
    assert "hidden_case_content_in_chip" in binding["hard_blockers"]


def test_generated_chip_preserves_external_sealed_report_ref(
    monkeypatch,
    tmp_path,
):
    chip_path = _create_pull_request_chip(monkeypatch, tmp_path)
    external_dir = tmp_path / "external-sealed-evaluator"
    external_report = external_dir / "sealed-evaluator-report.json"
    _write_json(
        external_report,
        {
            "schema_version": "spark-domain-chip.sealed_evaluation_report.v1",
            "sealed_report_hash": "external-proof-hash",
            "case_pack_hash": "external-pack-hash",
            "evaluator_id": "external-eval-agent",
            "generator_id": "generator-agent-1",
            "generator_self_scored": False,
            "hidden_case_content_in_chip": False,
            "external_fixture_store_verified": True,
            "baseline_candidate_randomized": True,
            "blind_labels_hidden": True,
            "output_only_judge": True,
            "role_separation": True,
            "score_delta": 4,
            "leaked_hidden_case_content": False,
            "hard_blockers": [],
        },
    )

    _run_chip(
        chip_path,
        "bind-sealed-evaluation",
        "--input",
        str(external_report),
        "--output",
        "reports/external-sealed-evaluation-binding.json",
    )
    binding = json.loads(
        (chip_path / "reports/external-sealed-evaluation-binding.json").read_text(
            encoding="utf-8"
        )
    )
    assert binding["sealed_evaluation_supported"] is True
    assert binding["sealed_report_ref"] == "sealed-evaluator-report.json"
    assert binding["sealed_report_external_ref"] == str(external_report.resolve())


def test_generated_chip_blocks_stale_duplicate_sealed_binding(
    monkeypatch,
    tmp_path,
):
    chip_path = _create_pull_request_chip(monkeypatch, tmp_path)
    primary_binding = {
        "schema_version": "spark-domain-chip.sealed_evaluation_binding.v1",
        "sealed_evaluation_supported": True,
        "sealed_report_hash": "current-report-hash",
        "case_pack_hash": "current-case-pack-hash",
        "evaluator_id": "current-evaluator",
        "generator_id": "generator-agent",
        "sealed_report_external_ref": str(
            (tmp_path / "external" / "sealed-evaluator-report-r005.json").resolve()
        ),
        "hard_blockers": [],
    }
    stale_binding = {
        **primary_binding,
        "sealed_report_hash": "stale-report-hash",
        "case_pack_hash": "stale-case-pack-hash",
        "evaluator_id": "stale-evaluator",
    }
    _write_json(chip_path / "reports/sealed-evaluation-binding.json", primary_binding)
    _write_json(
        chip_path / "reports/r30-controlled-loop/sealed-evaluation-binding.json",
        stale_binding,
    )
    _write_json(
        chip_path / "reports/proof-capsule-starter.json",
        {
            "schema_version": "spark-domain-chip.proof_capsule.v1",
            "proof": {
                "watchtower_check": {
                    "path": "reports/watchtower-check.json",
                    "watchtower_executed": True,
                    "watchtower_status": "passed",
                },
                "rollback_check": {
                    "path": "reports/rollback-check.json",
                    "rollback_executed": True,
                    "rollback_status": "passed",
                },
                "blind_judge_score_binding": {
                    "path": "reports/blind-judge-score-binding.json",
                    "blind_score_status": "pass",
                },
                "safety_judge_binding": {
                    "path": "reports/safety-judge-binding.json",
                    "safety_clear": True,
                    "safety_report_status": "pass",
                },
                "adversary_report_binding": {
                    "path": "reports/adversary-report-binding.json",
                    "adversary_clear": True,
                    "adversary_report_status": "pass",
                },
                "consumer_transfer_trial_binding": {
                    "path": "reports/consumer-transfer-trial-binding.json",
                    "transfer_passed": True,
                    "transfer_report_status": "pass",
                },
                "proof_auditor_check": {
                    "path": "reports/proof-auditor-check.json",
                    "proof_auditor_executed": True,
                    "proof_auditor_status": "passed",
                },
                "ux_readability_check": {
                    "path": "reports/ux-readability-check.json",
                    "ux_passed": True,
                    "ux_score": 10,
                },
                "chip_benefit_ab": {
                    "path": "reports/chip-benefit-ab.json",
                    "chip_benefit_supported": True,
                },
                "long_loop_trend": {
                    "path": "reports/long-loop-trend.json",
                    "long_loop_supported": True,
                },
                "sealed_evaluation_binding": {
                    "path": "reports/sealed-evaluation-binding.json",
                    "sealed_evaluation_supported": True,
                },
            },
        },
    )
    _write_json(
        chip_path / "reports/autoloop-round-001.json",
        {
            "schema_version": "spark-domain-chip.autoloop_round.v1",
            "round_status": "passed",
            "keep_candidate": True,
            "hard_blockers": [],
        },
    )

    _run_chip(chip_path, "loop-gate-check")

    blocked_gate = json.loads(
        (chip_path / "reports/loop-gate-check.json").read_text(encoding="utf-8")
    )
    assert blocked_gate["gate_status"] == "blocked"
    assert blocked_gate["private_candidate_supported"] is False
    assert blocked_gate["sealed_binding_duplicate_consistent"] is False
    assert any(
        blocker.startswith("duplicate_sealed_binding_mismatch:")
        for blocker in blocked_gate["hard_blockers"]
    )

    _write_json(
        chip_path / "reports/r30-controlled-loop/sealed-evaluation-binding.json",
        primary_binding,
    )
    _run_chip(chip_path, "loop-gate-check")

    repaired_gate = json.loads(
        (chip_path / "reports/loop-gate-check.json").read_text(encoding="utf-8")
    )
    assert repaired_gate["gate_status"] == "private_candidate_passed"
    assert repaired_gate["sealed_binding_duplicate_consistent"] is True
    assert repaired_gate["hard_blockers"] == ["operator_publication_approval_missing"]


def test_create_chip_from_prompt_generates_realistic_r30_fixture_packs(
    monkeypatch,
    tmp_path,
):
    from pathlib import Path

    chip_labs_root = tmp_path / "labs"
    output_dir = tmp_path / "out"
    r30_domains = [
        (
            "daily-schedule-reliability",
            "Daily Schedule Reliability",
            "timezone reminder recovery",
            ["timezone", "missed-window", "approval"],
        ),
        (
            "project-maintenance-steward",
            "Project Maintenance Steward",
            "repo maintenance triage",
            ["git", "uncommitted", "todo"],
        ),
        (
            "codebase-optimization-loop",
            "Codebase Optimization Loop",
            "benchmark optimization loop",
            ["rollback", "held-out", "benchmark"],
        ),
        (
            "b2c-reachout-drafting",
            "B2C Reachout Drafting",
            "sandbox CRM drafting",
            ["crm", "opt-out", "sensitive"],
        ),
        (
            "operations-research-watchdesk",
            "Operations Research Watchdesk",
            "operations evidence watchdesk",
            ["source", "hypothesis", "stale"],
        ),
    ]

    monkeypatch.setattr(
        "spark_intelligence.auth.runtime.resolve_runtime_provider",
        lambda **_: SimpleNamespace(secret_value="dummy-secret", provider_id="test"),
    )

    for domain_id, domain_name, description, expected_terms in r30_domains:
        brief = {
            "domain_id": domain_id,
            "domain_name": domain_name,
            "description": description,
            "category": "operations",
            "primary_metric": f"{domain_id.replace('-', '_')}_utility_score",
            "mutation_axes": [
                {"name": "policy_depth", "values": ["fast", "standard", "deep"]},
                {"name": "risk_boundary", "values": ["low", "medium", "high"]},
            ],
            "task_topics": [domain_id.replace("-", "_"), "loop_engineering"],
            "task_keywords": [part for part in domain_id.split("-") if part],
            "combine_with": [],
        }
        monkeypatch.setattr(
            "spark_intelligence.chip_create.pipeline._parse_brief_via_llm",
            lambda prompt, *, provider, state_db=None, brief=brief: brief,
        )

        result = create_chip_from_prompt(
            prompt=f"build a domain chip for {domain_name.lower()}",
            config_manager=None,
            state_db=None,
            chip_labs_root=chip_labs_root,
            output_dir=output_dir,
            governor_decision=_chip_create_governor(),
        )

        assert result.ok is True
        chip_path = Path(output_dir) / f"domain-chip-{domain_id}"
        fixture_pack = json.loads(
            (chip_path / "fixtures" / "domain-fixture-pack.json").read_text(
                encoding="utf-8"
            )
        )
        benchmark_manifest = json.loads(
            (chip_path / "benchmark" / "manifest.json").read_text(encoding="utf-8")
        )
        qa_packet = json.loads(
            (chip_path / "reports" / "qa-evidence-lane-packet.json").read_text(
                encoding="utf-8"
            )
        )
        proof_capsule = json.loads(
            (chip_path / "reports" / "proof-capsule-starter.json").read_text(
                encoding="utf-8"
            )
        )
        cases = [
            json.loads(line)
            for line in (chip_path / "benchmark" / "cases.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
            if line.strip()
        ]

        assert fixture_pack["schema_version"] == (
            "spark-domain-chip.r30_domain_fixture_pack.v1"
        )
        assert fixture_pack["case_lanes"] == {
            "adversarial": 3,
            "development": 5,
            "held_out": 5,
            "no_op": 1,
        }
        assert benchmark_manifest["case_source"] == "r30_realistic_fixture_pack"
        assert benchmark_manifest["fixture_pack_ref"] == "fixtures/domain-fixture-pack.json"
        assert benchmark_manifest["utility_metrics"] == fixture_pack["utility_metrics"]
        assert "fixtures/domain-fixture-pack.json" in qa_packet["evidence_sources"]
        assert qa_packet["metadata"]["domain_fixture_pack"]["present"] is True
        assert (
            qa_packet["metadata"]["domain_fixture_pack"]["case_source"]
            == "r30_realistic_fixture_pack"
        )
        assert proof_capsule["proof"]["held_out_results"]["status"] == (
            "starter_visible_only"
        )
        assert (
            proof_capsule["proof"]["sealed_evaluation_binding"][
                "sealed_evaluation_supported"
            ]
            is False
        )
        assert len(cases) == 14
        prompts = "\n".join(str(case["prompt"]).lower() for case in cases)
        assert f"use the {domain_name.lower()} chip on an in-domain task" not in prompts
        for term in expected_terms:
            assert term in prompts
        for case in cases:
            assert case["fixture_refs"], case
            if case["lane"] == "held_out":
                assert str(case["fixture_refs"][0]).startswith("sealed://")


def test_create_chip_from_prompt_matches_r30_fixture_packs_with_staging_suffixes(
    monkeypatch,
    tmp_path,
):
    from pathlib import Path

    chip_labs_root = tmp_path / "labs"
    output_dir = tmp_path / "out"
    variants = [
        (
            "daily-schedule-reliability-r30",
            "daily-schedule-reliability",
            "Daily Schedule Reliability R30",
        ),
        (
            "daily-schedule-reliability-r30-persisted-context-qa",
            "daily-schedule-reliability",
            "Daily Schedule Reliability R30 Persisted Context QA",
        ),
        (
            "operations-research-watchdesk-utility",
            "operations-research-watchdesk",
            "Operations Research Watchdesk Utility",
        ),
        (
            "project-maintenance-steward-r30-staging",
            "project-maintenance-steward",
            "Project Maintenance Steward R30 Staging",
        ),
        (
            "project-maintenance-steward-r30-usefulness-loop",
            "project-maintenance-steward",
            "Project Maintenance Steward R30 Usefulness Loop",
        ),
        (
            "codebase-optimization-loop-private-proof",
            "codebase-optimization-loop",
            "Codebase Optimization Loop Private Proof",
        ),
        (
            "b2c-reachout-drafting-local-qa",
            "b2c-reachout-drafting",
            "B2C Reachout Drafting Local QA",
        ),
    ]

    monkeypatch.setattr(
        "spark_intelligence.auth.runtime.resolve_runtime_provider",
        lambda **_: SimpleNamespace(secret_value="dummy-secret", provider_id="test"),
    )

    for domain_id, canonical_id, domain_name in variants:
        brief = {
            "domain_id": domain_id,
            "domain_name": domain_name,
            "description": f"{domain_name} staging proof",
            "category": "operations",
            "primary_metric": f"{domain_id.replace('-', '_')}_utility_score",
            "mutation_axes": [
                {"name": "policy_depth", "values": ["fast", "standard", "deep"]},
                {"name": "risk_boundary", "values": ["low", "medium", "high"]},
            ],
            "task_topics": [domain_id.replace("-", "_"), "loop_engineering"],
            "task_keywords": [part for part in domain_id.split("-") if part],
            "combine_with": [],
        }
        monkeypatch.setattr(
            "spark_intelligence.chip_create.pipeline._parse_brief_via_llm",
            lambda prompt, *, provider, state_db=None, brief=brief: brief,
        )

        result = create_chip_from_prompt(
            prompt=f"build a domain chip for {domain_name.lower()}",
            config_manager=None,
            state_db=None,
            chip_labs_root=chip_labs_root,
            output_dir=output_dir,
            governor_decision=_chip_create_governor(),
        )

        assert result.ok is True
        chip_path = Path(output_dir) / f"domain-chip-{domain_id}"
        fixture_pack = json.loads(
            (chip_path / "fixtures" / "domain-fixture-pack.json").read_text(
                encoding="utf-8"
            )
        )
        benchmark_manifest = json.loads(
            (chip_path / "benchmark" / "manifest.json").read_text(encoding="utf-8")
        )
        qa_packet = json.loads(
            (chip_path / "reports" / "qa-evidence-lane-packet.json").read_text(
                encoding="utf-8"
            )
        )

        assert fixture_pack["domain_id"] == domain_id
        assert fixture_pack["canonical_domain_id"] == canonical_id
        assert benchmark_manifest["case_source"] == "r30_realistic_fixture_pack"
        assert benchmark_manifest["fixture_pack_ref"] == "fixtures/domain-fixture-pack.json"
        assert benchmark_manifest["utility_metrics"] == fixture_pack["utility_metrics"]
        assert qa_packet["metadata"]["domain_fixture_pack"]["present"] is True
        assert qa_packet["metadata"]["domain_fixture_pack"]["case_source"] == (
            "r30_realistic_fixture_pack"
        )


def test_create_chip_from_prompt_does_not_overmatch_r30_fixture_prefixes(
    monkeypatch,
    tmp_path,
):
    from pathlib import Path

    chip_labs_root = tmp_path / "labs"
    output_dir = tmp_path / "out"
    domain_id = "daily-schedule-reliability-calendar-migration"
    brief = {
        "domain_id": domain_id,
        "domain_name": "Daily Schedule Reliability Calendar Migration",
        "description": "A related but distinct calendar migration chip.",
        "category": "operations",
        "primary_metric": "calendar_migration_utility_score",
        "mutation_axes": [
            {"name": "policy_depth", "values": ["fast", "standard", "deep"]},
            {"name": "risk_boundary", "values": ["low", "medium", "high"]},
        ],
        "task_topics": ["calendar_migration", "loop_engineering"],
        "task_keywords": ["calendar", "migration", "schedule", "timezone"],
        "combine_with": [],
    }
    monkeypatch.setattr(
        "spark_intelligence.auth.runtime.resolve_runtime_provider",
        lambda **_: SimpleNamespace(secret_value="dummy-secret", provider_id="test"),
    )
    monkeypatch.setattr(
        "spark_intelligence.chip_create.pipeline._parse_brief_via_llm",
        lambda prompt, *, provider, state_db=None, brief=brief: brief,
    )

    result = create_chip_from_prompt(
        prompt="build a domain chip for calendar migration reliability",
        config_manager=None,
        state_db=None,
        chip_labs_root=chip_labs_root,
        output_dir=output_dir,
        governor_decision=_chip_create_governor(),
    )

    assert result.ok is True
    chip_path = Path(output_dir) / f"domain-chip-{domain_id}"
    assert not (chip_path / "fixtures" / "domain-fixture-pack.json").exists()
    benchmark_manifest = json.loads(
        (chip_path / "benchmark" / "manifest.json").read_text(encoding="utf-8")
    )
    assert benchmark_manifest["case_source"] == "generic_axis_starter_pack"
    assert benchmark_manifest["fixture_pack_ref"] == ""


def test_create_chip_from_prompt_scaffold_emits_minimum_starter_benchmark_pack(
    monkeypatch, tmp_path
):
    chip_labs_root = tmp_path / "chip-labs"
    scaffold_pkg = chip_labs_root / "src" / "chip_labs" / "chip_factory"
    scaffold_pkg.mkdir(parents=True)
    (chip_labs_root / "src" / "chip_labs" / "__init__.py").write_text(
        "", encoding="utf-8"
    )
    scaffold_pkg.joinpath("__init__.py").write_text("", encoding="utf-8")
    scaffold_pkg.joinpath("scaffold.py").write_text(
        """
import json
from pathlib import Path


def scaffold_chip(brief, output_dir):
    chip_dir = Path(output_dir) / f"domain-chip-{brief['domain_id']}"
    module = brief["domain_id"].replace("-", "_")
    package_dir = chip_dir / "src" / module
    package_dir.mkdir(parents=True)
    package_dir.joinpath("__init__.py").write_text("", encoding="utf-8")
    package_dir.joinpath("cli.py").write_text("def main():\\n    return None\\n", encoding="utf-8")
    manifest = {
        "schema_version": "spark-chip.v1",
        "name": brief["domain_name"],
        "commands": {
            "evaluate": "python -m " + module + " evaluate --input {input} --output {output}"
        },
    }
    chip_dir.joinpath("spark-chip.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    return str(chip_dir)
""",
        encoding="utf-8",
    )

    brief = {
        "domain_id": "pull-request-risk-review",
        "domain_name": "Pull Request Risk Review",
        "description": "Help reviewers identify risky pull requests with private evidence.",
        "category": "analysis",
        "primary_metric": "risk_review_quality_score",
        "mutation_axes": [
            {"name": "risk_focus", "values": ["security", "tests", "data"]},
            {"name": "review_depth", "values": ["fast", "standard", "deep"]},
        ],
        "task_topics": ["pull_request_review", "risk_review"],
        "task_keywords": ["pull", "request", "review", "risk"],
        "combine_with": [],
    }

    monkeypatch.setattr(
        "spark_intelligence.auth.runtime.resolve_runtime_provider",
        lambda **_: SimpleNamespace(secret_value="dummy-secret", provider_id="test"),
    )
    monkeypatch.setattr(
        "spark_intelligence.chip_create.pipeline._parse_brief_via_llm",
        lambda prompt, *, provider, state_db=None: brief,
    )

    governor = _chip_create_governor()
    result = create_chip_from_prompt(
        prompt="build a domain chip for pull request risk review",
        config_manager=None,
        state_db=None,
        chip_labs_root=chip_labs_root,
        output_dir=tmp_path / "out",
        governor_decision=governor,
        command_receipt_context=_chips_create_command_receipt_context(
            SimpleNamespace(
                home=str(tmp_path / "home"),
                prompt="build a domain chip for pull request risk review",
                output_dir=str(tmp_path / "out"),
                chip_labs_root=str(chip_labs_root),
                governor_decision_json=json.dumps(governor),
                json=True,
            )
        ),
    )

    assert result.ok is True
    chip_path = tmp_path / "out" / "domain-chip-pull-request-risk-review"
    manifest = json.loads(
        (chip_path / "benchmark/manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["case_lanes"] == {
        "development": 5,
        "held_out": 5,
        "no_op": 1,
        "adversarial": 3,
    }
    assert manifest["case_count"] == 14
    assert manifest["trap_case_count"] == 3
    assert manifest["visible_pack_role"] == "starter_smoke_only"
    assert manifest["sealed_evaluation_required"] is True
    assert manifest["hidden_case_content_in_artifact"] is False
    sealed_contract = json.loads(
        (chip_path / "benchmark/sealed-evaluation-contract.json").read_text()
    )
    sealed_fixture_manifest = json.loads(
        (chip_path / "benchmark/sealed-fixtures.manifest.json").read_text()
    )
    assert sealed_contract["external_fixture_store_required"] is True
    assert sealed_contract["baseline_candidate_randomization_required"] is True
    assert sealed_fixture_manifest["contains_hidden_case_content"] is False

    cases = [
        json.loads(line)
        for line in (chip_path / "benchmark/cases.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    traps = [
        json.loads(line)
        for line in (chip_path / "benchmark/traps.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    assert Counter(case["lane"] for case in cases) == {
        "development": 5,
        "held_out": 5,
        "no_op": 1,
        "adversarial": 3,
    }
    assert len(cases) == 14
    assert len(traps) == 3

    baseline = json.loads((chip_path / "reports/baseline.json").read_text())
    candidate = json.loads((chip_path / "reports/candidate.json").read_text())
    held_out = json.loads((chip_path / "reports/held-out.json").read_text())
    trap_report = json.loads((chip_path / "reports/trap-results.json").read_text())
    no_op = json.loads((chip_path / "reports/no-op-regression.json").read_text())
    assert baseline["cases_evaluated"] == manifest["case_lanes"]
    assert candidate["cases_evaluated"] == manifest["case_lanes"]
    assert held_out["held_out_case_count"] == 5
    assert trap_report["trap_case_count"] == 3
    assert no_op["no_op_case_count"] == 1


def test_create_chip_from_prompt_scaffold_emits_autoloop_contract_artifacts(
    monkeypatch, tmp_path
):
    chip_labs_root = tmp_path / "chip-labs"
    scaffold_pkg = chip_labs_root / "src" / "chip_labs" / "chip_factory"
    scaffold_pkg.mkdir(parents=True)
    (chip_labs_root / "src" / "chip_labs" / "__init__.py").write_text(
        "", encoding="utf-8"
    )
    scaffold_pkg.joinpath("__init__.py").write_text("", encoding="utf-8")
    scaffold_pkg.joinpath("scaffold.py").write_text(
        """
import json
from pathlib import Path


def scaffold_chip(brief, output_dir):
    chip_dir = Path(output_dir) / f"domain-chip-{brief['domain_id']}"
    module = brief["domain_id"].replace("-", "_")
    package_dir = chip_dir / "src" / module
    package_dir.mkdir(parents=True)
    package_dir.joinpath("__init__.py").write_text("", encoding="utf-8")
    package_dir.joinpath("cli.py").write_text("def main():\\n    return None\\n", encoding="utf-8")
    manifest = {
        "schema_version": "spark-chip.v1",
        "name": brief["domain_name"],
        "commands": {
            "evaluate": "python -m " + module + " evaluate --input {input} --output {output}"
        },
    }
    chip_dir.joinpath("spark-chip.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    return str(chip_dir)
""",
        encoding="utf-8",
    )

    brief = {
        "domain_id": "pull-request-risk-review",
        "domain_name": "Pull Request Risk Review",
        "description": "Help reviewers identify risky pull requests with private evidence.",
        "category": "analysis",
        "primary_metric": "risk_review_quality_score",
        "mutation_axes": [
            {"name": "risk_focus", "values": ["security", "tests", "data"]},
            {"name": "review_depth", "values": ["fast", "standard", "deep"]},
        ],
        "task_topics": ["pull_request_review", "risk_review"],
        "task_keywords": ["pull", "request", "review", "risk"],
        "combine_with": [],
    }

    monkeypatch.setattr(
        "spark_intelligence.auth.runtime.resolve_runtime_provider",
        lambda **_: SimpleNamespace(secret_value="dummy-secret", provider_id="test"),
    )
    monkeypatch.setattr(
        "spark_intelligence.chip_create.pipeline._parse_brief_via_llm",
        lambda prompt, *, provider, state_db=None: brief,
    )

    result = create_chip_from_prompt(
        prompt="build a domain chip for pull request risk review",
        config_manager=None,
        state_db=None,
        chip_labs_root=chip_labs_root,
        output_dir=tmp_path / "out",
        governor_decision=_chip_create_governor(),
    )

    assert result.ok is True
    chip_path = tmp_path / "out" / "domain-chip-pull-request-risk-review"
    for rel in [
        "autoloop/policy.json",
        "autoloop/round-template.json",
        "autoloop/long-loop-trend-contract.json",
        "autoloop/watchtower-regression.json",
        "autoloop/rollback-plan.json",
    ]:
        assert (chip_path / rel).exists(), rel

    policy = json.loads((chip_path / "autoloop/policy.json").read_text())
    assert policy["max_rounds_before_review"] == 5
    assert policy["chip_benefit_ab_required"] is True
    assert policy["minimum_persisted_rounds_before_pass"] == 5
    assert policy["round_template"] == "autoloop/round-template.json"
    assert policy["watchtower_regression_plan"] == "autoloop/watchtower-regression.json"
    assert policy["rollback_plan"] == "autoloop/rollback-plan.json"
    assert policy["approval_boundary"] == "operator approval required before transfer, publication, or network absorption claims"
    assert policy["evidence_refs"] == {
        "baseline_report": "reports/baseline.json",
        "candidate_report": "reports/candidate.json",
        "score_delta_report": "reports/score-delta.json",
        "chip_benefit_ab_report": "reports/chip-benefit-ab.json",
        "long_loop_trend_report": "reports/long-loop-trend.json",
        "distilled_runtime_contract": "distilled-runtime/pull-request-risk-review-fast-path.json",
        "sealed_evaluation_binding": "reports/sealed-evaluation-binding.json",
        "held_out_report": "reports/held-out.json",
        "trap_report": "reports/trap-results.json",
        "no_op_report": "reports/no-op-regression.json",
        "proof_capsule": "reports/proof-capsule-starter.json",
    }

    round_template = json.loads(
        (chip_path / "autoloop/round-template.json").read_text()
    )
    assert round_template["comparison_method"] == "chip_benefit_ab_plus_score_delta_against_baseline_plus_gate_checks"
    assert round_template["candidate_hypothesis_required"] is True
    assert round_template["benefit_ab_command_ref"] == "spark-chip.json#commands.benefit-ab"
    assert round_template["long_loop_trend_command_ref"] == "spark-chip.json#commands.long-loop-trend"
    assert (
        round_template["distilled_runtime_contract"]
        == "distilled-runtime/pull-request-risk-review-fast-path.json"
    )
    assert round_template["promotion_blocked_until"] == [
        "chip_benefit_ab_passed",
        "five_round_long_loop_trend_or_no_safe_win",
        "positive_score_delta",
        "sealed_hidden_evaluation_bound",
        "held_out_passed",
        "trap_passed",
        "no_op_passed",
        "blind_judge_score_refs",
        "safety_judge_clear",
        "adversary_clear",
        "consumer_transfer_passed",
        "ux_readability_passed",
        "proof_auditor_clearance",
        "operator_approval",
    ]
    assert "benchmark answer-key hardcoding" in round_template["forbidden_mutations"]

    watchtower = json.loads(
        (chip_path / "autoloop/watchtower-regression.json").read_text()
    )
    assert {check["check"] for check in watchtower["checks"]} == {
        "route_drift",
        "privacy_boundary",
        "no_op_regression",
        "benchmark_gaming",
        "rollback_readiness",
    }
    assert all(check["blocks_promotion"] is True for check in watchtower["checks"])

    rollback = json.loads((chip_path / "autoloop/rollback-plan.json").read_text())
    assert "no_positive_score_delta" in rollback["rollback_triggers"]
    assert "safety_hard_blocker" in rollback["rollback_triggers"]
    assert "benchmark/" in rollback["protected_surfaces"]

    proof_artifacts = result.to_dict()["proof_artifacts"]
    assert proof_artifacts["autoloop_contract_artifacts"] == {
        "round_template": True,
        "watchtower_regression_plan": True,
        "rollback_plan": True,
        "long_loop_trend_contract": True,
    }


def test_create_chip_from_prompt_scaffold_emits_separated_review_role_packets(
    monkeypatch, tmp_path
):
    chip_labs_root = tmp_path / "chip-labs"
    scaffold_pkg = chip_labs_root / "src" / "chip_labs" / "chip_factory"
    scaffold_pkg.mkdir(parents=True)
    (chip_labs_root / "src" / "chip_labs" / "__init__.py").write_text(
        "", encoding="utf-8"
    )
    scaffold_pkg.joinpath("__init__.py").write_text("", encoding="utf-8")
    scaffold_pkg.joinpath("scaffold.py").write_text(
        """
import json
from pathlib import Path


def scaffold_chip(brief, output_dir):
    chip_dir = Path(output_dir) / f"domain-chip-{brief['domain_id']}"
    module = brief["domain_id"].replace("-", "_")
    package_dir = chip_dir / "src" / module
    package_dir.mkdir(parents=True)
    package_dir.joinpath("__init__.py").write_text("", encoding="utf-8")
    package_dir.joinpath("cli.py").write_text("def main():\\n    return None\\n", encoding="utf-8")
    manifest = {
        "schema_version": "spark-chip.v1",
        "name": brief["domain_name"],
        "commands": {
            "evaluate": "python -m " + module + " evaluate --input {input} --output {output}"
        },
    }
    chip_dir.joinpath("spark-chip.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    return str(chip_dir)
""",
        encoding="utf-8",
    )

    brief = {
        "domain_id": "pull-request-risk-review",
        "domain_name": "Pull Request Risk Review",
        "description": "Help reviewers identify risky pull requests with private evidence.",
        "category": "analysis",
        "primary_metric": "risk_review_quality_score",
        "mutation_axes": [
            {"name": "risk_focus", "values": ["security", "tests", "data"]},
            {"name": "review_depth", "values": ["fast", "standard", "deep"]},
        ],
        "task_topics": ["pull_request_review", "risk_review"],
        "task_keywords": ["pull", "request", "review", "risk"],
        "combine_with": [],
    }

    monkeypatch.setattr(
        "spark_intelligence.auth.runtime.resolve_runtime_provider",
        lambda **_: SimpleNamespace(secret_value="dummy-secret", provider_id="test"),
    )
    monkeypatch.setattr(
        "spark_intelligence.chip_create.pipeline._parse_brief_via_llm",
        lambda prompt, *, provider, state_db=None: brief,
    )

    result = create_chip_from_prompt(
        prompt="build a domain chip for pull request risk review",
        config_manager=None,
        state_db=None,
        chip_labs_root=chip_labs_root,
        output_dir=tmp_path / "out",
        governor_decision=_chip_create_governor(),
    )

    assert result.ok is True
    chip_path = tmp_path / "out" / "domain-chip-pull-request-risk-review"
    expected_packets = [
        "reports/review-role-index.json",
        "reports/blind-judge-packet.md",
        "reports/adversary-review-packet.md",
        "reports/safety-judge-verdict.md",
        "reports/consumer-transfer-proof.md",
        "reports/operator-review-packet.json",
        "reports/evidence_ladder.md",
        "reports/review_packet.md",
    ]
    for rel in expected_packets:
        assert (chip_path / rel).exists(), rel

    blind_packet = (
        chip_path / "reports/blind-judge-packet.md"
    ).read_text(encoding="utf-8")
    assert "Artifact A" in blind_packet
    assert "Artifact B" in blind_packet
    assert "baseline_report" not in blind_packet
    assert "candidate_report" not in blind_packet

    operator_packet = json.loads(
        (chip_path / "reports/operator-review-packet.json").read_text(
            encoding="utf-8"
        )
    )
    assert operator_packet["network_absorbable"] is False
    assert operator_packet["gate_status"]["publication_approval"] is False

    review_index = json.loads(
        (chip_path / "reports/review-role-index.json").read_text(encoding="utf-8")
    )
    assert set(review_index["roles"]) == {
        "blind_judge",
        "adversary",
        "safety_judge",
        "consumer",
        "operator",
    }

    proof_capsule = json.loads(
        (chip_path / "reports/proof-capsule-starter.json").read_text(
            encoding="utf-8"
        )
    )
    assert (
        proof_capsule["proof"]["blind_judge_packets"]["path"]
        == "reports/blind-judge-packet.md"
    )
    assert (
        proof_capsule["proof"]["adversary_report"]["path"]
        == "reports/adversary-review-packet.md"
    )
    assert (
        proof_capsule["proof"]["safety_judge_verdict"]["path"]
        == "reports/safety-judge-verdict.md"
    )
    assert (
        proof_capsule["proof"]["consumer_transfer_proof"]["path"]
        == "reports/consumer-transfer-proof.md"
    )
    assert (
        proof_capsule["proof"]["operator_approval"]["path"]
        == "reports/operator-review-packet.json"
    )

    qa_packet = json.loads(
        (chip_path / "reports/qa-evidence-lane-packet.json").read_text(
            encoding="utf-8"
        )
    )
    assert "reports/blind-judge-packet.md" in qa_packet["evidence_sources"]
    assert (
        "reports/blind-judge-packet.md#anonymized-output-only"
        in qa_packet["evidence_sources"]
    )
    assert "reports/adversary-review-packet.md" in qa_packet["evidence_sources"]
    assert "reports/safety-judge-verdict.md" in qa_packet["evidence_sources"]
    assert "reports/consumer-transfer-proof.md" in qa_packet["evidence_sources"]
    assert "reports/operator-review-packet.json" in qa_packet["evidence_sources"]
    assert "reports/evidence_ladder.md" in qa_packet["evidence_sources"]
    assert "reports/review_packet.md" in qa_packet["evidence_sources"]
    assert qa_packet["metadata"]["blind_judge_refs"] is True
    assert qa_packet["metadata"]["blind_labels_hidden"] is False
    assert qa_packet["metadata"]["output_only_judge"] is False
    assert qa_packet["metadata"].get("blind_judge_score") is None
    assert qa_packet["metadata"]["safety_judge_clear"] is False
    assert qa_packet["metadata"]["consumer_transfer"] is False
    assert qa_packet["metadata"]["operator_approval"] is False
    assert qa_packet["metadata"]["promotion_blocked"] is True


def test_create_chip_from_prompt_result_reports_proof_artifact_summary(
    monkeypatch, tmp_path
):
    chip_labs_root = tmp_path / "chip-labs"
    scaffold_pkg = chip_labs_root / "src" / "chip_labs" / "chip_factory"
    scaffold_pkg.mkdir(parents=True)
    (chip_labs_root / "src" / "chip_labs" / "__init__.py").write_text(
        "", encoding="utf-8"
    )
    scaffold_pkg.joinpath("__init__.py").write_text("", encoding="utf-8")
    scaffold_pkg.joinpath("scaffold.py").write_text(
        """
import json
from pathlib import Path


def scaffold_chip(brief, output_dir):
    chip_dir = Path(output_dir) / f"domain-chip-{brief['domain_id']}"
    module = brief["domain_id"].replace("-", "_")
    package_dir = chip_dir / "src" / module
    package_dir.mkdir(parents=True)
    package_dir.joinpath("__init__.py").write_text("", encoding="utf-8")
    package_dir.joinpath("cli.py").write_text("def main():\\n    return None\\n", encoding="utf-8")
    manifest = {
        "schema_version": "spark-chip.v1",
        "name": brief["domain_name"],
        "commands": {
            "evaluate": "python -m " + module + " evaluate --input {input} --output {output}"
        },
    }
    chip_dir.joinpath("spark-chip.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    return str(chip_dir)
""",
        encoding="utf-8",
    )

    brief = {
        "domain_id": "pull-request-risk-review",
        "domain_name": "Pull Request Risk Review",
        "description": "Help reviewers identify risky pull requests with private evidence.",
        "category": "analysis",
        "primary_metric": "risk_review_quality_score",
        "mutation_axes": [
            {"name": "risk_focus", "values": ["security", "tests", "data"]},
            {"name": "review_depth", "values": ["fast", "standard", "deep"]},
        ],
        "task_topics": ["pull_request_review", "risk_review"],
        "task_keywords": ["pull", "request", "review", "risk"],
        "combine_with": [],
    }

    monkeypatch.setattr(
        "spark_intelligence.auth.runtime.resolve_runtime_provider",
        lambda **_: SimpleNamespace(secret_value="dummy-secret", provider_id="test"),
    )
    monkeypatch.setattr(
        "spark_intelligence.chip_create.pipeline._parse_brief_via_llm",
        lambda prompt, *, provider, state_db=None: brief,
    )

    governor = _chip_create_governor()
    result = create_chip_from_prompt(
        prompt="build a domain chip for pull request risk review",
        config_manager=None,
        state_db=None,
        chip_labs_root=chip_labs_root,
        output_dir=tmp_path / "out",
        governor_decision=governor,
        command_receipt_context=_chips_create_command_receipt_context(
            SimpleNamespace(
                home=str(tmp_path / "home"),
                prompt="build a domain chip for pull request risk review",
                output_dir=str(tmp_path / "out"),
                chip_labs_root=str(chip_labs_root),
                governor_decision_json=json.dumps(governor),
                json=True,
            )
        ),
    )

    assert result.ok is True
    payload = result.to_dict()
    proof_artifacts = payload["proof_artifacts"]
    assert proof_artifacts["manifest_artifacts"] == {
        "creator_intent": True,
        "adapter_map": True,
        "created_artifact_manifest": True,
    }
    assert proof_artifacts["decision_artifacts"] == {
        "consumer_agent_transcript": True,
        "scorecard": True,
        "hard_blockers": True,
        "promotion_decision": True,
        "forbidden_claims": True,
    }
    assert proof_artifacts["domain_contract_artifacts"] == {
        "contract": True,
        "triggers": True,
        "playbook": True,
        "examples": True,
        "hook_contract": True,
        "dcl_manifest": True,
        "dcl_hook_contract": True,
        "dcl_triggers": True,
        "dcl_non_triggers": True,
        "dcl_playbook": True,
        "dcl_examples": True,
        "activation_notes": True,
    }
    assert proof_artifacts["builder_command_receipt"] is True
    assert proof_artifacts["builder_command_receipt_ref"] == "reports/builder-command-receipt.json"
    assert proof_artifacts["builder_command_receipt_status"] == "verified"
    assert proof_artifacts["builder_command_has_governor_decision_json"] is True
    assert proof_artifacts["builder_command_governor_hash"]
    receipt = json.loads(
        (tmp_path / "out" / "domain-chip-pull-request-risk-review" / "reports" / "builder-command-receipt.json").read_text(
            encoding="utf-8"
        )
    )
    assert receipt["schema_version"] == "spark-domain-chip.builder_command_receipt.v1"
    assert receipt["command"]["has_governor_decision_json_flag"] is True
    assert "--governor-decision-json" in receipt["command"]["flags_present"]
    assert "[redacted-json]" in receipt["command"]["argv_shape"]
    assert receipt["governor"]["verification_allowed"] is True
    assert receipt["restrictions"]["publish_allowed"] is False
    assert receipt["restrictions"]["network_absorbable"] is False
    assert "pull request risk review" not in json.dumps(receipt).lower()
    assert proof_artifacts["benchmark_pack"] is True
    assert proof_artifacts["benchmark_case_count"] == 14
    assert proof_artifacts["benchmark_case_lanes"] == {
        "development": 5,
        "held_out": 5,
        "no_op": 1,
        "adversarial": 3,
    }
    assert proof_artifacts["trap_case_count"] == 3
    assert proof_artifacts["sealed_evaluation_contract"] is True
    assert proof_artifacts["sealed_evaluation_contract_ref"] == (
        "benchmark/sealed-evaluation-contract.json"
    )
    assert proof_artifacts["sealed_fixture_manifest"] is True
    assert proof_artifacts["sealed_fixture_manifest_ref"] == (
        "benchmark/sealed-fixtures.manifest.json"
    )
    assert proof_artifacts["sealed_evaluation_binding"] is True
    assert proof_artifacts["sealed_evaluation_binding_ref"] == (
        "reports/sealed-evaluation-binding.json"
    )
    assert proof_artifacts["sealed_evaluation_supported"] is False
    assert proof_artifacts["hidden_case_content_in_chip"] is False
    assert proof_artifacts["autoloop_policy"] is True
    assert proof_artifacts["autoloop_contract_artifacts"] == {
        "round_template": True,
        "watchtower_regression_plan": True,
        "rollback_plan": True,
        "long_loop_trend_contract": True,
    }
    assert proof_artifacts["proof_capsule"] is True
    assert proof_artifacts["qa_evidence_lane_packet"] is True
    assert proof_artifacts["qa_evidence_lane_packet_ref"] == (
        "reports/qa-evidence-lane-packet.json"
    )
    assert proof_artifacts["consumer_transfer_trial_contract"] is True
    assert proof_artifacts["consumer_transfer_trial_contract_ref"] == (
        "reports/consumer-transfer-trial-contract.json"
    )
    assert proof_artifacts["consumer_transfer_trial_binding"] is True
    assert proof_artifacts["consumer_transfer_trial_binding_ref"] == (
        "reports/consumer-transfer-trial-binding.json"
    )
    assert proof_artifacts["consumer_transfer_trial_binding_status"] == "awaiting_report"
    assert proof_artifacts["consumer_transfer_supported"] is False
    assert proof_artifacts["blind_judge_score_binding"] is True
    assert proof_artifacts["blind_judge_score_binding_ref"] == (
        "reports/blind-judge-score-binding.json"
    )
    assert proof_artifacts["blind_judge_score_binding_status"] == "awaiting_scorecard"
    assert proof_artifacts["blind_judge_score_bound"] is False
    assert proof_artifacts["quality_supported"] is False
    assert proof_artifacts["safety_judge_binding"] is True
    assert proof_artifacts["safety_judge_binding_ref"] == (
        "reports/safety-judge-binding.json"
    )
    assert proof_artifacts["safety_judge_binding_status"] == "awaiting_report"
    assert proof_artifacts["safety_clear"] is False
    assert proof_artifacts["adversary_report_binding"] is True
    assert proof_artifacts["adversary_report_binding_ref"] == (
        "reports/adversary-report-binding.json"
    )
    assert proof_artifacts["adversary_report_binding_status"] == "awaiting_report"
    assert proof_artifacts["adversary_clear"] is False
    assert proof_artifacts["evaluate_run_contract"] is True
    assert (
        proof_artifacts["evaluate_run_contract_ref"]
        == "benchmark/evaluate-run-contract.json"
    )
    assert proof_artifacts["evaluate_input_ref"] == "benchmark/cases.jsonl"
    assert (
        proof_artifacts["evaluate_output_ref"]
        == "reports/local-evaluate-smoke.json"
    )
    assert (
        proof_artifacts["evaluate_expected_output_schema"]
        == "spark-domain-chip.local_evaluate_smoke.v1"
    )
    assert proof_artifacts["chip_benefit_ab_contract"] is True
    assert proof_artifacts["chip_benefit_ab_contract_ref"] == (
        "benchmark/chip-benefit-ab-contract.json"
    )
    assert proof_artifacts["chip_benefit_ab_report"] is True
    assert proof_artifacts["chip_benefit_ab_report_ref"] == (
        "reports/chip-benefit-ab.json"
    )
    assert proof_artifacts["chip_benefit_ab_status"] == "blocked"
    assert proof_artifacts["chip_benefit_utility_delta"] == 0.0
    assert proof_artifacts["long_loop_trend_contract"] is True
    assert proof_artifacts["long_loop_trend_contract_ref"] == (
        "autoloop/long-loop-trend-contract.json"
    )
    assert proof_artifacts["long_loop_trend_report"] is True
    assert proof_artifacts["long_loop_trend_report_ref"] == (
        "reports/long-loop-trend.json"
    )
    assert proof_artifacts["long_loop_required_rounds"] == 5
    assert proof_artifacts["long_loop_rounds_observed"] == 0
    assert proof_artifacts["long_loop_positive_trend_observed"] is False
    assert proof_artifacts["promotion_blocked"] is True
    assert proof_artifacts["network_absorbable"] is False
    assert proof_artifacts["consumer_transfer_claimed"] is False
    assert set(proof_artifacts["qa_evidence_lane_blockers"]) == {
        "no_positive_score_delta",
        "sealed_evaluation_report_missing",
        "chip_benefit_ab_missing",
        "long_loop_trend_missing",
        "blind_judge_score_missing",
        "safety_clearance_missing",
        "adversary_review_pending",
        "consumer_transfer_not_claimed",
        "operator_publication_approval_missing",
    }
    assert proof_artifacts["qa_evidence_lane_next_evidence"] == [
        "positive benchmark movement",
        "chip-benefit A/B win",
        "five-round long-loop trend",
        "sealed hidden benchmark report",
        "cited blind score",
        "safety clearance",
        "adversary clearance",
        "consumer transfer",
        "operator approval",
        "publication approval",
    ]
    assert proof_artifacts["review_role_packets"] == {
        "blind_judge": True,
        "adversary": True,
        "safety_judge": True,
        "consumer": True,
        "operator": True,
    }


def test_non_code_domain_chip_review_and_project_commands_do_not_leak_pr_risk(
    monkeypatch, tmp_path
):
    chip_labs_root = tmp_path / "chip-labs"
    scaffold_pkg = chip_labs_root / "src" / "chip_labs" / "chip_factory"
    scaffold_pkg.mkdir(parents=True)
    (chip_labs_root / "src" / "chip_labs" / "__init__.py").write_text(
        "", encoding="utf-8"
    )
    scaffold_pkg.joinpath("__init__.py").write_text("", encoding="utf-8")
    scaffold_pkg.joinpath("scaffold.py").write_text(
        """
import json
from pathlib import Path


def scaffold_chip(brief, output_dir):
    chip_dir = Path(output_dir) / f"domain-chip-{brief['domain_id']}"
    module = brief["domain_id"].replace("-", "_")
    package_dir = chip_dir / "src" / module
    package_dir.mkdir(parents=True)
    package_dir.joinpath("__init__.py").write_text("", encoding="utf-8")
    package_dir.joinpath("cli.py").write_text("def main():\\n    return None\\n", encoding="utf-8")
    manifest = {
        "schema_version": "spark-chip.v1",
        "name": brief["domain_name"],
        "commands": {
            "evaluate": "python -m " + module + " evaluate --input {input} --output {output}"
        },
    }
    chip_dir.joinpath("spark-chip.json").write_text(json.dumps(manifest), encoding="utf-8")
    chip_dir.joinpath("README.md").write_text(
        "# domain-chip-" + brief["domain_id"] + "\\n\\n"
        "## Quick Start\\n\\n```bash\\n"
        "python -m " + module + " evaluate --input input.json --output output.json\\n"
        "```\\n",
        encoding="utf-8",
    )
    chip_dir.joinpath("spark-researcher.project.json").write_text(json.dumps({
        "project_name": "domain-chip-" + brief["domain_id"],
        "commands": {
            "evaluate": {
                "kind": "chip-evaluate",
                "run": "python -m " + module + " evaluate --input {input} --output {output}",
            },
            "suggest": {
                "kind": "chip-suggest",
                "run": "python -m " + module + " suggest --input {input} --output {output}",
            },
        },
    }), encoding="utf-8")
    return str(chip_dir)
""",
        encoding="utf-8",
    )

    brief = {
        "domain_id": "b2c-reachout-proof-run",
        "domain_name": "B2C Reachout Proof Run",
        "description": (
            "Private local starter kit for sandbox CRM outreach drafts, opt-out "
            "boundaries, cadence, tone, and approval-gated follow-ups."
        ),
        "category": "strategy",
        "primary_metric": "approval_safe_draft_score",
        "mutation_axes": [
            {"name": "audience_segment", "values": ["new_leads", "warm_prospects", "trial_users"]},
            {"name": "draft_tone", "values": ["friendly", "concise", "premium"]},
            {"name": "cadence_stage", "values": ["first_touch", "follow_up", "pause_or_no_op"]},
            {"name": "safety_gate", "values": ["privacy_boundary", "opt_out_respect", "approval_required"]},
        ],
        "task_topics": [
            "sandbox_crm_drafts",
            "audience_segmentation",
            "follow_up_cadence",
            "opt_out_privacy",
            "approval_gated_drafts",
        ],
        "task_keywords": [
            "reachout",
            "outreach",
            "draft",
            "crm",
            "audience",
            "segment",
            "cadence",
            "optout",
            "privacy",
            "approval",
        ],
        "combine_with": [],
    }

    monkeypatch.setattr(
        "spark_intelligence.auth.runtime.resolve_runtime_provider",
        lambda **_: SimpleNamespace(secret_value="dummy-secret", provider_id="test"),
    )
    monkeypatch.setattr(
        "spark_intelligence.chip_create.pipeline._parse_brief_via_llm",
        lambda prompt, *, provider, state_db=None: brief,
    )

    result = create_chip_from_prompt(
        prompt="build a domain chip for b2c reachout drafting proof run",
        config_manager=None,
        state_db=None,
        chip_labs_root=chip_labs_root,
        output_dir=tmp_path / "out",
        governor_decision=_chip_create_governor(),
    )

    assert result.ok is True
    chip_path = tmp_path / "out" / "domain-chip-b2c-reachout-proof-run"
    project = json.loads(
        (chip_path / "spark-researcher.project.json").read_text(encoding="utf-8")
    )
    serialized_project = json.dumps(project)
    assert "python -m" not in serialized_project
    assert project["commands"]["evaluate"]["run"] == (
        "python3 chip-runner.py evaluate --input {input} --output {output}"
    )
    assert project["commands"]["suggest"]["run"] == (
        "python3 chip-runner.py suggest --input {input} --output {output}"
    )

    review_input = chip_path / "benchmark" / "b2c-reachout-proof-run-sample-input.json"
    review_input.write_text(
        json.dumps(
            {
                "title": "Warm trial-user follow-up draft",
                "summary": (
                    "Prepare a sandbox CRM follow-up for trial users. The source "
                    "mentions a CRM database export, but the task is draft-only and "
                    "must respect opt-out and approval gates."
                ),
                "notes": "No real customer send; cadence and privacy review only.",
            }
        ),
        encoding="utf-8",
    )
    review_output = chip_path / "reports" / "b2c-review.json"
    review_completed = subprocess.run(
        [
            sys.executable,
            "chip-runner.py",
            "review",
            "--input",
            str(review_input),
            "--output",
            str(review_output),
        ],
        cwd=chip_path,
        capture_output=True,
        text=True,
    )
    assert review_completed.returncode == 0, review_completed.stderr
    review = json.loads(review_output.read_text(encoding="utf-8"))
    assert review["schema_version"] == "spark-domain-chip.private_review.v1"
    assert "domain_evidence_present" in review["risk_signals"]
    assert "database_or_migration_surface" not in review["risk_signals"]
    assert "test_evidence_missing" not in review["risk_signals"]
    assert all("merge" not in action.lower() for action in review["next_actions"])
    assert review["promotion_blocked"] is True

    suggest_output = chip_path / "reports" / "b2c-suggest.json"
    suggest_cmd = (
        project["commands"]["suggest"]["run"]
        .replace("{input}", "benchmark/b2c-reachout-proof-run-sample-input.json")
        .replace("{output}", "reports/b2c-suggest.json")
        .split()
    )
    suggest_completed = subprocess.run(
        suggest_cmd,
        cwd=chip_path,
        capture_output=True,
        text=True,
    )
    assert suggest_completed.returncode == 0, suggest_completed.stderr
    suggest = json.loads(suggest_output.read_text(encoding="utf-8"))
    assert suggest["schema_version"] == "spark-domain-chip.private_suggestion.v1"
    assert suggest["promotion_blocked"] is True
    assert suggest["network_absorbable"] is False
