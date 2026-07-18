from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

from spark_intelligence.attachments.hooks import execute_chip_hook_record
from spark_intelligence.attachments.registry import AttachmentRecord


def _record(repo_root: Path, *, command: list[str]) -> AttachmentRecord:
    return AttachmentRecord(
        kind="chip",
        key="environment-probe",
        label="Environment probe",
        repo_root=str(repo_root),
        manifest_path=str(repo_root / "spark-chip.json"),
        hook_manifest_path=None,
        schema_version="spark-chip.v1",
        io_protocol="spark-hook-io.v1",
        status="ready",
        source="test",
        capabilities=["evaluate"],
        commands={"evaluate": command},
        description=None,
        frontier=None,
        task_topics=[],
        task_keywords=[],
        combine_with=[],
        onboarding=None,
    )


def _write_environment_probe(repo_root: Path) -> Path:
    script = repo_root / "probe.py"
    script.write_text(
        """
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--input", required=True)
parser.add_argument("--output", required=True)
args = parser.parse_args()
Path(args.output).write_text(
    json.dumps({"returncode": 0, "result": {"env": dict(os.environ)}}),
    encoding="utf-8",
)
""".strip()
        + "\n",
        encoding="utf-8",
    )
    return script


def test_chip_subprocess_cannot_observe_host_secrets_or_real_home(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "chip"
    repo_root.mkdir()
    script = _write_environment_probe(repo_root)
    host_home = str(tmp_path / "real-home")
    hostile_pythonpath = str(tmp_path / "host-pythonpath")
    hostile_path = str(tmp_path / "host-bin")
    ambient = {
        "SPARK_PROVIDER_TOKEN": "ambient-provider-secret",
        "OPENAI_API_KEY": "ambient-openai-secret",
        "HTTPS_PROXY": "http://proxy-with-secret.invalid",
        "HOME": host_home,
        "USERPROFILE": host_home,
        "PYTHONPATH": hostile_pythonpath,
        "PATH": hostile_path,
    }

    with patch.dict(os.environ, ambient, clear=False), patch(
        "spark_intelligence.attachments.hooks._verify_chip_hook_governor_authority",
        return_value={"allowed": True},
    ):
        execution = execute_chip_hook_record(
            _record(repo_root, command=[sys.executable, str(script)]),
            hook="evaluate",
            payload={"task": "inspect only the delegated payload"},
            governor_decision={"allowed": True},
        )

    assert execution.ok
    child_env = execution.output["result"]["env"]
    for forbidden in (
        "SPARK_PROVIDER_TOKEN",
        "OPENAI_API_KEY",
        "HTTPS_PROXY",
    ):
        assert forbidden not in child_env
    assert child_env["HOME"] != host_home
    assert child_env["USERPROFILE"] != host_home
    assert hostile_pythonpath not in child_env.get("PYTHONPATH", "")
    assert hostile_path not in child_env["PATH"]


def test_chip_subprocess_gets_disposable_home_temp_and_repo_pythonpath(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "chip"
    src_root = repo_root / "src"
    src_root.mkdir(parents=True)
    script = _write_environment_probe(repo_root)

    with patch(
        "spark_intelligence.attachments.hooks._verify_chip_hook_governor_authority",
        return_value={"allowed": True},
    ):
        execution = execute_chip_hook_record(
            _record(repo_root, command=["python", str(script)]),
            hook="evaluate",
            payload={},
            governor_decision={"allowed": True},
        )

    child_env = execution.output["result"]["env"]
    assert child_env["HOME"] == child_env["USERPROFILE"]
    assert child_env["TMPDIR"] == child_env["TMP"] == child_env["TEMP"]
    assert child_env["PYTHONPATH"] == str(src_root)
    assert str(Path(sys.executable).resolve().parent) in child_env["PATH"].split(os.pathsep)
    assert child_env["PYTHONUTF8"] == "1"
    assert child_env["PYTHONDONTWRITEBYTECODE"] == "1"


def test_explicit_payload_still_reaches_chip_without_ambient_environment(
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "chip"
    repo_root.mkdir()
    hook = repo_root / "payload.py"
    hook.write_text(
        """
from __future__ import annotations

import argparse
import json
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--input", required=True)
parser.add_argument("--output", required=True)
args = parser.parse_args()
payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
Path(args.output).write_text(
    json.dumps({"returncode": 0, "result": {"task": payload["task"]}}),
    encoding="utf-8",
)
""".strip()
        + "\n",
        encoding="utf-8",
    )

    with patch(
        "spark_intelligence.attachments.hooks._verify_chip_hook_governor_authority",
        return_value={"allowed": True},
    ):
        execution = execute_chip_hook_record(
            _record(repo_root, command=["python3", str(hook)]),
            hook="evaluate",
            payload={"task": "keep the explicit contract"},
            governor_decision={"allowed": True},
        )

    assert execution.output["result"]["task"] == "keep the explicit contract"
