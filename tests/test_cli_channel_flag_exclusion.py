from __future__ import annotations

import pytest

from spark_intelligence.cli import build_parser


@pytest.mark.parametrize(
    "arguments",
    (
        ["channel", "add", "discord", "--allow-legacy-message-webhook", "--disable-legacy-message-webhook"],
        ["channel", "add", "telegram", "--allowed-user", "1", "--clear-allowed-users"],
        ["channel", "telegram-onboard", "--allowed-user", "1", "--clear-allowed-users"],
    ),
)
def test_conflicting_channel_flags_are_rejected_during_parse(arguments: list[str]) -> None:
    with pytest.raises(SystemExit) as error:
        build_parser().parse_args(arguments)

    assert error.value.code == 2


def test_nonconflicting_channel_flags_still_parse() -> None:
    args = build_parser().parse_args(["channel", "add", "telegram", "--allowed-user", "1"])

    assert args.allowed_user == ["1"]
    assert args.clear_allowed_users is False
