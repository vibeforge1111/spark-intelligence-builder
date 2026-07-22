from __future__ import annotations

from pathlib import Path

from spark_intelligence.chip_create.pipeline import _strip_code_fences
from spark_intelligence.spawner_payload_drift import _normalize_key, _normalize_reference, _path_from_reference


def test_strip_code_fences_preserves_json_body() -> None:
    assert _strip_code_fences('```json\n{"domain_id":"risk"}\n```') == '{"domain_id":"risk"}'


def test_spawner_payload_normalizers_preserve_reference_behavior() -> None:
    assert _normalize_key("repoRoot-value") == "repo_root_value"
    assert _normalize_reference("Org Name/Repo_Name!") == "repo-name"


def test_spawner_payload_drive_reference_remains_path_like() -> None:
    assert _path_from_reference(r"C:\Workspace\spark-builder") == Path(r"C:\Workspace\spark-builder").expanduser().resolve()
