from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from spark_intelligence.observability.guardrail_repair import (
    default_builder_state_db,
    resolve_expected_guardrail_denial_events,
)
from spark_intelligence.state.db import StateDB


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Append resolution lifecycle rows for expected Harness Core guardrail denials."
    )
    parser.add_argument("--db", type=Path, help="Builder state.db path. Defaults to ~/.spark/state/spark-intelligence/state.db")
    parser.add_argument("--apply", action="store_true", help="Append resolution rows. Without this flag, only dry-run.")
    args = parser.parse_args()

    state_db = StateDB(args.db.expanduser()) if args.db else default_builder_state_db()
    result = resolve_expected_guardrail_denial_events(state_db, apply=args.apply)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
