"""Runtime-level integration tests for the draft capture pipeline.

Exercises `_maybe_save_reply_as_draft` with realistic message sequences,
asserting DB state and checking that no user-facing handle or footer leaks
into the returned text.
"""
from __future__ import annotations

import re

from spark_intelligence.adapters.telegram.runtime import _maybe_capture_user_instruction, _maybe_save_reply_as_draft
from spark_intelligence.bot_drafts import list_recent_drafts

from tests.test_support import SparkTestCase


HANDLE_MARKER = re.compile(r"D-[0-9a-f]{6,12}", re.IGNORECASE)
FOOTER_MARKER = re.compile(r"_\(draft[^)]*\)_", re.IGNORECASE)


class DraftRuntimeIntegrationTests(SparkTestCase):
    USER = "tg-test-001"
    CHANNEL = "telegram"

    def _drafts(self) -> list:
        return list_recent_drafts(
            self.state_db,
            external_user_id=self.USER,
            channel_kind=self.CHANNEL,
            limit=50,
        )

    def _send(self, *, user_message: str, reply_text: str) -> str:
        return _maybe_save_reply_as_draft(
            state_db=self.state_db,
            external_user_id=self.USER,
            session_id=None,
            chip_used=None,
            reply_text=reply_text,
            user_message=user_message,
        )

    def _assert_no_user_facing_noise(self, returned: str) -> None:
        self.assertIsNone(HANDLE_MARKER.search(returned),
                          f"handle leaked into reply: {returned!r}")
        self.assertIsNone(FOOTER_MARKER.search(returned),
                          f"footer leaked into reply: {returned!r}")

    # ---------- generative flow ----------

    def test_generative_request_saves_draft_silently(self) -> None:
        reply = "Here is a tweet about Mars."
        returned = self._send(
            user_message="write me a tweet about mars",
            reply_text=reply,
        )
        self.assertEqual(returned, reply)
        self._assert_no_user_facing_noise(returned)
        drafts = self._drafts()
        self.assertEqual(len(drafts), 1)
        self.assertEqual(drafts[0].content, reply)

    def test_chat_question_saves_nothing(self) -> None:
        reply = "You should post in the evening."
        returned = self._send(
            user_message="what's the best time to post?",
            reply_text=reply,
        )
        self.assertEqual(returned, reply)
        self._assert_no_user_facing_noise(returned)
        self.assertEqual(len(self._drafts()), 0)

    # ---------- iteration flow ----------

    def test_iteration_updates_in_place(self) -> None:
        first_reply = "V1 tweet about survival on Mars."
        self._send(user_message="draft a tweet about mars", reply_text=first_reply)
        initial = self._drafts()
        self.assertEqual(len(initial), 1)
        first_id = initial[0].draft_id

        second_reply = "V2 tighter tweet about Mars."
        returned = self._send(user_message="tighten this", reply_text=second_reply)
        self.assertEqual(returned, second_reply)
        self._assert_no_user_facing_noise(returned)

        after = self._drafts()
        self.assertEqual(len(after), 1, "iteration should update in place, not create a new row")
        self.assertEqual(after[0].draft_id, first_id)
        self.assertEqual(after[0].content, second_reply)

    def test_iteration_with_standalone_adjective(self) -> None:
        self._send(user_message="write a headline", reply_text="Long verbose headline draft.")
        initial_id = self._drafts()[0].draft_id

        returned = self._send(user_message="shorter", reply_text="Short headline.")
        self.assertEqual(returned, "Short headline.")
        self._assert_no_user_facing_noise(returned)
        drafts = self._drafts()
        self.assertEqual(len(drafts), 1)
        self.assertEqual(drafts[0].draft_id, initial_id)
        self.assertEqual(drafts[0].content, "Short headline.")

    def test_iteration_with_make_it_pattern(self) -> None:
        self._send(user_message="draft a pitch", reply_text="Corporate-sounding pitch v1.")
        initial_id = self._drafts()[0].draft_id
        returned = self._send(
            user_message="make it less corporate",
            reply_text="Human-sounding pitch v2.",
        )
        self._assert_no_user_facing_noise(returned)
        drafts = self._drafts()
        self.assertEqual(len(drafts), 1)
        self.assertEqual(drafts[0].draft_id, initial_id)

    def test_iteration_v2_keyword(self) -> None:
        self._send(user_message="write a post", reply_text="v1 post.")
        initial_id = self._drafts()[0].draft_id
        self._send(user_message="v2", reply_text="v2 post.")
        drafts = self._drafts()
        self.assertEqual(len(drafts), 1)
        self.assertEqual(drafts[0].draft_id, initial_id)
        self.assertEqual(drafts[0].content, "v2 post.")

    # ---------- multi-artifact flow ----------

    def test_new_generative_after_existing_draft_creates_second(self) -> None:
        self._send(user_message="draft a tweet about X", reply_text="Tweet about X.")
        self._send(user_message="now write a thread about Y", reply_text="Thread about Y.")
        drafts = self._drafts()
        self.assertEqual(len(drafts), 2)

    # ---------- negative guardrails ----------

    def test_empty_reply_does_not_crash_and_saves_nothing(self) -> None:
        returned = self._send(user_message="write me a tweet", reply_text="")
        self.assertEqual(returned, "")
        self.assertEqual(len(self._drafts()), 0)

    def test_no_user_id_is_noop(self) -> None:
        returned = _maybe_save_reply_as_draft(
            state_db=self.state_db,
            external_user_id="",
            session_id=None,
            chip_used=None,
            reply_text="some reply",
            user_message="write me a tweet",
        )
        self.assertEqual(returned, "some reply")
        self.assertEqual(len(self._drafts()), 0)

    def test_memory_delete_reply_does_not_append_instruction_forget_footer(self) -> None:
        returned = _maybe_capture_user_instruction(
            state_db=self.state_db,
            external_user_id=self.USER,
            user_message="Forget our assumption.",
            reply_text="I'll forget your current assumption.",
            bridge_mode="memory_generic_observation_delete",
            routing_decision="memory_generic_observation_delete",
        )
        self.assertEqual(returned, "I'll forget your current assumption.")
        self._assert_no_user_facing_noise(returned)

    def test_instruction_forget_still_appends_footer_when_not_a_memory_delete(self) -> None:
        returned = _maybe_capture_user_instruction(
            state_db=self.state_db,
            external_user_id=self.USER,
            user_message="Forget our assumption.",
            reply_text="Okay.",
        )
        self.assertIn("no matching saved instruction to forget", returned)
