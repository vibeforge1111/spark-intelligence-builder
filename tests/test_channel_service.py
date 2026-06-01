from spark_intelligence.channel.service import set_channel_status

from tests.test_support import SparkTestCase


class ChannelServiceErrorMessageTests(SparkTestCase):
    def test_set_channel_status_unknown_channel_names_known_channels(self) -> None:
        self.add_telegram_channel(pairing_mode="allowlist", allowed_users=["111"])

        with self.assertRaises(ValueError) as ctx:
            set_channel_status(
                config_manager=self.config_manager,
                state_db=self.state_db,
                channel_id="slack",
                status="enabled",
            )
        message = str(ctx.exception)
        self.assertIn("Unknown channel 'slack'.", message)
        self.assertIn("Known channels:", message)
        self.assertIn("telegram", message)

    def test_set_channel_status_unknown_channel_lists_none_when_no_records(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            set_channel_status(
                config_manager=self.config_manager,
                state_db=self.state_db,
                channel_id="telegram",
                status="enabled",
            )
        message = str(ctx.exception)
        self.assertIn("Unknown channel 'telegram'.", message)
        self.assertIn("Known channels: none configured.", message)
