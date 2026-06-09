from __future__ import annotations

from spark_intelligence.bot_drafts import (
    BotDraftAuthorityError,
    list_recent_drafts,
    save_draft,
    update_draft_content,
)
from spark_intelligence.bot_drafts.service import BOT_DRAFT_OWNER_SYSTEM, BOT_DRAFT_WRITE_TOOL
from spark_intelligence.bridge_authority import authorize_builder_bridge_action
from spark_intelligence.harness_contract import build_vnext_tool_intent_envelope

from tests.test_support import SparkTestCase


class BotDraftsAuthorityTests(SparkTestCase):
    USER = "tg-draft-authority-test"
    CHANNEL = "telegram"

    def _governor(
        self,
        *,
        tool_name: str = BOT_DRAFT_WRITE_TOOL,
        owner_system: str = BOT_DRAFT_OWNER_SYSTEM,
    ) -> dict:
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
        authority = authorize_builder_bridge_action(
            {"turn_intent_envelope_vnext": payload},
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

    def test_save_without_governor_decision_is_refused_and_writes_no_row(self) -> None:
        with self.assertRaises(BotDraftAuthorityError) as ctx:
            save_draft(
                self.state_db,
                external_user_id=self.USER,
                channel_kind=self.CHANNEL,
                content="Ungoverned draft must not persist.",
            )
        self.assertIn("missing_governor_decision", str(ctx.exception))
        self.assertEqual(self._drafts(), [])

    def test_save_with_governor_decision_succeeds(self) -> None:
        draft = save_draft(
            self.state_db,
            external_user_id=self.USER,
            channel_kind=self.CHANNEL,
            content="Governed draft persists.",
            governor_decision=self._governor(),
        )
        self.assertIsNotNone(draft)
        drafts = self._drafts()
        self.assertEqual(len(drafts), 1)
        self.assertEqual(drafts[0].content, "Governed draft persists.")

    def test_update_without_governor_decision_is_refused_and_leaves_content(self) -> None:
        draft = save_draft(
            self.state_db,
            external_user_id=self.USER,
            channel_kind=self.CHANNEL,
            content="Original governed content.",
            governor_decision=self._governor(),
        )
        assert draft is not None
        with self.assertRaises(BotDraftAuthorityError):
            update_draft_content(
                self.state_db,
                draft_id=draft.draft_id,
                content="Ungoverned overwrite.",
            )
        drafts = self._drafts()
        self.assertEqual(len(drafts), 1)
        self.assertEqual(drafts[0].content, "Original governed content.")

    def test_update_with_governor_decision_succeeds(self) -> None:
        draft = save_draft(
            self.state_db,
            external_user_id=self.USER,
            channel_kind=self.CHANNEL,
            content="Original governed content.",
            governor_decision=self._governor(),
        )
        assert draft is not None
        ok = update_draft_content(
            self.state_db,
            draft_id=draft.draft_id,
            content="Governed update lands.",
            governor_decision=self._governor(),
        )
        self.assertTrue(ok)
        drafts = self._drafts()
        self.assertEqual(len(drafts), 1)
        self.assertEqual(drafts[0].content, "Governed update lands.")

    def test_save_with_mismatched_tool_governor_decision_is_refused(self) -> None:
        foreign_decision = self._governor(
            tool_name="user_instruction.write",
            owner_system="spark-intelligence-builder",
        )
        with self.assertRaises(BotDraftAuthorityError):
            save_draft(
                self.state_db,
                external_user_id=self.USER,
                channel_kind=self.CHANNEL,
                content="Foreign capability decision must not authorize drafts.",
                governor_decision=foreign_decision,
            )
        self.assertEqual(self._drafts(), [])
