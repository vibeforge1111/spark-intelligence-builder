"""Coverage for schedule_bridge formatting + matching helpers.

The Telegram /schedules command renders these strings verbatim to users. The
NL matrix tests cover intent detection, but the human-readable formatters
(``format_schedule_list``, ``format_delete_prompt``, ``format_delete_ambiguous``,
``format_delete_not_found``) and the cron-to-prose helpers
(``humanize_cron`` for the well-supported common cases) have no direct test
coverage. These tests pin the conversational copy so a refactor of the
scheduler bridge cannot silently change what operators see.
"""
from __future__ import annotations

import pytest

from spark_intelligence.schedule_bridge.service import (
    _format_12,
    format_delete_ambiguous,
    format_delete_cancelled,
    format_delete_not_found,
    format_delete_prompt,
    format_delete_success,
    format_schedule_list,
    humanize_cron,
    match_schedules,
)


class TestFormat12:
    def test_midnight_is_12_am(self) -> None:
        assert _format_12(0, 0) == "12 AM"

    def test_one_pm_drops_minute_block_when_zero(self) -> None:
        assert _format_12(13, 0) == "1 PM"

    def test_noon_is_12_pm(self) -> None:
        assert _format_12(12, 0) == "12 PM"

    def test_minute_padded_two_digits(self) -> None:
        assert _format_12(9, 5) == "9:05 AM"


class TestHumanizeCron:
    def test_every_minute_star_pattern(self) -> None:
        assert humanize_cron("* * * * *") == "Every minute"

    def test_daily_at_named_time(self) -> None:
        assert humanize_cron("0 9 * * *") == "Daily at 9 AM"

    def test_weekly_on_monday(self) -> None:
        # 30 minute past hour 0 every Monday (dow=1).
        out = humanize_cron("30 0 * * 1")
        assert "Mon" in out
        assert "12:30 AM" in out

    def test_monthly_pattern(self) -> None:
        # 0:15 on day 1 of every month
        out = humanize_cron("15 0 1 * *")
        assert "Monthly on day 1" in out
        assert "12:15 AM" in out

    def test_unknown_pattern_returns_custom(self) -> None:
        # 5-field but not matching any branch -> "Custom: ..."
        out = humanize_cron("*/2 1,2 * * *")
        assert out.startswith("Custom: ")

    def test_not_five_fields_returned_raw(self) -> None:
        assert humanize_cron("not a cron") == "not a cron"


class TestFormatScheduleList:
    def test_empty_list_renders_empty_message(self) -> None:
        out = format_schedule_list([])
        assert "Nothing on the schedule" in out

    def test_single_item_uses_singular_opener(self) -> None:
        schedules = [
            {
                "id": "sched-001",
                "cron": "0 9 * * *",
                "action": "mission",
                "payload": {"goal": "daily standup"},
                "fireCount": 0,
                "nextFireAt": None,
            }
        ]
        out = format_schedule_list(schedules)
        assert "Just one thing on the schedule" in out
        assert "sched-001" in out
        assert "daily standup" in out

    def test_multi_item_uses_plural_opener(self) -> None:
        schedules = [
            {"id": "a", "cron": "0 9 * * *", "action": "mission", "payload": {"goal": "g1"}, "fireCount": 0},
            {"id": "b", "cron": "0 18 * * *", "action": "mission", "payload": {"goal": "g2"}, "fireCount": 1, "lastStatus": "ok"},
        ]
        out = format_schedule_list(schedules)
        assert "(2 active)" in out
        # Fired-count phrasing.
        assert "Fired 1 time" in out


class TestMatchSchedules:
    def test_schedule_id_exact_match(self) -> None:
        schedules = [{"id": "sched-a"}, {"id": "sched-b"}]
        assert match_schedules(schedules, {"schedule_id": "sched-a"}) == [{"id": "sched-a"}]

    def test_hour_filter(self) -> None:
        schedules = [
            {"id": "a", "cron": "0 9 * * *"},
            {"id": "b", "cron": "30 18 * * *"},
        ]
        out = match_schedules(schedules, {"hour_24": 18})
        assert out == [{"id": "b", "cron": "30 18 * * *"}]

    def test_no_matches_with_hour_falls_back_to_all_candidates(self) -> None:
        # When hour filter strips everything, return remaining candidates unchanged.
        schedules = [{"id": "a", "cron": "0 9 * * *"}]
        out = match_schedules(schedules, {"hour_24": 22})
        assert out == schedules  # No filtered subset -> candidates unchanged

    def test_empty_schedules_returns_empty(self) -> None:
        assert match_schedules([], {"schedule_id": "anything"}) == []


class TestFormatDeleteHelpers:
    def test_format_delete_prompt_mentions_id_and_goal(self) -> None:
        schedule = {
            "id": "sched-x1",
            "cron": "0 9 * * *",
            "action": "mission",
            "payload": {"goal": "standup"},
        }
        out = format_delete_prompt(schedule)
        assert "sched-x1" in out
        assert "standup" in out
        assert "yes cancel" in out
        assert "never mind" in out

    def test_format_delete_prompt_chip_branch(self) -> None:
        schedule = {
            "id": "sched-x2",
            "cron": "0 18 * * *",
            "action": "loop",
            "payload": {"chipKey": "startup-yc"},
        }
        out = format_delete_prompt(schedule)
        assert "startup-yc" in out

    def test_format_delete_ambiguous_lists_candidates(self) -> None:
        matches = [
            {"id": "a", "cron": "0 9 * * *", "action": "mission", "payload": {"goal": "g1"}},
            {"id": "b", "cron": "0 18 * * *", "action": "mission", "payload": {"goal": "g2"}},
        ]
        out = format_delete_ambiguous(matches)
        assert "a:" in out
        assert "b:" in out
        assert "g1" in out
        assert "g2" in out

    def test_format_delete_not_found_with_id(self) -> None:
        out = format_delete_not_found({"schedule_id": "sched-xyz"})
        assert "sched-xyz" in out

    def test_format_delete_not_found_with_time_of_day(self) -> None:
        out = format_delete_not_found({"time_of_day": "morning"})
        assert "morning" in out

    def test_format_delete_not_found_with_hour(self) -> None:
        out = format_delete_not_found({"hour_24": 9})
        assert "9:00" in out

    def test_format_delete_success_mentions_tag(self) -> None:
        schedule = {"action": "mission", "payload": {"goal": "g1"}}
        assert "g1" in format_delete_success(schedule)

    def test_format_delete_cancelled_returns_polite_string(self) -> None:
        assert "keeping" in format_delete_cancelled().lower()
