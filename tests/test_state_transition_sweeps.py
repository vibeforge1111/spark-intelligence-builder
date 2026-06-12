from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from spark_intelligence.bot_drafts import save_draft
from spark_intelligence.identity.service import expire_stale_pairing_reviews
from spark_intelligence.jobs.service import jobs_tick
from spark_intelligence.observability.store import (
    latest_events_by_type,
    sweep_open_builder_runs,
    sweep_stale_tool_call_ledgers,
)

from tests.test_support import SparkTestCase


class StateTransitionSweepTests(SparkTestCase):
    def test_sib_timeout_sweeps_move_stale_states_and_emit_uniform_events(self) -> None:
        old = "2026-01-01T00:00:00+00:00"
        cutoff = "2026-01-02T00:00:00+00:00"
        with self.state_db.connect() as conn:
            conn.execute(
                """
                INSERT INTO tool_call_ledger(
                    ledger_id, turn_id, status, ledger_json, created_at, updated_at
                )
                VALUES ('ledger:stale', 'turn:stale-ledger', 'not_started', ?, ?, ?)
                """,
                (json.dumps({"ledger_id": "ledger:stale", "result": {"status": "not_started"}}), old, old),
            )
            conn.execute(
                """
                INSERT INTO pairing_records(
                    pairing_id, channel_id, external_user_id, human_id, status, updated_at
                )
                VALUES
                    ('pairing:telegram:pending', 'telegram', 'pending', 'human:pending', 'pending', ?),
                    ('pairing:telegram:held', 'telegram', 'held', 'human:held', 'held', ?)
                """,
                (old, old),
            )
            conn.execute(
                """
                INSERT INTO builder_runs(
                    run_id, run_kind, origin_surface, request_id, status, opened_at
                )
                VALUES ('run:stale', 'job:test', 'jobs_tick', 'req:stale-run', 'open', ?)
                """,
                (old,),
            )
            conn.execute(
                """
                INSERT INTO run_registry(
                    run_id, run_kind, surface_kind, status, opened_at
                )
                VALUES ('run:stale', 'job:test', 'jobs_tick', 'open', ?)
                """,
                (old,),
            )
            conn.commit()

        draft = save_draft(
            self.state_db,
            external_user_id="user-1",
            channel_kind="telegram",
            session_id="session:1",
            content="old draft",
        )
        assert draft is not None
        with self.state_db.connect() as conn:
            conn.execute("UPDATE bot_drafts SET created_at = ? WHERE draft_id = ?", (old, draft.draft_id))
            conn.commit()

        from spark_intelligence.bot_drafts import prune_stale_drafts

        self.assertEqual(sweep_stale_tool_call_ledgers(self.state_db, older_than=cutoff), 1)
        self.assertEqual(expire_stale_pairing_reviews(self.state_db, older_than=cutoff), 2)
        self.assertEqual(sweep_open_builder_runs(self.state_db, older_than=cutoff), 1)
        self.assertEqual(prune_stale_drafts(self.state_db, older_than=cutoff), 1)

        with self.state_db.connect() as conn:
            ledger = conn.execute("SELECT status FROM tool_call_ledger WHERE ledger_id = 'ledger:stale'").fetchone()
            pairings = {
                row["pairing_id"]: row["status"]
                for row in conn.execute(
                    "SELECT pairing_id, status FROM pairing_records WHERE pairing_id LIKE 'pairing:telegram:%'"
                )
            }
            run = conn.execute("SELECT status, close_reason FROM builder_runs WHERE run_id = 'run:stale'").fetchone()
            draft_count = conn.execute("SELECT COUNT(*) FROM bot_drafts WHERE draft_id = ?", (draft.draft_id,)).fetchone()[0]

        self.assertEqual(ledger["status"], "expired")
        self.assertEqual(pairings["pairing:telegram:pending"], "expired")
        self.assertEqual(pairings["pairing:telegram:held"], "expired")
        self.assertEqual(run["status"], "stalled")
        self.assertEqual(run["close_reason"], "builder_run_timeout")
        self.assertEqual(draft_count, 0)

        events = latest_events_by_type(self.state_db, event_type="state_transition", limit=20)
        facts = [event["facts_json"] for event in events]
        transitions = {
            (fact["entity_type"], fact["from_state"], fact["to_state"], fact["reason"])
            for fact in facts
        }
        self.assertIn(("sib.tool_call_ledger", "not_started", "expired", "tool_call_ledger_timeout"), transitions)
        self.assertIn(("sib.pairing_record", "pending", "expired", "pairing_review_timeout"), transitions)
        self.assertIn(("sib.pairing_record", "held", "expired", "pairing_review_timeout"), transitions)
        self.assertIn(("sib.builder_run", "open", "stalled", "builder_run_timeout"), transitions)
        self.assertIn(("sib.bot_draft", "active", "expired", "bot_draft_timeout"), transitions)
        for fact in facts:
            for key in ("entity_type", "entity_id", "from_state", "to_state", "reason"):
                self.assertIn(key, fact)
        ledger_fact = next(fact for fact in facts if fact["entity_type"] == "sib.tool_call_ledger")
        self.assertEqual(ledger_fact["entity_id"], "ledger:stale")

    def test_jobs_tick_emits_scheduled_job_clock_transitions(self) -> None:
        with patch("spark_intelligence.jobs.service._run_sib_state_timeout_sweeps", return_value={}), patch(
            "spark_intelligence.jobs.service._run_job",
            return_value="ok",
        ):
            output = jobs_tick(self.config_manager, self.state_db)

        self.assertIn("Ran", output)
        events = latest_events_by_type(self.state_db, event_type="state_transition", limit=20)
        facts = [event["facts_json"] for event in events]
        scheduled = [fact for fact in facts if fact.get("entity_type") == "sib.job_record"]
        self.assertTrue(scheduled)
        self.assertTrue(
            any(
                fact.get("from_state") == "scheduled"
                and fact.get("to_state") == "running"
                and fact.get("reason") == "scheduled_job_tick"
                for fact in scheduled
            )
        )
        self.assertTrue(
            any(
                fact.get("from_state") == "running"
                and fact.get("to_state") == "scheduled"
                and fact.get("reason") == "scheduled_job_completed"
                for fact in scheduled
            )
        )
