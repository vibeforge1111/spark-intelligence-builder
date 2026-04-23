from __future__ import annotations

from spark_intelligence.bot_drafts import (
    detect_generative_intent,
    detect_iteration_intent,
    find_draft_for_iteration,
    list_recent_drafts,
    reply_resembles_draft,
    save_draft,
    update_draft_content,
)

from tests.test_support import SparkTestCase


class DetectGenerativeIntentTests(SparkTestCase):
    def test_write_me_a_tweet(self) -> None:
        self.assertTrue(detect_generative_intent("write me a tweet about BTC"))

    def test_draft_a_plan(self) -> None:
        self.assertTrue(detect_generative_intent("draft a plan for the launch"))

    def test_give_me_a_headline(self) -> None:
        self.assertTrue(detect_generative_intent("give me a headline for the article"))

    def test_create_thread(self) -> None:
        self.assertTrue(detect_generative_intent("create a thread on the rebuild"))

    def test_compose_email(self) -> None:
        self.assertTrue(detect_generative_intent("compose an email to investors"))

    def test_chat_question_is_not_generative(self) -> None:
        self.assertFalse(detect_generative_intent("what's the best time to post on X?"))

    def test_status_question_is_not_generative(self) -> None:
        self.assertFalse(detect_generative_intent("how does the draft system work?"))

    def test_empty_is_not_generative(self) -> None:
        self.assertFalse(detect_generative_intent(""))
        self.assertFalse(detect_generative_intent("   "))


class DetectIterationIntentTests(SparkTestCase):
    def test_tighten_this(self) -> None:
        self.assertIsNotNone(detect_iteration_intent("tighten this"))

    def test_optimize_that(self) -> None:
        self.assertIsNotNone(detect_iteration_intent("optimize that"))

    def test_standalone_shorter(self) -> None:
        self.assertIsNotNone(detect_iteration_intent("shorter"))

    def test_standalone_shorter_please(self) -> None:
        self.assertIsNotNone(detect_iteration_intent("shorter please"))

    def test_standalone_punchier(self) -> None:
        self.assertIsNotNone(detect_iteration_intent("punchier"))

    def test_make_it_shorter(self) -> None:
        self.assertIsNotNone(detect_iteration_intent("make it shorter"))

    def test_make_it_more_punchy(self) -> None:
        self.assertIsNotNone(detect_iteration_intent("make it more punchy"))

    def test_make_it_less_corporate(self) -> None:
        self.assertIsNotNone(detect_iteration_intent("make it less corporate"))

    def test_another_pass(self) -> None:
        self.assertIsNotNone(detect_iteration_intent("another pass"))

    def test_try_again(self) -> None:
        self.assertIsNotNone(detect_iteration_intent("try again"))

    def test_v2(self) -> None:
        self.assertIsNotNone(detect_iteration_intent("v2"))

    def test_tweak_it(self) -> None:
        self.assertIsNotNone(detect_iteration_intent("tweak it"))

    def test_change_the_ending(self) -> None:
        self.assertIsNotNone(detect_iteration_intent("change the ending"))

    def test_cut_the_fluff(self) -> None:
        self.assertIsNotNone(detect_iteration_intent("cut the fluff"))

    def test_tone_it_down(self) -> None:
        self.assertIsNotNone(detect_iteration_intent("tone it down"))

    def test_rework_it(self) -> None:
        self.assertIsNotNone(detect_iteration_intent("rework it"))

    def test_plain_chat_is_not_iteration(self) -> None:
        self.assertIsNone(detect_iteration_intent("what's the weather"))

    def test_unrelated_question_is_not_iteration(self) -> None:
        self.assertIsNone(detect_iteration_intent("how does this tool work?"))


class ReplyResemblesDraftTests(SparkTestCase):
    def test_tighter_version_resembles(self) -> None:
        original = "Got hacked. Three years of account history vanished in four hours. Rebuilding now."
        tighter = "Got hacked. Three years gone in four hours."
        self.assertTrue(reply_resembles_draft(original, tighter))

    def test_rewritten_version_resembles(self) -> None:
        original = "The hacker wiped three years of account history in four hours."
        rewrite = "A hacker erased three years of my account history in four hours."
        self.assertTrue(reply_resembles_draft(original, rewrite))

    def test_topic_drift_does_not_resemble(self) -> None:
        tweet = "Got hacked. Three years of account history gone. Rebuilding slowly."
        drifted = "Two chips are currently active: xcontent and startup. Everything else dormant."
        self.assertFalse(reply_resembles_draft(tweet, drifted))

    def test_completely_unrelated_does_not_resemble(self) -> None:
        a = "Write me a haiku about the ocean and the moon at midnight."
        b = "The quarterly earnings report highlights revenue growth in enterprise accounts."
        self.assertFalse(reply_resembles_draft(a, b))

    def test_empty_draft_falls_back_to_trust(self) -> None:
        # No signal on the draft side; don't block the update.
        self.assertTrue(reply_resembles_draft("", "anything"))

    def test_empty_reply_falls_back_to_trust(self) -> None:
        self.assertTrue(reply_resembles_draft("some prior draft content", ""))


class SaveDraftNoLengthGateTests(SparkTestCase):
    def test_short_content_is_saved(self) -> None:
        draft = save_draft(
            self.state_db,
            external_user_id="u1",
            channel_kind="telegram",
            content="Short tweet draft.",
        )
        self.assertIsNotNone(draft)
        assert draft is not None
        self.assertEqual(draft.content, "Short tweet draft.")

    def test_empty_content_is_rejected(self) -> None:
        draft = save_draft(
            self.state_db,
            external_user_id="u1",
            channel_kind="telegram",
            content="   ",
        )
        self.assertIsNone(draft)

    def test_save_then_list(self) -> None:
        save_draft(
            self.state_db,
            external_user_id="u1",
            channel_kind="telegram",
            content="first draft",
        )
        save_draft(
            self.state_db,
            external_user_id="u1",
            channel_kind="telegram",
            content="second draft",
        )
        drafts = list_recent_drafts(
            self.state_db,
            external_user_id="u1",
            channel_kind="telegram",
            limit=10,
        )
        self.assertEqual(len(drafts), 2)


class IterationRoundTripTests(SparkTestCase):
    def test_iteration_finds_most_recent_draft(self) -> None:
        first = save_draft(
            self.state_db,
            external_user_id="u1",
            channel_kind="telegram",
            content="Hook about Mars survival game mechanics.",
        )
        assert first is not None
        source = find_draft_for_iteration(
            self.state_db,
            external_user_id="u1",
            channel_kind="telegram",
            user_message="tighten this",
        )
        self.assertIsNotNone(source)
        assert source is not None
        self.assertEqual(source.draft_id, first.draft_id)

    def test_update_in_place_replaces_content(self) -> None:
        draft = save_draft(
            self.state_db,
            external_user_id="u1",
            channel_kind="telegram",
            content="v1 text",
        )
        assert draft is not None
        ok = update_draft_content(
            self.state_db,
            draft_id=draft.draft_id,
            content="v2 tighter text",
        )
        self.assertTrue(ok)
        drafts = list_recent_drafts(
            self.state_db,
            external_user_id="u1",
            channel_kind="telegram",
            limit=1,
        )
        self.assertEqual(drafts[0].content, "v2 tighter text")
        self.assertEqual(drafts[0].draft_id, draft.draft_id)
