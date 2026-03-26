from __future__ import annotations

import os
import stat
import subprocess
from dataclasses import dataclass
from getpass import getuser
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
            self._write_env_file("# Spark Intelligence secrets\n")
            created = True
        else:
            self.harden_env_file_permissions()
        return created

    def default_config(self) -> dict[str, Any]:
        return {
            "workspace": {"id": "default", "home": str(self.paths.home), "owner_human_id": "local-operator"},
            "runtime": {"foreground_only": True, "autostart": {"enabled": False}},
            "providers": {"default_provider": None, "records": {}},
            "channels": {"records": {}},
            "identity": {"default_pairing_mode": "pairing", "shared_surfaces_enabled": False},
            "jobs": {"scheduler": {"enabled": True, "tick_seconds": 60}},
            "spark": {
                "researcher": {
                    "enabled": True,
                    "runtime_root": None,
                    "routing": {
                        "conversational_fallback_enabled": True,
                        "conversational_fallback_max_chars": 240,
                    },
                },
                "swarm": {
                    "enabled": True,
                    "runtime_root": None,
                    "api_url": None,
                    "workspace_id": None,
                    "access_token_env": None,
                    "routing": {
                        "auto_recommend_enabled": True,
                        "long_task_word_count": 40,
                    },
                },
                "chips": {"roots": [], "active_keys": [], "pinned_keys": []},
                "specialization_paths": {"roots": [], "active_path_key": None},
            },
            "security": {
                "dangerous_approval_mode": "operator_only",
                "log_redaction": "standard",
                "telegram": {
                    "duplicate_window_size": 128,
                    "max_messages_per_minute": 6,
                    "rate_limit_notice_cooldown_seconds": 30,
                    "max_reply_chars": 3500,
                    "redact_secret_like_replies": True,
                },
            },
        }

    def load(self) -> dict[str, Any]:
        if not self.paths.config_yaml.exists():
            return self.default_config()
        data = yaml.safe_load(self.paths.config_yaml.read_text(encoding="utf-8")) or {}
        return data

    def save(self, data: dict[str, Any]) -> None:
        self.paths.config_yaml.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")

    def get_path(self, dotted_path: str, *, default: Any = None) -> Any:
        current: Any = self.load()
        for part in self._split_path(dotted_path):
            if not isinstance(current, dict) or part not in current:
                return default
            current = current[part]
        return current

    def set_path(self, dotted_path: str, value: Any) -> dict[str, Any]:
        data = self.load()
        current: dict[str, Any] = data
        parts = self._split_path(dotted_path)
        for part in parts[:-1]:
            child = current.get(part)
            if not isinstance(child, dict):
                child = {}
                current[part] = child
            current = child
        current[parts[-1]] = value
        self.save(data)
        return data

    def unset_path(self, dotted_path: str) -> bool:
        data = self.load()
        current: Any = data
        parts = self._split_path(dotted_path)
        for part in parts[:-1]:
            if not isinstance(current, dict) or part not in current:
                return False
            current = current[part]
        if not isinstance(current, dict) or parts[-1] not in current:
            return False
        del current[parts[-1]]
        self.save(data)
        return True

    def upsert_env_secret(self, key: str, value: str) -> None:
        env_map = self.read_env_map()
        env_map[key] = value
        content = "# Spark Intelligence secrets\n" + "".join(f"{name}={env_map[name]}\n" for name in sorted(env_map))
        self._write_env_file(content)

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

    def env_file_permission_status(self) -> tuple[bool, str]:
        if not self.paths.env_file.exists():
            return (False, "missing")
        try:
            if os.name == "nt":
                return self._windows_env_permission_status()
            return self._posix_env_permission_status()
        except Exception as exc:
            return (False, f"permission check failed: {exc}")

    @staticmethod
    def _split_path(dotted_path: str) -> list[str]:
        parts = [part.strip() for part in dotted_path.split(".") if part.strip()]
        if not parts:
            raise ValueError("Config path must not be empty.")
        return parts

    def _write_env_file(self, content: str) -> None:
        self.paths.env_file.write_text(content, encoding="utf-8")
        self.harden_env_file_permissions()

    def harden_env_file_permissions(self) -> None:
        if not self.paths.env_file.exists():
            return
        if os.name == "nt":
            self._harden_windows_env_file_permissions()
            return
        current_mode = stat.S_IMODE(self.paths.env_file.stat().st_mode)
        target_mode = 0o600
        if current_mode != target_mode:
            os.chmod(self.paths.env_file, target_mode)

    def _harden_windows_env_file_permissions(self) -> None:
        principal = self._windows_current_principal()
        subprocess.run(
            [
                "icacls",
                str(self.paths.env_file),
                "/inheritance:r",
                "/grant:r",
                f"{principal}:(R,W)",
            ],
            check=True,
            capture_output=True,
            text=True,
        )

    def _windows_env_permission_status(self) -> tuple[bool, str]:
        principal = self._windows_current_principal()
        result = subprocess.run(
            ["icacls", str(self.paths.env_file)],
            check=True,
            capture_output=True,
            text=True,
        )
        lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        acl_lines: list[str] = []
        for line in lines:
            if "Successfully processed" in line:
                continue
            normalized = line
            path_prefix = f"{self.paths.env_file} "
            if normalized.startswith(path_prefix):
                normalized = normalized[len(path_prefix) :].strip()
            acl_lines.append(normalized)
        if not acl_lines:
            return (False, "acl missing")
        inherited_entries = [line for line in acl_lines if "(I)" in line]
        if inherited_entries:
            return (False, "inherited ACL entries present")
        normalized_principal = principal.lower()
        allowed_entries = [line for line in acl_lines if line.lower().startswith(normalized_principal)]
        if len(allowed_entries) != 1:
            return (False, f"expected one explicit ACL for {principal}")
        if "(R,W)" not in allowed_entries[0]:
            return (False, f"unexpected ACL rights for {principal}")
        return (True, f"owner-only ACL for {principal}")

    def _posix_env_permission_status(self) -> tuple[bool, str]:
        mode = stat.S_IMODE(self.paths.env_file.stat().st_mode)
        if mode & 0o077:
            return (False, f"mode={oct(mode)}")
        return (True, f"mode={oct(mode)}")

    @staticmethod
    def _windows_current_principal() -> str:
        domain = os.environ.get("USERDOMAIN", "")
        username = os.environ.get("USERNAME") or getuser()
        return f"{domain}\\{username}" if domain else username
