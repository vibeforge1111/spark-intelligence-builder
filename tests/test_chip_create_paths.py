from spark_intelligence.chip_create.pipeline import (
    create_chip_from_prompt,
    _default_chip_labs_root,
    _default_output_dir,
)
from spark_intelligence.bridge_authority import authorize_builder_bridge_action
from spark_intelligence.harness_contract import build_vnext_action_intent_envelope


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
