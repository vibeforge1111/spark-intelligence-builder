from spark_intelligence.chip_create.pipeline import (
    _default_chip_labs_root,
    _default_output_dir,
)


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
