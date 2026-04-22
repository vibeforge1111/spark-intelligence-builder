"""Retention and revalidation windows for governed memory roles.

Single source of truth for Builder-side lifecycle timing. Moving these
values here makes policy changes reviewable in one place instead of
scattered through executable code.

Per-predicate tuning rationale:

- 14 days: rapidly-changing state (blocker, status, risk, dependency,
  constraint, operational summary fields). Short window reflects that
  these can flip overnight.
- 21 days: medium-horizon commitments (focus, commitment, milestone,
  owner).
- 30 days: long-horizon choices (plan, decision, assumption) plus the
  belief-level and structured-evidence cache.
"""

from __future__ import annotations


BELIEF_REVALIDATION_DAYS = 30
STRUCTURED_EVIDENCE_ARCHIVE_DAYS = 30
RAW_EPISODE_ARCHIVE_DAYS = 14

ACTIVE_STATE_REVALIDATION_DAYS_BY_PREDICATE: dict[str, int] = {
    "profile.current_plan": 30,
    "profile.current_focus": 21,
    "profile.current_decision": 30,
    "profile.current_blocker": 14,
    "profile.current_status": 14,
    "profile.current_commitment": 21,
    "profile.current_milestone": 21,
    "profile.current_risk": 14,
    "profile.current_dependency": 14,
    "profile.current_constraint": 14,
    "profile.current_assumption": 30,
    "profile.current_owner": 21,
    "telegram.summary.latest_meeting": 14,
    "telegram.summary.latest_deadline": 14,
    "telegram.summary.latest_shipped": 14,
}


def active_state_revalidation_days_for(predicate: str | None) -> int | None:
    if predicate is None:
        return None
    return ACTIVE_STATE_REVALIDATION_DAYS_BY_PREDICATE.get(predicate)
