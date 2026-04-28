from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Sequence

from spark_intelligence.memory.test_batches import (
    get_memory_test_batch,
    list_memory_test_batches,
    memory_test_batch_command,
    missing_memory_test_targets,
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run curated Spark memory unit-test batches.")
    parser.add_argument("--batch", default="fast-contract", help="Memory test batch id to run.")
    parser.add_argument("--list", action="store_true", help="List available memory test batches.")
    parser.add_argument("--print-only", action="store_true", help="Print the pytest command without executing it.")
    parser.add_argument("--repo-root", default=None, help="Repository root. Defaults to the current working directory.")
    args, extra_args = parser.parse_known_args(argv)

    pytest_extra_args = tuple(extra_args[1:] if extra_args[:1] == ["--"] else extra_args)

    if args.list:
        for batch in list_memory_test_batches():
            print(f"{batch.batch_id}: {batch.description}")
            print(f"  tracks: {', '.join(batch.covered_tracks)}")
            print(f"  targets: {', '.join(batch.pytest_targets)}")
        return 0

    repo_root = Path(args.repo_root).resolve() if args.repo_root else Path.cwd().resolve()
    batch = get_memory_test_batch(args.batch)
    missing = missing_memory_test_targets(batch.batch_id, repo_root=repo_root)
    if missing:
        print(
            f"Memory test batch `{batch.batch_id}` has missing pytest targets: {', '.join(missing)}",
            file=sys.stderr,
        )
        return 2

    command = memory_test_batch_command(batch.batch_id, python_executable=sys.executable, extra_args=pytest_extra_args)
    if args.print_only:
        print(" ".join(_quote_arg(part) for part in command))
        return 0

    completed = subprocess.run(command, cwd=str(repo_root), check=False)
    return int(completed.returncode)


def _quote_arg(value: str) -> str:
    text = str(value)
    if not text or any(char.isspace() for char in text):
        return f'"{text}"'
    return text


if __name__ == "__main__":
    raise SystemExit(main())
