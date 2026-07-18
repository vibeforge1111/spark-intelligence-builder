from __future__ import annotations

from unittest.mock import patch

from spark_intelligence.mission_bridge import service as mission_service
from spark_intelligence.schedule_bridge import service as schedule_service

from tests.test_support import SparkTestCase


class SpawnerEndpointAuthorityTests(SparkTestCase):
    def test_board_rejects_noncanonical_origins_before_network(self) -> None:
        unsafe_urls = (
            "http://169.254.169.254",
            "http://127.0.0.1:6379",
            "http://localhost:4174",
            "https://127.0.0.1:4174",
            "http://user:secret@127.0.0.1:4174",
            "http://127.0.0.1:4174/other",
            "http://127.0.0.1:4174?target=metadata",
            "http://127.0.0.1:4174#fragment",
        )
        with patch.object(
            mission_service,
            "_SPAWNER_URL",
            "http://127.0.0.1:4174",
        ):
            for unsafe_url in unsafe_urls:
                with self.subTest(unsafe_url=unsafe_url), patch.object(
                    mission_service.urllib.request,
                    "urlopen",
                    side_effect=AssertionError("legacy network must not run"),
                ), patch.object(
                    mission_service,
                    "request_local_spawner_json",
                    side_effect=AssertionError("governed network must not run"),
                    create=True,
                ):
                    assert mission_service.fetch_board(unsafe_url) == {
                        "ok": False,
                        "board": {},
                    }

    def test_schedule_reads_reject_noncanonical_origins_before_network(self) -> None:
        unsafe_urls = (
            "http://10.0.0.1",
            "http://127.0.0.1:6379",
            "http://localhost:4174",
            "file:///tmp/socket",
            "http://127.0.0.1:4174/api/scheduled",
        )
        with patch.object(
            schedule_service,
            "_SPAWNER_URL",
            "http://127.0.0.1:4174",
        ):
            for unsafe_url in unsafe_urls:
                with self.subTest(unsafe_url=unsafe_url), patch.object(
                    schedule_service.urllib.request,
                    "urlopen",
                    side_effect=AssertionError("legacy network must not run"),
                ), patch.object(
                    schedule_service,
                    "request_local_spawner_json",
                    side_effect=AssertionError("governed network must not run"),
                    create=True,
                ):
                    assert schedule_service.fetch_schedules(unsafe_url) == []

    def test_schedule_delete_rejects_noncanonical_origins_before_network(self) -> None:
        unsafe_urls = (
            "http://169.254.169.254",
            "http://127.0.0.1:6379",
            "http://localhost:4174",
            "http://127.0.0.1:4174?redirect=http://169.254.169.254",
        )
        with patch.object(
            schedule_service,
            "_SPAWNER_URL",
            "http://127.0.0.1:4174",
        ):
            for unsafe_url in unsafe_urls:
                with self.subTest(unsafe_url=unsafe_url), patch.object(
                    schedule_service.urllib.request,
                    "urlopen",
                    side_effect=AssertionError("legacy network must not run"),
                ), patch.object(
                    schedule_service,
                    "request_local_spawner_json",
                    side_effect=AssertionError("governed network must not run"),
                    create=True,
                ):
                    assert not schedule_service.delete_schedule_via_spawner(
                        "sched-secret",
                        unsafe_url,
                    )

    def test_bridge_failure_copy_stays_human_and_hides_endpoint_details(self) -> None:
        with patch.object(mission_service, "fetch_board", return_value={"ok": False, "board": {}}):
            reply = mission_service.format_board_from_spawner()

        assert reply == "Couldn't reach mission board right now. Try /board directly."
        assert "127.0.0.1" not in reply
        assert "spawner_url" not in reply
        assert "Mission:" not in reply

