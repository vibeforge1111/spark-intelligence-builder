from __future__ import annotations

import re
import tomllib
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "ci.yml"
PROJECT_PATH = REPO_ROOT / "pyproject.toml"


def test_pytest_shards_cover_each_node_once() -> None:
    from scripts.pytest_shard import select_shard

    nodeids = [f"tests/test_example.py::test_case[{index}]" for index in range(97)]
    shards = [select_shard(nodeids, shard=index, total=6) for index in range(6)]

    flattened = [nodeid for shard in shards for nodeid in shard]
    assert sorted(flattened) == sorted(nodeids)
    assert len(flattened) == len(set(flattened))
    assert all(shard for shard in shards)


def test_pytest_shards_are_stable_across_collection_order() -> None:
    from scripts.pytest_shard import select_shard

    nodeids = [f"tests/test_example.py::test_case[{index}]" for index in range(31)]

    for shard in range(6):
        assert sorted(select_shard(nodeids, shard=shard, total=6)) == sorted(
            select_shard(reversed(nodeids), shard=shard, total=6)
        )


def test_builder_ci_runs_the_complete_collected_suite_without_quarantines() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert "python -m pytest tests --collect-only -q" in workflow
    assert "scripts/pytest_shard.py" in workflow
    assert "@.pytest-shard-nodeids" in workflow
    assert "mapfile" not in workflow
    assert "--ignore=" not in workflow
    assert "--deselect=" not in workflow
    assert "matrix:" in workflow
    assert "shard: [0, 1, 2, 3, 4, 5]" in workflow


def test_builder_ci_harness_checkout_matches_declared_immutable_dependency() -> None:
    project = tomllib.loads(PROJECT_PATH.read_text(encoding="utf-8"))
    dependency = next(
        item for item in project["project"]["dependencies"] if item.startswith("spark-harness-core ")
    )
    match = re.search(r"@([0-9a-f]{40})$", dependency)
    assert match is not None
    harness_commit = match.group(1)
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")

    assert f"ref: {harness_commit}" in workflow
    assert "ref: codex/genesis-harness-core-20260602" not in workflow


def test_builder_ci_domain_memory_checkout_is_immutable() -> None:
    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    checkout = re.search(
        r"repository: vibeforge1111/domain-chip-memory\s+ref: ([^\s#]+)",
        workflow,
    )

    assert checkout is not None
    assert re.fullmatch(r"[0-9a-f]{40}", checkout.group(1))
