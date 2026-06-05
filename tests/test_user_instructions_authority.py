from __future__ import annotations

import json

from spark_intelligence.bridge_authority import authorize_builder_bridge_action
from spark_intelligence.harness_contract import build_vnext_tool_intent_envelope
from spark_intelligence.user_instructions import list_active_instructions

from tests.test_support import SparkTestCase


class UserInstructionsAuthorityTests(SparkTestCase):
    USER = "tg-cli-test"
    CHANNEL = "telegram"

    def _governor(self, action: str) -> dict:
        action_name = "archive" if action == "archive" else "write"
        tool_name = f"user_instruction.{action_name}"
        payload = build_vnext_tool_intent_envelope(
            surface="cli",
            actor_id_ref=f"human:{self.USER}",
            request_id=f"cli-user-instruction-{action_name}",
            source_kind=f"cli_test_user_instruction_{action_name}",
            tool_name=tool_name,
            owner_system="spark-intelligence-builder",
            mutation_class="writes_memory",
            intent_summary=f"Operator CLI turn authorizes saved-preference {action_name}.",
            raw_turn_summary="CLI saved-preference authority test turn remains offloaded.",
            confidence=0.95,
        )
        self.assertIsNotNone(payload)
        authority = authorize_builder_bridge_action(
            {"turn_intent_envelope_vnext": payload},
            tool_name=tool_name,
            owner_system="spark-intelligence-builder",
            mutation_class="writes_memory",
            state_db=self.state_db,
            request_id=f"cli-user-instruction-{action_name}",
            channel_id=self.CHANNEL,
            session_id=f"session:{self.USER}",
            human_id=f"human:{self.USER}",
            agent_id="agent:test",
            actor_id="test",
            component="cli.user_instruction",
        )
        self.assertTrue(authority.allowed, authority.reason_codes)
        self.assertIsInstance(authority.governor_decision, dict)
        return authority.governor_decision

    def _active(self) -> list:
        return list_active_instructions(
            self.state_db,
            external_user_id=self.USER,
            channel_kind=self.CHANNEL,
        )

    def test_cli_add_requires_governor_decision(self) -> None:
        exit_code, stdout, stderr = self.run_cli(
            "instructions",
            "add",
            "--user-id",
            self.USER,
            "--text",
            "Remember that I prefer concise replies.",
            "--home",
            str(self.home),
        )

        self.assertEqual(exit_code, 1)
        self.assertEqual(stdout, "")
        self.assertIn("missing_governor_decision", stderr)
        self.assertEqual(self._active(), [])

    def test_cli_add_accepts_valid_governor_decision(self) -> None:
        exit_code, stdout, stderr = self.run_cli(
            "instructions",
            "add",
            "--user-id",
            self.USER,
            "--text",
            "Remember that I prefer concise replies.",
            "--home",
            str(self.home),
            "--governor-decision-json",
            json.dumps(self._governor("write")),
        )

        self.assertEqual(exit_code, 0, stderr)
        self.assertIn("Saved preference", stdout)
        self.assertEqual(len(self._active()), 1)

    def test_cli_archive_requires_governor_decision(self) -> None:
        add_exit, _, add_stderr = self.run_cli(
            "instructions",
            "add",
            "--user-id",
            self.USER,
            "--text",
            "Remember that I prefer concise replies.",
            "--home",
            str(self.home),
            "--governor-decision-json",
            json.dumps(self._governor("write")),
        )
        self.assertEqual(add_exit, 0, add_stderr)
        instruction_id = self._active()[0].instruction_id

        exit_code, stdout, stderr = self.run_cli(
            "instructions",
            "archive",
            instruction_id,
            "--home",
            str(self.home),
        )

        self.assertEqual(exit_code, 1)
        self.assertEqual(stdout, "")
        self.assertIn("missing_governor_decision", stderr)
        self.assertEqual(len(self._active()), 1)

    def test_cli_archive_accepts_valid_governor_decision(self) -> None:
        add_exit, _, add_stderr = self.run_cli(
            "instructions",
            "add",
            "--user-id",
            self.USER,
            "--text",
            "Remember that I prefer concise replies.",
            "--home",
            str(self.home),
            "--governor-decision-json",
            json.dumps(self._governor("write")),
        )
        self.assertEqual(add_exit, 0, add_stderr)
        instruction_id = self._active()[0].instruction_id

        exit_code, stdout, stderr = self.run_cli(
            "instructions",
            "archive",
            instruction_id,
            "--home",
            str(self.home),
            "--governor-decision-json",
            json.dumps(self._governor("archive")),
        )

        self.assertEqual(exit_code, 0, stderr)
        self.assertIn(f"Archived preference {instruction_id}", stdout)
        self.assertEqual(self._active(), [])
