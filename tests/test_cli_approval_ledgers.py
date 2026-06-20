import json
import os

from spark_intelligence.cli_approval_ledgers import import_cli_approval_ledgers
from spark_intelligence.state.db import StateDB


def _approval_ledger_payload(index: int) -> dict:
    return {
        "schema_version": "tool-call-ledger-v1",
        "ledger_id": f"ledger:approval:{index}",
        "turn_id": f"turn:approval:{index}",
        "action_id": f"action:approval:{index}",
        "capability_id": "capability:spark-cli:test",
        "tool_name": "spark.test",
        "authorization": {
            "decision_id": f"authorization:approval:{index}",
            "verdict": "allow",
            "action_id": f"action:approval:{index}",
            "capability_id": "capability:spark-cli:test",
        },
        "result": {
            "status": "not_started",
            "summary": "Approval ledger test row.",
        },
        "trace": {
            "id": f"trace:approval:{index}",
        },
        "created_at": f"2026-06-13T0{index}:00:00+00:00",
    }


def test_import_cli_approval_ledgers_prunes_old_files_after_import(tmp_path) -> None:
    state_db = StateDB(tmp_path / "state.db")
    state_db.initialize()
    ledger_dir = tmp_path / "approval-ledgers"
    ledger_dir.mkdir()
    paths = []
    for index in range(3):
        path = ledger_dir / f"ledger-{index}.json"
        path.write_text(json.dumps(_approval_ledger_payload(index)), encoding="utf-8")
        os.utime(path, (index + 1, index + 1))
        paths.append(path)

    result = import_cli_approval_ledgers(state_db, ledger_dir=ledger_dir, retention_cap=2)

    assert result.imported == 3
    assert result.pruned == 1
    assert not paths[0].exists()
    assert paths[1].exists()
    assert paths[2].exists()
    with state_db.connect() as conn:
        count = conn.execute("SELECT COUNT(*) FROM tool_call_ledger").fetchone()[0]
    assert count == 3
