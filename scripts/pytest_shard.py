from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Iterable, Sequence
from pathlib import Path


def parse_nodeids(lines: Iterable[str]) -> list[str]:
    nodeids = [line.strip() for line in lines if line.strip().startswith("tests/")]
    if not nodeids:
        raise ValueError("pytest collection produced no test node ids")
    if len(nodeids) != len(set(nodeids)):
        raise ValueError("pytest collection produced duplicate test node ids")
    return nodeids


def shard_index(nodeid: str, *, total: int) -> int:
    if total < 1:
        raise ValueError("total must be at least 1")
    digest = hashlib.sha256(nodeid.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], byteorder="big") % total


def select_shard(nodeids: Iterable[str], *, shard: int, total: int) -> list[str]:
    if total < 1:
        raise ValueError("total must be at least 1")
    if shard < 0 or shard >= total:
        raise ValueError(f"shard must be between 0 and {total - 1}")
    return [nodeid for nodeid in nodeids if shard_index(nodeid, total=total) == shard]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Select a stable, complete pytest shard from collected node ids.")
    parser.add_argument("--input", type=Path, required=True, help="pytest --collect-only -q output")
    parser.add_argument("--output", type=Path, required=True, help="newline-delimited selected node ids")
    parser.add_argument("--shard", type=int, required=True)
    parser.add_argument("--total", type=int, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    nodeids = parse_nodeids(args.input.read_text(encoding="utf-8").splitlines())
    selected = select_shard(nodeids, shard=args.shard, total=args.total)
    if not selected:
        raise SystemExit(f"shard {args.shard}/{args.total} selected no tests")
    args.output.write_text("\n".join(selected) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "collected": len(nodeids),
                "selected": len(selected),
                "shard": args.shard,
                "total": args.total,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
