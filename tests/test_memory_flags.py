from pathlib import Path

from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.memory.flags import memory_shadow_mode
from spark_intelligence.memory.orchestrator import _memory_shadow_mode


def test_shadow_mode_defaults_true_when_key_missing(tmp_path: Path) -> None:
    config_manager = ConfigManager.from_home(str(tmp_path))
    config_manager.paths.home.mkdir(parents=True, exist_ok=True)
    config_manager.paths.config_yaml.write_text(
        "spark:\n  memory:\n    enabled: true\n",
        encoding="utf-8",
    )

    assert memory_shadow_mode(config_manager) is True
    assert _memory_shadow_mode(config_manager) is True


def test_no_raw_shadow_mode_reads_outside_flags() -> None:
    root = Path("src/spark_intelligence")
    matches: list[Path] = []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if '"spark.memory.shadow_mode"' in text:
            matches.append(path.relative_to(root))

    normalized = sorted(str(path).replace("\\", "/") for path in matches)
    assert "memory/flags.py" in normalized
    assert set(normalized) <= {"config/loader.py", "memory/flags.py"}
