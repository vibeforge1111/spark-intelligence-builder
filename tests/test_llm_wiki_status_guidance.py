from __future__ import annotations

import pytest

from spark_intelligence.llm_wiki.inbox import VALID_INBOX_STATUSES, _normalize_status as normalize_inbox_status
from spark_intelligence.llm_wiki.promote import (
    VALID_EVAL_COVERAGE_STATUSES,
    VALID_GATE_STATUSES,
    VALID_PROMOTION_STATUSES,
    _normalize_eval_coverage_status,
    _normalize_gate_status,
    _normalize_status as normalize_promotion_status,
)
from spark_intelligence.llm_wiki.user_notes import (
    VALID_USER_NOTE_STATUSES,
    _normalize_status as normalize_user_note_status,
)


@pytest.mark.parametrize(
    ("normalizer", "rejected", "allowed"),
    (
        (normalize_inbox_status, "approved", VALID_INBOX_STATUSES),
        (normalize_promotion_status, "verfied", VALID_PROMOTION_STATUSES),
        (_normalize_eval_coverage_status, "partial", VALID_EVAL_COVERAGE_STATUSES),
        (_normalize_gate_status, "blocked", VALID_GATE_STATUSES),
        (normalize_user_note_status, "verfied", VALID_USER_NOTE_STATUSES),
    ),
)
def test_invalid_status_names_rejected_value_and_allowed_set(normalizer, rejected: str, allowed: set[str]) -> None:
    with pytest.raises(ValueError) as error:
        normalizer(rejected)

    message = str(error.value)
    assert f"'{rejected}'" in message
    assert all(value in message for value in allowed)


@pytest.mark.parametrize(
    ("normalizer", "allowed"),
    (
        (normalize_inbox_status, VALID_INBOX_STATUSES),
        (normalize_promotion_status, VALID_PROMOTION_STATUSES),
        (_normalize_eval_coverage_status, VALID_EVAL_COVERAGE_STATUSES),
        (_normalize_gate_status, VALID_GATE_STATUSES),
        (normalize_user_note_status, VALID_USER_NOTE_STATUSES),
    ),
)
def test_valid_status_normalization_is_unchanged(normalizer, allowed: set[str]) -> None:
    for value in allowed:
        assert normalizer(f" {value.upper()} ") == value
