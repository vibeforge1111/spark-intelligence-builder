from __future__ import annotations

from pathlib import Path

from spark_intelligence.config.loader import ConfigManager


def wiki_root(config_manager: ConfigManager, output_dir: str | Path | None = None) -> Path:
    root = Path(output_dir) if output_dir else config_manager.paths.home / "wiki"
    expanded = root.expanduser()
    if expanded.is_absolute():
        return expanded
    return Path.cwd() / expanded
