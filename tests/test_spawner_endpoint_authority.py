from __future__ import annotations

from unittest.mock import MagicMock, patch

from spark_intelligence.mission_bridge import service as mission_service
from spark_intelligence.schedule_bridge import service as schedule_service
from spark_intelligence.security.spawner_endpoint import (
    request_local_spawner_json,
    resolve_local_spawner_endpoint,
)

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

    def test_board_uses_canonical_route_on_pinned_endpoint(self) -> None:
        payload = {"ok": True, "board": {"running": []}}
        with patch.object(
            mission_service,
            "_SPAWNER_URL",
            "http://127.0.0.1:4174",
        ), patch.object(
            mission_service,
            "request_local_spawner_json",
            return_value=payload,
        ) as request_mock:
            assert mission_service.fetch_board("http://127.0.0.1:4174/") == payload

        endpoint = request_mock.call_args.args[0]
        assert endpoint.hostname == "127.0.0.1"
        assert endpoint.port == 4174
        assert endpoint.request_target == "/api/mission-control/board"
        assert request_mock.call_args.kwargs["method"] == "GET"

    def test_schedule_read_and_delete_share_canonical_endpoint(self) -> None:
        schedules = [{"id": "sched-1"}]
        with patch.object(
            schedule_service,
            "_SPAWNER_URL",
            "http://127.0.0.1:4174",
        ), patch.object(
            schedule_service,
            "request_local_spawner_json",
            side_effect=[{"schedules": schedules}, {"ok": True}],
        ) as request_mock:
            assert schedule_service.fetch_schedules() == schedules
            assert schedule_service.delete_schedule_via_spawner("sched /?secret")

        assert request_mock.call_args_list[0].args[0].request_target == "/api/scheduled"
        assert request_mock.call_args_list[0].kwargs["method"] == "GET"
        assert request_mock.call_args_list[1].args[0].request_target == "/api/scheduled"
        assert request_mock.call_args_list[1].kwargs["method"] == "DELETE"
        assert request_mock.call_args_list[1].kwargs["query"] == {
            "id": "sched /?secret"
        }

    def test_local_transport_pins_address_and_encodes_delete_query(self) -> None:
        endpoint = resolve_local_spawner_endpoint(
            configured_url="http://127.0.0.1:4174",
            requested_url=None,
            route_path="/api/scheduled",
        )
        response = MagicMock(status=200)
        response.read.return_value = b'{"ok": true}'
        connection = MagicMock()
        connection.getresponse.return_value = response

        with patch(
            "spark_intelligence.security.spawner_endpoint._connection_for_endpoint",
            return_value=connection,
        ):
            payload = request_local_spawner_json(
                endpoint,
                method="DELETE",
                query={"id": "sched /?secret"},
                timeout_seconds=5,
                max_response_bytes=1024,
            )

        assert payload == {"ok": True}
        assert connection.request.call_args.args == (
            "DELETE",
            "/api/scheduled?id=sched+%2F%3Fsecret",
        )
        assert connection.request.call_args.kwargs["headers"]["Host"] == "127.0.0.1:4174"
        connection.close.assert_called_once()

    def test_localhost_mixed_resolution_is_rejected_before_connection(self) -> None:
        rows = [
            (2, 1, 6, "", ("127.0.0.1", 4174)),
            (2, 1, 6, "", ("10.0.0.8", 4174)),
        ]
        with patch(
            "spark_intelligence.security.spawner_endpoint.socket.getaddrinfo",
            return_value=rows,
        ), patch(
            "spark_intelligence.security.spawner_endpoint._connection_for_endpoint",
            side_effect=AssertionError("connection must not run"),
        ):
            with self.assertRaisesRegex(RuntimeError, "outside loopback"):
                resolve_local_spawner_endpoint(
                    configured_url="http://localhost:4174",
                    requested_url=None,
                    route_path="/api/scheduled",
                )

    def test_local_transport_blocks_redirect_and_oversized_response(self) -> None:
        endpoint = resolve_local_spawner_endpoint(
            configured_url="http://127.0.0.1:4174",
            requested_url=None,
            route_path="/api/scheduled",
        )
        cases = (
            (302, b"", "redirect blocked"),
            (200, b"12345", "safe size limit"),
        )
        for status, body, message in cases:
            response = MagicMock(status=status)
            response.read.return_value = body
            connection = MagicMock()
            connection.getresponse.return_value = response
            with self.subTest(status=status), patch(
                "spark_intelligence.security.spawner_endpoint._connection_for_endpoint",
                return_value=connection,
            ):
                with self.assertRaisesRegex(RuntimeError, message):
                    request_local_spawner_json(
                        endpoint,
                        method="GET",
                        timeout_seconds=5,
                        max_response_bytes=4,
                    )
                connection.close.assert_called_once()

    def test_policy_errors_hide_supplied_origin_and_exception_lineage(self) -> None:
        secret_url = "http://user:top-secret@127.0.0.1:not-a-port"
        with self.assertRaises(RuntimeError) as raised:
            resolve_local_spawner_endpoint(
                configured_url="http://127.0.0.1:4174",
                requested_url=secret_url,
                route_path="/api/scheduled",
            )

        assert "top-secret" not in str(raised.exception)
        assert secret_url not in str(raised.exception)
        assert raised.exception.__cause__ is None
