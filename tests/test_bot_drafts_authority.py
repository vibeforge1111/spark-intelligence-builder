from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from spark_intelligence.adapters.telegram.runtime import _maybe_save_reply_as_draft
from spark_intelligence.bot_drafts import list_recent_drafts, save_draft, update_draft_content
from spark_intelligence.bridge_authority import authorize_builder_bridge_action
from spark_intelligence.harness_contract import build_vnext_tool_intent_envelope

from tests.test_support import SparkTestCase


BOT_DRAFT_WRITE_TOOL = "bot_draft.write"
BOT_DRAFT_OWNER_SYSTEM = "spark-intelligence-builder"


class BotDraftAuthorityTests(SparkTestCase):
    USER = "tg-draft-authority-test"
    CHANNEL = "telegram"

    def _turn_payload(self, *, tool_name: str, owner_system: str) -> dict:
        payload = build_vnext_tool_intent_envelope(
            surface="telegram",
            actor_id_ref=f"human:{self.USER}",
            request_id=f"bot-draft-authority-{tool_name}",
            source_kind="bot_draft_authority_test",
            tool_name=tool_name,
            owner_system=owner_system,
            mutation_class="writes_memory",
            intent_summary="Fresh Telegram turn authorizes bot draft capture.",
            raw_turn_summary="Bot draft authority test turn remains offloaded.",
            confidence=0.95,
        )
        self.assertIsNotNone(payload)
        return {"turn_intent_envelope_vnext": payload}

    def _governor(self, *, tool_name: str, owner_system: str) -> dict:
        authority = authorize_builder_bridge_action(
            self._turn_payload(tool_name=tool_name, owner_system=owner_system),
            tool_name=tool_name,
            owner_system=owner_system,
            mutation_class="writes_memory",
            state_db=self.state_db,
            request_id=f"bot-draft-authority-{tool_name}",
            channel_id=self.CHANNEL,
            session_id=f"session:{self.USER}",
            human_id=f"human:{self.USER}",
            agent_id="agent:test",
            actor_id="test",
            component="bot_draft_authority_test",
        )
        self.assertTrue(authority.allowed, authority.reason_codes)
        self.assertIsInstance(authority.governor_decision, dict)
        return authority.governor_decision

    def _drafts(self) -> list:
        return list_recent_drafts(
            self.state_db,
            external_user_id=self.USER,
            channel_kind=self.CHANNEL,
            limit=20,
        )

    def test_direct_save_without_governor_is_refused_before_any_write(self) -> None:
        with self.assertRaises(PermissionError):
            save_draft(
                self.state_db,
                external_user_id=self.USER,
                channel_kind=self.CHANNEL,
                content="Ungoverned draft must not persist.",
            )
        self.assertEqual(self._drafts(), [])

    def test_direct_save_and_update_accept_only_bot_draft_authority(self) -> None:
        governor = self._governor(
            tool_name=BOT_DRAFT_WRITE_TOOL,
            owner_system=BOT_DRAFT_OWNER_SYSTEM,
        )
        draft = save_draft(
            self.state_db,
            external_user_id=self.USER,
            channel_kind=self.CHANNEL,
            content="Governed draft persists.",
            governor_decision=governor,
        )
        self.assertIsNotNone(draft)
        assert draft is not None

        with self.assertRaises(PermissionError):
            update_draft_content(
                self.state_db,
                draft_id=draft.draft_id,
                content="Ungoverned overwrite.",
            )
        self.assertEqual(self._drafts()[0].content, "Governed draft persists.")

        self.assertTrue(
            update_draft_content(
                self.state_db,
                draft_id=draft.draft_id,
                content="Governed update lands.",
                governor_decision=governor,
            )
        )
        self.assertEqual(self._drafts()[0].content, "Governed update lands.")

    def test_foreign_memory_authority_cannot_write_bot_drafts_directly(self) -> None:
        foreign_governor = self._governor(
            tool_name="memory.write",
            owner_system="domain-chip-memory",
        )
        with self.assertRaises(PermissionError):
            save_draft(
                self.state_db,
                external_user_id=self.USER,
                channel_kind=self.CHANNEL,
                content="Foreign capability must not authorize this table.",
                governor_decision=foreign_governor,
            )
        self.assertEqual(self._drafts(), [])

    def test_telegram_uses_dedicated_authority_without_debug_file_side_effects(self) -> None:
        update_payload = self._turn_payload(
            tool_name=BOT_DRAFT_WRITE_TOOL,
            owner_system=BOT_DRAFT_OWNER_SYSTEM,
        )
        with TemporaryDirectory() as tmp_dir:
            previous_cwd = Path.cwd()
            os.chdir(tmp_dir)
            try:
                returned = _maybe_save_reply_as_draft(
                    state_db=self.state_db,
                    update_payload=update_payload,
                    external_user_id=self.USER,
                    session_id=f"session:{self.USER}",
                    chip_used=None,
                    reply_text="A governed Telegram draft.",
                    user_message="write me a post",
                )
                self.assertFalse((Path(tmp_dir) / "C:").exists())
            finally:
                os.chdir(previous_cwd)

        self.assertEqual(returned, "A governed Telegram draft.")
        self.assertEqual([draft.content for draft in self._drafts()], ["A governed Telegram draft."])

    def test_plain_generative_telegram_turn_mints_fresh_draft_authority(self) -> None:
        returned = _maybe_save_reply_as_draft(
            state_db=self.state_db,
            update_payload={
                "update_id": 721,
                "message": {
                    "text": "write me a post about R30 authority",
                },
            },
            external_user_id=self.USER,
            session_id=f"session:{self.USER}",
            chip_used=None,
            reply_text="A fresh Telegram turn produced this governed draft.",
            user_message="write me a post about R30 authority",
        )

        self.assertEqual(returned, "A fresh Telegram turn produced this governed draft.")
        self.assertEqual(
            [draft.content for draft in self._drafts()],
            ["A fresh Telegram turn produced this governed draft."],
        )

    def test_plain_iteration_turn_mints_fresh_authority_and_updates_in_place(self) -> None:
        _maybe_save_reply_as_draft(
            state_db=self.state_db,
            update_payload={"update_id": 722, "message": {"text": "write me a post"}},
            external_user_id=self.USER,
            session_id=f"session:{self.USER}",
            chip_used=None,
            reply_text="First governed draft.",
            user_message="write me a post",
        )
        first = self._drafts()
        self.assertEqual(len(first), 1)

        _maybe_save_reply_as_draft(
            state_db=self.state_db,
            update_payload={"update_id": 723, "message": {"text": "make it punchier"}},
            external_user_id=self.USER,
            session_id=f"session:{self.USER}",
            chip_used=None,
            reply_text="Punchier governed draft.",
            user_message="make it punchier",
        )

        updated = self._drafts()
        self.assertEqual(len(updated), 1)
        self.assertEqual(updated[0].draft_id, first[0].draft_id)
        self.assertEqual(updated[0].content, "Punchier governed draft.")

    def test_mismatched_raw_message_cannot_mint_draft_authority(self) -> None:
        returned = _maybe_save_reply_as_draft(
            state_db=self.state_db,
            update_payload={"update_id": 724, "message": {"text": "tell me the weather"}},
            external_user_id=self.USER,
            session_id=f"session:{self.USER}",
            chip_used=None,
            reply_text="This must remain reply-only.",
            user_message="write me a post",
        )

        self.assertEqual(returned, "This must remain reply-only.")
        self.assertEqual(self._drafts(), [])

    def test_telegram_rejects_legacy_memory_authority(self) -> None:
        update_payload = self._turn_payload(
            tool_name="memory.write",
            owner_system="domain-chip-memory",
        )
        _maybe_save_reply_as_draft(
            state_db=self.state_db,
            update_payload=update_payload,
            external_user_id=self.USER,
            session_id=f"session:{self.USER}",
            chip_used=None,
            reply_text="A draft with the wrong capability.",
            user_message="write me a post",
        )
        self.assertEqual(self._drafts(), [])

    def test_telegram_logs_bounded_draft_lookup_failure_without_exception_detail(self) -> None:
        update_payload = self._turn_payload(
            tool_name=BOT_DRAFT_WRITE_TOOL,
            owner_system=BOT_DRAFT_OWNER_SYSTEM,
        )
        with patch(
            "spark_intelligence.bot_drafts.find_draft_for_iteration",
            side_effect=RuntimeError("secret draft database detail"),
        ), self.assertLogs(
            "spark_intelligence.adapters.telegram.runtime",
            level="WARNING",
        ) as captured:
            returned = _maybe_save_reply_as_draft(
                state_db=self.state_db,
                update_payload=update_payload,
                external_user_id=self.USER,
                session_id=f"session:{self.USER}",
                chip_used=None,
                reply_text="A safe iteration reply.",
                user_message="make it shorter",
            )

        self.assertEqual(returned, "A safe iteration reply.")
        self.assertIn("draft lookup failed", captured.output[0].lower())
        self.assertNotIn("secret", "\n".join(captured.output))
