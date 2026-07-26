from __future__ import annotations

from spark_intelligence.channel.service import set_channel_status
from spark_intelligence.identity.service import review_pairings
from spark_intelligence.ops.service import (
    clear_webhook_alert_snooze,
    list_webhook_alert_events,
    snooze_webhook_alert,
)
from spark_intelligence.self_awareness.capability_ledger import _unknown_capability_ledger_key_message

from tests.test_support import SparkTestCase


class OperatorErrorGuidanceTests(SparkTestCase):
    def test_unknown_webhook_event_names_the_canonical_event_set(self) -> None:
        with self.assertRaises(ValueError) as snooze_error:
            snooze_webhook_alert(state_db=self.state_db, event_name="unknown_event", minutes=15)
        with self.assertRaises(ValueError) as clear_error:
            clear_webhook_alert_snooze(state_db=self.state_db, event_name="unknown_event")

        for message in (str(snooze_error.exception), str(clear_error.exception)):
            self.assertIn("unknown_event", message)
            self.assertIn("Known events:", message)
            for event_name in list_webhook_alert_events():
                self.assertIn(event_name, message)

    def test_review_pairings_names_allowed_statuses(self) -> None:
        with self.assertRaises(ValueError) as error:
            review_pairings(self.state_db, status="approved")

        message = str(error.exception)
        self.assertIn("'approved'", message)
        self.assertIn("Allowed statuses: pending, held.", message)

    def test_unknown_channel_names_configured_channels(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        with self.assertRaises(ValueError) as error:
            set_channel_status(
                config_manager=self.config_manager,
                state_db=self.state_db,
                channel_id="discord",
                status="enabled",
            )

        self.assertIn("Unknown channel 'discord'. Known channels: telegram.", str(error.exception))

    def test_unknown_channel_explains_empty_configuration(self) -> None:
        with self.assertRaises(ValueError) as error:
            set_channel_status(
                config_manager=self.config_manager,
                state_db=self.state_db,
                channel_id="telegram",
                status="enabled",
            )

        self.assertIn("Known channels: none configured.", str(error.exception))

    def test_unknown_capability_key_names_only_valid_ledger_entries(self) -> None:
        message = _unknown_capability_ledger_key_message(
            "missing",
            {"zeta": {"status": "proposed"}, "ignored": "not-an-entry", "alpha": {}},
        )
        self.assertEqual(message, "Unknown capability ledger key: missing. Known keys: alpha, zeta.")

        empty_message = _unknown_capability_ledger_key_message("missing", {})
        self.assertIn("has no entries yet", empty_message)
