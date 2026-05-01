from __future__ import annotations

import importlib
import json
import os
import re
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, urlparse

from spark_intelligence.attachments import (
    build_attachment_context,
    list_active_chip_records,
    record_chip_hook_execution,
    run_chip_hook,
    run_first_active_chip_hook,
    run_first_chip_hook_supporting,
    screen_chip_hook_text,
)
from spark_intelligence.chip_router import select_chips_for_message
from spark_intelligence.auth.runtime import RuntimeProviderResolution, resolve_runtime_provider
from spark_intelligence.browser.service import (
    build_browser_navigate_payload,
    build_browser_page_snapshot_payload,
    build_browser_page_interactives_list_payload,
    build_browser_page_dom_extract_payload,
    build_browser_page_text_extract_payload,
    build_browser_status_payload,
    build_browser_tab_wait_payload,
)
from spark_intelligence.capability_router import build_capability_router_prompt_context
from spark_intelligence.character_runtime import ensure_spark_character_path
from spark_intelligence.build_quality_review import (
    build_build_quality_review_reply,
    build_memory_quality_dashboard_operator_reply,
    looks_like_build_quality_review_query,
    looks_like_memory_quality_dashboard_operator_query,
)
from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.context import build_spark_context_capsule
from spark_intelligence.harness_registry import build_harness_prompt_context
from spark_intelligence.memory import (
    archive_belief_from_memory,
    archive_raw_episode_from_memory,
    archive_structured_evidence_from_memory,
    delete_profile_fact_from_memory,
    explain_memory_answer_in_memory,
    hybrid_memory_retrieve,
    inspect_human_memory_in_memory,
    lookup_current_state_in_memory,
    lookup_historical_state_in_memory,
    read_memory_kernel,
    retrieve_memory_evidence_in_memory,
    retrieve_memory_events_in_memory,
    write_belief_to_memory,
    write_profile_fact_to_memory,
    write_raw_episode_to_memory,
    write_structured_evidence_to_memory,
    write_telegram_event_to_memory,
)
from spark_intelligence.memory.episodic_events import (
    build_telegram_memory_event_observation_answer,
    build_telegram_memory_event_query_answer,
    detect_telegram_memory_event_observation,
    detect_telegram_memory_event_query,
    filter_telegram_memory_event_records,
    telegram_event_summary_predicate,
)
from spark_intelligence.memory.generic_observations import (
    assess_telegram_generic_memory_candidate,
    classify_telegram_generic_memory_candidate,
    build_telegram_generic_deletion_answer,
    build_telegram_generic_observation_answer,
    detect_telegram_generic_deletion,
    detect_telegram_generic_observation,
)
from spark_intelligence.memory.session_summaries import build_daily_summary, build_project_summary, build_session_summary
from spark_intelligence.memory.profile_facts import (
    active_state_records_past_revalidation,
    build_profile_fact_explanation_answer,
    build_profile_fact_event_history_answer,
    build_profile_fact_history_answer,
    build_profile_fact_observation_answer,
    build_profile_fact_query_answer,
    build_profile_fact_query_context,
    build_profile_identity_summary_answer,
    detect_profile_fact_observation,
    detect_profile_fact_query,
)
from spark_intelligence.mission_control import (
    build_mission_control_direct_reply,
    build_mission_control_prompt_context,
    looks_like_mission_control_query,
)
from spark_intelligence.observability.policy import screen_model_visible_text
from spark_intelligence.llm.direct_provider import (
    DirectProviderGovernance,
    DirectProviderRequest,
    execute_direct_provider_prompt,
)
from spark_intelligence.llm_wiki import bootstrap_llm_wiki, compile_system_wiki
from spark_intelligence.observability.store import (
    build_text_mutation_facts,
    record_environment_snapshot,
    record_event,
    record_policy_gate_block,
    record_quarantine,
)
from spark_intelligence.observability.store import latest_events_by_type, latest_snapshots_by_surface
from spark_intelligence.personality import (
    build_personality_context,
    build_preference_acknowledgment,
    detect_and_persist_agent_persona_preferences,
    detect_and_persist_nl_preferences,
    detect_personality_query,
    load_personality_profile,
    maybe_evolve_traits,
    record_observation,
)
from spark_intelligence.personality.loader import (
    build_personality_system_directive,
    build_telegram_persona_reply_contract,
)
from spark_intelligence.security.prompt_boundaries import sanitize_prompt_boundary_text
from spark_intelligence.self_awareness import build_self_awareness_capsule
from spark_intelligence.state.db import StateDB
from spark_intelligence.state.hygiene import JSON_RICHNESS_MERGE_GUARD, upsert_runtime_state
from spark_intelligence.system_registry import (
    build_system_registry_direct_reply,
    build_system_registry_prompt_context,
    looks_like_self_awareness_query,
    looks_like_system_registry_query,
)
from spark_intelligence.user_instructions import list_active_instructions
from spark_intelligence.bot_drafts import find_draft_for_iteration

_BROWSER_SEARCH_SUMMARY_MAX_CHARS = 280
_BROWSER_SEARCH_EXCERPT_MAX_CHARS = 480
_RECENT_CONVERSATION_TURN_LIMIT = 4
_ATTACHMENT_PROMPT_CHIP_LIMIT = 12

_KNOWN_CHIP_ROLE_HINTS: dict[str, str] = {
    "startup-yc": "Founder/operator doctrine chip for decisive startup guidance when active.",
    "spark-browser": "Governed browser and search chip for page inspection, browse flows, and source capture.",
    "spark-personality-chip-labs": "Baseline personality import chip; it seeds Builder, but Builder owns live style state after import.",
    "spark-swarm": "Swarm bridge and identity/escalation chip for collective or deep-work execution.",
    "domain-chip-voice-comms": "Speech I/O chip for STT and TTS around the Builder-owned personality.",
}


@dataclass
class ResearcherBridgeResult:
    request_id: str
    reply_text: str
    evidence_summary: str
    escalation_hint: str | None
    trace_ref: str
    mode: str
    runtime_root: str | None
    config_path: str | None
    attachment_context: dict[str, object] | None
    provider_id: str | None = None
    provider_auth_profile_id: str | None = None
    provider_auth_method: str | None = None
    provider_model: str | None = None
    provider_model_family: str | None = None
    provider_execution_transport: str | None = None
    provider_base_url: str | None = None
    provider_source: str | None = None
    routing_decision: str | None = None
    active_chip_key: str | None = None
    active_chip_task_type: str | None = None
    active_chip_evaluate_used: bool = False
    raw_chip_metrics: list[dict[str, Any]] = field(default_factory=list)
    output_keepability: str = "ephemeral_context"
    promotion_disposition: str = "not_promotable"


def _profile_fact_query_related_predicates(predicate: str | None) -> tuple[str, ...]:
    normalized = str(predicate or "").strip()
    if normalized == "profile.startup_name":
        return ("profile.founder_of",)
    return ()


def _profile_fact_record_timestamp(record: dict[str, Any]) -> datetime:
    raw = str(record.get("timestamp") or "").strip()
    if not raw:
        return datetime.min.replace(tzinfo=timezone.utc)
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)


def _profile_fact_record_turn_key(record: dict[str, Any]) -> str:
    turn_ids = record.get("turn_ids")
    if isinstance(turn_ids, list) and turn_ids:
        return str(turn_ids[-1] or "").strip()
    return ""


def _profile_fact_record_value(record: dict[str, Any]) -> str:
    metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    return str(
        record.get("value")
        or record.get("normalized_value")
        or metadata.get("value")
        or metadata.get("normalized_value")
        or record.get("answer")
        or ""
    ).strip()


def _select_profile_fact_query_value(
    *,
    predicate: str | None,
    primary_records: list[dict[str, Any]],
    related_records: list[dict[str, Any]],
) -> str | None:
    target_predicate = str(predicate or "").strip()
    candidates: list[tuple[datetime, str, int, str]] = []
    for record in [*related_records, *primary_records]:
        value = _profile_fact_record_value(record)
        if not value:
            continue
        record_predicate = str(record.get("predicate") or "").strip()
        candidates.append(
            (
                _profile_fact_record_timestamp(record),
                _profile_fact_record_turn_key(record),
                1 if record_predicate == target_predicate else 0,
                value,
            )
        )
    if not candidates:
        return None
    candidates.sort()
    return candidates[-1][3]


def _select_previous_profile_fact_record(
    *,
    current_value: str | None,
    records: list[dict[str, Any]],
) -> dict[str, Any] | None:
    normalized_current = str(current_value or "").strip()
    distinct_records: list[dict[str, Any]] = []
    last_value = ""
    for record in sorted(
        records,
        key=lambda item: (
            _profile_fact_record_timestamp(item),
            _profile_fact_record_turn_key(item),
        ),
    ):
        value = _profile_fact_record_value(record)
        if not value or value == last_value:
            continue
        distinct_records.append(record)
        last_value = value
    if not distinct_records:
        return None
    if normalized_current:
        for record in reversed(distinct_records):
            value = _profile_fact_record_value(record)
            if value and value != normalized_current:
                return record
        return None
    return distinct_records[-1]


def _active_state_maintenance_actions(records: list[dict[str, Any]]) -> list[str]:
    actions: set[str] = set()
    for record in records:
        metadata = record.get("metadata")
        if not isinstance(metadata, dict):
            continue
        action = str(metadata.get("active_state_maintenance_action") or "").strip()
        if action:
            actions.add(action)
    actions.discard("")
    return sorted(actions)


def _ordered_profile_fact_event_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        [record for record in records if _profile_fact_record_value(record)],
        key=lambda item: (
            _profile_fact_record_timestamp(item),
            _profile_fact_record_turn_key(item),
        ),
    )


def _inspect_profile_fact_records(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    human_id: str,
    predicate: str | None,
    related_predicates: tuple[str, ...] = (),
    actor_id: str = "researcher_bridge",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    target_predicate = str(predicate or "").strip()
    relevant_predicates = {
        target_predicate,
        *(str(item or "").strip() for item in related_predicates),
    }
    relevant_predicates.discard("")
    if not target_predicate or not relevant_predicates:
        return [], []
    inspection = inspect_human_memory_in_memory(
        config_manager=config_manager,
        state_db=state_db,
        human_id=human_id,
        actor_id=actor_id,
    )
    if inspection.read_result.abstained or not inspection.read_result.records:
        return [], []
    return _partition_profile_fact_records(
        records=inspection.read_result.records,
        predicate=predicate,
        related_predicates=related_predicates,
    )


def _partition_profile_fact_records(
    *,
    records: list[dict[str, Any]],
    predicate: str | None,
    related_predicates: tuple[str, ...] = (),
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    target_predicate = str(predicate or "").strip()
    relevant_predicates = {
        target_predicate,
        *(str(item or "").strip() for item in related_predicates),
    }
    relevant_predicates.discard("")
    if not target_predicate or not relevant_predicates:
        return [], []
    primary_records: list[dict[str, Any]] = []
    related_records_out: list[dict[str, Any]] = []
    for record in records:
        record_predicate = str(record.get("predicate") or "").strip()
        if record_predicate not in relevant_predicates:
            continue
        if record_predicate == target_predicate:
            primary_records.append(record)
        else:
            related_records_out.append(record)
    return primary_records, related_records_out


def _profile_fact_explanation_has_content(payload: dict[str, Any] | None) -> bool:
    data = payload or {}
    if str(data.get("answer") or "").strip():
        return True
    evidence = data.get("evidence")
    if isinstance(evidence, list) and any(isinstance(item, dict) and str(item.get("text") or "").strip() for item in evidence):
        return True
    events = data.get("events")
    if isinstance(events, list) and any(item for item in events):
        return True
    return False


@dataclass(frozen=True)
class OpenMemoryRecallQuery:
    topic: str
    query_kind: str = "evidence_recall"


@dataclass(frozen=True)
class EntityStateHistoryQuery:
    topic: str
    attribute: str
    predicate: str


@dataclass(frozen=True)
class EntityStateSummaryQuery:
    topic: str


@dataclass(frozen=True)
class BeliefRecallQuery:
    topic: str


@dataclass(frozen=True)
class EpisodicDailyRecallQuery:
    day: str
    query_kind: str


@dataclass(frozen=True)
class EpisodicProjectRecallQuery:
    project_key: str
    query_kind: str


@dataclass(frozen=True)
class EpisodicSessionRecallQuery:
    session_id: str
    query_kind: str


_ENTITY_FOLLOWUP_PRONOUN_TOPICS: set[str] = {"it", "that", "this", "them", "they"}


_ENTITY_STATE_SUMMARY_ATTRIBUTES: tuple[tuple[str, str, str], ...] = (
    ("status", "entity.status", "Status"),
    ("owner", "entity.owner", "Owner"),
    ("deadline", "entity.deadline", "Deadline"),
    ("blocker", "entity.blocker", "Blocker"),
    ("priority", "entity.priority", "Priority"),
    ("decision", "entity.decision", "Decision"),
    ("next_action", "entity.next_action", "Next action"),
    ("metric", "entity.metric", "Metric"),
    ("project", "entity.project", "Project"),
    ("relation", "entity.relation", "Related to"),
    ("preference", "entity.preference", "Preference"),
    ("location", "entity.location", "Location"),
    ("name", "entity.name", "Name"),
)


_ENTITY_STATE_SUMMARY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"^(?:what|which)\s+do you\s+(?:know|remember|have saved)\s+about\s+(.+?)[\?\.\!]*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?:what(?:'s| is)\s+(?:the\s+)?state\s+of\s+)(.+?)[\?\.\!]*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?:summarize|summary\s+of)\s+(.+?)[\?\.\!]*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^what\s+is\s+open\s+on\s+(.+?)[\?\.\!]*$",
        re.IGNORECASE,
    ),
)


_OPEN_MEMORY_RECALL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "name_recall",
        re.compile(
            r"^(?:what|which)\s+did\s+i\s+name\s+(.+?)[\?\.\!]*$",
            re.IGNORECASE,
        ),
    ),
    (
        "name_recall",
        re.compile(
            r"^(?:what(?:'s| is)\s+)?(?:the\s+)?name\s+of\s+(.+?)[\?\.\!]*$",
            re.IGNORECASE,
        ),
    ),
    (
        "name_recall",
        re.compile(
            r"^(?!(?:where|what|who|when|which|why|how)\b)((?:the\s+)?[a-z0-9][a-z0-9\s_-]*\bplant)\s*[\?\.\!]*$",
            re.IGNORECASE,
        ),
    ),
    (
        "location_recall",
        re.compile(
            r"^where\s+is\s+(.+?)[\?\.\!]*$",
            re.IGNORECASE,
        ),
    ),
    (
        "owner_recall",
        re.compile(
            r"^who\s+(?:owns|handles)\s+(.+?)[\?\.\!]*$",
            re.IGNORECASE,
        ),
    ),
    (
        "owner_recall",
        re.compile(
            r"^who\s+is\s+the\s+owner\s+of\s+(.+?)[\?\.\!]*$",
            re.IGNORECASE,
        ),
    ),
    (
        "status_recall",
        re.compile(
            r"^(?:what(?:'s| is)\s+)?(?:the\s+)?status\s+of\s+(.+?)[\?\.\!]*$",
            re.IGNORECASE,
        ),
    ),
    (
        "deadline_recall",
        re.compile(
            r"^(?:when\s+is\s+|what(?:'s| is)\s+the\s+deadline\s+for\s+)(.+?)(?:\s+due)?[\?\.\!]*$",
            re.IGNORECASE,
        ),
    ),
    (
        "relation_recall",
        re.compile(
            r"^what\s+is\s+(.+?)\s+related\s+to[\?\.\!]*$",
            re.IGNORECASE,
        ),
    ),
    (
        "preference_recall",
        re.compile(
            r"^(?:what\s+does\s+(.+?)\s+prefer|what(?:'s| is)\s+(?:the\s+)?preference\s+of\s+(.+?))[\?\.\!]*$",
            re.IGNORECASE,
        ),
    ),
    (
        "project_recall",
        re.compile(
            r"^(?:what\s+project\s+is\s+(.+?)\s+for|what(?:'s| is)\s+(?:the\s+)?(?:active\s+)?project\s+for\s+(.+?))[\?\.\!]*$",
            re.IGNORECASE,
        ),
    ),
    (
        "blocker_recall",
        re.compile(
            r"^what(?:'s| is)\s+(?:the\s+)?blocker\s+for\s+(.+?)[\?\.\!]*$",
            re.IGNORECASE,
        ),
    ),
    (
        "blocker_recall",
        re.compile(
            r"^what(?:'s| is)\s+blocking\s+(.+?)[\?\.\!]*$",
            re.IGNORECASE,
        ),
    ),
    (
        "priority_recall",
        re.compile(
            r"^what(?:'s| is)\s+(?:the\s+)?priority\s+for\s+(.+?)[\?\.\!]*$",
            re.IGNORECASE,
        ),
    ),
    (
        "priority_recall",
        re.compile(
            r"^what\s+should\s+(.+?)\s+prioritize[\?\.\!]*$",
            re.IGNORECASE,
        ),
    ),
    (
        "decision_recall",
        re.compile(
            r"^(?:what(?:'s| is)\s+(?:the\s+)?decision\s+for\s+|what\s+did\s+we\s+decide\s+for\s+)(.+?)[\?\.\!]*$",
            re.IGNORECASE,
        ),
    ),
    (
        "decision_recall",
        re.compile(
            r"^what(?:'s| is)\s+(?:the\s+)?(.+?)\s+(?:onboarding\s+direction|direction)[\?\.\!]*$",
            re.IGNORECASE,
        ),
    ),
    (
        "decision_recall",
        re.compile(
            r"^what\s+((?:onboarding\s+)?direction)\s+(?:were\s+we|are\s+we|were\s+i|am\s+i)\s+(?:leaning\s+towards?|going\s+with)[\?\.\!]*$",
            re.IGNORECASE,
        ),
    ),
    (
        "next_action_recall",
        re.compile(
            r"^(?:what(?:'s| is)\s+(?:the\s+)?(?:next\s+action|next\s+step|todo)\s+for\s+)(.+?)[\?\.\!]*$",
            re.IGNORECASE,
        ),
    ),
    (
        "metric_recall",
        re.compile(
            r"^(?:what(?:'s| is)\s+(?:the\s+)?(?:metric|kpi)\s+for\s+)(.+?)[\?\.\!]*$",
            re.IGNORECASE,
        ),
    ),
    (
        "evidence_recall",
        re.compile(
            r"^(?:what|which)\s+(?:evidence|saved memory|memory)\s+do you have about\s+(.+?)[\?\.\!]*$",
            re.IGNORECASE,
        ),
    ),
    (
        "evidence_recall",
        re.compile(
            r"^(?:what|which)\s+do you\s+(?:remember|know|have saved)\s+about\s+(.+?)[\?\.\!]*$",
            re.IGNORECASE,
        ),
    ),
    (
        "evidence_recall",
        re.compile(
            r"^(?:what|which)\s+did\s+i\s+(?:tell|say|mention|share)\s+(?:to\s+)?you\s+about\s+(.+?)[\?\.\!]*$",
            re.IGNORECASE,
        ),
    ),
    (
        "evidence_recall",
        re.compile(
            r"^(?:do\s+you\s+)?(?:remember|recall)\s+what\s+i\s+(?:told|said|mentioned|shared)\s+(?:to\s+)?you\s+about\s+(.+?)[\?\.\!]*$",
            re.IGNORECASE,
        ),
    ),
    (
        "evidence_recall",
        re.compile(
            r"^(?:can\s+you\s+)?(?:remember|recall)\s+(?:anything\s+)?about\s+(.+?)[\?\.\!]*$",
            re.IGNORECASE,
        ),
    ),
    (
        "episodic_recall",
        re.compile(
            r"^(?:what|tell me what)\s+happened\s+(?:during|in|around|at)\s+(.+?)[\?\.\!]*$",
            re.IGNORECASE,
        ),
    ),
)


_ENTITY_STATE_HISTORY_PATTERNS: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    (
        "location",
        "entity.location",
        re.compile(
            r"^where\s+was\s+(.+?)\s+before[\?\.\!]*$",
            re.IGNORECASE,
        ),
    ),
    (
        "location",
        "entity.location",
        re.compile(
            r"^what\s+was\s+(?:the\s+)?(?:previous\s+)?location\s+of\s+(.+?)[\?\.\!]*$",
            re.IGNORECASE,
        ),
    ),
    (
        "owner",
        "entity.owner",
        re.compile(
            r"^who\s+(?:owned|handled)\s+(.+?)\s+before[\?\.\!]*$",
            re.IGNORECASE,
        ),
    ),
    (
        "owner",
        "entity.owner",
        re.compile(
            r"^who\s+was\s+(?:the\s+)?(?:previous\s+)?owner\s+of\s+(.+?)[\?\.\!]*$",
            re.IGNORECASE,
        ),
    ),
    (
        "status",
        "entity.status",
        re.compile(
            r"^what\s+was\s+(?:the\s+)?(?:previous\s+)?status\s+of\s+(.+?)\s+before[\?\.\!]*$",
            re.IGNORECASE,
        ),
    ),
    (
        "deadline",
        "entity.deadline",
        re.compile(
            r"^(?:when\s+was\s+(.+?)\s+due\s+before|what\s+was\s+(?:the\s+)?(?:previous\s+)?deadline\s+for\s+(.+?))[\?\.\!]*$",
            re.IGNORECASE,
        ),
    ),
    (
        "relation",
        "entity.relation",
        re.compile(
            r"^what\s+was\s+(.+?)\s+related\s+to\s+before[\?\.\!]*$",
            re.IGNORECASE,
        ),
    ),
    (
        "preference",
        "entity.preference",
        re.compile(
            r"^(?:what\s+did\s+(.+?)\s+prefer\s+before|what\s+was\s+(?:the\s+)?(?:previous\s+)?preference\s+of\s+(.+?))[\?\.\!]*$",
            re.IGNORECASE,
        ),
    ),
    (
        "project",
        "entity.project",
        re.compile(
            r"^what\s+was\s+(?:the\s+)?(?:previous\s+)?project\s+for\s+(.+?)[\?\.\!]*$",
            re.IGNORECASE,
        ),
    ),
    (
        "decision",
        "entity.decision",
        re.compile(
            r"^what\s+was\s+(?:the\s+)?(?:previous\s+)?(.+?)\s+(?:onboarding\s+direction|direction)\s+before[\?\.\!]*$",
            re.IGNORECASE,
        ),
    ),
    (
        "blocker",
        "entity.blocker",
        re.compile(
            r"^(?:what\s+was\s+(?:blocking\s+)?(.+?)\s+before|what\s+was\s+(?:the\s+)?(?:previous\s+)?blocker\s+for\s+(.+?))[\?\.\!]*$",
            re.IGNORECASE,
        ),
    ),
    (
        "priority",
        "entity.priority",
        re.compile(
            r"^what\s+was\s+(?:the\s+)?(?:previous\s+)?priority\s+for\s+(.+?)[\?\.\!]*$",
            re.IGNORECASE,
        ),
    ),
    (
        "decision",
        "entity.decision",
        re.compile(
            r"^what\s+was\s+(?:the\s+)?(?:previous\s+)?decision\s+for\s+(.+?)[\?\.\!]*$",
            re.IGNORECASE,
        ),
    ),
    (
        "next_action",
        "entity.next_action",
        re.compile(
            r"^what\s+was\s+(?:the\s+)?(?:previous\s+)?(?:next\s+action|next\s+step|todo)\s+for\s+(.+?)[\?\.\!]*$",
            re.IGNORECASE,
        ),
    ),
    (
        "metric",
        "entity.metric",
        re.compile(
            r"^what\s+was\s+(?:the\s+)?(?:previous\s+)?(?:metric|kpi)\s+for\s+(.+?)[\?\.\!]*$",
            re.IGNORECASE,
        ),
    ),
)


_BELIEF_RECALL_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"^(?:what(?:'s| is)|tell me)\s+your\s+(?:current\s+)?belief\s+about\s+(.+?)[\?\.\!]*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?:what|which)\s+belief\s+do you have about\s+(.+?)[\?\.\!]*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?:what|which)\s+do you\s+currently\s+believe\s+about\s+(.+?)[\?\.\!]*$",
        re.IGNORECASE,
    ),
)


_SPARK_SYSTEMS_SELF_KNOWLEDGE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"^(?:what|which)\s+do you\s+(?:know|remember|understand)\s+about\s+spark(?:\s+(?:systems?|ecosystem|modules?|stack))?\s*[\?\.\!]*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?:what|which)\s+(?:can|does)\s+spark\s+(?:do|help with|run|install)\s*[\?\.\!]*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?:what|which)\s+can\s+you\s+do\s+with\s+spark\s*[\?\.\!]*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?:what|tell me what)\s+(?:is|are)\s+spark\s+(?:systems?|ecosystem|modules?|stack)\s*[\?\.\!]*$",
        re.IGNORECASE,
    ),
    re.compile(
        r"^(?:how\s+can\s+i\s+use|how\s+do\s+i\s+use)\s+spark\s+(?:systems?|ecosystem|modules?|stack)?\s*[\?\.\!]*$",
        re.IGNORECASE,
    ),
)


def _detect_spark_systems_self_knowledge_query(user_message: str) -> bool:
    normalized = " ".join(str(user_message or "").strip().split())
    if not normalized:
        return False
    return any(pattern.match(normalized) for pattern in _SPARK_SYSTEMS_SELF_KNOWLEDGE_PATTERNS)


def _detect_episodic_daily_recall_query(user_message: str) -> EpisodicDailyRecallQuery | None:
    normalized = " ".join(str(user_message or "").strip().split())
    if not normalized:
        return None
    lowered = normalized.casefold().strip(" ?!.")
    today = datetime.now(timezone.utc).date().isoformat()
    patterns: tuple[tuple[str, re.Pattern[str]], ...] = (
        (
            "build_recall",
            re.compile(
                r"^(?:what\s+did\s+we\s+build|what\s+have\s+we\s+built|what\s+did\s+we\s+work\s+on)\s+today$",
                re.IGNORECASE,
            ),
        ),
        (
            "change_recall",
            re.compile(r"^(?:what\s+changed|what\s+has\s+changed|what\s+was\s+improved)\s+today$", re.IGNORECASE),
        ),
        (
            "open_recall",
            re.compile(
                r"^(?:what(?:'s| is)\s+still\s+open|what\s+did\s+we\s+leave\s+open|what\s+is\s+left)\s+today$",
                re.IGNORECASE,
            ),
        ),
        (
            "promise_recall",
            re.compile(r"^(?:what\s+did\s+(?:you|we)\s+promise|what\s+promises\s+were\s+made)\s+today$", re.IGNORECASE),
        ),
        (
            "memory_recall",
            re.compile(
                r"^(?:what\s+else\s+do\s+you\s+remember(?:\s+from)?|what\s+happened)\s+today$",
                re.IGNORECASE,
            ),
        ),
    )
    for query_kind, pattern in patterns:
        if pattern.match(lowered):
            return EpisodicDailyRecallQuery(day=today, query_kind=query_kind)
    return None


def _detect_episodic_project_recall_query(user_message: str) -> EpisodicProjectRecallQuery | None:
    normalized = " ".join(str(user_message or "").strip().split())
    if not normalized:
        return None
    lowered = normalized.casefold().strip(" ?!.")
    patterns: tuple[tuple[str, re.Pattern[str]], ...] = (
        (
            "build_recall",
            re.compile(
                r"^(?:what\s+did\s+we\s+(?:build|work\s+on)|what\s+have\s+we\s+built)\s+(?:for|in|on|with)\s+(.+?)(?:\s+today)?$",
                re.IGNORECASE,
            ),
        ),
        (
            "change_recall",
            re.compile(
                r"^(?:what\s+changed|what\s+has\s+changed|what\s+was\s+improved)\s+(?:for|in|on|with)\s+(.+?)(?:\s+today)?$",
                re.IGNORECASE,
            ),
        ),
        (
            "open_recall",
            re.compile(
                r"^(?:what(?:'s| is)\s+still\s+open|what\s+did\s+we\s+leave\s+open|what\s+is\s+left)\s+(?:for|in|on|with)\s+(.+?)(?:\s+today)?$",
                re.IGNORECASE,
            ),
        ),
        (
            "memory_recall",
            re.compile(
                r"^(?:what\s+else\s+do\s+you\s+remember|what\s+happened)\s+(?:about|for|in|on|with)\s+(.+?)(?:\s+today)?$",
                re.IGNORECASE,
            ),
        ),
    )
    for query_kind, pattern in patterns:
        match = pattern.match(lowered)
        if not match:
            continue
        project_key = _clean_entity_history_topic(str(match.group(1) or ""))
        if project_key and project_key not in {"me", "my profile", "today"}:
            return EpisodicProjectRecallQuery(project_key=project_key, query_kind=query_kind)
    return None


def _detect_episodic_session_recall_query(user_message: str, *, session_id: str | None) -> EpisodicSessionRecallQuery | None:
    normalized = " ".join(str(user_message or "").strip().split())
    normalized_session_id = str(session_id or "").strip()
    if not normalized or not normalized_session_id:
        return None
    lowered = normalized.casefold().strip(" ?!.")
    patterns: tuple[tuple[str, re.Pattern[str]], ...] = (
        (
            "build_recall",
            re.compile(
                r"^(?:what\s+did\s+we\s+(?:build|work\s+on|do)|what\s+have\s+we\s+built)\s+(?:in|during)\s+this\s+(?:chat|conversation|session)$",
                re.IGNORECASE,
            ),
        ),
        (
            "change_recall",
            re.compile(
                r"^(?:what\s+changed|what\s+has\s+changed|what\s+was\s+improved)\s+(?:in|during)\s+this\s+(?:chat|conversation|session)$",
                re.IGNORECASE,
            ),
        ),
        (
            "open_recall",
            re.compile(
                r"^(?:what(?:'s| is)\s+still\s+open|what\s+did\s+we\s+leave\s+open|what\s+is\s+left)\s+(?:in|during)\s+this\s+(?:chat|conversation|session)$",
                re.IGNORECASE,
            ),
        ),
        (
            "memory_recall",
            re.compile(
                r"^(?:what\s+else\s+do\s+you\s+remember|what\s+happened|summarize\s+what\s+happened)\s+(?:in|during)\s+this\s+(?:chat|conversation|session)$",
                re.IGNORECASE,
            ),
        ),
    )
    for query_kind, pattern in patterns:
        if pattern.match(lowered):
            return EpisodicSessionRecallQuery(session_id=normalized_session_id, query_kind=query_kind)
    return None


def _episodic_recall_sections(
    *,
    query_kind: str,
    what_changed: tuple[str, ...],
    decisions: tuple[str, ...],
    open_questions: tuple[str, ...],
    repos_touched: tuple[str, ...],
    artifacts_created: tuple[str, ...],
    promises_made: tuple[str, ...],
    next_actions: tuple[str, ...],
) -> list[tuple[str, tuple[str, ...]]]:
    if query_kind == "build_recall":
        return [
            ("What changed", what_changed),
            ("Repos touched", repos_touched),
            ("Artifacts created", artifacts_created),
            ("Next actions", next_actions),
        ]
    if query_kind == "change_recall":
        return [
            ("What changed", what_changed),
            ("Decisions", decisions),
            ("Artifacts created", artifacts_created),
        ]
    if query_kind == "open_recall":
        return [
            ("Still open", open_questions),
            ("Next actions", next_actions),
        ]
    if query_kind == "promise_recall":
        return [
            ("Promises made", promises_made),
            ("Next actions", next_actions),
        ]
    return [
        ("What changed", what_changed),
        ("Decisions", decisions),
        ("Still open", open_questions),
        ("Next actions", next_actions),
    ]


def _build_episodic_session_recall_reply(
    *,
    state_db: StateDB,
    human_id: str,
    query: EpisodicSessionRecallQuery,
) -> tuple[str, dict[str, Any]]:
    summary = build_session_summary(state_db=state_db, session_id=query.session_id, limit=500)
    facts: dict[str, Any] = {
        "session_id": query.session_id,
        "query_kind": query.query_kind,
        "scope": "session",
        "summary_source": "session_event_ledger_rollup",
        "event_count": summary.event_count,
        "session_count": 1 if summary.event_count > 0 else 0,
        "source_session_ids": [query.session_id] if summary.event_count > 0 else [],
        "source_event_ids": list(summary.source_event_ids[:20]),
    }
    if summary.event_count <= 0:
        return (
            "I do not have a saved memory trace for this conversation yet.",
            {
                **facts,
                "answered": False,
                "section_count": 0,
            },
        )

    sections = _episodic_recall_sections(
        query_kind=query.query_kind,
        what_changed=summary.what_changed,
        decisions=summary.decisions,
        open_questions=summary.open_questions,
        repos_touched=summary.repos_touched,
        artifacts_created=summary.artifacts_created,
        promises_made=summary.promises_made,
        next_actions=summary.next_actions,
    )
    rendered_sections: list[tuple[str, tuple[str, ...]]] = [
        (title, tuple(value for value in values if str(value).strip())[:6])
        for title, values in sections
        if any(str(value).strip() for value in values)
    ]
    if not rendered_sections:
        return (
            f"I found {summary.event_count} event(s) in this conversation, but no durable episodic summary details yet.",
            {
                **facts,
                "answered": False,
                "section_count": 0,
            },
        )

    lines = ["From this conversation's episodic memory:"]
    for title, values in rendered_sections:
        lines.append("")
        lines.append(title)
        lines.extend(f"- {value}" for value in values)
    lines.extend(
        [
            "",
            f"Source: session event ledger rollup for {query.session_id}.",
        ]
    )
    return (
        "\n".join(lines),
        {
            **facts,
            "answered": True,
            "section_count": len(rendered_sections),
            "sections": [title for title, _ in rendered_sections],
        },
    )


def _build_episodic_daily_recall_reply(
    *,
    state_db: StateDB,
    human_id: str,
    query: EpisodicDailyRecallQuery,
) -> tuple[str, dict[str, Any]]:
    summary = build_daily_summary(state_db=state_db, day=query.day, human_id=human_id, limit=500)
    facts: dict[str, Any] = {
        "day": query.day,
        "query_kind": query.query_kind,
        "scope": "daily",
        "summary_source": "daily_event_ledger_rollup",
        "event_count": summary.event_count,
        "session_count": summary.session_count,
        "source_session_ids": list(summary.source_session_ids[:10]),
        "source_event_ids": list(summary.source_event_ids[:20]),
    }
    if summary.event_count <= 0:
        return (
            "I do not have a saved daily memory trace for today yet.",
            {
                **facts,
                "answered": False,
                "section_count": 0,
            },
        )

    sections = _episodic_recall_sections(
        query_kind=query.query_kind,
        what_changed=summary.what_changed,
        decisions=summary.decisions,
        open_questions=summary.open_questions,
        repos_touched=summary.repos_touched,
        artifacts_created=summary.artifacts_created,
        promises_made=summary.promises_made,
        next_actions=summary.next_actions,
    )

    rendered_sections: list[tuple[str, tuple[str, ...]]] = [
        (title, tuple(value for value in values if str(value).strip())[:6])
        for title, values in sections
        if any(str(value).strip() for value in values)
    ]
    if not rendered_sections:
        return (
            f"I found {summary.event_count} event(s) in today's memory ledger, but no durable episodic summary details yet.",
            {
                **facts,
                "answered": False,
                "section_count": 0,
            },
        )

    lines = ["From today's episodic memory:"]
    for title, values in rendered_sections:
        lines.append("")
        lines.append(title)
        lines.extend(f"- {value}" for value in values)
    lines.extend(
        [
            "",
            f"Source: daily event ledger rollup for {query.day}.",
        ]
    )
    return (
        "\n".join(lines),
        {
            **facts,
            "answered": True,
            "section_count": len(rendered_sections),
            "sections": [title for title, _ in rendered_sections],
        },
    )


def _build_episodic_project_recall_reply(
    *,
    state_db: StateDB,
    human_id: str,
    query: EpisodicProjectRecallQuery,
) -> tuple[str, dict[str, Any]]:
    summary = build_project_summary(state_db=state_db, project_key=query.project_key, human_id=human_id, limit=500)
    facts: dict[str, Any] = {
        "project_key": query.project_key,
        "query_kind": query.query_kind,
        "scope": "project",
        "summary_source": "project_event_ledger_rollup",
        "event_count": summary.event_count,
        "session_count": summary.session_count,
        "source_session_ids": list(summary.source_session_ids[:10]),
        "source_event_ids": list(summary.source_event_ids[:20]),
    }
    if summary.event_count <= 0:
        return (
            f"I do not have a saved project memory trace for {query.project_key} yet.",
            {
                **facts,
                "answered": False,
                "section_count": 0,
            },
        )

    sections = _episodic_recall_sections(
        query_kind=query.query_kind,
        what_changed=summary.what_changed,
        decisions=summary.decisions,
        open_questions=summary.open_questions,
        repos_touched=summary.repos_touched,
        artifacts_created=summary.artifacts_created,
        promises_made=summary.promises_made,
        next_actions=summary.next_actions,
    )
    rendered_sections: list[tuple[str, tuple[str, ...]]] = [
        (title, tuple(value for value in values if str(value).strip())[:6])
        for title, values in sections
        if any(str(value).strip() for value in values)
    ]
    if not rendered_sections:
        return (
            f"I found {summary.event_count} event(s) for {query.project_key}, but no durable episodic summary details yet.",
            {
                **facts,
                "answered": False,
                "section_count": 0,
            },
        )

    lines = [f"From the {query.project_key} project memory:"]
    for title, values in rendered_sections:
        lines.append("")
        lines.append(title)
        lines.extend(f"- {value}" for value in values)
    lines.extend(
        [
            "",
            f"Source: project event ledger rollup for {query.project_key}.",
        ]
    )
    return (
        "\n".join(lines),
        {
            **facts,
            "answered": True,
            "section_count": len(rendered_sections),
            "sections": [title for title, _ in rendered_sections],
        },
    )


def _build_spark_systems_self_knowledge_answer() -> str:
    return (
        "Spark is a local agent ecosystem: spark-cli installs and starts the stack; "
        "spark-intelligence-builder handles memory, advisories, and agent wiring; "
        "domain-chip-memory is the default memory substrate; spark-researcher adds research and advisory runtime; "
        "spawner-ui provides Kanban and Mission Control for running work; and spark-telegram-bot is the long-polling Telegram gateway. "
        "I can help check status, configure an LLM provider, connect Builder memory, set up Telegram, start Mission Control, or launch a mission."
    )


def _detect_open_memory_recall_query(user_message: str) -> OpenMemoryRecallQuery | None:
    normalized = " ".join(str(user_message or "").strip().split())
    if not normalized:
        return None
    for query_kind, pattern in _OPEN_MEMORY_RECALL_PATTERNS:
        match = pattern.match(normalized)
        if not match:
            continue
        topic = str(next((group for group in match.groups() if group), "")).strip(" \t\r\n?!.\"'")
        if not topic or topic in {"me", "my profile"}:
            return None
        return OpenMemoryRecallQuery(topic=topic, query_kind=query_kind)
    return None


def _detect_entity_state_summary_query(user_message: str) -> EntityStateSummaryQuery | None:
    normalized = " ".join(str(user_message or "").strip().split())
    if not normalized:
        return None
    for pattern in _ENTITY_STATE_SUMMARY_PATTERNS:
        match = pattern.match(normalized)
        if not match:
            continue
        topic = _clean_entity_history_topic(str(match.group(1) or ""))
        if not topic or topic in {"me", "my profile"}:
            return None
        return EntityStateSummaryQuery(topic=topic)
    return None


def _clean_entity_history_topic(topic: str) -> str:
    cleaned = str(topic or "").strip(" \t\r\n?!.\"'")
    cleaned = re.sub(r"^(?:my|the)\s+", "", cleaned, flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned


def _detect_entity_state_history_query(user_message: str) -> EntityStateHistoryQuery | None:
    normalized = " ".join(str(user_message or "").strip().split())
    if not normalized:
        return None
    for attribute, predicate, pattern in _ENTITY_STATE_HISTORY_PATTERNS:
        match = pattern.match(normalized)
        if not match:
            continue
        topic = next((group for group in match.groups() if group), "")
        cleaned_topic = _clean_entity_history_topic(topic)
        if not cleaned_topic or cleaned_topic in {"me", "my profile"} or cleaned_topic.casefold() in _ENTITY_FOLLOWUP_PRONOUN_TOPICS:
            return None
        return EntityStateHistoryQuery(
            topic=cleaned_topic,
            attribute=attribute,
            predicate=predicate,
        )
    return None


def _detect_entity_state_followup_history_query(
    *,
    user_message: str,
    state_db: StateDB,
    channel_kind: str,
    session_id: str,
    human_id: str,
    agent_id: str,
    request_id: str,
) -> tuple[EntityStateHistoryQuery, dict[str, Any]] | None:
    normalized = " ".join(str(user_message or "").strip().split())
    if not normalized:
        return None
    if not re.match(
        r"^(?:what\s+was\s+(?:it|that|this|the\s+previous\s+value)\s+(?:before|previously)|what\s+was\s+it)[\?\.\!]*$",
        normalized,
        flags=re.IGNORECASE,
    ):
        return None
    try:
        with state_db.connect() as conn:
            rows = conn.execute(
                """
                SELECT request_id, facts_json, created_at
                FROM builder_events
                WHERE component = 'researcher_bridge'
                  AND event_type = 'tool_result_received'
                  AND channel_id = ?
                  AND session_id = ?
                  AND human_id = ?
                  AND agent_id = ?
                ORDER BY created_at DESC, event_id DESC
                LIMIT 20
                """,
                (channel_kind, session_id, human_id, agent_id),
            ).fetchall()
    except Exception:
        return None
    for row in rows:
        previous_request_id = str(row["request_id"] or "").strip()
        if previous_request_id and previous_request_id == request_id:
            continue
        try:
            facts = json.loads(row["facts_json"] or "{}")
        except Exception:
            continue
        if not isinstance(facts, dict):
            continue
        routing_decision = str(facts.get("routing_decision") or "").strip()
        bridge_mode = str(facts.get("bridge_mode") or "").strip()
        if routing_decision == "memory_entity_state_history_query" or bridge_mode == "memory_entity_state_history":
            topic = str(facts.get("topic") or "").strip()
            attribute = str(facts.get("attribute") or "").strip()
        elif routing_decision == "memory_open_recall_query" or bridge_mode == "memory_open_recall":
            topic = str(facts.get("topic") or "").strip()
            attribute = _open_memory_recall_entity_attribute(str(facts.get("query_kind") or "").strip()) or ""
        else:
            continue
        topic = _clean_entity_history_topic(topic)
        if not topic or topic.casefold() in _ENTITY_FOLLOWUP_PRONOUN_TOPICS:
            continue
        if not attribute:
            continue
        return (
            EntityStateHistoryQuery(
                topic=topic,
                attribute=attribute,
                predicate=f"entity.{attribute}",
            ),
            {
                "followup_resolved_from_request_id": previous_request_id or None,
                "followup_resolved_from_route": routing_decision or bridge_mode or None,
            },
        )
    return None


def _detect_belief_recall_query(user_message: str) -> BeliefRecallQuery | None:
    normalized = " ".join(str(user_message or "").strip().split())
    if not normalized:
        return None
    for pattern in _BELIEF_RECALL_PATTERNS:
        match = pattern.match(normalized)
        if not match:
            continue
        topic = str(match.group(1) or "").strip(" \t\r\n?!.\"'")
        if not topic or topic in {"me", "my profile"}:
            return None
        return BeliefRecallQuery(topic=topic)
    return None


def _memory_record_text(record: dict[str, Any]) -> str:
    metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    for key in ("evidence_text", "source_text", "value"):
        candidate = str(metadata.get(key) or "").strip()
        if candidate:
            return candidate
    text = str(record.get("text") or "").strip()
    if text:
        subject = str(record.get("subject") or "").strip()
        predicate = str(record.get("predicate") or "").strip()
        value = str(record.get("value") or metadata.get("normalized_value") or "").strip()
        if subject and predicate and text.startswith(f"{subject} {predicate} ") and value:
            return value
        return text
    return str(record.get("value") or metadata.get("normalized_value") or "").strip()


def _filter_open_memory_recall_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    superseded_ids: set[str] = set()
    deleted_scopes: list[tuple[str, str, str, str]] = []
    for record in records:
        lifecycle = record.get("lifecycle") if isinstance(record.get("lifecycle"), dict) else {}
        metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
        supersedes = str(lifecycle.get("supersedes") or metadata.get("supersedes") or "").strip()
        if supersedes:
            superseded_ids.add(supersedes)
        role = str(record.get("memory_role") or metadata.get("memory_role") or "").strip()
        operation = str(record.get("operation") or metadata.get("write_operation") or "").strip().lower()
        target_predicate = str(metadata.get("target_predicate") or "").strip()
        if role == "state_deletion" or operation == "delete" or target_predicate:
            deleted_scopes.append(
                (
                    str(record.get("subject") or "").strip(),
                    target_predicate or str(record.get("predicate") or "").strip(),
                    str(metadata.get("entity_key") or "").strip(),
                    _memory_record_lifecycle_time(record),
                )
            )
    filtered: list[dict[str, Any]] = []
    for record in records:
        predicate = str(record.get("predicate") or "").strip()
        record_id = str(record.get("observation_id") or (record.get("metadata") or {}).get("observation_id") or "").strip()
        metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
        role = str(record.get("memory_role") or metadata.get("memory_role") or "").strip()
        if _record_is_suppressed_by_state_deletion(record=record, deleted_scopes=deleted_scopes):
            continue
        if role == "state_deletion" or str(record.get("operation") or "").strip().lower() == "delete":
            continue
        if record_id and record_id in superseded_ids:
            continue
        if metadata.get("raw_episode_lifecycle_action") == "archived":
            continue
        if role in {"structured_evidence", "episodic"}:
            filtered.append(record)
            continue
        if role == "current_state" and predicate.startswith("entity."):
            filtered.append(record)
            continue
        if predicate == "profile.current_low_stakes_test_fact":
            filtered.append(record)
            continue
        if predicate == "raw_turn" or predicate.startswith("evidence.telegram."):
            filtered.append(record)
    return filtered


def _memory_record_lifecycle_time(record: dict[str, Any]) -> str:
    lifecycle = record.get("lifecycle") if isinstance(record.get("lifecycle"), dict) else {}
    metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    return str(
        lifecycle.get("deleted_at")
        or lifecycle.get("created_at")
        or lifecycle.get("document_time")
        or metadata.get("deleted_at")
        or metadata.get("created_at")
        or metadata.get("document_time")
        or ""
    ).strip()


def _record_is_suppressed_by_state_deletion(
    *,
    record: dict[str, Any],
    deleted_scopes: list[tuple[str, str, str, str]],
) -> bool:
    if not deleted_scopes:
        return False
    metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    subject = str(record.get("subject") or "").strip()
    predicate = str(record.get("predicate") or "").strip()
    entity_key = str(metadata.get("entity_key") or "").strip()
    record_time = _memory_record_lifecycle_time(record)
    for deleted_subject, deleted_predicate, deleted_entity_key, deleted_time in deleted_scopes:
        if subject != deleted_subject or predicate != deleted_predicate:
            continue
        if deleted_entity_key and entity_key != deleted_entity_key:
            continue
        if deleted_time and record_time and record_time > deleted_time:
            continue
        return True
    return False


def _record_matches_open_memory_topic(*, record: dict[str, Any], topic: str) -> bool:
    normalized_topic = str(topic or "").strip().casefold()
    if not normalized_topic:
        return False
    predicate = str(record.get("predicate") or "").strip()
    metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    if predicate.startswith("entity."):
        entity_label = str(metadata.get("entity_label") or "").strip()
        if entity_label and _entity_label_matches_open_memory_topic(entity_label=entity_label, topic=topic):
            return True
        attribute = str(metadata.get("entity_attribute") or predicate.split(".", 1)[-1]).strip()
        return _entity_value_matches_open_memory_topic(
            attribute=attribute,
            value=_memory_record_text(record),
            topic=topic,
        )
    haystack = _memory_record_text(record).casefold()
    if not haystack:
        return False
    if normalized_topic in haystack:
        return True
    topic_tokens = [
        token
        for token in re.findall(r"[a-z0-9][a-z0-9_-]*", normalized_topic)
        if token
        and token
        not in {
            "a",
            "an",
            "and",
            "about",
            "for",
            "from",
            "in",
            "it",
            "me",
            "my",
            "of",
            "on",
            "our",
            "that",
            "the",
            "this",
            "to",
            "with",
            "you",
        }
    ]
    if not topic_tokens:
        return False
    matched_tokens = [token for token in topic_tokens if token in haystack]
    required_matches = 1 if len(topic_tokens) <= 2 else 2
    return len(matched_tokens) >= required_matches


def _named_object_answer_from_snippet(*, topic: str, snippet: str) -> str | None:
    topic_tokens = [
        token
        for token in re.findall(r"[a-z0-9][a-z0-9_-]*", str(topic or "").casefold())
        if token
        and token
        not in {
            "a",
            "an",
            "and",
            "did",
            "i",
            "my",
            "name",
            "of",
            "the",
        }
    ]
    for match in re.finditer(
        r"\b(?:my|the)\s+(.+?)\s+is\s+named\s+([A-Z][A-Za-z0-9_-]*)\b",
        str(snippet or ""),
    ):
        object_label = " ".join(str(match.group(1) or "").strip().split())
        name = str(match.group(2) or "").strip()
        if not object_label or not name:
            continue
        normalized_object = object_label.casefold()
        if topic_tokens and not any(token in normalized_object for token in topic_tokens):
            continue
        return f"You named the {object_label} {name}."
    return None


def _entity_state_answer_from_record(*, query: OpenMemoryRecallQuery, record: dict[str, Any]) -> str | None:
    predicate = str(record.get("predicate") or "").strip()
    if not predicate.startswith("entity."):
        return None
    metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    label = " ".join(str(metadata.get("entity_label") or "").strip().split())
    value = str(record.get("value") or metadata.get("value") or "").strip()
    if not label or not value:
        return None
    expected_attribute = _open_memory_recall_entity_attribute(query.query_kind)
    if not _entity_label_matches_open_memory_topic(entity_label=label, topic=query.topic):
        if not _entity_value_matches_open_memory_topic(
            attribute=expected_attribute,
            value=f"{value} {_memory_record_text(record)}",
            topic=query.topic,
        ):
            return None
    attribute = str(metadata.get("entity_attribute") or predicate.split(".", 1)[-1]).strip()
    if expected_attribute and attribute != expected_attribute:
        return None
    if attribute == "name":
        return f"You named the {label} {value}."
    if attribute == "location":
        preposition = str(metadata.get("location_preposition") or "at").strip()
        return f"The {label} is {preposition} {value}."
    if attribute == "owner":
        return f"The {label} is owned by {value}."
    if attribute == "deadline":
        return f"The {label} is due {value}."
    if attribute == "status":
        return f"The {label} status is {value}."
    if attribute == "relation":
        return f"The {label} is related to {value}."
    if attribute == "preference":
        return f"The {label} preference is {value}."
    if attribute == "project":
        return f"The {label} project is {value}."
    if attribute == "blocker":
        return f"The {label} blocker is {value}."
    if attribute == "priority":
        return f"The {label} priority is {value}."
    if attribute == "decision":
        return f"The {label} decision is {value}."
    if attribute == "next_action":
        return f"The {label} next action is {value}."
    if attribute == "metric":
        return f"The {label} metric is {value}."
    return f"The {label} {attribute} is {value}."


def _open_memory_recall_decisive_records(
    *,
    query: OpenMemoryRecallQuery,
    records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    expected_attribute = _open_memory_recall_entity_attribute(query.query_kind)
    if not expected_attribute:
        return records
    for record in records:
        if _entity_state_answer_from_record(query=query, record=record):
            return [record]
    return records


def _open_memory_recall_record_role(record: dict[str, Any]) -> str:
    metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    predicate = str(record.get("predicate") or "").strip()
    if predicate.startswith("entity.") or metadata.get("entity_attribute"):
        return "entity_state"
    return str(record.get("memory_role") or metadata.get("memory_role") or "").strip()


def _open_memory_recall_enriched_entity_record(
    *,
    query: OpenMemoryRecallQuery,
    record: dict[str, Any],
) -> dict[str, Any]:
    attribute = _open_memory_recall_entity_attribute(query.query_kind)
    if not attribute or str(record.get("predicate") or "").strip() != f"entity.{attribute}":
        return record
    enriched = dict(record)
    metadata = dict(record.get("metadata") if isinstance(record.get("metadata"), dict) else {})
    metadata.setdefault("entity_attribute", attribute)
    metadata.setdefault("entity_label", _clean_entity_history_topic(query.topic))
    entity_key = _open_memory_recall_entity_key(query)
    if entity_key:
        metadata.setdefault("entity_key", entity_key)
    enriched["metadata"] = metadata
    enriched.setdefault("memory_role", "current_state")
    return enriched


def _open_memory_recall_entity_key(query: OpenMemoryRecallQuery) -> str | None:
    if not _open_memory_recall_entity_attribute(query.query_kind):
        return None
    entity_label = _clean_entity_history_topic(query.topic)
    if not entity_label:
        return None
    slug = re.sub(r"[^a-z0-9]+", "-", entity_label.lower()).strip("-")
    return f"named-object:{slug or 'unknown'}"


def _entity_state_history_entity_key(query: EntityStateHistoryQuery) -> str | None:
    entity_label = _clean_entity_history_topic(query.topic)
    if not entity_label:
        return None
    slug = re.sub(r"[^a-z0-9]+", "-", entity_label.lower()).strip("-")
    return f"named-object:{slug or 'unknown'}"


def _entity_state_summary_entity_key(query: EntityStateSummaryQuery) -> str | None:
    entity_label = _clean_entity_history_topic(query.topic)
    if not entity_label:
        return None
    slug = re.sub(r"[^a-z0-9]+", "-", entity_label.lower()).strip("-")
    return f"named-object:{slug or 'unknown'}"


def _entity_state_record_entity_key(record: dict[str, Any]) -> str | None:
    metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    value = str(metadata.get("entity_key") or "").strip()
    return value or None


def _entity_state_history_as_of_before(record: dict[str, Any]) -> str | None:
    timestamp = _parse_memory_timestamp(_memory_record_timestamp(record))
    if timestamp is None:
        return None
    return (timestamp - timedelta(microseconds=1)).astimezone(timezone.utc).isoformat(timespec="microseconds")


def _open_memory_recall_entity_attribute(query_kind: str | None) -> str | None:
    return {
        "name_recall": "name",
        "location_recall": "location",
        "owner_recall": "owner",
        "status_recall": "status",
        "deadline_recall": "deadline",
        "relation_recall": "relation",
        "preference_recall": "preference",
        "project_recall": "project",
        "blocker_recall": "blocker",
        "priority_recall": "priority",
        "decision_recall": "decision",
        "next_action_recall": "next_action",
        "metric_recall": "metric",
    }.get(str(query_kind or "").strip())


def _entity_label_matches_open_memory_topic(*, entity_label: str, topic: str) -> bool:
    label_tokens = {
        token
        for token in re.findall(r"[a-z0-9][a-z0-9_-]*", str(entity_label or "").casefold())
        if token and token not in {"a", "an", "my", "of", "the"}
    }
    topic_tokens = [
        token
        for token in re.findall(r"[a-z0-9][a-z0-9_-]*", str(topic or "").casefold())
        if token and token not in {"a", "an", "is", "my", "of", "the", "where"}
    ]
    if not label_tokens or not topic_tokens:
        return False
    if len(topic_tokens) == 1:
        return topic_tokens[0] in label_tokens
    return all(token in label_tokens for token in topic_tokens)


def _entity_value_matches_open_memory_topic(*, attribute: str | None, value: str, topic: str) -> bool:
    if attribute != "decision":
        return False
    topic_tokens = {
        token
        for token in re.findall(r"[a-z0-9][a-z0-9_-]*", str(topic or "").casefold())
        if token and token not in {"a", "an", "is", "my", "of", "the", "what"}
    }
    if not {"onboarding", "direction"}.issubset(topic_tokens):
        return False
    value_tokens = {
        token
        for token in re.findall(r"[a-z0-9][a-z0-9_-]*", str(value or "").casefold())
        if token
    }
    return "onboarding" in value_tokens


def _filter_entity_state_history_records(
    *,
    query: EntityStateHistoryQuery,
    records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    for record in records:
        predicate = str(record.get("predicate") or "").strip()
        if predicate != query.predicate:
            continue
        metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
        entity_label = str(metadata.get("entity_label") or "").strip()
        if not entity_label or not _entity_label_matches_open_memory_topic(
            entity_label=entity_label,
            topic=query.topic,
        ):
            continue
        if not _profile_fact_record_value(record):
            continue
        filtered.append(record)
    return _ordered_profile_fact_event_records(filtered)


def _filter_entity_state_summary_records(
    *,
    query: EntityStateSummaryQuery,
    attribute: str,
    predicate: str,
    records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    entity_key = _entity_state_summary_entity_key(query)
    filtered: list[dict[str, Any]] = []
    for record in records:
        record_predicate = str(record.get("predicate") or "").strip()
        if record_predicate != predicate:
            continue
        metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
        record_attribute = str(metadata.get("entity_attribute") or record_predicate.split(".", 1)[-1]).strip()
        if record_attribute != attribute:
            continue
        record_entity_key = str(metadata.get("entity_key") or "").strip()
        entity_label = str(metadata.get("entity_label") or "").strip()
        if entity_key and record_entity_key and record_entity_key != entity_key:
            continue
        if not record_entity_key and (
            not entity_label
            or not _entity_label_matches_open_memory_topic(entity_label=entity_label, topic=query.topic)
        ):
            continue
        if not _profile_fact_record_value(record):
            continue
        filtered.append(record)
    return _ordered_profile_fact_event_records(_filter_open_memory_recall_records(filtered))


def _entity_state_record_label(record: dict[str, Any] | None, fallback: str) -> str:
    if record is None:
        return fallback
    metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    label = " ".join(str(metadata.get("entity_label") or "").strip().split())
    return label or fallback


def _entity_state_record_phrase(*, attribute: str, record: dict[str, Any]) -> str:
    metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    value = _profile_fact_record_value(record)
    if attribute == "location":
        preposition = str(metadata.get("location_preposition") or "at").strip()
        return f"{preposition} {value}"
    if attribute == "owner":
        return f"owned by {value}"
    if attribute == "deadline":
        return f"due {value}"
    if attribute == "status":
        return f"status was {value}"
    if attribute == "relation":
        return f"related to {value}"
    if attribute == "preference":
        return f"preference was {value}"
    if attribute == "project":
        return f"project was {value}"
    if attribute == "blocker":
        return f"blocker was {value}"
    if attribute == "priority":
        return f"priority was {value}"
    if attribute == "decision":
        return f"decision was {value}"
    if attribute == "next_action":
        return f"next action was {value}"
    if attribute == "metric":
        return f"metric was {value}"
    if attribute == "name":
        return f"named {value}"
    return f"{attribute} was {value}"


def _entity_state_summary_value(*, attribute: str, record: dict[str, Any]) -> str:
    metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    value = _profile_fact_record_value(record)
    if attribute == "location":
        preposition = str(metadata.get("location_preposition") or "at").strip()
        return f"{preposition} {value}".strip()
    return value


def _build_entity_state_summary_answer(
    *,
    query: EntityStateSummaryQuery,
    records_by_attribute: dict[str, dict[str, Any]],
) -> str:
    label = _entity_state_record_label(next(iter(records_by_attribute.values()), None), query.topic)
    if not records_by_attribute:
        return f"I don't currently have saved entity state for {query.topic}."
    lines = [f"Here is what I have for the {label}:", ""]
    for attribute, _, display_label in _ENTITY_STATE_SUMMARY_ATTRIBUTES:
        record = records_by_attribute.get(attribute)
        if not record:
            continue
        value = _entity_state_summary_value(attribute=attribute, record=record)
        if value:
            lines.append(f"- {display_label}: {value}")
    return "\n".join(lines)


def _build_entity_state_history_answer(
    *,
    query: EntityStateHistoryQuery,
    records: list[dict[str, Any]],
) -> str:
    ordered_records = _ordered_profile_fact_event_records(records)
    if not ordered_records:
        return f"I don't currently have saved {query.attribute} history for that."
    current_record = ordered_records[-1]
    current_value = _profile_fact_record_value(current_record)
    previous_record = _select_previous_profile_fact_record(
        current_value=current_value,
        records=ordered_records,
    )
    if previous_record is None:
        return f"I don't currently have an earlier saved {query.attribute} for that."
    label = _entity_state_record_label(current_record, query.topic)
    if query.attribute == "status":
        previous_value = _profile_fact_record_value(previous_record)
        return f"Before the {label} status was {current_value}, it was {previous_value}."
    if query.attribute == "preference":
        previous_value = _profile_fact_record_value(previous_record)
        return f"Before the {label} preference was {current_value}, it was {previous_value}."
    if query.attribute == "project":
        previous_value = _profile_fact_record_value(previous_record)
        return f"Before the {label} project was {current_value}, it was {previous_value}."
    if query.attribute == "blocker":
        previous_value = _profile_fact_record_value(previous_record)
        return f"Before the {label} blocker was {current_value}, it was {previous_value}."
    if query.attribute == "priority":
        previous_value = _profile_fact_record_value(previous_record)
        return f"Before the {label} priority was {current_value}, it was {previous_value}."
    if query.attribute == "decision":
        previous_value = _profile_fact_record_value(previous_record)
        return f"Before the {label} decision was {current_value}, it was {previous_value}."
    if query.attribute == "next_action":
        previous_value = _profile_fact_record_value(previous_record)
        return f"Before the {label} next action was {current_value}, it was {previous_value}."
    if query.attribute == "metric":
        previous_value = _profile_fact_record_value(previous_record)
        return f"Before the {label} metric was {current_value}, it was {previous_value}."
    current_phrase = _entity_state_record_phrase(attribute=query.attribute, record=current_record)
    previous_phrase = _entity_state_record_phrase(attribute=query.attribute, record=previous_record)
    if query.attribute in {"location", "deadline", "relation", "name"}:
        return f"Before the {label} was {current_phrase}, it was {previous_phrase}."
    if query.attribute == "owner":
        return f"Before the {label} was {current_phrase}, it was {previous_phrase}."
    return f"Before the {label} {current_phrase}, it {previous_phrase}."


def _build_open_memory_recall_answer(*, query: OpenMemoryRecallQuery, records: list[dict[str, Any]]) -> str:
    expected_attribute = _open_memory_recall_entity_attribute(query.query_kind)
    for record in records:
        entity_answer = _entity_state_answer_from_record(query=query, record=record)
        if entity_answer:
            return entity_answer
    if expected_attribute:
        return f"I don't currently have saved {expected_attribute} for that."
    snippets: list[str] = []
    seen: set[str] = set()
    for record in records:
        snippet = _memory_record_text(record)
        if not snippet:
            continue
        normalized = snippet.casefold()
        if normalized in seen:
            continue
        seen.add(normalized)
        snippets.append(snippet)
        if len(snippets) >= 2:
            break
    if not snippets:
        return "I don't currently have saved memory about that."
    if query.query_kind == "name_recall":
        for snippet in snippets:
            named_answer = _named_object_answer_from_snippet(topic=query.topic, snippet=snippet)
            if named_answer:
                return named_answer
    if len(snippets) == 1:
        return f'I have saved memory about {query.topic}: "{snippets[0]}"'
    return f'I have saved memory about {query.topic}: "{snippets[0]}" Also: "{snippets[1]}"'


def _filter_belief_recall_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    for record in records:
        predicate = str(record.get("predicate") or "").strip()
        role = str(record.get("memory_role") or "").strip()
        if role == "belief" or predicate.startswith("belief.telegram."):
            filtered.append(record)
    if not filtered:
        return filtered
    superseded_ids: set[str] = set()
    for record in filtered:
        lifecycle = record.get("lifecycle") if isinstance(record.get("lifecycle"), dict) else {}
        metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
        supersedes = str(lifecycle.get("supersedes") or metadata.get("supersedes") or "").strip()
        if supersedes:
            superseded_ids.add(supersedes)
    active = [
        record
        for record in filtered
        if str(record.get("observation_id") or (record.get("metadata") or {}).get("observation_id") or "").strip()
        not in superseded_ids
    ]
    if not active:
        return filtered
    return active


def _filter_structured_evidence_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    for record in records:
        predicate = str(record.get("predicate") or "").strip()
        role = str(record.get("memory_role") or "").strip()
        if role == "structured_evidence" or predicate.startswith("evidence.telegram."):
            filtered.append(record)
    return filtered


def _filter_current_state_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    for record in records:
        predicate = str(record.get("predicate") or "").strip()
        role = str(record.get("memory_role") or "").strip()
        if role == "current_state" or predicate.startswith("profile.current_"):
            filtered.append(record)
    return filtered


def _newer_current_state_records_than_beliefs(
    *,
    belief_records: list[dict[str, Any]],
    current_state_records: list[dict[str, Any]],
    topic: str,
) -> list[dict[str, Any]]:
    if not belief_records or not current_state_records:
        return []
    latest_belief_timestamp = max((_memory_record_timestamp(record) for record in belief_records), default="")
    if not latest_belief_timestamp:
        return []
    return [
        record
        for record in current_state_records
        if _record_matches_open_memory_topic(record=record, topic=topic)
        and _memory_record_timestamp(record) > latest_belief_timestamp
    ]


def _select_belief_recall_newer_evidence_records(
    *,
    belief_records: list[dict[str, Any]],
    candidate_records: list[dict[str, Any]],
    topic: str,
) -> list[dict[str, Any]]:
    if not belief_records or not candidate_records:
        return []
    topical_current_state_records = _newer_current_state_records_than_beliefs(
        belief_records=belief_records,
        current_state_records=_filter_current_state_records(candidate_records),
        topic=topic,
    )
    topical_structured_evidence_records = [
        record
        for record in _newer_evidence_than_beliefs(
            belief_records=belief_records,
            evidence_records=_filter_structured_evidence_records(candidate_records),
        )
        if _record_matches_open_memory_topic(record=record, topic=topic)
    ]
    if not topical_current_state_records and not topical_structured_evidence_records:
        return []
    return sorted(
        _merge_memory_records(topical_current_state_records, topical_structured_evidence_records),
        key=_memory_record_timestamp,
        reverse=True,
    )


def _synthesize_belief_records_from_consolidated_memory(
    *,
    records: list[dict[str, Any]],
    topic: str,
) -> list[dict[str, Any]]:
    matching_evidence = [
        record
        for record in _filter_structured_evidence_records(records)
        if _record_matches_open_memory_topic(record=record, topic=topic)
    ]
    if not matching_evidence:
        return []
    current_state_records = _filter_current_state_records(records)
    if not current_state_records:
        return []
    latest_current_state = sorted(current_state_records, key=_memory_record_timestamp)[-1]
    source_value = str(latest_current_state.get("value") or "").strip()
    if not source_value:
        source_value = _memory_record_text(latest_current_state).strip()
    if not source_value:
        return []
    belief_text = source_value if source_value.casefold().startswith("i think ") else f"I think {source_value[0].lower() + source_value[1:] if len(source_value) > 1 else source_value.lower()}"
    topic_slug = re.sub(r"[^a-z0-9]+", "_", str(topic or "synthetic").strip().lower()).strip("_") or "synthetic"
    return [
        {
            "memory_role": "belief",
            "predicate": f"belief.synthetic.{topic_slug}",
            "text": belief_text,
            "value": belief_text,
            "timestamp": _memory_record_timestamp(latest_current_state),
            "metadata": {
                "synthetic_from_current_state": True,
                "source_predicate": str(latest_current_state.get("predicate") or "").strip(),
                "source_topic": topic,
            },
        }
    ]


def _structured_evidence_invalidated_belief_ids(records: list[dict[str, Any]]) -> set[str]:
    invalidated_ids: set[str] = set()
    for record in _filter_structured_evidence_records(records):
        metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
        for belief_id in list(metadata.get("invalidated_belief_ids") or []):
            normalized = str(belief_id or "").strip()
            if normalized:
                invalidated_ids.add(normalized)
    return invalidated_ids


def _filter_records_by_observation_ids(records: list[dict[str, Any]], observation_ids: set[str]) -> list[dict[str, Any]]:
    if not observation_ids:
        return []
    filtered: list[dict[str, Any]] = []
    for record in records:
        record_id = str(record.get("observation_id") or (record.get("metadata") or {}).get("observation_id") or "").strip()
        if record_id and record_id in observation_ids:
            filtered.append(record)
    return filtered


def _merge_memory_records(primary: list[dict[str, Any]], supplemental: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for record in [*primary, *supplemental]:
        key = (
            str(record.get("observation_id") or (record.get("metadata") or {}).get("observation_id") or "").strip(),
            str(record.get("predicate") or "").strip(),
            _memory_record_text(record).strip().casefold(),
        )
        if key in seen:
            continue
        seen.add(key)
        merged.append(record)
    return merged


def _memory_record_timestamp(record: dict[str, Any]) -> str:
    metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
    return str(record.get("timestamp") or metadata.get("document_time") or "").strip()


def _parse_memory_timestamp(value: str | None) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _belief_records_past_revalidation(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc)
    stale_records: list[dict[str, Any]] = []
    for record in records:
        metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
        revalidate_at = _parse_memory_timestamp(metadata.get("revalidate_at"))
        if revalidate_at is None:
            timestamp = _parse_memory_timestamp(_memory_record_timestamp(record))
            if timestamp is not None:
                revalidate_at = timestamp + timedelta(days=30)
        if revalidate_at is not None and revalidate_at <= now:
            stale_records.append(record)
    return stale_records


def _raw_episode_records_past_archive(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc)
    stale_records: list[dict[str, Any]] = []
    for record in records:
        metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
        archive_at = _parse_memory_timestamp(metadata.get("archive_at"))
        if archive_at is None:
            timestamp = _parse_memory_timestamp(_memory_record_timestamp(record))
            if timestamp is not None:
                archive_at = timestamp + timedelta(days=14)
        if archive_at is not None and archive_at <= now:
            stale_records.append(record)
    return stale_records


def _structured_evidence_records_past_archive(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    now = datetime.now(timezone.utc)
    stale_records: list[dict[str, Any]] = []
    for record in records:
        metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
        archive_at = _parse_memory_timestamp(metadata.get("archive_at"))
        if archive_at is None:
            timestamp = _parse_memory_timestamp(_memory_record_timestamp(record))
            if timestamp is not None:
                archive_at = timestamp + timedelta(days=30)
        if archive_at is not None and archive_at <= now:
            stale_records.append(record)
    return stale_records


def _filter_raw_episode_records(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    for record in records:
        predicate = str(record.get("predicate") or "").strip()
        role = str(record.get("memory_role") or "").strip()
        metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
        if role == "episodic" or predicate == "raw_turn" or bool(metadata.get("raw_episode")):
            filtered.append(record)
    return filtered


def _newer_evidence_than_beliefs(
    *,
    belief_records: list[dict[str, Any]],
    evidence_records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not belief_records or not evidence_records:
        return []
    latest_belief_timestamp = max((_memory_record_timestamp(record) for record in belief_records), default="")
    if not latest_belief_timestamp:
        return []
    return [
        record
        for record in evidence_records
        if _memory_record_timestamp(record) > latest_belief_timestamp
    ]


def _newer_structured_evidence_than_raw_episodes(
    *,
    raw_episode_records: list[dict[str, Any]],
    evidence_records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not raw_episode_records or not evidence_records:
        return []
    latest_raw_episode_timestamp = max((_memory_record_timestamp(record) for record in raw_episode_records), default="")
    if not latest_raw_episode_timestamp:
        return []
    return [
        record
        for record in evidence_records
        if _memory_record_timestamp(record) > latest_raw_episode_timestamp
    ]


def _newer_structured_evidence_records(
    *,
    evidence_records: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if len(evidence_records) < 2:
        return []
    latest_evidence_timestamp = max((_memory_record_timestamp(record) for record in evidence_records), default="")
    if not latest_evidence_timestamp:
        return []
    return [
        record
        for record in evidence_records
        if _memory_record_timestamp(record) < latest_evidence_timestamp
    ]


def _build_belief_recall_answer(
    *,
    query: BeliefRecallQuery,
    records: list[dict[str, Any]],
    newer_evidence_records: list[dict[str, Any]] | None = None,
    stale_belief_records: list[dict[str, Any]] | None = None,
) -> str:
    if newer_evidence_records:
        snippets: list[str] = []
        seen: set[str] = set()
        for record in sorted(newer_evidence_records, key=_memory_record_timestamp, reverse=True):
            snippet = _memory_record_text(record)
            if not snippet:
                continue
            normalized = snippet.casefold()
            if normalized in seen:
                continue
            seen.add(normalized)
            snippets.append(snippet)
            if len(snippets) >= 2:
                break
        if snippets:
            if not records and not stale_belief_records:
                if len(snippets) == 1:
                    return (
                        f'I do not currently have a saved inferred belief about {query.topic}, '
                        f'but newer direct evidence says: "{snippets[0]}"'
                    )
                return (
                    f'I do not currently have saved inferred beliefs about {query.topic}, '
                    f'but newer direct evidence says: "{snippets[0]}" Also: "{snippets[1]}"'
                )
            if len(snippets) == 1:
                return (
                    f'I have an older inferred belief about {query.topic}, but newer direct evidence says: "{snippets[0]}" '
                    "I would not treat the older belief as current."
                )
            return (
                f'I have older inferred beliefs about {query.topic}, but newer direct evidence says: "{snippets[0]}" '
                f'Also: "{snippets[1]}" I would not treat the older belief as current.'
            )
        return (
            f"I have older inferred beliefs about {query.topic}, but newer direct evidence exists, "
            "so I would not treat the older belief as current."
        )
    if stale_belief_records:
        snippets: list[str] = []
        seen: set[str] = set()
        for record in stale_belief_records:
            snippet = _memory_record_text(record)
            if not snippet:
                continue
            normalized = snippet.casefold()
            if normalized in seen:
                continue
            seen.add(normalized)
            snippets.append(snippet)
            if len(snippets) >= 2:
                break
        if snippets:
            if len(snippets) == 1:
                return (
                    f'I have an older inferred belief about {query.topic}: "{snippets[0]}" '
                    "but it has not been revalidated recently, so I would not treat it as current."
                )
            return (
                f'I have older inferred beliefs about {query.topic}: "{snippets[0]}" Also: "{snippets[1]}" '
                "but they have not been revalidated recently, so I would not treat them as current."
            )
        return f"I have an older inferred belief about {query.topic}, but it has not been revalidated recently."
    snippets: list[str] = []
    seen: set[str] = set()
    for record in records:
        snippet = _memory_record_text(record)
        if not snippet:
            continue
        normalized = snippet.casefold()
        if normalized in seen:
            continue
        seen.add(normalized)
        snippets.append(snippet)
        if len(snippets) >= 2:
            break
    if not snippets:
        return "I don't currently have a saved belief about that."
    if len(snippets) == 1:
        return f'My saved belief about {query.topic} is: "{snippets[0]}" This is an inferred belief, not a direct fact.'
    return (
        f'My saved beliefs about {query.topic} are: "{snippets[0]}" Also: "{snippets[1]}" '
        "These are inferred beliefs, not direct facts."
    )


def _build_belief_observation_answer(*, belief_text: str) -> str:
    snippet = str(belief_text or "").strip()
    if not snippet:
        return "Got it. I'll keep that in mind."
    return f"Got it. I'll keep that in mind: \"{snippet}\""


def _build_structured_evidence_observation_answer(*, evidence_text: str) -> str:
    snippet = str(evidence_text or "").strip()
    if not snippet:
        return "Logged. I'll factor that in."
    return f"Logged. I'll factor that in: \"{snippet}\""


def _build_raw_episode_observation_answer(*, episode_text: str) -> str:
    snippet = str(episode_text or "").strip()
    if not snippet:
        return "Noted."
    return f"Noted: \"{snippet}\""


@dataclass
class ResearcherBridgeStatus:
    enabled: bool
    configured: bool
    available: bool
    mode: str
    runtime_root: str | None
    config_path: str | None
    attachment_context: dict[str, object]
    last_mode: str | None
    last_trace_ref: str | None
    last_request_id: str | None
    last_runtime_root: str | None
    last_config_path: str | None
    last_evidence_summary: str | None
    last_attachment_context: dict[str, Any] | None
    last_provider_id: str | None
    last_provider_model: str | None
    last_provider_model_family: str | None
    last_provider_auth_method: str | None
    last_provider_execution_transport: str | None
    last_routing_decision: str | None
    last_active_chip_key: str | None
    last_active_chip_task_type: str | None
    last_active_chip_evaluate_used: bool
    last_output_keepability: str | None
    last_promotion_disposition: str | None
    failure_count: int
    last_failure: dict[str, Any] | None

    def to_json(self) -> str:
        return json.dumps(
            {
                "configured": self.configured,
                "available": self.available,
                "enabled": self.enabled,
                "mode": self.mode,
                "runtime_root": self.runtime_root,
                "config_path": self.config_path,
                "attachment_context": self.attachment_context,
                "last_mode": self.last_mode,
                "last_trace_ref": self.last_trace_ref,
                "last_request_id": self.last_request_id,
                "last_runtime_root": self.last_runtime_root,
                "last_config_path": self.last_config_path,
                "last_evidence_summary": self.last_evidence_summary,
                "last_attachment_context": self.last_attachment_context,
                "last_provider_id": self.last_provider_id,
                "last_provider_model": self.last_provider_model,
                "last_provider_model_family": self.last_provider_model_family,
                "last_provider_auth_method": self.last_provider_auth_method,
                "last_provider_execution_transport": self.last_provider_execution_transport,
                "last_routing_decision": self.last_routing_decision,
                "last_active_chip_key": self.last_active_chip_key,
                "last_active_chip_task_type": self.last_active_chip_task_type,
                "last_active_chip_evaluate_used": self.last_active_chip_evaluate_used,
                "last_output_keepability": self.last_output_keepability,
                "last_promotion_disposition": self.last_promotion_disposition,
                "failure_count": self.failure_count,
                "last_failure": self.last_failure,
            },
            indent=2,
        )

    def to_text(self) -> str:
        lines = [
            f"Researcher bridge enabled: {'yes' if self.enabled else 'no'}",
            f"Researcher bridge configured: {'yes' if self.configured else 'no'}",
            f"- available: {'yes' if self.available else 'no'}",
            f"- mode: {self.mode}",
            f"- runtime_root: {self.runtime_root or 'missing'}",
            f"- config_path: {self.config_path or 'missing'}",
            f"- active_chip_keys: {', '.join(self.attachment_context.get('active_chip_keys', [])) if self.attachment_context.get('active_chip_keys') else 'none'}",
            f"- pinned_chip_keys: {', '.join(self.attachment_context.get('pinned_chip_keys', [])) if self.attachment_context.get('pinned_chip_keys') else 'none'}",
            f"- active_path_key: {self.attachment_context.get('active_path_key') or 'none'}",
            f"- last_mode: {self.last_mode or 'none'}",
            f"- last_trace_ref: {self.last_trace_ref or 'none'}",
            f"- last_request_id: {self.last_request_id or 'none'}",
        ]
        if self.last_runtime_root:
            lines.append(f"- last_runtime_root: {self.last_runtime_root}")
        if self.last_config_path:
            lines.append(f"- last_config_path: {self.last_config_path}")
        if self.last_evidence_summary:
            lines.append(f"- last_evidence_summary: {self.last_evidence_summary}")
        if self.last_provider_id:
            lines.append(f"- last_provider_id: {self.last_provider_id}")
        if self.last_provider_model:
            lines.append(f"- last_provider_model: {self.last_provider_model}")
        if self.last_provider_model_family:
            lines.append(f"- last_provider_model_family: {self.last_provider_model_family}")
        if self.last_provider_auth_method:
            lines.append(f"- last_provider_auth_method: {self.last_provider_auth_method}")
        if self.last_provider_execution_transport:
            lines.append(f"- last_provider_execution_transport: {self.last_provider_execution_transport}")
        if self.last_routing_decision:
            lines.append(f"- last_routing_decision: {self.last_routing_decision}")
        if self.last_active_chip_key:
            lines.append(f"- last_active_chip_key: {self.last_active_chip_key}")
        if self.last_active_chip_task_type:
            lines.append(f"- last_active_chip_task_type: {self.last_active_chip_task_type}")
        lines.append(f"- last_active_chip_evaluate_used: {'yes' if self.last_active_chip_evaluate_used else 'no'}")
        if self.last_output_keepability:
            lines.append(f"- last_output_keepability: {self.last_output_keepability}")
        if self.last_promotion_disposition:
            lines.append(f"- last_promotion_disposition: {self.last_promotion_disposition}")
        lines.append(f"- failure_count: {self.failure_count}")
        if self.last_failure:
            lines.append(
                f"- last_failure: mode={self.last_failure.get('mode')} "
                f"at={self.last_failure.get('recorded_at')} "
                f"message={self.last_failure.get('message')}"
            )
        return "\n".join(lines)


def discover_researcher_runtime_root(config_manager: ConfigManager) -> tuple[Path | None, str]:
    config = config_manager.load()
    configured_root = config.get("spark", {}).get("researcher", {}).get("runtime_root")
    if configured_root:
        path = config_manager.normalize_runtime_path(configured_root) or Path(str(configured_root)).expanduser()
        return (path if path.exists() else None, "configured")

    autodetect = Path.home() / "Desktop" / "spark-researcher"
    if autodetect.exists():
        return autodetect, "autodiscovered"
    return None, "missing"


def resolve_researcher_config_path(config_manager: ConfigManager, runtime_root: Path) -> Path:
    config = config_manager.load()
    configured_path = config.get("spark", {}).get("researcher", {}).get("config_path")
    if configured_path:
        return config_manager.normalize_runtime_path(configured_path) or Path(str(configured_path)).expanduser()
    return runtime_root / "spark-researcher.project.json"


def _import_build_advisory(runtime_root: Path):
    src_root = runtime_root / "src"
    if str(src_root) not in sys.path:
        sys.path.insert(0, str(src_root))
    module = importlib.import_module("spark_researcher.advisory")
    return getattr(module, "build_advisory")


def _import_execute_with_research(runtime_root: Path):
    src_root = runtime_root / "src"
    if str(src_root) not in sys.path:
        sys.path.insert(0, str(src_root))
    module = importlib.import_module("spark_researcher.research")
    return getattr(module, "execute_with_research")


def _render_reply_from_advisory(advisory: dict) -> tuple[str, str, str]:
    guidance = advisory.get("guidance") or []
    epistemic = advisory.get("epistemic_status") or {}
    selected_packet_ids = advisory.get("selected_packet_ids") or []
    trace_ref = advisory.get("trace_path") or advisory.get("trace_id") or "trace:missing"

    guidance_lines = guidance[:2] if isinstance(guidance, list) else []
    if guidance_lines:
        reply_text = " ".join(str(item).strip() for item in guidance_lines if str(item).strip())
    else:
        reply_text = "Spark Researcher returned no concrete guidance for this message."

    evidence_summary = (
        f"status={epistemic.get('status', 'unknown')} "
        f"packets={len(selected_packet_ids)} "
        f"stability={((epistemic.get('packet_stability') or {}).get('status', 'unknown'))}"
    )
    return reply_text, evidence_summary, str(trace_ref)


def _render_reply_from_execution(execution: dict[str, Any], advisory: dict[str, Any]) -> tuple[str, str, str]:
    reply_text = _extract_execution_reply_text(execution)
    decision = str(execution.get("decision") or execution.get("status") or "unknown")

    if not reply_text:
        if decision == "research_needed":
            research_query = str(
                execution.get("research_query")
                or advisory.get("original_user_message")
                or advisory.get("task")
                or ""
            ).strip()
            if research_query:
                reply_text = (
                    "I need live web evidence before I answer that, so I'm checking the web now for: "
                    f"{research_query}"
                )
            else:
                reply_text = "I need live web evidence before I answer that, so I'm checking the web now."
        else:
            reply_text, _, _ = _render_reply_from_advisory(advisory)

    trace_ref = (
        str(execution.get("research_trace_path") or "")
        or str(execution.get("trace_path") or "")
        or str(advisory.get("trace_path") or advisory.get("trace_id") or "trace:missing")
    )
    evidence_summary = f"status={decision} provider_execution=yes"
    return reply_text, evidence_summary, trace_ref


def _extract_execution_reply_text(execution: dict[str, Any]) -> str:
    for candidate in _execution_reply_candidates(execution):
        text = _extract_text_from_response_payload(candidate)
        if text:
            return text

    critique = execution.get("critique")
    if isinstance(critique, dict):
        best_next_question = str(critique.get("best_next_question") or "").strip()
        if best_next_question:
            return f"I need one thing before I give you a hard answer: {best_next_question}"

    clarifying_questions = execution.get("clarifying_questions")
    if isinstance(clarifying_questions, list):
        for item in clarifying_questions:
            question = str(item or "").strip()
            if question:
                return f"I need one thing before I give you a hard answer: {question}"

    return ""


def _execution_reply_candidates(execution: dict[str, Any]) -> list[Any]:
    candidates: list[Any] = [execution.get("response")]
    drafts = execution.get("drafts")
    if isinstance(drafts, dict):
        selected = str(drafts.get("selected") or "").strip().lower()
        if selected in {"a", "b"}:
            candidates.append(drafts.get(selected))
    candidates.extend([execution.get("draft"), execution.get("revised")])
    if isinstance(drafts, dict):
        candidates.extend([drafts.get("a"), drafts.get("b")])
    return [candidate for candidate in candidates if candidate]


def _extract_text_from_response_payload(payload: Any) -> str:
    if isinstance(payload, str):
        return payload.strip()
    if not isinstance(payload, dict):
        return ""
    raw = payload.get("raw_response")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    response = payload.get("response")
    if response is not None and response is not payload:
        nested = _extract_text_from_response_payload(response)
        if nested:
            return nested
    return ""


def _researcher_routing_policy(config_manager: ConfigManager) -> dict[str, Any]:
    return {
        "conversational_fallback_enabled": bool(
            config_manager.get_path("spark.researcher.routing.conversational_fallback_enabled", default=True)
        ),
        "conversational_fallback_max_chars": int(
            config_manager.get_path("spark.researcher.routing.conversational_fallback_max_chars", default=240)
        ),
    }


_FAST_GREETING_PHRASES = frozenset(
    {
        "hi", "hey", "hello", "yo", "sup",
        "what's up", "whats up",
        "how are you", "how are you doing",
        "how's it going", "hows it going",
        "good morning", "good afternoon", "good evening",
        "thanks", "thank you", "ty",
        "ok", "okay", "cool", "nice", "got it",
    }
)


# ────────────────────────────────────────────────────────────────────
# Multi-tier intent router (see spark-tui-lab/ROUTING.md and
# spark-tui-lab/ROUTING_RESEARCH.md for design and field research).
#
# Five tiers, each with a different latency budget and context
# assembly:
#
#   Tier 0  instant    — greetings, acknowledgments    (≤2s)
#   Tier 1  direct     — LLM + personality + memory    (2-5s)
#   Tier 2  scoped     — one targeted tool call        (deferred, slash-only)
#   Tier 3  research   — advisory + web + multi-source (15-30s)
#   Tier 4  agent      — harness runtime               (deferred, slash-only)
#
# The heuristic classifier intentionally does only THREE jobs:
#
#   1. Catch trivial greetings (Tier 0) via a tight allowlist
#   2. Catch explicit research needs (Tier 3) via a tight hard-phrase
#      list + live-data-noun + time-word combo
#   3. Default everything else to Tier 1 (direct) — the fat middle
#
# Tier 2 and Tier 4 are NOT auto-detected. Users enter them by typing
# a slash command prefix (/scoped, /agent, /architect). This matches
# the pattern every shipping agent CLI converges on: keyword routing
# is brittle, so keep the keyword list small and let explicit user
# prefixes carry the edge cases (per Claude Code / Aider / gptme).
#
# Slash-command overrides always win over heuristic classification.
# ────────────────────────────────────────────────────────────────────


# Hard research signals. Any of these → Tier 3 regardless of other
# factors. Indicates the user genuinely needs live data.
_RESEARCH_HARD_SIGNALS = (
    # Time-sensitive language
    "latest news", "breaking news", "happening right now",
    "right now", "as of today", "as of now", "as we speak",
    "this morning", "this evening", "this afternoon",
    # Live-data entities with implied time sensitivity
    "current price", "stock price", "exchange rate", "market cap",
    "score of", "final score", "latest score",
    # Explicit research verbs as sentence starters (whole-word match)
    "research ", "look up ", "search for ", "find info on ",
    "look into ", "investigate ", "dig into ",
    # Specific named-entity-with-time patterns
    "weather today", "weather tomorrow",
)

# Time-word + live-data-noun combo → Tier 3.
# A single word like 'price' alone isn't enough; 'current BTC price' is.
_RESEARCH_TIME_WORDS = frozenset({
    "latest", "today", "tomorrow", "current", "currently",
    "recent", "recently", "now",
})
_RESEARCH_LIVE_DATA_NOUNS = (
    "price", "news", "market", "rate", "score",
    "weather", "forecast", "stock", "trending",
)


# Slash-command → tier mapping. Every shipping agent CLI uses slash
# commands as hard overrides that bypass classification entirely.
# Aliases match vocabulary from Aider (ask/architect) and generic
# help convention.
_TIER_SLASH_COMMANDS = {
    "/instant": "instant",
    "/direct": "direct",
    "/scoped": "scoped",
    "/research": "research",
    "/agent": "agent",
    # Aider vocabulary aliases
    "/ask": "direct",       # Aider 'ask' = conceptual chat = direct
    "/architect": "agent",  # Aider 'architect' = plan + execute = agent
    # Universal help convention → instant tier (self-referential, fast)
    "/help": "instant",
}


def _classify_intent_tier(user_message: str) -> str:
    """Classify a chat message into one of: instant, direct, scoped, research, agent.

    Pure heuristic — no LLM call. Does three jobs and nothing else:
      1. Honor slash-command overrides (hard dispatch)
      2. Catch trivial greetings (Tier 0)
      3. Catch hard research signals (Tier 3)

    Everything else defaults to Tier 1 (direct). Tier 2 and Tier 4
    are only reachable via slash-command override — the classifier
    does NOT auto-detect 'this is a workspace query' or 'this is a
    multi-step task' because keyword routing for those boundaries
    is brittle (per research of Claude Code, Aider, gptme, et al).

    See spark-tui-lab/ROUTING_RESEARCH.md for the field study.
    """
    if not user_message:
        return "direct"

    raw = user_message.strip()
    lowered = re.sub(r"\s+", " ", raw.lower())

    # 1. Slash-command hard overrides. Strip the command from the
    #    message and return the mapped tier. The caller is responsible
    #    for also stripping the command before sending to the LLM.
    first_word = lowered.split(" ", 1)[0] if lowered else ""
    if first_word in _TIER_SLASH_COMMANDS:
        return _TIER_SLASH_COMMANDS[first_word]

    # Shortcut prefixes: '?' = research, '!' = agent. Same pattern as
    # the full slash commands but one-character for speed.
    if raw.startswith("?") and not raw.startswith("??"):
        return "research"
    if raw.startswith("!") and not raw.startswith("!!"):
        return "agent"

    # 2. URLs → research (we need to fetch them).
    if "://" in lowered:
        return "research"

    # 3. Tier 0: instant greetings (reuses the fast-greeting allowlist).
    if _is_fast_greeting(user_message):
        return "instant"

    # 4. Tier 3: hard research signals — phrases that unambiguously
    #    request live data.
    for phrase in _RESEARCH_HARD_SIGNALS:
        if phrase in lowered:
            return "research"

    # 5. Tier 3: time-word + live-data-noun combo. Both must be present
    #    and the message must be short enough that it's plausibly a
    #    live-data query (not a long conceptual discussion that happens
    #    to use 'current' in passing).
    if len(lowered) <= 120:
        tokens = set(lowered.split())
        if tokens & _RESEARCH_TIME_WORDS:
            if any(noun in lowered for noun in _RESEARCH_LIVE_DATA_NOUNS):
                return "research"

    # 6. Default: direct. The fat middle. Conceptual, creative, coding,
    #    opinion, follow-up — anything that isn't a trivial greeting or
    #    an obvious research query lands here and gets a fast LLM reply
    #    with full personality + memory + chip context.
    return "direct"


def _strip_tier_slash_command(user_message: str) -> tuple[str, str | None]:
    """If the message starts with a known tier slash command, strip it.

    Returns (cleaned_message, slash_command) where slash_command is
    None if no override was present.
    """
    if not user_message:
        return user_message, None
    raw = user_message.strip()
    lowered = raw.lower()
    first_word = lowered.split(" ", 1)[0] if lowered else ""
    if first_word in _TIER_SLASH_COMMANDS:
        # Remove the first word (the slash command) and return the rest
        remainder = raw.split(None, 1)
        cleaned = remainder[1] if len(remainder) > 1 else ""
        return cleaned, first_word
    if raw.startswith("?") and not raw.startswith("??"):
        return raw[1:].lstrip(), "?"
    if raw.startswith("!") and not raw.startswith("!!"):
        return raw[1:].lstrip(), "!"
    return user_message, None


def _is_fast_greeting(user_message: str) -> bool:
    """True for short trivial greetings where advisory is wasted work.

    The advisory subprocess is the slowest part of every reply, especially
    on Windows where Python cold-start is 500ms-1s. For 'hi', running
    advisory only to discover that we should use the conversational
    fallback is a multi-second waste.
    """
    if not user_message:
        return False
    lowered = re.sub(r"\s+", " ", user_message.strip().lower()).rstrip("!.?")
    if not lowered or len(lowered) > 60:
        return False
    if any(char.isdigit() for char in lowered):
        return False
    return lowered in _FAST_GREETING_PHRASES


def _synthesize_skipped_advisory(user_message: str, request_id: str) -> dict[str, Any]:
    """Fake advisory that triggers the conversational fallback path."""
    return {
        "guidance": [],
        "boundaries": [],
        "packets": [],
        "packet_ids": [],
        "epistemic_status": {
            "status": "under_supported",
            "clarity": "skipped_for_greeting",
            "recommended_actions": [],
        },
        "trace_id": f"fast-greeting-{request_id}",
        "trace_path": "",
        "intent": {"query": user_message},
        "original_user_message": user_message,
    }


def _is_conversational_fallback_candidate(
    *,
    user_message: str,
    advisory: dict[str, Any],
    fallback_max_chars: int,
) -> bool:
    epistemic = advisory.get("epistemic_status", {}) if isinstance(advisory, dict) else {}
    if str(epistemic.get("status") or "") != "under_supported":
        return False
    lowered = re.sub(r"\s+", " ", str(user_message or "").strip().lower())
    if not lowered:
        return False
    if len(lowered) > fallback_max_chars:
        return False
    if any(char.isdigit() for char in lowered):
        return False
    blocked_terms = (
        "http://",
        "https://",
        "latest",
        "today",
        "news",
        "price",
        "stock",
        "lawsuit",
        "legal",
        "medical",
        "diagnose",
        "treatment",
        "tax",
        "invest",
        "contract",
        "traceback",
        "exception",
        "stack trace",
        "error code",
    )
    if any(term in lowered for term in blocked_terms):
        return False
    if lowered in {
        "hi",
        "hey",
        "hello",
        "yo",
        "sup",
        "what's up",
        "whats up",
        "how are you",
        "how are you doing",
        "good morning",
        "good afternoon",
        "good evening",
    }:
        return True
    tokens = lowered.split()
    return len(tokens) <= 10


_SPARK_CHARACTER_PERSONA_CACHE: str | None = None


def try_spark_character_fallback(
    *,
    user_message: str,
    config_manager: ConfigManager,
    use_critic: bool = False,
    surface: str | None = None,
) -> str | None:
    """Generate a reply via spark-character when the Researcher bridge
    cannot serve the request. Provider-agnostic, picks up ZAI_API_KEY /
    OPENAI_API_KEY / etc from the workspace .env file. Returns None on
    any failure so the caller can fall through to the error copy.
    """
    text = str(user_message or "").strip()
    if not text:
        return None
    try:
        ensure_spark_character_path()
        from spark_character import (  # type: ignore
            ProviderSpec,
            generate,
            generate_with_critique,
            load_persona,
        )
    except Exception:
        return None
    env_map: dict[str, str] = {}
    try:
        env_map = dict(config_manager.read_env_map() or {})
    except Exception:
        env_map = {}
    provider = _resolve_spark_character_provider(env_map)
    if provider is None:
        return None
    tools = _spark_character_provider_tools(provider)
    try:
        try:
            from spark_character.persona import detect_provider_kind  # type: ignore
            kind = detect_provider_kind(provider)
        except Exception:
            kind = None
        persona = _resolve_chip_or_persona(kind=kind, surface=surface)
        if use_critic:
            result = generate_with_critique(text, provider=provider, persona=persona)
        else:
            # enable_search=True turns on the spark-character client-side
            # web search adapter for prompts that look like they need
            # current data (price, news, today's, latest). Provider-
            # agnostic, works even when the backend's native web_search
            # tool is ignored (Z.AI coding endpoint) or unavailable.
            result = generate(
                text,
                provider=provider,
                persona=persona,
                tools=tools,
                enable_search=True,
                surface=surface,
            )
        reply = str(result.final or "").strip()
        return reply or None
    except Exception:
        return None


def _resolve_chip_or_persona(*, kind: str | None, surface: str | None = None):
    """Try chip-rendered persona first (with provider + surface overlays
    appended), fall back to flat-MD persona if the chip lab is not present.

    Returns a spark_character.PersonaSpec or None on any failure so the
    caller can decide what to do.

    surface: 'voice' / 'browser_extension' / 'telegram' / etc. Appends the
    matching surface overlay so output is shaped for that delivery channel
    (short declarative sentences for voice, popup-friendly for browser, etc).
    """
    try:
        ensure_spark_character_path()
        from spark_character import (  # type: ignore
            PersonaSpec,
            load_chip_by_id,
            render_chip_to_system_prompt,
        )
        from spark_character.persona import load_overlay, load_surface_overlay  # type: ignore
        chip_id = _resolve_active_personality_chip_id()
        chip = load_chip_by_id(chip_id)
        rendered = render_chip_to_system_prompt(chip)
        parts = [rendered]
        if kind:
            overlay = load_overlay(kind)
            if overlay:
                parts.append(overlay)
        if surface:
            try:
                surface_overlay = load_surface_overlay(surface)
                if surface_overlay:
                    parts.append(surface_overlay)
            except Exception:
                pass
        combined = "\n\n---\n\n".join(parts)
        version_tag = f"chip:{chip_id}"
        if kind:
            version_tag += f":{kind}"
        if surface:
            version_tag += f":{surface}"
        return PersonaSpec(version=version_tag, text=combined)
    except Exception:
        try:
            return (
                load_persona(provider_kind=kind, surface=surface)
                if (kind or surface)
                else load_persona()
            )
        except Exception:
            return None


def _spark_character_provider_tools(provider) -> list[dict] | None:
    """Return the native tool list to attach for this provider so Spark
    can ask for live data when the user needs it. Z.AI exposes
    web_search as a built-in tool; the model decides when to call it.
    For providers that do not support native tools we attach nothing
    and the persona spec falls back to the "say so plainly" rule.
    """
    base = (getattr(provider, "base_url", "") or "").lower()
    if "z.ai" in base or "zhipuai" in base or "bigmodel" in base:
        return [{"type": "web_search", "web_search": {"enable": True, "search_result": True}}]
    return None


def _resolve_spark_character_provider(env_map: dict[str, str]):
    try:
        ensure_spark_character_path()
        from spark_character import ProviderSpec  # type: ignore
    except Exception:
        return None
    candidates = (
        ("ZAI_API_KEY", "ZAI_BASE_URL", "ZAI_MODEL",
         "https://api.z.ai/api/coding/paas/v4/", "glm-5.1"),
        ("MINIMAX_API_KEY", "MINIMAX_BASE_URL", "MINIMAX_MODEL",
         "https://api.minimax.io/v1/", "MiniMax-M2.7"),
        ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL",
         "https://api.openai.com/v1/", "gpt-4o-mini"),
    )
    for key_env, base_env, model_env, default_base, default_model in candidates:
        api_key = (env_map.get(key_env) or "").strip()
        if not api_key:
            continue
        base_url = (env_map.get(base_env) or default_base).strip()
        model = (env_map.get(model_env) or default_model).strip()
        return ProviderSpec(base_url=base_url, model=model, api_key=api_key)
    return None


_ACTIVE_PERSONALITY_CACHE: str | None = None


def _resolve_active_personality_chip_id() -> str:
    """Resolve the active personality chip id.

    Priority order:
      1. SPARK_INTELLIGENCE_PERSONALITY env override (operator escape hatch).
      2. SIB's own agent_persona_profiles table when populated (workspace-
         level active persona, set via SIB's persona ops commands).
      3. spark-personality-chip-labs ~/.spark/active_personality.json
         (system-wide active personality set via the chip lab UI / CLI).
      4. Fallback to founder-operator.

    Cached per process. Restart the gateway to pick up a switch.
    """
    global _ACTIVE_PERSONALITY_CACHE
    if _ACTIVE_PERSONALITY_CACHE is not None:
        return _ACTIVE_PERSONALITY_CACHE
    # 1. Operator override
    env_override = (os.environ.get("SPARK_INTELLIGENCE_PERSONALITY") or "").strip()
    if env_override:
        _ACTIVE_PERSONALITY_CACHE = env_override
        return _ACTIVE_PERSONALITY_CACHE
    # 2. SIB workspace-level (agent_persona_profiles)
    sib_active = _read_sib_active_personality_id()
    if sib_active:
        _ACTIVE_PERSONALITY_CACHE = sib_active
        return _ACTIVE_PERSONALITY_CACHE
    # 3. Chip lab system-wide
    try:
        from personality_engine.active import get_active_personality_id  # type: ignore
        active = get_active_personality_id()
        if active:
            _ACTIVE_PERSONALITY_CACHE = str(active)
            return _ACTIVE_PERSONALITY_CACHE
    except Exception:
        pass
    # 4. Fallback
    _ACTIVE_PERSONALITY_CACHE = "founder-operator"
    return _ACTIVE_PERSONALITY_CACHE


def _read_sib_active_personality_id() -> str | None:
    """Read the most recently updated agent_persona_profiles row for the
    current workspace's state.db. Returns the persona_name when one exists,
    None otherwise. Soft-fails on any error so the resolver can continue."""
    home = os.environ.get("SPARK_INTELLIGENCE_HOME")
    if not home:
        return None
    try:
        import sqlite3
        from pathlib import Path
        db = Path(home) / "state.db"
        if not db.exists():
            return None
        con = sqlite3.connect(str(db))
        try:
            cur = con.cursor()
            cur.execute(
                "SELECT persona_name FROM agent_persona_profiles "
                "WHERE persona_name IS NOT NULL AND persona_name != '' "
                "ORDER BY updated_at DESC LIMIT 1"
            )
            row = cur.fetchone()
            return str(row[0]) if row else None
        finally:
            con.close()
    except Exception:
        return None


def _load_spark_character_persona() -> str:
    """Load Spark's canonical persona.

    Resolution order, all soft-failing:
      1. spark-personality-chip-labs chip (rendered via spark-character's
         render_chip_to_system_prompt). This is the unified path: chip
         lab is canonical schema, spark-character is the rendering and
         evolution engine.
      2. spark-character flat persona artifact (load_persona) for
         legacy / chip-lab-not-installed cases.
      3. A minimal inline voice rule set as the absolute fallback so
         SIB never breaks on a missing dependency chain.
    """
    global _SPARK_CHARACTER_PERSONA_CACHE
    if _SPARK_CHARACTER_PERSONA_CACHE is not None:
        return _SPARK_CHARACTER_PERSONA_CACHE
    # Try chip-rendered path first
    try:
        ensure_spark_character_path()
        from spark_character import load_chip_by_id, render_chip_to_system_prompt  # type: ignore
        chip_id = _resolve_active_personality_chip_id()
        chip = load_chip_by_id(chip_id)
        rendered = render_chip_to_system_prompt(chip)
        if rendered:
            _SPARK_CHARACTER_PERSONA_CACHE = rendered
            return _SPARK_CHARACTER_PERSONA_CACHE
    except Exception:
        pass
    # Fallback: flat MD persona artifact
    try:
        ensure_spark_character_path()
        from spark_character import load_persona  # type: ignore
        _SPARK_CHARACTER_PERSONA_CACHE = load_persona().system_prompt
    except Exception:
        _SPARK_CHARACTER_PERSONA_CACHE = (
            "You are Spark, the user's personal operator and thinking partner. "
            "Lead with the answer in the first sentence. Be warm but high-signal. "
            "Never use em dashes. Never name internal subsystems. "
            "Continue the conversation, do not reset to a greeting."
        )
    return _SPARK_CHARACTER_PERSONA_CACHE


_L1_STATE_PREDICATE_LABELS: tuple[tuple[str, str], ...] = (
    ("profile.cofounder_name", "cofounder"),
    ("profile.partner_name", "partner"),
    ("profile.mentor_name", "mentor"),
    ("profile.assistant_name", "assistant"),
    ("profile.current_focus", "current_focus"),
    ("profile.current_plan", "current_plan"),
    ("profile.current_low_stakes_test_fact", "low_stakes_test_fact"),
    ("profile.current_blocker", "current_blocker"),
    ("profile.current_decision", "current_decision"),
    ("profile.current_status", "current_status"),
    ("profile.current_commitment", "current_commitment"),
    ("profile.current_milestone", "current_milestone"),
    ("profile.current_risk", "current_risk"),
    ("profile.current_dependency", "current_dependency"),
    ("profile.current_constraint", "current_constraint"),
    ("profile.current_owner", "current_owner"),
    ("profile.recent_family_members", "recent_family"),
    ("profile.favorite_color", "favorite_color"),
    ("profile.favorite_food", "favorite_food"),
)


def _build_current_state_block(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    human_id: str,
    channel_kind: str = "",
) -> str:
    """Project current profile facts into an always-on L1 state block."""
    if not human_id:
        return ""
    candidates: list[str] = []
    if channel_kind and not human_id.startswith(f"{channel_kind}:"):
        candidates.append(f"{channel_kind}:{human_id}")
    candidates.append(human_id)

    records: list[dict[str, Any]] = []
    for candidate in candidates:
        try:
            inspection = inspect_human_memory_in_memory(
                config_manager=config_manager,
                state_db=state_db,
                human_id=candidate,
                actor_id="researcher_bridge_l1_state",
            )
        except Exception:
            continue
        records = (inspection.read_result.records if inspection.read_result else None) or []
        if records:
            break

    by_predicate: dict[str, dict[str, Any]] = {}
    for record in records:
        predicate = str(record.get("predicate") or "").strip()
        value = str(record.get("value") or "").strip()
        if not predicate or not value:
            continue
        current = by_predicate.get(predicate)
        if current is None or _memory_record_timestamp(record) >= _memory_record_timestamp(current):
            by_predicate[predicate] = record

    lines: list[str] = []
    for predicate, label in _L1_STATE_PREDICATE_LABELS:
        record = by_predicate.get(predicate)
        value = str(record.get("value") or "").strip() if record else ""
        if value:
            lines.append(f"- {label}: {value}")
    if not lines:
        return ""
    return "[CURRENT STATE]\n" + "\n".join(lines)


def _render_direct_provider_chat_fallback(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    provider: RuntimeProviderResolution,
    user_message: str,
    channel_kind: str,
    attachment_context: dict[str, object],
    active_chip_evaluate: dict[str, Any] | None = None,
    personality_profile: dict[str, Any] | None = None,
    personality_context_extra: str = "",
    browser_search_context_extra: str = "",
    recent_conversation_context: str = "",
    run_id: str | None = None,
    request_id: str | None = None,
    trace_ref: str | None = None,
    enable_web_search: bool = False,
    human_id: str = "",
    session_id: str = "",
) -> str:
    # Voice + character: load the canonical persona from spark-character.
    # Anyone running their own Spark inherits the same evolved voice from
    # the package's persona artifact. SIB-specific operational guidance is
    # appended below.
    persona_prompt = _load_spark_character_persona()
    # Append surface overlay when the surface is identifiable. The browser
    # path is the most common non-text surface for the in-pipeline render,
    # detected via browser_search_context_extra being set.
    inferred_surface = "browser_extension" if browser_search_context_extra else None
    if inferred_surface:
        try:
            ensure_spark_character_path()
            from spark_character.persona import load_surface_overlay  # type: ignore
            surface_overlay = load_surface_overlay(inferred_surface)
            if surface_overlay:
                persona_prompt = f"{persona_prompt}\n\n---\n\n{surface_overlay}"
        except Exception:
            pass
    current_state_block = _build_current_state_block(
        config_manager=config_manager,
        state_db=state_db,
        human_id=human_id,
        channel_kind=channel_kind,
    )
    context_capsule_obj = build_spark_context_capsule(
        config_manager=config_manager,
        state_db=state_db,
        human_id=human_id,
        session_id=session_id,
        channel_kind=channel_kind,
        request_id=request_id,
        user_message=user_message,
    )
    context_capsule = context_capsule_obj.render()
    if context_capsule and request_id:
        record_event(
            state_db,
            event_type="context_capsule_compiled",
            component="researcher_bridge",
            summary="Spark context capsule was compiled for the direct-provider fallback prompt.",
            run_id=run_id,
            request_id=request_id,
            trace_ref=trace_ref,
            channel_id=channel_kind,
            session_id=session_id,
            human_id=human_id,
            actor_id="researcher_bridge",
            reason_code="context_capsule_compiled_direct_provider",
            facts={
                "keepability": "ephemeral_context",
                "capsule_chars": len(context_capsule),
                "source_counts": context_capsule_obj.source_counts,
                "source_ledger": context_capsule_obj.source_ledger(),
                "context_route": "direct_provider_fallback",
            },
            provenance={
                "source_kind": "context_compiler",
                "source_ref": "spark_context_capsule.v1",
            },
        )
    base_system_prompt = (
        f"{persona_prompt}\n\n"
        + (f"{current_state_block}\n\n" if current_state_block else "")
        + "Operational rules for this conversation surface:\n"
        "- When domain chip guidance is attached, treat it as hidden background context rather than an output template. "
        "Do not echo internal headings, confidence scores, packet ids, doctrine labels, or evidence-gap sections unless the user explicitly asks for them.\n"
        "- If the user asks for factual, legal, medical, financial, or time-sensitive guidance "
        "and you are not confident, say plainly that you need more context or verification before giving a hard answer.\n"
        "- When evidence is good enough, make the call. Do not over-hedge. Do not mention internal advisory or verification systems.\n"
        "- The [CURRENT STATE] block above is ground truth about the user. Use it to anchor replies. Do not repeat it back verbatim unless asked. Do not say you don't know something that's listed there."
    )
    if browser_search_context_extra:
        base_system_prompt = (
            f"{base_system_prompt} "
            "Browser search evidence is already attached in the user prompt. "
            "Do not say you cannot browse, cannot access real-time data, or need to look something up. "
            "Answer directly from the attached browser evidence. "
            "When the user wants an opinion on a site or product, anchor the answer in two or three concrete details from the page before giving your verdict. "
            "Prefer a crisp conclusion over a generic coaching question. "
            "Only cite the provided source_url in plain text when it is present and external. "
            "Never cite search_url or a search-engine results page as the source. "
            "If the evidence is approximate or snippet-based, say that plainly, but still answer the user's question."
        )
    elif enable_web_search:
        base_system_prompt = (
            f"{base_system_prompt} "
            "You have a web_search tool attached and MUST use it for any question requiring current, real-time, or external factual information. "
            "Do not claim you cannot browse the web; you can. "
            "After searching, synthesize a concise answer and cite 2 to 4 source URLs in plain text at the end."
        )
    if _is_startup_operator_chip(active_chip_evaluate):
        base_system_prompt = f"{base_system_prompt} {_startup_operator_reply_contract()}"
    if personality_profile:
        personality_directive = build_personality_system_directive(personality_profile)
        if personality_directive:
            base_system_prompt = f"{base_system_prompt} {personality_directive}"
        if channel_kind == "telegram":
            telegram_persona_contract = build_telegram_persona_reply_contract(personality_profile)
            if telegram_persona_contract:
                base_system_prompt = f"{base_system_prompt} {telegram_persona_contract}"
    system_registry_context = build_system_registry_prompt_context(
        config_manager=config_manager,
        state_db=state_db,
        user_message=user_message,
    )
    mission_control_context = build_mission_control_prompt_context(
        config_manager=config_manager,
        state_db=state_db,
        user_message=user_message,
    )
    capability_router_context = build_capability_router_prompt_context(
        config_manager=config_manager,
        state_db=state_db,
        user_message=user_message,
    )
    harness_context = build_harness_prompt_context(
        config_manager=config_manager,
        state_db=state_db,
        user_message=user_message,
    )
    payload = execute_direct_provider_prompt(
        provider=DirectProviderRequest(
            provider_id=provider.provider_id,
            provider_kind=provider.provider_kind,
            auth_method=provider.auth_method,
            api_mode=provider.api_mode,
            base_url=provider.base_url,
            model=provider.default_model,
            secret_value=provider.secret_value,
        ),
        system_prompt=base_system_prompt,
        user_prompt=_build_contextual_task(
            user_message=(
                f"[channel_kind={channel_kind}]\n"
                f"[fallback_mode=conversational_under_supported]\n"
                f"{user_message}"
            ),
            channel_kind=channel_kind,
            attachment_context=attachment_context,
            active_chip_evaluate=active_chip_evaluate,
            personality_profile=personality_profile,
            personality_context_extra=personality_context_extra,
            browser_search_context_extra=browser_search_context_extra,
            recent_conversation_context=recent_conversation_context,
            context_capsule=context_capsule,
            system_registry_context=system_registry_context,
            mission_control_context=mission_control_context,
            capability_router_context=capability_router_context,
            harness_context=harness_context,
        ),
        governance=DirectProviderGovernance(
            state_db_path=str(state_db.path),
            source_kind="researcher_bridge_direct_prompt",
            source_ref=request_id or provider.provider_id,
            summary="Builder blocked direct-provider fallback context before execution.",
            reason_code="provider_fallback_prompt_secret_like",
            policy_domain="researcher_bridge",
            blocked_stage="pre_model",
            run_id=run_id,
            request_id=request_id,
            trace_ref=trace_ref,
            provenance={
                "source_kind": "researcher_bridge",
                "source_ref": provider.provider_id,
                "channel_kind": channel_kind,
                "active_chip_key": active_chip_evaluate.get("chip_key") if active_chip_evaluate else None,
                "active_path_key": attachment_context.get("active_path_key"),
                "personality_name": personality_profile.get("personality_name") if personality_profile else None,
            },
        ),
        tools=(
            [{"type": "web_search", "web_search": {"enable": True, "search_result": True}}]
            if enable_web_search
            else None
        ),
    )
    raw_response = str(payload.get("raw_response") or "").strip()
    if not raw_response:
        raise RuntimeError("Direct provider fallback returned no text content.")
    if browser_search_context_extra:
        raw_response = _rewrite_browser_search_capability_denial(
            raw_response,
            browser_search_context_extra=browser_search_context_extra,
        )
    return raw_response


def _parse_browser_search_context_facts(context: str) -> dict[str, str]:
    facts: dict[str, str] = {}
    for raw_line in str(context or "").splitlines():
        line = raw_line.strip()
        if not line or "=" not in line:
            continue
        key, value = line.split("=", 1)
        facts[key.strip()] = value.strip()
    return facts


def _browser_reply_denies_browsing(text: str) -> bool:
    normalized = str(text or "").strip().lower()
    denial_patterns = (
        "i don't have real-time web search",
        "i do not have real-time web search",
        "i don't have real-time web browsing capability",
        "i do not have real-time web browsing capability",
        "i don't have web access",
        "i do not have web access",
        "i can't browse",
        "i cannot browse",
        "i can't access the web",
        "i cannot access the web",
        "i can't pull live",
        "i cannot pull live",
        "i can't access real-time data",
        "i cannot access real-time data",
        "i can't look that up",
        "i cannot look that up",
    )
    return any(pattern in normalized for pattern in denial_patterns)


def _rewrite_browser_search_capability_denial(reply_text: str, *, browser_search_context_extra: str) -> str:
    text = str(reply_text or "").strip()
    if not text or not _browser_reply_denies_browsing(text):
        return text
    facts = _parse_browser_search_context_facts(browser_search_context_extra)
    search_query = str(facts.get("search_query") or "that topic").strip()
    external_source_captured = str(facts.get("external_source_captured") or "").strip().lower() == "yes"
    source_url = str(facts.get("source_url") or "").strip()
    source_summary = str(facts.get("source_summary") or "").strip()
    source_excerpt = str(facts.get("source_excerpt") or "").strip()
    has_usable_source = bool(source_url or source_summary or source_excerpt) and source_summary.lower() != "unknown"
    if external_source_captured or has_usable_source:
        return (
            f"I did run a browser search for \"{search_query}\", but the captured source evidence still was not strong "
            "enough to support a confident live answer. Retry the search if you need an authoritative citation."
        )
    targeted_query = search_query if len(search_query.split()) >= 2 else f"{search_query} price today"
    return (
        f"I did run a browser search for \"{search_query}\", but it only yielded weak search-page evidence and no "
        "usable external source capture. I can't verify a live answer from that evidence yet.\n\n"
        f"Next: retry with a more specific query like \"{targeted_query}\" if you need an authoritative citation."
    )


def _should_collect_browser_search_context(user_message: str) -> bool:
    lowered = re.sub(r"\s+", " ", str(user_message or "").strip().lower())
    if not lowered:
        return False
    if _detect_memory_quality_evaluation_plan_query(lowered) or _detect_memory_source_explanation_query(lowered):
        return False
    domain_or_url_hint = re.search(
        r"(https?://\S+|www\.\S+|\b[a-z0-9-]+(?:\.[a-z0-9-]+)+\b)",
        lowered,
    )
    explicit_signals = (
        "search the web",
        "web search",
        "websearch",
        "look up",
        "browse for",
        "find online",
        "search online",
        "google ",
    )
    if any(signal in lowered for signal in explicit_signals):
        return True
    if domain_or_url_hint and any(
        signal in lowered
        for signal in ("browse ", "open ", "go to ", "visit ", "check out ")
    ):
        return True
    current_fact_signals = ("current", "latest", "today", "price", "news", "source")
    return any(signal in lowered for signal in current_fact_signals) and any(
        token in lowered for token in ("search", "look", "find", "source")
    )


def _detect_memory_source_explanation_query(user_message: str) -> bool:
    text = re.sub(r"\s+", " ", str(user_message or "").strip().lower())
    if not text:
        return False
    return "memory" in text and any(
        marker in text
        for marker in (
            "memory sources",
            "sources you used",
            "source you used",
            "what sources",
            "which sources",
            "explain what memory",
        )
    )


def _detect_memory_quality_evaluation_plan_query(user_message: str) -> bool:
    text = re.sub(r"\s+", " ", str(user_message or "").strip().lower())
    if not text:
        return False
    memory_quality_anchor = any(
        marker in text
        for marker in (
            "persistent memory quality",
            "memory quality",
            "natural recall",
            "stale context avoidance",
            "current-state priority",
            "current state priority",
        )
    )
    plan_anchor = any(
        marker in text
        for marker in (
            "evaluation plan",
            "test plan",
            "concrete plan",
            "what should we evaluate",
            "evaluate next",
            "quality metric",
            "benchmark",
        )
    )
    return memory_quality_anchor and plan_anchor


def _normalize_browser_search_query(user_message: str) -> str:
    query = str(user_message or "").strip()
    query = re.sub(
        r"^\s*(?:i\s+want\s+you\s+to|can\s+you|could\s+you|would\s+you|please)\s+",
        "",
        query,
        flags=re.IGNORECASE,
    )
    patterns = (
        r"^\s*(please\s+)?search (the )?web (for|and tell me)?\s+",
        r"^\s*(please\s+)?websearch\s+",
        r"^\s*(please\s+)?look up\s+",
        r"^\s*(please\s+)?find online\s+",
        r"^\s*(please\s+)?search online\s+",
        r"^\s*(please\s+)?browse\s+",
        r"^\s*(please\s+)?open\s+",
        r"^\s*(please\s+)?go to\s+",
        r"^\s*(please\s+)?visit\s+",
        r"^\s*(please\s+)?check out\s+",
    )
    for pattern in patterns:
        updated = re.sub(pattern, "", query, flags=re.IGNORECASE)
        if updated != query:
            query = updated
            break
    query = re.sub(r"^\s*tell me\s+", "", query, flags=re.IGNORECASE)
    query = re.sub(r"^\s*the\s+", "", query, flags=re.IGNORECASE)
    query = re.sub(r"\s+with the source you used\.?\s*$", "", query, flags=re.IGNORECASE)
    query = re.sub(r"\s+and tell me the source you used\.?\s*$", "", query, flags=re.IGNORECASE)
    query = re.sub(r"\s+tell me the source you used\.?\s*$", "", query, flags=re.IGNORECASE)
    query = re.sub(r"\s+(?:and\s+)?cite (?:the )?source(?:s)?(?: you used)?\.?\s*$", "", query, flags=re.IGNORECASE)
    query = re.sub(r"\s+(?:and\s+)?cite your source\.?\s*$", "", query, flags=re.IGNORECASE)
    query = re.sub(r"\s+(?:and\s+)?with sources?\.?\s*$", "", query, flags=re.IGNORECASE)
    query = re.sub(r"\s+and cite (the )?source(s)?\.?\s*$", "", query, flags=re.IGNORECASE)
    domain_or_url_match = re.search(
        r"(https?://\S+|www\.\S+|\b[a-z0-9-]+(?:\.[a-z0-9-]+)+\b)",
        query,
        flags=re.IGNORECASE,
    )
    if domain_or_url_match:
        query = domain_or_url_match.group(1)
    query = query.strip(" ?")
    return query or str(user_message or "").strip()


def _build_browser_search_url(query: str) -> str:
    return f"https://duckduckgo.com/?q={quote(query)}&ia=web"


def _resolve_direct_browser_target_url(user_message: str, query: str) -> str | None:
    lowered = str(user_message or "").strip().lower()
    if not any(
        signal in lowered
        for signal in ("browse ", "open ", "go to ", "visit ", "check out ")
    ):
        return None
    candidate = str(query or "").strip()
    if not candidate or any(char.isspace() for char in candidate):
        return None
    if re.match(r"^https?://", candidate, flags=re.IGNORECASE):
        return candidate
    if re.match(r"^(?:www\.)?[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+(?:/[^\s<>\"']*)?$", candidate):
        return f"https://{candidate}"
    return None


def _truncate_browser_evidence_text(text: str, *, max_chars: int) -> str:
    normalized = re.sub(r"\s+", " ", str(text or "").strip())
    if len(normalized) <= max_chars:
        return normalized
    return f"{normalized[: max_chars - 3].rstrip()}..."


_SEARCH_ENGINE_HOST_SUFFIXES = (
    "duckduckgo.com",
    "duck.ai",
    "google.com",
    "bing.com",
    "search.brave.com",
    "search.yahoo.com",
)

_BROWSER_SEARCH_QUERY_STOPWORDS = {
    "about",
    "cite",
    "current",
    "find",
    "for",
    "official",
    "page",
    "search",
    "source",
    "tell",
    "the",
    "used",
    "web",
}

_BROWSER_DIRECT_OPEN_MAX_TEXT_CHARACTERS = 1500


def _normalize_hostname(host: str) -> str:
    normalized = str(host or "").strip().lower().rstrip(".")
    if normalized.startswith("www."):
        normalized = normalized[4:]
    return normalized


def _is_search_engine_host(host: str) -> bool:
    normalized = _normalize_hostname(host)
    if not normalized:
        return False
    return any(
        normalized == suffix or normalized.endswith(f".{suffix}")
        for suffix in _SEARCH_ENGINE_HOST_SUFFIXES
    )


def _is_search_engine_url(url: str) -> bool:
    host = str(urlparse(str(url or "").strip()).hostname or "")
    return _is_search_engine_host(host)


def _execute_browser_hook(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    hook: str,
    payload: dict[str, Any],
    run_id: str | None,
    request_id: str,
    channel_kind: str,
    session_id: str,
    human_id: str,
    agent_id: str,
) -> tuple[dict[str, Any] | None, str | None]:
    try:
        execution = run_first_chip_hook_supporting(config_manager, hook=hook, payload=payload)
    except Exception:
        return None, None
    if not execution or not execution.ok:
        return None, None
    record_chip_hook_execution(
        state_db,
        execution=execution,
        component="researcher_bridge",
        actor_id="researcher_bridge",
        summary="Researcher bridge executed a browser chip hook before provider execution.",
        reason_code=f"browser_hook_{hook.replace('.', '_')}",
        keepability="ephemeral_context",
        run_id=run_id,
        request_id=request_id,
        channel_id=channel_kind,
        session_id=session_id,
        human_id=human_id,
        agent_id=agent_id,
    )
    output = execution.output if isinstance(execution.output, dict) else None
    return output, execution.chip_key


def _browser_hook_blocked_reply(output: dict[str, Any]) -> tuple[str | None, str | None]:
    error = output.get("error") if isinstance(output.get("error"), dict) else {}
    code = str(error.get("code") or "").strip()
    if code != "HOST_PERMISSION_REQUIRED":
        return None, code or None
    details = error.get("details") if isinstance(error.get("details"), dict) else {}
    origin = str(details.get("origin") or "").strip() or "the requested origin"
    return (
        (
            f"Web search is blocked because the browser extension does not have host access for {origin}. "
            f"Open the extension popup and grant explicit site access for {origin}, then retry the search."
        ),
        code,
    )


def _browser_hook_error_code(output: dict[str, Any] | None) -> str | None:
    if not isinstance(output, dict):
        return None
    error = output.get("error") if isinstance(output.get("error"), dict) else {}
    code = str(error.get("code") or "").strip()
    return code or None


def _browser_hook_succeeded(output: dict[str, Any] | None) -> bool:
    return isinstance(output, dict) and str(output.get("status") or "").strip().lower() == "succeeded"


def _is_transient_browser_session_error(code: str | None) -> bool:
    normalized = str(code or "").strip().upper()
    return normalized in {
        "BROWSER_SESSION_STALE",
        "BROWSER_SESSION_NOT_CONNECTED",
        "BROWSER_SESSION_TIMEOUT",
    }


def _resolve_external_search_result_href(href: str, *, search_host: str) -> str | None:
    candidate = str(href or "").strip()
    if not candidate:
        return None
    parsed = urlparse(candidate)
    parsed_host = _normalize_hostname(str(parsed.hostname or ""))
    normalized_search_host = _normalize_hostname(search_host)
    if parsed_host and (parsed_host == normalized_search_host or _is_search_engine_host(parsed_host)):
        redirected = parse_qs(parsed.query).get("uddg")
        if redirected:
            return str(redirected[0]).strip() or None
    if parsed.scheme in {"http", "https"} and parsed.netloc and not _is_search_engine_host(parsed_host):
        return candidate
    return None


def _normalize_search_result_candidate_href(raw_value: str, *, search_host: str) -> str | None:
    candidate = str(raw_value or "").strip().rstrip(".,);:]}>")
    if not candidate:
        return None
    if not re.match(r"^[a-z]+://", candidate, flags=re.IGNORECASE):
        if re.match(r"^(?:www\.)?[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?:/[^\s<>\"']*)?$", candidate):
            candidate = f"https://{candidate}"
        else:
            return None
    resolved = _resolve_external_search_result_href(candidate, search_host=search_host)
    if not resolved:
        return None
    host = _normalize_hostname(str(urlparse(resolved).hostname or ""))
    if not host or host == _normalize_hostname(search_host) or _is_search_engine_host(host):
        return None
    return resolved


def _browser_search_query_tokens(query: str) -> list[str]:
    tokens = re.findall(r"[A-Za-z0-9]+", str(query or "").lower())
    return [
        token
        for token in tokens
        if len(token) >= 4 and token not in _BROWSER_SEARCH_QUERY_STOPWORDS
    ]


def _score_search_result_candidate(*, search_query: str, href: str, text_summary: str) -> int:
    tokens = _browser_search_query_tokens(search_query)
    if not tokens:
        return 0
    haystack = f"{href} {text_summary}".lower()
    score = sum(1 for token in tokens if token in haystack)
    path = str(urlparse(href).path or "").strip("/")
    if path:
        score += 1
    return score


def _summarize_dom_outline_nodes(nodes: Any, *, max_items: int = 5) -> list[str]:
    if not isinstance(nodes, list):
        return []
    lines: list[str] = []
    for node in nodes:
        if not isinstance(node, dict):
            continue
        text = str(node.get("text_summary") or "").strip()
        href = str(node.get("href") or "").strip()
        if not text and not href:
            continue
        line = text or href
        if href:
            line = f"{line} | href={href}"
        lines.append(line)
        if len(lines) >= max_items:
            break
    return lines


def _summarize_interactives(items: Any, *, max_items: int = 5) -> list[str]:
    if not isinstance(items, list):
        return []
    lines: list[str] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        label = str(
            item.get("label") or item.get("text") or item.get("selector") or ""
        ).strip()
        href = str(item.get("href") or "").strip()
        if not label and not href:
            continue
        line = label or href
        if href:
            line = f"{line} | href={href}"
        lines.append(line)
        if len(lines) >= max_items:
            break
    return lines


def _select_search_result_candidate(
    dom_extract_output: dict[str, Any], *, search_url: str, search_query: str = ""
) -> dict[str, str] | None:
    result = dom_extract_output.get("result") if isinstance(dom_extract_output.get("result"), dict) else {}
    dom_outline = result.get("dom_outline") if isinstance(result.get("dom_outline"), dict) else {}
    search_host = _normalize_hostname(str(urlparse(search_url).hostname or ""))
    nodes = dom_outline.get("nodes") if isinstance(dom_outline.get("nodes"), list) else []
    best_candidate: dict[str, str] | None = None
    best_score = -1
    for node in nodes:
        if not isinstance(node, dict):
            continue
        href = _resolve_external_search_result_href(str(node.get("href") or ""), search_host=search_host)
        if not href:
            continue
        candidate_host = _normalize_hostname(str(urlparse(href).hostname or ""))
        if not candidate_host or candidate_host == search_host or _is_search_engine_host(candidate_host):
            continue
        candidate = {
            "href": href,
            "text_summary": str(node.get("text_summary") or "").strip(),
        }
        score = _score_search_result_candidate(
            search_query=search_query,
            href=candidate["href"],
            text_summary=candidate["text_summary"],
        )
        if score > best_score:
            best_candidate = candidate
            best_score = score
    return best_candidate


def _select_search_result_candidate_from_text_result(
    text_extract_output: dict[str, Any],
    *,
    search_url: str,
) -> dict[str, str] | None:
    result = text_extract_output.get("result") if isinstance(text_extract_output.get("result"), dict) else {}
    visible_text = result.get("visible_text") if isinstance(result.get("visible_text"), dict) else {}
    combined = "\n".join(
        part for part in (
            str(visible_text.get("summary") or "").strip(),
            str(visible_text.get("excerpt") or "").strip(),
        )
        if part
    )
    if not combined:
        return None
    search_host = _normalize_hostname(str(urlparse(search_url).hostname or ""))
    pattern = r"https?://[^\s<>\"']+|(?:www\.)?[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?:/[^\s<>\"']*)?"
    seen: set[str] = set()
    for match in re.finditer(pattern, combined):
        href = _normalize_search_result_candidate_href(match.group(0), search_host=search_host)
        if not href or href in seen:
            continue
        seen.add(href)
        return {"href": href, "text_summary": ""}
    return None


def _select_search_result_candidate_from_interactives_result(
    interactives_output: dict[str, Any],
    *,
    search_url: str,
    search_query: str = "",
) -> dict[str, str] | None:
    result = interactives_output.get("result") if isinstance(interactives_output.get("result"), dict) else {}
    interactives = result.get("interactives") if isinstance(result.get("interactives"), list) else []
    search_host = _normalize_hostname(str(urlparse(search_url).hostname or ""))
    seen: set[str] = set()
    best_candidate: dict[str, str] | None = None
    best_score = -1
    for item in interactives:
        if not isinstance(item, dict):
            continue
        href = _normalize_search_result_candidate_href(str(item.get("href") or ""), search_host=search_host)
        if not href or href in seen:
            continue
        seen.add(href)
        candidate = {
            "href": href,
            "text_summary": str(item.get("label") or item.get("text") or item.get("selector") or "").strip(),
        }
        score = _score_search_result_candidate(
            search_query=search_query,
            href=candidate["href"],
            text_summary=candidate["text_summary"],
        )
        if score > best_score:
            best_candidate = candidate
            best_score = score
    return best_candidate


def _select_primary_page_link_from_interactives_result(
    interactives_output: dict[str, Any],
) -> dict[str, str] | None:
    result = interactives_output.get("result") if isinstance(interactives_output.get("result"), dict) else {}
    interactives = result.get("interactives") if isinstance(result.get("interactives"), list) else []
    for item in interactives:
        if not isinstance(item, dict):
            continue
        href = str(item.get("href") or "").strip()
        parsed = urlparse(href)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            continue
        label = str(item.get("label") or item.get("text") or item.get("selector") or "").strip()
        return {"href": href, "label": label}
    return None


def _build_direct_browser_snapshot_context(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    direct_target_url: str,
    search_query: str,
    request_id: str,
    channel_kind: str,
    agent_id: str,
    human_id: str,
    session_id: str,
    run_id: str | None = None,
) -> dict[str, str | None]:
    direct_target_host = str(urlparse(direct_target_url).hostname or "").strip().lower()
    navigate_output, chip_key = _execute_browser_hook(
        config_manager=config_manager,
        state_db=state_db,
        hook="browser.navigate",
        payload=build_browser_navigate_payload(
            config_manager=config_manager,
            url=direct_target_url,
            agent_id=agent_id,
            request_id=f"{request_id}:browser-direct-snapshot-navigate",
        ),
        run_id=run_id,
        request_id=request_id,
        channel_kind=channel_kind,
        session_id=session_id,
        human_id=human_id,
        agent_id=agent_id,
    )
    blocked_reply, blocked_code = _browser_hook_blocked_reply(navigate_output or {})
    if blocked_reply:
        return {"context": "", "blocked_reply": blocked_reply, "blocked_code": blocked_code}
    if not _browser_hook_succeeded(navigate_output):
        return {"context": "", "blocked_reply": None, "blocked_code": None}
    navigate_result = navigate_output.get("result") if isinstance(navigate_output.get("result"), dict) else {}
    wait_hint = navigate_result.get("wait_hint") if isinstance(navigate_result.get("wait_hint"), dict) else {}
    wait_target = wait_hint.get("target") if isinstance(wait_hint.get("target"), dict) else {}
    wait_origin = str(wait_target.get("origin") or navigate_result.get("origin") or direct_target_url).strip()
    wait_tab_id = str(wait_target.get("tab_id") or ((navigate_result.get("tab") or {}).get("id") if isinstance(navigate_result.get("tab"), dict) else "") or "").strip()
    if not wait_tab_id:
        return {"context": "", "blocked_reply": None, "blocked_code": None}
    wait_output, _ = _execute_browser_hook(
        config_manager=config_manager,
        state_db=state_db,
        hook="browser.tab.wait",
        payload=build_browser_tab_wait_payload(
            config_manager=config_manager,
            origin=wait_origin,
            tab_id=wait_tab_id,
            agent_id=agent_id,
            request_id=f"{request_id}:browser-direct-snapshot-wait",
        ),
        run_id=run_id,
        request_id=request_id,
        channel_kind=channel_kind,
        session_id=session_id,
        human_id=human_id,
        agent_id=agent_id,
    )
    blocked_reply, blocked_code = _browser_hook_blocked_reply(wait_output or {})
    if blocked_reply:
        return {"context": "", "blocked_reply": blocked_reply, "blocked_code": blocked_code}
    snapshot_output, chip_key = _execute_browser_hook(
        config_manager=config_manager,
        state_db=state_db,
        hook="browser.page.snapshot",
        payload=build_browser_page_snapshot_payload(
            config_manager=config_manager,
            origin=wait_origin,
            tab_id=wait_tab_id,
            agent_id=agent_id,
            request_id=f"{request_id}:browser-direct-snapshot",
            max_text_characters=_BROWSER_DIRECT_OPEN_MAX_TEXT_CHARACTERS,
            max_controls=10,
            allowed_domains=[direct_target_host] if direct_target_host else None,
        ),
        run_id=run_id,
        request_id=request_id,
        channel_kind=channel_kind,
        session_id=session_id,
        human_id=human_id,
        agent_id=agent_id,
    )
    blocked_reply, blocked_code = _browser_hook_blocked_reply(snapshot_output or {})
    if blocked_reply:
        return {"context": "", "blocked_reply": blocked_reply, "blocked_code": blocked_code}
    if not _browser_hook_succeeded(snapshot_output):
        return {"context": "", "blocked_reply": None, "blocked_code": None}
    snapshot_result = snapshot_output.get("result") if isinstance(snapshot_output.get("result"), dict) else {}
    visible_text = snapshot_result.get("visible_text") if isinstance(snapshot_result.get("visible_text"), dict) else {}
    source_summary = _truncate_browser_evidence_text(
        str(visible_text.get("summary") or ""),
        max_chars=_BROWSER_SEARCH_SUMMARY_MAX_CHARS,
    )
    source_excerpt = _truncate_browser_evidence_text(
        str(visible_text.get("excerpt") or ""),
        max_chars=_BROWSER_SEARCH_EXCERPT_MAX_CHARS,
    )
    lines = [
        "[Browser search evidence]",
        "The user explicitly asked for web/current/source-backed information.",
        "Use this evidence as the factual basis for the reply instead of asking the user what to optimize for.",
        "Only cite the source_url field when it is present.",
        "Never cite search_url or a search-engine results page as the source.",
        "Do not invent names, counts, URLs, or facts that are not explicitly present in the captured source.",
        f"browser_chip_key={chip_key or 'unknown'}",
        "browser_mode=direct_open",
        f"search_query={search_query}",
        f"search_url={direct_target_url}",
        f"search_results_title={snapshot_result.get('title') or 'unknown'}",
        "external_source_captured=yes",
        "This is a direct page-open request, not a web-search results task.",
        "Answer only with the page facts the user asked for.",
        "Do not add strategy advice, jokes, speculative commentary, or unrelated follow-up questions.",
        "Keep the reply to 1 to 4 short factual lines.",
        "If the page excerpt is partial, say that directly instead of guessing.",
        f"source_url={direct_target_url}",
        f"source_title={snapshot_result.get('title') or 'unknown'}",
        f"source_origin={snapshot_result.get('origin') or direct_target_url}",
        f"source_summary={source_summary}",
        f"source_excerpt={source_excerpt}",
        "",
    ]
    return {
        "context": "\n".join(lines),
        "blocked_reply": None,
        "blocked_code": None,
        "source_url": direct_target_url,
    }


def _build_browser_search_context(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    user_message: str,
    request_id: str,
    channel_kind: str,
    agent_id: str,
    human_id: str,
    session_id: str,
    run_id: str | None = None,
) -> dict[str, str | None]:
    empty = {
        "context": "",
        "blocked_reply": None,
        "blocked_code": None,
    }
    if not _should_collect_browser_search_context(user_message):
        return empty

    session_unavailable = {
        "context": "",
        "blocked_reply": (
            "Web search is currently unavailable because the Spark Browser Extension live session "
            "is disconnected. Reload or reconnect the extension, then retry the search."
        ),
        "blocked_code": "BROWSER_SESSION_UNAVAILABLE",
    }

    search_query = _normalize_browser_search_query(user_message)
    direct_target_url = _resolve_direct_browser_target_url(user_message, search_query)
    search_url = direct_target_url or _build_browser_search_url(search_query)
    status_output, chip_key = _execute_browser_hook(
        config_manager=config_manager,
        state_db=state_db,
        hook="browser.status",
        payload=build_browser_status_payload(
            config_manager=config_manager,
            agent_id=agent_id,
            request_id=f"{request_id}:browser-status",
        ),
        run_id=run_id,
        request_id=request_id,
        channel_kind=channel_kind,
        session_id=session_id,
        human_id=human_id,
        agent_id=agent_id,
    )
    if not _browser_hook_succeeded(status_output):
        status_error_code = _browser_hook_error_code(status_output)
        if _is_transient_browser_session_error(status_error_code):
            time.sleep(1.0)
            retried_status_output, retried_chip_key = _execute_browser_hook(
                config_manager=config_manager,
                state_db=state_db,
                hook="browser.status",
                payload=build_browser_status_payload(
                    config_manager=config_manager,
                    agent_id=agent_id,
                    request_id=f"{request_id}:browser-status-retry",
                ),
                run_id=run_id,
                request_id=request_id,
                channel_kind=channel_kind,
                session_id=session_id,
                human_id=human_id,
                agent_id=agent_id,
            )
            if retried_status_output:
                status_output = retried_status_output
                chip_key = retried_chip_key
    if not status_output:
        return session_unavailable
    blocked_reply, blocked_code = _browser_hook_blocked_reply(status_output)
    if blocked_reply:
        return {"context": "", "blocked_reply": blocked_reply, "blocked_code": blocked_code}
    if not _browser_hook_succeeded(status_output):
        return session_unavailable
    status_result = status_output.get("result") if isinstance(status_output.get("result"), dict) else {}
    extension = status_result.get("extension") if isinstance(status_result.get("extension"), dict) else {}
    if not bool(extension.get("running")):
        return session_unavailable

    navigate_output, chip_key = _execute_browser_hook(
        config_manager=config_manager,
        state_db=state_db,
        hook="browser.navigate",
        payload=build_browser_navigate_payload(
            config_manager=config_manager,
            url=search_url,
            agent_id=agent_id,
            request_id=f"{request_id}:browser-search-navigate",
        ),
        run_id=run_id,
        request_id=request_id,
        channel_kind=channel_kind,
        session_id=session_id,
        human_id=human_id,
        agent_id=agent_id,
    )
    if not navigate_output:
        if direct_target_url:
            return _build_direct_browser_snapshot_context(
                config_manager=config_manager,
                state_db=state_db,
                direct_target_url=direct_target_url,
                search_query=search_query,
                request_id=request_id,
                channel_kind=channel_kind,
                agent_id=agent_id,
                human_id=human_id,
                session_id=session_id,
                run_id=run_id,
            )
        return empty
    blocked_reply, blocked_code = _browser_hook_blocked_reply(navigate_output)
    if blocked_reply:
        return {"context": "", "blocked_reply": blocked_reply, "blocked_code": blocked_code}
    if not _browser_hook_succeeded(navigate_output):
        if direct_target_url:
            return _build_direct_browser_snapshot_context(
                config_manager=config_manager,
                state_db=state_db,
                direct_target_url=direct_target_url,
                search_query=search_query,
                request_id=request_id,
                channel_kind=channel_kind,
                agent_id=agent_id,
                human_id=human_id,
                session_id=session_id,
                run_id=run_id,
            )
        return empty
    navigate_result = navigate_output.get("result") if isinstance(navigate_output.get("result"), dict) else {}
    wait_hint = navigate_result.get("wait_hint") if isinstance(navigate_result.get("wait_hint"), dict) else {}
    wait_target = wait_hint.get("target") if isinstance(wait_hint.get("target"), dict) else {}
    search_origin = str(wait_target.get("origin") or navigate_result.get("origin") or search_url).strip()
    search_tab_id = str(wait_target.get("tab_id") or ((navigate_result.get("tab") or {}).get("id") if isinstance(navigate_result.get("tab"), dict) else "") or "").strip()
    if not search_tab_id:
        if direct_target_url:
            return _build_direct_browser_snapshot_context(
                config_manager=config_manager,
                state_db=state_db,
                direct_target_url=direct_target_url,
                search_query=search_query,
                request_id=request_id,
                channel_kind=channel_kind,
                agent_id=agent_id,
                human_id=human_id,
                session_id=session_id,
                run_id=run_id,
            )
        return empty

    wait_output, _ = _execute_browser_hook(
        config_manager=config_manager,
        state_db=state_db,
        hook="browser.tab.wait",
        payload=build_browser_tab_wait_payload(
            config_manager=config_manager,
            origin=search_origin,
            tab_id=search_tab_id,
            agent_id=agent_id,
            request_id=f"{request_id}:browser-search-wait",
        ),
        run_id=run_id,
        request_id=request_id,
        channel_kind=channel_kind,
        session_id=session_id,
        human_id=human_id,
        agent_id=agent_id,
    )
    blocked_reply, blocked_code = _browser_hook_blocked_reply(wait_output or {})
    if blocked_reply:
        return {"context": "", "blocked_reply": blocked_reply, "blocked_code": blocked_code}

    dom_result: dict[str, Any] = {}
    search_nodes: list[str] = []
    page_outline: list[str] = []
    page_interactives: list[str] = []
    candidate: dict[str, str] | None = None
    primary_page_link: dict[str, str] | None = None
    search_text_output = None
    browser_mode = "search_results"
    if direct_target_url:
        browser_mode = "direct_open"
        source_url = direct_target_url
        candidate = {
            "href": direct_target_url,
            "text_summary": "Direct page requested by the user",
        }
        dom_output, chip_key = _execute_browser_hook(
            config_manager=config_manager,
            state_db=state_db,
            hook="browser.page.dom_extract",
            payload=build_browser_page_dom_extract_payload(
                config_manager=config_manager,
                origin=search_origin,
                tab_id=search_tab_id,
                agent_id=agent_id,
                request_id=f"{request_id}:browser-direct-dom",
            ),
            run_id=run_id,
            request_id=request_id,
            channel_kind=channel_kind,
            session_id=session_id,
            human_id=human_id,
            agent_id=agent_id,
        )
        blocked_reply, blocked_code = _browser_hook_blocked_reply(dom_output or {})
        if blocked_reply:
            return {"context": "", "blocked_reply": blocked_reply, "blocked_code": blocked_code}
        if _browser_hook_succeeded(dom_output):
            dom_result = dom_output.get("result") if isinstance(dom_output.get("result"), dict) else {}
            page_outline = _summarize_dom_outline_nodes((dom_result.get("dom_outline") or {}).get("nodes"))
        interactives_output, _ = _execute_browser_hook(
            config_manager=config_manager,
            state_db=state_db,
            hook="browser.page.interactives.list",
            payload=build_browser_page_interactives_list_payload(
                config_manager=config_manager,
                origin=search_origin,
                tab_id=search_tab_id,
                agent_id=agent_id,
                request_id=f"{request_id}:browser-direct-interactives",
                max_items=10,
            ),
            run_id=run_id,
            request_id=request_id,
            channel_kind=channel_kind,
            session_id=session_id,
            human_id=human_id,
            agent_id=agent_id,
        )
        blocked_reply, blocked_code = _browser_hook_blocked_reply(interactives_output or {})
        if blocked_reply:
            return {"context": "", "blocked_reply": blocked_reply, "blocked_code": blocked_code}
        if _browser_hook_succeeded(interactives_output):
            interactives_result = interactives_output.get("result") if isinstance(interactives_output.get("result"), dict) else {}
            page_interactives = _summarize_interactives(interactives_result.get("interactives"))
            primary_page_link = _select_primary_page_link_from_interactives_result(
                interactives_output
            )
    else:
        dom_output, chip_key = _execute_browser_hook(
            config_manager=config_manager,
            state_db=state_db,
            hook="browser.page.dom_extract",
            payload=build_browser_page_dom_extract_payload(
                config_manager=config_manager,
                origin=search_origin,
                tab_id=search_tab_id,
                agent_id=agent_id,
                request_id=f"{request_id}:browser-search-dom",
            ),
            run_id=run_id,
            request_id=request_id,
            channel_kind=channel_kind,
            session_id=session_id,
            human_id=human_id,
            agent_id=agent_id,
        )
        if not dom_output:
            return empty
        blocked_reply, blocked_code = _browser_hook_blocked_reply(dom_output)
        if blocked_reply:
            return {"context": "", "blocked_reply": blocked_reply, "blocked_code": blocked_code}
        if not _browser_hook_succeeded(dom_output):
            return empty
        dom_result = dom_output.get("result") if isinstance(dom_output.get("result"), dict) else {}
        search_nodes = _summarize_dom_outline_nodes((dom_result.get("dom_outline") or {}).get("nodes"))
        candidate = _select_search_result_candidate(
            dom_output,
            search_url=search_url,
            search_query=search_query,
        )
        if not candidate:
            interactives_output, _ = _execute_browser_hook(
                config_manager=config_manager,
                state_db=state_db,
                hook="browser.page.interactives.list",
                payload=build_browser_page_interactives_list_payload(
                    config_manager=config_manager,
                    origin=search_origin,
                    tab_id=search_tab_id,
                    agent_id=agent_id,
                    request_id=f"{request_id}:browser-search-interactives",
                    max_items=10,
                ),
                run_id=run_id,
                request_id=request_id,
                channel_kind=channel_kind,
                session_id=session_id,
                human_id=human_id,
                agent_id=agent_id,
            )
            blocked_reply, blocked_code = _browser_hook_blocked_reply(interactives_output or {})
            if blocked_reply:
                return {"context": "", "blocked_reply": blocked_reply, "blocked_code": blocked_code}
            if _browser_hook_succeeded(interactives_output):
                candidate = _select_search_result_candidate_from_interactives_result(
                    interactives_output,
                    search_url=search_url,
                    search_query=search_query,
                )
        if not candidate:
            search_text_output, _ = _execute_browser_hook(
                config_manager=config_manager,
                state_db=state_db,
                hook="browser.page.text_extract",
                payload=build_browser_page_text_extract_payload(
                    config_manager=config_manager,
                    origin=search_origin,
                    tab_id=search_tab_id,
                    agent_id=agent_id,
                    request_id=f"{request_id}:browser-search-text",
                    max_text_characters=900,
                    max_controls=6,
                ),
                run_id=run_id,
                request_id=request_id,
                channel_kind=channel_kind,
                session_id=session_id,
                human_id=human_id,
                agent_id=agent_id,
            )
            blocked_reply, blocked_code = _browser_hook_blocked_reply(search_text_output or {})
            if blocked_reply:
                return {"context": "", "blocked_reply": blocked_reply, "blocked_code": blocked_code}
            if _browser_hook_succeeded(search_text_output):
                candidate = _select_search_result_candidate_from_text_result(
                    search_text_output,
                    search_url=search_url,
                )

        source_url = candidate["href"] if candidate else search_url
    source_origin = search_origin
    source_tab_id = search_tab_id
    source_navigate_output = None
    if source_url != search_url:
        source_navigate_output, _ = _execute_browser_hook(
            config_manager=config_manager,
            state_db=state_db,
            hook="browser.navigate",
            payload=build_browser_navigate_payload(
                config_manager=config_manager,
                url=source_url,
                agent_id=agent_id,
                request_id=f"{request_id}:browser-source-navigate",
            ),
            run_id=run_id,
            request_id=request_id,
            channel_kind=channel_kind,
            session_id=session_id,
            human_id=human_id,
            agent_id=agent_id,
        )
    if source_navigate_output:
        source_navigate_result = (
            source_navigate_output.get("result")
            if isinstance(source_navigate_output.get("result"), dict)
            else {}
        )
        source_wait_hint = (
            source_navigate_result.get("wait_hint")
            if isinstance(source_navigate_result.get("wait_hint"), dict)
            else {}
        )
        source_wait_target = source_wait_hint.get("target") if isinstance(source_wait_hint.get("target"), dict) else {}
        source_origin = str(source_wait_target.get("origin") or source_navigate_result.get("origin") or source_url).strip()
        source_tab_id = str(source_wait_target.get("tab_id") or ((source_navigate_result.get("tab") or {}).get("id") if isinstance(source_navigate_result.get("tab"), dict) else "") or source_tab_id).strip()
        if source_tab_id:
            wait_output, _ = _execute_browser_hook(
                config_manager=config_manager,
                state_db=state_db,
                hook="browser.tab.wait",
                payload=build_browser_tab_wait_payload(
                    config_manager=config_manager,
                    origin=source_origin,
                    tab_id=source_tab_id,
                    agent_id=agent_id,
                    request_id=f"{request_id}:browser-source-wait",
                ),
                run_id=run_id,
                request_id=request_id,
                channel_kind=channel_kind,
                session_id=session_id,
                human_id=human_id,
                agent_id=agent_id,
            )
            blocked_reply, blocked_code = _browser_hook_blocked_reply(wait_output or {})
            if blocked_reply:
                return {"context": "", "blocked_reply": blocked_reply, "blocked_code": blocked_code}

    text_output = search_text_output if source_url == search_url and search_text_output else None
    if text_output is None:
        text_output, _ = _execute_browser_hook(
            config_manager=config_manager,
            state_db=state_db,
            hook="browser.page.text_extract",
            payload=build_browser_page_text_extract_payload(
                config_manager=config_manager,
                origin=source_origin,
                tab_id=source_tab_id or None,
                agent_id=agent_id,
                request_id=f"{request_id}:browser-source-text",
                max_text_characters=_BROWSER_DIRECT_OPEN_MAX_TEXT_CHARACTERS if browser_mode == "direct_open" else 900,
                max_controls=6,
            ),
            run_id=run_id,
            request_id=request_id,
            channel_kind=channel_kind,
            session_id=session_id,
            human_id=human_id,
            agent_id=agent_id,
        )
    snapshot_output = None
    if text_output:
        blocked_reply, blocked_code = _browser_hook_blocked_reply(text_output)
        if blocked_reply:
            return {"context": "", "blocked_reply": blocked_reply, "blocked_code": blocked_code}
    if not text_output or not _browser_hook_succeeded(text_output):
        snapshot_output, _ = _execute_browser_hook(
            config_manager=config_manager,
            state_db=state_db,
            hook="browser.page.snapshot",
            payload=build_browser_page_snapshot_payload(
                config_manager=config_manager,
                origin=source_origin,
                tab_id=source_tab_id or None,
                agent_id=agent_id,
                request_id=f"{request_id}:browser-source-snapshot",
                max_text_characters=_BROWSER_DIRECT_OPEN_MAX_TEXT_CHARACTERS if browser_mode == "direct_open" else 900,
                max_controls=10,
                allowed_domains=[str(urlparse(source_url).hostname or "").strip().lower()] if source_url and str(urlparse(source_url).hostname or "").strip() else None,
            ),
            run_id=run_id,
            request_id=request_id,
            channel_kind=channel_kind,
            session_id=session_id,
            human_id=human_id,
            agent_id=agent_id,
        )
        blocked_reply, blocked_code = _browser_hook_blocked_reply(snapshot_output or {})
        if blocked_reply:
            return {"context": "", "blocked_reply": blocked_reply, "blocked_code": blocked_code}
        if not _browser_hook_succeeded(snapshot_output):
            if direct_target_url:
                return _build_direct_browser_snapshot_context(
                    config_manager=config_manager,
                    state_db=state_db,
                    direct_target_url=direct_target_url,
                    search_query=search_query,
                    request_id=request_id,
                    channel_kind=channel_kind,
                    agent_id=agent_id,
                    human_id=human_id,
                    session_id=session_id,
                    run_id=run_id,
                )
            return empty
        snapshot_result = snapshot_output.get("result") if isinstance(snapshot_output.get("result"), dict) else {}
        text_result = {
            "title": snapshot_result.get("title"),
            "origin": snapshot_result.get("origin"),
            "visible_text": snapshot_result.get("visible_text"),
        }
    else:
        text_result = text_output.get("result") if isinstance(text_output.get("result"), dict) else {}
    visible_text = text_result.get("visible_text") if isinstance(text_result.get("visible_text"), dict) else {}
    search_results_title = (
        dom_result.get("title")
        or text_result.get("title")
        or "unknown"
    )
    external_source_url = source_url if source_url and not _is_search_engine_url(source_url) else None

    lines = [
        "[Browser search evidence]",
        "The user explicitly asked for web/current/source-backed information.",
        "Use this evidence as the factual basis for the reply instead of asking the user what to optimize for.",
        "Only cite the source_url field when it is present.",
        "Never cite search_url or a search-engine results page as the source.",
        "Do not invent names, counts, URLs, or facts that are not explicitly present in the captured source.",
        f"browser_chip_key={chip_key or 'unknown'}",
        f"browser_mode={browser_mode}",
        f"search_query={search_query}",
        f"search_url={search_url}",
        f"search_results_title={search_results_title}",
        f"external_source_captured={'yes' if external_source_url else 'no'}",
    ]
    if browser_mode == "direct_open":
        lines.extend(
            [
                "This is a direct page-open request, not a web-search results task.",
                "Answer only with the page facts the user asked for.",
                "Do not add strategy advice, jokes, speculative commentary, or unrelated follow-up questions.",
                "If the user asks for the main link on the page, use primary_link_href when it is present.",
                "Keep the reply to 1 to 4 short factual lines.",
                "If the page excerpt is partial, say that directly instead of guessing.",
            ]
        )
    if search_nodes:
        lines.append("search_result_candidates=" + json.dumps(search_nodes, ensure_ascii=True))
    if page_outline:
        lines.append("page_outline=" + json.dumps(page_outline, ensure_ascii=True))
    if page_interactives:
        lines.append("page_interactives=" + json.dumps(page_interactives, ensure_ascii=True))
    if candidate:
        lines.append(f"selected_result_hint={candidate.get('text_summary') or 'unknown'}")
    if primary_page_link:
        lines.append(f"primary_link_label={primary_page_link.get('label') or 'unknown'}")
        lines.append(f"primary_link_href={primary_page_link.get('href') or 'unknown'}")
    source_summary = _truncate_browser_evidence_text(
        str(visible_text.get("summary") or ""),
        max_chars=_BROWSER_SEARCH_SUMMARY_MAX_CHARS,
    )
    source_excerpt = _truncate_browser_evidence_text(
        str(visible_text.get("excerpt") or ""),
        max_chars=_BROWSER_SEARCH_EXCERPT_MAX_CHARS,
    )
    lines.extend(
        [
            *( [f"source_url={external_source_url}"] if external_source_url else ["source_capture_status=external_source_missing"] ),
            f"source_title={text_result.get('title') or 'unknown'}",
            f"source_origin={text_result.get('origin') or source_origin}",
            f"source_summary={source_summary}",
            f"source_excerpt={source_excerpt}",
            "",
        ]
    )
    return {
        "context": "\n".join(lines),
        "blocked_reply": None,
        "blocked_code": None,
        "source_url": external_source_url,
    }


def _is_startup_operator_chip(active_chip_evaluate: dict[str, Any] | None) -> bool:
    if not isinstance(active_chip_evaluate, dict):
        return False
    return str(active_chip_evaluate.get("chip_key") or "").strip().lower() == "startup-yc"


def _browser_block_metadata(blocked_code: str | None) -> tuple[str, str, str]:
    code = str(blocked_code or "").strip().upper()
    if code == "HOST_PERMISSION_REQUIRED":
        return (
            "browser_permission_required",
            "grant_origin_access",
            "Browser search blocked by missing host permission.",
        )
    if code == "BROWSER_SESSION_UNAVAILABLE":
        return (
            "browser_unavailable",
            "reconnect_browser_session",
            "Browser search unavailable because the live browser session is disconnected.",
        )
    return (
        "browser_unavailable",
        "reconnect_browser_session",
        "Browser search is currently unavailable.",
    )


def _startup_operator_reply_contract() -> str:
    return (
        "When the active chip is startup-yc, answer like a decisive startup operator. "
        "Answer the user's actual question in the first sentence. "
        "If they ask what to focus on, which segment to choose, or whether to drop something, "
        "make a provisional recommendation instead of only listing considerations. "
        "Then give 2 to 4 concrete actions for this week. "
        "Do not invent numbers, cohort sizes, revenue, timelines, retention windows, or interview counts. "
        "If the user supplied a count, you may use it; otherwise prefer plain-language quantities like "
        "'talk to a few' or 'talk to all of them' only when the total is known. "
        "Avoid numeric ranges like 3-5 or 2-3. "
        "If evidence is thin, say what missing fact would change the call, but still give your current best recommendation. "
        "Keep the reply in plain text with no markdown headings, no bold emphasis, and no memo framing."
    )


def _maybe_apply_swarm_recommendation(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    user_message: str,
    channel_kind: str,
    reply_text: str,
    evidence_summary: str,
    routing_decision: str | None,
    run_id: str | None = None,
    request_id: str | None = None,
    trace_ref: str | None = None,
    session_id: str | None = None,
    human_id: str | None = None,
    agent_id: str | None = None,
) -> tuple[str, str, str | None, str | None]:
    try:
        from spark_intelligence.swarm_bridge import evaluate_swarm_escalation
    except Exception:  # pragma: no cover - defensive import guard
        return reply_text, evidence_summary, None, routing_decision

    decision = evaluate_swarm_escalation(
        config_manager=config_manager,
        state_db=state_db,
        task=user_message,
        run_id=run_id,
        request_id=request_id,
        trace_ref=trace_ref,
        channel_id=channel_kind,
        session_id=session_id,
        human_id=human_id,
        agent_id=agent_id,
        actor_id="researcher_bridge",
    )
    if not decision.escalate or decision.mode != "manual_recommended":
        return reply_text, evidence_summary, None, routing_decision

    triggers = ", ".join(decision.triggers) if decision.triggers else "explicit request"
    escalation_hint = decision.mode
    next_line = (
        "Swarm: recommended for this task because it asks for delegation or multi-agent work "
        f"({triggers})."
    )
    if channel_kind == "telegram":
        reply_text = f"{reply_text}\n\n{next_line}"
    evidence_summary = f"{evidence_summary} swarm={decision.mode}"
    if routing_decision:
        routing_decision = f"{routing_decision}+{decision.mode}"
    else:
        routing_decision = decision.mode
    return reply_text, evidence_summary, escalation_hint, routing_decision


def _build_contextual_task(
    *,
    user_message: str,
    channel_kind: str | None = None,
    attachment_context: dict[str, object],
    active_chip_evaluate: dict[str, Any] | None = None,
    personality_profile: dict[str, Any] | None = None,
    personality_context_extra: str = "",
    browser_search_context_extra: str = "",
    recent_conversation_context: str = "",
    context_capsule: str = "",
    system_registry_context: str = "",
    mission_control_context: str = "",
    capability_router_context: str = "",
    harness_context: str = "",
    user_instructions_context: str = "",
    iteration_draft_context: str = "",
) -> str:
    active_chip_keys = attachment_context.get("active_chip_keys") or []
    pinned_chip_keys = attachment_context.get("pinned_chip_keys") or []
    attached_chip_keys = attachment_context.get("attached_chip_keys") or []
    attached_path_keys = attachment_context.get("attached_path_keys") or []
    active_path_key = attachment_context.get("active_path_key") or None
    lines = [
        "[Spark Intelligence context]",
        f"active_chip_keys={','.join(str(item) for item in active_chip_keys) if active_chip_keys else 'none'}",
        f"pinned_chip_keys={','.join(str(item) for item in pinned_chip_keys) if pinned_chip_keys else 'none'}",
        f"attached_chip_keys={','.join(str(item) for item in attached_chip_keys) if attached_chip_keys else 'none'}",
        f"attached_path_keys={','.join(str(item) for item in attached_path_keys) if attached_path_keys else 'none'}",
        f"active_path_key={active_path_key or 'none'}",
        "",
        "[Context source contract]",
        "- Current-state facts from Spark Context Capsule or [CURRENT STATE] are the authority for the user's saved focus, plan, blocker, status, and preferences.",
        "- Latest diagnostics summary from Spark Context Capsule is the authority for the most recent scan counts and clean/failure status.",
        "- Clean diagnostics or successful maintenance do not by themselves resolve an active focus, plan, or blocker; only explicit completion/closure evidence should do that.",
        "- When asked what to verify next for an active focus, distinguish green system evidence from user-level closure; say what is verified, what remains open, and ask or suggest the next validation instead of declaring the focus complete.",
        "- When asked whether context survived across a new turn, answer as a context-survival check: list the current focus, current plan, latest diagnostics status, and maintenance summary from the capsule, then state whether each survived without being collapsed into done.",
        "- For context-survival checks, do not substitute older diagnostics handoff tasks, stale to-do lists, or Codex mission proposals unless the user explicitly asks for next implementation work.",
        "- When the user asks what is verified, still open, or only they should close, and active current-state facts exist, answer against the active focus/plan first; do not pivot to older missions, apps, or workflow residue unless the user names them.",
        "- Mission, Spawner, Swarm, chip, and older conversation context are advisory unless the user specifically asks about those systems.",
        "- If sources conflict, say which source is newer or more authoritative and answer from that source.",
        "- Do not invent unavailable slash commands such as /recall. If context is present here, use it directly.",
        "",
    ]
    if _detect_memory_quality_evaluation_plan_query(user_message) or (
        "current_focus: persistent memory quality evaluation" in str(context_capsule or "")
        and any(
            marker in str(user_message or "").lower()
            for marker in ("evaluation plan", "concrete plan", "evaluate next", "quality")
        )
    ):
        lines.extend(
            [
                "[Active focus guard]",
                "- The active focus is persistent memory quality evaluation. Answer the user's request as a memory-quality evaluation plan.",
                "- Do not reuse the old diagnostics integration checklist unless the user explicitly asks for diagnostics work.",
                "- Cover natural recall, stale-context avoidance, current-state priority, and source explanation.",
                "- Diagnostics and maintenance counts are background evidence only; they are not the next plan.",
                "",
            ]
        )
    spark_self_knowledge_context = _build_spark_self_knowledge_context(
        user_message=user_message,
        attachment_context=attachment_context,
    )
    if spark_self_knowledge_context:
        lines.extend([spark_self_knowledge_context, ""])
    if context_capsule:
        lines.extend([sanitize_prompt_boundary_text(context_capsule), ""])
    if system_registry_context:
        lines.extend([sanitize_prompt_boundary_text(system_registry_context), ""])
    if mission_control_context:
        lines.extend([sanitize_prompt_boundary_text(mission_control_context), ""])
    if capability_router_context:
        lines.extend([sanitize_prompt_boundary_text(capability_router_context), ""])
    if harness_context:
        lines.extend([sanitize_prompt_boundary_text(harness_context), ""])
    if personality_profile:
        personality_ctx = build_personality_context(personality_profile)
        if personality_ctx:
            lines.extend([personality_ctx, ""])
        if channel_kind == "telegram":
            telegram_persona_contract = build_telegram_persona_reply_contract(personality_profile)
            if telegram_persona_contract:
                lines.extend(["[Telegram reply contract]", telegram_persona_contract, ""])
    if personality_context_extra:
        lines.extend([sanitize_prompt_boundary_text(personality_context_extra), ""])
    if recent_conversation_context:
        lines.extend([sanitize_prompt_boundary_text(recent_conversation_context), ""])
    attachment_inventory_context = _build_attachment_inventory_context(attachment_context=attachment_context)
    if attachment_inventory_context:
        lines.extend([
            "[GROUND TRUTH — runtime inventory below overrides any conflicting claim from earlier in this conversation]",
            "If you previously told the user a chip was unwired, not invokable, missing connectors, or stub-only,",
            "and the inventory below contradicts that, the inventory is correct and your prior claim was wrong.",
            "Quote from the inventory verbatim if asked, do not paraphrase from memory.",
            attachment_inventory_context,
            "",
        ])
    if user_instructions_context:
        lines.extend([sanitize_prompt_boundary_text(user_instructions_context), ""])
    if browser_search_context_extra:
        lines.extend([sanitize_prompt_boundary_text(browser_search_context_extra), ""])
    if iteration_draft_context:
        lines.extend([sanitize_prompt_boundary_text(iteration_draft_context), ""])
    if active_chip_evaluate:
        chip_guidance = _summarize_active_chip_guidance(str(active_chip_evaluate.get("analysis") or ""))
        lines.extend(
            [
                "[Active chip guidance]",
                f"chip_key={active_chip_evaluate.get('chip_key') or 'unknown'}",
                f"task_type={active_chip_evaluate.get('task_type') or 'unknown'}",
                f"stage={active_chip_evaluate.get('stage') or 'unknown'}",
                (
                    "GROUND-TRUTH RULE for chip output: "
                    "You may paraphrase the chip's prose explanation freely, "
                    "BUT any verdict, score, percentage, or confidence value MUST be quoted EXACTLY as the chip emitted it. "
                    "Never round, re-estimate, average, or substitute your own assessment for the chip's numeric scores. "
                    "If the chip says verdict=reject confidence=50%, you say verdict=reject confidence=50% — "
                    "even if your prose interpretation suggests a more favourable read. "
                    "Do not copy headings, packet ids, or memo formatting structure — those are paraphrasable."
                ),
            ]
        )
        if active_chip_evaluate.get("stage_transition_suggested"):
            lines.append(
                f"possible_stage_transition={active_chip_evaluate['stage_transition_suggested']} "
                "(confirm with the user before treating it as true)"
            )
        if active_chip_evaluate.get("detected_state_updates"):
            lines.append(
                "possible_state_updates="
                + json.dumps(active_chip_evaluate["detected_state_updates"], sort_keys=True)
                + " (confirm with the user before treating them as true)"
            )
        if chip_guidance:
            lines.extend(["", chip_guidance, ""])
    lines.extend(
        [
        "[User message]",
        sanitize_prompt_boundary_text(user_message),
        ]
    )
    return "\n".join(lines)


def _build_iteration_draft_context(
    *,
    state_db: StateDB,
    human_id: str,
    channel_kind: str,
    user_message: str,
) -> str:
    external_user_id = ""
    raw = str(human_id or "").strip()
    if raw:
        external_user_id = raw.split(":")[-1].strip()
    if not external_user_id or not channel_kind:
        return ""
    try:
        draft = find_draft_for_iteration(
            state_db,
            external_user_id=external_user_id,
            channel_kind=channel_kind,
            user_message=user_message,
        )
    except Exception:
        return ""
    if draft is None:
        return ""
    body = draft.content
    if len(body) > 6000:
        body = body[:6000].rstrip() + "\n…[truncated for context, full draft is in storage]"
    return "\n".join([
        f"[GROUND TRUTH OVERRIDE — draft to iterate, handle {draft.handle}, created {draft.created_at}]",
        "This block overrides any earlier topic, hook, or artifact mentioned in conversation history.",
        "If recent conversation history references a different draft (e.g. an earlier 3am-founder thread,",
        "an unrelated test article, or any prior topic), IGNORE THAT. The user is iterating THIS exact draft.",
        "Apply chip recommendations and your edits ONLY to the text between BEGIN DRAFT / END DRAFT.",
        "Do NOT switch to a different topic. Do NOT invent fresh subject matter. Do NOT generate a new artifact.",
        "Your reply should be a revised version of the draft below, with the chip's recommendations applied.",
        f"Topic of this draft: {draft.topic_hint or '(no topic captured)'}",
        f"Chip used originally: {draft.chip_used or '(none)'}",
        "--- BEGIN DRAFT ---",
        body,
        "--- END DRAFT ---",
    ])


def _build_user_instructions_context(
    *,
    state_db: StateDB,
    human_id: str,
    channel_kind: str,
) -> str:
    external_user_id = ""
    raw = str(human_id or "").strip()
    if raw:
        external_user_id = raw.split(":")[-1].strip()
    if not external_user_id or not channel_kind:
        return ""
    try:
        instructions = list_active_instructions(
            state_db,
            external_user_id=external_user_id,
            channel_kind=channel_kind,
            limit=20,
        )
    except Exception:
        return ""
    if not instructions:
        return ""
    lines = [
        "[Persistent user instructions — saved by the user, must be honoured]",
        "These are durable instructions this user has explicitly given. Treat them as standing rules.",
        "If a current request conflicts with one, follow the instruction unless the user is overriding it now.",
    ]
    for inst in instructions:
        lines.append(f"- {inst.instruction_text}")
    return "\n".join(lines)


def _build_attachment_inventory_context(*, attachment_context: dict[str, object]) -> str:
    chip_records = attachment_context.get("attached_chip_records") or []
    if not isinstance(chip_records, list) or not chip_records:
        return ""
    lines = [
        "[Attached chip inventory]",
        "Note: Chips marked router_invokable=yes are wired through the live chip router.",
        "When an incoming user message matches a chip's task_topics/task_keywords,",
        "the router calls that chip's evaluate hook BEFORE generating your reply, and",
        "the chip's analysis is injected into your prompt. So you should describe",
        "router_invokable chips as actively callable — not as inventory-only stubs.",
    ]

    def _priority(rec: dict) -> int:
        if not isinstance(rec, dict):
            return 9
        if rec.get("router_invokable"):
            return 0
        mode = str(rec.get("attachment_mode") or "").strip()
        if mode == "pinned":
            return 1
        if mode == "active":
            return 2
        return 3

    sorted_records = sorted(
        (r for r in chip_records if isinstance(r, dict)),
        key=lambda r: (_priority(r), str(r.get("key") or "")),
    )
    for record in sorted_records[:_ATTACHMENT_PROMPT_CHIP_LIMIT]:
        if not isinstance(record, dict):
            continue
        key = str(record.get("key") or "").strip()
        if not key:
            continue
        attachment_mode = str(record.get("attachment_mode") or "available").strip() or "available"
        hook_names = [str(item) for item in (record.get("hook_names") or []) if str(item)]
        description = str(record.get("description") or "").strip() or _KNOWN_CHIP_ROLE_HINTS.get(key, "")
        topics = [str(item) for item in (record.get("task_topics") or []) if str(item)]
        invokable = bool(record.get("router_invokable"))
        line = f"- {key} mode={attachment_mode} router_invokable={'yes' if invokable else 'no'}"
        if hook_names:
            line += f" hooks={','.join(hook_names[:8])}"
        if topics:
            line += f" topics={','.join(topics[:6])}"
        if description:
            line += f" :: {description}"
        lines.append(line)
    return "\n".join(lines) if len(lines) > 1 else ""


def _looks_like_spark_self_knowledge_query(lowered_message: str) -> bool:
    return looks_like_system_registry_query(lowered_message)


def _build_spark_self_knowledge_context(
    *,
    user_message: str,
    attachment_context: dict[str, object],
) -> str:
    lowered_message = str(user_message or "").strip().lower()
    if not lowered_message or not _looks_like_spark_self_knowledge_query(lowered_message):
        return ""

    active_chip_keys = [str(item) for item in (attachment_context.get("active_chip_keys") or []) if str(item)]
    pinned_chip_keys = [str(item) for item in (attachment_context.get("pinned_chip_keys") or []) if str(item)]
    attached_chip_keys = [str(item) for item in (attachment_context.get("attached_chip_keys") or []) if str(item)]
    attached_path_keys = [str(item) for item in (attachment_context.get("attached_path_keys") or []) if str(item)]
    active_path_key = str(attachment_context.get("active_path_key") or "").strip()

    lines = ["[Spark self-knowledge]"]
    lines.append(f"- active_chips: {', '.join(active_chip_keys) if active_chip_keys else 'none'}")
    lines.append(f"- pinned_chips: {', '.join(pinned_chip_keys) if pinned_chip_keys else 'none'}")
    lines.append(f"- attached_chips: {', '.join(attached_chip_keys) if attached_chip_keys else 'none'}")
    lines.append(f"- active_path: {active_path_key or 'none'}")
    if attached_path_keys:
        lines.append(f"- attached_paths: {', '.join(attached_path_keys)}")
    lines.append(
        "- Treat this as Spark-owned runtime context about currently attached tools and paths, not as user memory."
    )
    return "\n".join(lines)


def _load_recent_active_chip_keys(
    *,
    state_db: StateDB,
    session_id: str,
    channel_kind: str,
    request_id: str | None,
    turn_limit: int = 4,
) -> list[str]:
    if not session_id or not channel_kind or turn_limit <= 0:
        return []
    keys: list[str] = []
    seen: set[str] = set()
    try:
        with state_db.connect() as conn:
            rows = conn.execute(
                """
                SELECT request_id, facts_json
                FROM builder_events
                WHERE component = 'researcher_bridge'
                  AND channel_id = ?
                  AND session_id = ?
                  AND reason_code = 'active_chip_evaluate'
                ORDER BY created_at DESC, rowid DESC
                LIMIT ?
                """,
                (channel_kind, session_id, turn_limit * 4),
            ).fetchall()
    except Exception:
        return []
    for row in rows:
        if request_id and str(row["request_id"] or "") == request_id:
            continue
        try:
            facts = json.loads(row["facts_json"] or "{}")
        except Exception:
            continue
        chip_key_raw = str(facts.get("chip_key") or "").strip()
        if not chip_key_raw:
            continue
        for chip_key in chip_key_raw.split(","):
            chip_key = chip_key.strip()
            if chip_key and chip_key not in seen:
                seen.add(chip_key)
                keys.append(chip_key)
        if len(keys) >= turn_limit:
            break
    return keys[:turn_limit]


def _load_recent_conversation_context(
    *,
    state_db: StateDB,
    session_id: str,
    channel_kind: str,
    request_id: str | None,
    turn_limit: int = _RECENT_CONVERSATION_TURN_LIMIT,
) -> str:
    if not session_id or not channel_kind or turn_limit <= 0:
        return ""

    transcript: list[tuple[str, str]] = []
    with state_db.connect() as conn:
        rows = conn.execute(
            """
            SELECT event_type, request_id, facts_json
            FROM builder_events
            WHERE component = 'telegram_runtime'
              AND channel_id = ?
              AND session_id = ?
              AND (
                    event_type = 'intent_committed'
                 OR (event_type = 'delivery_succeeded' AND reason_code = 'telegram_bridge_outbound')
              )
            ORDER BY created_at DESC, rowid DESC
            LIMIT ?
            """,
            (channel_kind, session_id, max(turn_limit * 4, 12)),
        ).fetchall()

    for row in reversed(rows):
        if request_id and str(row["request_id"] or "") == request_id:
            continue
        try:
            facts = json.loads(row["facts_json"] or "{}")
        except json.JSONDecodeError:
            facts = {}
        event_type = str(row["event_type"] or "")
        if event_type == "intent_committed":
            message_text = str(facts.get("message_text") or "").strip()
            if message_text:
                transcript.append(("user", message_text))
        elif event_type == "delivery_succeeded":
            delivered_text = str(facts.get("delivered_text") or "").strip()
            if delivered_text:
                transcript.append(("assistant", delivered_text))

    if not transcript:
        return ""

    recent_turns = transcript[-(turn_limit * 2) :]
    lines = ["[Recent conversation]"]
    for role, text in recent_turns:
        lines.append(f"{role}: {text}")
    visible_turn_labels = (
        "latest_visible_turn",
        "previous_visible_turn",
        "turn_before_previous_visible_turn",
    )
    for index, label in enumerate(visible_turn_labels, start=1):
        if len(recent_turns) >= index:
            role, text = recent_turns[-index]
            lines.append(f"{label}.role={role}")
            lines.append(f"{label}.text={text}")
    user_turns = [text for role, text in recent_turns if role == "user"]
    user_turn_labels = (
        "latest_user_message",
        "previous_user_message",
        "user_message_before_previous",
    )
    for index, label in enumerate(user_turn_labels, start=1):
        if len(user_turns) >= index:
            lines.append(f"{label}={user_turns[-index]}")
    return "\n".join(lines)


def _summarize_active_chip_guidance(analysis: str, *, max_lines: int = 4, max_chars: int = 700) -> str:
    cleaned_lines: list[str] = []
    for raw_line in analysis.splitlines():
        line = raw_line.strip()
        if not line or line == "---":
            continue
        line = re.sub(r"^#+\s*", "", line)
        line = re.sub(r"^\*\*(.*?)\*\*$", r"\1", line)
        line = re.sub(r"^[-*]\s*", "", line)
        lowered = line.lower().rstrip(":")
        if lowered in {"primary focus", "why this works", "what changes this", "next step"}:
            continue
        if lowered.startswith(("confidence:", "evidence gap:", "note:")):
            continue
        if _is_operational_residue_line(line):
            continue
        if lowered.startswith("recommendation:") or lowered.startswith("revised:"):
            _, _, remainder = line.partition(":")
            line = remainder.strip()
            if not line:
                continue
        cleaned_lines.append(line)
        if len(cleaned_lines) >= max_lines:
            break
    summary = "\n".join(cleaned_lines).strip()
    if len(summary) > max_chars:
        summary = f"{summary[: max_chars - 3].rstrip()}..."
    return summary


def _clean_messaging_reply(text: str, *, channel_kind: str) -> str:
    cleaned, _ = _clean_messaging_reply_with_metadata(text, channel_kind=channel_kind)
    return cleaned


def _contains_search_engine_url(text: str) -> bool:
    for match in re.finditer(r"https?://[^\s<>()]+", str(text or "")):
        if _is_search_engine_url(match.group(0).rstrip(".,);:]>}")):
            return True
    return False


def _prefer_more_specific_same_host_source_url(text: str, source_url: str | None) -> str | None:
    candidate_source = str(source_url or "").strip()
    if not candidate_source:
        return None
    parsed_source = urlparse(candidate_source)
    source_host = _normalize_hostname(str(parsed_source.hostname or ""))
    source_path = str(parsed_source.path or "").strip("/")
    if not source_host or source_path:
        return candidate_source
    best_url = candidate_source
    best_path_length = -1
    for match in re.finditer(r"https?://[^\s<>()]+", str(text or "")):
        url = match.group(0).rstrip(".,);:]>}")
        parsed = urlparse(url)
        host = _normalize_hostname(str(parsed.hostname or ""))
        path = str(parsed.path or "").strip("/")
        if host != source_host or not path:
            continue
        if len(path) > best_path_length:
            best_url = url
            best_path_length = len(path)
    return best_url


def _sanitize_browser_search_reply(
    text: str,
    *,
    source_url: str | None,
) -> tuple[str, list[str]]:
    mutation_actions: list[str] = []
    sanitized = str(text or "").replace("\r\n", "\n")
    sanitized = re.sub(r"(?is)\[TOOL_CALL\].*?\[/TOOL_CALL\]", "", sanitized).strip()
    stripped_search_markup = re.sub(r"(?is)<search>.*?</search>", "", sanitized)
    stripped_search_markup = re.sub(r"(?im)^\s*</?(?:search|query)>\s*$", "", stripped_search_markup)
    stripped_search_markup = re.sub(r"(?im)^\s*<query>.*?</query>\s*$", "", stripped_search_markup)
    if stripped_search_markup != sanitized:
        sanitized = stripped_search_markup
        mutation_actions.append("strip_internal_search_markup")
    cleaned_lines: list[str] = []
    for raw_line in sanitized.split("\n"):
        line = raw_line.strip()
        lowered = line.lower()
        if line and (lowered.startswith("source:") or lowered.startswith("sources:") or lowered.startswith("duckduckgo:")):
            if _contains_search_engine_url(line):
                mutation_actions.append("strip_search_engine_citation")
                continue
        cleaned_lines.append(raw_line)
    sanitized = "\n".join(cleaned_lines)
    sanitized = re.sub(r"\n{3,}", "\n\n", sanitized).strip()
    process_residue = sanitized
    process_residue = re.sub(r"(?is)\n\nI used a DuckDuckGo search.*$", "", process_residue).strip()
    process_residue = re.sub(r"(?is)\n\nThat is the authoritative upstream.*$", "", process_residue).strip()
    process_residue = re.sub(r"(?is)\n\nAlso\s*[–-]\s*to answer your earlier question.*$", "", process_residue).strip()
    process_residue = re.sub(r"(?is)\n\nThe user asked me to give sources using the `source_url` field.*$", "", process_residue).strip()
    process_residue = re.sub(r"(?is)\n\nWhat were you thinking in terms of setup\?.*$", "", process_residue).strip()
    if process_residue != sanitized:
        sanitized = process_residue
        mutation_actions.append("strip_browser_process_residue")
    effective_source_url = _prefer_more_specific_same_host_source_url(sanitized, source_url)
    if source_url and not _is_search_engine_url(source_url):
        if effective_source_url and effective_source_url != source_url:
            source_url = effective_source_url
            mutation_actions.append("promote_specific_same_host_source_url")
        has_explicit_source_line = any(
            line.strip().lower().startswith("source:")
            for line in sanitized.split("\n")
        )
        if not has_explicit_source_line:
            sanitized = f"{sanitized}\n\nSource: {source_url}" if sanitized else f"Source: {source_url}"
            mutation_actions.append("append_external_source_citation")
        sanitized, polish_actions = _polish_browser_grounded_reply(sanitized)
        mutation_actions.extend(polish_actions)
        return sanitized, mutation_actions
    if _contains_search_engine_url(sanitized):
        sanitized = re.sub(r"https?://[^\s<>()]+", "", sanitized)
        sanitized = re.sub(r"\(\s*\)", "", sanitized)
        sanitized = re.sub(r"\n{3,}", "\n\n", sanitized).strip()
        mutation_actions.append("strip_inline_search_engine_url")
    weak_capture_reply = _rewrite_weak_browser_source_capture_reply(sanitized)
    if weak_capture_reply != sanitized:
        sanitized = weak_capture_reply
        mutation_actions.append("rewrite_weak_source_capture_reply")
    warning = "Source capture failed on the result page, so retry the search if you need an authoritative citation."
    if warning not in sanitized:
        sanitized = f"{sanitized}\n\n{warning}" if sanitized else warning
        mutation_actions.append("append_source_capture_warning")
    sanitized, polish_actions = _polish_browser_grounded_reply(sanitized)
    mutation_actions.extend(polish_actions)
    return sanitized, mutation_actions


def _rewrite_weak_browser_source_capture_reply(text: str) -> str:
    original = str(text or "").strip()
    if not original:
        return original
    normalized = re.sub(r"\s+", " ", original).strip().lower()
    weak_capture_markers = (
        "can't pull actual content from that search",
        "cannot pull actual content from that search",
        "external source came back empty",
        "external source came back missing",
        "external source capture came back empty",
        "capture returned empty results",
        "no live excerpt or page content to pull from",
        "returned no extractable data",
        "can't cite meaningful information",
        "cannot cite meaningful information",
    )
    weak_capture_patterns = (
        r"\bi (?:don't|do not) have .* content from the search\b",
        r"\bi ran the search .* but the actual source content (?:didn't|did not) come through\b",
        r"\bthe search was attempted but the actual content from the source couldn't be captured\b",
        r"\bsource content (?:wasn't|was not) captured\b",
        r"\bsource (?:couldn't|could not) be captured\b",
        r"\bsearch was performed but the source content (?:wasn't|was not) captured\b",
        r"\bactual (?:page )?content (?:wasn't|was not) captured\b",
        r"\bactual source content (?:didn't|did not) come through\b",
    )
    if not any(marker in normalized for marker in weak_capture_markers) and not any(
        re.search(pattern, normalized) for pattern in weak_capture_patterns
    ):
        return original
    return (
        "Web search ran, but source capture failed on the result page.\n"
        "Reason: the search result page did not yield usable external content to cite.\n"
        "Next: retry with a more specific query or open a stronger source page."
    )


def _polish_browser_grounded_reply(text: str) -> tuple[str, list[str]]:
    body_lines: list[str] = []
    source_lines: list[str] = []
    actions: list[str] = []
    quote_spacing_fixed = False
    for raw_line in str(text or "").replace("\r\n", "\n").split("\n"):
        line = raw_line.strip()
        if not line:
            body_lines.append("")
            continue
        if line.lower().startswith("source:"):
            source_lines.append(line)
            continue
        fixed = raw_line
        fixed = re.sub(r'([A-Za-z0-9])(["”])([A-Za-z])', r"\1\2 \3", fixed)
        if fixed != raw_line:
            quote_spacing_fixed = True
        body_lines.append(fixed.strip())
    body = "\n".join(body_lines)
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    if quote_spacing_fixed:
        actions.append("repair_quote_spacing")
    generic_followup_patterns = (
        r"(?is)\n\nWhat problem are you trying to solve with this\?\s*$",
        r"(?is)\n\nWhat problem are you trying to solve\?\s*$",
        r"(?is)\n\nWhat are you trying to solve with this\?\s*$",
        r"(?is)\n\nWhat's the actual project context you want to organize around\?.*$",
        r"(?is)\n\nWhat is the actual project context you want to organize around\?.*$",
        r"(?is)\n\nOn your earlier question about tools.*$",
        r"(?is)\n\nWhat were you actually trying to get done\?.*$",
        r"(?is)\n\nWhat were you thinking in terms of setup\?.*$",
    )
    stripped_followup = body
    for pattern in generic_followup_patterns:
        stripped_followup = re.sub(pattern, "", stripped_followup).strip()
    if stripped_followup != body:
        body = stripped_followup
        actions.append("strip_generic_followup_question")
    parts = [part for part in (body, "\n".join(source_lines).strip()) if part]
    polished = "\n\n".join(parts).strip()
    return polished, actions


def _strip_reasoning_blocks(text: str) -> tuple[str, bool]:
    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    stripped = re.sub(r"(?is)<think>.*?</think>", "", normalized)
    stripped = re.sub(r"\n{3,}", "\n\n", stripped).strip()
    return stripped, stripped != normalized


def _normalize_dash_punctuation(text: str) -> str:
    if not text:
        return text
    return (
        text.replace("\u2014", "-")
        .replace("\u2013", "-")
        .replace("\u2015", "-")
        .replace("\u2012", "-")
    )


def _clean_messaging_reply_with_metadata(text: str, *, channel_kind: str) -> tuple[str, list[str]]:
    text = _normalize_dash_punctuation(text)
    visible_text, _ = _strip_reasoning_blocks(text)
    if channel_kind != "telegram":
        cleaned, removed_lines = _strip_operational_residue_lines(visible_text)
        return (cleaned if cleaned or not visible_text else visible_text), removed_lines
    rewritten = _rewrite_structured_telegram_reply(visible_text)
    if rewritten:
        visible_text = rewritten
    cleaned_lines: list[str] = []
    for raw_line in visible_text.split("\n"):
        line = raw_line.strip()
        if not line or line == "---":
            if cleaned_lines and cleaned_lines[-1] != "":
                cleaned_lines.append("")
            continue
        normalized = re.sub(r"^#+\s*", "", line)
        normalized = re.sub(r"^\*\*(.*?)\*\*$", r"\1", normalized)
        normalized_for_meta = re.sub(r"^[-*]\s*", "", normalized)
        lowered = normalized_for_meta.lower().rstrip(":")
        if lowered in {"primary focus", "why this works", "what changes this", "next step"}:
            continue
        if lowered.startswith(("confidence:", "evidence gap:", "note: advisory")):
            continue
        if lowered.startswith("recommendation:") or lowered.startswith("revised:"):
            _, _, remainder = normalized_for_meta.partition(":")
            normalized = remainder.strip()
            if not normalized:
                continue
        cleaned_lines.append(normalized)
    reply = "\n".join(cleaned_lines)
    reply = re.sub(r"\n{3,}", "\n\n", reply).strip()
    reply = _strip_internal_reply_prefixes(reply)
    reply = _strip_inline_markdown_emphasis(reply)
    sanitized, removed_lines = _strip_operational_residue_lines(reply or visible_text)
    return (sanitized if sanitized or not visible_text else visible_text), removed_lines


def _strip_operational_residue_lines(text: str) -> tuple[str, list[str]]:
    kept_lines: list[str] = []
    removed_lines: list[str] = []
    for raw_line in text.replace("\r\n", "\n").split("\n"):
        line = raw_line.strip()
        if line and _is_operational_residue_line(line):
            removed_lines.append(line)
            continue
        kept_lines.append(raw_line)
    cleaned = "\n".join(kept_lines)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned, removed_lines


def _is_operational_residue_line(line: str) -> bool:
    lowered = re.sub(r"^[-*]\s*", "", line.strip()).lower()
    explicit_prefixes = (
        "trace_ref:",
        "trace_path:",
        "trace_id:",
        "selected_packet_ids",
        "packet_refs:",
        "memory_refs:",
        "followup_actions:",
        "quarantine_id:",
        "runtime_root:",
        "config_path:",
        "recorded_at:",
        "epistemic_status:",
    )
    if lowered.startswith(explicit_prefixes):
        return True
    if "trace:" in lowered and any(token in lowered for token in ("trace", "research_trace_path", "trace_ref", "trace_path")):
        return True
    if any(token in lowered for token in ("packet_refs", "memory_refs", "selected_packet_ids", "followup_actions", "quarantine_id")):
        return True
    return False


def _rewrite_structured_telegram_reply(text: str) -> str | None:
    sections: dict[str, list[str]] = {
        "recommendation": [],
        "primary": [],
        "why": [],
        "changes": [],
        "next": [],
        "body": [],
    }
    current_section = "body"
    saw_structured_markers = False

    for raw_line in text.replace("\r\n", "\n").split("\n"):
        line = raw_line.strip()
        if not line or line == "---":
            continue
        normalized = re.sub(r"^#+\s*", "", line)
        normalized = re.sub(r"^\*\*(.*?)\*\*\s*:\s*", r"\1: ", normalized)
        normalized = re.sub(r"^\*\*(.*?)\*\*$", r"\1", normalized)
        normalized_for_meta = re.sub(r"^[-*]\s*", "", normalized).strip()
        lowered = normalized_for_meta.lower().rstrip(":")

        if lowered in {"primary focus", "why this works", "what changes this", "next step"}:
            saw_structured_markers = True
            current_section = {
                "primary focus": "primary",
                "why this works": "why",
                "what changes this": "changes",
                "next step": "next",
            }[lowered]
            continue
        if lowered.startswith(("confidence:", "evidence gap:", "note:")):
            saw_structured_markers = True
            continue
        if lowered.startswith("revised:"):
            saw_structured_markers = True
            continue
        if lowered.startswith("recommendation:"):
            saw_structured_markers = True
            _, _, remainder = normalized_for_meta.partition(":")
            candidate = remainder.strip()
            if candidate:
                sections["recommendation"].append(candidate)
            continue
        sections[current_section].append(normalized_for_meta)

    if not saw_structured_markers:
        return None

    lead = _first_distinct_line(
        sections["recommendation"] + sections["primary"] + sections["body"] + sections["why"] + sections["changes"]
    )
    if not lead:
        return None

    parts = [_ensure_terminal_punctuation(lead)]
    support_lines = _distinct_lines(
        sections["primary"] + sections["why"] + sections["changes"] + sections["body"],
        exclude={lead},
    )
    if support_lines:
        parts.append(" ".join(_ensure_terminal_punctuation(item) for item in support_lines[:2]))
    next_step = _first_distinct_line(sections["next"])
    if next_step:
        parts.append(f"Next: {_strip_terminal_punctuation(next_step)}.")
    return "\n\n".join(part for part in parts if part).strip() or None


def _strip_internal_reply_prefixes(text: str) -> str:
    patterns = (
        r"^based on (?:the )?(?:research|researcher) notes(?: provided)?[,:\-]\s*",
        r"^according to (?:the )?(?:research|researcher) notes[,:\-]\s*",
        r"^from (?:the )?(?:research|researcher) notes[,:\-]\s*",
        r"^the (?:research|researcher) notes (?:show|suggest|indicate) that\s*",
    )
    cleaned = text.strip()
    for pattern in patterns:
        cleaned = re.sub(pattern, "", cleaned, flags=re.IGNORECASE).strip()
    return cleaned


def _strip_inline_markdown_emphasis(text: str) -> str:
    cleaned = re.sub(r"\*\*(.*?)\*\*", r"\1", str(text or ""))
    cleaned = re.sub(r"__(.*?)__", r"\1", cleaned)
    return cleaned.strip()


def _first_distinct_line(lines: list[str]) -> str | None:
    for line in lines:
        candidate = line.strip()
        if candidate:
            return candidate
    return None


def _distinct_lines(lines: list[str], *, exclude: set[str] | None = None) -> list[str]:
    excluded = {item.strip().lower() for item in (exclude or set()) if item.strip()}
    seen: set[str] = set()
    output: list[str] = []
    for line in lines:
        candidate = line.strip()
        if not candidate:
            continue
        normalized = candidate.lower()
        if normalized in seen or normalized in excluded:
            continue
        seen.add(normalized)
        output.append(candidate)
    return output


def _ensure_terminal_punctuation(text: str) -> str:
    value = text.strip()
    if not value:
        return value
    if value[-1] in ".!?":
        return value
    return f"{value}."


def _strip_terminal_punctuation(text: str) -> str:
    return text.strip().rstrip(".!?")


def _run_active_chip_evaluate(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    request_id: str,
    channel_kind: str,
    agent_id: str,
    human_id: str,
    session_id: str,
    user_message: str,
    conversation_history: str,
    attachment_context: dict[str, object],
    run_id: str | None = None,
) -> dict[str, Any] | None:
    payload = {
        "situation": user_message,
        "conversation_history": conversation_history,
        "channel_kind": channel_kind,
        "request_id": request_id,
        "agent_id": agent_id,
        "human_id": human_id,
        "session_id": session_id,
        "attachment_context": attachment_context,
    }
    try:
        active_records = list_active_chip_records(config_manager)
    except Exception:
        active_records = []

    recent_chip_keys = _load_recent_active_chip_keys(
        state_db=state_db,
        session_id=session_id,
        channel_kind=channel_kind,
        request_id=request_id,
    )
    decision = select_chips_for_message(
        user_message,
        active_records,
        conversation_history=conversation_history,
        recent_active_chip_keys=recent_chip_keys,
    )
    if not decision.selected:
        return None

    selected_keys = [s.chip_key for s in decision.selected]
    selected_records = [r for r in active_records if r.key in selected_keys and "evaluate" in r.commands]
    if not selected_records:
        return None

    analyses: list[str] = []
    primary_result: dict[str, Any] = {}
    primary_chip_key: str | None = None
    merged_context_packet_ids: list[Any] = []
    merged_activations: list[Any] = []
    merged_state_updates: list[Any] = []
    chips_used: list[str] = []
    quarantined_keys: list[str] = []
    last_quarantine_id: str | None = None
    raw_metrics_per_chip: list[dict[str, Any]] = []

    for record in selected_records:
        try:
            execution = run_chip_hook(config_manager, chip_key=record.key, hook="evaluate", payload=payload)
        except Exception:
            continue
        if not execution or not execution.ok:
            continue
        record_chip_hook_execution(
            state_db,
            execution=execution,
            component="researcher_bridge",
            actor_id="researcher_bridge",
            summary="Researcher bridge executed an active chip hook before bridge execution.",
            reason_code="active_chip_evaluate",
            keepability="ephemeral_context",
            run_id=run_id,
            request_id=request_id,
            channel_id=channel_kind,
            session_id=session_id,
            human_id=human_id,
            agent_id=agent_id,
        )
        result = execution.output.get("result") if isinstance(execution.output, dict) else None
        if not isinstance(result, dict):
            continue
        analysis = str(result.get("analysis") or "").strip()
        if not analysis:
            continue
        screened = screen_chip_hook_text(
            state_db=state_db,
            execution=execution,
            text=analysis,
            summary="Chip guidance was quarantined before model-visible prompt assembly.",
            reason_code="chip_guidance_secret_like",
            policy_domain="researcher_bridge",
            blocked_stage="pre_model",
            run_id=run_id,
            request_id=request_id,
            trace_ref=f"trace:{agent_id}:{human_id}:{request_id}",
        )
        if not screened["allowed"]:
            quarantined_keys.append(record.key)
            last_quarantine_id = screened.get("quarantine_id")
            continue
        analyses.append(f"[{record.key}] {analysis}")
        chips_used.append(record.key)
        if not primary_chip_key:
            primary_chip_key = record.key
            primary_result = result
        merged_context_packet_ids.extend(result.get("context_packet_ids") or [])
        merged_activations.extend(result.get("activations") or [])
        merged_state_updates.extend(result.get("detected_state_updates") or [])
        chip_metrics_payload = execution.output.get("metrics") if isinstance(execution.output, dict) else None
        raw_metrics_per_chip.append({
            "chip_key": record.key,
            "verdict": result.get("verdict"),
            "verdict_confidence": result.get("verdict_confidence"),
            "metrics": chip_metrics_payload if isinstance(chip_metrics_payload, dict) else {},
            "claim": result.get("claim"),
            "recommended_next_step": result.get("recommended_next_step"),
        })

    if not analyses:
        if quarantined_keys:
            return {
                "chip_key": ",".join(quarantined_keys),
                "analysis": "",
                "task_type": None,
                "stage": None,
                "context_packet_ids": [],
                "activations": [],
                "detected_state_updates": [],
                "stage_transition_suggested": None,
                "quarantined": True,
                "quarantine_id": last_quarantine_id,
                "router_decision": decision.to_dict(),
            }
        return None

    return {
        "chip_key": ",".join(chips_used),
        "analysis": "\n\n".join(analyses),
        "task_type": primary_result.get("task_type"),
        "stage": primary_result.get("stage"),
        "context_packet_ids": merged_context_packet_ids,
        "activations": merged_activations,
        "detected_state_updates": merged_state_updates,
        "stage_transition_suggested": primary_result.get("stage_transition_suggested"),
        "router_decision": decision.to_dict(),
        "raw_chip_metrics": raw_metrics_per_chip,
    }


def _bridge_output_classification(*, mode: str, routing_decision: str | None) -> tuple[str, str]:
    operator_debug_modes = {"blocked", "disabled", "bridge_error", "stub", "context_source_debug"}
    operator_debug_decisions = {
        "bridge_disabled",
        "context_source_debug",
        "provider_resolution_failed",
        "bridge_error",
        "secret_boundary_blocked",
        "stub",
    }
    if mode in operator_debug_modes or routing_decision in operator_debug_decisions:
        return ("operator_debug_only", "not_promotable")
    return ("ephemeral_context", "not_promotable")


def _append_direct_reply_section(reply_text: str, appendix: str) -> str:
    base = str(reply_text or "").strip()
    extra = str(appendix or "").strip()
    if not extra:
        return base
    if not base:
        return extra
    return f"{base}\n\n{extra}"


def _build_self_knowledge_wiki_appendix(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    user_message: str,
    actor_id: str,
) -> tuple[str, str]:
    try:
        bootstrap_llm_wiki(config_manager=config_manager)
        compile_result = compile_system_wiki(config_manager=config_manager, state_db=state_db)
        wiki_context = hybrid_memory_retrieve(
            config_manager=config_manager,
            state_db=state_db,
            query=str(user_message or "Spark self-awareness and system capability map").strip(),
            limit=3,
            actor_id=actor_id,
            source_surface="self_knowledge_wiki_context",
            record_activity=False,
        )
    except Exception as exc:
        return (
            "LLM wiki\n- refresh: unavailable this turn\n- reason: wiki_refresh_failed\n- next probe: run `spark-intelligence wiki compile-system --json`",
            f"wiki_refresh=failed error={exc.__class__.__name__}",
        )
    context_packet = wiki_context.context_packet
    wiki_lane = next(
        (
            lane
            for lane in wiki_context.lane_summaries
            if isinstance(lane, dict) and str(lane.get("lane") or "") == "wiki_packets"
        ),
        {},
    )
    section_names = [
        str(section.get("section") or "")
        for section in context_packet.sections
        if isinstance(section, dict) and str(section.get("section") or "").strip()
    ]
    source_paths: list[str] = []
    for section in context_packet.sections:
        if not isinstance(section, dict):
            continue
        for item in list(section.get("items") or []):
            if not isinstance(item, dict):
                continue
            source_path = str(item.get("source_path") or "").strip()
            if source_path and source_path not in source_paths:
                source_paths.append(source_path)
    lines = [
        "LLM wiki",
        f"- refresh: generated {len(compile_result.generated_files)} live system pages",
        f"- retrieval: {wiki_lane.get('status') or 'unknown'} ({wiki_lane.get('record_count') or 0} wiki hits)",
        f"- context: {', '.join(section_names) if section_names else 'none'}",
        "- authority: supporting_not_authoritative",
    ]
    if source_paths:
        lines.append("- source pages:")
        lines.extend(f"  - {path}" for path in source_paths[:3])
    evidence = (
        f"wiki_refresh=ok wiki_records={wiki_lane.get('record_count') or 0} "
        f"wiki_pages={len(compile_result.generated_files)} "
        f"project_knowledge_first={(context_packet.trace or {}).get('project_knowledge_first')}"
    )
    return "\n".join(lines), evidence


def _bridge_event_facts(
    *,
    routing_decision: str | None,
    bridge_mode: str | None,
    evidence_summary: str | None = None,
    runtime_root: str | None = None,
    config_path: str | None = None,
    provider_id: str | None = None,
    provider_auth_method: str | None = None,
    provider_model: str | None = None,
    provider_model_family: str | None = None,
    provider_execution_transport: str | None = None,
    provider_base_url: str | None = None,
    provider_source: str | None = None,
    active_chip_key: str | None = None,
    active_chip_task_type: str | None = None,
    active_chip_evaluate_used: bool | None = None,
    raw_chip_metrics: list[dict[str, Any]] | None = None,
    keepability: str | None = None,
    promotion_disposition: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    facts: dict[str, Any] = {
        "routing_decision": routing_decision,
        "bridge_mode": bridge_mode,
        "evidence_summary": evidence_summary,
        "runtime_root": runtime_root,
        "config_path": config_path,
        "provider_id": provider_id,
        "provider_auth_method": provider_auth_method,
        "provider_model": provider_model,
        "provider_model_family": provider_model_family,
        "provider_execution_transport": provider_execution_transport,
        "provider_base_url": provider_base_url,
        "provider_source": provider_source,
        "active_chip_key": active_chip_key,
        "active_chip_task_type": active_chip_task_type,
        "active_chip_evaluate_used": active_chip_evaluate_used,
        "keepability": keepability,
        "promotion_disposition": promotion_disposition,
    }
    if extra:
        facts.update(extra)
    return facts


def _detect_context_source_debug_query(user_message: str) -> bool:
    text = re.sub(r"\s+", " ", str(user_message or "").strip().lower())
    if not text:
        return False
    if re.search(r"\bwhy\s+did\s+you\s+(answer|say|reply|respond)\s+(that|this)\b", text):
        return True
    if re.search(r"\bwhy\s+was\s+that\s+your\s+(answer|reply|response)\b", text):
        return True
    source_markers = (
        "what sources did you use",
        "what context did you use",
        "where did that answer come from",
        "why did you answer like that",
        "explain your context",
        "explain the source",
        "show me the source ledger",
        "show the source ledger",
    )
    return any(marker in text for marker in source_markers)


def _format_context_source_ledger_reply(
    *,
    ledger: list[dict[str, Any]],
    source_counts: dict[str, Any] | None,
    explained_request_id: str | None,
    route_facts: dict[str, Any] | None = None,
) -> str:
    normalized: list[dict[str, Any]] = []
    counts = source_counts or {}
    for item in ledger:
        if not isinstance(item, dict):
            continue
        source = str(item.get("source") or "").strip()
        if not source:
            continue
        role = str(item.get("role") or "").strip() or "unknown"
        count = item.get("count")
        if count is None and source in counts:
            count = counts.get(source)
        try:
            count_value = int(count or 0)
        except (TypeError, ValueError):
            count_value = 0
        try:
            priority = int(item.get("priority") or 999)
        except (TypeError, ValueError):
            priority = 999
        normalized.append(
            {
                "source": source,
                "role": role,
                "count": count_value,
                "priority": priority,
            }
        )
    normalized.sort(key=lambda item: (item["priority"], item["source"]))
    authority = [item for item in normalized if item["role"] == "authority"]
    supporting = [item for item in normalized if item["role"] != "authority"]

    def render(items: list[dict[str, Any]]) -> list[str]:
        return [
            f"- {item['source']}: {item['role']}, {item['count']} items"
            for item in items
        ] or ["- none"]

    route_facts = route_facts or {}
    routing_decision = str(route_facts.get("routing_decision") or "").strip()
    if routing_decision == "memory_kernel_next_step":
        lines = ["I answered from the memory kernel next-step route for the previous Telegram turn."]
        route_lines = [
            "",
            "Route:",
            "- routing_decision: memory_kernel_next_step",
        ]
        current_focus = str(route_facts.get("current_focus") or "").strip()
        current_plan = str(route_facts.get("current_plan") or "").strip()
        if current_focus:
            route_lines.append(f"- current_focus: {current_focus}")
        if current_plan:
            route_lines.append(f"- current_plan: {current_plan}")
        focus_source = str(route_facts.get("focus_source_class") or "").strip()
        focus_method = str(route_facts.get("focus_read_method") or "").strip()
        if focus_source or focus_method:
            route_lines.append(
                f"- focus source: {focus_source or 'unknown'}"
                + (f" via {focus_method}" if focus_method else "")
            )
        plan_source = str(route_facts.get("plan_source_class") or "").strip()
        plan_method = str(route_facts.get("plan_read_method") or "").strip()
        if plan_source or plan_method:
            route_lines.append(
                f"- plan source: {plan_source or 'unknown'}"
                + (f" via {plan_method}" if plan_method else "")
            )
        evidence_source = str(route_facts.get("evidence_source_class") or "").strip()
        evidence_method = str(route_facts.get("evidence_read_method") or "").strip()
        if evidence_source or evidence_method:
            route_lines.append(
                f"- supporting evidence: {evidence_source or 'unknown'}"
                + (f" via {evidence_method}" if evidence_method else "")
            )
        try:
            ignored_count = int(route_facts.get("ignored_stale_record_count") or 0)
        except (TypeError, ValueError):
            ignored_count = 0
        if ignored_count:
            route_lines.append(f"- ignored stale records: {ignored_count}")
        promotion_gates = route_facts.get("context_packet_promotion_gates")
        if isinstance(promotion_gates, dict) and promotion_gates:
            gate_status = str(promotion_gates.get("status") or "unknown").strip()
            route_lines.append(f"- promotion gates: {gate_status}")
            gates = promotion_gates.get("gates") if isinstance(promotion_gates.get("gates"), dict) else {}
            for gate_name in (
                "source_swamp_resistance",
                "stale_current_conflict",
                "recent_conversation_noise",
                "source_mix_stability",
            ):
                gate = gates.get(gate_name) if isinstance(gates, dict) else None
                if not isinstance(gate, dict):
                    continue
                route_lines.append(
                    f"  - {gate_name}: {gate.get('status') or 'unknown'}"
                    + (f" ({gate.get('reason')})" if gate.get("reason") else "")
                )
        lines.extend(route_lines)
        lines.extend(
            [
                "",
                "Reason:",
                "The previous answer was a direct memory-kernel read. "
                "Current-state facts supplied the active focus and plan, stale records were advisory only, "
                "and the hybrid promotion gates describe packet quality before acceptance.",
            ]
        )
        return "\n".join(lines)
    route_reply = _format_memory_route_source_reply(route_facts=route_facts)
    if route_reply:
        return route_reply

    lines = ["I answered from the latest Spark context capsule for the previous Telegram turn."]
    lines.extend(["", "Authority sources:", *render(authority)])
    lines.extend(["", "Supporting/advisory sources:", *render(supporting)])
    lines.extend(
        [
            "",
            "Reason:",
            "Current-state facts are the authority for saved focus and plan. "
            "Diagnostics are authority for scan and connector health. "
            "Workflow or mission residue is advisory unless you ask about those systems directly.",
        ]
    )
    return "\n".join(lines)


def _format_memory_route_source_reply(*, route_facts: dict[str, Any]) -> str | None:
    routing_decision = str(route_facts.get("routing_decision") or "").strip()
    bridge_mode = str(route_facts.get("bridge_mode") or "").strip()
    evidence_summary = str(route_facts.get("evidence_summary") or "").strip()
    attribute_match = re.search(r"\battribute=([^\s]+)", evidence_summary)
    query_kind = str(route_facts.get("query_kind") or "").strip()
    open_recall_attribute = _open_memory_recall_entity_attribute(query_kind)
    attribute_label = (
        attribute_match.group(1).replace("_", " ")
        if attribute_match
        else (open_recall_attribute.replace("_", " ") if open_recall_attribute else "entity")
    )
    route_label: str | None = None
    source_line: str | None = None
    reason: str | None = None
    if routing_decision == "memory_current_focus_plan_query" or bridge_mode == "memory_current_focus_plan":
        route_label = "current focus and plan route"
        source_line = "current_state focus and plan records"
        reason = (
            "The previous answer was a current-state focus/plan read. "
            "It used authoritative current-state slots for the active focus and plan, so clean diagnostics or old "
            "workflow residue did not close or override the user-level state."
        )
    elif routing_decision == "memory_entity_state_summary_query" or bridge_mode == "memory_entity_state_summary":
        route_label = "entity-state summary route"
        source_line = "entity_state current summary records"
        reason = (
            "The previous answer was an entity-state summary read. "
            "It gathered current entity-scoped records across attributes for the named object, so current state was "
            "the authority and historical or workflow residue was not used."
        )
    elif routing_decision == "memory_entity_state_history_query" or bridge_mode == "memory_entity_state_history":
        route_label = "entity-state history route"
        source_line = "entity_state history records"
        previous_value_found = route_facts.get("previous_value_found")
        if previous_value_found is None and evidence_summary:
            match = re.search(r"\bprevious_value_found=(yes|no)", evidence_summary)
            if match:
                previous_value_found = match.group(1) == "yes"
        if previous_value_found is False:
            reason = (
                "The previous answer was a temporal entity-memory read. "
                f"It used entity-scoped {attribute_label} history for the named object, but found no distinct previous value. "
                "Duplicate same-value records were not treated as a previous value."
            )
        else:
            reason = (
                "The previous answer was a temporal entity-memory read. "
                f"It used entity-scoped {attribute_label} history for the named object, so the current value and previous value "
                "came from entity-state records rather than diagnostics or workflow residue."
            )
    elif routing_decision == "memory_open_recall_query" or bridge_mode == "memory_open_recall":
        if open_recall_attribute:
            route_label = "entity-state current recall route"
            source_line = "entity_state current records"
            reason = (
                "The previous answer was a current entity-memory read. "
                f"It used entity-scoped {attribute_label} records for the named object, so the current value "
                "came from entity-state records rather than diagnostics or workflow residue."
            )
        else:
            route_label = "open memory recall route"
            source_line = "entity_state/current_state records plus relevant evidence"
            reason = (
                "The previous answer was an open memory recall. "
                "It selected records matching the question topic and requested attribute, while stale or wrong-attribute "
                "records stayed out of the final answer."
            )
    elif routing_decision == "build_quality_review_direct" or bridge_mode == "build_quality_review_direct":
        route_label = "build-quality review route"
        source_line = "grounded local repo evidence"
        reason = (
            "The previous answer was a deterministic build-quality guardrail. "
            "It inspected the target repo, route presence, git state, test evidence, and demo evidence before deciding "
            "whether a real rating was justified."
        )
    elif routing_decision == "memory_quality_dashboard_operator" or bridge_mode == "memory_quality_dashboard_operator":
        route_label = "memory-quality dashboard operator route"
        source_line = "recorded dashboard repo, test, demo, and export evidence"
        reason = (
            "The previous answer was a deterministic operator launch read. "
            "It used the recorded dashboard repo, demo URL, test evidence, and export command instead of inferring "
            "dashboard state from stale workflow residue."
        )
    elif routing_decision == "memory_episodic_daily_recall" or bridge_mode == "memory_episodic_daily_recall":
        route_label = "daily episodic memory route"
        source_line = "daily event ledger rollup"
        reason = (
            "The previous answer was a source-aware episodic recall. "
            "It summarized today's event ledger into human-readable sections, so the answer came from the work narrative "
            "rather than isolated slot facts, diagnostics, or workflow residue."
        )
    elif routing_decision == "memory_episodic_session_recall" or bridge_mode == "memory_episodic_session_recall":
        route_label = "session episodic memory route"
        source_line = "session event ledger rollup"
        reason = (
            "The previous answer was a source-aware session recall. "
            "It summarized the current conversation's event ledger into human-readable sections, so the answer came from "
            "this chat's narrative rather than isolated slot facts or stale workflow residue."
        )
    elif routing_decision == "memory_episodic_project_recall" or bridge_mode == "memory_episodic_project_recall":
        route_label = "project episodic memory route"
        source_line = "project event ledger rollup"
        reason = (
            "The previous answer was a source-aware project episodic recall. "
            "It filtered the event ledger by project/topic and summarized the work narrative, so the answer came from "
            "project memory rather than only current slots, diagnostics, or workflow residue."
        )
    if route_label is None:
        return None
    lines = [f"I answered from the {route_label} for the previous Telegram turn."]
    lines.extend(
        [
            "",
            "Route:",
            f"- routing_decision: {routing_decision or 'unknown'}",
            f"- bridge_mode: {bridge_mode or 'unknown'}",
            f"- source: {source_line}",
        ]
    )
    if (
        routing_decision == "build_quality_review_direct"
        or bridge_mode == "build_quality_review_direct"
        or routing_decision == "memory_quality_dashboard_operator"
        or bridge_mode == "memory_quality_dashboard_operator"
    ):
        for key, label in (
            ("target_repo", "target_repo"),
            ("verdict", "verdict"),
            ("evidence_complete", "evidence_complete"),
            ("url", "url"),
            ("export_command", "export_command"),
        ):
            value = route_facts.get(key)
            if value is not None and str(value).strip():
                lines.append(f"- {label}: {value}")
        for key, label in (("git", "git_state"), ("route", "route"), ("tests", "tests"), ("demo", "demo")):
            value = route_facts.get(key)
            if isinstance(value, dict):
                status = str(value.get("status") or "").strip()
                if status:
                    lines.append(f"- {label}: {status}")
        missing = route_facts.get("missing_evidence")
        if isinstance(missing, list) and missing:
            lines.append("- missing_evidence: " + ", ".join(str(item) for item in missing if str(item).strip()))
    for key, label in (
        ("query_kind", "query_kind"),
        ("topic", "topic"),
        ("record_count", "record_count"),
        ("attribute_count", "attribute_count"),
        ("candidate_record_count", "candidate_record_count"),
        ("event_count", "event_count"),
        ("session_count", "session_count"),
        ("section_count", "section_count"),
        ("read_method", "read_method"),
        ("summary_source", "summary_source"),
        ("session_id", "session_id"),
        ("project_key", "project_key"),
        ("current_focus", "current_focus"),
        ("current_plan", "current_plan"),
        ("focus_source_class", "focus_source_class"),
        ("focus_read_method", "focus_read_method"),
        ("plan_source_class", "plan_source_class"),
        ("plan_read_method", "plan_read_method"),
    ):
        value = route_facts.get(key)
        if value is not None and str(value).strip():
            lines.append(f"- {label}: {value}")
    attributes = route_facts.get("attributes")
    if isinstance(attributes, list) and attributes:
        lines.append("- attributes: " + ", ".join(str(item) for item in attributes if str(item).strip()))
    if evidence_summary:
        lines.append(f"- evidence_summary: {evidence_summary}")
    lines.extend(["", "Reason:", reason or "The previous answer came from the named memory route."])
    return "\n".join(lines)


def _build_context_source_debug_reply(
    *,
    state_db: StateDB,
    channel_kind: str,
    session_id: str,
    human_id: str,
    agent_id: str,
    request_id: str,
) -> tuple[str, dict[str, Any]] | None:
    try:
        with state_db.connect() as conn:
            tool_rows = conn.execute(
                """
                SELECT request_id, facts_json, created_at
                FROM builder_events
                WHERE component = 'researcher_bridge'
                  AND event_type = 'tool_result_received'
                  AND channel_id = ?
                  AND session_id = ?
                  AND human_id = ?
                  AND agent_id = ?
                ORDER BY created_at DESC, rowid DESC
                LIMIT 12
                """,
                (channel_kind, session_id, human_id, agent_id),
            ).fetchall()
            rows = conn.execute(
                """
                SELECT request_id, facts_json, created_at
                FROM builder_events
                WHERE component = 'researcher_bridge'
                  AND event_type = 'context_capsule_compiled'
                  AND channel_id = ?
                  AND session_id = ?
                  AND human_id = ?
                  AND agent_id = ?
                ORDER BY created_at DESC, rowid DESC
                LIMIT 12
                """,
                (channel_kind, session_id, human_id, agent_id),
            ).fetchall()
    except Exception:
        return None

    previous_route_request_id: str | None = None
    previous_route_created_at: str | None = None
    previous_route_facts: dict[str, Any] | None = None
    for row in tool_rows:
        explained_request_id = str(row["request_id"] or "").strip()
        if explained_request_id and explained_request_id == request_id:
            continue
        try:
            facts = json.loads(row["facts_json"] or "{}")
        except Exception:
            continue
        if not isinstance(facts, dict):
            continue
        routing_decision = str(facts.get("routing_decision") or facts.get("bridge_mode") or "").strip()
        if not routing_decision or routing_decision == "context_source_debug":
            continue
        previous_route_request_id = explained_request_id or None
        previous_route_created_at = row["created_at"]
        previous_route_facts = facts
        break

    if (
        previous_route_facts is not None
        and str(previous_route_facts.get("routing_decision") or "") == "memory_kernel_next_step"
    ):
        reply = _format_context_source_ledger_reply(
            ledger=[],
            source_counts={},
            explained_request_id=previous_route_request_id,
            route_facts=previous_route_facts,
        )
        return (
            reply,
            {
                "explained_request_id": previous_route_request_id,
                "explained_tool_result_created_at": previous_route_created_at,
                "explained_routing_decision": previous_route_facts.get("routing_decision"),
                "explained_bridge_mode": previous_route_facts.get("bridge_mode"),
                "explained_current_focus": previous_route_facts.get("current_focus"),
                "explained_current_plan": previous_route_facts.get("current_plan"),
                "explained_focus_read_method": previous_route_facts.get("focus_read_method"),
                "explained_focus_source_class": previous_route_facts.get("focus_source_class"),
                "explained_plan_read_method": previous_route_facts.get("plan_read_method"),
                "explained_plan_source_class": previous_route_facts.get("plan_source_class"),
                "explained_evidence_read_method": previous_route_facts.get("evidence_read_method"),
                "explained_evidence_source_class": previous_route_facts.get("evidence_source_class"),
                "explained_ignored_stale_record_count": previous_route_facts.get("ignored_stale_record_count"),
                "context_packet_promotion_gates": previous_route_facts.get("context_packet_promotion_gates"),
                "source_ledger": [],
                "source_counts": {},
            },
        )
    if previous_route_facts is not None:
        route_reply = _format_memory_route_source_reply(route_facts=previous_route_facts)
        if route_reply:
            return (
                route_reply,
                {
                    "explained_request_id": previous_route_request_id,
                    "explained_tool_result_created_at": previous_route_created_at,
                    "explained_routing_decision": previous_route_facts.get("routing_decision"),
                    "explained_bridge_mode": previous_route_facts.get("bridge_mode"),
                    "explained_evidence_summary": previous_route_facts.get("evidence_summary"),
                    "explained_topic": previous_route_facts.get("topic"),
                    "explained_query_kind": previous_route_facts.get("query_kind"),
                    "explained_record_count": previous_route_facts.get("record_count"),
                    "explained_read_method": previous_route_facts.get("read_method"),
                    "source_ledger": [],
                    "source_counts": {},
                },
            )

    for row in rows:
        explained_request_id = str(row["request_id"] or "").strip()
        if explained_request_id and explained_request_id == request_id:
            continue
        try:
            facts = json.loads(row["facts_json"] or "{}")
        except Exception:
            continue
        ledger = facts.get("source_ledger")
        if not isinstance(ledger, list) or not ledger:
            continue
        source_counts_raw = facts.get("source_counts")
        source_counts = source_counts_raw if isinstance(source_counts_raw, dict) else {}
        reply = _format_context_source_ledger_reply(
            ledger=ledger,
            source_counts=source_counts,
            explained_request_id=explained_request_id or None,
            route_facts=previous_route_facts if previous_route_request_id == explained_request_id else None,
        )
        return (
            reply,
            {
                "explained_request_id": explained_request_id or None,
                "explained_context_created_at": row["created_at"],
                "source_ledger": ledger,
                "source_counts": source_counts,
            },
        )
    return None


def _detect_active_context_status_query(user_message: str) -> bool:
    text = re.sub(r"\s+", " ", str(user_message or "").strip().lower())
    if not text:
        return False
    asks_open_status = any(
        marker in text
        for marker in (
            "still open",
            "what is open",
            "what's open",
            "what remains open",
            "what is verified",
            "what's verified",
            "only be closed by me",
            "only i should close",
            "should only be closed",
        )
    )
    asks_next_step = any(
        marker in text
        for marker in (
            "what should we work on next",
            "what should i work on next",
            "what do we work on next",
            "what next",
            "next move",
            "next step",
            "what should we verify next",
            "what should we evaluate next",
            "what should i evaluate next",
            "evaluate next",
        )
    )
    asks_recent_closure = any(
        marker in text
        for marker in (
            "what did we just close",
            "what did i just close",
            "what was just closed",
            "what we just closed",
        )
    )
    context_anchor = any(
        marker in text
        for marker in (
            "current focus",
            "current plan",
            "those sources",
            "these sources",
            "based on sources",
            "based on those sources",
            "based on the capsule",
        )
    )
    return (asks_open_status or asks_next_step or asks_recent_closure) and context_anchor


def _detect_open_ended_memory_next_step_query(user_message: str) -> bool:
    text = re.sub(r"\s+", " ", str(user_message or "").strip().lower())
    if not text:
        return False
    if any(
        marker in text
        for marker in (
            "based on my current focus",
            "based on the current focus",
            "current focus",
            "current plan",
            "what is verified",
            "still open",
            "what remains open",
        )
    ):
        return False
    if len(text) > 140:
        return False
    next_step_markers = (
        "what should we work on next",
        "what should i work on next",
        "what do we work on next",
        "what should we focus on next",
        "what should i focus on next",
        "what should we evaluate next",
        "what should i evaluate next",
        "what should we verify next",
        "what's next",
        "whats next",
        "what next",
        "next move",
        "next step",
        "continue on",
        "continue?",
    )
    return any(marker in text for marker in next_step_markers)


def _detect_current_focus_plan_query(user_message: str) -> bool:
    text = re.sub(r"\s+", " ", str(user_message or "").strip().lower())
    if not text:
        return False
    asks_current_state = any(
        marker in text
        for marker in (
            "what is",
            "what's",
            "what are",
            "show me",
            "tell me",
        )
    )
    has_focus = "current focus" in text
    has_plan = "current plan" in text or "focus and plan" in text or "plan and focus" in text
    return asks_current_state and has_focus and has_plan


def _detect_memory_cleanup_sample_query(user_message: str) -> bool:
    text = re.sub(r"\s+", " ", str(user_message or "").strip().lower())
    if not text:
        return False
    wants_sample = any(marker in text for marker in ("sample", "examples", "spot-check", "spot check"))
    cleanup_anchor = any(
        marker in text
        for marker in (
            "cleanup",
            "maintenance",
            "archived",
            "deleted",
            "still-current",
            "still current",
        )
    )
    memory_anchor = "memory" in text or "memories" in text
    return wants_sample and cleanup_anchor and memory_anchor


def _build_memory_quality_evaluation_plan_reply() -> tuple[str, dict[str, Any]]:
    lines = [
        "Here is the Telegram memory-quality evaluation plan:",
        "",
        "1. Natural recall",
        "- Seed one normal fact in conversation without saying it is a test.",
        "- After 3-5 unrelated turns, ask for it naturally.",
        "- Pass: Spark recalls the right fact without asking you to restate it.",
        "",
        "2. Stale context avoidance",
        "- Set an old value, then replace it with a newer value.",
        "- Ask the same question later in an open-ended way.",
        "- Pass: Spark uses the newer current-state value and does not revive the stale one.",
        "",
        "3. Current-state priority",
        "- Create a conflict between old workflow residue and current_state.",
        "- Ask what is active now.",
        "- Pass: current_state wins, and old workflow_state is treated as advisory only.",
        "",
        "4. Source explanation",
        "- After any memory answer, ask why it answered that way.",
        "- Pass: Spark names the memory source class it used, such as current_state, recent conversation, diagnostics, or workflow_state.",
        "",
        "5. Open-ended synthesis",
        "- Ask what we should do next without using exact route words like status, diagnostics, or checklist.",
        "- Pass: Spark reasons from the active focus instead of falling back to the old diagnostics handoff.",
        "",
        "Start with natural recall. Give Spark one low-stakes fact, then we will distract it for a few turns and ask for it back.",
    ]
    return "\n".join(lines), {
        "plan_kind": "persistent_memory_quality_evaluation",
        "dimensions": [
            "natural_recall",
            "stale_context_avoidance",
            "current_state_priority",
            "source_explanation",
            "open_ended_synthesis",
        ],
    }


def _detect_memory_cleanup_closure_query(user_message: str) -> bool:
    text = re.sub(r"\s+", " ", str(user_message or "").strip().lower())
    if not text:
        return False
    cleanup_anchor = any(
        marker in text
        for marker in (
            "cleanup",
            "maintenance",
            "archived",
            "deleted",
            "still-current",
            "still current",
            "cleanup samples",
        )
    )
    evidence_anchor = any(
        marker in text
        for marker in (
            "what exact evidence",
            "what evidence are you using",
            "evidence are you using",
        )
    )
    close_anchor = any(
        marker in text
        for marker in (
            "ready to close",
            "ready for close",
            "close this",
            "close it",
            "close the focus",
            "current focus ready",
        )
    )
    closure_anchor = close_anchor or evidence_anchor or any(
        marker in text
        for marker in (
            "what should only i decide",
            "only i decide",
        )
    )
    focus_anchor = "current focus" in text or "current plan" in text or "focus" in text or "plan" in text
    return (cleanup_anchor and closure_anchor and focus_anchor) or (evidence_anchor and close_anchor)


def _detect_current_focus_transition_command(user_message: str) -> tuple[str | None, str] | None:
    text = re.sub(r"\s+", " ", str(user_message or "").strip())
    if not text or "?" in text:
        return None
    set_match = re.search(
        r"\bset\s+(?:my|our|the)\s+(?:current\s+)?focus\s+to\s+(.+?)(?:[.!]|$)",
        text,
        flags=re.IGNORECASE,
    )
    if set_match is None:
        return None
    new_focus = _truncate_browser_evidence_text(set_match.group(1).strip(), max_chars=180)
    if not new_focus:
        return None
    close_match = re.search(
        r"\bmark\s+(.+?)\s+(?:as\s+)?closed\b",
        text,
        flags=re.IGNORECASE,
    )
    closed_focus = _truncate_browser_evidence_text(close_match.group(1).strip(), max_chars=180) if close_match else None
    return closed_focus, new_focus


def _detect_current_plan_transition_command(user_message: str) -> str | None:
    text = re.sub(r"\s+", " ", str(user_message or "").strip())
    if not text or "?" in text:
        return None
    set_match = re.search(
        r"\bset\s+(?:my|our|the)\s+(?:current\s+)?plan\s+to\s+(.+?)(?:[.!]|$)",
        text,
        flags=re.IGNORECASE,
    )
    if set_match is None:
        return None
    new_plan = _truncate_browser_evidence_text(set_match.group(1).strip(), max_chars=180)
    return new_plan or None


def _active_context_status_query_wants_next_step(user_message: str) -> bool:
    text = re.sub(r"\s+", " ", str(user_message or "").strip().lower())
    return any(
        marker in text
        for marker in (
            "what should we work on next",
            "what should i work on next",
            "what do we work on next",
            "what next",
            "next move",
            "next step",
            "what should we verify next",
            "what should we evaluate next",
            "what should i evaluate next",
            "evaluate next",
        )
    )


def _active_context_status_query_wants_full_status(user_message: str) -> bool:
    text = re.sub(r"\s+", " ", str(user_message or "").strip().lower())
    return any(
        marker in text
        for marker in (
            "what is verified",
            "what's verified",
            "only be closed by me",
            "only i should close",
            "should only be closed",
        )
    )


def _active_context_status_query_wants_recent_closure(user_message: str) -> bool:
    text = re.sub(r"\s+", " ", str(user_message or "").strip().lower())
    return any(
        marker in text
        for marker in (
            "what did we just close",
            "what did i just close",
            "what was just closed",
            "what we just closed",
        )
    )


def _canonical_event_human_id(value: str | None) -> str:
    raw = str(value or "").strip()
    if raw.startswith("human:"):
        raw = raw[len("human:") :]
    return raw


def _latest_current_focus_transition_event(*, state_db: StateDB, human_id: str) -> dict[str, Any] | None:
    target_human = _canonical_event_human_id(human_id)
    for event in latest_events_by_type(state_db, event_type="tool_result_received", limit=50):
        facts = event.get("facts_json") or {}
        if not isinstance(facts, dict):
            continue
        if facts.get("routing_decision") != "current_focus_transition":
            continue
        if _canonical_event_human_id(event.get("human_id")) != target_human:
            continue
        return event
    return None


def _capsule_line_value(lines: list[str], label: str) -> str | None:
    prefix = f"- {label}:"
    for line in lines:
        if not line.startswith(prefix):
            continue
        value = line[len(prefix) :].strip()
        value = re.sub(r"\s+\(as_of=.*\)$", "", value).strip()
        return value or None
    return None


def _capsule_line_values(lines: list[str], label: str) -> list[str]:
    prefix = f"- {label}:"
    values: list[str] = []
    for line in lines:
        if line.startswith(prefix):
            value = line[len(prefix) :].strip()
            if value:
                values.append(value)
    return values


def _format_memory_maintenance_evidence(job_line: str) -> str:
    text = str(job_line or "").strip()
    if not text:
        return ""
    last_run_match = re.search(r"\blast_run=([^\s]+)", text)
    result_match = re.search(r"\bresult=(.*)$", text)
    result_text = result_match.group(1).strip() if result_match else ""
    fields: dict[str, str] = {}
    for key, value in re.findall(r"\b([a-z_]+)=([^\s]+)", result_text):
        fields[key] = value
    if not fields:
        return f"Memory maintenance evidence: {text}."

    def field(key: str) -> str | None:
        value = fields.get(key)
        return value if value not in {None, ""} else None

    last_run = _format_operator_time_utc(last_run_match.group(1)) if last_run_match else None
    status = field("status")
    before = field("before")
    after = field("after")
    details: list[str] = []
    if before and after:
        details.append(f"{before} items down to {after}")
    for key, label in (
        ("deletions", "deleted"),
        ("archived", "archived"),
        ("superseded", "superseded"),
        ("still_current", "still current"),
        ("stale_preserved", "stale preserved"),
    ):
        value = field(key)
        if value is not None:
            details.append(f"{value} {label}")

    status_phrase = "and succeeded" if status == "succeeded" else f"with status {status}" if status else ""
    prefix = "Memory maintenance"
    if last_run:
        prefix += f" ran at {last_run}"
    if status_phrase:
        prefix += f" {status_phrase}"
    if details:
        return f"{prefix}: {', '.join(details)}."
    return f"{prefix}."


def _format_operator_time_utc(raw_timestamp: str | None) -> str | None:
    raw = str(raw_timestamp or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return parsed.astimezone(timezone.utc).strftime("%H:%M UTC")
    except ValueError:
        return raw


def _build_active_context_status_reply(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    human_id: str,
    session_id: str,
    channel_kind: str,
    request_id: str,
    user_message: str,
) -> tuple[str, dict[str, Any]]:
    capsule = build_spark_context_capsule(
        config_manager=config_manager,
        state_db=state_db,
        human_id=human_id,
        session_id=session_id,
        channel_kind=channel_kind,
        request_id=request_id,
        user_message=user_message,
    )
    current_state = capsule.sections.get("current_state") or []
    diagnostics = capsule.sections.get("diagnostics") or []
    workflow_state = capsule.sections.get("workflow_state") or []
    current_focus = _capsule_line_value(current_state, "current_focus")
    current_plan = _capsule_line_value(current_state, "current_plan")
    diagnostic_status = _capsule_line_value(diagnostics, "status")
    scanned_lines = _capsule_line_value(diagnostics, "scanned_lines")
    failure_lines = _capsule_line_value(diagnostics, "failure_lines")
    finding_signatures = _capsule_line_value(diagnostics, "finding_signatures")
    connector_health = _capsule_line_value(diagnostics, "connector_health")
    memory_jobs = [
        value
        for value in _capsule_line_values(workflow_state, "job")
        if "memory:sdk-maintenance" in value
    ]
    wants_next_step = _active_context_status_query_wants_next_step(user_message)
    wants_full_status = _active_context_status_query_wants_full_status(user_message)
    wants_recent_closure = _active_context_status_query_wants_recent_closure(user_message)
    focus_transition_event = _latest_current_focus_transition_event(state_db=state_db, human_id=human_id)
    focus_transition_facts = (
        focus_transition_event.get("facts_json") if focus_transition_event is not None else {}
    )
    focus_transition_facts = focus_transition_facts if isinstance(focus_transition_facts, dict) else {}
    recently_closed_focus = str(focus_transition_facts.get("closed_focus") or "").strip() or None
    transition_new_focus = str(focus_transition_facts.get("new_focus") or "").strip() or None

    lines = ["Based on the current Spark context capsule:"]
    lines.extend(["", "Current state"])
    lines.append(f"- Focus: {current_focus or 'not saved'}")
    lines.append(f"- Plan: {current_plan or 'not saved'}")
    if wants_recent_closure:
        lines.extend(["", "Recently closed"])
        if recently_closed_focus:
            lines.append(f"- {recently_closed_focus}")
        else:
            lines.append("- No recent focus-closure event is recorded.")

    verified: list[str] = []
    if diagnostic_status == "clean_latest_scan_no_failures_or_findings":
        detail_parts = []
        if scanned_lines:
            detail_parts.append(f"{scanned_lines} lines scanned")
        if failure_lines is not None:
            detail_parts.append(f"{failure_lines} failures")
        if finding_signatures is not None:
            detail_parts.append(f"{finding_signatures} findings")
        verified.append(
            "Latest diagnostics are clean"
            + (f" ({', '.join(detail_parts)})" if detail_parts else "")
            + "."
        )
    elif diagnostic_status:
        verified.append(f"Latest diagnostics status is {diagnostic_status}.")
    if connector_health:
        verified.append(f"Connector health: {connector_health}.")
    if wants_full_status:
        for job in memory_jobs[:1]:
            verified.append(_format_memory_maintenance_evidence(job))
    elif memory_jobs:
        verified.append("Memory maintenance succeeded.")
    if not verified:
        verified.append("No diagnostic or maintenance evidence is present in the capsule.")

    lines.extend(["", "Verified"])
    if wants_next_step and not wants_full_status:
        lines.append(f"- {verified[0]}")
        if len(verified) > 1:
            lines.append(f"- {verified[-1]}")
    else:
        lines.extend(f"- {item}" for item in verified)

    open_items: list[str] = []
    if current_focus:
        open_items.append(
            f'Focus "{current_focus}" remains open until you explicitly close it.'
        )
    if current_plan:
        open_items.append(
            f'Plan "{current_plan}" remains open until you confirm it meets your standard.'
        )
    if not open_items:
        open_items.append("No active focus or plan is saved in current_state.")
    lines.extend(["", "Open"])
    if wants_next_step and not wants_full_status:
        if current_focus:
            lines.append(f'- Focus "{current_focus}"')
        if current_plan:
            lines.append(f'- Plan "{current_plan}"')
        if not current_focus and not current_plan:
            lines.extend(f"- {item}" for item in open_items)
    else:
        lines.extend(f"- {item}" for item in open_items)
        lines.extend(
            [
                "",
                "Closure rule",
                "- Clean diagnostics and successful maintenance are evidence, not user-level closure.",
                "- current_state wins over old workflow_state for focus and plan.",
            ]
        )
    if wants_next_step:
        next_steps: list[str] = []
        if current_plan and memory_jobs:
            next_steps.append(
                "Spot-check a small sample of archived, deleted, and still-current memories before closing this."
            )
        if current_focus:
            next_steps.append(
                f'If the sample looks right, mark "{current_focus}" closed and set the next focus.'
            )
        if (current_focus or transition_new_focus) == "persistent memory quality evaluation":
            next_steps = [
                "Evaluate whether current focus updates survive across a new turn.",
                "Test open-ended recall against the same facts without triggering deterministic helper routes.",
                "Check whether old workflow_state ever outranks current_state again.",
            ]
        if not next_steps:
            next_steps.append("Pick a new current focus, because the capsule has no active focus or plan saved.")
        lines.extend(["", "Next"])
        lines.extend(f"- {item}" for item in next_steps)

    facts = {
        "source_counts": capsule.source_counts,
        "source_ledger": capsule.source_ledger(),
        "current_focus": current_focus,
        "current_plan": current_plan,
        "diagnostic_status": diagnostic_status,
        "maintenance_jobs": memory_jobs,
        "recently_closed_focus": recently_closed_focus,
        "transition_new_focus": transition_new_focus,
    }
    return "\n".join(lines), facts


def _build_open_ended_memory_next_step_reply(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    human_id: str,
    session_id: str,
    request_id: str,
    user_message: str,
) -> tuple[str | None, dict[str, Any]]:
    memory_subject = human_id if str(human_id or "").startswith("human:") else f"human:{human_id}"
    focus_lookup = read_memory_kernel(
        config_manager=config_manager,
        state_db=state_db,
        method="get_current_state",
        subject=memory_subject,
        predicate="profile.current_focus",
        query=user_message,
        actor_id="researcher_bridge",
        session_id=session_id,
        turn_id=request_id,
        source_surface="telegram_open_ended_next_step",
    )
    if focus_lookup.abstained or not focus_lookup.answer:
        return None, {
            "memory_kernel_used": True,
            "focus_found": False,
            "focus_read_method": focus_lookup.read_method,
            "focus_source_class": focus_lookup.source_class,
            "focus_reason": focus_lookup.reason,
        }
    plan_lookup = read_memory_kernel(
        config_manager=config_manager,
        state_db=state_db,
        method="get_current_state",
        subject=memory_subject,
        predicate="profile.current_plan",
        query=user_message,
        actor_id="researcher_bridge",
        session_id=session_id,
        turn_id=f"{request_id}:plan",
        source_surface="telegram_open_ended_next_step",
    )
    evidence_lookup = read_memory_kernel(
        config_manager=config_manager,
        state_db=state_db,
        method="retrieve_evidence",
        subject=memory_subject,
        query=user_message,
        limit=5,
        actor_id="researcher_bridge",
        session_id=session_id,
        turn_id=f"{request_id}:evidence",
        source_surface="telegram_open_ended_next_step",
        record_activity=False,
    )
    promotion_gates = _build_memory_next_step_promotion_gates(
        config_manager=config_manager,
        state_db=state_db,
        memory_subject=memory_subject,
        user_message=user_message,
        request_id=request_id,
        session_id=session_id,
    )
    focus = focus_lookup.answer
    plan = plan_lookup.answer if not plan_lookup.abstained else None
    lines = [
        f"Your active focus is {focus}.",
    ]
    if plan:
        lines.append(f"Your active plan is {plan}.")
    if str(focus or "").strip().lower() == "persistent memory quality evaluation":
        lines.extend(
            [
                "",
                "Next:",
                "- Run an open-ended recall check without using exact helper wording.",
                "- Create a stale/current conflict and verify current_state wins.",
                "- Ask why it answered that way and confirm the source class is named.",
            ]
        )
    else:
        lines.extend(
            [
                "",
                "Next:",
                "- Use this focus to define one concrete validation question.",
                "- Check whether current_state, recent conversation, or older workflow residue answers it.",
                "- Only close the focus after you confirm the result matches your standard.",
            ]
        )
    if evidence_lookup.ignored_stale_records:
        lines.extend(
            [
                "",
                "Note:",
                f"- I ignored {len(evidence_lookup.ignored_stale_records)} stale memory record(s) while checking supporting evidence.",
            ]
        )
    facts = {
        "memory_kernel_used": True,
        "current_focus": focus,
        "current_plan": plan,
        "focus_read_method": focus_lookup.read_method,
        "focus_source_class": focus_lookup.source_class,
        "plan_read_method": plan_lookup.read_method,
        "plan_source_class": plan_lookup.source_class,
        "evidence_read_method": evidence_lookup.read_method,
        "evidence_source_class": evidence_lookup.source_class,
        "ignored_stale_record_count": len(evidence_lookup.ignored_stale_records),
        "context_packet_promotion_gates": promotion_gates,
        "focus_trace": (focus_lookup.read_result.retrieval_trace or {}).get("memory_kernel"),
        "plan_trace": (plan_lookup.read_result.retrieval_trace or {}).get("memory_kernel"),
        "evidence_trace": (evidence_lookup.read_result.retrieval_trace or {}).get("memory_kernel"),
    }
    return "\n".join(lines), facts


def _build_memory_next_step_promotion_gates(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    memory_subject: str,
    user_message: str,
    request_id: str,
    session_id: str,
) -> dict[str, Any]:
    try:
        result = hybrid_memory_retrieve(
            config_manager=config_manager,
            state_db=state_db,
            query=user_message,
            subject=memory_subject,
            predicate="profile.current_focus",
            limit=5,
            actor_id="researcher_bridge",
            session_id=session_id,
            turn_id=f"{request_id}:graph-shadow-gate",
            source_surface="telegram_open_ended_next_step_gate_probe",
            record_activity=True,
        )
    except Exception as exc:
        return {
            "status": "warn",
            "mode": "trace_only",
            "reason": "promotion_gate_probe_failed",
            "error": exc.__class__.__name__,
            "gates": {},
        }
    if result.context_packet is None:
        return {
            "status": "warn",
            "mode": "trace_only",
            "reason": "context_packet_missing",
            "gates": {},
        }
    gates = result.context_packet.trace.get("promotion_gates")
    return gates if isinstance(gates, dict) else {}


def _record_memory_graph_shadow_probe(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    query: str,
    subject: str,
    predicate: str | None,
    entity_key: str | None,
    session_id: str,
    turn_id: str,
    source_surface: str,
    limit: int = 5,
) -> dict[str, Any]:
    """Record an observational hybrid read so graph sidecar lanes are visible without changing the answer."""
    try:
        result = hybrid_memory_retrieve(
            config_manager=config_manager,
            state_db=state_db,
            query=query,
            subject=subject,
            predicate=predicate,
            entity_key=entity_key,
            limit=limit,
            actor_id="researcher_bridge",
            session_id=session_id,
            turn_id=turn_id,
            source_surface=source_surface,
            record_activity=True,
        )
    except Exception as exc:
        return {"status": "error", "error": exc.__class__.__name__, "turn_id": turn_id}

    graph_lane = _graph_sidecar_lane_from_hybrid_trace(result.read_result.retrieval_trace)
    return {
        "status": "recorded",
        "turn_id": turn_id,
        "source_surface": source_surface,
        "selected_count": len([candidate for candidate in result.candidates if candidate.selected]),
        "candidate_count": len(result.candidates),
        "graph_lane": graph_lane,
    }


def _graph_sidecar_lane_from_hybrid_trace(trace: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(trace, dict):
        return None
    hybrid_trace = trace.get("hybrid_memory_retrieve")
    if not isinstance(hybrid_trace, dict):
        return None
    lane_summaries = hybrid_trace.get("lane_summaries")
    if not isinstance(lane_summaries, list):
        return None
    for lane in lane_summaries:
        if not isinstance(lane, dict):
            continue
        if lane.get("lane") == "typed_temporal_graph" or lane.get("source_class") == "graphiti_temporal_graph":
            return {
                "lane": lane.get("lane"),
                "source_class": lane.get("source_class"),
                "status": lane.get("status"),
                "reason": lane.get("reason"),
                "mode": lane.get("mode"),
                "shadow_only": lane.get("shadow_only"),
                "episode_export_count": lane.get("episode_export_count"),
                "sidecar_hit_count": lane.get("sidecar_hit_count"),
            }
    return None


def _build_current_focus_plan_reply(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    human_id: str,
    session_id: str,
    request_id: str,
    user_message: str,
) -> tuple[str, dict[str, Any]]:
    memory_subject = human_id if str(human_id or "").startswith("human:") else f"human:{human_id}"
    focus_lookup = read_memory_kernel(
        config_manager=config_manager,
        state_db=state_db,
        method="get_current_state",
        subject=memory_subject,
        predicate="profile.current_focus",
        query=user_message,
        actor_id="researcher_bridge",
        session_id=session_id,
        turn_id=f"{request_id}:focus",
        source_surface="telegram_current_focus_plan_query",
    )
    plan_lookup = read_memory_kernel(
        config_manager=config_manager,
        state_db=state_db,
        method="get_current_state",
        subject=memory_subject,
        predicate="profile.current_plan",
        query=user_message,
        actor_id="researcher_bridge",
        session_id=session_id,
        turn_id=f"{request_id}:plan",
        source_surface="telegram_current_focus_plan_query",
    )
    focus = focus_lookup.answer if not focus_lookup.abstained else None
    plan = plan_lookup.answer if not plan_lookup.abstained else None
    focus_query = detect_profile_fact_query("What is my current focus?")
    plan_query = detect_profile_fact_query("What is my current plan?")
    lines = [
        build_profile_fact_query_answer(query=focus_query, value=focus, stale=False)
        if focus_query is not None
        else f"Your current focus is {focus}." if focus else "I don't currently have your current focus saved.",
        build_profile_fact_query_answer(query=plan_query, value=plan, stale=False)
        if plan_query is not None
        else f"Your current plan is {plan}." if plan else "I don't currently have your current plan saved.",
    ]
    facts = {
        "memory_kernel_used": True,
        "current_focus": focus,
        "current_plan": plan,
        "focus_found": bool(focus),
        "plan_found": bool(plan),
        "focus_read_method": focus_lookup.read_method,
        "focus_source_class": focus_lookup.source_class,
        "plan_read_method": plan_lookup.read_method,
        "plan_source_class": plan_lookup.source_class,
        "focus_trace": (focus_lookup.read_result.retrieval_trace or {}).get("memory_kernel"),
        "plan_trace": (plan_lookup.read_result.retrieval_trace or {}).get("memory_kernel"),
    }
    return "\n".join(lines), facts


def _build_memory_cleanup_sample_reply(
    *,
    state_db: StateDB,
    human_id: str | None = None,
) -> tuple[str, dict[str, Any]]:
    event = _latest_memory_maintenance_event(state_db=state_db)
    if event is None:
        reply = (
            "I only have the cleanup request, not a reviewable sample yet.\n\n"
            "Next: run memory maintenance once more after the audit-sample patch, then ask for archived, deleted, and still-current samples again."
        )
        return reply, {"sample_available": False, "reason": "maintenance_event_missing"}
    facts = event.get("facts_json") or {}
    maintenance = facts.get("maintenance") if isinstance(facts.get("maintenance"), dict) else {}
    samples = maintenance.get("audit_samples") if isinstance(maintenance.get("audit_samples"), dict) else {}
    if not any(samples.get(bucket) for bucket in ("archived", "deleted", "still_current")):
        counts = {
            "archived": maintenance.get("active_state_archived_count", 0),
            "deleted": maintenance.get("active_deletion_count", 0),
            "still_current": maintenance.get("active_state_still_current_count", 0),
        }
        reply = (
            "I only have cleanup counts right now, not reviewable sample rows.\n\n"
            f"Counts: {counts.get('archived', 0)} archived, {counts.get('deleted', 0)} deleted, "
            f"{counts.get('still_current', 0)} still current.\n\n"
            "Next: rerun memory maintenance so the new audit-sample field is recorded, then ask me for the samples again."
        )
        return (
            reply,
            {
                "sample_available": False,
                "reason": "audit_samples_missing",
                "counts": counts,
                "maintenance_event_id": event.get("event_id"),
            },
        )

    lines = ["Here is a small memory cleanup audit sample:"]
    for bucket, title in (
        ("archived", "Archived"),
        ("deleted", "Deleted"),
        ("still_current", "Still current"),
    ):
        lines.extend(["", title])
        raw_bucket_samples = samples.get(bucket) if isinstance(samples.get(bucket), list) else []
        bucket_samples = _rank_memory_cleanup_samples(raw_bucket_samples, human_id=human_id)
        if not bucket_samples:
            lines.append("- none in the latest sample")
            continue
        for sample in bucket_samples[:3]:
            if not isinstance(sample, dict):
                continue
            predicate = str(sample.get("predicate") or "unknown").strip()
            value = _truncate_browser_evidence_text(
                str(sample.get("value") or sample.get("text") or "").strip(),
                max_chars=120,
            )
            reason = str(sample.get("reason") or "").strip()
            suffix = f" ({reason})" if reason else ""
            lines.append(f"- {predicate}: {value or 'no value'}{suffix}")
    lines.extend(
        [
            "",
            "Use this as a spot-check only. If anything looks wrong, inspect the full maintenance artifact before closing the focus.",
        ]
    )
    return (
        "\n".join(lines),
        {
            "sample_available": True,
            "maintenance_event_id": event.get("event_id"),
            "audit_samples": samples,
        },
    )


def _maintenance_count(
    maintenance: dict[str, Any],
    *keys: str,
) -> Any:
    for key in keys:
        value = maintenance.get(key)
        if value is not None:
            return value
    return None


def _memory_cleanup_sample_preview(
    samples: dict[str, Any],
    *,
    human_id: str | None,
    bucket: str,
    limit: int = 2,
) -> list[str]:
    raw_bucket_samples = samples.get(bucket) if isinstance(samples.get(bucket), list) else []
    preview: list[str] = []
    for sample in _rank_memory_cleanup_samples(raw_bucket_samples, human_id=human_id)[:limit]:
        predicate = str(sample.get("predicate") or "unknown").strip()
        value = _truncate_browser_evidence_text(
            str(sample.get("value") or sample.get("text") or "").strip(),
            max_chars=80,
        )
        if value:
            preview.append(f"{predicate}: {value}")
        else:
            preview.append(predicate)
    return preview


def _build_memory_cleanup_closure_reply(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    human_id: str,
    session_id: str,
    channel_kind: str,
    request_id: str,
    user_message: str,
) -> tuple[str, dict[str, Any]]:
    capsule = build_spark_context_capsule(
        config_manager=config_manager,
        state_db=state_db,
        human_id=human_id,
        session_id=session_id,
        channel_kind=channel_kind,
        request_id=request_id,
        user_message=user_message,
    )
    current_state = capsule.sections.get("current_state") or []
    diagnostics = capsule.sections.get("diagnostics") or []
    current_focus = _capsule_line_value(current_state, "current_focus")
    current_plan = _capsule_line_value(current_state, "current_plan")
    diagnostic_status = _capsule_line_value(diagnostics, "status")
    scanned_lines = _capsule_line_value(diagnostics, "scanned_lines")
    failure_lines = _capsule_line_value(diagnostics, "failure_lines")
    finding_signatures = _capsule_line_value(diagnostics, "finding_signatures")

    event = _latest_memory_maintenance_event(state_db=state_db)
    facts = event.get("facts_json") if event else {}
    facts = facts if isinstance(facts, dict) else {}
    maintenance = facts.get("maintenance") if isinstance(facts.get("maintenance"), dict) else {}
    samples = maintenance.get("audit_samples") if isinstance(maintenance.get("audit_samples"), dict) else {}

    before = _maintenance_count(maintenance, "manual_observations_before", "before")
    after = _maintenance_count(maintenance, "manual_observations_after", "after")
    archived = _maintenance_count(maintenance, "active_state_archived_count", "archived")
    deleted = _maintenance_count(maintenance, "active_deletion_count", "deletions", "deleted")
    superseded = _maintenance_count(maintenance, "active_state_superseded_count", "superseded")
    still_current = _maintenance_count(maintenance, "active_state_still_current_count", "still_current")
    stale_preserved = _maintenance_count(maintenance, "active_state_stale_preserved_count", "stale_preserved")

    lines = [
        "The evidence is enough to say this is closeable if you are satisfied, not enough to prove every memory was reviewed.",
        "",
        "Evidence I have",
    ]
    if diagnostic_status == "clean_latest_scan_no_failures_or_findings":
        details = []
        if scanned_lines:
            details.append(f"{scanned_lines} lines")
        if failure_lines is not None:
            details.append(f"{failure_lines} failures")
        if finding_signatures is not None:
            details.append(f"{finding_signatures} findings")
        lines.append("- Latest diagnostics are clean" + (f": {', '.join(details)}." if details else "."))
    elif diagnostic_status:
        lines.append(f"- Latest diagnostics status: {diagnostic_status}.")
    else:
        lines.append("- No latest diagnostic summary is loaded in the capsule.")

    if maintenance:
        count_parts = []
        if before is not None and after is not None:
            count_parts.append(f"{before} -> {after}")
        if archived is not None:
            count_parts.append(f"{archived} archived")
        if deleted is not None:
            count_parts.append(f"{deleted} deletion markers")
        if superseded is not None:
            count_parts.append(f"{superseded} superseded")
        if still_current is not None:
            count_parts.append(f"{still_current} still current")
        if stale_preserved is not None:
            count_parts.append(f"{stale_preserved} stale preserved")
        lines.append("- Memory maintenance succeeded" + (f": {', '.join(count_parts)}." if count_parts else "."))
    else:
        lines.append("- No memory maintenance artifact is loaded for this question.")

    archived_preview = _memory_cleanup_sample_preview(samples, human_id=human_id, bucket="archived")
    deleted_preview = _memory_cleanup_sample_preview(samples, human_id=human_id, bucket="deleted")
    still_preview = _memory_cleanup_sample_preview(samples, human_id=human_id, bucket="still_current")
    if archived_preview or deleted_preview or still_preview:
        lines.extend(["- Audit sample reviewed.", "", "Archived examples"])
        if archived_preview:
            lines.extend(f"- {item}" for item in archived_preview)
        else:
            lines.append("- none in the latest sample")
        lines.append("")
        lines.append("Deleted examples")
        if deleted_preview:
            lines.extend(f"- {item}" for item in deleted_preview)
        else:
            lines.append("- none in the latest sample")
        lines.append("")
        lines.append("Still-current examples")
        if still_preview:
            lines.extend(f"- {item}" for item in still_preview)
        else:
            lines.append("- none in the latest sample")
        lines.extend(["", "Sample verdict", "- The sample did not show obvious damage."])
    else:
        lines.append("- No audit sample rows are loaded, so this cannot confirm sample quality.")

    lines.extend(
        [
            "",
            "Limits",
            "- I reviewed a small audit sample, not every archived or deleted memory.",
            "- I cannot decide whether a stale or deleted item mattered strategically to you.",
            "",
            "Only you should close",
            f'- Focus "{current_focus}"' if current_focus else "- The current focus, if you still consider it active.",
            f'- Plan "{current_plan}"' if current_plan else "- The current plan, if you still consider it active.",
        ]
    )
    return (
        "\n".join(lines),
        {
            "source_counts": capsule.source_counts,
            "source_ledger": capsule.source_ledger(),
            "current_focus": current_focus,
            "current_plan": current_plan,
            "diagnostic_status": diagnostic_status,
            "maintenance_event_id": event.get("event_id") if event else None,
            "sample_available": bool(archived_preview or deleted_preview or still_preview),
        },
    )


def _rank_memory_cleanup_samples(samples: list[Any], *, human_id: str | None) -> list[dict[str, Any]]:
    normalized = [sample for sample in samples if isinstance(sample, dict)]
    target_human = str(human_id or "").strip()

    def score(sample: dict[str, Any]) -> tuple[int, int, str]:
        subject = str(sample.get("subject") or "").strip()
        observation_id = str(sample.get("observation_id") or "").strip()
        predicate = str(sample.get("predicate") or "").strip()
        same_human = bool(target_human and subject == target_human)
        smoke_or_sim = (
            "smoke" in subject.casefold()
            or observation_id.startswith("sim:")
            or subject.startswith("human:telegram:949385504366")
            or subject.startswith("human:telegram:905162608906")
        )
        current_profile = predicate.startswith("profile.current_")
        return (
            0 if same_human else 1,
            0 if current_profile else 1,
            1 if smoke_or_sim else 0,
            observation_id,
        )

    return sorted(normalized, key=score)


def _latest_memory_maintenance_event(*, state_db: StateDB) -> dict[str, Any] | None:
    for event in latest_events_by_type(state_db, event_type="memory_maintenance_run", limit=20):
        if event.get("component") == "memory_orchestrator":
            return event
    return None


def _bridge_reply_mutation_facts(
    *,
    raw_text: str,
    mutated_text: str,
    mutation_actions: list[str] | None = None,
) -> dict[str, Any]:
    return build_text_mutation_facts(
        raw_text=raw_text,
        mutated_text=mutated_text,
        mutation_actions=mutation_actions,
    )


def _runtime_safe_bridge_failure_message(result: ResearcherBridgeResult) -> str:
    failure_kind = str(result.routing_decision or result.mode or "bridge_failure")
    if result.output_keepability == "operator_debug_only":
        return (
            f"{failure_kind} recorded. Inspect trace and event history for operator-only details."
        )
    return str(result.reply_text or "").strip()


_EXPLICIT_MEMORY_PREFIX_PATTERN = re.compile(
    r"^\s*(?:"
    r"/(?:remember|save)\b"
    r"|(?:please\s+)?(?:remember|save)\s+(?:this|that|the following)(?:\s+for me)?\s*:?"
    r")\s*",
    re.IGNORECASE,
)


def _normalize_explicit_memory_message(user_message: str) -> tuple[bool, str]:
    text = " ".join(str(user_message or "").strip().split())
    if not text:
        return False, ""
    normalized = _EXPLICIT_MEMORY_PREFIX_PATTERN.sub("", text, count=1).strip()
    if not normalized or normalized == text:
        return False, text
    return True, normalized


_EXPLICIT_CURRENT_STATE_MEMORY_PATTERN = re.compile(
    r"^(?:(?:actually|update|correction)[:,]?\s+)?(?:memory\s+update:\s*)?"
    r"(?:(?:my|our|the)\s+current\s+"
    r"(?:plan|focus|decision|blocker|status|commitment|milestone|risk|dependency|constraint|assumption|owner)\s+is\b"
    r"|the\s+plan\s+is\b"
    r"|our\s+priority\s+is\b"
    r"|(?:for\s+the\s+(?:natural\s+recall|recall|memory\s+quality)\s+test[:,]?\s*)?"
    r"(?:please\s+)?remember\s+that\s+(?:my|the)\s+(?:low[-\s]stakes\s+)?test\s+fact\s+is\b"
    r"|(?:my|the)\s+(?:low[-\s]stakes\s+)?test\s+fact\s+is\b)",
    re.IGNORECASE,
)


def _should_direct_acknowledge_current_state_memory(user_message: str) -> bool:
    text = " ".join(str(user_message or "").strip().split())
    if not text:
        return False
    return bool(_EXPLICIT_CURRENT_STATE_MEMORY_PATTERN.search(text))


def _build_direct_preference_update_answer(*, user_message: str) -> str:
    if re.search(r"\b(?:reply|response|style|tone|voice|concise|warm|direct)\b", user_message, flags=re.I):
        return "Saved that reply style preference."
    return "Saved that preference. I'll use it going forward."


def _explicit_style_preference_canonical_message(user_message: str) -> str | None:
    lowered = str(user_message or "").casefold()
    if not re.search(r"\b(?:reply|response|answer|communication)\s+style\b", lowered):
        return None
    directives: list[str] = []
    if any(word in lowered for word in ("concise", "brief", "short", "low-fluff", "low fluff", "no-fluff")):
        directives.append("be concise")
    if "warm" in lowered or "friendly" in lowered:
        directives.append("be warm")
    if "direct" in lowered:
        directives.append("be direct")
    if not directives:
        return None
    return " and ".join(directives)


def researcher_bridge_status(*, config_manager: ConfigManager, state_db: StateDB) -> ResearcherBridgeStatus:
    attachment_context = build_attachment_context(config_manager)
    runtime_root, runtime_source = discover_researcher_runtime_root(config_manager)
    config_path = resolve_researcher_config_path(config_manager, runtime_root) if runtime_root else None
    enabled = bool(config_manager.get_path("spark.researcher.enabled", default=True))
    available = enabled and bool(runtime_root and config_path and config_path.exists())
    mode = "disabled" if not enabled else (f"external_{runtime_source}" if available else "stub")
    typed_state = _read_typed_researcher_bridge_state(state_db)
    runtime_state = _read_runtime_state(state_db)
    return ResearcherBridgeStatus(
        enabled=enabled,
        configured=runtime_root is not None,
        available=available,
        mode=mode,
        runtime_root=str(runtime_root) if runtime_root else None,
        config_path=str(config_path) if config_path else None,
        attachment_context=attachment_context,
        last_mode=str(typed_state.get("last_mode") or runtime_state.get("researcher:last_mode") or "") or None,
        last_trace_ref=str(typed_state.get("last_trace_ref") or runtime_state.get("researcher:last_trace_ref") or "") or None,
        last_request_id=str(typed_state.get("last_request_id") or runtime_state.get("researcher:last_request_id") or "") or None,
        last_runtime_root=str(typed_state.get("last_runtime_root") or runtime_state.get("researcher:last_runtime_root") or "") or None,
        last_config_path=str(typed_state.get("last_config_path") or runtime_state.get("researcher:last_config_path") or "") or None,
        last_evidence_summary=str(typed_state.get("last_evidence_summary") or runtime_state.get("researcher:last_evidence_summary") or "") or None,
        last_attachment_context=typed_state.get("last_attachment_context") or _loads_json(runtime_state.get("researcher:last_attachment_context")),
        last_provider_id=str(typed_state.get("last_provider_id") or runtime_state.get("researcher:last_provider_id") or "") or None,
        last_provider_model=str(typed_state.get("last_provider_model") or runtime_state.get("researcher:last_provider_model") or "") or None,
        last_provider_model_family=str(typed_state.get("last_provider_model_family") or runtime_state.get("researcher:last_provider_model_family") or "") or None,
        last_provider_auth_method=str(typed_state.get("last_provider_auth_method") or runtime_state.get("researcher:last_provider_auth_method") or "") or None,
        last_provider_execution_transport=str(typed_state.get("last_provider_execution_transport") or runtime_state.get("researcher:last_provider_execution_transport") or "") or None,
        last_routing_decision=str(typed_state.get("last_routing_decision") or runtime_state.get("researcher:last_routing_decision") or "") or None,
        last_active_chip_key=str(typed_state.get("last_active_chip_key") or runtime_state.get("researcher:last_active_chip_key") or "") or None,
        last_active_chip_task_type=str(typed_state.get("last_active_chip_task_type") or runtime_state.get("researcher:last_active_chip_task_type") or "") or None,
        last_active_chip_evaluate_used=bool(
            typed_state.get("last_active_chip_evaluate_used")
            if typed_state.get("last_active_chip_evaluate_used") is not None
            else _parse_bool(runtime_state.get("researcher:last_active_chip_evaluate_used"))
        ),
        last_output_keepability=str(typed_state.get("last_output_keepability") or runtime_state.get("researcher:last_output_keepability") or "") or None,
        last_promotion_disposition=str(typed_state.get("last_promotion_disposition") or runtime_state.get("researcher:last_promotion_disposition") or "") or None,
        failure_count=int(typed_state.get("failure_count") or _parse_int(runtime_state.get("researcher:failure_count"))),
        last_failure=typed_state.get("last_failure") or _loads_json(runtime_state.get("researcher:last_failure")),
    )


def record_researcher_bridge_result(*, state_db: StateDB, result: ResearcherBridgeResult) -> None:
    with state_db.connect() as conn:
        _set_runtime_state(conn, "researcher:last_mode", result.mode)
        _set_runtime_state(conn, "researcher:last_trace_ref", result.trace_ref)
        _set_runtime_state(conn, "researcher:last_request_id", result.request_id)
        _set_runtime_state(conn, "researcher:last_runtime_root", result.runtime_root or "")
        _set_runtime_state(conn, "researcher:last_config_path", result.config_path or "")
        _set_runtime_state(conn, "researcher:last_evidence_summary", result.evidence_summary)
        _set_runtime_state(conn, "researcher:last_provider_id", result.provider_id or "")
        _set_runtime_state(conn, "researcher:last_provider_model", result.provider_model or "")
        _set_runtime_state(conn, "researcher:last_provider_model_family", result.provider_model_family or "")
        _set_runtime_state(conn, "researcher:last_provider_auth_method", result.provider_auth_method or "")
        _set_runtime_state(
            conn,
            "researcher:last_provider_execution_transport",
            result.provider_execution_transport or "",
        )
        _set_runtime_state(conn, "researcher:last_routing_decision", result.routing_decision or "")
        _set_runtime_state(conn, "researcher:last_active_chip_key", result.active_chip_key or "")
        _set_runtime_state(conn, "researcher:last_active_chip_task_type", result.active_chip_task_type or "")
        _set_runtime_state(
            conn,
            "researcher:last_active_chip_evaluate_used",
            "1" if result.active_chip_evaluate_used else "0",
        )
        _set_runtime_state(conn, "researcher:last_output_keepability", result.output_keepability)
        _set_runtime_state(conn, "researcher:last_promotion_disposition", result.promotion_disposition)
        _set_runtime_state(
            conn,
            "researcher:last_attachment_context",
            json.dumps(result.attachment_context or {}, sort_keys=True),
            guard_strategy=JSON_RICHNESS_MERGE_GUARD,
        )
        if result.mode == "bridge_error":
            failure_count = _read_failure_count(conn, "researcher:failure_count")
            _set_runtime_state(conn, "researcher:failure_count", str(failure_count + 1))
            _set_runtime_state(
                conn,
                "researcher:last_failure",
                json.dumps(
                    {
                        "mode": result.mode,
                        "request_id": result.request_id,
                        "trace_ref": result.trace_ref,
                        "routing_decision": result.routing_decision,
                        "runtime_root": result.runtime_root,
                        "config_path": result.config_path,
                        "message": _runtime_safe_bridge_failure_message(result),
                        "output_keepability": result.output_keepability,
                        "promotion_disposition": result.promotion_disposition,
                        "recorded_at": _utc_now_iso(),
                    },
                    sort_keys=True,
                ),
                guard_strategy=JSON_RICHNESS_MERGE_GUARD,
            )
        conn.commit()


def build_researcher_reply(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    request_id: str,
    agent_id: str,
    human_id: str,
    session_id: str,
    channel_kind: str,
    user_message: str,
    run_id: str | None = None,
) -> ResearcherBridgeResult:
    attachment_context = build_attachment_context(config_manager)
    explicit_memory_message, memory_user_message = _normalize_explicit_memory_message(user_message)
    preference_detection_message = (
        _explicit_style_preference_canonical_message(memory_user_message)
        if explicit_memory_message
        else None
    ) or memory_user_message

    # ── Personality integration ──
    personality_profile = None
    personality_context_extra = ""  # extra context for acknowledgments/queries
    personality_query_kind = "none"
    evolved_deltas = None
    observation_record = None
    detected_profile_fact = None
    detected_profile_fact_query = None
    detected_memory_event = None
    detected_memory_event_query = None
    detected_entity_state_history_query = None
    detected_entity_state_history_followup: dict[str, Any] | None = None
    detected_entity_state_summary_query = None
    detected_open_memory_recall_query = None
    detected_belief_recall_query = None
    detected_episodic_daily_recall_query = None
    detected_episodic_project_recall_query = None
    detected_generic_memory_candidate = None
    assessed_generic_memory_candidate = None
    detected_generic_memory_deletion = None
    detected_generic_memory_observation = None
    try:
        personality_profile = load_personality_profile(
            human_id=human_id,
            agent_id=agent_id,
            state_db=state_db,
            config_manager=config_manager,
        )
    except Exception:
        pass

    # Check for personality queries (status, reset) before NL detection
    try:
        query_result = detect_personality_query(
            user_message=user_message,
            human_id=human_id,
            agent_id=agent_id,
            state_db=state_db,
            profile=personality_profile,
            config_manager=config_manager,
            session_id=session_id,
            turn_id=request_id,
        )
        if query_result.kind != "none":
            personality_query_kind = query_result.kind
            personality_context_extra = query_result.context_injection
            if query_result.kind == "reset":
                # Reload profile after reset
                personality_profile = load_personality_profile(
                    human_id=human_id,
                    agent_id=agent_id,
                    state_db=state_db,
                    config_manager=config_manager,
                )
    except Exception:
        pass

    agent_persona_mutation = None
    if not personality_context_extra:
        try:
            agent_persona_mutation = detect_and_persist_agent_persona_preferences(
                agent_id=agent_id,
                human_id=human_id,
                user_message=user_message,
                state_db=state_db,
                source_surface=channel_kind,
                source_ref=request_id,
            )
            if agent_persona_mutation is not None:
                personality_context_extra = agent_persona_mutation.context_injection
                personality_profile = load_personality_profile(
                    human_id=human_id,
                    agent_id=agent_id,
                    state_db=state_db,
                    config_manager=config_manager,
                )
        except Exception:
            pass

    if not personality_context_extra and _detect_context_source_debug_query(user_message):
        trace_ref = f"trace:{agent_id}:{human_id}:{request_id}"
        context_source_debug = _build_context_source_debug_reply(
            state_db=state_db,
            channel_kind=channel_kind,
            session_id=session_id,
            human_id=human_id,
            agent_id=agent_id,
            request_id=request_id,
        )
        if context_source_debug is not None:
            reply_text, debug_facts = context_source_debug
            output_keepability, promotion_disposition = _bridge_output_classification(
                mode="context_source_debug",
                routing_decision="context_source_debug",
            )
            evidence_summary = (
                "status=context_source_debug "
                f"explained_request_id={debug_facts.get('explained_request_id') or 'unknown'}"
            )
            record_event(
                state_db,
                event_type="tool_result_received",
                component="researcher_bridge",
                summary="Researcher bridge explained the latest context source ledger.",
                run_id=run_id,
                request_id=request_id,
                trace_ref=trace_ref,
                channel_id=channel_kind,
                session_id=session_id,
                human_id=human_id,
                agent_id=agent_id,
                actor_id="researcher_bridge",
                reason_code="context_source_debug",
                facts=_bridge_event_facts(
                    routing_decision="context_source_debug",
                    bridge_mode="context_source_debug",
                    evidence_summary=evidence_summary,
                    active_chip_key=None,
                    active_chip_task_type=None,
                    active_chip_evaluate_used=False,
                    keepability=output_keepability,
                    promotion_disposition=promotion_disposition,
                    extra={
                        "query_text": str(user_message or "").strip(),
                        **debug_facts,
                    },
                ),
            )
            return ResearcherBridgeResult(
                request_id=request_id,
                reply_text=reply_text,
                evidence_summary=evidence_summary,
                escalation_hint=None,
                trace_ref=trace_ref,
                mode="context_source_debug",
                runtime_root=None,
                config_path=None,
                attachment_context=attachment_context,
                routing_decision="context_source_debug",
                active_chip_key=None,
                active_chip_task_type=None,
                active_chip_evaluate_used=False,
                output_keepability=output_keepability,
                promotion_disposition=promotion_disposition,
            )

    if not personality_context_extra and _detect_memory_quality_evaluation_plan_query(user_message):
        trace_ref = f"trace:{agent_id}:{human_id}:{request_id}"
        reply_text, plan_facts = _build_memory_quality_evaluation_plan_reply()
        output_keepability, promotion_disposition = _bridge_output_classification(
            mode="memory_quality_evaluation_plan",
            routing_decision="memory_quality_evaluation_plan",
        )
        evidence_summary = "status=memory_quality_evaluation_plan dimensions=5"
        record_event(
            state_db,
            event_type="tool_result_received",
            component="researcher_bridge",
            summary="Researcher bridge answered a persistent memory quality evaluation plan request.",
            run_id=run_id,
            request_id=request_id,
            trace_ref=trace_ref,
            channel_id=channel_kind,
            session_id=session_id,
            human_id=human_id,
            agent_id=agent_id,
            actor_id="researcher_bridge",
            reason_code="memory_quality_evaluation_plan",
            facts=_bridge_event_facts(
                routing_decision="memory_quality_evaluation_plan",
                bridge_mode="memory_quality_evaluation_plan",
                evidence_summary=evidence_summary,
                active_chip_key=None,
                active_chip_task_type=None,
                active_chip_evaluate_used=False,
                keepability=output_keepability,
                promotion_disposition=promotion_disposition,
                extra={
                    "query_text": str(user_message or "").strip(),
                    **plan_facts,
                },
            ),
        )
        return ResearcherBridgeResult(
            request_id=request_id,
            reply_text=reply_text,
            evidence_summary=evidence_summary,
            escalation_hint=None,
            trace_ref=trace_ref,
            mode="memory_quality_evaluation_plan",
            runtime_root=None,
            config_path=None,
            attachment_context=attachment_context,
            routing_decision="memory_quality_evaluation_plan",
            active_chip_key=None,
            active_chip_task_type=None,
            active_chip_evaluate_used=False,
            output_keepability=output_keepability,
            promotion_disposition=promotion_disposition,
        )

    if not personality_context_extra and looks_like_memory_quality_dashboard_operator_query(user_message):
        trace_ref = f"trace:{agent_id}:{human_id}:{request_id}"
        dashboard = build_memory_quality_dashboard_operator_reply(
            config_manager=config_manager,
            state_db=state_db,
            user_message=user_message,
        )
        output_keepability, promotion_disposition = _bridge_output_classification(
            mode="memory_quality_dashboard_operator",
            routing_decision="memory_quality_dashboard_operator",
        )
        evidence_summary = (
            "status=memory_quality_dashboard_operator "
            f"target_repo={dashboard.facts.get('target_repo') or 'unknown'} "
            f"url={dashboard.facts.get('url') or 'unknown'}"
        )
        record_event(
            state_db,
            event_type="tool_result_received",
            component="researcher_bridge",
            summary="Researcher bridge answered a memory-quality dashboard operator launch query.",
            run_id=run_id,
            request_id=request_id,
            trace_ref=trace_ref,
            channel_id=channel_kind,
            session_id=session_id,
            human_id=human_id,
            agent_id=agent_id,
            actor_id="researcher_bridge",
            reason_code="memory_quality_dashboard_operator",
            facts=_bridge_event_facts(
                routing_decision="memory_quality_dashboard_operator",
                bridge_mode="memory_quality_dashboard_operator",
                evidence_summary=evidence_summary,
                active_chip_key=None,
                active_chip_task_type=None,
                active_chip_evaluate_used=False,
                keepability=output_keepability,
                promotion_disposition=promotion_disposition,
                extra={
                    "query_text": str(user_message or "").strip(),
                    **dashboard.facts,
                },
            ),
        )
        return ResearcherBridgeResult(
            request_id=request_id,
            reply_text=dashboard.reply_text,
            evidence_summary=evidence_summary,
            escalation_hint=None,
            trace_ref=trace_ref,
            mode="memory_quality_dashboard_operator",
            runtime_root=None,
            config_path=None,
            attachment_context=attachment_context,
            routing_decision="memory_quality_dashboard_operator",
            active_chip_key=None,
            active_chip_task_type=None,
            active_chip_evaluate_used=False,
            output_keepability=output_keepability,
            promotion_disposition=promotion_disposition,
        )

    if not personality_context_extra and looks_like_build_quality_review_query(user_message):
        trace_ref = f"trace:{agent_id}:{human_id}:{request_id}"
        review = build_build_quality_review_reply(
            config_manager=config_manager,
            state_db=state_db,
            user_message=user_message,
        )
        output_keepability, promotion_disposition = _bridge_output_classification(
            mode="build_quality_review_direct",
            routing_decision="build_quality_review_direct",
        )
        evidence_summary = (
            "status=build_quality_review "
            f"target_repo={review.facts.get('target_repo') or 'unknown'} "
            f"evidence_complete={'yes' if review.facts.get('evidence_complete') else 'no'}"
        )
        record_event(
            state_db,
            event_type="tool_result_received",
            component="researcher_bridge",
            summary="Researcher bridge answered a build-quality review from grounded local evidence.",
            run_id=run_id,
            request_id=request_id,
            trace_ref=trace_ref,
            channel_id=channel_kind,
            session_id=session_id,
            human_id=human_id,
            agent_id=agent_id,
            actor_id="researcher_bridge",
            reason_code="build_quality_review_direct",
            facts=_bridge_event_facts(
                routing_decision="build_quality_review_direct",
                bridge_mode="build_quality_review_direct",
                evidence_summary=evidence_summary,
                active_chip_key=None,
                active_chip_task_type=None,
                active_chip_evaluate_used=False,
                keepability=output_keepability,
                promotion_disposition=promotion_disposition,
                extra={
                    "query_text": str(user_message or "").strip(),
                    **review.facts,
                },
            ),
        )
        return ResearcherBridgeResult(
            request_id=request_id,
            reply_text=review.reply_text,
            evidence_summary=evidence_summary,
            escalation_hint=None,
            trace_ref=trace_ref,
            mode="build_quality_review_direct",
            runtime_root=None,
            config_path=None,
            attachment_context=attachment_context,
            routing_decision="build_quality_review_direct",
            active_chip_key=None,
            active_chip_task_type=None,
            active_chip_evaluate_used=False,
            output_keepability=output_keepability,
            promotion_disposition=promotion_disposition,
        )

    if not personality_context_extra and _detect_memory_cleanup_closure_query(user_message):
        trace_ref = f"trace:{agent_id}:{human_id}:{request_id}"
        reply_text, closure_facts = _build_memory_cleanup_closure_reply(
            config_manager=config_manager,
            state_db=state_db,
            human_id=human_id,
            session_id=session_id,
            channel_kind=channel_kind,
            request_id=request_id,
            user_message=user_message,
        )
        output_keepability, promotion_disposition = _bridge_output_classification(
            mode="memory_cleanup_closure_evidence",
            routing_decision="memory_cleanup_closure_evidence",
        )
        evidence_summary = (
            "status=memory_cleanup_closure_evidence "
            f"sample_available={'yes' if closure_facts.get('sample_available') else 'no'} "
            f"diagnostics={closure_facts.get('diagnostic_status') or 'unknown'}"
        )
        record_event(
            state_db,
            event_type="tool_result_received",
            component="researcher_bridge",
            summary="Researcher bridge answered cleanup closure evidence without overclaiming full review.",
            run_id=run_id,
            request_id=request_id,
            trace_ref=trace_ref,
            channel_id=channel_kind,
            session_id=session_id,
            human_id=human_id,
            agent_id=agent_id,
            actor_id="researcher_bridge",
            reason_code="memory_cleanup_closure_evidence",
            facts=_bridge_event_facts(
                routing_decision="memory_cleanup_closure_evidence",
                bridge_mode="memory_cleanup_closure_evidence",
                evidence_summary=evidence_summary,
                active_chip_key=None,
                active_chip_task_type=None,
                active_chip_evaluate_used=False,
                keepability=output_keepability,
                promotion_disposition=promotion_disposition,
                extra={
                    "query_text": str(user_message or "").strip(),
                    **closure_facts,
                },
            ),
        )
        return ResearcherBridgeResult(
            request_id=request_id,
            reply_text=reply_text,
            evidence_summary=evidence_summary,
            escalation_hint=None,
            trace_ref=trace_ref,
            mode="memory_cleanup_closure_evidence",
            runtime_root=None,
            config_path=None,
            attachment_context=attachment_context,
            routing_decision="memory_cleanup_closure_evidence",
            active_chip_key=None,
            active_chip_task_type=None,
            active_chip_evaluate_used=False,
            output_keepability=output_keepability,
            promotion_disposition=promotion_disposition,
        )

    if not personality_context_extra and _detect_active_context_status_query(user_message):
        trace_ref = f"trace:{agent_id}:{human_id}:{request_id}"
        reply_text, status_facts = _build_active_context_status_reply(
            config_manager=config_manager,
            state_db=state_db,
            human_id=human_id,
            session_id=session_id,
            channel_kind=channel_kind,
            request_id=request_id,
            user_message=user_message,
        )
        output_keepability, promotion_disposition = _bridge_output_classification(
            mode="active_context_status",
            routing_decision="active_context_status",
        )
        evidence_summary = (
            "status=active_context_status "
            f"focus_found={'yes' if status_facts.get('current_focus') else 'no'} "
            f"plan_found={'yes' if status_facts.get('current_plan') else 'no'} "
            f"diagnostics={status_facts.get('diagnostic_status') or 'unknown'}"
        )
        record_event(
            state_db,
            event_type="tool_result_received",
            component="researcher_bridge",
            summary="Researcher bridge summarized active context status from the capsule.",
            run_id=run_id,
            request_id=request_id,
            trace_ref=trace_ref,
            channel_id=channel_kind,
            session_id=session_id,
            human_id=human_id,
            agent_id=agent_id,
            actor_id="researcher_bridge",
            reason_code="active_context_status",
            facts=_bridge_event_facts(
                routing_decision="active_context_status",
                bridge_mode="active_context_status",
                evidence_summary=evidence_summary,
                active_chip_key=None,
                active_chip_task_type=None,
                active_chip_evaluate_used=False,
                keepability=output_keepability,
                promotion_disposition=promotion_disposition,
                extra={
                    "query_text": str(user_message or "").strip(),
                    **status_facts,
                },
            ),
        )
        return ResearcherBridgeResult(
            request_id=request_id,
            reply_text=reply_text,
            evidence_summary=evidence_summary,
            escalation_hint=None,
            trace_ref=trace_ref,
            mode="active_context_status",
            runtime_root=None,
            config_path=None,
            attachment_context=attachment_context,
            routing_decision="active_context_status",
            active_chip_key=None,
            active_chip_task_type=None,
            active_chip_evaluate_used=False,
            output_keepability=output_keepability,
            promotion_disposition=promotion_disposition,
            )

    if not personality_context_extra:
        detected_episodic_session_recall_query = _detect_episodic_session_recall_query(
            user_message,
            session_id=session_id,
        )
    if not personality_context_extra and detected_episodic_session_recall_query is not None:
        trace_ref = f"trace:{agent_id}:{human_id}:{request_id}"
        reply_text, episodic_facts = _build_episodic_session_recall_reply(
            state_db=state_db,
            human_id=human_id,
            query=detected_episodic_session_recall_query,
        )
        output_keepability, promotion_disposition = _bridge_output_classification(
            mode="memory_episodic_session_recall",
            routing_decision="memory_episodic_session_recall",
        )
        evidence_summary = (
            "status=memory_episodic_session_recall "
            f"scope=session "
            f"session_id={episodic_facts.get('session_id') or 'unknown'} "
            f"query_kind={episodic_facts.get('query_kind') or 'unknown'} "
            f"event_count={episodic_facts.get('event_count') or 0} "
            f"section_count={episodic_facts.get('section_count') or 0} "
            f"source={episodic_facts.get('summary_source') or 'unknown'}"
        )
        record_event(
            state_db,
            event_type="tool_result_received",
            component="researcher_bridge",
            summary="Researcher bridge answered a source-aware session episodic memory recall query.",
            run_id=run_id,
            request_id=request_id,
            trace_ref=trace_ref,
            channel_id=channel_kind,
            session_id=session_id,
            human_id=human_id,
            agent_id=agent_id,
            actor_id="researcher_bridge",
            reason_code="memory_episodic_session_recall",
            facts=_bridge_event_facts(
                routing_decision="memory_episodic_session_recall",
                bridge_mode="memory_episodic_session_recall",
                evidence_summary=evidence_summary,
                active_chip_key=None,
                active_chip_task_type=None,
                active_chip_evaluate_used=False,
                keepability=output_keepability,
                promotion_disposition=promotion_disposition,
                extra={
                    "query_text": str(user_message or "").strip(),
                    **episodic_facts,
                },
            ),
        )
        return ResearcherBridgeResult(
            request_id=request_id,
            reply_text=reply_text,
            evidence_summary=evidence_summary,
            escalation_hint=None,
            trace_ref=trace_ref,
            mode="memory_episodic_session_recall",
            runtime_root=None,
            config_path=None,
            attachment_context=attachment_context,
            routing_decision="memory_episodic_session_recall",
            active_chip_key=None,
            active_chip_task_type=None,
            active_chip_evaluate_used=False,
            output_keepability=output_keepability,
            promotion_disposition=promotion_disposition,
        )

    if not personality_context_extra:
        detected_episodic_project_recall_query = _detect_episodic_project_recall_query(user_message)
    if not personality_context_extra and detected_episodic_project_recall_query is not None:
        trace_ref = f"trace:{agent_id}:{human_id}:{request_id}"
        reply_text, episodic_facts = _build_episodic_project_recall_reply(
            state_db=state_db,
            human_id=human_id,
            query=detected_episodic_project_recall_query,
        )
        output_keepability, promotion_disposition = _bridge_output_classification(
            mode="memory_episodic_project_recall",
            routing_decision="memory_episodic_project_recall",
        )
        evidence_summary = (
            "status=memory_episodic_project_recall "
            f"scope=project "
            f"project_key={episodic_facts.get('project_key') or 'unknown'} "
            f"query_kind={episodic_facts.get('query_kind') or 'unknown'} "
            f"event_count={episodic_facts.get('event_count') or 0} "
            f"session_count={episodic_facts.get('session_count') or 0} "
            f"section_count={episodic_facts.get('section_count') or 0} "
            f"source={episodic_facts.get('summary_source') or 'unknown'}"
        )
        record_event(
            state_db,
            event_type="tool_result_received",
            component="researcher_bridge",
            summary="Researcher bridge answered a source-aware project episodic memory recall query.",
            run_id=run_id,
            request_id=request_id,
            trace_ref=trace_ref,
            channel_id=channel_kind,
            session_id=session_id,
            human_id=human_id,
            agent_id=agent_id,
            actor_id="researcher_bridge",
            reason_code="memory_episodic_project_recall",
            facts=_bridge_event_facts(
                routing_decision="memory_episodic_project_recall",
                bridge_mode="memory_episodic_project_recall",
                evidence_summary=evidence_summary,
                active_chip_key=None,
                active_chip_task_type=None,
                active_chip_evaluate_used=False,
                keepability=output_keepability,
                promotion_disposition=promotion_disposition,
                extra={
                    "query_text": str(user_message or "").strip(),
                    **episodic_facts,
                },
            ),
        )
        return ResearcherBridgeResult(
            request_id=request_id,
            reply_text=reply_text,
            evidence_summary=evidence_summary,
            escalation_hint=None,
            trace_ref=trace_ref,
            mode="memory_episodic_project_recall",
            runtime_root=None,
            config_path=None,
            attachment_context=attachment_context,
            routing_decision="memory_episodic_project_recall",
            active_chip_key=None,
            active_chip_task_type=None,
            active_chip_evaluate_used=False,
            output_keepability=output_keepability,
            promotion_disposition=promotion_disposition,
        )

    if not personality_context_extra:
        detected_episodic_daily_recall_query = _detect_episodic_daily_recall_query(user_message)
    if not personality_context_extra and detected_episodic_daily_recall_query is not None:
        trace_ref = f"trace:{agent_id}:{human_id}:{request_id}"
        reply_text, episodic_facts = _build_episodic_daily_recall_reply(
            state_db=state_db,
            human_id=human_id,
            query=detected_episodic_daily_recall_query,
        )
        output_keepability, promotion_disposition = _bridge_output_classification(
            mode="memory_episodic_daily_recall",
            routing_decision="memory_episodic_daily_recall",
        )
        evidence_summary = (
            "status=memory_episodic_daily_recall "
            f"scope=daily "
            f"day={episodic_facts.get('day') or 'unknown'} "
            f"query_kind={episodic_facts.get('query_kind') or 'unknown'} "
            f"event_count={episodic_facts.get('event_count') or 0} "
            f"session_count={episodic_facts.get('session_count') or 0} "
            f"section_count={episodic_facts.get('section_count') or 0} "
            f"source={episodic_facts.get('summary_source') or 'unknown'}"
        )
        record_event(
            state_db,
            event_type="tool_result_received",
            component="researcher_bridge",
            summary="Researcher bridge answered a source-aware daily episodic memory recall query.",
            run_id=run_id,
            request_id=request_id,
            trace_ref=trace_ref,
            channel_id=channel_kind,
            session_id=session_id,
            human_id=human_id,
            agent_id=agent_id,
            actor_id="researcher_bridge",
            reason_code="memory_episodic_daily_recall",
            facts=_bridge_event_facts(
                routing_decision="memory_episodic_daily_recall",
                bridge_mode="memory_episodic_daily_recall",
                evidence_summary=evidence_summary,
                active_chip_key=None,
                active_chip_task_type=None,
                active_chip_evaluate_used=False,
                keepability=output_keepability,
                promotion_disposition=promotion_disposition,
                extra={
                    "query_text": str(user_message or "").strip(),
                    **episodic_facts,
                },
            ),
        )
        return ResearcherBridgeResult(
            request_id=request_id,
            reply_text=reply_text,
            evidence_summary=evidence_summary,
            escalation_hint=None,
            trace_ref=trace_ref,
            mode="memory_episodic_daily_recall",
            runtime_root=None,
            config_path=None,
            attachment_context=attachment_context,
            routing_decision="memory_episodic_daily_recall",
            active_chip_key=None,
            active_chip_task_type=None,
            active_chip_evaluate_used=False,
            output_keepability=output_keepability,
            promotion_disposition=promotion_disposition,
        )

    if not personality_context_extra and _detect_open_ended_memory_next_step_query(user_message):
        trace_ref = f"trace:{agent_id}:{human_id}:{request_id}"
        reply_text, kernel_facts = _build_open_ended_memory_next_step_reply(
            config_manager=config_manager,
            state_db=state_db,
            human_id=human_id,
            session_id=session_id,
            request_id=request_id,
            user_message=user_message,
        )
        if reply_text:
            output_keepability, promotion_disposition = _bridge_output_classification(
                mode="memory_kernel_next_step",
                routing_decision="memory_kernel_next_step",
            )
            evidence_summary = (
                "status=memory_kernel_next_step "
                f"focus_found={'yes' if kernel_facts.get('current_focus') else 'no'} "
                f"read_method={kernel_facts.get('focus_read_method') or 'unknown'} "
                f"source_class={kernel_facts.get('focus_source_class') or 'unknown'}"
            )
            record_event(
                state_db,
                event_type="tool_result_received",
                component="researcher_bridge",
                summary="Researcher bridge answered an open-ended next-step query through the memory kernel.",
                run_id=run_id,
                request_id=request_id,
                trace_ref=trace_ref,
                channel_id=channel_kind,
                session_id=session_id,
                human_id=human_id,
                agent_id=agent_id,
                actor_id="researcher_bridge",
                reason_code="memory_kernel_next_step",
                facts=_bridge_event_facts(
                    routing_decision="memory_kernel_next_step",
                    bridge_mode="memory_kernel_next_step",
                    evidence_summary=evidence_summary,
                    active_chip_key=None,
                    active_chip_task_type=None,
                    active_chip_evaluate_used=False,
                    keepability=output_keepability,
                    promotion_disposition=promotion_disposition,
                    extra={
                        "query_text": str(user_message or "").strip(),
                        **kernel_facts,
                    },
                ),
            )
            return ResearcherBridgeResult(
                request_id=request_id,
                reply_text=reply_text,
                evidence_summary=evidence_summary,
                escalation_hint=None,
                trace_ref=trace_ref,
                mode="memory_kernel_next_step",
                runtime_root=None,
                config_path=None,
                attachment_context=attachment_context,
                routing_decision="memory_kernel_next_step",
                active_chip_key=None,
                active_chip_task_type=None,
                active_chip_evaluate_used=False,
                output_keepability=output_keepability,
                promotion_disposition=promotion_disposition,
            )

    if not personality_context_extra and _detect_memory_cleanup_sample_query(user_message):
        trace_ref = f"trace:{agent_id}:{human_id}:{request_id}"
        reply_text, sample_facts = _build_memory_cleanup_sample_reply(
            state_db=state_db,
            human_id=human_id,
        )
        output_keepability, promotion_disposition = _bridge_output_classification(
            mode="memory_cleanup_sample",
            routing_decision="memory_cleanup_sample",
        )
        evidence_summary = (
            "status=memory_cleanup_sample "
            f"sample_available={'yes' if sample_facts.get('sample_available') else 'no'}"
        )
        record_event(
            state_db,
            event_type="tool_result_received",
            component="researcher_bridge",
            summary="Researcher bridge answered a memory cleanup sample request.",
            run_id=run_id,
            request_id=request_id,
            trace_ref=trace_ref,
            channel_id=channel_kind,
            session_id=session_id,
            human_id=human_id,
            agent_id=agent_id,
            actor_id="researcher_bridge",
            reason_code="memory_cleanup_sample",
            facts=_bridge_event_facts(
                routing_decision="memory_cleanup_sample",
                bridge_mode="memory_cleanup_sample",
                evidence_summary=evidence_summary,
                active_chip_key=None,
                active_chip_task_type=None,
                active_chip_evaluate_used=False,
                keepability=output_keepability,
                promotion_disposition=promotion_disposition,
                extra={
                    "query_text": str(user_message or "").strip(),
                    **sample_facts,
                },
            ),
        )
        return ResearcherBridgeResult(
            request_id=request_id,
            reply_text=reply_text,
            evidence_summary=evidence_summary,
            escalation_hint=None,
            trace_ref=trace_ref,
            mode="memory_cleanup_sample",
            runtime_root=None,
            config_path=None,
            attachment_context=attachment_context,
            routing_decision="memory_cleanup_sample",
            active_chip_key=None,
            active_chip_task_type=None,
            active_chip_evaluate_used=False,
            output_keepability=output_keepability,
            promotion_disposition=promotion_disposition,
        )

    plan_transition = _detect_current_plan_transition_command(memory_user_message)
    if plan_transition is not None and config_manager.get_path("spark.memory.enabled", default=False):
        write_result = write_profile_fact_to_memory(
            config_manager=config_manager,
            state_db=state_db,
            human_id=human_id,
            predicate="profile.current_plan",
            value=plan_transition,
            evidence_text=str(user_message or "").strip(),
            fact_name="profile_current_plan",
            session_id=session_id,
            turn_id=request_id,
            channel_kind=channel_kind,
            actor_id="current_plan_transition_command",
        )
        if write_result.accepted_count > 0:
            output_keepability, promotion_disposition = _bridge_output_classification(
                mode="current_plan_transition",
                routing_decision="current_plan_transition",
            )
            trace_ref = f"trace:{agent_id}:{human_id}:{request_id}"
            reply_text = f"Done. Your current plan is now: {plan_transition}."
            evidence_summary = "status=current_plan_transition plan_updated=yes"
            record_event(
                state_db,
                event_type="tool_result_received",
                component="researcher_bridge",
                summary="Researcher bridge set a new current plan.",
                run_id=run_id,
                request_id=request_id,
                trace_ref=trace_ref,
                channel_id=channel_kind,
                session_id=session_id,
                human_id=human_id,
                agent_id=agent_id,
                actor_id="researcher_bridge",
                reason_code="current_plan_transition",
                facts=_bridge_event_facts(
                    routing_decision="current_plan_transition",
                    bridge_mode="current_plan_transition",
                    evidence_summary=evidence_summary,
                    active_chip_key=None,
                    active_chip_task_type=None,
                    active_chip_evaluate_used=False,
                    keepability=output_keepability,
                    promotion_disposition=promotion_disposition,
                    extra={
                        "query_text": str(user_message or "").strip(),
                        "new_plan": plan_transition,
                        "predicate": "profile.current_plan",
                    },
                ),
            )
            return ResearcherBridgeResult(
                request_id=request_id,
                reply_text=reply_text,
                evidence_summary=evidence_summary,
                escalation_hint=None,
                trace_ref=trace_ref,
                mode="current_plan_transition",
                runtime_root=None,
                config_path=None,
                attachment_context=attachment_context,
                routing_decision="current_plan_transition",
                active_chip_key=None,
                active_chip_task_type=None,
                active_chip_evaluate_used=False,
                output_keepability=output_keepability,
                promotion_disposition=promotion_disposition,
            )

    focus_transition = _detect_current_focus_transition_command(memory_user_message)
    if focus_transition is not None and config_manager.get_path("spark.memory.enabled", default=False):
        closed_focus, new_focus = focus_transition
        write_result = write_profile_fact_to_memory(
            config_manager=config_manager,
            state_db=state_db,
            human_id=human_id,
            predicate="profile.current_focus",
            value=new_focus,
            evidence_text=str(user_message or "").strip(),
            fact_name="profile_current_focus",
            session_id=session_id,
            turn_id=request_id,
            channel_kind=channel_kind,
            actor_id="current_focus_transition_command",
        )
        if write_result.accepted_count > 0:
            output_keepability, promotion_disposition = _bridge_output_classification(
                mode="current_focus_transition",
                routing_decision="current_focus_transition",
            )
            trace_ref = f"trace:{agent_id}:{human_id}:{request_id}"
            lines = []
            if closed_focus:
                lines.append(f"Done. {closed_focus} is closed.")
                lines.append("")
            lines.append(f"New focus: {new_focus}.")
            lines.extend(
                [
                    "",
                    "Next",
                    "- Evaluate whether Spark recalls this new focus across turns.",
                    "- Test whether it can explain what was closed without confusing old workflow state for current state.",
                ]
            )
            reply_text = "\n".join(lines)
            evidence_summary = "status=current_focus_transition focus_updated=yes"
            record_event(
                state_db,
                event_type="tool_result_received",
                component="researcher_bridge",
                summary="Researcher bridge closed an old focus label and set a new current focus.",
                run_id=run_id,
                request_id=request_id,
                trace_ref=trace_ref,
                channel_id=channel_kind,
                session_id=session_id,
                human_id=human_id,
                agent_id=agent_id,
                actor_id="researcher_bridge",
                reason_code="current_focus_transition",
                facts=_bridge_event_facts(
                    routing_decision="current_focus_transition",
                    bridge_mode="current_focus_transition",
                    evidence_summary=evidence_summary,
                    active_chip_key=None,
                    active_chip_task_type=None,
                    active_chip_evaluate_used=False,
                    keepability=output_keepability,
                    promotion_disposition=promotion_disposition,
                    extra={
                        "query_text": str(user_message or "").strip(),
                        "closed_focus": closed_focus,
                        "new_focus": new_focus,
                        "predicate": "profile.current_focus",
                    },
                ),
            )
            return ResearcherBridgeResult(
                request_id=request_id,
                reply_text=reply_text,
                evidence_summary=evidence_summary,
                escalation_hint=None,
                trace_ref=trace_ref,
                mode="current_focus_transition",
                runtime_root=None,
                config_path=None,
                attachment_context=attachment_context,
                routing_decision="current_focus_transition",
                active_chip_key=None,
                active_chip_task_type=None,
                active_chip_evaluate_used=False,
                output_keepability=output_keepability,
                promotion_disposition=promotion_disposition,
            )

    if (
        not explicit_memory_message
        and not personality_context_extra
        and _detect_spark_systems_self_knowledge_query(user_message)
    ):
        output_keepability, promotion_disposition = _bridge_output_classification(
            mode="spark_systems_self_knowledge",
            routing_decision="spark_systems_self_knowledge",
        )
        trace_ref = f"trace:{agent_id}:{human_id}:{request_id}"
        reply_text = _build_spark_systems_self_knowledge_answer()
        evidence_summary = "status=spark_systems_self_knowledge source=starter_ecosystem_contract"
        record_event(
            state_db,
            event_type="tool_result_received",
            component="researcher_bridge",
            summary="Researcher bridge answered Spark systems self-knowledge directly.",
            run_id=run_id,
            request_id=request_id,
            trace_ref=trace_ref,
            channel_id=channel_kind,
            session_id=session_id,
            human_id=human_id,
            agent_id=agent_id,
            actor_id="researcher_bridge",
            reason_code="spark_systems_self_knowledge",
            facts=_bridge_event_facts(
                routing_decision="spark_systems_self_knowledge",
                bridge_mode="spark_systems_self_knowledge",
                evidence_summary=evidence_summary,
                active_chip_key=None,
                active_chip_task_type=None,
                active_chip_evaluate_used=False,
                keepability=output_keepability,
                promotion_disposition=promotion_disposition,
                extra={
                    "query_text": str(user_message or "").strip(),
                    "starter_modules": [
                        "spark-cli",
                        "spark-intelligence-builder",
                        "domain-chip-memory",
                        "spark-researcher",
                        "spawner-ui",
                        "spark-telegram-bot",
                    ],
                },
            ),
        )
        return ResearcherBridgeResult(
            request_id=request_id,
            reply_text=reply_text,
            evidence_summary=evidence_summary,
            escalation_hint=None,
            trace_ref=trace_ref,
            mode="spark_systems_self_knowledge",
            runtime_root=None,
            config_path=None,
            attachment_context=attachment_context,
            routing_decision="spark_systems_self_knowledge",
            active_chip_key=None,
            active_chip_task_type=None,
            active_chip_evaluate_used=False,
            output_keepability=output_keepability,
            promotion_disposition=promotion_disposition,
        )

    if not personality_context_extra and looks_like_system_registry_query(user_message):
        self_awareness_query = looks_like_self_awareness_query(user_message)
        direct_mode = "self_awareness_direct" if self_awareness_query else "system_registry_direct"
        output_keepability, promotion_disposition = _bridge_output_classification(
            mode=direct_mode,
            routing_decision=direct_mode,
        )
        trace_ref = f"trace:{agent_id}:{human_id}:{request_id}"
        if self_awareness_query:
            capsule = build_self_awareness_capsule(
                config_manager=config_manager,
                state_db=state_db,
                human_id=human_id,
                session_id=session_id,
                channel_kind=channel_kind,
                request_id=request_id,
                user_message=user_message,
            )
            wiki_appendix, wiki_evidence = _build_self_knowledge_wiki_appendix(
                config_manager=config_manager,
                state_db=state_db,
                user_message=user_message,
                actor_id="researcher_bridge",
            )
            reply_text = _append_direct_reply_section(capsule.to_text(), wiki_appendix)
            evidence_summary = f"status=self_awareness_direct source=self_awareness_capsule {wiki_evidence}".strip()
        else:
            registry_reply = build_system_registry_direct_reply(
                config_manager=config_manager,
                state_db=state_db,
                user_message=user_message,
            )
            wiki_appendix, wiki_evidence = _build_self_knowledge_wiki_appendix(
                config_manager=config_manager,
                state_db=state_db,
                user_message=user_message,
                actor_id="researcher_bridge",
            )
            reply_text = _append_direct_reply_section(registry_reply, wiki_appendix)
            evidence_summary = f"status=system_registry_direct source=verified_registry {wiki_evidence}".strip()
        record_event(
            state_db,
            event_type="tool_result_received",
            component="researcher_bridge",
            summary="Researcher bridge answered a self-knowledge query directly from grounded runtime state.",
            run_id=run_id,
            request_id=request_id,
            trace_ref=trace_ref,
            channel_id=channel_kind,
            session_id=session_id,
            human_id=human_id,
            agent_id=agent_id,
            actor_id="researcher_bridge",
            reason_code=direct_mode,
            facts=_bridge_event_facts(
                routing_decision=direct_mode,
                bridge_mode=direct_mode,
                evidence_summary=evidence_summary,
                active_chip_key=None,
                active_chip_task_type=None,
                active_chip_evaluate_used=False,
                keepability=output_keepability,
                promotion_disposition=promotion_disposition,
                extra={"query_text": str(user_message or "").strip()},
            ),
        )
        return ResearcherBridgeResult(
            request_id=request_id,
            reply_text=reply_text,
            evidence_summary=evidence_summary,
            escalation_hint=None,
            trace_ref=trace_ref,
            mode=direct_mode,
            runtime_root=None,
            config_path=None,
            attachment_context=attachment_context,
            routing_decision=direct_mode,
            active_chip_key=None,
            active_chip_task_type=None,
            active_chip_evaluate_used=False,
            output_keepability=output_keepability,
            promotion_disposition=promotion_disposition,
        )

    if not personality_context_extra and config_manager.get_path("spark.memory.enabled", default=False):
        try:
            detected_profile_fact_query = detect_profile_fact_query(user_message)
            if detected_profile_fact_query is not None:
                profile_fact_lookup = lookup_current_state_in_memory(
                    config_manager=config_manager,
                    state_db=state_db,
                    subject=f"human:{human_id}",
                    predicate=detected_profile_fact_query.predicate,
                    actor_id="researcher_bridge",
                )
                fact_value = None
                if not profile_fact_lookup.read_result.abstained and profile_fact_lookup.read_result.records:
                    fact_value = str(profile_fact_lookup.read_result.records[0].get("value") or "").strip() or None
                personality_context_extra = build_profile_fact_query_context(
                    query=detected_profile_fact_query,
                    value=fact_value,
                )
            elif detected_memory_event_query is None:
                detected_memory_event_query = detect_telegram_memory_event_query(user_message)
            if (
                detected_profile_fact_query is None
                and detected_memory_event_query is None
                and detected_entity_state_history_query is None
                and detected_entity_state_summary_query is None
            ):
                detected_entity_state_history_query = _detect_entity_state_history_query(user_message)
            if (
                detected_profile_fact_query is None
                and detected_memory_event_query is None
                and detected_entity_state_history_query is None
                and detected_entity_state_summary_query is None
            ):
                followup_history_query = _detect_entity_state_followup_history_query(
                    user_message=user_message,
                    state_db=state_db,
                    channel_kind=channel_kind,
                    session_id=session_id,
                    human_id=human_id,
                    agent_id=agent_id,
                    request_id=request_id,
                )
                if followup_history_query is not None:
                    detected_entity_state_history_query, detected_entity_state_history_followup = followup_history_query
            if (
                detected_profile_fact_query is None
                and detected_memory_event_query is None
                and detected_entity_state_history_query is None
                and detected_entity_state_summary_query is None
            ):
                detected_entity_state_summary_query = _detect_entity_state_summary_query(user_message)
            if (
                detected_profile_fact_query is None
                and detected_memory_event_query is None
                and detected_entity_state_history_query is None
                and detected_entity_state_summary_query is None
                and detected_open_memory_recall_query is None
            ):
                detected_open_memory_recall_query = _detect_open_memory_recall_query(user_message)
            if (
                detected_profile_fact_query is None
                and detected_memory_event_query is None
                and detected_entity_state_history_query is None
                and detected_entity_state_summary_query is None
                and detected_open_memory_recall_query is None
                and detected_belief_recall_query is None
            ):
                detected_belief_recall_query = _detect_belief_recall_query(user_message)
        except Exception:
            pass

    # Detect NL personality preferences and persist per-user deltas
    nl_pref_enabled = config_manager.get_path("spark.personality.nl_preference_detection", default=True)
    detected_deltas = None
    if nl_pref_enabled and not personality_context_extra:
        try:
            detected_deltas = detect_and_persist_nl_preferences(
                human_id=human_id,
                user_message=preference_detection_message,
                state_db=state_db,
                config_manager=config_manager,
                session_id=session_id,
                turn_id=request_id,
                channel_kind=channel_kind,
            )
            if detected_deltas:
                # Build acknowledgment context for the LLM
                personality_context_extra = build_preference_acknowledgment(detected_deltas)
                # Reload profile with updated deltas applied
                personality_profile = load_personality_profile(
                    human_id=human_id,
                    agent_id=agent_id,
                    state_db=state_db,
                    config_manager=config_manager,
                )
        except Exception:
            pass

    if not personality_context_extra:
        try:
            detected_profile_fact = detect_profile_fact_observation(memory_user_message)
            if detected_profile_fact is not None:
                write_profile_fact_to_memory(
                    config_manager=config_manager,
                    state_db=state_db,
                    human_id=human_id,
                    predicate=detected_profile_fact.predicate,
                    value=detected_profile_fact.value,
                    evidence_text=detected_profile_fact.evidence_text,
                    fact_name=detected_profile_fact.fact_name,
                    session_id=session_id,
                    turn_id=request_id,
                    channel_kind=channel_kind,
                )
            elif config_manager.get_path("spark.memory.enabled", default=False):
                detected_memory_event = detect_telegram_memory_event_observation(memory_user_message)
                if detected_memory_event is not None:
                    write_telegram_event_to_memory(
                        config_manager=config_manager,
                        state_db=state_db,
                        human_id=human_id,
                        predicate=detected_memory_event.predicate,
                        value=detected_memory_event.value,
                        evidence_text=detected_memory_event.evidence_text,
                        event_name=detected_memory_event.event_name,
                        session_id=session_id,
                        turn_id=request_id,
                        channel_kind=channel_kind,
                    )
                else:
                    detected_generic_memory_candidate = classify_telegram_generic_memory_candidate(memory_user_message)
                    if detected_generic_memory_candidate is not None:
                        if detected_generic_memory_candidate.operation == "delete":
                            detected_generic_memory_deletion = detect_telegram_generic_deletion(memory_user_message)
                            if detected_generic_memory_deletion is not None:
                                generic_delete_result = delete_profile_fact_from_memory(
                                    config_manager=config_manager,
                                    state_db=state_db,
                                    human_id=human_id,
                                    predicate=detected_generic_memory_deletion.predicate,
                                    evidence_text=detected_generic_memory_deletion.evidence_text,
                                    fact_name=detected_generic_memory_deletion.fact_name,
                                    session_id=session_id,
                                    turn_id=request_id,
                                    channel_kind=channel_kind,
                                    actor_id="telegram_generic_observation_loader",
                                )
                                if generic_delete_result.accepted_count <= 0:
                                    detected_generic_memory_candidate = None
                                    detected_generic_memory_deletion = None
                        else:
                            detected_generic_memory_observation = detect_telegram_generic_observation(memory_user_message)
                            if detected_generic_memory_observation is not None:
                                generic_write_result = write_profile_fact_to_memory(
                                    config_manager=config_manager,
                                    state_db=state_db,
                                    human_id=human_id,
                                    predicate=detected_generic_memory_observation.predicate,
                                    value=detected_generic_memory_observation.value,
                                    evidence_text=detected_generic_memory_observation.evidence_text,
                                    fact_name=detected_generic_memory_observation.fact_name,
                                    session_id=session_id,
                                    turn_id=request_id,
                                    channel_kind=channel_kind,
                                    actor_id="telegram_generic_observation_loader",
                                )
                                if generic_write_result.accepted_count <= 0:
                                    detected_generic_memory_candidate = None
                                    detected_generic_memory_observation = None
                    if (
                        detected_generic_memory_candidate is None
                        and detected_memory_event is None
                        and detected_profile_fact is None
                    ):
                        assessed_generic_memory_candidate = assess_telegram_generic_memory_candidate(memory_user_message)
                        if assessed_generic_memory_candidate.outcome == "drop":
                            record_event(
                                state_db,
                                event_type="memory_candidate_assessed",
                                component="researcher_bridge",
                                summary=(
                                    "Researcher bridge assessed and dropped a Telegram generic memory candidate."
                                ),
                                run_id=run_id,
                                request_id=request_id,
                                channel_id=channel_kind,
                                session_id=session_id,
                                human_id=human_id,
                                agent_id=agent_id,
                                actor_id="researcher_bridge",
                                reason_code="memory_candidate_drop",
                                facts={
                                    "message_text": str(user_message or "").strip(),
                                    "outcome": assessed_generic_memory_candidate.outcome,
                                    "reason": assessed_generic_memory_candidate.reason,
                                    "memory_role": assessed_generic_memory_candidate.memory_role,
                                    "retention_class": assessed_generic_memory_candidate.retention_class,
                                    "domain_pack": assessed_generic_memory_candidate.domain_pack,
                                    "predicate": assessed_generic_memory_candidate.predicate,
                                    "value": assessed_generic_memory_candidate.value,
                                    "operation": assessed_generic_memory_candidate.operation,
                                    "fact_name": assessed_generic_memory_candidate.fact_name,
                                    "label": assessed_generic_memory_candidate.label,
                                    **assessed_generic_memory_candidate.salience_metadata(),
                                },
                            )
                            record_policy_gate_block(
                                state_db,
                                component="researcher_bridge",
                                policy_domain="memory_salience",
                                gate_name="memory.generic_candidate",
                                source_kind="telegram_message",
                                source_ref=request_id,
                                summary="Researcher bridge rejected a Telegram generic memory candidate before persistence.",
                                action="blocked",
                                reason_code=(
                                    assessed_generic_memory_candidate.salience_decision.reason_code
                                    if assessed_generic_memory_candidate.salience_decision is not None
                                    else f"memory_candidate_{assessed_generic_memory_candidate.reason}"
                                ),
                                blocked_stage="generic_candidate_assessment",
                                input_ref=request_id,
                                severity="low",
                                run_id=run_id,
                                request_id=request_id,
                                channel_id=channel_kind,
                                session_id=session_id,
                                actor_id="researcher_bridge",
                                provenance={"memory_role": "none", "human_id": human_id},
                                facts={
                                    "message_text": str(user_message or "").strip(),
                                    "outcome": assessed_generic_memory_candidate.outcome,
                                    "reason": assessed_generic_memory_candidate.reason,
                                    "memory_role": assessed_generic_memory_candidate.memory_role,
                                    "retention_class": assessed_generic_memory_candidate.retention_class,
                                    "domain_pack": assessed_generic_memory_candidate.domain_pack,
                                    **assessed_generic_memory_candidate.salience_metadata(),
                                },
                            )
                            assessed_generic_memory_candidate = None
        except Exception:
            pass

    if assessed_generic_memory_candidate is not None:
        if assessed_generic_memory_candidate.outcome == "structured_evidence":
            try:
                write_structured_evidence_to_memory(
                    config_manager=config_manager,
                    state_db=state_db,
                    human_id=human_id,
                    evidence_text=assessed_generic_memory_candidate.evidence_text,
                    domain_pack=str(assessed_generic_memory_candidate.domain_pack or "evidence"),
                    evidence_kind=str(assessed_generic_memory_candidate.reason or "structured_evidence"),
                    session_id=session_id,
                    turn_id=request_id,
                    channel_kind=channel_kind,
                    actor_id="telegram_structured_evidence_loader",
                    salience_decision=assessed_generic_memory_candidate.salience_decision,
                )
            except Exception:
                pass
        elif assessed_generic_memory_candidate.outcome == "raw_episode":
            try:
                write_raw_episode_to_memory(
                    config_manager=config_manager,
                    state_db=state_db,
                    human_id=human_id,
                    episode_text=assessed_generic_memory_candidate.evidence_text,
                    domain_pack=str(assessed_generic_memory_candidate.domain_pack or "raw_episode"),
                    session_id=session_id,
                    turn_id=request_id,
                    channel_kind=channel_kind,
                    actor_id="telegram_raw_episode_loader",
                    salience_decision=assessed_generic_memory_candidate.salience_decision,
                )
            except Exception:
                pass
        elif assessed_generic_memory_candidate.outcome == "belief_candidate":
            try:
                write_belief_to_memory(
                    config_manager=config_manager,
                    state_db=state_db,
                    human_id=human_id,
                    belief_text=assessed_generic_memory_candidate.evidence_text,
                    domain_pack=str(assessed_generic_memory_candidate.domain_pack or "belief"),
                    belief_kind=str(assessed_generic_memory_candidate.reason or "belief_candidate"),
                    session_id=session_id,
                    turn_id=request_id,
                    channel_kind=channel_kind,
                    actor_id="telegram_belief_loader",
                    salience_decision=assessed_generic_memory_candidate.salience_decision,
                )
            except Exception:
                pass
        record_event(
            state_db,
            event_type="memory_candidate_assessed",
            component="researcher_bridge",
            summary="Researcher bridge assessed a Telegram memory candidate without promoting it to a direct memory write.",
            run_id=run_id,
            request_id=request_id,
            channel_id=channel_kind,
            session_id=session_id,
            human_id=human_id,
            agent_id=agent_id,
            actor_id="researcher_bridge",
            reason_code=f"memory_candidate_{assessed_generic_memory_candidate.outcome}",
            facts={
                "message_text": str(user_message or "").strip(),
                "outcome": assessed_generic_memory_candidate.outcome,
                "reason": assessed_generic_memory_candidate.reason,
                "memory_role": assessed_generic_memory_candidate.memory_role,
                "retention_class": assessed_generic_memory_candidate.retention_class,
                "domain_pack": assessed_generic_memory_candidate.domain_pack,
                "predicate": assessed_generic_memory_candidate.predicate,
                "value": assessed_generic_memory_candidate.value,
                "operation": assessed_generic_memory_candidate.operation,
                "fact_name": assessed_generic_memory_candidate.fact_name,
                "label": assessed_generic_memory_candidate.label,
                **assessed_generic_memory_candidate.salience_metadata(),
            },
        )

    memory_write_should_reply_via_persona = (
        bool(config_manager.get_path("spark.researcher.enabled", default=True))
        and channel_kind == "telegram"
        and not explicit_memory_message
        and not (
            detected_generic_memory_observation is not None
            and _should_direct_acknowledge_current_state_memory(memory_user_message)
        )
        and not (
            detected_generic_memory_observation is not None
            and detected_generic_memory_observation.predicate == "profile.recent_family_members"
        )
        and not (
            detected_generic_memory_observation is not None
            and detected_generic_memory_observation.predicate == "profile.current_low_stakes_test_fact"
        )
        and not (
            detected_generic_memory_observation is not None
            and detected_generic_memory_observation.predicate.startswith("entity.")
        )
        and (
            detected_profile_fact is not None
            or detected_memory_event is not None
            or detected_generic_memory_observation is not None
            or (
                assessed_generic_memory_candidate is not None
                and assessed_generic_memory_candidate.outcome in {"structured_evidence", "raw_episode", "belief_candidate"}
            )
        )
    )
    if memory_write_should_reply_via_persona:
        memory_label = "memory"
        if detected_profile_fact is not None:
            profile_label = (
                getattr(detected_profile_fact, "label", None)
                or detected_profile_fact.fact_name
                or detected_profile_fact.predicate
            )
            memory_label = f"profile fact ({profile_label})"
        elif detected_memory_event is not None:
            memory_label = f"Telegram event ({detected_memory_event.label or detected_memory_event.event_name})"
        elif detected_generic_memory_observation is not None:
            memory_label = (
                f"{detected_generic_memory_observation.label or detected_generic_memory_observation.fact_name} "
                "current-state memory"
            )
        elif assessed_generic_memory_candidate is not None:
            memory_role = str(assessed_generic_memory_candidate.memory_role or "memory")
            memory_outcome = str(assessed_generic_memory_candidate.outcome or "memory")
            memory_label = f"{memory_role} memory ({memory_outcome})"
        memory_context = (
            "[Memory write this turn]\n"
            f"Spark captured the user's message as {memory_label}. "
            "Reply to the user's actual message naturally through the persona path. "
            "Do not respond with only a filing acknowledgement like Logged, Noted, Saved, or Remembered."
        )
        personality_context_extra = (
            f"{personality_context_extra}\n\n{memory_context}"
            if personality_context_extra
            else memory_context
        )

    memory_candidate_should_reply_via_persona = (
        memory_write_should_reply_via_persona
        and assessed_generic_memory_candidate is not None
        and assessed_generic_memory_candidate.outcome in {"structured_evidence", "raw_episode", "belief_candidate"}
    )

    # Periodically trigger self-evolution based on accumulated observations
    try:
        evolved_deltas = maybe_evolve_traits(human_id=human_id, state_db=state_db)
    except Exception:
        pass

    # Record observation for self-evolution (runs on every message)
    try:
        if personality_profile and personality_profile.get("traits"):
            observation_record = record_observation(
                human_id=human_id,
                user_message=user_message,
                traits_active=personality_profile["traits"],
                state_db=state_db,
            )
    except Exception:
        pass

    if (
        personality_profile
        or personality_context_extra
        or detected_deltas
        or agent_persona_mutation
        or evolved_deltas
        or observation_record
        or detected_profile_fact
        or detected_profile_fact_query
        or detected_memory_event
        or detected_memory_event_query
        or assessed_generic_memory_candidate
        or detected_generic_memory_deletion
        or detected_generic_memory_observation
    ):
        source_kind = "personality_profile"
        if detected_deltas:
            source_kind = "personality_preference_update"
        elif agent_persona_mutation is not None:
            source_kind = "agent_persona_update"
        elif detected_profile_fact is not None:
            source_kind = "profile_fact_update"
        elif detected_profile_fact_query is not None:
            source_kind = "profile_fact_query"
        elif detected_memory_event is not None:
            source_kind = "telegram_event_update"
        elif detected_memory_event_query is not None:
            source_kind = "telegram_event_query"
        elif assessed_generic_memory_candidate is not None:
            source_kind = f"generic_memory_candidate_{assessed_generic_memory_candidate.outcome}"
        elif detected_generic_memory_deletion is not None:
            source_kind = "generic_memory_deletion"
        elif detected_generic_memory_observation is not None:
            source_kind = "generic_memory_observation"
        elif personality_query_kind != "none":
            source_kind = f"personality_query_{personality_query_kind}"
        elif evolved_deltas:
            source_kind = "personality_evolution"
        record_event(
            state_db,
            event_type="plugin_or_chip_influence_recorded",
            component="researcher_bridge",
            summary="Personality influence was recorded before bridge execution.",
            run_id=run_id,
            request_id=request_id,
            channel_id=channel_kind,
            session_id=session_id,
            human_id=human_id,
            agent_id=agent_id,
            actor_id="researcher_bridge",
            reason_code="personality_context_applied",
            facts={
                "personality_name": personality_profile.get("personality_name") if personality_profile else None,
                "personality_id": personality_profile.get("personality_id") if personality_profile else None,
                "personality_source": personality_profile.get("source") if personality_profile else None,
                "user_deltas_applied": bool(personality_profile.get("user_deltas_applied")) if personality_profile else False,
                "query_kind": personality_query_kind,
                "agent_persona_name": personality_profile.get("agent_persona_name") if personality_profile else None,
                "agent_persona_summary": personality_profile.get("agent_persona_summary") if personality_profile else None,
                "agent_behavioral_rules": personality_profile.get("agent_behavioral_rules") if personality_profile else [],
                "agent_base_traits": personality_profile.get("agent_base_traits") if personality_profile else {},
                "agent_persona_mutation": (
                    {
                        "agent_name": agent_persona_mutation.agent_name,
                        "trait_deltas": agent_persona_mutation.trait_deltas,
                        "behavioral_rules": agent_persona_mutation.behavioral_rules,
                    }
                    if agent_persona_mutation is not None
                    else {}
                ),
                "detected_deltas": detected_deltas or {},
                "detected_profile_fact": (
                    {
                        "predicate": detected_profile_fact.predicate,
                        "value": detected_profile_fact.value,
                        "operation": detected_profile_fact.operation,
                        "fact_name": detected_profile_fact.fact_name,
                    }
                    if detected_profile_fact is not None
                    else None
                ),
                "detected_profile_fact_query": (
                    {
                        "predicate": detected_profile_fact_query.predicate,
                        "fact_name": detected_profile_fact_query.fact_name,
                        "label": detected_profile_fact_query.label,
                        "query_kind": detected_profile_fact_query.query_kind,
                        "predicate_prefix": detected_profile_fact_query.predicate_prefix,
                        "message_text": str(user_message or "").strip(),
                    }
                    if detected_profile_fact_query is not None
                    else None
                ),
                "detected_memory_event": (
                    {
                        "predicate": detected_memory_event.predicate,
                        "value": detected_memory_event.value,
                        "event_name": detected_memory_event.event_name,
                        "label": detected_memory_event.label,
                    }
                    if detected_memory_event is not None
                    else None
                ),
                "detected_memory_event_query": (
                    {
                        "predicate": detected_memory_event_query.predicate,
                        "label": detected_memory_event_query.label,
                        "query_kind": detected_memory_event_query.query_kind,
                        "message_text": str(user_message or "").strip(),
                    }
                    if detected_memory_event_query is not None
                    else None
                ),
                "assessed_generic_memory_candidate": (
                    {
                        "outcome": assessed_generic_memory_candidate.outcome,
                        "reason": assessed_generic_memory_candidate.reason,
                        "memory_role": assessed_generic_memory_candidate.memory_role,
                        "retention_class": assessed_generic_memory_candidate.retention_class,
                        "domain_pack": assessed_generic_memory_candidate.domain_pack,
                        "predicate": assessed_generic_memory_candidate.predicate,
                        "value": assessed_generic_memory_candidate.value,
                        "operation": assessed_generic_memory_candidate.operation,
                        "fact_name": assessed_generic_memory_candidate.fact_name,
                        "label": assessed_generic_memory_candidate.label,
                        "message_text": str(user_message or "").strip(),
                    }
                    if assessed_generic_memory_candidate is not None
                    else None
                ),
                "detected_generic_memory_observation": (
                    {
                        "predicate": detected_generic_memory_observation.predicate,
                        "value": detected_generic_memory_observation.value,
                        "fact_name": detected_generic_memory_observation.fact_name,
                        "label": detected_generic_memory_observation.label,
                        "message_text": str(user_message or "").strip(),
                    }
                    if detected_generic_memory_observation is not None
                    else None
                ),
                "detected_generic_memory_deletion": (
                    {
                        "predicate": detected_generic_memory_deletion.predicate,
                        "fact_name": detected_generic_memory_deletion.fact_name,
                        "label": detected_generic_memory_deletion.label,
                        "message_text": str(user_message or "").strip(),
                    }
                    if detected_generic_memory_deletion is not None
                    else None
                ),
                "detected_generic_memory_candidate": (
                    {
                        "predicate": detected_generic_memory_candidate.predicate,
                        "value": detected_generic_memory_candidate.value,
                        "fact_name": detected_generic_memory_candidate.fact_name,
                        "label": detected_generic_memory_candidate.label,
                        "operation": detected_generic_memory_candidate.operation,
                        "memory_role": detected_generic_memory_candidate.memory_role,
                        "retention_class": detected_generic_memory_candidate.retention_class,
                        "domain_pack": detected_generic_memory_candidate.domain_pack,
                        "message_text": str(user_message or "").strip(),
                    }
                    if detected_generic_memory_candidate is not None
                    else None
                ),
                "evolved_deltas": evolved_deltas or {},
                "observation_state": (
                    observation_record.get("user_state")
                    if isinstance(observation_record, dict)
                    else None
                ),
                "observation_confidence": (
                    observation_record.get("confidence")
                    if isinstance(observation_record, dict)
                    else None
                ),
                "keepability": "user_preference_ephemeral",
            },
            provenance={
                "source_kind": source_kind,
                "source_ref": personality_profile.get("personality_id") if personality_profile else human_id,
            },
        )

    if detected_deltas and explicit_memory_message:
        output_keepability, promotion_disposition = _bridge_output_classification(
            mode="personality_preference_update",
            routing_decision="personality_preference_update",
        )
        trace_ref = f"trace:{agent_id}:{human_id}:{request_id}"
        reply_text = _build_direct_preference_update_answer(user_message=memory_user_message)
        evidence_summary = "status=personality_preference_update traits=" + ",".join(
            sorted(str(trait) for trait in detected_deltas)
        )
        record_event(
            state_db,
            event_type="tool_result_received",
            component="researcher_bridge",
            summary="Researcher bridge acknowledged an explicit Telegram preference memory update directly.",
            run_id=run_id,
            request_id=request_id,
            trace_ref=trace_ref,
            channel_id=channel_kind,
            session_id=session_id,
            human_id=human_id,
            agent_id=agent_id,
            actor_id="researcher_bridge",
            reason_code="personality_preference_update",
            facts=_bridge_event_facts(
                routing_decision="personality_preference_update",
                bridge_mode="personality_preference_update",
                evidence_summary=evidence_summary,
                active_chip_key=None,
                active_chip_task_type=None,
                active_chip_evaluate_used=False,
                keepability=output_keepability,
                promotion_disposition=promotion_disposition,
                extra={
                    "detected_deltas": detected_deltas,
                    "explicit_memory_message": explicit_memory_message,
                    "normalized_memory_message": memory_user_message,
                },
            ),
        )
        return ResearcherBridgeResult(
            request_id=request_id,
            reply_text=reply_text,
            evidence_summary=evidence_summary,
            escalation_hint=None,
            trace_ref=trace_ref,
            mode="personality_preference_update",
            runtime_root=None,
            config_path=None,
            attachment_context=attachment_context,
            routing_decision="personality_preference_update",
            active_chip_key=None,
            active_chip_task_type=None,
            active_chip_evaluate_used=False,
            output_keepability=output_keepability,
            promotion_disposition=promotion_disposition,
        )

    if detected_profile_fact is not None and not memory_write_should_reply_via_persona:
        output_keepability, promotion_disposition = _bridge_output_classification(
            mode="memory_profile_fact_update",
            routing_decision="memory_profile_fact_observation",
        )
        trace_ref = f"trace:{agent_id}:{human_id}:{request_id}"
        reply_text = build_profile_fact_observation_answer(observation=detected_profile_fact)
        evidence_summary = (
            "status=memory_profile_fact_update "
            f"predicate={detected_profile_fact.predicate or 'unknown'}"
        )
        record_event(
            state_db,
            event_type="tool_result_received",
            component="researcher_bridge",
            summary="Researcher bridge acknowledged a profile fact update directly from memory.",
            run_id=run_id,
            request_id=request_id,
            trace_ref=trace_ref,
            channel_id=channel_kind,
            session_id=session_id,
            human_id=human_id,
            agent_id=agent_id,
            actor_id="researcher_bridge",
            reason_code="memory_profile_fact_observation",
            facts=_bridge_event_facts(
                routing_decision="memory_profile_fact_observation",
                bridge_mode="memory_profile_fact_update",
                evidence_summary=evidence_summary,
                active_chip_key=None,
                active_chip_task_type=None,
                active_chip_evaluate_used=False,
                keepability=output_keepability,
                promotion_disposition=promotion_disposition,
                extra={
                    "fact_name": detected_profile_fact.fact_name,
                    "predicate": detected_profile_fact.predicate,
                    "value": detected_profile_fact.value,
                    "operation": detected_profile_fact.operation,
                },
            ),
        )
        return ResearcherBridgeResult(
            request_id=request_id,
            reply_text=reply_text,
            evidence_summary=evidence_summary,
            escalation_hint=None,
            trace_ref=trace_ref,
            mode="memory_profile_fact_update",
            runtime_root=None,
            config_path=None,
            attachment_context=attachment_context,
            routing_decision="memory_profile_fact_observation",
            active_chip_key=None,
            active_chip_task_type=None,
            active_chip_evaluate_used=False,
            output_keepability=output_keepability,
            promotion_disposition=promotion_disposition,
        )

    if detected_memory_event is not None and not memory_write_should_reply_via_persona:
        output_keepability, promotion_disposition = _bridge_output_classification(
            mode="memory_telegram_event_update",
            routing_decision="memory_telegram_event_observation",
        )
        trace_ref = f"trace:{agent_id}:{human_id}:{request_id}"
        reply_text = build_telegram_memory_event_observation_answer(observation=detected_memory_event)
        evidence_summary = (
            "status=memory_telegram_event_update "
            f"predicate={detected_memory_event.predicate or 'unknown'}"
        )
        record_event(
            state_db,
            event_type="tool_result_received",
            component="researcher_bridge",
            summary="Researcher bridge acknowledged a Telegram event update directly from memory.",
            run_id=run_id,
            request_id=request_id,
            trace_ref=trace_ref,
            channel_id=channel_kind,
            session_id=session_id,
            human_id=human_id,
            agent_id=agent_id,
            actor_id="researcher_bridge",
            reason_code="memory_telegram_event_observation",
            facts=_bridge_event_facts(
                routing_decision="memory_telegram_event_observation",
                bridge_mode="memory_telegram_event_update",
                evidence_summary=evidence_summary,
                active_chip_key=None,
                active_chip_task_type=None,
                active_chip_evaluate_used=False,
                keepability=output_keepability,
                promotion_disposition=promotion_disposition,
                extra={
                    "event_name": detected_memory_event.event_name,
                    "predicate": detected_memory_event.predicate,
                    "value": detected_memory_event.value,
                    "label": detected_memory_event.label,
                },
            ),
        )
        return ResearcherBridgeResult(
            request_id=request_id,
            reply_text=reply_text,
            evidence_summary=evidence_summary,
            escalation_hint=None,
            trace_ref=trace_ref,
            mode="memory_telegram_event_update",
            runtime_root=None,
            config_path=None,
            attachment_context=attachment_context,
            routing_decision="memory_telegram_event_observation",
            active_chip_key=None,
            active_chip_task_type=None,
            active_chip_evaluate_used=False,
            output_keepability=output_keepability,
            promotion_disposition=promotion_disposition,
        )

    if detected_generic_memory_observation is not None and not memory_write_should_reply_via_persona:
        output_keepability, promotion_disposition = _bridge_output_classification(
            mode="memory_generic_observation_update",
            routing_decision="memory_generic_observation",
        )
        trace_ref = f"trace:{agent_id}:{human_id}:{request_id}"
        reply_text = build_telegram_generic_observation_answer(
            observation=detected_generic_memory_observation
        )
        evidence_summary = (
            "status=memory_generic_observation_update "
            f"predicate={detected_generic_memory_observation.predicate or 'unknown'}"
        )
        record_event(
            state_db,
            event_type="tool_result_received",
            component="researcher_bridge",
            summary="Researcher bridge acknowledged a generic Telegram memory observation directly from memory.",
            run_id=run_id,
            request_id=request_id,
            trace_ref=trace_ref,
            channel_id=channel_kind,
            session_id=session_id,
            human_id=human_id,
            agent_id=agent_id,
            actor_id="researcher_bridge",
            reason_code="memory_generic_observation",
            facts=_bridge_event_facts(
                routing_decision="memory_generic_observation",
                bridge_mode="memory_generic_observation_update",
                evidence_summary=evidence_summary,
                active_chip_key=None,
                active_chip_task_type=None,
                active_chip_evaluate_used=False,
                keepability=output_keepability,
                promotion_disposition=promotion_disposition,
                extra={
                    "fact_name": detected_generic_memory_observation.fact_name,
                    "predicate": detected_generic_memory_observation.predicate,
                    "value": detected_generic_memory_observation.value,
                    "label": detected_generic_memory_observation.label,
                    "memory_role": (
                        detected_generic_memory_candidate.memory_role
                        if detected_generic_memory_candidate is not None
                        else "current_state"
                    ),
                    "retention_class": (
                        detected_generic_memory_candidate.retention_class
                        if detected_generic_memory_candidate is not None
                        else None
                    ),
                    "domain_pack": (
                        detected_generic_memory_candidate.domain_pack
                        if detected_generic_memory_candidate is not None
                        else None
                    ),
                    "operation": "update",
                },
            ),
        )
        return ResearcherBridgeResult(
            request_id=request_id,
            reply_text=reply_text,
            evidence_summary=evidence_summary,
            escalation_hint=None,
            trace_ref=trace_ref,
            mode="memory_generic_observation_update",
            runtime_root=None,
            config_path=None,
            attachment_context=attachment_context,
            routing_decision="memory_generic_observation",
            active_chip_key=None,
            active_chip_task_type=None,
            active_chip_evaluate_used=False,
            output_keepability=output_keepability,
            promotion_disposition=promotion_disposition,
        )

    if detected_generic_memory_deletion is not None:
        output_keepability, promotion_disposition = _bridge_output_classification(
            mode="memory_generic_observation_delete",
            routing_decision="memory_generic_observation_delete",
        )
        trace_ref = f"trace:{agent_id}:{human_id}:{request_id}"
        reply_text = build_telegram_generic_deletion_answer(
            deletion=detected_generic_memory_deletion
        )
        evidence_summary = (
            "status=memory_generic_observation_delete "
            f"predicate={detected_generic_memory_deletion.predicate or 'unknown'}"
        )
        record_event(
            state_db,
            event_type="tool_result_received",
            component="researcher_bridge",
            summary="Researcher bridge acknowledged a generic Telegram memory deletion directly from memory.",
            run_id=run_id,
            request_id=request_id,
            trace_ref=trace_ref,
            channel_id=channel_kind,
            session_id=session_id,
            human_id=human_id,
            agent_id=agent_id,
            actor_id="researcher_bridge",
            reason_code="memory_generic_observation_delete",
            facts=_bridge_event_facts(
                routing_decision="memory_generic_observation_delete",
                bridge_mode="memory_generic_observation_delete",
                evidence_summary=evidence_summary,
                active_chip_key=None,
                active_chip_task_type=None,
                active_chip_evaluate_used=False,
                keepability=output_keepability,
                promotion_disposition=promotion_disposition,
                extra={
                    "fact_name": detected_generic_memory_deletion.fact_name,
                    "predicate": detected_generic_memory_deletion.predicate,
                    "label": detected_generic_memory_deletion.label,
                    "memory_role": (
                        detected_generic_memory_candidate.memory_role
                        if detected_generic_memory_candidate is not None
                        else "current_state"
                    ),
                    "retention_class": (
                        detected_generic_memory_candidate.retention_class
                        if detected_generic_memory_candidate is not None
                        else None
                    ),
                    "domain_pack": (
                        detected_generic_memory_candidate.domain_pack
                        if detected_generic_memory_candidate is not None
                        else None
                    ),
                    "operation": "delete",
                },
            ),
        )
        return ResearcherBridgeResult(
            request_id=request_id,
            reply_text=reply_text,
            evidence_summary=evidence_summary,
            escalation_hint=None,
            trace_ref=trace_ref,
            mode="memory_generic_observation_delete",
            runtime_root=None,
            config_path=None,
            attachment_context=attachment_context,
            routing_decision="memory_generic_observation_delete",
            active_chip_key=None,
            active_chip_task_type=None,
            active_chip_evaluate_used=False,
            output_keepability=output_keepability,
            promotion_disposition=promotion_disposition,
        )

    if (
        assessed_generic_memory_candidate is not None
        and assessed_generic_memory_candidate.outcome == "structured_evidence"
        and not memory_candidate_should_reply_via_persona
    ):
        output_keepability, promotion_disposition = _bridge_output_classification(
            mode="memory_structured_evidence_update",
            routing_decision="memory_structured_evidence_observation",
        )
        trace_ref = f"trace:{agent_id}:{human_id}:{request_id}"
        reply_text = _build_structured_evidence_observation_answer(
            evidence_text=assessed_generic_memory_candidate.evidence_text,
        )
        evidence_summary = (
            "status=memory_structured_evidence_update "
            f"domain_pack={assessed_generic_memory_candidate.domain_pack or 'evidence'} "
            f"evidence_kind={assessed_generic_memory_candidate.reason or 'structured_evidence'}"
        )
        record_event(
            state_db,
            event_type="tool_result_received",
            component="researcher_bridge",
            summary="Researcher bridge acknowledged structured evidence directly from memory.",
            run_id=run_id,
            request_id=request_id,
            trace_ref=trace_ref,
            channel_id=channel_kind,
            session_id=session_id,
            human_id=human_id,
            agent_id=agent_id,
            actor_id="researcher_bridge",
            reason_code="memory_structured_evidence_observation",
            facts=_bridge_event_facts(
                routing_decision="memory_structured_evidence_observation",
                bridge_mode="memory_structured_evidence_update",
                evidence_summary=evidence_summary,
                active_chip_key=None,
                active_chip_task_type=None,
                active_chip_evaluate_used=False,
                keepability=output_keepability,
                promotion_disposition=promotion_disposition,
                extra={
                    "memory_role": assessed_generic_memory_candidate.memory_role,
                    "retention_class": assessed_generic_memory_candidate.retention_class,
                    "domain_pack": assessed_generic_memory_candidate.domain_pack,
                    "evidence_kind": assessed_generic_memory_candidate.reason,
                    "evidence_text": assessed_generic_memory_candidate.evidence_text,
                    "operation": assessed_generic_memory_candidate.operation,
                },
            ),
        )
        return ResearcherBridgeResult(
            request_id=request_id,
            reply_text=reply_text,
            evidence_summary=evidence_summary,
            escalation_hint=None,
            trace_ref=trace_ref,
            mode="memory_structured_evidence_update",
            runtime_root=None,
            config_path=None,
            attachment_context=attachment_context,
            routing_decision="memory_structured_evidence_observation",
            active_chip_key=None,
            active_chip_task_type=None,
            active_chip_evaluate_used=False,
            output_keepability=output_keepability,
            promotion_disposition=promotion_disposition,
        )

    if (
        assessed_generic_memory_candidate is not None
        and assessed_generic_memory_candidate.outcome == "raw_episode"
        and not memory_candidate_should_reply_via_persona
    ):
        output_keepability, promotion_disposition = _bridge_output_classification(
            mode="memory_raw_episode_update",
            routing_decision="memory_raw_episode_observation",
        )
        trace_ref = f"trace:{agent_id}:{human_id}:{request_id}"
        reply_text = _build_raw_episode_observation_answer(
            episode_text=assessed_generic_memory_candidate.evidence_text,
        )
        evidence_summary = (
            "status=memory_raw_episode_update "
            f"domain_pack={assessed_generic_memory_candidate.domain_pack or 'raw_episode'}"
        )
        record_event(
            state_db,
            event_type="tool_result_received",
            component="researcher_bridge",
            summary="Researcher bridge acknowledged a raw episode directly from memory.",
            run_id=run_id,
            request_id=request_id,
            trace_ref=trace_ref,
            channel_id=channel_kind,
            session_id=session_id,
            human_id=human_id,
            agent_id=agent_id,
            actor_id="researcher_bridge",
            reason_code="memory_raw_episode_observation",
            facts=_bridge_event_facts(
                routing_decision="memory_raw_episode_observation",
                bridge_mode="memory_raw_episode_update",
                evidence_summary=evidence_summary,
                active_chip_key=None,
                active_chip_task_type=None,
                active_chip_evaluate_used=False,
                keepability=output_keepability,
                promotion_disposition=promotion_disposition,
                extra={
                    "memory_role": assessed_generic_memory_candidate.memory_role,
                    "retention_class": assessed_generic_memory_candidate.retention_class,
                    "domain_pack": assessed_generic_memory_candidate.domain_pack,
                    "episode_text": assessed_generic_memory_candidate.evidence_text,
                    "operation": assessed_generic_memory_candidate.operation,
                },
            ),
        )
        return ResearcherBridgeResult(
            request_id=request_id,
            reply_text=reply_text,
            evidence_summary=evidence_summary,
            escalation_hint=None,
            trace_ref=trace_ref,
            mode="memory_raw_episode_update",
            runtime_root=None,
            config_path=None,
            attachment_context=attachment_context,
            routing_decision="memory_raw_episode_observation",
            active_chip_key=None,
            active_chip_task_type=None,
            active_chip_evaluate_used=False,
            output_keepability=output_keepability,
            promotion_disposition=promotion_disposition,
        )

    if (
        assessed_generic_memory_candidate is not None
        and assessed_generic_memory_candidate.outcome == "belief_candidate"
        and not memory_candidate_should_reply_via_persona
    ):
        output_keepability, promotion_disposition = _bridge_output_classification(
            mode="memory_belief_update",
            routing_decision="memory_belief_observation",
        )
        trace_ref = f"trace:{agent_id}:{human_id}:{request_id}"
        reply_text = _build_belief_observation_answer(
            belief_text=assessed_generic_memory_candidate.evidence_text,
        )
        evidence_summary = (
            "status=memory_belief_update "
            f"domain_pack={assessed_generic_memory_candidate.domain_pack or 'belief'} "
            f"belief_kind={assessed_generic_memory_candidate.reason or 'belief_candidate'}"
        )
        record_event(
            state_db,
            event_type="tool_result_received",
            component="researcher_bridge",
            summary="Researcher bridge acknowledged a Telegram belief observation directly from memory.",
            run_id=run_id,
            request_id=request_id,
            trace_ref=trace_ref,
            channel_id=channel_kind,
            session_id=session_id,
            human_id=human_id,
            agent_id=agent_id,
            actor_id="researcher_bridge",
            reason_code="memory_belief_observation",
            facts=_bridge_event_facts(
                routing_decision="memory_belief_observation",
                bridge_mode="memory_belief_update",
                evidence_summary=evidence_summary,
                active_chip_key=None,
                active_chip_task_type=None,
                active_chip_evaluate_used=False,
                keepability=output_keepability,
                promotion_disposition=promotion_disposition,
                extra={
                    "memory_role": assessed_generic_memory_candidate.memory_role,
                    "retention_class": assessed_generic_memory_candidate.retention_class,
                    "domain_pack": assessed_generic_memory_candidate.domain_pack,
                    "belief_kind": assessed_generic_memory_candidate.reason,
                    "belief_text": assessed_generic_memory_candidate.evidence_text,
                    "operation": assessed_generic_memory_candidate.operation,
                },
            ),
        )
        return ResearcherBridgeResult(
            request_id=request_id,
            reply_text=reply_text,
            evidence_summary=evidence_summary,
            escalation_hint=None,
            trace_ref=trace_ref,
            mode="memory_belief_update",
            runtime_root=None,
            config_path=None,
            attachment_context=attachment_context,
            routing_decision="memory_belief_observation",
            active_chip_key=None,
            active_chip_task_type=None,
            active_chip_evaluate_used=False,
            output_keepability=output_keepability,
            promotion_disposition=promotion_disposition,
        )

    if (
        detected_profile_fact_query is not None
        and detected_profile_fact_query.query_kind == "fact_explanation"
    ):
        memory_subject = human_id if str(human_id or "").startswith("human:") else f"human:{human_id}"
        direct_fact_question = str(user_message or "").strip() or f"How do you know my {detected_profile_fact_query.label}?"
        direct_fact_read_method = "explain_answer"
        explanation_payload: dict[str, Any] = {}
        related_predicates = _profile_fact_query_related_predicates(detected_profile_fact_query.predicate)
        explanation_related_predicates: tuple[str, ...] = related_predicates
        direct_fact_explanation = explain_memory_answer_in_memory(
            config_manager=config_manager,
            state_db=state_db,
            subject=memory_subject,
            predicate=str(detected_profile_fact_query.predicate or ""),
            question=direct_fact_question,
            actor_id="researcher_bridge",
        )
        explanation_payload = direct_fact_explanation.read_result.answer_explanation or {}

        if not _profile_fact_explanation_has_content(explanation_payload):
            primary_records, related_records = _inspect_profile_fact_records(
                config_manager=config_manager,
                state_db=state_db,
                human_id=human_id,
                predicate=detected_profile_fact_query.predicate,
                related_predicates=explanation_related_predicates,
                actor_id="researcher_bridge",
            )
            fallback_answer = _select_profile_fact_query_value(
                predicate=detected_profile_fact_query.predicate,
                primary_records=primary_records,
                related_records=related_records,
            )
            if fallback_answer:
                explanation_payload = {"answer": fallback_answer}
                direct_fact_read_method = (
                    "inspect_current_state(+related)" if related_records else "inspect_current_state"
                )
        if not _profile_fact_explanation_has_content(explanation_payload):
            evidence_lookup = retrieve_memory_evidence_in_memory(
                config_manager=config_manager,
                state_db=state_db,
                query=direct_fact_question,
                subject=memory_subject,
                limit=6,
                actor_id="researcher_bridge",
            )
            if not evidence_lookup.read_result.abstained and evidence_lookup.read_result.records:
                primary_records, related_records = _partition_profile_fact_records(
                    records=evidence_lookup.read_result.records,
                    predicate=detected_profile_fact_query.predicate,
                    related_predicates=explanation_related_predicates,
                )
                fallback_answer = _select_profile_fact_query_value(
                    predicate=detected_profile_fact_query.predicate,
                    primary_records=primary_records,
                    related_records=related_records,
                )
                if fallback_answer:
                    evidence_items = []
                    for record in [*primary_records, *related_records]:
                        evidence_text = str(record.get("text") or "").strip()
                        if evidence_text:
                            evidence_items.append({"text": evidence_text})
                    explanation_payload = {"answer": fallback_answer}
                    if evidence_items:
                        explanation_payload["evidence"] = evidence_items[:1]
                    direct_fact_read_method = (
                        "retrieve_evidence(+related)" if related_records else "retrieve_evidence"
                    )

        output_keepability, promotion_disposition = _bridge_output_classification(
            mode="memory_profile_fact_explanation",
            routing_decision="memory_profile_fact_explanation",
        )
        trace_ref = f"trace:{agent_id}:{human_id}:{request_id}"
        reply_text = build_profile_fact_explanation_answer(
            query=detected_profile_fact_query,
            explanation=explanation_payload,
        )
        evidence_summary = (
            "status=memory_profile_fact_explanation "
            f"predicate={detected_profile_fact_query.predicate or 'unknown'} "
            f"explanation_found={'yes' if bool(explanation_payload) else 'no'} "
            f"read_method={direct_fact_read_method}"
        )
        record_event(
            state_db,
            event_type="tool_result_received",
            component="researcher_bridge",
            summary="Researcher bridge answered a profile fact explanation query directly from memory.",
            run_id=run_id,
            request_id=request_id,
            trace_ref=trace_ref,
            channel_id=channel_kind,
            session_id=session_id,
            human_id=human_id,
            agent_id=agent_id,
            actor_id="researcher_bridge",
            reason_code="memory_profile_fact_explanation",
            facts=_bridge_event_facts(
                routing_decision="memory_profile_fact_explanation",
                bridge_mode="memory_profile_fact_explanation",
                evidence_summary=evidence_summary,
                active_chip_key=None,
                active_chip_task_type=None,
                active_chip_evaluate_used=False,
                keepability=output_keepability,
                promotion_disposition=promotion_disposition,
                extra={
                    "fact_name": detected_profile_fact_query.fact_name,
                    "predicate": detected_profile_fact_query.predicate,
                    "label": detected_profile_fact_query.label,
                    "read_method": direct_fact_read_method,
                    "explanation_found": bool(explanation_payload),
                },
            ),
        )
        return ResearcherBridgeResult(
            request_id=request_id,
            reply_text=reply_text,
            evidence_summary=evidence_summary,
            escalation_hint=None,
            trace_ref=trace_ref,
            mode="memory_profile_fact_explanation",
            runtime_root=None,
            config_path=None,
            attachment_context=attachment_context,
            routing_decision="memory_profile_fact_explanation",
            active_chip_key=None,
            active_chip_task_type=None,
            active_chip_evaluate_used=False,
            output_keepability=output_keepability,
            promotion_disposition=promotion_disposition,
        )

    if (
        config_manager.get_path("spark.memory.enabled", default=False)
        and _detect_current_focus_plan_query(user_message)
    ):
        reply_text, focus_plan_facts = _build_current_focus_plan_reply(
            config_manager=config_manager,
            state_db=state_db,
            human_id=human_id,
            session_id=session_id,
            request_id=request_id,
            user_message=user_message,
        )
        output_keepability, promotion_disposition = _bridge_output_classification(
            mode="memory_current_focus_plan",
            routing_decision="memory_current_focus_plan_query",
        )
        trace_ref = f"trace:{agent_id}:{human_id}:{request_id}"
        evidence_summary = (
            "status=memory_current_focus_plan "
            f"focus_found={'yes' if focus_plan_facts.get('current_focus') else 'no'} "
            f"plan_found={'yes' if focus_plan_facts.get('current_plan') else 'no'} "
            f"focus_source_class={focus_plan_facts.get('focus_source_class') or 'unknown'} "
            f"plan_source_class={focus_plan_facts.get('plan_source_class') or 'unknown'}"
        )
        record_event(
            state_db,
            event_type="tool_result_received",
            component="researcher_bridge",
            summary="Researcher bridge answered a combined current focus and plan query from memory.",
            run_id=run_id,
            request_id=request_id,
            trace_ref=trace_ref,
            channel_id=channel_kind,
            session_id=session_id,
            human_id=human_id,
            agent_id=agent_id,
            actor_id="researcher_bridge",
            reason_code="memory_current_focus_plan_query",
            facts=_bridge_event_facts(
                routing_decision="memory_current_focus_plan_query",
                bridge_mode="memory_current_focus_plan",
                evidence_summary=evidence_summary,
                active_chip_key=None,
                active_chip_task_type=None,
                active_chip_evaluate_used=False,
                keepability=output_keepability,
                promotion_disposition=promotion_disposition,
                extra={
                    "query_text": str(user_message or "").strip(),
                    **focus_plan_facts,
                },
            ),
        )
        return ResearcherBridgeResult(
            request_id=request_id,
            reply_text=reply_text,
            evidence_summary=evidence_summary,
            escalation_hint=None,
            trace_ref=trace_ref,
            mode="memory_current_focus_plan",
            runtime_root=None,
            config_path=None,
            attachment_context=attachment_context,
            routing_decision="memory_current_focus_plan_query",
            active_chip_key=None,
            active_chip_task_type=None,
            active_chip_evaluate_used=False,
            output_keepability=output_keepability,
            promotion_disposition=promotion_disposition,
        )

    if (
        detected_profile_fact_query is not None
        and detected_profile_fact_query.query_kind == "single_fact"
    ):
        memory_subject = human_id if str(human_id or "").startswith("human:") else f"human:{human_id}"
        target_predicate = str(detected_profile_fact_query.predicate or "").strip()
        related_predicates = _profile_fact_query_related_predicates(detected_profile_fact_query.predicate)
        primary_records: list[dict[str, Any]] = []
        related_records: list[dict[str, Any]] = []
        current_fact_deleted = False
        if target_predicate == "profile.startup_name":
            direct_fact_lookup = lookup_current_state_in_memory(
                config_manager=config_manager,
                state_db=state_db,
                subject=memory_subject,
                predicate="profile.startup_name",
                actor_id="researcher_bridge",
            )
            current_fact_deleted = getattr(direct_fact_lookup.read_result, "memory_role", None) == "state_deletion"
            if not direct_fact_lookup.read_result.abstained and direct_fact_lookup.read_result.records:
                primary_records = [
                    record
                    for record in direct_fact_lookup.read_result.records
                    if _profile_fact_record_value(record)
                ]
            if not primary_records and not current_fact_deleted:
                founder_lookup = lookup_current_state_in_memory(
                    config_manager=config_manager,
                    state_db=state_db,
                    subject=memory_subject,
                    predicate="profile.founder_of",
                    actor_id="researcher_bridge",
                )
                if not founder_lookup.read_result.abstained and founder_lookup.read_result.records:
                    related_records = [
                        record
                        for record in founder_lookup.read_result.records
                        if _profile_fact_record_value(record)
                    ]
            if not current_fact_deleted and not primary_records and not related_records:
                primary_records, related_records = _inspect_profile_fact_records(
                    config_manager=config_manager,
                    state_db=state_db,
                    human_id=human_id,
                    predicate=detected_profile_fact_query.predicate,
                    related_predicates=related_predicates,
                    actor_id="researcher_bridge",
                )
        elif related_predicates:
            primary_records, related_records = _inspect_profile_fact_records(
                config_manager=config_manager,
                state_db=state_db,
                human_id=human_id,
                predicate=detected_profile_fact_query.predicate,
                related_predicates=related_predicates,
                actor_id="researcher_bridge",
            )
        else:
            direct_fact_lookup = lookup_current_state_in_memory(
                config_manager=config_manager,
                state_db=state_db,
                subject=memory_subject,
                predicate=str(detected_profile_fact_query.predicate or ""),
                actor_id="researcher_bridge",
            )
            current_fact_deleted = getattr(direct_fact_lookup.read_result, "memory_role", None) == "state_deletion"
            if not direct_fact_lookup.read_result.abstained and direct_fact_lookup.read_result.records:
                primary_records = [
                    record
                    for record in direct_fact_lookup.read_result.records
                    if _profile_fact_record_value(record)
                ]
            if not primary_records and not current_fact_deleted:
                primary_records, related_records = _inspect_profile_fact_records(
                    config_manager=config_manager,
                    state_db=state_db,
                    human_id=human_id,
                    predicate=detected_profile_fact_query.predicate,
                    actor_id="researcher_bridge",
                )
        if not current_fact_deleted and not primary_records and not related_records:
            evidence_lookup = retrieve_memory_evidence_in_memory(
                config_manager=config_manager,
                state_db=state_db,
                query=str(user_message or "").strip() or f"What is my {detected_profile_fact_query.label}?",
                subject=memory_subject,
                predicate=detected_profile_fact_query.predicate,
                limit=6,
                actor_id="researcher_bridge",
            )
            if not evidence_lookup.read_result.abstained and evidence_lookup.read_result.records:
                primary_records, related_records = _partition_profile_fact_records(
                    records=evidence_lookup.read_result.records,
                    predicate=detected_profile_fact_query.predicate,
                    related_predicates=related_predicates,
                )
        if (
            str(detected_profile_fact_query.predicate or "").startswith("profile.current_")
            and primary_records
            and not current_fact_deleted
        ):
            inspected_primary_records, inspected_related_records = _inspect_profile_fact_records(
                config_manager=config_manager,
                state_db=state_db,
                human_id=human_id,
                predicate=detected_profile_fact_query.predicate,
                related_predicates=related_predicates,
                actor_id="researcher_bridge",
            )
            if inspected_primary_records:
                primary_records = inspected_primary_records
            if inspected_related_records:
                related_records = inspected_related_records
        direct_fact_value = _select_profile_fact_query_value(
            predicate=detected_profile_fact_query.predicate,
            primary_records=primary_records,
            related_records=related_records,
        )
        stale_primary_records = active_state_records_past_revalidation(primary_records)
        stale_current_fact = bool(primary_records) and len(stale_primary_records) == len(primary_records)
        maintenance_actions = _active_state_maintenance_actions(primary_records)
        output_keepability, promotion_disposition = _bridge_output_classification(
            mode="memory_profile_fact",
            routing_decision="memory_profile_fact_query",
        )
        trace_ref = f"trace:{agent_id}:{human_id}:{request_id}"
        reply_text = build_profile_fact_query_answer(
            query=detected_profile_fact_query,
            value=direct_fact_value,
            stale=stale_current_fact,
        )
        evidence_summary = (
            "status=memory_profile_fact "
            f"predicate={detected_profile_fact_query.predicate or 'unknown'} "
            f"value_found={'yes' if direct_fact_value else 'no'} "
            f"stale_current_fact={'yes' if stale_current_fact else 'no'} "
            f"active_state_maintenance_actions={','.join(maintenance_actions) if maintenance_actions else 'none'}"
        )
        record_event(
            state_db,
            event_type="tool_result_received",
            component="researcher_bridge",
            summary="Researcher bridge answered a single-fact profile query directly from memory.",
            run_id=run_id,
            request_id=request_id,
            trace_ref=trace_ref,
            channel_id=channel_kind,
            session_id=session_id,
            human_id=human_id,
            agent_id=agent_id,
            actor_id="researcher_bridge",
            reason_code="memory_profile_fact_query",
            facts=_bridge_event_facts(
                routing_decision="memory_profile_fact_query",
                bridge_mode="memory_profile_fact",
                evidence_summary=evidence_summary,
                active_chip_key=None,
                active_chip_task_type=None,
                active_chip_evaluate_used=False,
                keepability=output_keepability,
                promotion_disposition=promotion_disposition,
                extra={
                    "fact_name": detected_profile_fact_query.fact_name,
                    "predicate": detected_profile_fact_query.predicate,
                    "label": detected_profile_fact_query.label,
                    "value_found": bool(direct_fact_value),
                    "stale_current_fact": stale_current_fact,
                    "active_state_maintenance_actions": maintenance_actions,
                },
            ),
        )
        return ResearcherBridgeResult(
            request_id=request_id,
            reply_text=reply_text,
            evidence_summary=evidence_summary,
            escalation_hint=None,
            trace_ref=trace_ref,
            mode="memory_profile_fact",
            runtime_root=None,
            config_path=None,
            attachment_context=attachment_context,
            routing_decision="memory_profile_fact_query",
            active_chip_key=None,
            active_chip_task_type=None,
            active_chip_evaluate_used=False,
            output_keepability=output_keepability,
            promotion_disposition=promotion_disposition,
        )
    if (
        detected_profile_fact_query is not None
        and detected_profile_fact_query.query_kind == "fact_history"
    ):
        memory_subject = human_id if str(human_id or "").startswith("human:") else f"human:{human_id}"
        target_predicate = str(detected_profile_fact_query.predicate or "").strip()
        current_lookup = lookup_current_state_in_memory(
            config_manager=config_manager,
            state_db=state_db,
            subject=memory_subject,
            predicate=target_predicate,
            actor_id="researcher_bridge",
        )
        current_records = []
        if not current_lookup.read_result.abstained and current_lookup.read_result.records:
            current_records = [
                record
                for record in current_lookup.read_result.records
                if _profile_fact_record_value(record)
            ]
        current_value = _select_profile_fact_query_value(
            predicate=target_predicate,
            primary_records=current_records,
            related_records=[],
        )
        history_lookup = retrieve_memory_events_in_memory(
            config_manager=config_manager,
            state_db=state_db,
            query=str(user_message or "").strip() or f"What did I have before for {detected_profile_fact_query.label}?",
            subject=memory_subject,
            predicate=target_predicate,
            limit=8,
            actor_id="researcher_bridge",
        )
        history_records: list[dict[str, Any]] = []
        if not history_lookup.read_result.abstained and history_lookup.read_result.records:
            history_records = _ordered_profile_fact_event_records(
                [
                    record
                    for record in history_lookup.read_result.records
                    if str(record.get("predicate") or "").strip() == target_predicate
                ]
            )
        history_read_method = "retrieve_events"
        if not history_records:
            inspection_records, _ = _inspect_profile_fact_records(
                config_manager=config_manager,
                state_db=state_db,
                human_id=human_id,
                predicate=target_predicate,
                actor_id="researcher_bridge",
            )
            history_records = _ordered_profile_fact_event_records(inspection_records)
            if history_records:
                history_read_method = "inspect_current_state_history"
        previous_record = _select_previous_profile_fact_record(
            current_value=current_value,
            records=history_records,
        )
        previous_value = None
        if previous_record is not None:
            previous_value = _profile_fact_record_value(previous_record)
            previous_as_of = str(previous_record.get("timestamp") or "").strip()
            if previous_as_of:
                historical_lookup = lookup_historical_state_in_memory(
                    config_manager=config_manager,
                    state_db=state_db,
                    subject=memory_subject,
                    predicate=target_predicate,
                    as_of=previous_as_of,
                    actor_id="researcher_bridge",
                )
                if not historical_lookup.read_result.abstained and historical_lookup.read_result.records:
                    previous_value = _profile_fact_record_value(historical_lookup.read_result.records[0]) or previous_value
                    if history_read_method == "inspect_current_state_history":
                        history_read_method = "get_historical_state+inspect_current_state_history"
                    else:
                        history_read_method = "get_historical_state+retrieve_events"
        output_keepability, promotion_disposition = _bridge_output_classification(
            mode="memory_profile_fact_history",
            routing_decision="memory_profile_fact_history_query",
        )
        trace_ref = f"trace:{agent_id}:{human_id}:{request_id}"
        reply_text = build_profile_fact_history_answer(
            query=detected_profile_fact_query,
            previous_value=previous_value,
            current_value=current_value,
        )
        evidence_summary = (
            "status=memory_profile_fact_history "
            f"predicate={target_predicate or 'unknown'} "
            f"previous_value_found={'yes' if previous_value else 'no'} "
            f"read_method={history_read_method}"
        )
        record_event(
            state_db,
            event_type="tool_result_received",
            component="researcher_bridge",
            summary="Researcher bridge answered a profile fact history query directly from memory.",
            run_id=run_id,
            request_id=request_id,
            trace_ref=trace_ref,
            channel_id=channel_kind,
            session_id=session_id,
            human_id=human_id,
            agent_id=agent_id,
            actor_id="researcher_bridge",
            reason_code="memory_profile_fact_history_query",
            facts=_bridge_event_facts(
                routing_decision="memory_profile_fact_history_query",
                bridge_mode="memory_profile_fact_history",
                evidence_summary=evidence_summary,
                active_chip_key=None,
                active_chip_task_type=None,
                active_chip_evaluate_used=False,
                keepability=output_keepability,
                promotion_disposition=promotion_disposition,
                extra={
                    "fact_name": detected_profile_fact_query.fact_name,
                    "predicate": target_predicate,
                    "label": detected_profile_fact_query.label,
                    "current_value_found": bool(current_value),
                    "previous_value_found": bool(previous_value),
                    "event_record_count": len(history_records),
                    "read_method": history_read_method,
                },
            ),
        )
        return ResearcherBridgeResult(
            request_id=request_id,
            reply_text=reply_text,
            evidence_summary=evidence_summary,
            escalation_hint=None,
            trace_ref=trace_ref,
            mode="memory_profile_fact_history",
            runtime_root=None,
            config_path=None,
            attachment_context=attachment_context,
            routing_decision="memory_profile_fact_history_query",
            active_chip_key=None,
            active_chip_task_type=None,
            active_chip_evaluate_used=False,
            output_keepability=output_keepability,
            promotion_disposition=promotion_disposition,
        )
    if (
        detected_profile_fact_query is not None
        and detected_profile_fact_query.query_kind == "event_history"
    ):
        memory_subject = human_id if str(human_id or "").startswith("human:") else f"human:{human_id}"
        target_predicate = str(detected_profile_fact_query.predicate or "").strip()
        history_lookup = retrieve_memory_events_in_memory(
            config_manager=config_manager,
            state_db=state_db,
            query=str(user_message or "").strip() or f"Show my {detected_profile_fact_query.label} history.",
            subject=memory_subject,
            predicate=target_predicate,
            limit=8,
            actor_id="researcher_bridge",
        )
        history_records: list[dict[str, Any]] = []
        if not history_lookup.read_result.abstained and history_lookup.read_result.records:
            history_records = _ordered_profile_fact_event_records(
                [
                    record
                    for record in history_lookup.read_result.records
                    if str(record.get("predicate") or "").strip() == target_predicate
                ]
            )
        history_read_method = "retrieve_events"
        if not history_records:
            inspection_records, _ = _inspect_profile_fact_records(
                config_manager=config_manager,
                state_db=state_db,
                human_id=human_id,
                predicate=target_predicate,
                actor_id="researcher_bridge",
            )
            history_records = _ordered_profile_fact_event_records(inspection_records)
            if history_records:
                history_read_method = "inspect_current_state_history"
        output_keepability, promotion_disposition = _bridge_output_classification(
            mode="memory_profile_event_history",
            routing_decision="memory_profile_event_history_query",
        )
        trace_ref = f"trace:{agent_id}:{human_id}:{request_id}"
        reply_text = build_profile_fact_event_history_answer(
            query=detected_profile_fact_query,
            records=history_records,
        )
        evidence_summary = (
            "status=memory_profile_event_history "
            f"predicate={target_predicate or 'unknown'} "
            f"event_count={len(history_records)} "
            f"read_method={history_read_method}"
        )
        record_event(
            state_db,
            event_type="tool_result_received",
            component="researcher_bridge",
            summary="Researcher bridge answered a profile fact event history query directly from memory.",
            run_id=run_id,
            request_id=request_id,
            trace_ref=trace_ref,
            channel_id=channel_kind,
            session_id=session_id,
            human_id=human_id,
            agent_id=agent_id,
            actor_id="researcher_bridge",
            reason_code="memory_profile_event_history_query",
            facts=_bridge_event_facts(
                routing_decision="memory_profile_event_history_query",
                bridge_mode="memory_profile_event_history",
                evidence_summary=evidence_summary,
                active_chip_key=None,
                active_chip_task_type=None,
                active_chip_evaluate_used=False,
                keepability=output_keepability,
                promotion_disposition=promotion_disposition,
                extra={
                    "fact_name": detected_profile_fact_query.fact_name,
                    "predicate": target_predicate,
                    "label": detected_profile_fact_query.label,
                    "event_record_count": len(history_records),
                    "read_method": history_read_method,
                },
            ),
        )
        return ResearcherBridgeResult(
            request_id=request_id,
            reply_text=reply_text,
            evidence_summary=evidence_summary,
            escalation_hint=None,
            trace_ref=trace_ref,
            mode="memory_profile_event_history",
            runtime_root=None,
            config_path=None,
            attachment_context=attachment_context,
            routing_decision="memory_profile_event_history_query",
            active_chip_key=None,
            active_chip_task_type=None,
            active_chip_evaluate_used=False,
            output_keepability=output_keepability,
            promotion_disposition=promotion_disposition,
        )
    if detected_entity_state_history_query is not None:
        memory_subject = human_id if str(human_id or "").startswith("human:") else f"human:{human_id}"
        target_predicate = str(detected_entity_state_history_query.predicate or "").strip()
        history_records: list[dict[str, Any]] = []
        history_read_method = "get_historical_state"
        entity_key = _entity_state_history_entity_key(detected_entity_state_history_query)
        current_lookup = read_memory_kernel(
            config_manager=config_manager,
            state_db=state_db,
            method="get_current_state",
            query=str(user_message or "").strip() or f"What was {detected_entity_state_history_query.topic} before?",
            subject=memory_subject,
            predicate=target_predicate,
            entity_key=entity_key,
            actor_id="researcher_bridge",
            session_id=session_id,
            turn_id=f"{request_id}:entity-history-current-state",
            source_surface="researcher_bridge:entity_history_current_state",
        )
        current_records = _filter_entity_state_history_records(
            query=detected_entity_state_history_query,
            records=list(current_lookup.read_result.records or []),
        )
        if not current_records:
            current_lookup = read_memory_kernel(
                config_manager=config_manager,
                state_db=state_db,
                method="get_current_state",
                query=str(user_message or "").strip() or f"What was {detected_entity_state_history_query.topic} before?",
                subject=memory_subject,
                predicate_prefix=target_predicate,
                actor_id="researcher_bridge",
                session_id=session_id,
                turn_id=f"{request_id}:entity-history-current-state-prefix",
                source_surface="researcher_bridge:entity_history_current_state_prefix",
            )
            current_records = _filter_entity_state_history_records(
                query=detected_entity_state_history_query,
                records=list(current_lookup.read_result.records or []),
            )
        if current_records:
            current_record = _ordered_profile_fact_event_records(current_records)[-1]
            historical_entity_key = _entity_state_record_entity_key(current_record) or entity_key
            as_of = _entity_state_history_as_of_before(current_record)
            if historical_entity_key and as_of:
                historical_lookup = read_memory_kernel(
                    config_manager=config_manager,
                    state_db=state_db,
                    method="get_historical_state",
                    query=str(user_message or "").strip() or f"What was {detected_entity_state_history_query.topic} before?",
                    subject=memory_subject,
                    predicate=target_predicate,
                    entity_key=historical_entity_key,
                    as_of=as_of,
                    actor_id="researcher_bridge",
                    session_id=session_id,
                    turn_id=f"{request_id}:entity-history-historical-state",
                    source_surface="researcher_bridge:entity_history_historical_state",
                )
                historical_records = _filter_entity_state_history_records(
                    query=detected_entity_state_history_query,
                    records=list(historical_lookup.read_result.records or []),
                )
                previous_records = [
                    record
                    for record in historical_records
                    if _profile_fact_record_value(record) != _profile_fact_record_value(current_record)
                ]
                if previous_records:
                    previous_record = _ordered_profile_fact_event_records(previous_records)[-1]
                    history_records = [previous_record, current_record]
                elif historical_records:
                    history_records = [current_record]
        if not history_records and not current_records:
            history_lookup = retrieve_memory_events_in_memory(
                config_manager=config_manager,
                state_db=state_db,
                query=str(user_message or "").strip() or f"What was {detected_entity_state_history_query.topic} before?",
                subject=memory_subject,
                predicate=target_predicate,
                limit=12,
                actor_id="researcher_bridge",
            )
            if not history_lookup.read_result.abstained and history_lookup.read_result.records:
                history_records = _filter_entity_state_history_records(
                    query=detected_entity_state_history_query,
                    records=list(history_lookup.read_result.records),
                )
            history_read_method = "retrieve_events"
        if not history_records and not current_records:
            direct_inspection = inspect_human_memory_in_memory(
                config_manager=config_manager,
                state_db=state_db,
                human_id=human_id,
                actor_id="researcher_bridge",
            )
            if not direct_inspection.read_result.abstained and direct_inspection.read_result.records:
                history_records = _filter_entity_state_history_records(
                    query=detected_entity_state_history_query,
                    records=list(direct_inspection.read_result.records),
                )
                if history_records:
                    history_read_method = "inspect_memory_records"
        output_keepability, promotion_disposition = _bridge_output_classification(
            mode="memory_entity_state_history",
            routing_decision="memory_entity_state_history_query",
        )
        trace_ref = f"trace:{agent_id}:{human_id}:{request_id}"
        reply_text = _build_entity_state_history_answer(
            query=detected_entity_state_history_query,
            records=history_records,
        )
        previous_value_found = (
            _select_previous_profile_fact_record(
                current_value=_profile_fact_record_value(_ordered_profile_fact_event_records(history_records)[-1])
                if history_records
                else "",
                records=history_records,
            )
            is not None
        )
        graph_shadow_trace = _record_memory_graph_shadow_probe(
            config_manager=config_manager,
            state_db=state_db,
            query=str(user_message or "").strip() or f"What was {detected_entity_state_history_query.topic} before?",
            subject=memory_subject,
            predicate=target_predicate,
            entity_key=entity_key,
            session_id=session_id,
            turn_id=f"{request_id}:entity-history-graph-shadow",
            source_surface="researcher_bridge:entity_state_history_graph_shadow",
            limit=5,
        )
        evidence_summary = (
            "status=memory_entity_state_history "
            f"predicate={target_predicate or 'unknown'} "
            f"attribute={detected_entity_state_history_query.attribute or 'unknown'} "
            f"topic={detected_entity_state_history_query.topic or 'unknown'} "
            f"event_record_count={len(history_records)} "
            f"previous_value_found={'yes' if previous_value_found else 'no'} "
            f"read_method={history_read_method}"
        )
        event_extra = {
            "predicate": target_predicate,
            "attribute": detected_entity_state_history_query.attribute,
            "topic": detected_entity_state_history_query.topic,
            "event_record_count": len(history_records),
            "previous_value_found": previous_value_found,
            "read_method": history_read_method,
            "graph_shadow_trace": graph_shadow_trace,
        }
        if detected_entity_state_history_followup:
            event_extra["followup_resolved"] = True
            event_extra.update(detected_entity_state_history_followup)
        record_event(
            state_db,
            event_type="tool_result_received",
            component="researcher_bridge",
            summary="Researcher bridge answered an entity state history query directly from memory.",
            run_id=run_id,
            request_id=request_id,
            trace_ref=trace_ref,
            channel_id=channel_kind,
            session_id=session_id,
            human_id=human_id,
            agent_id=agent_id,
            actor_id="researcher_bridge",
            reason_code="memory_entity_state_history_query",
            facts=_bridge_event_facts(
                routing_decision="memory_entity_state_history_query",
                bridge_mode="memory_entity_state_history",
                evidence_summary=evidence_summary,
                active_chip_key=None,
                active_chip_task_type=None,
                active_chip_evaluate_used=False,
                keepability=output_keepability,
                promotion_disposition=promotion_disposition,
                extra=event_extra,
            ),
        )
        return ResearcherBridgeResult(
            request_id=request_id,
            reply_text=reply_text,
            evidence_summary=evidence_summary,
            escalation_hint=None,
            trace_ref=trace_ref,
            mode="memory_entity_state_history",
            runtime_root=None,
            config_path=None,
            attachment_context=attachment_context,
            routing_decision="memory_entity_state_history_query",
            active_chip_key=None,
            active_chip_task_type=None,
            active_chip_evaluate_used=False,
            output_keepability=output_keepability,
            promotion_disposition=promotion_disposition,
        )
    if detected_entity_state_summary_query is not None:
        memory_subject = human_id if str(human_id or "").startswith("human:") else f"human:{human_id}"
        entity_key = _entity_state_summary_entity_key(detected_entity_state_summary_query)
        records_by_attribute: dict[str, dict[str, Any]] = {}
        candidate_record_count = 0
        read_method = "get_current_state"
        for attribute, predicate, _display_label in _ENTITY_STATE_SUMMARY_ATTRIBUTES:
            lookup = read_memory_kernel(
                config_manager=config_manager,
                state_db=state_db,
                method="get_current_state",
                query=str(user_message or "").strip() or f"What do you know about {detected_entity_state_summary_query.topic}?",
                subject=memory_subject,
                predicate=predicate,
                entity_key=entity_key,
                actor_id="researcher_bridge",
                session_id=session_id,
                turn_id=f"{request_id}:entity-summary-{attribute}",
                source_surface="researcher_bridge:entity_state_summary",
            )
            records = _filter_entity_state_summary_records(
                query=detected_entity_state_summary_query,
                attribute=attribute,
                predicate=predicate,
                records=list(lookup.read_result.records or []),
            )
            candidate_record_count += len(records)
            if records:
                records_by_attribute[attribute] = records[-1]
        direct_attribute_count = len(records_by_attribute)
        inspection_record_count = 0
        if len(records_by_attribute) < len(_ENTITY_STATE_SUMMARY_ATTRIBUTES):
            direct_inspection = inspect_human_memory_in_memory(
                config_manager=config_manager,
                state_db=state_db,
                human_id=human_id,
                actor_id="researcher_bridge",
            )
            if not direct_inspection.read_result.abstained and direct_inspection.read_result.records:
                inspection_records = list(direct_inspection.read_result.records or [])
                for attribute, predicate, _display_label in _ENTITY_STATE_SUMMARY_ATTRIBUTES:
                    if attribute in records_by_attribute:
                        continue
                    records = _filter_entity_state_summary_records(
                        query=detected_entity_state_summary_query,
                        attribute=attribute,
                        predicate=predicate,
                        records=inspection_records,
                    )
                    inspection_record_count += len(records)
                    if records:
                        records_by_attribute[attribute] = records[-1]
                if len(records_by_attribute) > direct_attribute_count:
                    read_method = "get_current_state+inspect_memory_records"
        output_keepability, promotion_disposition = _bridge_output_classification(
            mode="memory_entity_state_summary",
            routing_decision="memory_entity_state_summary_query",
        )
        trace_ref = f"trace:{agent_id}:{human_id}:{request_id}"
        reply_text = _build_entity_state_summary_answer(
            query=detected_entity_state_summary_query,
            records_by_attribute=records_by_attribute,
        )
        attributes = [
            attribute
            for attribute, _predicate, _display_label in _ENTITY_STATE_SUMMARY_ATTRIBUTES
            if attribute in records_by_attribute
        ]
        evidence_summary = (
            "status=memory_entity_state_summary "
            f"topic={detected_entity_state_summary_query.topic or 'unknown'} "
            f"entity_key={entity_key or 'unknown'} "
            f"attribute_count={len(attributes)} "
            f"candidate_record_count={candidate_record_count} "
            f"inspection_record_count={inspection_record_count} "
            f"read_method={read_method}"
        )
        graph_shadow_trace = _record_memory_graph_shadow_probe(
            config_manager=config_manager,
            state_db=state_db,
            query=str(user_message or "").strip() or f"What do you know about {detected_entity_state_summary_query.topic}?",
            subject=memory_subject,
            predicate=None,
            entity_key=entity_key,
            session_id=session_id,
            turn_id=f"{request_id}:entity-summary-graph-shadow",
            source_surface="researcher_bridge:entity_state_summary_graph_shadow",
            limit=8,
        )
        record_event(
            state_db,
            event_type="tool_result_received",
            component="researcher_bridge",
            summary="Researcher bridge answered an entity state summary query directly from memory.",
            run_id=run_id,
            request_id=request_id,
            trace_ref=trace_ref,
            channel_id=channel_kind,
            session_id=session_id,
            human_id=human_id,
            agent_id=agent_id,
            actor_id="researcher_bridge",
            reason_code="memory_entity_state_summary_query",
            facts=_bridge_event_facts(
                routing_decision="memory_entity_state_summary_query",
                bridge_mode="memory_entity_state_summary",
                evidence_summary=evidence_summary,
                active_chip_key=None,
                active_chip_task_type=None,
                active_chip_evaluate_used=False,
                keepability=output_keepability,
                promotion_disposition=promotion_disposition,
                extra={
                    "topic": detected_entity_state_summary_query.topic,
                    "entity_key": entity_key,
                    "attribute_count": len(attributes),
                    "candidate_record_count": candidate_record_count,
                    "inspection_record_count": inspection_record_count,
                    "attributes": attributes,
                    "read_method": read_method,
                    "graph_shadow_trace": graph_shadow_trace,
                },
            ),
        )
        return ResearcherBridgeResult(
            request_id=request_id,
            reply_text=reply_text,
            evidence_summary=evidence_summary,
            escalation_hint=None,
            trace_ref=trace_ref,
            mode="memory_entity_state_summary",
            runtime_root=None,
            config_path=None,
            attachment_context=attachment_context,
            routing_decision="memory_entity_state_summary_query",
            active_chip_key=None,
            active_chip_task_type=None,
            active_chip_evaluate_used=False,
            output_keepability=output_keepability,
            promotion_disposition=promotion_disposition,
        )
    if (
        detected_profile_fact_query is not None
        and detected_profile_fact_query.query_kind == "identity_summary"
    ):
        memory_subject = human_id if str(human_id or "").startswith("human:") else f"human:{human_id}"
        direct_identity_inspection = inspect_human_memory_in_memory(
            config_manager=config_manager,
            state_db=state_db,
            human_id=human_id,
            actor_id="researcher_bridge",
        )
        direct_identity_records = []
        if not direct_identity_inspection.read_result.abstained and direct_identity_inspection.read_result.records:
            direct_identity_records = [
                record
                for record in direct_identity_inspection.read_result.records
                if str(record.get("predicate") or "").startswith(
                    str(detected_profile_fact_query.predicate_prefix or "")
                )
            ]
        identity_evidence = retrieve_memory_evidence_in_memory(
            config_manager=config_manager,
            state_db=state_db,
            query=str(user_message or "").strip() or "What do you remember about me?",
            subject=memory_subject,
            limit=8,
            actor_id="researcher_bridge",
        )
        identity_evidence_records = []
        if not identity_evidence.read_result.abstained and identity_evidence.read_result.records:
            identity_evidence_records = [
                record
                for record in identity_evidence.read_result.records
                if str(record.get("predicate") or "").startswith(
                    str(detected_profile_fact_query.predicate_prefix or "")
                )
                and str(record.get("value") or "").strip()
            ]
        combined_identity_records = [
            *identity_evidence_records,
            *direct_identity_records,
        ]
        output_keepability, promotion_disposition = _bridge_output_classification(
            mode="memory_profile_identity",
            routing_decision="memory_profile_identity_summary",
        )
        trace_ref = f"trace:{agent_id}:{human_id}:{request_id}"
        reply_text = build_profile_identity_summary_answer(records=combined_identity_records)
        evidence_summary = (
            "status=memory_profile_identity "
            f"record_count={len(combined_identity_records)} "
            f"inspection_records={len(direct_identity_records)} "
            f"evidence_records={len(identity_evidence_records)}"
        )
        record_event(
            state_db,
            event_type="tool_result_received",
            component="researcher_bridge",
            summary="Researcher bridge answered an identity summary query directly from memory.",
            run_id=run_id,
            request_id=request_id,
            trace_ref=trace_ref,
            channel_id=channel_kind,
            session_id=session_id,
            human_id=human_id,
            agent_id=agent_id,
            actor_id="researcher_bridge",
            reason_code="memory_profile_identity_summary",
            facts=_bridge_event_facts(
                routing_decision="memory_profile_identity_summary",
                bridge_mode="memory_profile_identity",
                evidence_summary=evidence_summary,
                active_chip_key=None,
                active_chip_task_type=None,
                active_chip_evaluate_used=False,
                keepability=output_keepability,
                promotion_disposition=promotion_disposition,
                extra={
                    "fact_name": detected_profile_fact_query.fact_name,
                    "predicate_prefix": detected_profile_fact_query.predicate_prefix,
                    "record_count": len(combined_identity_records),
                    "inspection_record_count": len(direct_identity_records),
                    "evidence_record_count": len(identity_evidence_records),
                    "read_method": "get_current_state+retrieve_evidence",
                },
            ),
        )
        return ResearcherBridgeResult(
            request_id=request_id,
            reply_text=reply_text,
            evidence_summary=evidence_summary,
            escalation_hint=None,
            trace_ref=trace_ref,
            mode="memory_profile_identity",
            runtime_root=None,
            config_path=None,
            attachment_context=attachment_context,
            routing_decision="memory_profile_identity_summary",
            active_chip_key=None,
            active_chip_task_type=None,
            active_chip_evaluate_used=False,
            output_keepability=output_keepability,
            promotion_disposition=promotion_disposition,
        )
    if detected_open_memory_recall_query is not None:
        memory_subject = human_id if str(human_id or "").startswith("human:") else f"human:{human_id}"
        archived_raw_episode_count = 0
        archived_structured_evidence_count = 0
        direct_inspection = None
        recall_records: list[dict[str, Any]] = []
        read_method = "get_current_state"
        entity_attribute = _open_memory_recall_entity_attribute(detected_open_memory_recall_query.query_kind)
        entity_key = _open_memory_recall_entity_key(detected_open_memory_recall_query)
        if entity_attribute:
            direct_state_lookup = read_memory_kernel(
                config_manager=config_manager,
                state_db=state_db,
                method="get_current_state",
                query=str(user_message or "").strip() or detected_open_memory_recall_query.topic,
                subject=memory_subject,
                predicate=f"entity.{entity_attribute}",
                entity_key=entity_key,
                actor_id="researcher_bridge",
                session_id=session_id,
                turn_id=f"{request_id}:entity-current-state",
                source_surface="researcher_bridge:entity_current_recall",
            )
            direct_state_records = [
                record
                for record in _filter_open_memory_recall_records(
                    [
                        _open_memory_recall_enriched_entity_record(
                            query=detected_open_memory_recall_query,
                            record=record,
                        )
                        for record in list(direct_state_lookup.read_result.records or [])
                    ]
                )
                if _entity_state_answer_from_record(query=detected_open_memory_recall_query, record=record)
            ]
            recall_records = _open_memory_recall_decisive_records(
                query=detected_open_memory_recall_query,
                records=direct_state_records,
            )
            if not recall_records:
                direct_state_lookup = read_memory_kernel(
                    config_manager=config_manager,
                    state_db=state_db,
                    method="get_current_state",
                    query=str(user_message or "").strip() or detected_open_memory_recall_query.topic,
                    subject=memory_subject,
                    predicate_prefix=f"entity.{entity_attribute}",
                    actor_id="researcher_bridge",
                    session_id=session_id,
                    turn_id=f"{request_id}:entity-current-state-prefix",
                    source_surface="researcher_bridge:entity_current_recall_prefix",
                )
                direct_state_records = [
                    record
                    for record in _filter_open_memory_recall_records(
                        [
                            _open_memory_recall_enriched_entity_record(
                                query=detected_open_memory_recall_query,
                                record=record,
                            )
                            for record in list(direct_state_lookup.read_result.records or [])
                        ]
                    )
                    if _entity_state_answer_from_record(query=detected_open_memory_recall_query, record=record)
                ]
                recall_records = _open_memory_recall_decisive_records(
                    query=detected_open_memory_recall_query,
                    records=direct_state_records,
                )
        if not recall_records:
            read_method = "retrieve_evidence"
            evidence_lookup = retrieve_memory_evidence_in_memory(
                config_manager=config_manager,
                state_db=state_db,
                query=str(user_message or "").strip() or detected_open_memory_recall_query.topic,
                subject=memory_subject,
                limit=6,
                actor_id="researcher_bridge",
            )
            if not evidence_lookup.read_result.abstained and evidence_lookup.read_result.records:
                recall_records = [
                    record
                    for record in _filter_open_memory_recall_records(evidence_lookup.read_result.records)
                    if _record_matches_open_memory_topic(
                        record=record,
                        topic=detected_open_memory_recall_query.topic,
                    )
                ]
            direct_inspection = inspect_human_memory_in_memory(
                config_manager=config_manager,
                state_db=state_db,
                human_id=human_id,
                actor_id="researcher_bridge",
            )
            if not direct_inspection.read_result.abstained and direct_inspection.read_result.records:
                supplemental_records = [
                    record
                    for record in _filter_open_memory_recall_records(direct_inspection.read_result.records)
                    if _record_matches_open_memory_topic(
                        record=record,
                        topic=detected_open_memory_recall_query.topic,
                    )
                ]
                merged_records = _merge_memory_records(recall_records, supplemental_records)
                if merged_records != recall_records:
                    recall_records = merged_records
                    read_method = "retrieve_evidence+inspect_memory_records"
            structured_evidence_records = _filter_structured_evidence_records(recall_records)
            archivable_structured_evidence_records = _structured_evidence_records_past_archive(structured_evidence_records)
            if len(structured_evidence_records) >= 2 and archivable_structured_evidence_records:
                older_evidence_records = _newer_structured_evidence_records(evidence_records=structured_evidence_records)
                archived_structured_evidence_ids: set[str] = set()
                archived_structured_evidence_texts: set[str] = set()
                for record in archivable_structured_evidence_records:
                    record_id = str(
                        record.get("observation_id") or (record.get("metadata") or {}).get("observation_id") or ""
                    ).strip()
                    record_text = _memory_record_text(record).strip().casefold()
                    if not record_id:
                        if not record_text:
                            continue
                    if not any(
                        (
                            str(
                                candidate.get("observation_id") or (candidate.get("metadata") or {}).get("observation_id") or ""
                            ).strip()
                            == record_id
                            if record_id
                            else _memory_record_text(candidate).strip().casefold() == record_text
                        )
                        for candidate in older_evidence_records
                    ):
                        continue
                    try:
                        archive_structured_evidence_from_memory(
                            config_manager=config_manager,
                            state_db=state_db,
                            human_id=human_id,
                            predicate=str(record.get("predicate") or ""),
                            evidence_text=_memory_record_text(record),
                            evidence_observation_id=record_id,
                            archive_reason="eclipsed_by_newer_structured_evidence",
                            session_id=session_id,
                            turn_id=request_id,
                            channel_kind=channel_kind,
                            actor_id="telegram_structured_evidence_archiver",
                        )
                        archived_structured_evidence_count += 1
                        if record_id:
                            archived_structured_evidence_ids.add(record_id)
                        if record_text:
                            archived_structured_evidence_texts.add(record_text)
                    except Exception:
                        pass
                if archived_structured_evidence_ids or archived_structured_evidence_texts:
                    recall_records = [
                        record
                        for record in recall_records
                        if (
                            str(
                                record.get("observation_id") or (record.get("metadata") or {}).get("observation_id") or ""
                            ).strip()
                            not in archived_structured_evidence_ids
                            and _memory_record_text(record).strip().casefold() not in archived_structured_evidence_texts
                        )
                    ]
                    structured_evidence_records = [
                        record
                        for record in structured_evidence_records
                        if (
                            str(
                                record.get("observation_id") or (record.get("metadata") or {}).get("observation_id") or ""
                            ).strip()
                            not in archived_structured_evidence_ids
                            and _memory_record_text(record).strip().casefold() not in archived_structured_evidence_texts
                        )
                    ]
            raw_episode_records = _filter_raw_episode_records(recall_records)
            archivable_raw_episode_records = _raw_episode_records_past_archive(raw_episode_records)
            if structured_evidence_records and archivable_raw_episode_records:
                newer_evidence_records = _newer_structured_evidence_than_raw_episodes(
                    raw_episode_records=archivable_raw_episode_records,
                    evidence_records=structured_evidence_records,
                )
                archived_raw_episode_ids: set[str] = set()
                for record in archivable_raw_episode_records:
                    record_timestamp = _memory_record_timestamp(record)
                    if not any(_memory_record_timestamp(evidence_record) > record_timestamp for evidence_record in newer_evidence_records):
                        continue
                    try:
                        archive_raw_episode_from_memory(
                            config_manager=config_manager,
                            state_db=state_db,
                            human_id=human_id,
                            episode_text=_memory_record_text(record),
                            raw_episode_observation_id=str(
                                record.get("observation_id") or (record.get("metadata") or {}).get("observation_id") or ""
                            ).strip()
                            or None,
                            archive_reason="covered_by_newer_structured_evidence",
                            session_id=session_id,
                            turn_id=request_id,
                            channel_kind=channel_kind,
                            actor_id="telegram_raw_episode_archiver",
                        )
                        archived_raw_episode_count += 1
                        record_id = str(
                            record.get("observation_id") or (record.get("metadata") or {}).get("observation_id") or ""
                        ).strip()
                        if record_id:
                            archived_raw_episode_ids.add(record_id)
                    except Exception:
                        pass
                if archived_raw_episode_ids:
                    recall_records = [
                        record
                        for record in recall_records
                        if str(
                            record.get("observation_id") or (record.get("metadata") or {}).get("observation_id") or ""
                        ).strip()
                        not in archived_raw_episode_ids
                    ]
        if not recall_records:
            if direct_inspection is None:
                direct_inspection = inspect_human_memory_in_memory(
                    config_manager=config_manager,
                    state_db=state_db,
                    human_id=human_id,
                    actor_id="researcher_bridge",
                )
            if not direct_inspection.read_result.abstained and direct_inspection.read_result.records:
                recall_records = [
                    record
                    for record in _filter_open_memory_recall_records(direct_inspection.read_result.records)
                    if _record_matches_open_memory_topic(
                        record=record,
                        topic=detected_open_memory_recall_query.topic,
                    )
                ]
                structured_evidence_records = _filter_structured_evidence_records(recall_records)
                archivable_structured_evidence_records = _structured_evidence_records_past_archive(structured_evidence_records)
                if len(structured_evidence_records) >= 2 and archivable_structured_evidence_records:
                    older_evidence_records = _newer_structured_evidence_records(evidence_records=structured_evidence_records)
                    archived_structured_evidence_ids: set[str] = set()
                    archived_structured_evidence_texts: set[str] = set()
                    for record in archivable_structured_evidence_records:
                        record_id = str(
                            record.get("observation_id") or (record.get("metadata") or {}).get("observation_id") or ""
                        ).strip()
                        record_text = _memory_record_text(record).strip().casefold()
                        if not record_id:
                            if not record_text:
                                continue
                        if not any(
                            (
                                str(
                                    candidate.get("observation_id") or (candidate.get("metadata") or {}).get("observation_id") or ""
                                ).strip()
                                == record_id
                                if record_id
                                else _memory_record_text(candidate).strip().casefold() == record_text
                            )
                            for candidate in older_evidence_records
                        ):
                            continue
                        try:
                            archive_structured_evidence_from_memory(
                                config_manager=config_manager,
                                state_db=state_db,
                                human_id=human_id,
                                predicate=str(record.get("predicate") or ""),
                                evidence_text=_memory_record_text(record),
                                evidence_observation_id=record_id,
                                archive_reason="eclipsed_by_newer_structured_evidence",
                                session_id=session_id,
                                turn_id=request_id,
                                channel_kind=channel_kind,
                                actor_id="telegram_structured_evidence_archiver",
                            )
                            archived_structured_evidence_count += 1
                            if record_id:
                                archived_structured_evidence_ids.add(record_id)
                            if record_text:
                                archived_structured_evidence_texts.add(record_text)
                        except Exception:
                            pass
                    if archived_structured_evidence_ids or archived_structured_evidence_texts:
                        recall_records = [
                            record
                            for record in recall_records
                            if (
                                str(
                                    record.get("observation_id") or (record.get("metadata") or {}).get("observation_id") or ""
                                ).strip()
                                not in archived_structured_evidence_ids
                                and _memory_record_text(record).strip().casefold() not in archived_structured_evidence_texts
                            )
                        ]
                        structured_evidence_records = [
                            record
                            for record in structured_evidence_records
                            if (
                                str(
                                    record.get("observation_id") or (record.get("metadata") or {}).get("observation_id") or ""
                                ).strip()
                                not in archived_structured_evidence_ids
                                and _memory_record_text(record).strip().casefold() not in archived_structured_evidence_texts
                            )
                        ]
                raw_episode_records = _filter_raw_episode_records(recall_records)
                archivable_raw_episode_records = _raw_episode_records_past_archive(raw_episode_records)
                if structured_evidence_records and archivable_raw_episode_records:
                    newer_evidence_records = _newer_structured_evidence_than_raw_episodes(
                        raw_episode_records=archivable_raw_episode_records,
                        evidence_records=structured_evidence_records,
                    )
                    archived_raw_episode_ids: set[str] = set()
                    for record in archivable_raw_episode_records:
                        record_timestamp = _memory_record_timestamp(record)
                        if not any(_memory_record_timestamp(evidence_record) > record_timestamp for evidence_record in newer_evidence_records):
                            continue
                        try:
                            archive_raw_episode_from_memory(
                                config_manager=config_manager,
                                state_db=state_db,
                                human_id=human_id,
                                episode_text=_memory_record_text(record),
                                raw_episode_observation_id=str(
                                    record.get("observation_id") or (record.get("metadata") or {}).get("observation_id") or ""
                                ).strip()
                                or None,
                                archive_reason="covered_by_newer_structured_evidence",
                                session_id=session_id,
                                turn_id=request_id,
                                channel_kind=channel_kind,
                                actor_id="telegram_raw_episode_archiver",
                            )
                            archived_raw_episode_count += 1
                            record_id = str(
                                record.get("observation_id") or (record.get("metadata") or {}).get("observation_id") or ""
                            ).strip()
                            if record_id:
                                archived_raw_episode_ids.add(record_id)
                        except Exception:
                            pass
                    if archived_raw_episode_ids:
                        recall_records = [
                            record
                            for record in recall_records
                            if str(
                                record.get("observation_id") or (record.get("metadata") or {}).get("observation_id") or ""
                            ).strip()
                            not in archived_raw_episode_ids
                        ]
                if recall_records:
                    read_method = "inspect_memory_records"
        output_keepability, promotion_disposition = _bridge_output_classification(
            mode="memory_open_recall",
            routing_decision="memory_open_recall_query",
        )
        trace_ref = f"trace:{agent_id}:{human_id}:{request_id}"
        reply_text = _build_open_memory_recall_answer(
            query=detected_open_memory_recall_query,
            records=recall_records,
        )
        decisive_records = _open_memory_recall_decisive_records(
            query=detected_open_memory_recall_query,
            records=recall_records,
        )
        retrieved_memory_roles = sorted(
            {
                role
                for role in (_open_memory_recall_record_role(record) for record in decisive_records)
                if role
            }
        )
        candidate_memory_roles = sorted(
            {
                role
                for role in (_open_memory_recall_record_role(record) for record in recall_records)
                if role
            }
        )
        graph_shadow_trace = _record_memory_graph_shadow_probe(
            config_manager=config_manager,
            state_db=state_db,
            query=str(user_message or "").strip() or detected_open_memory_recall_query.topic,
            subject=memory_subject,
            predicate=f"entity.{entity_attribute}" if entity_attribute else None,
            entity_key=entity_key,
            session_id=session_id,
            turn_id=f"{request_id}:graph-shadow",
            source_surface="researcher_bridge:memory_open_recall_graph_shadow",
            limit=5,
        )
        evidence_summary = (
            "status=memory_open_recall "
            f"topic={detected_open_memory_recall_query.topic or 'unknown'} "
            f"query_kind={detected_open_memory_recall_query.query_kind or 'unknown'} "
            f"record_count={len(decisive_records)} "
            f"candidate_record_count={len(recall_records)} "
            f"archived_structured_evidence_count={archived_structured_evidence_count} "
            f"archived_raw_episode_count={archived_raw_episode_count} "
            f"read_method={read_method} "
            f"retrieved_roles={','.join(retrieved_memory_roles) if retrieved_memory_roles else 'none'} "
            f"candidate_roles={','.join(candidate_memory_roles) if candidate_memory_roles else 'none'}"
        )
        record_event(
            state_db,
            event_type="tool_result_received",
            component="researcher_bridge",
            summary="Researcher bridge answered an open memory recall query directly from memory.",
            run_id=run_id,
            request_id=request_id,
            trace_ref=trace_ref,
            channel_id=channel_kind,
            session_id=session_id,
            human_id=human_id,
            agent_id=agent_id,
            actor_id="researcher_bridge",
            reason_code="memory_open_recall_query",
            facts=_bridge_event_facts(
                routing_decision="memory_open_recall_query",
                bridge_mode="memory_open_recall",
                evidence_summary=evidence_summary,
                active_chip_key=None,
                active_chip_task_type=None,
                active_chip_evaluate_used=False,
                keepability=output_keepability,
                promotion_disposition=promotion_disposition,
                extra={
                    "topic": detected_open_memory_recall_query.topic,
                    "query_kind": detected_open_memory_recall_query.query_kind,
                    "record_count": len(decisive_records),
                    "candidate_record_count": len(recall_records),
                    "archived_structured_evidence_count": archived_structured_evidence_count,
                    "archived_raw_episode_count": archived_raw_episode_count,
                    "read_method": read_method,
                    "retrieved_memory_roles": retrieved_memory_roles,
                    "candidate_memory_roles": candidate_memory_roles,
                    "graph_shadow_trace": graph_shadow_trace,
                },
            ),
        )
        return ResearcherBridgeResult(
            request_id=request_id,
            reply_text=reply_text,
            evidence_summary=evidence_summary,
            escalation_hint=None,
            trace_ref=trace_ref,
            mode="memory_open_recall",
            runtime_root=None,
            config_path=None,
            attachment_context=attachment_context,
            routing_decision="memory_open_recall_query",
            active_chip_key=None,
            active_chip_task_type=None,
            active_chip_evaluate_used=False,
            output_keepability=output_keepability,
            promotion_disposition=promotion_disposition,
        )
    if detected_belief_recall_query is not None:
        memory_subject = human_id if str(human_id or "").startswith("human:") else f"human:{human_id}"
        evidence_lookup = retrieve_memory_evidence_in_memory(
            config_manager=config_manager,
            state_db=state_db,
            query=str(user_message or "").strip() or detected_belief_recall_query.topic,
            subject=memory_subject,
            limit=6,
            actor_id="researcher_bridge",
        )
        belief_records: list[dict[str, Any]] = []
        newer_evidence_records: list[dict[str, Any]] = []
        stale_belief_records: list[dict[str, Any]] = []
        archived_belief_count = 0
        read_method = "retrieve_evidence"
        focused_belief_lookup_records: list[dict[str, Any]] = []
        if not evidence_lookup.read_result.abstained and evidence_lookup.read_result.records:
            belief_records = _filter_belief_recall_records(evidence_lookup.read_result.records)
            invalidated_belief_ids = _structured_evidence_invalidated_belief_ids(evidence_lookup.read_result.records)
            if invalidated_belief_ids:
                archivable_belief_records = _filter_records_by_observation_ids(
                    _belief_records_past_revalidation(belief_records),
                    invalidated_belief_ids,
                )
                for record in archivable_belief_records:
                    try:
                        archive_belief_from_memory(
                            config_manager=config_manager,
                            state_db=state_db,
                            human_id=human_id,
                            predicate=str(record.get("predicate") or ""),
                            belief_text=_memory_record_text(record),
                            belief_observation_id=str(
                                record.get("observation_id") or (record.get("metadata") or {}).get("observation_id") or ""
                            ).strip()
                            or None,
                            archive_reason="invalidated_and_past_revalidation",
                            session_id=session_id,
                            turn_id=request_id,
                            channel_kind=channel_kind,
                            actor_id="telegram_belief_archiver",
                        )
                        archived_belief_count += 1
                    except Exception:
                        pass
                evidence_records = _filter_structured_evidence_records(evidence_lookup.read_result.records)
                newer_evidence_records = [
                    record
                    for record in evidence_records
                    if set((record.get("metadata") or {}).get("invalidated_belief_ids") or []) & invalidated_belief_ids
                ]
                belief_records = [
                    record
                    for record in belief_records
                    if str(record.get("observation_id") or (record.get("metadata") or {}).get("observation_id") or "").strip()
                    not in invalidated_belief_ids
                ]
            if belief_records:
                newer_evidence_records = _select_belief_recall_newer_evidence_records(
                    belief_records=belief_records,
                    candidate_records=evidence_lookup.read_result.records,
                    topic=detected_belief_recall_query.topic,
                )
                if not newer_evidence_records:
                    stale_belief_records = _belief_records_past_revalidation(belief_records)
        direct_inspection = None
        if belief_records and not stale_belief_records:
            direct_inspection = inspect_human_memory_in_memory(
                config_manager=config_manager,
                state_db=state_db,
                human_id=human_id,
                actor_id="researcher_bridge",
            )
            if not direct_inspection.read_result.abstained and direct_inspection.read_result.records:
                invalidated_belief_ids = _structured_evidence_invalidated_belief_ids(direct_inspection.read_result.records)
                if invalidated_belief_ids:
                    archivable_belief_records = _filter_records_by_observation_ids(
                        _belief_records_past_revalidation(belief_records),
                        invalidated_belief_ids,
                    )
                    for record in archivable_belief_records:
                        try:
                            archive_belief_from_memory(
                                config_manager=config_manager,
                                state_db=state_db,
                                human_id=human_id,
                                predicate=str(record.get("predicate") or ""),
                                belief_text=_memory_record_text(record),
                                belief_observation_id=str(
                                    record.get("observation_id") or (record.get("metadata") or {}).get("observation_id") or ""
                                ).strip()
                                or None,
                                archive_reason="invalidated_and_past_revalidation",
                                session_id=session_id,
                                turn_id=request_id,
                                channel_kind=channel_kind,
                                actor_id="telegram_belief_archiver",
                            )
                            archived_belief_count += 1
                        except Exception:
                            pass
                    evidence_records = _filter_structured_evidence_records(direct_inspection.read_result.records)
                    newer_evidence_records = [
                        record
                        for record in evidence_records
                        if set((record.get("metadata") or {}).get("invalidated_belief_ids") or []) & invalidated_belief_ids
                    ]
                    belief_records = [
                        record
                        for record in belief_records
                        if str(record.get("observation_id") or (record.get("metadata") or {}).get("observation_id") or "").strip()
                        not in invalidated_belief_ids
                    ]
                if belief_records:
                    inspection_evidence_records = _select_belief_recall_newer_evidence_records(
                        belief_records=belief_records,
                        candidate_records=direct_inspection.read_result.records,
                        topic=detected_belief_recall_query.topic,
                    )
                    if inspection_evidence_records:
                        newer_evidence_records = sorted(
                            _merge_memory_records(newer_evidence_records, inspection_evidence_records),
                            key=_memory_record_timestamp,
                            reverse=True,
                        )
                    if not newer_evidence_records:
                        stale_belief_records = _belief_records_past_revalidation(belief_records)
                if newer_evidence_records:
                    read_method = "inspect_memory_records"
        if not belief_records and detected_belief_recall_query.topic:
            focused_belief_lookup = retrieve_memory_evidence_in_memory(
                config_manager=config_manager,
                state_db=state_db,
                query=detected_belief_recall_query.topic,
                subject=memory_subject,
                limit=12,
                actor_id="researcher_bridge_belief_focus",
                record_activity=False,
            )
            if not focused_belief_lookup.read_result.abstained and focused_belief_lookup.read_result.records:
                focused_belief_lookup_records = focused_belief_lookup.read_result.records
                belief_records = _filter_belief_recall_records(focused_belief_lookup.read_result.records)
                if belief_records:
                    invalidated_belief_ids = _structured_evidence_invalidated_belief_ids(
                        focused_belief_lookup.read_result.records
                    )
                    if invalidated_belief_ids:
                        evidence_records = _filter_structured_evidence_records(focused_belief_lookup.read_result.records)
                        newer_evidence_records = [
                            record
                            for record in evidence_records
                            if set((record.get("metadata") or {}).get("invalidated_belief_ids") or [])
                            & invalidated_belief_ids
                        ]
                        belief_records = [
                            record
                            for record in belief_records
                            if str(
                                record.get("observation_id") or (record.get("metadata") or {}).get("observation_id") or ""
                            ).strip()
                            not in invalidated_belief_ids
                        ]
                    if belief_records:
                        focused_newer_evidence_records = _select_belief_recall_newer_evidence_records(
                            belief_records=belief_records,
                            candidate_records=focused_belief_lookup.read_result.records,
                            topic=detected_belief_recall_query.topic,
                        )
                        if focused_newer_evidence_records:
                            newer_evidence_records = focused_newer_evidence_records
                        if not newer_evidence_records:
                            stale_belief_records = _belief_records_past_revalidation(belief_records)
                    read_method = "retrieve_evidence(topic_focus)"
        if not belief_records:
            if direct_inspection is None:
                direct_inspection = inspect_human_memory_in_memory(
                    config_manager=config_manager,
                    state_db=state_db,
                    human_id=human_id,
                    actor_id="researcher_bridge",
                )
            if not direct_inspection.read_result.abstained and direct_inspection.read_result.records:
                belief_records = [
                    record
                    for record in _filter_belief_recall_records(direct_inspection.read_result.records)
                    if _record_matches_open_memory_topic(
                        record=record,
                        topic=detected_belief_recall_query.topic,
                    )
                ]
                if belief_records:
                    read_method = "inspect_memory_records"
        if belief_records and not stale_belief_records:
            belief_recall_candidate_records: list[dict[str, Any]] = []
            if not evidence_lookup.read_result.abstained and evidence_lookup.read_result.records:
                belief_recall_candidate_records.extend(evidence_lookup.read_result.records)
            if focused_belief_lookup_records:
                belief_recall_candidate_records.extend(focused_belief_lookup_records)
            if direct_inspection is not None and not direct_inspection.read_result.abstained and direct_inspection.read_result.records:
                belief_recall_candidate_records.extend(direct_inspection.read_result.records)
            reconciled_newer_evidence_records = _select_belief_recall_newer_evidence_records(
                belief_records=belief_records,
                candidate_records=belief_recall_candidate_records,
                topic=detected_belief_recall_query.topic,
            )
            if reconciled_newer_evidence_records:
                newer_evidence_records = reconciled_newer_evidence_records
            elif not newer_evidence_records:
                stale_belief_records = _belief_records_past_revalidation(belief_records)
        if not belief_records:
            synthetic_source_records: list[dict[str, Any]] = []
            if not evidence_lookup.read_result.abstained and evidence_lookup.read_result.records:
                synthetic_source_records.extend(evidence_lookup.read_result.records)
            if direct_inspection is not None and not direct_inspection.read_result.abstained and direct_inspection.read_result.records:
                synthetic_source_records.extend(direct_inspection.read_result.records)
            topical_evidence_records = [
                record
                for record in _filter_structured_evidence_records(synthetic_source_records)
                if _record_matches_open_memory_topic(
                    record=record,
                    topic=detected_belief_recall_query.topic,
                )
            ]
            if topical_evidence_records:
                newer_evidence_records = sorted(
                    topical_evidence_records,
                    key=_memory_record_timestamp,
                    reverse=True,
                )
                read_method = "retrieve_evidence(evidence_only)"
            else:
                belief_records = _synthesize_belief_records_from_consolidated_memory(
                    records=synthetic_source_records,
                    topic=detected_belief_recall_query.topic,
                )
                if belief_records:
                    read_method = "synthesized_from_current_state"
        retrieved_memory_roles = sorted(
            {
                str(record.get("memory_role") or "").strip()
                for record in belief_records
                if str(record.get("memory_role") or "").strip()
            }
        )
        output_keepability, promotion_disposition = _bridge_output_classification(
            mode="memory_belief_recall",
            routing_decision="memory_belief_recall_query",
        )
        trace_ref = f"trace:{agent_id}:{human_id}:{request_id}"
        reply_text = _build_belief_recall_answer(
            query=detected_belief_recall_query,
            records=belief_records,
            newer_evidence_records=newer_evidence_records,
            stale_belief_records=stale_belief_records,
        )
        evidence_summary = (
            "status=memory_belief_recall "
            f"topic={detected_belief_recall_query.topic or 'unknown'} "
            f"record_count={len(belief_records)} "
            f"newer_evidence_count={len(newer_evidence_records)} "
            f"stale_belief_count={len(stale_belief_records)} "
            f"archived_belief_count={archived_belief_count} "
            f"read_method={read_method} "
            f"retrieved_roles={','.join(retrieved_memory_roles) if retrieved_memory_roles else 'none'}"
        )
        record_event(
            state_db,
            event_type="tool_result_received",
            component="researcher_bridge",
            summary="Researcher bridge answered a belief recall query directly from memory.",
            run_id=run_id,
            request_id=request_id,
            trace_ref=trace_ref,
            channel_id=channel_kind,
            session_id=session_id,
            human_id=human_id,
            agent_id=agent_id,
            actor_id="researcher_bridge",
            reason_code="memory_belief_recall_query",
            facts=_bridge_event_facts(
                routing_decision="memory_belief_recall_query",
                bridge_mode="memory_belief_recall",
                evidence_summary=evidence_summary,
                active_chip_key=None,
                active_chip_task_type=None,
                active_chip_evaluate_used=False,
                keepability=output_keepability,
                promotion_disposition=promotion_disposition,
                extra={
                    "topic": detected_belief_recall_query.topic,
                    "record_count": len(belief_records),
                    "newer_evidence_count": len(newer_evidence_records),
                    "belief_stale_due_to_evidence": bool(newer_evidence_records),
                    "belief_stale_due_to_age": bool(stale_belief_records),
                    "stale_belief_count": len(stale_belief_records),
                    "archived_belief_count": archived_belief_count,
                    "read_method": read_method,
                    "retrieved_memory_roles": retrieved_memory_roles,
                },
            ),
        )
        return ResearcherBridgeResult(
            request_id=request_id,
            reply_text=reply_text,
            evidence_summary=evidence_summary,
            escalation_hint=None,
            trace_ref=trace_ref,
            mode="memory_belief_recall",
            runtime_root=None,
            config_path=None,
            attachment_context=attachment_context,
            routing_decision="memory_belief_recall_query",
            active_chip_key=None,
            active_chip_task_type=None,
            active_chip_evaluate_used=False,
            output_keepability=output_keepability,
            promotion_disposition=promotion_disposition,
        )
    if detected_memory_event_query is not None and detected_memory_event_query.query_kind == "latest_event":
        memory_subject = human_id if str(human_id or "").startswith("human:") else f"human:{human_id}"
        summary_predicate = detected_memory_event_query.summary_predicate or telegram_event_summary_predicate(
            detected_memory_event_query.predicate
        )
        latest_records: list[dict[str, Any]] = []
        latest_read_method = "retrieve_events"
        if summary_predicate:
            latest_lookup = lookup_current_state_in_memory(
                config_manager=config_manager,
                state_db=state_db,
                subject=memory_subject,
                predicate=summary_predicate,
                actor_id="researcher_bridge",
            )
            if not latest_lookup.read_result.abstained and latest_lookup.read_result.records:
                latest_records = list(latest_lookup.read_result.records)
                latest_read_method = "get_current_state"
        if not latest_records:
            event_lookup = retrieve_memory_events_in_memory(
                config_manager=config_manager,
                state_db=state_db,
                query=str(user_message or "").strip() or f"What {detected_memory_event_query.label} do I have?",
                subject=memory_subject,
                predicate=detected_memory_event_query.predicate,
                limit=8,
                actor_id="researcher_bridge",
            )
            if not event_lookup.read_result.abstained and event_lookup.read_result.records:
                latest_records = filter_telegram_memory_event_records(
                    query=detected_memory_event_query,
                    records=list(event_lookup.read_result.records),
                )
        output_keepability, promotion_disposition = _bridge_output_classification(
            mode="memory_telegram_event_latest",
            routing_decision="memory_telegram_event_latest_query",
        )
        trace_ref = f"trace:{agent_id}:{human_id}:{request_id}"
        reply_text = build_telegram_memory_event_query_answer(
            query=detected_memory_event_query,
            records=latest_records,
        )
        evidence_summary = (
            "status=memory_telegram_event_latest "
            f"predicate={detected_memory_event_query.predicate or 'telegram.event.*'} "
            f"record_count={len(latest_records)} "
            f"read_method={latest_read_method}"
        )
        record_event(
            state_db,
            event_type="tool_result_received",
            component="researcher_bridge",
            summary="Researcher bridge answered a latest Telegram event query directly from memory.",
            run_id=run_id,
            request_id=request_id,
            trace_ref=trace_ref,
            channel_id=channel_kind,
            session_id=session_id,
            human_id=human_id,
            agent_id=agent_id,
            actor_id="researcher_bridge",
            reason_code="memory_telegram_event_latest_query",
            facts=_bridge_event_facts(
                routing_decision="memory_telegram_event_latest_query",
                bridge_mode="memory_telegram_event_latest",
                evidence_summary=evidence_summary,
                active_chip_key=None,
                active_chip_task_type=None,
                active_chip_evaluate_used=False,
                keepability=output_keepability,
                promotion_disposition=promotion_disposition,
                extra={
                    "predicate": detected_memory_event_query.predicate,
                    "summary_predicate": summary_predicate,
                    "label": detected_memory_event_query.label,
                    "record_count": len(latest_records),
                    "read_method": latest_read_method,
                },
            ),
        )
        return ResearcherBridgeResult(
            request_id=request_id,
            reply_text=reply_text,
            evidence_summary=evidence_summary,
            escalation_hint=None,
            trace_ref=trace_ref,
            mode="memory_telegram_event_latest",
            runtime_root=None,
            config_path=None,
            attachment_context=attachment_context,
            routing_decision="memory_telegram_event_latest_query",
            active_chip_key=None,
            active_chip_task_type=None,
            active_chip_evaluate_used=False,
            output_keepability=output_keepability,
            promotion_disposition=promotion_disposition,
        )
    if detected_memory_event_query is not None:
        memory_subject = human_id if str(human_id or "").startswith("human:") else f"human:{human_id}"
        event_lookup = retrieve_memory_events_in_memory(
            config_manager=config_manager,
            state_db=state_db,
            query=str(user_message or "").strip() or "What events did I mention?",
            subject=memory_subject,
            predicate=detected_memory_event_query.predicate,
            limit=8,
            actor_id="researcher_bridge",
        )
        event_records: list[dict[str, Any]] = []
        if not event_lookup.read_result.abstained and event_lookup.read_result.records:
            event_records = filter_telegram_memory_event_records(
                query=detected_memory_event_query,
                records=list(event_lookup.read_result.records),
            )
        output_keepability, promotion_disposition = _bridge_output_classification(
            mode="memory_telegram_event_history",
            routing_decision="memory_telegram_event_query",
        )
        trace_ref = f"trace:{agent_id}:{human_id}:{request_id}"
        reply_text = build_telegram_memory_event_query_answer(
            query=detected_memory_event_query,
            records=event_records,
        )
        evidence_summary = (
            "status=memory_telegram_event_history "
            f"predicate={detected_memory_event_query.predicate or 'telegram.event.*'} "
            f"event_count={len(event_records)}"
        )
        record_event(
            state_db,
            event_type="tool_result_received",
            component="researcher_bridge",
            summary="Researcher bridge answered a Telegram event query directly from memory.",
            run_id=run_id,
            request_id=request_id,
            trace_ref=trace_ref,
            channel_id=channel_kind,
            session_id=session_id,
            human_id=human_id,
            agent_id=agent_id,
            actor_id="researcher_bridge",
            reason_code="memory_telegram_event_query",
            facts=_bridge_event_facts(
                routing_decision="memory_telegram_event_query",
                bridge_mode="memory_telegram_event_history",
                evidence_summary=evidence_summary,
                active_chip_key=None,
                active_chip_task_type=None,
                active_chip_evaluate_used=False,
                keepability=output_keepability,
                promotion_disposition=promotion_disposition,
                extra={
                    "predicate": detected_memory_event_query.predicate,
                    "label": detected_memory_event_query.label,
                    "event_record_count": len(event_records),
                    "read_method": "retrieve_events",
                },
            ),
        )
        return ResearcherBridgeResult(
            request_id=request_id,
            reply_text=reply_text,
            evidence_summary=evidence_summary,
            escalation_hint=None,
            trace_ref=trace_ref,
            mode="memory_telegram_event_history",
            runtime_root=None,
            config_path=None,
            attachment_context=attachment_context,
            routing_decision="memory_telegram_event_query",
            active_chip_key=None,
            active_chip_task_type=None,
            active_chip_evaluate_used=False,
            output_keepability=output_keepability,
            promotion_disposition=promotion_disposition,
        )
    if looks_like_mission_control_query(user_message):
        output_keepability, promotion_disposition = _bridge_output_classification(
            mode="mission_control_direct",
            routing_decision="mission_control_direct",
        )
        trace_ref = f"trace:{agent_id}:{human_id}:{request_id}"
        reply_text = build_mission_control_direct_reply(
            config_manager=config_manager,
            state_db=state_db,
            user_message=user_message,
        )
        evidence_summary = "status=mission_control_direct source=verified_runtime_health"
        record_event(
            state_db,
            event_type="tool_result_received",
            component="researcher_bridge",
            summary="Researcher bridge answered a runtime-health query directly from mission control.",
            run_id=run_id,
            request_id=request_id,
            trace_ref=trace_ref,
            channel_id=channel_kind,
            session_id=session_id,
            human_id=human_id,
            agent_id=agent_id,
            actor_id="researcher_bridge",
            reason_code="mission_control_direct",
            facts=_bridge_event_facts(
                routing_decision="mission_control_direct",
                bridge_mode="mission_control_direct",
                evidence_summary=evidence_summary,
                active_chip_key=None,
                active_chip_task_type=None,
                active_chip_evaluate_used=False,
                keepability=output_keepability,
                promotion_disposition=promotion_disposition,
                extra={"query_text": str(user_message or "").strip()},
            ),
        )
        return ResearcherBridgeResult(
            request_id=request_id,
            reply_text=reply_text,
            evidence_summary=evidence_summary,
            escalation_hint=None,
            trace_ref=trace_ref,
            mode="mission_control_direct",
            runtime_root=None,
            config_path=None,
            attachment_context=attachment_context,
            routing_decision="mission_control_direct",
            active_chip_key=None,
            active_chip_task_type=None,
            active_chip_evaluate_used=False,
            output_keepability=output_keepability,
            promotion_disposition=promotion_disposition,
        )
    recent_conversation_context = _load_recent_conversation_context(
        state_db=state_db,
        session_id=session_id,
        channel_kind=channel_kind,
        request_id=request_id,
    )

    active_chip_evaluate = _run_active_chip_evaluate(
        config_manager=config_manager,
        state_db=state_db,
        request_id=request_id,
        channel_kind=channel_kind,
        agent_id=agent_id,
        human_id=human_id,
        session_id=session_id,
        user_message=user_message,
        conversation_history=recent_conversation_context,
        attachment_context=attachment_context,
        run_id=run_id,
    )
    if attachment_context.get("active_chip_keys") or attachment_context.get("active_path_key") or active_chip_evaluate:
        record_event(
            state_db,
            event_type="plugin_or_chip_influence_recorded",
            component="researcher_bridge",
            summary="Attachment or chip influence was recorded before bridge execution.",
            run_id=run_id,
            request_id=request_id,
            channel_id=channel_kind,
            session_id=session_id,
            human_id=human_id,
            agent_id=agent_id,
            actor_id="researcher_bridge",
            reason_code="attachment_context_applied",
            facts={
                "active_chip_keys": attachment_context.get("active_chip_keys") or [],
                "pinned_chip_keys": attachment_context.get("pinned_chip_keys") or [],
                "active_path_key": attachment_context.get("active_path_key"),
                "active_chip_key": active_chip_evaluate.get("chip_key") if active_chip_evaluate else None,
                "active_chip_task_type": active_chip_evaluate.get("task_type") if active_chip_evaluate else None,
                "keepability": "ephemeral_context",
            },
            provenance={
                "source_kind": "chip_hook" if active_chip_evaluate else "attachment_snapshot",
                "source_ref": active_chip_evaluate.get("chip_key") if active_chip_evaluate else "attachments",
            },
        )
    browser_search_support = _build_browser_search_context(
        config_manager=config_manager,
        state_db=state_db,
        user_message=user_message,
        request_id=request_id,
        channel_kind=channel_kind,
        agent_id=agent_id,
        human_id=human_id,
        session_id=session_id,
        run_id=run_id,
    )
    browser_search_context_extra = str(browser_search_support.get("context") or "")
    browser_search_blocked_reply = str(browser_search_support.get("blocked_reply") or "") or None
    browser_search_blocked_code = str(browser_search_support.get("blocked_code") or "") or None
    browser_search_source_url = str(browser_search_support.get("source_url") or "") or None
    if browser_search_source_url and _is_search_engine_url(browser_search_source_url):
        browser_search_source_url = None
    system_registry_context = build_system_registry_prompt_context(
        config_manager=config_manager,
        state_db=state_db,
        user_message=user_message,
    )
    mission_control_context = build_mission_control_prompt_context(
        config_manager=config_manager,
        state_db=state_db,
        user_message=user_message,
    )
    capability_router_context = build_capability_router_prompt_context(
        config_manager=config_manager,
        state_db=state_db,
        user_message=user_message,
    )
    harness_context = build_harness_prompt_context(
        config_manager=config_manager,
        state_db=state_db,
        user_message=user_message,
    )
    user_instructions_context = _build_user_instructions_context(
        state_db=state_db,
        human_id=human_id,
        channel_kind=channel_kind,
    )
    iteration_draft_context = _build_iteration_draft_context(
        state_db=state_db,
        human_id=human_id,
        channel_kind=channel_kind,
        user_message=user_message,
    )
    context_capsule_obj = build_spark_context_capsule(
        config_manager=config_manager,
        state_db=state_db,
        human_id=human_id,
        session_id=session_id,
        channel_kind=channel_kind,
        request_id=request_id,
        user_message=user_message,
    )
    context_capsule = context_capsule_obj.render()
    if context_capsule:
        record_event(
            state_db,
            event_type="context_capsule_compiled",
            component="researcher_bridge",
            summary="Spark context capsule was compiled for the provider prompt.",
            run_id=run_id,
            request_id=request_id,
            trace_ref=f"trace:{agent_id}:{human_id}:{request_id}",
            channel_id=channel_kind,
            session_id=session_id,
            human_id=human_id,
            agent_id=agent_id,
            actor_id="researcher_bridge",
            reason_code="context_capsule_compiled",
            facts={
                "keepability": "ephemeral_context",
                "capsule_chars": len(context_capsule),
                "source_counts": context_capsule_obj.source_counts,
                "source_ledger": context_capsule_obj.source_ledger(),
                "context_route": "researcher_bridge_provider",
            },
            provenance={
                "source_kind": "context_compiler",
                "source_ref": "spark_context_capsule.v1",
            },
        )
    contextual_task = _build_contextual_task(
        user_message=user_message,
        channel_kind=channel_kind,
        attachment_context=attachment_context,
        active_chip_evaluate=active_chip_evaluate,
        personality_profile=personality_profile,
        personality_context_extra=personality_context_extra,
        browser_search_context_extra=browser_search_context_extra,
        recent_conversation_context=recent_conversation_context,
        context_capsule=context_capsule,
        system_registry_context=system_registry_context,
        mission_control_context=mission_control_context,
        capability_router_context=capability_router_context,
        harness_context=harness_context,
        user_instructions_context=user_instructions_context,
        iteration_draft_context=iteration_draft_context,
    )
    active_chip_key = str(active_chip_evaluate.get("chip_key")) if active_chip_evaluate else None
    active_chip_task_type = str(active_chip_evaluate.get("task_type")) if active_chip_evaluate and active_chip_evaluate.get("task_type") else None
    active_chip_evaluate_used = active_chip_evaluate is not None
    raw_chip_metrics = (active_chip_evaluate or {}).get("raw_chip_metrics") or []
    screened_context = screen_model_visible_text(
        state_db=state_db,
        source_kind="contextual_task",
        source_ref=request_id,
        text=contextual_task,
        summary="Builder blocked model-visible context before bridge execution.",
        reason_code="contextual_task_secret_like",
        policy_domain="researcher_bridge",
        run_id=run_id,
        request_id=request_id,
        trace_ref=f"trace:{agent_id}:{human_id}:{request_id}",
        provenance={
            "channel_kind": channel_kind,
            "active_chip_key": active_chip_evaluate.get("chip_key") if active_chip_evaluate else None,
            "active_path_key": attachment_context.get("active_path_key"),
            "personality_name": personality_profile.get("personality_name") if personality_profile else None,
        },
    )
    if not screened_context["allowed"]:
        output_keepability, promotion_disposition = _bridge_output_classification(
            mode="blocked",
            routing_decision="secret_boundary_blocked",
        )
        record_event(
            state_db,
            event_type="dispatch_failed",
            component="researcher_bridge",
            summary="Researcher bridge dispatch was blocked by the pre-model secret boundary.",
            run_id=run_id,
            request_id=request_id,
            trace_ref=f"trace:{agent_id}:{human_id}:{request_id}",
            channel_id=channel_kind,
            session_id=session_id,
            human_id=human_id,
            agent_id=agent_id,
            actor_id="researcher_bridge",
            reason_code="secret_boundary_blocked",
            severity="high",
            facts=_bridge_event_facts(
                routing_decision="secret_boundary_blocked",
                bridge_mode="blocked",
                active_chip_key=active_chip_key,
                active_chip_task_type=active_chip_task_type,
                active_chip_evaluate_used=active_chip_evaluate_used,
            raw_chip_metrics=raw_chip_metrics,
                keepability=output_keepability,
                promotion_disposition=promotion_disposition,
                extra={
                    "quarantine_id": screened_context["quarantine_id"],
                    "blocked_stage": "contextual_task",
                },
            ),
        )
        return ResearcherBridgeResult(
            request_id=request_id,
            reply_text=(
                "[Spark Researcher blocked] Sensitive material was detected in model-visible context. "
                "I did not send it to the bridge or provider."
            ),
            evidence_summary="Pre-model secret boundary blocked bridge execution.",
            escalation_hint="secret_boundary_violation",
            trace_ref=f"trace:{agent_id}:{human_id}:{request_id}",
            mode="blocked",
            runtime_root=None,
            config_path=None,
            attachment_context=attachment_context,
            routing_decision="secret_boundary_blocked",
            active_chip_key=active_chip_key,
            active_chip_task_type=active_chip_task_type,
            active_chip_evaluate_used=active_chip_evaluate_used,
            raw_chip_metrics=raw_chip_metrics,
            output_keepability=output_keepability,
            promotion_disposition=promotion_disposition,
        )
    provider_selection = _resolve_bridge_provider(config_manager=config_manager, state_db=state_db)
    routing_policy = _researcher_routing_policy(config_manager)
    runtime_root, runtime_source = discover_researcher_runtime_root(config_manager)
    config_path = resolve_researcher_config_path(config_manager, runtime_root) if runtime_root is not None else None
    record_environment_snapshot(
        state_db,
        surface="researcher_bridge",
        run_id=run_id,
        request_id=request_id,
        summary="Researcher bridge environment snapshot recorded.",
        provider_id=provider_selection.provider.provider_id if provider_selection.provider else None,
        provider_model=provider_selection.provider.default_model if provider_selection.provider else None,
        provider_base_url=provider_selection.provider.base_url if provider_selection.provider else None,
        provider_execution_transport=(
            provider_selection.provider.execution_transport if provider_selection.provider else None
        ),
        runtime_root=str(runtime_root) if runtime_root else None,
        config_path=str(config_path) if config_path else None,
        env_refs={
            "provider_auth_profile_id": (
                provider_selection.provider.auth_profile_id if provider_selection.provider else None
            ),
            "provider_source": provider_selection.provider.source if provider_selection.provider else None,
        },
        facts={"model_family": provider_selection.model_family, "runtime_source": runtime_source},
    )
    if not bool(config_manager.get_path("spark.researcher.enabled", default=True)):
        output_keepability, promotion_disposition = _bridge_output_classification(
            mode="disabled",
            routing_decision="bridge_disabled",
        )
        record_event(
            state_db,
            event_type="dispatch_failed",
            component="researcher_bridge",
            summary="Researcher bridge dispatch was blocked because the bridge is disabled.",
            run_id=run_id,
            request_id=request_id,
            channel_id=channel_kind,
            session_id=session_id,
            human_id=human_id,
            agent_id=agent_id,
            actor_id="researcher_bridge",
            reason_code="bridge_disabled",
            severity="high",
            facts=_bridge_event_facts(
                routing_decision="bridge_disabled",
                bridge_mode="disabled",
                provider_id=provider_selection.provider.provider_id if provider_selection.provider else None,
                provider_auth_method=provider_selection.provider.auth_method if provider_selection.provider else None,
                provider_model=provider_selection.provider.default_model if provider_selection.provider else None,
                provider_model_family=provider_selection.model_family,
                provider_execution_transport=(
                    provider_selection.provider.execution_transport if provider_selection.provider else None
                ),
                provider_base_url=provider_selection.provider.base_url if provider_selection.provider else None,
                provider_source=provider_selection.provider.source if provider_selection.provider else None,
                active_chip_key=active_chip_key,
                active_chip_task_type=active_chip_task_type,
                active_chip_evaluate_used=active_chip_evaluate_used,
            raw_chip_metrics=raw_chip_metrics,
                keepability=output_keepability,
                promotion_disposition=promotion_disposition,
            ),
        )
        return ResearcherBridgeResult(
            request_id=request_id,
            reply_text="[Spark Researcher disabled] The operator has disabled the Spark Researcher bridge for this workspace.",
            evidence_summary="Spark Researcher bridge disabled by operator.",
            escalation_hint=None,
            trace_ref=f"trace:{agent_id}:{human_id}:{request_id}",
            mode="disabled",
            runtime_root=None,
            config_path=None,
            attachment_context=attachment_context,
            provider_id=provider_selection.provider.provider_id if provider_selection.provider else None,
            provider_auth_profile_id=provider_selection.provider.auth_profile_id if provider_selection.provider else None,
            provider_auth_method=provider_selection.provider.auth_method if provider_selection.provider else None,
            provider_model=provider_selection.provider.default_model if provider_selection.provider else None,
            provider_model_family=provider_selection.model_family,
            provider_execution_transport=(
                provider_selection.provider.execution_transport if provider_selection.provider else None
            ),
            provider_base_url=provider_selection.provider.base_url if provider_selection.provider else None,
            provider_source=provider_selection.provider.source if provider_selection.provider else None,
            routing_decision="bridge_disabled",
            active_chip_key=active_chip_key,
            active_chip_task_type=active_chip_task_type,
            active_chip_evaluate_used=active_chip_evaluate_used,
            raw_chip_metrics=raw_chip_metrics,
            output_keepability=output_keepability,
            promotion_disposition=promotion_disposition,
        )
    if provider_selection.error:
        output_keepability, promotion_disposition = _bridge_output_classification(
            mode="bridge_error",
            routing_decision="provider_resolution_failed",
        )
        record_event(
            state_db,
            event_type="dispatch_failed",
            component="researcher_bridge",
            summary="Researcher bridge dispatch failed closed during provider resolution.",
            run_id=run_id,
            request_id=request_id,
            channel_id=channel_kind,
            session_id=session_id,
            human_id=human_id,
            agent_id=agent_id,
            actor_id="researcher_bridge",
            reason_code="provider_resolution_failed",
            severity="high",
            facts=_bridge_event_facts(
                routing_decision="provider_resolution_failed",
                bridge_mode="bridge_error",
                provider_id=provider_selection.provider.provider_id if provider_selection.provider else None,
                provider_auth_method=provider_selection.provider.auth_method if provider_selection.provider else None,
                provider_model=provider_selection.provider.default_model if provider_selection.provider else None,
                provider_model_family=provider_selection.model_family,
                provider_execution_transport=(
                    provider_selection.provider.execution_transport if provider_selection.provider else None
                ),
                provider_base_url=provider_selection.provider.base_url if provider_selection.provider else None,
                provider_source=provider_selection.provider.source if provider_selection.provider else None,
                active_chip_key=active_chip_key,
                active_chip_task_type=active_chip_task_type,
                active_chip_evaluate_used=active_chip_evaluate_used,
            raw_chip_metrics=raw_chip_metrics,
                keepability=output_keepability,
                promotion_disposition=promotion_disposition,
                extra={"error": provider_selection.error},
            ),
        )
        return ResearcherBridgeResult(
            request_id=request_id,
            reply_text=f"[Spark Researcher provider auth error] {provider_selection.error}",
            evidence_summary="Provider resolution failed closed before bridge execution.",
            escalation_hint="provider_auth_error",
            trace_ref=f"trace:{agent_id}:{human_id}:{request_id}",
            mode="bridge_error",
            runtime_root=None,
            config_path=None,
            attachment_context=attachment_context,
            provider_id=provider_selection.provider.provider_id if provider_selection.provider else None,
            provider_auth_profile_id=provider_selection.provider.auth_profile_id if provider_selection.provider else None,
            provider_auth_method=provider_selection.provider.auth_method if provider_selection.provider else None,
            provider_model=provider_selection.provider.default_model if provider_selection.provider else None,
            provider_model_family=provider_selection.model_family,
            provider_execution_transport=(
                provider_selection.provider.execution_transport if provider_selection.provider else None
            ),
            provider_base_url=provider_selection.provider.base_url if provider_selection.provider else None,
            provider_source=provider_selection.provider.source if provider_selection.provider else None,
            routing_decision="provider_resolution_failed",
            active_chip_key=active_chip_key,
            active_chip_task_type=active_chip_task_type,
            active_chip_evaluate_used=active_chip_evaluate_used,
            raw_chip_metrics=raw_chip_metrics,
            output_keepability=output_keepability,
            promotion_disposition=promotion_disposition,
        )
    if browser_search_blocked_reply:
        if (
            provider_selection.provider
            and provider_selection.provider.execution_transport == "direct_http"
        ):
            try:
                web_search_reply = _render_direct_provider_chat_fallback(
                    config_manager=config_manager,
                    state_db=state_db,
                    provider=provider_selection.provider,
                    user_message=user_message,
                    channel_kind=channel_kind,
                    attachment_context=attachment_context,
                    active_chip_evaluate=active_chip_evaluate,
                    personality_profile=personality_profile,
                    personality_context_extra=personality_context_extra,
                    browser_search_context_extra="",
                    recent_conversation_context=recent_conversation_context,
                    run_id=run_id,
                    request_id=request_id,
                    trace_ref=f"trace:{agent_id}:{human_id}:{request_id}",
                    enable_web_search=True,
                    session_id=session_id,
                    human_id=human_id,
                )
            except Exception:
                web_search_reply = ""
            if web_search_reply.strip():
                web_search_reply, _ = _clean_messaging_reply_with_metadata(
                    web_search_reply,
                    channel_kind=channel_kind,
                )
                trace_ref_ws = f"trace:{agent_id}:{human_id}:{request_id}"
                routing_decision_ws = "browser_fallback_web_search"
                output_keepability_ws, promotion_disposition_ws = _bridge_output_classification(
                    mode="direct_provider_web_search",
                    routing_decision=routing_decision_ws,
                )
                record_event(
                    state_db,
                    event_type="tool_result_received",
                    component="researcher_bridge",
                    summary="Researcher bridge produced a web-search grounded LLM fallback (browser session unavailable).",
                    run_id=run_id,
                    request_id=request_id,
                    trace_ref=trace_ref_ws,
                    channel_id=channel_kind,
                    session_id=session_id,
                    human_id=human_id,
                    agent_id=agent_id,
                    actor_id="researcher_bridge",
                    reason_code=routing_decision_ws,
                    facts=_bridge_event_facts(
                        routing_decision=routing_decision_ws,
                        bridge_mode="direct_provider_web_search",
                        provider_id=provider_selection.provider.provider_id,
                        provider_auth_method=provider_selection.provider.auth_method,
                        provider_model=provider_selection.provider.default_model,
                        provider_model_family=provider_selection.model_family,
                        provider_execution_transport=provider_selection.provider.execution_transport,
                        provider_base_url=provider_selection.provider.base_url,
                        provider_source=provider_selection.provider.source,
                        active_chip_key=active_chip_key,
                        active_chip_task_type=active_chip_task_type,
                        active_chip_evaluate_used=active_chip_evaluate_used,
                        raw_chip_metrics=raw_chip_metrics,
                        keepability=output_keepability_ws,
                        promotion_disposition=promotion_disposition_ws,
                    ),
                )
                return ResearcherBridgeResult(
                    request_id=request_id,
                    reply_text=web_search_reply.strip(),
                    evidence_summary="status=web_search_grounded provider_fallback=direct_http_chat_web_search",
                    escalation_hint=None,
                    trace_ref=trace_ref_ws,
                    mode="direct_provider_web_search",
                    runtime_root=str(runtime_root) if runtime_root else None,
                    config_path=str(config_path) if config_path else None,
                    attachment_context=attachment_context,
                    provider_id=provider_selection.provider.provider_id,
                    provider_auth_profile_id=provider_selection.provider.auth_profile_id,
                    provider_auth_method=provider_selection.provider.auth_method,
                    provider_model=provider_selection.provider.default_model,
                    provider_model_family=provider_selection.model_family,
                    provider_execution_transport=provider_selection.provider.execution_transport,
                    provider_base_url=provider_selection.provider.base_url,
                    provider_source=provider_selection.provider.source,
                    routing_decision=routing_decision_ws,
                    active_chip_key=active_chip_key,
                    active_chip_task_type=active_chip_task_type,
                    active_chip_evaluate_used=active_chip_evaluate_used,
                    raw_chip_metrics=raw_chip_metrics,
                    output_keepability=output_keepability_ws,
                    promotion_disposition=promotion_disposition_ws,
                )
        blocked_routing_decision, blocked_escalation_hint, blocked_evidence_summary = _browser_block_metadata(
            browser_search_blocked_code
        )
        output_keepability, promotion_disposition = _bridge_output_classification(
            mode="blocked",
            routing_decision=blocked_routing_decision,
        )
        record_event(
            state_db,
            event_type="dispatch_failed",
            component="researcher_bridge",
            summary=blocked_evidence_summary,
            run_id=run_id,
            request_id=request_id,
            trace_ref=f"trace:{agent_id}:{human_id}:{request_id}",
            channel_id=channel_kind,
            session_id=session_id,
            human_id=human_id,
            agent_id=agent_id,
            actor_id="researcher_bridge",
            reason_code=blocked_routing_decision,
            severity="medium",
            facts=_bridge_event_facts(
                routing_decision=blocked_routing_decision,
                bridge_mode="blocked",
                provider_id=provider_selection.provider.provider_id if provider_selection.provider else None,
                provider_auth_method=provider_selection.provider.auth_method if provider_selection.provider else None,
                provider_model=provider_selection.provider.default_model if provider_selection.provider else None,
                provider_model_family=provider_selection.model_family,
                provider_execution_transport=(
                    provider_selection.provider.execution_transport if provider_selection.provider else None
                ),
                provider_base_url=provider_selection.provider.base_url if provider_selection.provider else None,
                provider_source=provider_selection.provider.source if provider_selection.provider else None,
                active_chip_key=active_chip_key,
                active_chip_task_type=active_chip_task_type,
                active_chip_evaluate_used=active_chip_evaluate_used,
            raw_chip_metrics=raw_chip_metrics,
                keepability=output_keepability,
                promotion_disposition=promotion_disposition,
                extra={"blocked_code": browser_search_blocked_code},
            ),
        )
        return ResearcherBridgeResult(
            request_id=request_id,
            reply_text=browser_search_blocked_reply,
            evidence_summary=blocked_evidence_summary,
            escalation_hint=blocked_escalation_hint,
            trace_ref=f"trace:{agent_id}:{human_id}:{request_id}",
            mode="blocked",
            runtime_root=str(runtime_root) if runtime_root else None,
            config_path=str(config_path) if config_path else None,
            attachment_context=attachment_context,
            provider_id=provider_selection.provider.provider_id if provider_selection.provider else None,
            provider_auth_profile_id=provider_selection.provider.auth_profile_id if provider_selection.provider else None,
            provider_auth_method=provider_selection.provider.auth_method if provider_selection.provider else None,
            provider_model=provider_selection.provider.default_model if provider_selection.provider else None,
            provider_model_family=provider_selection.model_family,
            provider_execution_transport=(
                provider_selection.provider.execution_transport if provider_selection.provider else None
            ),
            provider_base_url=provider_selection.provider.base_url if provider_selection.provider else None,
            provider_source=provider_selection.provider.source if provider_selection.provider else None,
            routing_decision=blocked_routing_decision,
            active_chip_key=active_chip_key,
            active_chip_task_type=active_chip_task_type,
            active_chip_evaluate_used=active_chip_evaluate_used,
            raw_chip_metrics=raw_chip_metrics,
            output_keepability=output_keepability,
            promotion_disposition=promotion_disposition,
        )
    if (
        browser_search_context_extra
        and provider_selection.provider
        and provider_selection.provider.execution_transport == "direct_http"
    ):
        raw_reply_text = _render_direct_provider_chat_fallback(
            config_manager=config_manager,
            state_db=state_db,
            provider=provider_selection.provider,
            user_message=user_message,
            channel_kind=channel_kind,
            attachment_context=attachment_context,
            active_chip_evaluate=active_chip_evaluate,
            personality_profile=personality_profile,
            personality_context_extra=personality_context_extra,
            browser_search_context_extra=browser_search_context_extra,
            recent_conversation_context=recent_conversation_context,
            run_id=run_id,
            request_id=request_id,
            trace_ref=f"trace:{agent_id}:{human_id}:{request_id}",
            session_id=session_id,
            human_id=human_id,
        )
        cleaned_reply, removed_residue = _clean_messaging_reply_with_metadata(
            raw_reply_text,
            channel_kind=channel_kind,
        )
        reply_mutation_actions: list[str] = []
        if removed_residue:
            reply_mutation_actions.append("strip_operational_residue")
            record_quarantine(
                state_db,
                run_id=run_id,
                request_id=request_id,
                source_kind="reply_residue",
                source_ref=request_id,
                policy_domain="researcher_bridge_residue",
                reason_code="operational_residue_removed",
                summary="Operational residue was stripped from a browser-evidence direct-provider reply before delivery.",
                payload_preview="\n".join(removed_residue)[:160],
                provenance={"channel_kind": channel_kind, "trace_ref": f"trace:{agent_id}:{human_id}:{request_id}"},
            )
        if cleaned_reply != raw_reply_text and not reply_mutation_actions:
            reply_mutation_actions.append("rewrite_reply")
        cleaned_reply, browser_search_mutations = _sanitize_browser_search_reply(
            cleaned_reply,
            source_url=browser_search_source_url,
        )
        reply_mutation_actions.extend(browser_search_mutations)
        reply_text = cleaned_reply
        trace_ref = f"trace:{agent_id}:{human_id}:{request_id}"
        browser_mode = "direct_open" if "browser_mode=direct_open" in browser_search_context_extra else "search_results"
        evidence_summary = (
            f"status=browser_evidence browser_mode={browser_mode} provider_fallback=direct_http_chat"
        )
        initial_routing_decision = (
            "browser_direct_open_provider_chat"
            if browser_mode == "direct_open"
            else "browser_search_provider_chat"
        )
        reply_text, evidence_summary, escalation_hint, routing_decision = _maybe_apply_swarm_recommendation(
            config_manager=config_manager,
            state_db=state_db,
            user_message=user_message,
            channel_kind=channel_kind,
            reply_text=reply_text,
            evidence_summary=evidence_summary,
            routing_decision=initial_routing_decision,
            run_id=run_id,
            request_id=request_id,
            trace_ref=trace_ref,
            session_id=session_id,
            human_id=human_id,
            agent_id=agent_id,
        )
        if reply_text != cleaned_reply:
            reply_mutation_actions.append("apply_swarm_recommendation")
        output_keepability, promotion_disposition = _bridge_output_classification(
            mode="browser_evidence",
            routing_decision=routing_decision,
        )
        record_event(
            state_db,
            event_type="tool_result_received",
            component="researcher_bridge",
            summary="Researcher bridge produced a browser-evidence direct-provider result.",
            run_id=run_id,
            request_id=request_id,
            trace_ref=trace_ref,
            channel_id=channel_kind,
            session_id=session_id,
            human_id=human_id,
            agent_id=agent_id,
            actor_id="researcher_bridge",
            reason_code=initial_routing_decision,
            facts=_bridge_event_facts(
                routing_decision=routing_decision,
                bridge_mode="browser_evidence",
                evidence_summary=evidence_summary,
                runtime_root=str(runtime_root) if runtime_root else None,
                config_path=str(config_path) if config_path else None,
                provider_id=provider_selection.provider.provider_id,
                provider_auth_method=provider_selection.provider.auth_method,
                provider_model=provider_selection.provider.default_model,
                provider_model_family=provider_selection.model_family,
                provider_execution_transport=provider_selection.provider.execution_transport,
                provider_base_url=provider_selection.provider.base_url,
                provider_source=provider_selection.provider.source,
                active_chip_key=active_chip_key,
                active_chip_task_type=active_chip_task_type,
                active_chip_evaluate_used=active_chip_evaluate_used,
            raw_chip_metrics=raw_chip_metrics,
                keepability=output_keepability,
                promotion_disposition=promotion_disposition,
                extra=_bridge_reply_mutation_facts(
                    raw_text=raw_reply_text,
                    mutated_text=reply_text,
                    mutation_actions=reply_mutation_actions,
                ),
            ),
        )
        return ResearcherBridgeResult(
            request_id=request_id,
            reply_text=reply_text,
            evidence_summary=evidence_summary,
            escalation_hint=escalation_hint,
            trace_ref=trace_ref,
            mode="browser_evidence",
            runtime_root=str(runtime_root) if runtime_root else None,
            config_path=str(config_path) if config_path else None,
            attachment_context=attachment_context,
            provider_id=provider_selection.provider.provider_id,
            provider_auth_profile_id=provider_selection.provider.auth_profile_id,
            provider_auth_method=provider_selection.provider.auth_method,
            provider_model=provider_selection.provider.default_model,
            provider_model_family=provider_selection.model_family,
            provider_execution_transport=provider_selection.provider.execution_transport,
            provider_base_url=provider_selection.provider.base_url,
            provider_source=provider_selection.provider.source,
            routing_decision=routing_decision,
            active_chip_key=active_chip_key,
            active_chip_task_type=active_chip_task_type,
            active_chip_evaluate_used=active_chip_evaluate_used,
            raw_chip_metrics=raw_chip_metrics,
            output_keepability=output_keepability,
            promotion_disposition=promotion_disposition,
        )
    if runtime_root is not None:
        if config_path.exists():
            try:
                record_event(
                    state_db,
                    event_type="dispatch_started",
                    component="researcher_bridge",
                    summary="Researcher bridge dispatch started.",
                    run_id=run_id,
                    request_id=request_id,
                    channel_id=channel_kind,
                    session_id=session_id,
                    human_id=human_id,
                    agent_id=agent_id,
                    actor_id="researcher_bridge",
                    reason_code="build_advisory",
                    facts=_bridge_event_facts(
                        routing_decision="build_advisory",
                        bridge_mode=f"external_{runtime_source}",
                        runtime_root=str(runtime_root),
                        config_path=str(config_path),
                        provider_id=provider_selection.provider.provider_id if provider_selection.provider else None,
                        provider_auth_method=provider_selection.provider.auth_method if provider_selection.provider else None,
                        provider_model=provider_selection.provider.default_model if provider_selection.provider else None,
                        provider_model_family=provider_selection.model_family,
                        provider_execution_transport=(
                            provider_selection.provider.execution_transport if provider_selection.provider else None
                        ),
                        provider_base_url=provider_selection.provider.base_url if provider_selection.provider else None,
                        provider_source=provider_selection.provider.source if provider_selection.provider else None,
                        active_chip_key=active_chip_key,
                        active_chip_task_type=active_chip_task_type,
                        active_chip_evaluate_used=active_chip_evaluate_used,
            raw_chip_metrics=raw_chip_metrics,
                        extra={"runtime_source": runtime_source},
                    ),
                )
                if (
                    channel_kind == "telegram"
                    and active_chip_evaluate is not None
                    and provider_selection.provider
                    and provider_selection.provider.execution_transport == "direct_http"
                ):
                    raw_reply_text = _render_direct_provider_chat_fallback(
                        config_manager=config_manager,
                        state_db=state_db,
                        provider=provider_selection.provider,
                        user_message=user_message,
                        channel_kind=channel_kind,
                        attachment_context=attachment_context,
                        active_chip_evaluate=active_chip_evaluate,
                        personality_profile=personality_profile,
                        personality_context_extra=personality_context_extra,
                        browser_search_context_extra=browser_search_context_extra,
                        recent_conversation_context=recent_conversation_context,
                        run_id=run_id,
                        request_id=request_id,
                        trace_ref=f"trace:{agent_id}:{human_id}:{request_id}",
                        session_id=session_id,
                        human_id=human_id,
                    )
                    cleaned_reply, removed_residue = _clean_messaging_reply_with_metadata(
                        raw_reply_text,
                        channel_kind=channel_kind,
                    )
                    reply_mutation_actions: list[str] = []
                    if removed_residue:
                        reply_mutation_actions.append("strip_operational_residue")
                        record_quarantine(
                            state_db,
                            run_id=run_id,
                            request_id=request_id,
                            source_kind="reply_residue",
                            source_ref=request_id,
                            policy_domain="researcher_bridge_residue",
                            reason_code="operational_residue_removed",
                            summary="Operational residue was stripped from a chip-guided direct-provider reply before delivery.",
                            payload_preview="\n".join(removed_residue)[:160],
                            provenance={"channel_kind": channel_kind, "trace_ref": f"trace:{agent_id}:{human_id}:{request_id}"},
                        )
                    if cleaned_reply != raw_reply_text and not reply_mutation_actions:
                        reply_mutation_actions.append("rewrite_reply")
                    reply_text = cleaned_reply
                    trace_ref = f"trace:{agent_id}:{human_id}:{request_id}"
                    evidence_summary = "status=chip_guided provider_fallback=direct_http_chat"
                    reply_text, evidence_summary, escalation_hint, routing_decision = _maybe_apply_swarm_recommendation(
                        config_manager=config_manager,
                        state_db=state_db,
                        user_message=user_message,
                        channel_kind=channel_kind,
                        reply_text=reply_text,
                        evidence_summary=evidence_summary,
                        routing_decision="provider_fallback_chat",
                        run_id=run_id,
                        request_id=request_id,
                        trace_ref=trace_ref,
                        session_id=session_id,
                        human_id=human_id,
                        agent_id=agent_id,
                    )
                    if reply_text != cleaned_reply:
                        reply_mutation_actions.append("apply_swarm_recommendation")
                    output_keepability, promotion_disposition = _bridge_output_classification(
                        mode=f"external_{runtime_source}",
                        routing_decision=routing_decision,
                    )
                    record_event(
                        state_db,
                        event_type="tool_result_received",
                        component="researcher_bridge",
                        summary="Researcher bridge produced a chip-guided direct-provider result.",
                        run_id=run_id,
                        request_id=request_id,
                        trace_ref=trace_ref,
                        channel_id=channel_kind,
                        session_id=session_id,
                        human_id=human_id,
                        agent_id=agent_id,
                        actor_id="researcher_bridge",
                        reason_code="provider_fallback_chat",
                        facts=_bridge_event_facts(
                            routing_decision=routing_decision,
                            bridge_mode=f"external_{runtime_source}",
                            evidence_summary=evidence_summary,
                            runtime_root=str(runtime_root),
                            config_path=str(config_path),
                            provider_id=provider_selection.provider.provider_id,
                            provider_auth_method=provider_selection.provider.auth_method,
                            provider_model=provider_selection.provider.default_model,
                            provider_model_family=provider_selection.model_family,
                            provider_execution_transport=provider_selection.provider.execution_transport,
                            provider_base_url=provider_selection.provider.base_url,
                            provider_source=provider_selection.provider.source,
                            active_chip_key=active_chip_key,
                            active_chip_task_type=active_chip_task_type,
                            active_chip_evaluate_used=active_chip_evaluate_used,
            raw_chip_metrics=raw_chip_metrics,
                            keepability=output_keepability,
                            promotion_disposition=promotion_disposition,
                            extra=_bridge_reply_mutation_facts(
                                raw_text=raw_reply_text,
                                mutated_text=reply_text,
                                mutation_actions=reply_mutation_actions,
                            ),
                        ),
                    )
                    return ResearcherBridgeResult(
                        request_id=request_id,
                        reply_text=reply_text,
                        evidence_summary=evidence_summary,
                        escalation_hint=escalation_hint,
                        trace_ref=trace_ref,
                        mode=f"external_{runtime_source}",
                        runtime_root=str(runtime_root),
                        config_path=str(config_path),
                        attachment_context=attachment_context,
                        provider_id=provider_selection.provider.provider_id,
                        provider_auth_profile_id=provider_selection.provider.auth_profile_id,
                        provider_auth_method=provider_selection.provider.auth_method,
                        provider_model=provider_selection.provider.default_model,
                        provider_model_family=provider_selection.model_family,
                        provider_execution_transport=provider_selection.provider.execution_transport,
                        provider_base_url=provider_selection.provider.base_url,
                        provider_source=provider_selection.provider.source,
                        routing_decision=routing_decision,
                        active_chip_key=active_chip_key,
                        active_chip_task_type=active_chip_task_type,
                        active_chip_evaluate_used=active_chip_evaluate_used,
            raw_chip_metrics=raw_chip_metrics,
                        output_keepability=output_keepability,
                        promotion_disposition=promotion_disposition,
                    )
                # Multi-tier intent routing (see spark-tui-lab/ROUTING.md
                # and ROUTING_RESEARCH.md). Phase A handles three tiers
                # heuristically and two by slash-command override:
                #   - instant  : greetings, no advisory, minimal context
                #   - direct   : default, no advisory, full chat context
                #   - research : advisory + research subprocess path
                # Tier 2 (scoped) and Tier 4 (agent) are slash-command-only
                # and fall through to the 'direct' behavior in Phase A.
                # Phase B/C will wire them to a tool registry and harness
                # runtime respectively.
                #
                # The slash command itself gets stripped from user_message
                # before advisory/LLM consumption so the LLM doesn't see
                # '/direct hey' — just 'hey'.
                tier = _classify_intent_tier(user_message)
                user_message, _stripped_command = _strip_tier_slash_command(user_message)
                tier_skips_advisory = tier in ("instant", "direct", "scoped", "agent")
                if (
                    provider_selection.provider
                    and provider_selection.provider.execution_transport == "direct_http"
                    and routing_policy["conversational_fallback_enabled"]
                    and tier_skips_advisory
                ):
                    # Fast path: synthesize an under_supported advisory so
                    # the existing direct-provider fallback code below
                    # renders the reply without spawning the advisory
                    # subprocess. Personality, memory, and chip context
                    # are still assembled later in the function.
                    advisory = _synthesize_skipped_advisory(user_message, request_id)
                    advisory["epistemic_status"]["clarity"] = f"tier_{tier}"
                    execute_with_research = None  # type: ignore[assignment]
                else:
                    build_advisory = _import_build_advisory(runtime_root)
                    execute_with_research = _import_execute_with_research(runtime_root)
                    advisory = build_advisory(
                        config_path,
                        contextual_task,
                        model=provider_selection.model_family,
                        limit=3,
                        domain=None,
                    )
                advisory["original_user_message"] = user_message
                advisory_intent = advisory.get("intent")
                if isinstance(advisory_intent, dict):
                    advisory_intent["query"] = user_message
                # Tier-aware direct fallback decision. If the tier router
                # already picked a non-research tier, we use the direct
                # provider fallback unconditionally (the tier classifier
                # is the authority). For research tier, we still check
                # _is_conversational_fallback_candidate — that's the
                # existing behavior for research queries that turn out
                # to be simple enough for a direct reply.
                use_direct_fallback = (
                    provider_selection.provider
                    and provider_selection.provider.execution_transport == "direct_http"
                    and routing_policy["conversational_fallback_enabled"]
                    and (
                        tier_skips_advisory
                        or _is_conversational_fallback_candidate(
                            user_message=user_message,
                            advisory=advisory,
                            fallback_max_chars=int(routing_policy["conversational_fallback_max_chars"]),
                        )
                    )
                )
                if use_direct_fallback:
                    raw_reply_text = _render_direct_provider_chat_fallback(
                        config_manager=config_manager,
                        state_db=state_db,
                        provider=provider_selection.provider,
                        user_message=user_message,
                        channel_kind=channel_kind,
                        attachment_context=attachment_context,
                        active_chip_evaluate=active_chip_evaluate,
                        personality_profile=personality_profile,
                        personality_context_extra=personality_context_extra,
                        browser_search_context_extra=browser_search_context_extra,
                        recent_conversation_context=recent_conversation_context,
                        run_id=run_id,
                        request_id=request_id,
                        trace_ref=f"trace:{agent_id}:{human_id}:{request_id}",
                        session_id=session_id,
                        human_id=human_id,
                    )
                    cleaned_reply, removed_residue = _clean_messaging_reply_with_metadata(
                        raw_reply_text,
                        channel_kind=channel_kind,
                    )
                    reply_mutation_actions: list[str] = []
                    if removed_residue:
                        reply_mutation_actions.append("strip_operational_residue")
                        record_quarantine(
                            state_db,
                            run_id=run_id,
                            request_id=request_id,
                            source_kind="reply_residue",
                            source_ref=request_id,
                            policy_domain="researcher_bridge_residue",
                            reason_code="operational_residue_removed",
                            summary="Operational residue was stripped from a direct-provider fallback reply before delivery.",
                            payload_preview="\n".join(removed_residue)[:160],
                            provenance={"channel_kind": channel_kind, "trace_ref": f"trace:{agent_id}:{human_id}:{request_id}"},
                        )
                    if cleaned_reply != raw_reply_text and not reply_mutation_actions:
                        reply_mutation_actions.append("rewrite_reply")
                    reply_text = cleaned_reply
                    trace_ref = str(advisory.get("trace_path") or advisory.get("trace_id") or "trace:missing")
                    evidence_summary = "status=under_supported provider_fallback=direct_http_chat"
                    reply_text, evidence_summary, escalation_hint, routing_decision = _maybe_apply_swarm_recommendation(
                        config_manager=config_manager,
                        state_db=state_db,
                        user_message=user_message,
                        channel_kind=channel_kind,
                        reply_text=reply_text,
                        evidence_summary=evidence_summary,
                        routing_decision="provider_fallback_chat",
                        run_id=run_id,
                        request_id=request_id,
                        trace_ref=trace_ref,
                        session_id=session_id,
                        human_id=human_id,
                        agent_id=agent_id,
                    )
                    if reply_text != cleaned_reply:
                        reply_mutation_actions.append("apply_swarm_recommendation")
                    output_keepability, promotion_disposition = _bridge_output_classification(
                        mode=f"external_{runtime_source}",
                        routing_decision=routing_decision,
                    )
                    record_event(
                        state_db,
                        event_type="tool_result_received",
                        component="researcher_bridge",
                        summary="Researcher bridge produced a provider fallback result.",
                        run_id=run_id,
                        request_id=request_id,
                        trace_ref=trace_ref,
                        channel_id=channel_kind,
                        session_id=session_id,
                        human_id=human_id,
                        agent_id=agent_id,
                        actor_id="researcher_bridge",
                        reason_code="provider_fallback_chat",
                        facts=_bridge_event_facts(
                            routing_decision=routing_decision,
                            bridge_mode=f"external_{runtime_source}",
                            evidence_summary=evidence_summary,
                            runtime_root=str(runtime_root),
                            config_path=str(config_path),
                            provider_id=provider_selection.provider.provider_id,
                            provider_auth_method=provider_selection.provider.auth_method,
                            provider_model=provider_selection.provider.default_model,
                            provider_model_family=provider_selection.model_family,
                            provider_execution_transport=provider_selection.provider.execution_transport,
                            provider_base_url=provider_selection.provider.base_url,
                            provider_source=provider_selection.provider.source,
                            active_chip_key=active_chip_key,
                            active_chip_task_type=active_chip_task_type,
                            active_chip_evaluate_used=active_chip_evaluate_used,
            raw_chip_metrics=raw_chip_metrics,
                            keepability=output_keepability,
                            promotion_disposition=promotion_disposition,
                            extra=_bridge_reply_mutation_facts(
                                raw_text=raw_reply_text,
                                mutated_text=reply_text,
                                mutation_actions=reply_mutation_actions,
                            ),
                        ),
                    )
                    return ResearcherBridgeResult(
                        request_id=request_id,
                        reply_text=reply_text,
                        evidence_summary=evidence_summary,
                        escalation_hint=escalation_hint,
                        trace_ref=trace_ref,
                        mode=f"external_{runtime_source}",
                        runtime_root=str(runtime_root),
                        config_path=str(config_path),
                        attachment_context=attachment_context,
                        provider_id=provider_selection.provider.provider_id,
                        provider_auth_profile_id=provider_selection.provider.auth_profile_id,
                        provider_auth_method=provider_selection.provider.auth_method,
                        provider_model=provider_selection.provider.default_model,
                        provider_model_family=provider_selection.model_family,
                        provider_execution_transport=provider_selection.provider.execution_transport,
                        provider_base_url=provider_selection.provider.base_url,
                        provider_source=provider_selection.provider.source,
                        routing_decision=routing_decision,
                        active_chip_key=active_chip_key,
                        active_chip_task_type=active_chip_task_type,
                        active_chip_evaluate_used=active_chip_evaluate_used,
            raw_chip_metrics=raw_chip_metrics,
                        output_keepability=output_keepability,
                        promotion_disposition=promotion_disposition,
                    )
                if provider_selection.provider and _supports_direct_or_cli_execution(provider_selection):
                    with _temporary_provider_env(
                        provider_selection.provider,
                        state_db=state_db,
                        run_id=run_id,
                        request_id=request_id,
                        trace_ref=f"trace:{agent_id}:{human_id}:{request_id}",
                    ):
                        execution = execute_with_research(
                            runtime_root,
                            advisory=advisory,
                            model=provider_selection.model_family,
                            command_override=_command_override_for_provider(provider_selection),
                            dry_run=False,
                        )
                    reply_text, evidence_summary, trace_ref = _render_reply_from_execution(execution, advisory)
                else:
                    reply_text, evidence_summary, trace_ref = _render_reply_from_advisory(advisory)
                raw_reply_text = reply_text
                reply_text, removed_residue = _clean_messaging_reply_with_metadata(reply_text, channel_kind=channel_kind)
                reply_mutation_actions: list[str] = []
                if removed_residue:
                    reply_mutation_actions.append("strip_operational_residue")
                    record_quarantine(
                        state_db,
                        run_id=run_id,
                        request_id=request_id,
                        source_kind="reply_residue",
                        source_ref=request_id,
                        policy_domain="researcher_bridge_residue",
                        reason_code="operational_residue_removed",
                        summary="Operational residue was stripped from a researcher reply before delivery.",
                        payload_preview="\n".join(removed_residue)[:160],
                        provenance={"channel_kind": channel_kind, "trace_ref": trace_ref},
                    )
                if reply_text != raw_reply_text and not reply_mutation_actions:
                    reply_mutation_actions.append("rewrite_reply")
                base_routing_decision = (
                    "provider_execution"
                    if provider_selection.provider and _supports_direct_or_cli_execution(provider_selection)
                    else "researcher_advisory"
                )
                swarm_input_reply = reply_text
                reply_text, evidence_summary, escalation_hint, routing_decision = _maybe_apply_swarm_recommendation(
                    config_manager=config_manager,
                    state_db=state_db,
                    user_message=user_message,
                    channel_kind=channel_kind,
                    reply_text=reply_text,
                    evidence_summary=evidence_summary,
                    routing_decision=base_routing_decision,
                    run_id=run_id,
                    request_id=request_id,
                    trace_ref=trace_ref,
                    session_id=session_id,
                    human_id=human_id,
                    agent_id=agent_id,
                )
                if reply_text != swarm_input_reply:
                    reply_mutation_actions.append("apply_swarm_recommendation")
                output_keepability, promotion_disposition = _bridge_output_classification(
                    mode=f"external_{runtime_source}",
                    routing_decision=routing_decision,
                )
                record_event(
                    state_db,
                    event_type="tool_result_received",
                    component="researcher_bridge",
                    summary="Researcher bridge produced a result.",
                    run_id=run_id,
                    request_id=request_id,
                    trace_ref=trace_ref,
                    channel_id=channel_kind,
                    session_id=session_id,
                    human_id=human_id,
                    agent_id=agent_id,
                    actor_id="researcher_bridge",
                    reason_code=base_routing_decision,
                    facts=_bridge_event_facts(
                        routing_decision=routing_decision,
                        bridge_mode=f"external_{runtime_source}",
                        evidence_summary=evidence_summary,
                        runtime_root=str(runtime_root),
                        config_path=str(config_path),
                        provider_id=provider_selection.provider.provider_id if provider_selection.provider else None,
                        provider_auth_method=provider_selection.provider.auth_method if provider_selection.provider else None,
                        provider_model=provider_selection.provider.default_model if provider_selection.provider else None,
                        provider_model_family=provider_selection.model_family,
                        provider_execution_transport=(
                            provider_selection.provider.execution_transport if provider_selection.provider else None
                        ),
                        provider_base_url=provider_selection.provider.base_url if provider_selection.provider else None,
                        provider_source=provider_selection.provider.source if provider_selection.provider else None,
                        active_chip_key=active_chip_key,
                        active_chip_task_type=active_chip_task_type,
                        active_chip_evaluate_used=active_chip_evaluate_used,
            raw_chip_metrics=raw_chip_metrics,
                        keepability=output_keepability,
                        promotion_disposition=promotion_disposition,
                        extra=_bridge_reply_mutation_facts(
                            raw_text=raw_reply_text,
                            mutated_text=reply_text,
                            mutation_actions=reply_mutation_actions,
                        ),
                    ),
                )
                return ResearcherBridgeResult(
                    request_id=request_id,
                    reply_text=reply_text,
                    evidence_summary=evidence_summary,
                    escalation_hint=escalation_hint,
                    trace_ref=trace_ref,
                    mode=f"external_{runtime_source}",
                    runtime_root=str(runtime_root),
                    config_path=str(config_path),
                    attachment_context=attachment_context,
                    provider_id=provider_selection.provider.provider_id if provider_selection.provider else None,
                    provider_auth_profile_id=provider_selection.provider.auth_profile_id if provider_selection.provider else None,
                    provider_auth_method=provider_selection.provider.auth_method if provider_selection.provider else None,
                    provider_model=provider_selection.provider.default_model if provider_selection.provider else None,
                    provider_model_family=provider_selection.model_family,
                    provider_execution_transport=(
                        provider_selection.provider.execution_transport if provider_selection.provider else None
                    ),
                    provider_base_url=provider_selection.provider.base_url if provider_selection.provider else None,
                    provider_source=provider_selection.provider.source if provider_selection.provider else None,
                    routing_decision=routing_decision,
                    active_chip_key=active_chip_key,
                    active_chip_task_type=active_chip_task_type,
                    active_chip_evaluate_used=active_chip_evaluate_used,
            raw_chip_metrics=raw_chip_metrics,
                    output_keepability=output_keepability,
                    promotion_disposition=promotion_disposition,
                )
            except Exception as exc:  # pragma: no cover - external bridge safety
                output_keepability, promotion_disposition = _bridge_output_classification(
                    mode="bridge_error",
                    routing_decision="bridge_error",
                )
                record_event(
                    state_db,
                    event_type="dispatch_failed",
                    component="researcher_bridge",
                    summary="Researcher bridge dispatch failed with an exception.",
                    run_id=run_id,
                    request_id=request_id,
                    channel_id=channel_kind,
                    session_id=session_id,
                    human_id=human_id,
                    agent_id=agent_id,
                    actor_id="researcher_bridge",
                    reason_code="bridge_error",
                    severity="high",
                    facts=_bridge_event_facts(
                        routing_decision="bridge_error",
                        bridge_mode="bridge_error",
                        runtime_root=str(runtime_root),
                        config_path=str(config_path),
                        provider_id=provider_selection.provider.provider_id if provider_selection.provider else None,
                        provider_auth_method=provider_selection.provider.auth_method if provider_selection.provider else None,
                        provider_model=provider_selection.provider.default_model if provider_selection.provider else None,
                        provider_model_family=provider_selection.model_family,
                        provider_execution_transport=(
                            provider_selection.provider.execution_transport if provider_selection.provider else None
                        ),
                        provider_base_url=provider_selection.provider.base_url if provider_selection.provider else None,
                        provider_source=provider_selection.provider.source if provider_selection.provider else None,
                        active_chip_key=active_chip_key,
                        active_chip_task_type=active_chip_task_type,
                        active_chip_evaluate_used=active_chip_evaluate_used,
            raw_chip_metrics=raw_chip_metrics,
                        keepability=output_keepability,
                        promotion_disposition=promotion_disposition,
                        extra={"error": str(exc)},
                    ),
                )
                return ResearcherBridgeResult(
                    request_id=request_id,
                    reply_text=f"[Spark Researcher bridge error] {exc}",
                    evidence_summary="External bridge failed closed.",
                    escalation_hint="bridge_error",
                    trace_ref=f"trace:{agent_id}:{human_id}:{request_id}",
                    mode="bridge_error",
                    runtime_root=str(runtime_root),
                    config_path=str(config_path),
                    attachment_context=attachment_context,
                    provider_id=provider_selection.provider.provider_id if provider_selection.provider else None,
                    provider_auth_profile_id=provider_selection.provider.auth_profile_id if provider_selection.provider else None,
                    provider_auth_method=provider_selection.provider.auth_method if provider_selection.provider else None,
                    provider_model=provider_selection.provider.default_model if provider_selection.provider else None,
                    provider_model_family=provider_selection.model_family,
                    provider_execution_transport=(
                        provider_selection.provider.execution_transport if provider_selection.provider else None
                    ),
                    provider_base_url=provider_selection.provider.base_url if provider_selection.provider else None,
                    provider_source=provider_selection.provider.source if provider_selection.provider else None,
                    routing_decision="bridge_error",
                    active_chip_key=active_chip_key,
                    active_chip_task_type=active_chip_task_type,
                    active_chip_evaluate_used=active_chip_evaluate_used,
            raw_chip_metrics=raw_chip_metrics,
                    output_keepability=output_keepability,
                    promotion_disposition=promotion_disposition,
                )

    reply_text = (
        f"[Spark Researcher stub] I received your message in {channel_kind} "
        f"for {session_id}: {user_message}"
    )
    output_keepability, promotion_disposition = _bridge_output_classification(
        mode="stub",
        routing_decision="stub",
    )
    record_event(
        state_db,
        event_type="tool_result_received",
        component="researcher_bridge",
        summary="Researcher bridge stub result produced.",
        run_id=run_id,
        request_id=request_id,
        trace_ref=f"trace:{agent_id}:{human_id}:{request_id}",
        channel_id=channel_kind,
        session_id=session_id,
        human_id=human_id,
        agent_id=agent_id,
        actor_id="researcher_bridge",
        reason_code="stub",
        facts=_bridge_event_facts(
            routing_decision="stub",
            bridge_mode="stub",
            evidence_summary=(
                "No external Spark Researcher runtime was configured or discovered. "
                f"active_chips={len(attachment_context.get('active_chip_keys') or [])} "
                f"active_path={attachment_context.get('active_path_key') or 'none'}"
            ),
            active_chip_key=active_chip_key,
            active_chip_task_type=active_chip_task_type,
            active_chip_evaluate_used=active_chip_evaluate_used,
            raw_chip_metrics=raw_chip_metrics,
            keepability=output_keepability,
            promotion_disposition=promotion_disposition,
        ),
    )
    return ResearcherBridgeResult(
        request_id=request_id,
        reply_text=reply_text,
        evidence_summary=(
            "No external Spark Researcher runtime was configured or discovered. "
            f"active_chips={len(attachment_context.get('active_chip_keys') or [])} "
            f"active_path={attachment_context.get('active_path_key') or 'none'}"
        ),
        escalation_hint=None,
        trace_ref=f"trace:{agent_id}:{human_id}:{request_id}",
        mode="stub",
        runtime_root=str(runtime_root) if runtime_root else None,
        config_path=str(resolve_researcher_config_path(config_manager, runtime_root)) if runtime_root else None,
        attachment_context=attachment_context,
        provider_id=provider_selection.provider.provider_id if provider_selection.provider else None,
        provider_auth_profile_id=provider_selection.provider.auth_profile_id if provider_selection.provider else None,
        provider_auth_method=provider_selection.provider.auth_method if provider_selection.provider else None,
        provider_model=provider_selection.provider.default_model if provider_selection.provider else None,
        provider_model_family=provider_selection.model_family,
        provider_execution_transport=(
            provider_selection.provider.execution_transport if provider_selection.provider else None
        ),
        provider_base_url=provider_selection.provider.base_url if provider_selection.provider else None,
        provider_source=provider_selection.provider.source if provider_selection.provider else None,
        routing_decision="stub",
        active_chip_key=active_chip_key,
        active_chip_task_type=active_chip_task_type,
        active_chip_evaluate_used=active_chip_evaluate_used,
            raw_chip_metrics=raw_chip_metrics,
        output_keepability=output_keepability,
        promotion_disposition=promotion_disposition,
    )


@dataclass(frozen=True)
class ResearcherProviderSelection:
    provider: RuntimeProviderResolution | None
    model_family: str
    error: str | None = None


def _resolve_bridge_provider(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
) -> ResearcherProviderSelection:
    provider_records = config_manager.load().get("providers", {}).get("records", {}) or {}
    if not provider_records:
        return ResearcherProviderSelection(provider=None, model_family="generic")
    try:
        provider = resolve_runtime_provider(config_manager=config_manager, state_db=state_db)
    except RuntimeError as exc:
        return ResearcherProviderSelection(provider=None, model_family="generic", error=str(exc))
    return ResearcherProviderSelection(
        provider=provider,
        model_family=_model_family_for_provider(provider),
    )


def _model_family_for_provider(provider: RuntimeProviderResolution) -> str:
    provider_id = provider.provider_id.lower()
    model_name = (provider.default_model or "").lower()
    if provider_id == "openai-codex" or "codex" in model_name:
        return "codex"
    if provider.api_mode == "anthropic_messages" or provider_id == "anthropic" or "claude" in model_name:
        return "claude"
    if "openclaw" in model_name:
        return "openclaw"
    return "generic"


def _supports_direct_or_cli_execution(selection: ResearcherProviderSelection) -> bool:
    provider = selection.provider
    if provider is None:
        return False
    return provider.execution_transport in {"direct_http", "external_cli_wrapper"}


def _command_override_for_provider(selection: ResearcherProviderSelection) -> list[str] | None:
    provider = selection.provider
    if provider is None:
        return None
    if provider.execution_transport == "external_cli_wrapper":
        return None
    if provider.execution_transport != "direct_http":
        raise RuntimeError(f"Unsupported provider execution transport '{provider.execution_transport}'.")
    return [
        sys.executable,
        "-m",
        "spark_intelligence.llm.provider_wrapper",
        "{system_prompt_path}",
        "{user_prompt_path}",
        "{response_path}",
    ]


@contextmanager
def _temporary_provider_env(
    provider: RuntimeProviderResolution,
    *,
    state_db: StateDB | None = None,
    run_id: str | None = None,
    request_id: str | None = None,
    trace_ref: str | None = None,
):
    values = {
        "SPARK_INTELLIGENCE_PROVIDER_ID": provider.provider_id,
        "SPARK_INTELLIGENCE_PROVIDER_KIND": provider.provider_kind,
        "SPARK_INTELLIGENCE_PROVIDER_AUTH_METHOD": provider.auth_method,
        "SPARK_INTELLIGENCE_PROVIDER_API_MODE": provider.api_mode,
        "SPARK_INTELLIGENCE_PROVIDER_EXECUTION_TRANSPORT": provider.execution_transport,
        "SPARK_INTELLIGENCE_PROVIDER_MODEL": provider.default_model or "",
        "SPARK_INTELLIGENCE_PROVIDER_BASE_URL": provider.base_url or "",
        "SPARK_INTELLIGENCE_PROVIDER_SECRET": provider.secret_value,
    }
    if state_db is not None:
        values["SPARK_INTELLIGENCE_STATE_DB_PATH"] = str(state_db.path)
    if run_id:
        values["SPARK_INTELLIGENCE_RUN_ID"] = run_id
    if request_id:
        values["SPARK_INTELLIGENCE_REQUEST_ID"] = request_id
    if trace_ref:
        values["SPARK_INTELLIGENCE_TRACE_REF"] = trace_ref
    original = {key: os.environ.get(key) for key in values}
    try:
        for key, value in values.items():
            os.environ[key] = value
        yield
    finally:
        for key, value in original.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _read_runtime_state(state_db: StateDB) -> dict[str, str]:
    with state_db.connect() as conn:
        rows = conn.execute(
            "SELECT state_key, value FROM runtime_state WHERE state_key LIKE 'researcher:%'"
        ).fetchall()
    return {str(row["state_key"]): str(row["value"] or "") for row in rows}


def _read_typed_researcher_bridge_state(state_db: StateDB) -> dict[str, Any]:
    last_result = _latest_researcher_bridge_event(
        state_db,
        event_types=("tool_result_received", "dispatch_failed"),
    )
    last_failure = _latest_researcher_bridge_event(
        state_db,
        event_types=("dispatch_failed",),
    )
    last_influence = _latest_researcher_bridge_event(
        state_db,
        event_types=("plugin_or_chip_influence_recorded",),
    )
    snapshot = latest_snapshots_by_surface(state_db).get("researcher_bridge") or {}
    failure_count = 0
    with state_db.connect() as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM builder_events
            WHERE component = 'researcher_bridge'
              AND event_type = 'dispatch_failed'
            """
        ).fetchone()
        if row:
            failure_count = int(row["count"])
    facts = last_result.get("facts_json") or {}
    failure_facts = last_failure.get("facts_json") or {}
    influence_facts = last_influence.get("facts_json") or {}
    typed_failure = None
    if last_failure:
        typed_failure = {
            "mode": str(failure_facts.get("bridge_mode") or failure_facts.get("mode") or "bridge_error"),
            "request_id": last_failure.get("request_id"),
            "trace_ref": last_failure.get("trace_ref"),
            "routing_decision": failure_facts.get("routing_decision"),
            "runtime_root": failure_facts.get("runtime_root") or snapshot.get("runtime_root"),
            "config_path": failure_facts.get("config_path") or snapshot.get("config_path"),
            "message": (
                str(failure_facts.get("error") or "")
                or str(last_failure.get("summary") or "")
            ),
            "output_keepability": failure_facts.get("keepability"),
            "promotion_disposition": failure_facts.get("promotion_disposition"),
            "recorded_at": last_failure.get("created_at"),
        }
    return {
        "last_mode": facts.get("bridge_mode") or failure_facts.get("bridge_mode") or failure_facts.get("mode"),
        "last_trace_ref": last_result.get("trace_ref"),
        "last_request_id": last_result.get("request_id"),
        "last_runtime_root": snapshot.get("runtime_root"),
        "last_config_path": snapshot.get("config_path"),
        "last_evidence_summary": facts.get("evidence_summary"),
        "last_attachment_context": {
            "active_chip_keys": influence_facts.get("active_chip_keys") or [],
            "pinned_chip_keys": influence_facts.get("pinned_chip_keys") or [],
            "active_path_key": influence_facts.get("active_path_key"),
        }
        if last_influence and (
            influence_facts.get("active_chip_keys")
            or influence_facts.get("pinned_chip_keys")
            or influence_facts.get("active_path_key")
        )
        else None,
        "last_provider_id": facts.get("provider_id") or snapshot.get("provider_id"),
        "last_provider_model": facts.get("provider_model") or snapshot.get("provider_model"),
        "last_provider_model_family": facts.get("provider_model_family") or snapshot.get("model_family"),
        "last_provider_auth_method": facts.get("provider_auth_method"),
        "last_provider_execution_transport": (
            facts.get("provider_execution_transport") or snapshot.get("provider_execution_transport")
        ),
        "last_routing_decision": facts.get("routing_decision") or failure_facts.get("routing_decision"),
        "last_active_chip_key": facts.get("active_chip_key") or influence_facts.get("active_chip_key"),
        "last_active_chip_task_type": (
            facts.get("active_chip_task_type") or influence_facts.get("active_chip_task_type")
        ),
        "last_active_chip_evaluate_used": (
            facts.get("active_chip_evaluate_used")
            if facts.get("active_chip_evaluate_used") is not None
            else influence_facts.get("active_chip_evaluate_used")
        ),
        "last_output_keepability": facts.get("keepability") or failure_facts.get("keepability"),
        "last_promotion_disposition": (
            facts.get("promotion_disposition") or failure_facts.get("promotion_disposition")
        ),
        "failure_count": failure_count,
        "last_failure": typed_failure,
    }


def _latest_researcher_bridge_event(
    state_db: StateDB,
    *,
    event_types: tuple[str, ...],
) -> dict[str, Any]:
    events: list[dict[str, Any]] = []
    for event_type in event_types:
        events.extend(
            [
                event
                for event in latest_events_by_type(state_db, event_type=event_type, limit=200)
                if str(event.get("component") or "") == "researcher_bridge"
            ]
        )
    if not events:
        return {}
    events.sort(
        key=lambda event: (
            str(event.get("created_at") or ""),
            str(event.get("event_id") or ""),
        ),
        reverse=True,
    )
    return events[0]


def _loads_json(value: str | None) -> dict[str, Any] | None:
    if not value:
        return None
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


def _parse_int(value: str | None) -> int:
    if value is None or value == "":
        return 0
    try:
        return int(value)
    except ValueError:
        return 0


def _parse_bool(value: str | None) -> bool:
    if value is None:
        return False
    normalized = str(value).strip().lower()
    return normalized in {"1", "true", "yes", "on"}


def _read_failure_count(conn: Any, state_key: str) -> int:
    row = conn.execute("SELECT value FROM runtime_state WHERE state_key = ? LIMIT 1", (state_key,)).fetchone()
    if not row or row["value"] is None:
        return 0
    try:
        return int(str(row["value"]))
    except ValueError:
        return 0


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _set_runtime_state(
    conn: Any,
    state_key: str,
    value: str,
    *,
    guard_strategy: str | None = None,
) -> None:
    upsert_runtime_state(
        conn,
        state_key=state_key,
        value=value,
        component="researcher_bridge",
        guard_strategy=guard_strategy,
    )
