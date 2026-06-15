from __future__ import annotations

import json
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DEFAULT_BACKUP_ROOT = Path.home() / ".spark_backups"
STATE_BACKUP_PREFIX = "spark-intelligence-state"
DEFAULT_DAILY_RETENTION = 7


@dataclass(frozen=True)
class StateBackupReport:
    backup_path: str
    backup_root: str
    source_state_db: str
    integrity: str
    size_bytes: int
    rotated_paths: list[str]
    weekly_paths: list[str]

    def to_payload(self) -> dict[str, Any]:
        return {
            "backup_path": self.backup_path,
            "backup_root": self.backup_root,
            "source_state_db": self.source_state_db,
            "integrity": self.integrity,
            "size_bytes": self.size_bytes,
            "rotated_paths": self.rotated_paths,
            "weekly_paths": self.weekly_paths,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_payload(), indent=2)

    def to_text(self) -> str:
        lines = [
            "State backup complete",
            f"- backup: {self.backup_path}",
            f"- integrity: {self.integrity}",
            f"- size_bytes: {self.size_bytes}",
            f"- rotated: {len(self.rotated_paths)}",
            f"- weekly_paths: {len(self.weekly_paths)}",
        ]
        return "\n".join(lines)


@dataclass(frozen=True)
class BackupAgeHealth:
    ok: bool
    detail: str
    latest_path: str | None
    age_seconds: float | None


def configured_backup_root(config_manager: Any | None = None) -> Path:
    if config_manager is not None:
        configured = str(config_manager.get_path("spark.backups.root", default="") or "").strip()
        if configured:
            return Path(configured).expanduser()
    return DEFAULT_BACKUP_ROOT


def run_state_backup(
    *,
    home: str | Path,
    backup_root: str | Path | None = None,
    now: datetime | None = None,
    daily_retention: int = DEFAULT_DAILY_RETENTION,
    include_weekly: bool = True,
) -> StateBackupReport:
    now = _utc_now(now)
    home_path = Path(home).expanduser()
    source_state_db = home_path / "state.db"
    if not source_state_db.exists():
        raise FileNotFoundError(f"state.db not found: {source_state_db}")

    root = Path(backup_root).expanduser() if backup_root is not None else DEFAULT_BACKUP_ROOT
    daily_dir = root / "daily"
    daily_dir.mkdir(parents=True, exist_ok=True)
    timestamp = now.strftime("%Y%m%dT%H%M%SZ")
    backup_path = daily_dir / f"{STATE_BACKUP_PREFIX}-{timestamp}.db"
    temp_path = backup_path.with_suffix(".db.tmp")

    _sqlite_backup(source_state_db, temp_path)
    integrity = _sqlite_integrity(temp_path)
    if integrity != "ok":
        temp_path.unlink(missing_ok=True)
        raise RuntimeError(f"backup integrity failed: {integrity}")
    temp_path.replace(backup_path)

    rotated_paths = _rotate_daily_backups(daily_dir, keep=max(1, int(daily_retention)))
    weekly_paths = _copy_weekly_state(home_path=home_path, backup_root=root, now=now) if include_weekly else []
    return StateBackupReport(
        backup_path=str(backup_path),
        backup_root=str(root),
        source_state_db=str(source_state_db),
        integrity=integrity,
        size_bytes=backup_path.stat().st_size,
        rotated_paths=rotated_paths,
        weekly_paths=weekly_paths,
    )


def backup_age_health(
    *,
    backup_root: str | Path | None = None,
    now: datetime | None = None,
    max_age_hours: float = 24.0,
) -> BackupAgeHealth:
    now = _utc_now(now)
    root = Path(backup_root).expanduser() if backup_root is not None else DEFAULT_BACKUP_ROOT
    daily_dir = root / "daily"
    backups = sorted(
        daily_dir.glob(f"{STATE_BACKUP_PREFIX}-*.db"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    ) if daily_dir.exists() else []
    if not backups:
        return BackupAgeHealth(False, f"no daily backups under {daily_dir}", None, None)
    latest = backups[0]
    latest_mtime = datetime.fromtimestamp(latest.stat().st_mtime, tz=UTC)
    age_seconds = max(0.0, (now - latest_mtime).total_seconds())
    age_hours = age_seconds / 3600.0
    ok = age_hours <= max_age_hours
    detail = f"latest={latest} age_hours={age_hours:.2f} size_bytes={latest.stat().st_size}"
    return BackupAgeHealth(ok, detail, str(latest), age_seconds)


def _sqlite_backup(source: Path, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.unlink(missing_ok=True)
    source_uri = f"file:{source}?mode=ro"
    source_conn = sqlite3.connect(source_uri, uri=True)
    dest_conn = sqlite3.connect(dest)
    try:
        source_conn.backup(dest_conn)
    finally:
        dest_conn.close()
        source_conn.close()


def _sqlite_integrity(path: Path) -> str:
    conn = sqlite3.connect(path)
    try:
        row = conn.execute("PRAGMA integrity_check").fetchone()
    finally:
        conn.close()
    return str(row[0]) if row else "missing"


def _rotate_daily_backups(daily_dir: Path, *, keep: int) -> list[str]:
    backups = sorted(
        daily_dir.glob(f"{STATE_BACKUP_PREFIX}-*.db"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    rotated: list[str] = []
    for old_path in backups[keep:]:
        rotated.append(str(old_path))
        old_path.unlink(missing_ok=True)
    return rotated


def _copy_weekly_state(*, home_path: Path, backup_root: Path, now: datetime) -> list[str]:
    iso_year, iso_week, _ = now.isocalendar()
    weekly_dir = backup_root / "weekly" / f"{iso_year}-W{iso_week:02d}"
    if weekly_dir.exists():
        return []
    copied: list[str] = []
    spark_state_dir = home_path.parent
    spark_root = spark_state_dir.parent
    for source, name in (
        (spark_state_dir / "spawner-ui", "spawner-ui"),
        (spark_root / "state" / "approval-ledgers", "approval-ledgers"),
    ):
        if not source.exists():
            continue
        destination = weekly_dir / name
        if source.is_dir():
            shutil.copytree(source, destination)
        else:
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        copied.append(str(destination))
    if copied:
        (weekly_dir / "manifest.json").write_text(
            json.dumps({"created_at": now.isoformat().replace("+00:00", "Z"), "paths": copied}, indent=2),
            encoding="utf-8",
        )
    return copied


def _utc_now(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(UTC)
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
