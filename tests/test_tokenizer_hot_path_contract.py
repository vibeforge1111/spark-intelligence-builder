from __future__ import annotations

from unittest.mock import patch

from spark_intelligence import build_quality_review, target_confirmation
from spark_intelligence.context import capsule
from spark_intelligence.user_instructions import service as user_instructions

from tests.test_support import SparkTestCase


class TokenizerHotPathContractTests(SparkTestCase):
    def test_build_route_extraction_uses_compiled_pattern(self) -> None:
        with patch.object(
            build_quality_review.re,
            "findall",
            side_effect=AssertionError("route pattern must be compiled once"),
        ):
            routes = build_quality_review._extract_routes(
                "Check /api/teams, /api/teams, and /mission/active now."
            )

        self.assertEqual(routes, ["/api/teams", "/mission/active"])

    def test_context_capsule_tokenization_uses_compiled_pattern(self) -> None:
        with patch.object(
            capsule.re,
            "findall",
            side_effect=AssertionError("capsule pattern must be compiled once"),
        ):
            tokens = capsule._capsule_tokens("What is the Spark memory-recovery plan?")

        self.assertEqual(tokens, {"spark", "memory-recovery", "plan"})

    def test_target_confirmation_tokenization_uses_compiled_pattern(self) -> None:
        with patch.object(
            target_confirmation.re,
            "findall",
            side_effect=AssertionError("target pattern must be compiled once"),
        ):
            tokens = target_confirmation._tokens("Build in spark_intelligence-builder with tests")

        self.assertEqual(tokens, {"build", "spark", "intelligence", "builder", "tests"})

    def test_instruction_matching_uses_compiled_pattern(self) -> None:
        with self.state_db.connect() as conn:
            conn.execute(
                """
                INSERT INTO user_instructions(
                    instruction_id, external_user_id, channel_kind,
                    instruction_text, source, status, created_at, archived_at
                ) VALUES (?, ?, ?, ?, ?, 'active', ?, NULL)
                """,
                (
                    "inst-compiled-tokenizer",
                    "telegram-user",
                    "telegram",
                    "Always keep replies concise and evidence grounded.",
                    "explicit",
                    "2026-07-18T00:00:00+00:00",
                ),
            )

        with patch.object(
            user_instructions.re,
            "findall",
            side_effect=AssertionError("instruction pattern must be compiled once"),
        ):
            matches = user_instructions.matching_instructions_to_archive(
                self.state_db,
                external_user_id="telegram-user",
                channel_kind="telegram",
                needle="concise evidence replies",
            )

        self.assertEqual([item.instruction_id for item in matches], ["inst-compiled-tokenizer"])
