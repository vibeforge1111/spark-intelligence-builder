"""Pure-helper coverage for gateway.runtime classification + filtering.

The Telegram failure classifier, the channel-id/user-ref extractors, and the
log-record filter are quiet boundary functions. They run on every gateway
invocation but have no direct test coverage. These tests pin the matrix so
adding a new transport (Discord/WhatsApp) cannot silently shuffle the
classifier branches.
"""
from __future__ import annotations

import urllib.error

import pytest

from spark_intelligence.gateway.runtime import (
    _classify_telegram_failure,
    _filter_log_records,
    _record_channel_id,
    _record_user_ref,
    _synthetic_telegram_message_id,
    _synthetic_telegram_update_id,
    _telegram_failure_message,
)


class TestClassifyTelegramFailure:
    def test_http_error_yields_http_code_label(self) -> None:
        exc = urllib.error.HTTPError("url", 404, "msg", {}, None)
        assert _classify_telegram_failure(exc) == "http_404"

    def test_url_error_yields_network_error(self) -> None:
        exc = urllib.error.URLError("connection refused")
        assert _classify_telegram_failure(exc) == "network_error"

    def test_runtime_timeout_yields_timeout(self) -> None:
        assert _classify_telegram_failure(RuntimeError("operation timed out")) == "timeout"

    def test_runtime_auth_yields_auth_error(self) -> None:
        assert _classify_telegram_failure(RuntimeError("unauthorized request")) == "auth_error"

    def test_runtime_token_keyword_yields_auth_error(self) -> None:
        assert _classify_telegram_failure(RuntimeError("invalid bot token")) == "auth_error"

    def test_runtime_other_yields_runtime_error(self) -> None:
        assert _classify_telegram_failure(RuntimeError("unrelated")) == "runtime_error"

    def test_unexpected_exception_yields_unexpected_error(self) -> None:
        assert _classify_telegram_failure(ValueError("bad value")) == "unexpected_error"


class TestTelegramFailureMessage:
    def test_http_error_renders_http_status(self) -> None:
        exc = urllib.error.HTTPError("url", 503, "service unavailable", {}, None)
        assert _telegram_failure_message(exc) == "HTTP 503"

    def test_url_error_renders_reason(self) -> None:
        assert _telegram_failure_message(urllib.error.URLError("dns failure")) == "dns failure"

    def test_runtime_error_renders_message(self) -> None:
        assert _telegram_failure_message(RuntimeError("explicit text")) == "explicit text"


class TestRecordChannelId:
    def test_explicit_channel_id_wins(self) -> None:
        assert _record_channel_id({"channel_id": "discord", "event": "telegram_x"}) == "discord"

    def test_telegram_event_inferred(self) -> None:
        assert _record_channel_id({"event": "telegram_update_processed"}) == "telegram"

    def test_discord_event_inferred(self) -> None:
        assert _record_channel_id({"event": "discord_message_ignored"}) == "discord"

    def test_whatsapp_event_inferred(self) -> None:
        assert _record_channel_id({"event": "whatsapp_message"}) == "whatsapp"

    def test_unknown_event_fallback(self) -> None:
        assert _record_channel_id({"event": "other_event"}) == "unknown"

    def test_empty_record_fallback(self) -> None:
        assert _record_channel_id({}) == "unknown"


class TestRecordUserRef:
    def test_telegram_user_id_first(self) -> None:
        assert _record_user_ref({"telegram_user_id": "111", "external_user_id": "222"}) == "111"

    def test_external_user_id_used_when_telegram_absent(self) -> None:
        assert _record_user_ref({"external_user_id": "222"}) == "222"

    def test_user_id_then_chat_id_fallback(self) -> None:
        assert _record_user_ref({"user_id": "333"}) == "333"
        assert _record_user_ref({"chat_id": "444"}) == "444"

    def test_blank_strings_skipped(self) -> None:
        # Empty string should be treated as missing per the {None, ""} guard.
        assert (
            _record_user_ref({"telegram_user_id": "", "external_user_id": "222"}) == "222"
        )

    def test_none_falls_back_to_unknown(self) -> None:
        assert _record_user_ref({}) == "unknown"


class TestFilterLogRecords:
    @pytest.fixture
    def records(self) -> list[dict]:
        return [
            {"event": "telegram_x", "channel_id": "telegram", "telegram_user_id": "1", "decision": "ok", "delivery_ok": True, "response_preview": "hello"},
            {"event": "telegram_y", "channel_id": "telegram", "telegram_user_id": "2", "decision": "denied", "delivery_ok": False, "response_preview": "world"},
            {"event": "discord_z", "channel_id": "discord", "external_user_id": "3", "decision": "ok", "delivery_ok": True, "response_preview": "discord-msg"},
        ]

    def test_filter_by_channel(self, records: list[dict]) -> None:
        filtered = _filter_log_records(records, channel_id="discord")
        assert len(filtered) == 1
        assert filtered[0]["channel_id"] == "discord"

    def test_filter_by_event(self, records: list[dict]) -> None:
        filtered = _filter_log_records(records, event="telegram_y")
        assert len(filtered) == 1

    def test_filter_by_user(self, records: list[dict]) -> None:
        filtered = _filter_log_records(records, user="2")
        assert len(filtered) == 1

    def test_filter_by_decision(self, records: list[dict]) -> None:
        filtered = _filter_log_records(records, decision="denied")
        assert len(filtered) == 1

    def test_filter_by_delivery_ok(self, records: list[dict]) -> None:
        ok = _filter_log_records(records, delivery="ok")
        not_ok = _filter_log_records(records, delivery="fail")
        assert all(r["delivery_ok"] is True for r in ok)
        assert all(r["delivery_ok"] is False for r in not_ok)

    def test_filter_by_contains_is_case_insensitive(self, records: list[dict]) -> None:
        filtered = _filter_log_records(records, contains="DISCORD")
        assert len(filtered) == 1
        assert filtered[0]["channel_id"] == "discord"

    def test_no_filters_returns_all_records(self, records: list[dict]) -> None:
        assert _filter_log_records(records) == records


class TestSyntheticIds:
    def test_synthetic_update_id_is_positive_int(self) -> None:
        assert _synthetic_telegram_update_id() > 0

    def test_synthetic_message_id_within_telegram_range(self) -> None:
        # _synthetic_telegram_message_id() % 2_000_000_000 -> non-negative bounded int.
        value = _synthetic_telegram_message_id()
        assert 0 <= value < 2_000_000_000
