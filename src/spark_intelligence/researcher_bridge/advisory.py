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
from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.harness_registry import build_harness_prompt_context
from spark_intelligence.memory import (
    archive_belief_from_memory,
    archive_raw_episode_from_memory,
    archive_structured_evidence_from_memory,
    delete_profile_fact_from_memory,
    explain_memory_answer_in_memory,
    inspect_human_memory_in_memory,
    lookup_current_state_in_memory,
    lookup_historical_state_in_memory,
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
from spark_intelligence.observability.store import (
    build_text_mutation_facts,
    record_environment_snapshot,
    record_event,
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
from spark_intelligence.state.db import StateDB
from spark_intelligence.state.hygiene import JSON_RICHNESS_MERGE_GUARD, upsert_runtime_state
from spark_intelligence.system_registry import (
    build_system_registry_direct_reply,
    build_system_registry_prompt_context,
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
    return str(
        record.get("value")
        or record.get("normalized_value")
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
class BeliefRecallQuery:
    topic: str


_OPEN_MEMORY_RECALL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
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
        topic = str(match.group(1) or "").strip(" \t\r\n?!.\"'")
        if not topic or topic in {"me", "my profile"}:
            return None
        return OpenMemoryRecallQuery(topic=topic, query_kind=query_kind)
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
    for record in records:
        lifecycle = record.get("lifecycle") if isinstance(record.get("lifecycle"), dict) else {}
        metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
        supersedes = str(lifecycle.get("supersedes") or metadata.get("supersedes") or "").strip()
        if supersedes:
            superseded_ids.add(supersedes)
    filtered: list[dict[str, Any]] = []
    for record in records:
        predicate = str(record.get("predicate") or "").strip()
        role = str(record.get("memory_role") or "").strip()
        record_id = str(record.get("observation_id") or (record.get("metadata") or {}).get("observation_id") or "").strip()
        metadata = record.get("metadata") if isinstance(record.get("metadata"), dict) else {}
        if role == "state_deletion" or str(record.get("operation") or "").strip().lower() == "delete":
            continue
        if record_id and record_id in superseded_ids:
            continue
        if metadata.get("raw_episode_lifecycle_action") == "archived":
            continue
        if role in {"structured_evidence", "episodic"}:
            filtered.append(record)
            continue
        if predicate == "raw_turn" or predicate.startswith("evidence.telegram."):
            filtered.append(record)
    return filtered


def _record_matches_open_memory_topic(*, record: dict[str, Any], topic: str) -> bool:
    normalized_topic = str(topic or "").strip().casefold()
    if not normalized_topic:
        return False
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


def _build_open_memory_recall_answer(*, query: OpenMemoryRecallQuery, records: list[dict[str, Any]]) -> str:
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

    by_predicate: dict[str, str] = {}
    for record in records:
        predicate = str(record.get("predicate") or "").strip()
        value = str(record.get("value") or "").strip()
        if predicate and value and predicate not in by_predicate:
            by_predicate[predicate] = value

    lines: list[str] = []
    for predicate, label in _L1_STATE_PREDICATE_LABELS:
        value = by_predicate.get(predicate)
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
    ]
    spark_self_knowledge_context = _build_spark_self_knowledge_context(
        user_message=user_message,
        attachment_context=attachment_context,
    )
    if spark_self_knowledge_context:
        lines.extend([spark_self_knowledge_context, ""])
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
    operator_debug_modes = {"blocked", "disabled", "bridge_error", "stub"}
    operator_debug_decisions = {
        "bridge_disabled",
        "provider_resolution_failed",
        "bridge_error",
        "secret_boundary_blocked",
        "stub",
    }
    if mode in operator_debug_modes or routing_decision in operator_debug_decisions:
        return ("operator_debug_only", "not_promotable")
    return ("ephemeral_context", "not_promotable")


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
    detected_open_memory_recall_query = None
    detected_belief_recall_query = None
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
                and detected_open_memory_recall_query is None
            ):
                detected_open_memory_recall_query = _detect_open_memory_recall_query(user_message)
            if (
                detected_profile_fact_query is None
                and detected_memory_event_query is None
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
            },
        )

    memory_write_should_reply_via_persona = (
        bool(config_manager.get_path("spark.researcher.enabled", default=True))
        and channel_kind == "telegram"
        and not explicit_memory_message
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
        evidence_lookup = retrieve_memory_evidence_in_memory(
            config_manager=config_manager,
            state_db=state_db,
            query=str(user_message or "").strip() or detected_open_memory_recall_query.topic,
            subject=memory_subject,
            limit=6,
            actor_id="researcher_bridge",
        )
        recall_records: list[dict[str, Any]] = []
        read_method = "retrieve_evidence"
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
        retrieved_memory_roles = sorted(
            {
                str(record.get("memory_role") or "").strip()
                for record in recall_records
                if str(record.get("memory_role") or "").strip()
            }
        )
        output_keepability, promotion_disposition = _bridge_output_classification(
            mode="memory_open_recall",
            routing_decision="memory_open_recall_query",
        )
        trace_ref = f"trace:{agent_id}:{human_id}:{request_id}"
        reply_text = _build_open_memory_recall_answer(
            query=detected_open_memory_recall_query,
            records=recall_records,
        )
        evidence_summary = (
            "status=memory_open_recall "
            f"topic={detected_open_memory_recall_query.topic or 'unknown'} "
            f"record_count={len(recall_records)} "
            f"archived_structured_evidence_count={archived_structured_evidence_count} "
            f"archived_raw_episode_count={archived_raw_episode_count} "
            f"read_method={read_method} "
            f"retrieved_roles={','.join(retrieved_memory_roles) if retrieved_memory_roles else 'none'}"
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
                    "record_count": len(recall_records),
                    "archived_structured_evidence_count": archived_structured_evidence_count,
                    "archived_raw_episode_count": archived_raw_episode_count,
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
    if looks_like_system_registry_query(user_message):
        output_keepability, promotion_disposition = _bridge_output_classification(
            mode="system_registry_direct",
            routing_decision="system_registry_direct",
        )
        trace_ref = f"trace:{agent_id}:{human_id}:{request_id}"
        reply_text = build_system_registry_direct_reply(
            config_manager=config_manager,
            state_db=state_db,
            user_message=user_message,
        )
        evidence_summary = "status=system_registry_direct source=verified_registry"
        record_event(
            state_db,
            event_type="tool_result_received",
            component="researcher_bridge",
            summary="Researcher bridge answered a self-knowledge query directly from the verified system registry.",
            run_id=run_id,
            request_id=request_id,
            trace_ref=trace_ref,
            channel_id=channel_kind,
            session_id=session_id,
            human_id=human_id,
            agent_id=agent_id,
            actor_id="researcher_bridge",
            reason_code="system_registry_direct",
            facts=_bridge_event_facts(
                routing_decision="system_registry_direct",
                bridge_mode="system_registry_direct",
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
            mode="system_registry_direct",
            runtime_root=None,
            config_path=None,
            attachment_context=attachment_context,
            routing_decision="system_registry_direct",
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
    contextual_task = _build_contextual_task(
        user_message=user_message,
        channel_kind=channel_kind,
        attachment_context=attachment_context,
        active_chip_evaluate=active_chip_evaluate,
        personality_profile=personality_profile,
        personality_context_extra=personality_context_extra,
        browser_search_context_extra=browser_search_context_extra,
        recent_conversation_context=recent_conversation_context,
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
