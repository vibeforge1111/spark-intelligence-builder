"""Retention and revalidation windows for governed memory roles.

Single source of truth for Builder-side lifecycle timing. Moving these
values here makes policy changes reviewable in one place instead of
scattered through executable code.

Per-predicate revalidation tuning:

- 14 days: rapidly-changing state (blocker, status, risk, dependency,
  constraint, operational summary fields). Short window reflects that
  these can flip overnight.
- 21 days: medium-horizon commitments (focus, commitment, milestone,
  owner).
- 30 days: long-horizon choices (plan, decision, assumption) plus the
  belief-level and structured-evidence cache.

For predicates that have a TelegramGenericPack entry, `revalidation_days`
lives on the pack itself. The dict below only covers orphan predicates
that are not (yet) pack-backed.
"""

from __future__ import annotations


BELIEF_REVALIDATION_DAYS = 30
STRUCTURED_EVIDENCE_ARCHIVE_DAYS = 30
RAW_EPISODE_ARCHIVE_DAYS = 14

_ORPHAN_REVALIDATION_DAYS: dict[str, int] = {
    "telegram.summary.latest_meeting": 14,
    "telegram.summary.latest_deadline": 14,
    "telegram.summary.latest_shipped": 14,
    "entity.name": 30,
    "entity.status": 14,
    "entity.location": 21,
    "entity.owner": 21,
    "entity.deadline": 14,
    "entity.relation": 30,
    "entity.preference": 30,
    "entity.project": 21,
    "entity.blocker": 14,
    "entity.priority": 14,
    "entity.decision": 30,
    "entity.next_action": 14,
    "entity.metric": 14,
}


def active_state_revalidation_days_for(predicate: str | None) -> int | None:
    if predicate is None:
        return None
    from spark_intelligence.memory.generic_observations import pack_revalidation_days

    pack_days = pack_revalidation_days(predicate)
    if pack_days is not None:
        return pack_days
    return _ORPHAN_REVALIDATION_DAYS.get(predicate)
