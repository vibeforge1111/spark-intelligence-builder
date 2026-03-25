from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class SparkPaths:
    home: Path
    config_yaml: Path
    env_file: Path
    state_db: Path
    logs_dir: Path
    adapters_dir: Path
    migrations_dir: Path


class ConfigManager:
    def __init__(self, paths: SparkPaths):
        self.paths = paths

    @classmethod
    def from_home(cls, home: str | None) -> "ConfigManager":
        root = Path(home).expanduser() if home else Path(os.environ.get("SPARK_INTELLIGENCE_HOME", "~/.spark-intelligence")).expanduser()
        paths = SparkPaths(
            home=root,
            config_yaml=root / "config.yaml",
            env_file=root / ".env",
            state_db=root / "state.db",
            logs_dir=root / "logs",
            adapters_dir=root / "adapters",
            migrations_dir=root / "migrations",
        )
        return cls(paths)

    def bootstrap(self) -> bool:
        created = False
        self.paths.home.mkdir(parents=True, exist_ok=True)
        self.paths.logs_dir.mkdir(exist_ok=True)
        self.paths.adapters_dir.mkdir(exist_ok=True)
        self.paths.migrations_dir.mkdir(exist_ok=True)

        if not self.paths.config_yaml.exists():
            self.save(self.default_config())
            created = True
        if not self.paths.env_file.exists():
            self.paths.env_file.write_text("# Spark Intelligence secrets\n", encoding="utf-8")
            created = True
        return created

    def default_config(self) -> dict[str, Any]:
        return {
            "workspace": {"id": "default", "home": str(self.paths.home)},
            "runtime": {"foreground_only": True, "autostart": {"enabled": False}},
            "providers": {"default_provider": None, "records": {}},
            "channels": {"records": {}},
            "identity": {"default_pairing_mode": "pairing", "shared_surfaces_enabled": False},
            "jobs": {"scheduler": {"enabled": True, "tick_seconds": 60}},
            "spark": {
                "researcher": {"enabled": True, "runtime_root": None},
                "swarm": {"enabled": True, "runtime_root": None},
                "chips": {"roots": []},
                "specialization_paths": {"roots": []},
            },
            "security": {"dangerous_approval_mode": "operator_only", "log_redaction": "standard"},
        }

    def load(self) -> dict[str, Any]:
        if not self.paths.config_yaml.exists():
            return self.default_config()
        data = yaml.safe_load(self.paths.config_yaml.read_text(encoding="utf-8")) or {}
        return data

    def save(self, data: dict[str, Any]) -> None:
        self.paths.config_yaml.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    def upsert_env_secret(self, key: str, value: str) -> None:
        env_map = self.read_env_map()
        env_map[key] = value
        content = "# Spark Intelligence secrets\n" + "".join(f"{name}={env_map[name]}\n" for name in sorted(env_map))
        self.paths.env_file.write_text(content, encoding="utf-8")

    def read_env_map(self) -> dict[str, str]:
        if not self.paths.env_file.exists():
            return {}
        mapping: dict[str, str] = {}
        for line in self.paths.env_file.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, value = stripped.split("=", 1)
            mapping[key] = value
        return mapping
