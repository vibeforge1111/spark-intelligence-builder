from __future__ import annotations

from spark_intelligence.attachments.registry import AttachmentRecord
from spark_intelligence.chip_router.service import (
    RECENT_CHIP_STICKY_BOOST,
    select_chips_for_message,
)


def _chip(
    key: str,
    *,
    task_keywords: list[str] | None = None,
    task_topics: list[str] | None = None,
) -> AttachmentRecord:
    return AttachmentRecord(
        kind="chip",
        key=key,
        label=key,
        repo_root="",
        manifest_path="",
        hook_manifest_path=None,
        schema_version="spark-chip.v1",
        io_protocol="spark-hook-io.v1",
        status="available",
        source="test",
        capabilities=["evaluate"],
        commands={},
        description=None,
        frontier=None,
        task_topics=task_topics or [],
        task_keywords=task_keywords or [],
        combine_with=[],
        onboarding=None,
    )


def test_sticky_chip_with_zero_message_and_history_score_is_not_selected() -> None:
    decision = select_chips_for_message(
        "what is the weather today?",
        [_chip("startup-yc", task_keywords=["startup"], task_topics=["startup"])],
        recent_active_chip_keys=["startup-yc"],
    )

    assert decision.selected == []
    assert decision.considered == []
    assert decision.fell_through is True


def test_sticky_boost_still_applies_when_chip_has_topical_signal() -> None:
    decision = select_chips_for_message(
        "tighten this founder launch plan",
        [_chip("startup-yc", task_keywords=["founder"], task_topics=["startup"])],
        recent_active_chip_keys=["startup-yc"],
    )

    assert [chip.chip_key for chip in decision.selected] == ["startup-yc"]
    selected = decision.selected[0]
    assert selected.sticky_boost_applied is True
    assert selected.score >= 1.0 + RECENT_CHIP_STICKY_BOOST


def test_quoted_or_meta_keyword_does_not_select_chip() -> None:
    # Word-hijack regression (2026-06-16): a chip keyword that is only QUOTED or DISCUSSED
    # ("the word oauth", typed 'oauth') must not select the chip. Topical use, including
    # negation and questions, must still select it (relevance is never dropped).
    oauth = _chip(
        "oauth-chip",
        task_keywords=["oauth", "auth", "login", "sso"],
        task_topics=["authentication"],
    )

    for text in [
        'the keyword "oauth" tripped the router, ignore it',
        "qa case: user typed 'oauth' and the bot wrongly loaded a chip",
        "talking about the word oauth here",
    ]:
        decision = select_chips_for_message(text, [oauth])
        assert decision.selected == [], f"quoted/meta mention hijacked chip selection: {text!r}"

    for text in [
        "set up oauth login for my app",
        "i dont want to use oauth, what are the alternatives",
        "how does oauth work under the hood",
    ]:
        decision = select_chips_for_message(text, [oauth])
        assert [c.chip_key for c in decision.selected] == ["oauth-chip"], (
            f"relevant chip wrongly dropped: {text!r}"
        )
