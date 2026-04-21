from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.identity.service import approve_pairing, consume_pairing_welcome, rename_agent_identity
from spark_intelligence.memory.architecture_benchmark import benchmark_memory_architectures
from spark_intelligence.memory.architecture_live_comparison import compare_telegram_memory_architectures
from spark_intelligence.memory.knowledge_base import build_telegram_state_knowledge_base
from spark_intelligence.memory.orchestrator import inspect_human_memory_in_memory
from spark_intelligence.state.db import StateDB


@dataclass(frozen=True)
class TelegramMemoryRegressionCase:
    case_id: str
    category: str
    message: str
    expected_bridge_mode: str | None = None
    expected_routing_decision: str | None = None
    expected_response_contains: tuple[str, ...] = ()
    expected_response_excludes: tuple[str, ...] = ()
    benchmark_tags: tuple[str, ...] = ()
    isolate_memory: bool = False


QUALITY_LANE_KEYS: tuple[str, ...] = ("staleness", "overwrite", "abstention")
ARCHITECTURE_PROMOTION_GAP_LABEL = "architecture_promotion_gap"


DEFAULT_TELEGRAM_MEMORY_REGRESSION_CASES: tuple[TelegramMemoryRegressionCase, ...] = (
    TelegramMemoryRegressionCase(
        case_id="name_write",
        category="profile_write",
        message="My name is Sarah.",
        expected_bridge_mode="memory_profile_fact_update",
        expected_routing_decision="memory_profile_fact_observation",
        expected_response_contains=("Sarah",),
    ),
    TelegramMemoryRegressionCase(
        case_id="name_query",
        category="profile_query",
        message="What is my name?",
        expected_bridge_mode="memory_profile_fact",
        expected_routing_decision="memory_profile_fact_query",
        expected_response_contains=("Sarah",),
    ),
    TelegramMemoryRegressionCase(
        case_id="occupation_write",
        category="profile_write",
        message="I am an entrepreneur.",
        expected_bridge_mode="memory_profile_fact_update",
        expected_routing_decision="memory_profile_fact_observation",
        expected_response_contains=("entrepreneur",),
    ),
    TelegramMemoryRegressionCase(
        case_id="occupation_query",
        category="profile_query",
        message="What do I do?",
        expected_bridge_mode="memory_profile_fact",
        expected_routing_decision="memory_profile_fact_query",
        expected_response_contains=("entrepreneur",),
    ),
    TelegramMemoryRegressionCase(
        case_id="city_write",
        category="profile_write",
        message="I live in Dubai.",
        expected_bridge_mode="memory_profile_fact_update",
        expected_routing_decision="memory_profile_fact_observation",
        expected_response_contains=("Dubai",),
    ),
    TelegramMemoryRegressionCase(
        case_id="city_explanation",
        category="explanation",
        message="How do you know where I live?",
        expected_bridge_mode="memory_profile_fact_explanation",
        expected_routing_decision="memory_profile_fact_explanation",
        expected_response_contains=("saved memory record", "Dubai"),
    ),
    TelegramMemoryRegressionCase(
        case_id="startup_write",
        category="profile_write",
        message="My startup is Seedify.",
        expected_bridge_mode="memory_profile_fact_update",
        expected_routing_decision="memory_profile_fact_observation",
        expected_response_contains=("Seedify",),
    ),
    TelegramMemoryRegressionCase(
        case_id="startup_explanation",
        category="explanation",
        message="How do you know my startup?",
        expected_bridge_mode="memory_profile_fact_explanation",
        expected_routing_decision="memory_profile_fact_explanation",
        expected_response_contains=("saved memory record", "Seedify"),
    ),
    TelegramMemoryRegressionCase(
        case_id="founder_write",
        category="profile_write",
        message="I am the founder of Spark Swarm.",
        expected_bridge_mode="memory_profile_fact_update",
        expected_routing_decision="memory_profile_fact_observation",
        expected_response_contains=("Spark Swarm",),
    ),
    TelegramMemoryRegressionCase(
        case_id="founder_query",
        category="profile_query",
        message="What company did I found?",
        expected_bridge_mode="memory_profile_fact",
        expected_routing_decision="memory_profile_fact_query",
        expected_response_contains=("Spark Swarm",),
    ),
    TelegramMemoryRegressionCase(
        case_id="startup_query_after_founder",
        category="staleness",
        message="What startup did I create?",
        expected_bridge_mode="memory_profile_fact",
        expected_routing_decision="memory_profile_fact_query",
        expected_response_contains=("Spark Swarm",),
    ),
    TelegramMemoryRegressionCase(
        case_id="startup_explanation_after_founder",
        category="staleness",
        message="How do you know my startup?",
        expected_bridge_mode="memory_profile_fact_explanation",
        expected_routing_decision="memory_profile_fact_explanation",
        expected_response_contains=("saved memory record", "Seedify"),
    ),
    TelegramMemoryRegressionCase(
        case_id="mission_write",
        category="profile_write",
        message="I am trying to survive the hack and revive the companies.",
        expected_bridge_mode="memory_profile_fact_update",
        expected_routing_decision="memory_profile_fact_observation",
        expected_response_contains=("revive the companies",),
    ),
    TelegramMemoryRegressionCase(
        case_id="mission_query",
        category="profile_query",
        message="What am I trying to do now?",
        expected_bridge_mode="memory_profile_fact",
        expected_routing_decision="memory_profile_fact_query",
        expected_response_contains=("revive the companies",),
    ),
    TelegramMemoryRegressionCase(
        case_id="mission_explanation",
        category="explanation",
        message="How do you know what I'm trying to do now?",
        expected_bridge_mode="memory_profile_fact_explanation",
        expected_routing_decision="memory_profile_fact_explanation",
        expected_response_contains=("saved memory record", "revive the companies"),
    ),
    TelegramMemoryRegressionCase(
        case_id="hack_actor_write",
        category="profile_write",
        message="We were hacked by North Korea.",
        expected_bridge_mode="memory_profile_fact_update",
        expected_routing_decision="memory_profile_fact_observation",
        expected_response_contains=("North Korea",),
    ),
    TelegramMemoryRegressionCase(
        case_id="hack_actor_query",
        category="profile_query",
        message="Who hacked us?",
        expected_bridge_mode="memory_profile_fact",
        expected_routing_decision="memory_profile_fact_query",
        expected_response_contains=("North Korea",),
    ),
    TelegramMemoryRegressionCase(
        case_id="spark_role_write",
        category="profile_write",
        message="Spark will be an important part of the rebuild.",
        expected_bridge_mode="memory_profile_fact_update",
        expected_routing_decision="memory_profile_fact_observation",
        expected_response_contains=("important part of the rebuild",),
    ),
    TelegramMemoryRegressionCase(
        case_id="spark_role_query",
        category="profile_query",
        message="What role will Spark play in this?",
        expected_bridge_mode="memory_profile_fact",
        expected_routing_decision="memory_profile_fact_query",
        expected_response_contains=("important part of the rebuild",),
    ),
    TelegramMemoryRegressionCase(
        case_id="spark_role_abstention",
        category="abstention",
        message="What role will Spark play in this?",
        expected_bridge_mode="memory_profile_fact",
        expected_routing_decision="memory_profile_fact_query",
        expected_response_contains=("don't currently have that saved",),
        isolate_memory=True,
    ),
    TelegramMemoryRegressionCase(
        case_id="hack_actor_query_missing",
        category="abstention",
        message="Who hacked us?",
        expected_bridge_mode="memory_profile_fact",
        expected_routing_decision="memory_profile_fact_query",
        expected_response_contains=("don't currently have that saved",),
        isolate_memory=True,
    ),
    TelegramMemoryRegressionCase(
        case_id="timezone_write",
        category="profile_write",
        message="My timezone is Asia/Dubai.",
        expected_bridge_mode="memory_profile_fact_update",
        expected_routing_decision="memory_profile_fact_observation",
        expected_response_contains=("Asia/Dubai",),
    ),
    TelegramMemoryRegressionCase(
        case_id="timezone_query",
        category="profile_query",
        message="What timezone do you have for me?",
        expected_bridge_mode="memory_profile_fact",
        expected_routing_decision="memory_profile_fact_query",
        expected_response_contains=("Asia/Dubai",),
    ),
    TelegramMemoryRegressionCase(
        case_id="country_write",
        category="profile_write",
        message="My country is UAE.",
        expected_bridge_mode="memory_profile_fact_update",
        expected_routing_decision="memory_profile_fact_observation",
        expected_response_contains=("UAE",),
    ),
    TelegramMemoryRegressionCase(
        case_id="country_query",
        category="profile_query",
        message="What country do you have for me?",
        expected_bridge_mode="memory_profile_fact",
        expected_routing_decision="memory_profile_fact_query",
        expected_response_contains=("UAE",),
    ),
    TelegramMemoryRegressionCase(
        case_id="country_explanation",
        category="explanation",
        message="How do you know my country?",
        expected_bridge_mode="memory_profile_fact_explanation",
        expected_routing_decision="memory_profile_fact_explanation",
        expected_response_contains=("saved memory record", "UAE"),
    ),
    TelegramMemoryRegressionCase(
        case_id="identity_summary",
        category="identity",
        message="What do you know about me?",
        expected_bridge_mode="memory_profile_identity",
        expected_routing_decision="memory_profile_identity_summary",
        expected_response_contains=("entrepreneur", "Seedify"),
    ),
    TelegramMemoryRegressionCase(
        case_id="city_overwrite",
        category="overwrite",
        message="I live in Abu Dhabi now.",
        expected_bridge_mode="memory_profile_fact_update",
        expected_routing_decision="memory_profile_fact_observation",
        expected_response_contains=("Abu Dhabi",),
    ),
    TelegramMemoryRegressionCase(
        case_id="country_overwrite",
        category="overwrite",
        message="I moved to Canada.",
        expected_bridge_mode="memory_profile_fact_update",
        expected_routing_decision="memory_profile_fact_observation",
        expected_response_contains=("Canada",),
    ),
    TelegramMemoryRegressionCase(
        case_id="country_query_after_overwrite",
        category="overwrite",
        message="What country do you have for me?",
        expected_bridge_mode="memory_profile_fact",
        expected_routing_decision="memory_profile_fact_query",
        expected_response_contains=("Canada",),
    ),
    TelegramMemoryRegressionCase(
        case_id="city_query_after_overwrite",
        category="overwrite",
        message="Where do I live now?",
        expected_bridge_mode="memory_profile_fact",
        expected_routing_decision="memory_profile_fact_query",
        expected_response_contains=("Abu Dhabi",),
    ),
    TelegramMemoryRegressionCase(
        case_id="city_history_query_after_overwrite",
        category="event_history",
        message="Where did I live before?",
        expected_bridge_mode="memory_profile_fact_history",
        expected_routing_decision="memory_profile_fact_history_query",
        expected_response_contains=("Dubai", "Abu Dhabi"),
    ),
    TelegramMemoryRegressionCase(
        case_id="country_history_query_after_overwrite",
        category="event_history",
        message="What was my previous country?",
        expected_bridge_mode="memory_profile_fact_history",
        expected_routing_decision="memory_profile_fact_history_query",
        expected_response_contains=("UAE", "Canada"),
    ),
    TelegramMemoryRegressionCase(
        case_id="city_event_history_query_after_overwrite",
        category="event_history",
        message="What memory events do you have about where I live?",
        expected_bridge_mode="memory_profile_event_history",
        expected_routing_decision="memory_profile_event_history_query",
        expected_response_contains=("Dubai", "Abu Dhabi"),
    ),
    TelegramMemoryRegressionCase(
        case_id="meeting_write",
        category="event_write",
        message="My meeting with Omar is on May 3.",
        expected_bridge_mode="memory_telegram_event_update",
        expected_routing_decision="memory_telegram_event_observation",
        expected_response_contains=("meeting with Omar on May 3",),
    ),
    TelegramMemoryRegressionCase(
        case_id="event_query_after_meeting_write",
        category="event_query",
        message="What event did I mention?",
        expected_bridge_mode="memory_telegram_event_history",
        expected_routing_decision="memory_telegram_event_query",
        expected_response_contains=("meeting with Omar on May 3",),
    ),
    TelegramMemoryRegressionCase(
        case_id="flight_write",
        category="event_write",
        message="My flight to London is on May 6.",
        expected_bridge_mode="memory_telegram_event_update",
        expected_routing_decision="memory_telegram_event_observation",
        expected_response_contains=("flight to London on May 6",),
    ),
    TelegramMemoryRegressionCase(
        case_id="flight_overwrite",
        category="event_overwrite",
        message="My flight to Paris is on May 9.",
        expected_bridge_mode="memory_telegram_event_update",
        expected_routing_decision="memory_telegram_event_observation",
        expected_response_contains=("flight to Paris on May 9",),
    ),
    TelegramMemoryRegressionCase(
        case_id="latest_flight_query_after_overwrite",
        category="event_overwrite",
        message="What flight do I have?",
        expected_bridge_mode="memory_telegram_event_latest",
        expected_routing_decision="memory_telegram_event_latest_query",
        expected_response_contains=("flight to Paris on May 9",),
        expected_response_excludes=("flight to London on May 6",),
    ),
    TelegramMemoryRegressionCase(
        case_id="flight_history_query_after_overwrite",
        category="event_history",
        message="What flights did I mention?",
        expected_bridge_mode="memory_telegram_event_history",
        expected_routing_decision="memory_telegram_event_query",
        expected_response_contains=("flight to London on May 6", "flight to Paris on May 9"),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_cofounder_write",
        category="generic_memory",
        message="My cofounder is Omar.",
        expected_bridge_mode="memory_generic_observation_update",
        expected_routing_decision="memory_generic_observation",
        expected_response_contains=("cofounder", "Omar"),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_cofounder_overwrite",
        category="generic_memory",
        message="Actually, my cofounder is Sara.",
        expected_bridge_mode="memory_generic_observation_update",
        expected_routing_decision="memory_generic_observation",
        expected_response_contains=("cofounder", "Sara"),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_cofounder_current_query_after_overwrite",
        category="generic_memory",
        message="Who is my cofounder?",
        expected_bridge_mode="memory_profile_fact",
        expected_routing_decision="memory_profile_fact_query",
        expected_response_contains=("Sara",),
        expected_response_excludes=("Omar",),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_cofounder_history_query_after_overwrite",
        category="generic_memory_history",
        message="Who was my cofounder before?",
        expected_bridge_mode="memory_profile_fact_history",
        expected_routing_decision="memory_profile_fact_history_query",
        expected_response_contains=("Omar", "Sara"),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_cofounder_delete",
        category="generic_memory_delete",
        message="Forget my cofounder.",
        expected_bridge_mode="memory_generic_observation_delete",
        expected_routing_decision="memory_generic_observation_delete",
        expected_response_contains=("forget", "cofounder"),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_cofounder_current_query_after_delete",
        category="generic_memory_delete",
        message="Who is my cofounder?",
        expected_bridge_mode="memory_profile_fact",
        expected_routing_decision="memory_profile_fact_query",
        expected_response_contains=("don't currently have",),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_cofounder_history_query_after_delete",
        category="generic_memory_delete",
        message="Who was my cofounder before?",
        expected_bridge_mode="memory_profile_fact_history",
        expected_routing_decision="memory_profile_fact_history_query",
        expected_response_contains=("Sara",),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_cofounder_event_history_query_after_delete",
        category="generic_memory_delete",
        message="Show my cofounder history.",
        expected_bridge_mode="memory_profile_event_history",
        expected_routing_decision="memory_profile_event_history_query",
        expected_response_contains=("Omar", "Sara"),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_decision_write",
        category="generic_memory",
        message="We decided to launch Atlas through agency partners first.",
        expected_bridge_mode="memory_generic_observation_update",
        expected_routing_decision="memory_generic_observation",
        expected_response_contains=("current decision", "agency partners"),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_decision_overwrite",
        category="generic_memory",
        message="Update: we're going with self-serve onboarding first.",
        expected_bridge_mode="memory_generic_observation_update",
        expected_routing_decision="memory_generic_observation",
        expected_response_contains=("current decision", "self-serve onboarding first"),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_decision_current_query_after_overwrite",
        category="generic_memory",
        message="What did we decide?",
        expected_bridge_mode="memory_profile_fact",
        expected_routing_decision="memory_profile_fact_query",
        expected_response_contains=("self-serve onboarding first",),
        expected_response_excludes=("agency partners",),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_decision_history_query_after_overwrite",
        category="generic_memory_history",
        message="What did we decide before?",
        expected_bridge_mode="memory_profile_fact_history",
        expected_routing_decision="memory_profile_fact_history_query",
        expected_response_contains=("agency partners", "self-serve onboarding first"),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_decision_delete",
        category="generic_memory_delete",
        message="Forget our decision.",
        expected_bridge_mode="memory_generic_observation_delete",
        expected_routing_decision="memory_generic_observation_delete",
        expected_response_contains=("forget", "current decision"),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_decision_current_query_after_delete",
        category="generic_memory_delete",
        message="What did we decide?",
        expected_bridge_mode="memory_profile_fact",
        expected_routing_decision="memory_profile_fact_query",
        expected_response_contains=("don't currently have",),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_decision_history_query_after_delete",
        category="generic_memory_delete",
        message="What did we decide before?",
        expected_bridge_mode="memory_profile_fact_history",
        expected_routing_decision="memory_profile_fact_history_query",
        expected_response_contains=("self-serve onboarding first",),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_decision_event_history_query_after_delete",
        category="generic_memory_delete",
        message="Show our decision history.",
        expected_bridge_mode="memory_profile_event_history",
        expected_routing_decision="memory_profile_event_history_query",
        expected_response_contains=("agency partners", "self-serve onboarding first"),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_blocker_write",
        category="generic_memory",
        message="We're blocked on onboarding instrumentation.",
        expected_bridge_mode="memory_generic_observation_update",
        expected_routing_decision="memory_generic_observation",
        expected_response_contains=("current blocker", "onboarding instrumentation"),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_blocker_overwrite",
        category="generic_memory",
        message="Our bottleneck is enterprise lead volume.",
        expected_bridge_mode="memory_generic_observation_update",
        expected_routing_decision="memory_generic_observation",
        expected_response_contains=("current blocker", "enterprise lead volume"),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_blocker_current_query_after_overwrite",
        category="generic_memory",
        message="What are we blocked on?",
        expected_bridge_mode="memory_profile_fact",
        expected_routing_decision="memory_profile_fact_query",
        expected_response_contains=("enterprise lead volume",),
        expected_response_excludes=("onboarding instrumentation",),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_blocker_history_query_after_overwrite",
        category="generic_memory_history",
        message="What were we blocked on before?",
        expected_bridge_mode="memory_profile_fact_history",
        expected_routing_decision="memory_profile_fact_history_query",
        expected_response_contains=("onboarding instrumentation", "enterprise lead volume"),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_blocker_delete",
        category="generic_memory_delete",
        message="Forget our blocker.",
        expected_bridge_mode="memory_generic_observation_delete",
        expected_routing_decision="memory_generic_observation_delete",
        expected_response_contains=("forget", "current blocker"),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_blocker_current_query_after_delete",
        category="generic_memory_delete",
        message="What are we blocked on?",
        expected_bridge_mode="memory_profile_fact",
        expected_routing_decision="memory_profile_fact_query",
        expected_response_contains=("don't currently have",),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_blocker_history_query_after_delete",
        category="generic_memory_delete",
        message="What were we blocked on before?",
        expected_bridge_mode="memory_profile_fact_history",
        expected_routing_decision="memory_profile_fact_history_query",
        expected_response_contains=("enterprise lead volume",),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_blocker_event_history_query_after_delete",
        category="generic_memory_delete",
        message="Show our blocker history.",
        expected_bridge_mode="memory_profile_event_history",
        expected_routing_decision="memory_profile_event_history_query",
        expected_response_contains=("onboarding instrumentation", "enterprise lead volume"),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_status_write",
        category="generic_memory",
        message="Status update: private beta is live with 14 design partners.",
        expected_bridge_mode="memory_generic_observation_update",
        expected_routing_decision="memory_generic_observation",
        expected_response_contains=("current status", "14 design partners"),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_status_overwrite",
        category="generic_memory",
        message="Project status is onboarding activation is above 40 percent.",
        expected_bridge_mode="memory_generic_observation_update",
        expected_routing_decision="memory_generic_observation",
        expected_response_contains=("current status", "above 40 percent"),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_status_current_query_after_overwrite",
        category="generic_memory",
        message="What is the project status?",
        expected_bridge_mode="memory_profile_fact",
        expected_routing_decision="memory_profile_fact_query",
        expected_response_contains=("above 40 percent",),
        expected_response_excludes=("14 design partners",),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_status_history_query_after_overwrite",
        category="generic_memory_history",
        message="What was the project status before?",
        expected_bridge_mode="memory_profile_fact_history",
        expected_routing_decision="memory_profile_fact_history_query",
        expected_response_contains=("14 design partners", "above 40 percent"),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_status_delete",
        category="generic_memory_delete",
        message="Forget our status.",
        expected_bridge_mode="memory_generic_observation_delete",
        expected_routing_decision="memory_generic_observation_delete",
        expected_response_contains=("forget", "current status"),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_status_current_query_after_delete",
        category="generic_memory_delete",
        message="What is the project status?",
        expected_bridge_mode="memory_profile_fact",
        expected_routing_decision="memory_profile_fact_query",
        expected_response_contains=("don't currently have",),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_status_history_query_after_delete",
        category="generic_memory_delete",
        message="What was the project status before?",
        expected_bridge_mode="memory_profile_fact_history",
        expected_routing_decision="memory_profile_fact_history_query",
        expected_response_contains=("above 40 percent",),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_status_event_history_query_after_delete",
        category="generic_memory_delete",
        message="Show our status history.",
        expected_bridge_mode="memory_profile_event_history",
        expected_routing_decision="memory_profile_event_history_query",
        expected_response_contains=("14 design partners", "above 40 percent"),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_commitment_write",
        category="generic_memory",
        message="We committed to closing the pilot by June 1.",
        expected_bridge_mode="memory_generic_observation_update",
        expected_routing_decision="memory_generic_observation",
        expected_response_contains=("current commitment", "June 1"),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_commitment_overwrite",
        category="generic_memory",
        message="Update: our commitment is to close the pilot by June 10.",
        expected_bridge_mode="memory_generic_observation_update",
        expected_routing_decision="memory_generic_observation",
        expected_response_contains=("current commitment", "June 10"),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_commitment_current_query_after_overwrite",
        category="generic_memory",
        message="What is our commitment?",
        expected_bridge_mode="memory_profile_fact",
        expected_routing_decision="memory_profile_fact_query",
        expected_response_contains=("June 10",),
        expected_response_excludes=(),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_commitment_history_query_after_overwrite",
        category="generic_memory_history",
        message="What did we commit to before?",
        expected_bridge_mode="memory_profile_fact_history",
        expected_routing_decision="memory_profile_fact_history_query",
        expected_response_contains=("June 1", "June 10"),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_commitment_delete",
        category="generic_memory_delete",
        message="Forget our commitment.",
        expected_bridge_mode="memory_generic_observation_delete",
        expected_routing_decision="memory_generic_observation_delete",
        expected_response_contains=("forget", "current commitment"),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_commitment_current_query_after_delete",
        category="generic_memory_delete",
        message="What is our commitment?",
        expected_bridge_mode="memory_profile_fact",
        expected_routing_decision="memory_profile_fact_query",
        expected_response_contains=("don't currently have",),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_commitment_history_query_after_delete",
        category="generic_memory_delete",
        message="What did we commit to before?",
        expected_bridge_mode="memory_profile_fact_history",
        expected_routing_decision="memory_profile_fact_history_query",
        expected_response_contains=("June 10",),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_commitment_event_history_query_after_delete",
        category="generic_memory_delete",
        message="Show our commitment history.",
        expected_bridge_mode="memory_profile_event_history",
        expected_routing_decision="memory_profile_event_history_query",
        expected_response_contains=("June 1", "June 10"),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_milestone_write",
        category="generic_memory",
        message="Our next milestone is activation above 50 weekly teams.",
        expected_bridge_mode="memory_generic_observation_update",
        expected_routing_decision="memory_generic_observation",
        expected_response_contains=("current milestone", "50 weekly teams"),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_milestone_overwrite",
        category="generic_memory",
        message="The current milestone is 10 enterprise design partners live.",
        expected_bridge_mode="memory_generic_observation_update",
        expected_routing_decision="memory_generic_observation",
        expected_response_contains=("current milestone", "10 enterprise design partners"),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_milestone_current_query_after_overwrite",
        category="generic_memory",
        message="What is our milestone?",
        expected_bridge_mode="memory_profile_fact",
        expected_routing_decision="memory_profile_fact_query",
        expected_response_contains=("10 enterprise design partners",),
        expected_response_excludes=(),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_milestone_history_query_after_overwrite",
        category="generic_memory_history",
        message="What was the milestone before?",
        expected_bridge_mode="memory_profile_fact_history",
        expected_routing_decision="memory_profile_fact_history_query",
        expected_response_contains=("50 weekly teams", "10 enterprise design partners"),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_milestone_delete",
        category="generic_memory_delete",
        message="Forget our milestone.",
        expected_bridge_mode="memory_generic_observation_delete",
        expected_routing_decision="memory_generic_observation_delete",
        expected_response_contains=("forget", "current milestone"),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_milestone_current_query_after_delete",
        category="generic_memory_delete",
        message="What is our milestone?",
        expected_bridge_mode="memory_profile_fact",
        expected_routing_decision="memory_profile_fact_query",
        expected_response_contains=("don't currently have",),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_milestone_history_query_after_delete",
        category="generic_memory_delete",
        message="What was the milestone before?",
        expected_bridge_mode="memory_profile_fact_history",
        expected_routing_decision="memory_profile_fact_history_query",
        expected_response_contains=("10 enterprise design partners",),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_milestone_event_history_query_after_delete",
        category="generic_memory_delete",
        message="Show our milestone history.",
        expected_bridge_mode="memory_profile_event_history",
        expected_routing_decision="memory_profile_event_history_query",
        expected_response_contains=("50 weekly teams", "10 enterprise design partners"),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_risk_write",
        category="generic_memory",
        message="Our main risk is enterprise churn during onboarding.",
        expected_bridge_mode="memory_generic_observation_update",
        expected_routing_decision="memory_generic_observation",
        expected_response_contains=("current risk", "enterprise churn during onboarding"),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_risk_overwrite",
        category="generic_memory",
        message="The biggest risk is delayed product instrumentation.",
        expected_bridge_mode="memory_generic_observation_update",
        expected_routing_decision="memory_generic_observation",
        expected_response_contains=("current risk", "delayed product instrumentation"),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_risk_current_query_after_overwrite",
        category="generic_memory",
        message="What is our risk?",
        expected_bridge_mode="memory_profile_fact",
        expected_routing_decision="memory_profile_fact_query",
        expected_response_contains=("delayed product instrumentation",),
        expected_response_excludes=("enterprise churn during onboarding",),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_risk_history_query_after_overwrite",
        category="generic_memory_history",
        message="What was the risk before?",
        expected_bridge_mode="memory_profile_fact_history",
        expected_routing_decision="memory_profile_fact_history_query",
        expected_response_contains=("enterprise churn during onboarding", "delayed product instrumentation"),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_risk_delete",
        category="generic_memory_delete",
        message="Forget our risk.",
        expected_bridge_mode="memory_generic_observation_delete",
        expected_routing_decision="memory_generic_observation_delete",
        expected_response_contains=("forget", "current risk"),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_risk_current_query_after_delete",
        category="generic_memory_delete",
        message="What is our risk?",
        expected_bridge_mode="memory_profile_fact",
        expected_routing_decision="memory_profile_fact_query",
        expected_response_contains=("don't currently have",),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_risk_history_query_after_delete",
        category="generic_memory_delete",
        message="What was the risk before?",
        expected_bridge_mode="memory_profile_fact_history",
        expected_routing_decision="memory_profile_fact_history_query",
        expected_response_contains=("delayed product instrumentation",),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_risk_event_history_query_after_delete",
        category="generic_memory_delete",
        message="Show our risk history.",
        expected_bridge_mode="memory_profile_event_history",
        expected_routing_decision="memory_profile_event_history_query",
        expected_response_contains=("enterprise churn during onboarding", "delayed product instrumentation"),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_dependency_write",
        category="generic_memory",
        message="Our dependency is Stripe approval.",
        expected_bridge_mode="memory_generic_observation_update",
        expected_routing_decision="memory_generic_observation",
        expected_response_contains=("current dependency", "Stripe approval"),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_dependency_overwrite",
        category="generic_memory",
        message="The current dependency is partner API access.",
        expected_bridge_mode="memory_generic_observation_update",
        expected_routing_decision="memory_generic_observation",
        expected_response_contains=("current dependency", "partner API access"),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_dependency_current_query_after_overwrite",
        category="generic_memory",
        message="What is our dependency?",
        expected_bridge_mode="memory_profile_fact",
        expected_routing_decision="memory_profile_fact_query",
        expected_response_contains=("partner API access",),
        expected_response_excludes=("Stripe approval",),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_dependency_history_query_after_overwrite",
        category="generic_memory_history",
        message="What was the dependency before?",
        expected_bridge_mode="memory_profile_fact_history",
        expected_routing_decision="memory_profile_fact_history_query",
        expected_response_contains=("Stripe approval", "partner API access"),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_dependency_delete",
        category="generic_memory_delete",
        message="Forget our dependency.",
        expected_bridge_mode="memory_generic_observation_delete",
        expected_routing_decision="memory_generic_observation_delete",
        expected_response_contains=("forget", "current dependency"),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_dependency_current_query_after_delete",
        category="generic_memory_delete",
        message="What is our dependency?",
        expected_bridge_mode="memory_profile_fact",
        expected_routing_decision="memory_profile_fact_query",
        expected_response_contains=("don't currently have",),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_dependency_history_query_after_delete",
        category="generic_memory_delete",
        message="What was the dependency before?",
        expected_bridge_mode="memory_profile_fact_history",
        expected_routing_decision="memory_profile_fact_history_query",
        expected_response_contains=("partner API access",),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_dependency_event_history_query_after_delete",
        category="generic_memory_delete",
        message="Show our dependency history.",
        expected_bridge_mode="memory_profile_event_history",
        expected_routing_decision="memory_profile_event_history_query",
        expected_response_contains=("Stripe approval", "partner API access"),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_constraint_write",
        category="generic_memory",
        message="Our constraint is limited founder bandwidth.",
        expected_bridge_mode="memory_generic_observation_update",
        expected_routing_decision="memory_generic_observation",
        expected_response_contains=("current constraint", "limited founder bandwidth"),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_constraint_overwrite",
        category="generic_memory",
        message="The current constraint is budget for only one engineer.",
        expected_bridge_mode="memory_generic_observation_update",
        expected_routing_decision="memory_generic_observation",
        expected_response_contains=("current constraint", "budget for only one engineer"),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_constraint_current_query_after_overwrite",
        category="generic_memory",
        message="What is our constraint?",
        expected_bridge_mode="memory_profile_fact",
        expected_routing_decision="memory_profile_fact_query",
        expected_response_contains=("budget for only one engineer",),
        expected_response_excludes=("limited founder bandwidth",),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_constraint_history_query_after_overwrite",
        category="generic_memory_history",
        message="What was the constraint before?",
        expected_bridge_mode="memory_profile_fact_history",
        expected_routing_decision="memory_profile_fact_history_query",
        expected_response_contains=("limited founder bandwidth", "budget for only one engineer"),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_constraint_delete",
        category="generic_memory_delete",
        message="Forget our constraint.",
        expected_bridge_mode="memory_generic_observation_delete",
        expected_routing_decision="memory_generic_observation_delete",
        expected_response_contains=("forget", "current constraint"),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_constraint_current_query_after_delete",
        category="generic_memory_delete",
        message="What is our constraint?",
        expected_bridge_mode="memory_profile_fact",
        expected_routing_decision="memory_profile_fact_query",
        expected_response_contains=("don't currently have",),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_constraint_history_query_after_delete",
        category="generic_memory_delete",
        message="What was the constraint before?",
        expected_bridge_mode="memory_profile_fact_history",
        expected_routing_decision="memory_profile_fact_history_query",
        expected_response_contains=("budget for only one engineer",),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_constraint_event_history_query_after_delete",
        category="generic_memory_delete",
        message="Show our constraint history.",
        expected_bridge_mode="memory_profile_event_history",
        expected_routing_decision="memory_profile_event_history_query",
        expected_response_contains=("limited founder bandwidth", "budget for only one engineer"),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_assumption_write",
        category="generic_memory",
        message="Our assumption is users will self-serve after onboarding.",
        expected_bridge_mode="memory_generic_observation_update",
        expected_routing_decision="memory_generic_observation",
        expected_response_contains=("current assumption", "users will self-serve after onboarding"),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_assumption_overwrite",
        category="generic_memory",
        message="The current assumption is enterprise teams need hands-on setup first.",
        expected_bridge_mode="memory_generic_observation_update",
        expected_routing_decision="memory_generic_observation",
        expected_response_contains=("current assumption", "enterprise teams need hands-on setup first"),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_assumption_current_query_after_overwrite",
        category="generic_memory",
        message="What is our assumption?",
        expected_bridge_mode="memory_profile_fact",
        expected_routing_decision="memory_profile_fact_query",
        expected_response_contains=("enterprise teams need hands-on setup first",),
        expected_response_excludes=("users will self-serve after onboarding",),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_assumption_history_query_after_overwrite",
        category="generic_memory_history",
        message="What was the assumption before?",
        expected_bridge_mode="memory_profile_fact_history",
        expected_routing_decision="memory_profile_fact_history_query",
        expected_response_contains=("users will self-serve after onboarding", "enterprise teams need hands-on setup first"),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_assumption_delete",
        category="generic_memory_delete",
        message="Forget our assumption.",
        expected_bridge_mode="memory_generic_observation_delete",
        expected_routing_decision="memory_generic_observation_delete",
        expected_response_contains=("forget", "current assumption"),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_assumption_current_query_after_delete",
        category="generic_memory_delete",
        message="What is our assumption?",
        expected_bridge_mode="memory_profile_fact",
        expected_routing_decision="memory_profile_fact_query",
        expected_response_contains=("don't currently have",),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_assumption_history_query_after_delete",
        category="generic_memory_delete",
        message="What was the assumption before?",
        expected_bridge_mode="memory_profile_fact_history",
        expected_routing_decision="memory_profile_fact_history_query",
        expected_response_contains=("enterprise teams need hands-on setup first",),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_assumption_event_history_query_after_delete",
        category="generic_memory_delete",
        message="Show our assumption history.",
        expected_bridge_mode="memory_profile_event_history",
        expected_routing_decision="memory_profile_event_history_query",
        expected_response_contains=("users will self-serve after onboarding", "enterprise teams need hands-on setup first"),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_owner_write",
        category="generic_memory",
        message="Our owner is Omar.",
        expected_bridge_mode="memory_generic_observation_update",
        expected_routing_decision="memory_generic_observation",
        expected_response_contains=("current owner", "Omar"),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_owner_overwrite",
        category="generic_memory",
        message="The current owner is Sara.",
        expected_bridge_mode="memory_generic_observation_update",
        expected_routing_decision="memory_generic_observation",
        expected_response_contains=("current owner", "Sara"),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_owner_current_query_after_overwrite",
        category="generic_memory",
        message="Who is the owner?",
        expected_bridge_mode="memory_profile_fact",
        expected_routing_decision="memory_profile_fact_query",
        expected_response_contains=("Sara",),
        expected_response_excludes=("Omar",),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_owner_history_query_after_overwrite",
        category="generic_memory_history",
        message="What was the owner before?",
        expected_bridge_mode="memory_profile_fact_history",
        expected_routing_decision="memory_profile_fact_history_query",
        expected_response_contains=("Omar", "Sara"),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_owner_delete",
        category="generic_memory_delete",
        message="Forget our owner.",
        expected_bridge_mode="memory_generic_observation_delete",
        expected_routing_decision="memory_generic_observation_delete",
        expected_response_contains=("forget", "current owner"),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_owner_current_query_after_delete",
        category="generic_memory_delete",
        message="Who is the owner?",
        expected_bridge_mode="memory_profile_fact",
        expected_routing_decision="memory_profile_fact_query",
        expected_response_contains=("don't currently have",),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_owner_history_query_after_delete",
        category="generic_memory_delete",
        message="What was the owner before?",
        expected_bridge_mode="memory_profile_fact_history",
        expected_routing_decision="memory_profile_fact_history_query",
        expected_response_contains=("Sara",),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_owner_event_history_query_after_delete",
        category="generic_memory_delete",
        message="Show our owner history.",
        expected_bridge_mode="memory_profile_event_history",
        expected_routing_decision="memory_profile_event_history_query",
        expected_response_contains=("Omar", "Sara"),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_long_run_owner_write_initial",
        category="generic_memory_churn",
        message="Our owner is Omar.",
        expected_bridge_mode="memory_generic_observation_update",
        expected_routing_decision="memory_generic_observation",
        expected_response_contains=("current owner", "Omar"),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_long_run_risk_write_initial",
        category="generic_memory_churn",
        message="Our main risk is enterprise churn during onboarding.",
        expected_bridge_mode="memory_generic_observation_update",
        expected_routing_decision="memory_generic_observation",
        expected_response_contains=("current risk", "enterprise churn during onboarding"),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_long_run_owner_overwrite",
        category="generic_memory_churn",
        message="The current owner is Sara.",
        expected_bridge_mode="memory_generic_observation_update",
        expected_routing_decision="memory_generic_observation",
        expected_response_contains=("current owner", "Sara"),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_long_run_dependency_write",
        category="generic_memory_churn",
        message="Our dependency is Stripe approval.",
        expected_bridge_mode="memory_generic_observation_update",
        expected_routing_decision="memory_generic_observation",
        expected_response_contains=("current dependency", "Stripe approval"),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_long_run_owner_delete",
        category="generic_memory_churn",
        message="Forget our owner.",
        expected_bridge_mode="memory_generic_observation_delete",
        expected_routing_decision="memory_generic_observation_delete",
        expected_response_contains=("forget", "current owner"),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_long_run_owner_rewrite",
        category="generic_memory_churn",
        message="The current owner is Nadia.",
        expected_bridge_mode="memory_generic_observation_update",
        expected_routing_decision="memory_generic_observation",
        expected_response_contains=("current owner", "Nadia"),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_long_run_risk_overwrite",
        category="generic_memory_churn",
        message="The biggest risk is delayed product instrumentation.",
        expected_bridge_mode="memory_generic_observation_update",
        expected_routing_decision="memory_generic_observation",
        expected_response_contains=("current risk", "delayed product instrumentation"),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_long_run_risk_delete",
        category="generic_memory_churn",
        message="Forget our risk.",
        expected_bridge_mode="memory_generic_observation_delete",
        expected_routing_decision="memory_generic_observation_delete",
        expected_response_contains=("forget", "current risk"),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_long_run_risk_rewrite",
        category="generic_memory_churn",
        message="Our main risk is model drift in onboarding scoring.",
        expected_bridge_mode="memory_generic_observation_update",
        expected_routing_decision="memory_generic_observation",
        expected_response_contains=("current risk", "model drift in onboarding scoring"),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_long_run_owner_current_query",
        category="generic_memory_churn",
        message="Who is the owner?",
        expected_bridge_mode="memory_profile_fact",
        expected_routing_decision="memory_profile_fact_query",
        expected_response_contains=("Nadia",),
        expected_response_excludes=("Sara", "Omar"),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_long_run_owner_history_query",
        category="generic_memory_churn",
        message="What was the owner before?",
        expected_bridge_mode="memory_profile_fact_history",
        expected_routing_decision="memory_profile_fact_history_query",
        expected_response_contains=("Nadia", "Sara"),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_long_run_owner_event_history_query",
        category="generic_memory_churn",
        message="Show our owner history.",
        expected_bridge_mode="memory_profile_event_history",
        expected_routing_decision="memory_profile_event_history_query",
        expected_response_contains=("Omar", "Sara", "Nadia"),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_long_run_risk_current_query",
        category="generic_memory_churn",
        message="What is our risk?",
        expected_bridge_mode="memory_profile_fact",
        expected_routing_decision="memory_profile_fact_query",
        expected_response_contains=("model drift in onboarding scoring",),
        expected_response_excludes=("delayed product instrumentation", "enterprise churn during onboarding"),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_long_run_risk_history_query",
        category="generic_memory_churn",
        message="What was the risk before?",
        expected_bridge_mode="memory_profile_fact_history",
        expected_routing_decision="memory_profile_fact_history_query",
        expected_response_contains=("model drift in onboarding scoring", "delayed product instrumentation"),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_long_run_risk_event_history_query",
        category="generic_memory_churn",
        message="Show our risk history.",
        expected_bridge_mode="memory_profile_event_history",
        expected_routing_decision="memory_profile_event_history_query",
        expected_response_contains=(
            "enterprise churn during onboarding",
            "delayed product instrumentation",
            "model drift in onboarding scoring",
        ),
    ),
    TelegramMemoryRegressionCase(
        case_id="generic_long_run_dependency_current_query",
        category="generic_memory_churn",
        message="What is our dependency?",
        expected_bridge_mode="memory_profile_fact",
        expected_routing_decision="memory_profile_fact_query",
        expected_response_contains=("Stripe approval",),
    ),
    TelegramMemoryRegressionCase(
        case_id="open_evidence_write_onboarding",
        category="open_memory_recall",
        message="Users keep dropping during onboarding because Stripe verification fails.",
        benchmark_tags=("structured_evidence", "open_recall_seed"),
    ),
    TelegramMemoryRegressionCase(
        case_id="open_evidence_recall_onboarding",
        category="open_memory_recall",
        message="What evidence do you have about onboarding?",
        expected_bridge_mode="memory_open_recall",
        expected_routing_decision="memory_open_recall_query",
        expected_response_contains=("onboarding", "Stripe verification fails"),
        benchmark_tags=("structured_evidence", "open_recall"),
    ),
    TelegramMemoryRegressionCase(
        case_id="open_episode_write_demo",
        category="open_memory_recall",
        message="The pricing page felt confusing during the demo.",
        benchmark_tags=("raw_episode", "open_recall_seed"),
    ),
    TelegramMemoryRegressionCase(
        case_id="open_episode_recall_demo",
        category="open_memory_recall",
        message="What happened during the demo?",
        expected_bridge_mode="memory_open_recall",
        expected_routing_decision="memory_open_recall_query",
        expected_response_contains=("demo", "pricing page felt confusing"),
        benchmark_tags=("raw_episode", "open_recall"),
    ),
    TelegramMemoryRegressionCase(
        case_id="belief_write_onboarding",
        category="belief_memory_recall",
        message="I think enterprise teams need hands-on onboarding.",
        benchmark_tags=("belief", "belief_seed"),
    ),
    TelegramMemoryRegressionCase(
        case_id="belief_recall_onboarding",
        category="belief_memory_recall",
        message="What is your current belief about onboarding?",
        expected_bridge_mode="memory_belief_recall",
        expected_routing_decision="memory_belief_recall_query",
        expected_response_contains=("onboarding", "hands-on onboarding", "inferred belief"),
        benchmark_tags=("belief", "belief_recall"),
    ),
    TelegramMemoryRegressionCase(
        case_id="mixed_session_owner_write_initial",
        category="mixed_memory_churn",
        message="Our owner is Omar.",
        expected_bridge_mode="memory_generic_observation_update",
        expected_routing_decision="memory_generic_observation",
        expected_response_contains=("current owner", "Omar"),
    ),
    TelegramMemoryRegressionCase(
        case_id="mixed_session_meeting_write",
        category="mixed_memory_churn",
        message="My meeting with Omar is on May 3.",
        expected_bridge_mode="memory_telegram_event_update",
        expected_routing_decision="memory_telegram_event_observation",
        expected_response_contains=("meeting with Omar on May 3",),
    ),
    TelegramMemoryRegressionCase(
        case_id="mixed_session_owner_overwrite",
        category="mixed_memory_churn",
        message="The current owner is Sara.",
        expected_bridge_mode="memory_generic_observation_update",
        expected_routing_decision="memory_generic_observation",
        expected_response_contains=("current owner", "Sara"),
    ),
    TelegramMemoryRegressionCase(
        case_id="mixed_session_flight_write",
        category="mixed_memory_churn",
        message="My flight to London is on May 6.",
        expected_bridge_mode="memory_telegram_event_update",
        expected_routing_decision="memory_telegram_event_observation",
        expected_response_contains=("flight to London on May 6",),
    ),
    TelegramMemoryRegressionCase(
        case_id="mixed_session_dependency_write",
        category="mixed_memory_churn",
        message="Our dependency is Stripe approval.",
        expected_bridge_mode="memory_generic_observation_update",
        expected_routing_decision="memory_generic_observation",
        expected_response_contains=("current dependency", "Stripe approval"),
    ),
    TelegramMemoryRegressionCase(
        case_id="mixed_session_flight_overwrite",
        category="mixed_memory_churn",
        message="My flight to Paris is on May 9.",
        expected_bridge_mode="memory_telegram_event_update",
        expected_routing_decision="memory_telegram_event_observation",
        expected_response_contains=("flight to Paris on May 9",),
    ),
    TelegramMemoryRegressionCase(
        case_id="mixed_session_owner_delete",
        category="mixed_memory_churn",
        message="Forget our owner.",
        expected_bridge_mode="memory_generic_observation_delete",
        expected_routing_decision="memory_generic_observation_delete",
        expected_response_contains=("forget", "current owner"),
    ),
    TelegramMemoryRegressionCase(
        case_id="mixed_session_owner_rewrite",
        category="mixed_memory_churn",
        message="The current owner is Nadia.",
        expected_bridge_mode="memory_generic_observation_update",
        expected_routing_decision="memory_generic_observation",
        expected_response_contains=("current owner", "Nadia"),
    ),
    TelegramMemoryRegressionCase(
        case_id="mixed_session_risk_write",
        category="mixed_memory_churn",
        message="Our main risk is delayed product instrumentation.",
        expected_bridge_mode="memory_generic_observation_update",
        expected_routing_decision="memory_generic_observation",
        expected_response_contains=("current risk", "delayed product instrumentation"),
    ),
    TelegramMemoryRegressionCase(
        case_id="mixed_session_owner_current_query",
        category="mixed_memory_churn",
        message="Who is the owner?",
        expected_bridge_mode="memory_profile_fact",
        expected_routing_decision="memory_profile_fact_query",
        expected_response_contains=("Nadia",),
        expected_response_excludes=("Sara", "Omar"),
    ),
    TelegramMemoryRegressionCase(
        case_id="mixed_session_owner_history_query",
        category="mixed_memory_churn",
        message="What was the owner before?",
        expected_bridge_mode="memory_profile_fact_history",
        expected_routing_decision="memory_profile_fact_history_query",
        expected_response_contains=("Nadia", "Sara"),
    ),
    TelegramMemoryRegressionCase(
        case_id="mixed_session_owner_event_history_query",
        category="mixed_memory_churn",
        message="Show our owner history.",
        expected_bridge_mode="memory_profile_event_history",
        expected_routing_decision="memory_profile_event_history_query",
        expected_response_contains=("Omar", "Sara", "Nadia"),
    ),
    TelegramMemoryRegressionCase(
        case_id="mixed_session_dependency_current_query",
        category="mixed_memory_churn",
        message="What is our dependency?",
        expected_bridge_mode="memory_profile_fact",
        expected_routing_decision="memory_profile_fact_query",
        expected_response_contains=("Stripe approval",),
    ),
    TelegramMemoryRegressionCase(
        case_id="mixed_session_risk_current_query",
        category="mixed_memory_churn",
        message="What is our risk?",
        expected_bridge_mode="memory_profile_fact",
        expected_routing_decision="memory_profile_fact_query",
        expected_response_contains=("delayed product instrumentation",),
    ),
    TelegramMemoryRegressionCase(
        case_id="mixed_session_latest_flight_query",
        category="mixed_memory_churn",
        message="What flight do I have?",
        expected_bridge_mode="memory_telegram_event_latest",
        expected_routing_decision="memory_telegram_event_latest_query",
        expected_response_contains=("flight to Paris on May 9",),
        expected_response_excludes=("flight to London on May 6",),
    ),
    TelegramMemoryRegressionCase(
        case_id="mixed_session_flight_history_query",
        category="mixed_memory_churn",
        message="What flights did I mention?",
        expected_bridge_mode="memory_telegram_event_history",
        expected_routing_decision="memory_telegram_event_query",
        expected_response_contains=("flight to London on May 6", "flight to Paris on May 9"),
    ),
)


@dataclass(frozen=True)
class TelegramMemoryRegressionResult:
    output_dir: Path
    payload: dict[str, Any]

    def to_json(self) -> str:
        return json.dumps(self.payload, indent=2)

    def to_text(self) -> str:
        summary = self.payload.get("summary") if isinstance(self.payload, dict) else {}
        lines = ["Spark memory Telegram regression"]
        lines.append(f"- output_dir: {self.output_dir}")
        if isinstance(summary, dict):
            lines.append(f"- status: {summary.get('status') or 'unknown'}")
            lines.append(f"- cases: {summary.get('case_count', 0)}")
            lines.append(f"- matched: {summary.get('matched_case_count', 0)}")
            lines.append(f"- mismatched: {summary.get('mismatched_case_count', 0)}")
            lines.append(f"- selected_user_id: {summary.get('selected_user_id') or 'unknown'}")
            lines.append(f"- selected_chat_id: {summary.get('selected_chat_id') or 'unknown'}")
            lines.append(f"- kb_has_probe_coverage: {'yes' if summary.get('kb_has_probe_coverage') else 'no'}")
            lines.append(
                f"- kb_current_state_hits: "
                f"{summary.get('kb_current_state_hits', 0)}/{summary.get('kb_current_state_total', 0)}"
            )
            lines.append(
                f"- kb_evidence_hits: "
                f"{summary.get('kb_evidence_hits', 0)}/{summary.get('kb_evidence_total', 0)}"
            )
            blocked_reason = str(summary.get("blocked_reason") or "").strip()
            if blocked_reason:
                lines.append(f"- blocked_reason: {blocked_reason}")
        mismatches = self.payload.get("mismatches") if isinstance(self.payload, dict) else None
        if isinstance(mismatches, list) and mismatches:
            lines.append("- mismatch_cases: " + ", ".join(str(item.get("case_id") or "unknown") for item in mismatches[:8]))
        return "\n".join(lines)


def run_telegram_memory_regression(
    *,
    config_manager: ConfigManager,
    state_db: StateDB,
    output_dir: str | Path | None = None,
    user_id: str | None = None,
    username: str | None = None,
    chat_id: str | None = None,
    kb_limit: int = 25,
    validator_root: str | Path | None = None,
    write_path: str | Path | None = None,
    case_ids: list[str] | None = None,
    categories: list[str] | None = None,
    cases: list[TelegramMemoryRegressionCase] | tuple[TelegramMemoryRegressionCase, ...] | None = None,
    benchmark_pack_ids: list[str] | tuple[str, ...] | None = None,
    baseline_names: list[str] | tuple[str, ...] | None = None,
) -> TelegramMemoryRegressionResult:
    from spark_intelligence.gateway.runtime import gateway_ask_telegram

    resolved_output_dir = Path(output_dir) if output_dir else _default_output_dir(config_manager)
    resolved_output_dir.mkdir(parents=True, exist_ok=True)
    resolved_kb_output_dir = resolved_output_dir / "kb"
    resolved_write_path = Path(write_path) if write_path else resolved_output_dir / "telegram-memory-regression.json"
    kb_write_path = resolved_output_dir / "telegram-memory-kb.json"
    regression_summary_markdown_path = resolved_output_dir / "regression-summary.md"
    regression_cases_json_path = resolved_output_dir / "regression-cases.json"
    architecture_benchmark_output_dir = resolved_output_dir / "architecture-benchmark"
    architecture_live_comparison_output_dir = resolved_output_dir / "architecture-live-comparison"

    case_payloads: list[dict[str, Any]] = []
    mismatches: list[dict[str, Any]] = []
    selected_user_id = str(user_id or "").strip() or None
    selected_chat_id = str(chat_id or "").strip() or None
    requested_case_ids = [str(item).strip() for item in (case_ids or []) if str(item).strip()]
    requested_categories = [str(item).strip() for item in (categories or []) if str(item).strip()]
    requested_benchmark_pack_ids = [str(item).strip() for item in (benchmark_pack_ids or []) if str(item).strip()]
    requested_baseline_names = [str(item).strip() for item in (baseline_names or []) if str(item).strip()]
    selected_pack_cases = cases
    benchmark_pack_error: str | None = None
    if selected_pack_cases is None and requested_benchmark_pack_ids:
        from spark_intelligence.memory.benchmark_packs import (
            flatten_benchmark_pack_cases,
            select_telegram_memory_benchmark_packs,
        )

        try:
            selected_pack_cases = flatten_benchmark_pack_cases(
                select_telegram_memory_benchmark_packs(requested_benchmark_pack_ids)
            )
        except ValueError as exc:
            benchmark_pack_error = str(exc)
    selected_cases = _select_regression_cases(
        case_ids=requested_case_ids,
        categories=requested_categories,
        cases_source=selected_pack_cases,
    )
    selection_summary = _selection_summary(selected_cases)
    filter_summary = {
        "requested_case_ids": requested_case_ids,
        "requested_categories": requested_categories,
        "requested_benchmark_pack_ids": requested_benchmark_pack_ids,
        "requested_baseline_names": requested_baseline_names,
    }
    if selected_user_id is None:
        selected_user_id, selected_chat_id = _allocate_regression_identity(chat_id=selected_chat_id)
        _prepare_regression_identity(
            state_db=state_db,
            external_user_id=selected_user_id,
            username=username or "memory-regression",
        )
    elif selected_chat_id is None:
        selected_chat_id = selected_user_id

    if benchmark_pack_error or not selected_cases:
        errors = [benchmark_pack_error] if benchmark_pack_error else ["no_regression_cases_selected"]
        payload = {
            "summary": {
                "status": "invalid_request",
                "case_count": 0,
                "matched_case_count": 0,
                "mismatched_case_count": 0,
                "selected_user_id": selected_user_id,
                "selected_chat_id": selected_chat_id,
                "human_id": f"human:telegram:{selected_user_id}" if selected_user_id else None,
                "issue_labels": [],
                "kb_has_probe_coverage": False,
                "kb_issue_labels": [],
                "kb_current_state_hits": 0,
                "kb_current_state_total": 0,
                "kb_evidence_hits": 0,
                "kb_evidence_total": 0,
                **filter_summary,
                **selection_summary,
            },
            "errors": errors,
            "cases": [],
            "mismatches": [],
            "inspection": None,
            "kb_compile": None,
            "artifact_paths": {
                "summary_json": str(resolved_write_path),
                "kb_output_dir": str(resolved_kb_output_dir),
                "kb_json": str(kb_write_path),
                "regression_report_markdown": str(regression_summary_markdown_path),
                "regression_cases_json": str(regression_cases_json_path),
            },
        }
        resolved_write_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return TelegramMemoryRegressionResult(output_dir=resolved_output_dir, payload=payload)

    for case in selected_cases:
        case_user_id = selected_user_id
        case_chat_id = selected_chat_id
        if case.isolate_memory and selected_user_id:
            case_user_id = f"{selected_user_id}-{case.case_id}"
            case_chat_id = f"{(selected_chat_id or selected_user_id)}-{case.case_id}"
            _prepare_regression_identity(
                state_db=state_db,
                external_user_id=case_user_id,
                username=username or f"Regression {case.case_id}",
            )
        raw = gateway_ask_telegram(
            config_manager=config_manager,
            state_db=state_db,
            message=case.message,
            user_id=case_user_id,
            username=username,
            chat_id=case_chat_id,
            as_json=True,
        )
        gateway_payload = _parse_json_object(raw)
        if selected_user_id is None:
            candidate_user_id = str(gateway_payload.get("user_id") or "").strip()
            if candidate_user_id:
                selected_user_id = candidate_user_id
        if selected_chat_id is None:
            candidate_chat_id = str(gateway_payload.get("chat_id") or "").strip()
            if candidate_chat_id:
                selected_chat_id = candidate_chat_id
            elif selected_user_id:
                selected_chat_id = selected_user_id
        case_result = _build_case_result(case=case, payload=gateway_payload)
        if case_result.get("decision") != "allowed":
            blocked_reason = _blocked_reason_for_case(case_result)
            payload = {
                "summary": {
                    "status": "blocked_precondition",
                    "case_count": len(selected_cases),
                    "matched_case_count": 0,
                    "mismatched_case_count": 0,
                    "selected_user_id": selected_user_id,
                    "selected_chat_id": selected_chat_id,
                    "human_id": f"human:telegram:{selected_user_id}" if selected_user_id else None,
                    "issue_labels": [],
                    "kb_has_probe_coverage": False,
                    "kb_issue_labels": [],
                    "kb_current_state_hits": 0,
                    "kb_current_state_total": 0,
                    "kb_evidence_hits": 0,
                    "kb_evidence_total": 0,
                    "blocked_reason": blocked_reason,
                    **filter_summary,
                    **selection_summary,
                },
                "cases": [case_result],
                "mismatches": [],
                "inspection": None,
                "kb_compile": None,
                "artifact_paths": {
                    "summary_json": str(resolved_write_path),
                    "kb_output_dir": str(resolved_kb_output_dir),
                    "kb_json": str(kb_write_path),
                    "regression_report_markdown": str(regression_summary_markdown_path),
                    "regression_cases_json": str(regression_cases_json_path),
                },
            }
            resolved_write_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            return TelegramMemoryRegressionResult(output_dir=resolved_output_dir, payload=payload)
        case_payloads.append(case_result)
        if not case_result.get("matched_expectations", True):
            mismatches.append(case_result)

    resolved_human_id = f"human:telegram:{selected_user_id}" if selected_user_id else None
    inspection_payload: dict[str, Any] | None = None
    if resolved_human_id:
        inspection_result = inspect_human_memory_in_memory(
            config_manager=config_manager,
            state_db=state_db,
            human_id=resolved_human_id,
            actor_id="memory_regression",
        )
        inspection_payload = _parse_json_object(inspection_result.to_json())

    architecture_benchmark_result = benchmark_memory_architectures(
        config_manager=config_manager,
        output_dir=architecture_benchmark_output_dir,
        validator_root=validator_root,
        baseline_names=requested_baseline_names or None,
    )
    architecture_benchmark_payload = architecture_benchmark_result.payload
    architecture_summary_path = (
        (architecture_benchmark_payload.get("artifact_paths") or {}).get("summary_markdown")
        if isinstance(architecture_benchmark_payload, dict)
        else None
    )
    architecture_live_comparison_result = compare_telegram_memory_architectures(
        config_manager=config_manager,
        case_payloads=case_payloads,
        selected_cases=selected_cases,
        output_dir=architecture_live_comparison_output_dir,
        validator_root=validator_root,
        baseline_names=requested_baseline_names or None,
    )
    architecture_live_comparison_payload = architecture_live_comparison_result.payload
    architecture_live_comparison_summary_path = (
        (architecture_live_comparison_payload.get("artifact_paths") or {}).get("summary_markdown")
        if isinstance(architecture_live_comparison_payload, dict)
        else None
    )
    regression_summary_markdown_path.write_text(
        _build_regression_summary_markdown(
            selected_user_id=selected_user_id,
            selected_chat_id=selected_chat_id,
            case_payloads=case_payloads,
            mismatches=mismatches,
            inspection_payload=inspection_payload,
            architecture_benchmark_payload=architecture_benchmark_payload,
            architecture_live_comparison_payload=architecture_live_comparison_payload,
        ),
        encoding="utf-8",
    )
    regression_cases_json_path.write_text(
        json.dumps(
            {
                "selected_user_id": selected_user_id,
                "selected_chat_id": selected_chat_id,
                "human_id": resolved_human_id,
                "cases": case_payloads,
                "mismatches": mismatches,
                "inspection": inspection_payload,
                "architecture_live_comparison": architecture_live_comparison_payload,
                **filter_summary,
                **selection_summary,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    repo_sources = [str(regression_summary_markdown_path), str(regression_cases_json_path)]
    if architecture_summary_path:
        repo_sources.append(str(architecture_summary_path))
    if architecture_live_comparison_summary_path:
        repo_sources.append(str(architecture_live_comparison_summary_path))
    kb_result = build_telegram_state_knowledge_base(
        config_manager=config_manager,
        output_dir=resolved_kb_output_dir,
        limit=max(int(kb_limit), 1),
        chat_id=selected_chat_id,
        repo_sources=repo_sources,
        write_path=kb_write_path,
        validator_root=validator_root,
    )
    kb_payload = kb_result.payload
    current_probe = _probe_row(kb_payload, "current_state")
    evidence_probe = _probe_row(kb_payload, "evidence")
    issue_labels = _build_regression_issue_labels(
        kb_payload=kb_payload,
        architecture_live_comparison_payload=architecture_live_comparison_payload,
    )
    summary = {
        "status": "ok",
        "case_count": len(case_payloads),
        "matched_case_count": len(case_payloads) - len(mismatches),
        "mismatched_case_count": len(mismatches),
        "selected_user_id": selected_user_id,
        "selected_chat_id": selected_chat_id,
        "human_id": resolved_human_id,
        "issue_labels": issue_labels,
        "architecture_runtime_sdk_class": _nested_get(
            architecture_benchmark_payload,
            "summary",
            "runtime_sdk_class",
            default=None,
        ),
        "architecture_runtime_memory_architecture": _nested_get(
            architecture_benchmark_payload,
            "summary",
            "runtime_memory_architecture",
            default=None,
        ),
        "architecture_compared_baselines": _nested_get(
            architecture_benchmark_payload,
            "summary",
            "baseline_names",
            default=[],
        ),
        "architecture_documented_frontier": _nested_get(
            architecture_benchmark_payload,
            "summary",
            "documented_frontier_architecture",
            default=None,
        ),
        "architecture_runtime_matches_documented_frontier": _nested_get(
            architecture_benchmark_payload,
            "summary",
            "runtime_matches_documented_frontier",
            default=False,
        ),
        "architecture_product_memory_leaders": _nested_get(
            architecture_benchmark_payload,
            "summary",
            "product_memory_leader_names",
            default=[],
        ),
        "live_architecture_case_count": _nested_get(
            architecture_live_comparison_payload,
            "summary",
            "case_count",
            default=0,
        ),
        "live_architecture_compared_baselines": _nested_get(
            architecture_live_comparison_payload,
            "summary",
            "baseline_names",
            default=[],
        ),
        "live_architecture_leaders": _nested_get(
            architecture_live_comparison_payload,
            "summary",
            "leader_names",
            default=[],
        ),
        "live_architecture_recommended_runtime": _nested_get(
            architecture_live_comparison_payload,
            "summary",
            "recommended_runtime_architecture",
            default=None,
        ),
        "live_architecture_runtime_matches_leader": _nested_get(
            architecture_live_comparison_payload,
            "summary",
            "runtime_matches_live_leader",
            default=False,
        ),
        "kb_has_probe_coverage": _nested_get(kb_payload, "failure_taxonomy", "summary", "has_probe_coverage", default=False),
        "kb_issue_labels": _nested_get(kb_payload, "failure_taxonomy", "summary", "issue_labels", default=[]),
        "kb_current_state_hits": current_probe.get("hits", 0),
        "kb_current_state_total": current_probe.get("total", 0),
        "kb_evidence_hits": evidence_probe.get("hits", 0),
        "kb_evidence_total": evidence_probe.get("total", 0),
        **filter_summary,
        **selection_summary,
    }
    payload = {
        "summary": summary,
        "cases": case_payloads,
        "mismatches": mismatches,
        "inspection": inspection_payload,
        "architecture_benchmark": architecture_benchmark_payload,
        "architecture_live_comparison": architecture_live_comparison_payload,
        "kb_compile": kb_payload,
        "artifact_paths": {
            "summary_json": str(resolved_write_path),
            "kb_output_dir": str(resolved_kb_output_dir),
            "kb_json": str(kb_write_path),
            "regression_report_markdown": str(regression_summary_markdown_path),
            "regression_cases_json": str(regression_cases_json_path),
            "architecture_benchmark_dir": str(architecture_benchmark_output_dir),
            "architecture_benchmark_markdown": str(architecture_summary_path) if architecture_summary_path else None,
            "architecture_live_comparison_dir": str(architecture_live_comparison_output_dir),
            "architecture_live_comparison_markdown": (
                str(architecture_live_comparison_summary_path) if architecture_live_comparison_summary_path else None
            ),
        },
    }
    resolved_write_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return TelegramMemoryRegressionResult(output_dir=resolved_output_dir, payload=payload)


def _allocate_regression_identity(*, chat_id: str | None) -> tuple[str, str]:
    run_suffix = uuid4().hex[:8]
    user_id = f"spark-memory-regression-user-{run_suffix}"
    resolved_chat_id = str(chat_id or "").strip() or user_id
    return user_id, resolved_chat_id


def _prepare_regression_identity(
    *,
    state_db: StateDB,
    external_user_id: str,
    username: str,
) -> None:
    approve_pairing(
        state_db=state_db,
        channel_id="telegram",
        external_user_id=external_user_id,
        display_name=username,
    )
    rename_agent_identity(
        state_db=state_db,
        human_id=f"human:telegram:{external_user_id}",
        new_name="Atlas",
        source_surface="memory_regression",
        source_ref="memory-regression-setup",
    )
    consume_pairing_welcome(
        state_db=state_db,
        channel_id="telegram",
        external_user_id=external_user_id,
    )


def _build_case_result(*, case: TelegramMemoryRegressionCase, payload: dict[str, Any]) -> dict[str, Any]:
    result = payload.get("result") if isinstance(payload.get("result"), dict) else {}
    detail = result.get("detail") if isinstance(result.get("detail"), dict) else {}
    bridge_mode = str(detail.get("bridge_mode") or payload.get("bridge_mode") or "").strip()
    routing_decision = str(detail.get("routing_decision") or payload.get("routing_decision") or "").strip()
    response_text = str(detail.get("response_text") or payload.get("response_text") or "").strip()
    mismatches: list[str] = []
    if case.expected_bridge_mode and bridge_mode != case.expected_bridge_mode:
        mismatches.append(f"bridge_mode:{bridge_mode or 'missing'}")
    if case.expected_routing_decision and routing_decision != case.expected_routing_decision:
        mismatches.append(f"routing_decision:{routing_decision or 'missing'}")
    lowered_response = response_text.lower()
    for expected_fragment in case.expected_response_contains:
        if expected_fragment.lower() not in lowered_response:
            mismatches.append(f"response_missing:{expected_fragment}")
    for forbidden_fragment in case.expected_response_excludes:
        if forbidden_fragment.lower() in lowered_response:
            mismatches.append(f"response_forbidden:{forbidden_fragment}")
    return {
        "case_id": case.case_id,
        "category": case.category,
        "message": case.message,
        "expected_response_contains": list(case.expected_response_contains),
        "expected_response_excludes": list(case.expected_response_excludes),
        "benchmark_tags": list(case.benchmark_tags),
        "decision": str(result.get("decision") or payload.get("decision") or "").strip(),
        "bridge_mode": bridge_mode,
        "routing_decision": routing_decision,
        "response_text": response_text,
        "trace_ref": str(detail.get("trace_ref") or payload.get("trace_ref") or "").strip(),
        "matched_expectations": not mismatches,
        "mismatches": mismatches,
        "gateway_payload": payload,
    }


def _default_output_dir(config_manager: ConfigManager) -> Path:
    return config_manager.paths.home / "artifacts" / "telegram-memory-regression"


def _select_regression_cases(
    *,
    case_ids: list[str] | None,
    categories: list[str] | None,
    cases_source: list[TelegramMemoryRegressionCase] | tuple[TelegramMemoryRegressionCase, ...] | None = None,
) -> tuple[TelegramMemoryRegressionCase, ...]:
    requested_case_ids = {str(item).strip() for item in (case_ids or []) if str(item).strip()}
    requested_categories = {str(item).strip() for item in (categories or []) if str(item).strip()}
    available_cases = tuple(cases_source or DEFAULT_TELEGRAM_MEMORY_REGRESSION_CASES)
    if not requested_case_ids and not requested_categories:
        return available_cases
    selected: list[TelegramMemoryRegressionCase] = []
    for case in available_cases:
        if requested_case_ids and case.case_id not in requested_case_ids:
            continue
        if requested_categories and case.category not in requested_categories:
            continue
        selected.append(case)
    return tuple(selected)


def _selection_summary(cases: tuple[TelegramMemoryRegressionCase, ...]) -> dict[str, Any]:
    category_counts = _build_category_counts(case.category for case in cases)
    return {
        "selected_case_ids": [case.case_id for case in cases],
        "selected_categories": sorted(category_counts),
        "category_counts": category_counts,
        "quality_lanes": _build_quality_lanes(category_counts),
    }


def _nested_get(payload: dict[str, Any], *path: str, default: Any = None) -> Any:
    current: Any = payload
    for key in path:
        if not isinstance(current, dict):
            return default
        current = current.get(key)
    return default if current is None else current


def _parse_json_object(raw: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {"raw_output": raw}
    return parsed if isinstance(parsed, dict) else {"raw_output": raw}


def _probe_row(payload: dict[str, Any], probe_type: str) -> dict[str, Any]:
    rows = _nested_get(payload, "failure_taxonomy", "probe_rows", default=[])
    if not isinstance(rows, list):
        return {}
    for row in rows:
        if isinstance(row, dict) and str(row.get("probe_type") or "").strip() == probe_type:
            return row
    return {}


def _blocked_reason_for_case(case_result: dict[str, Any]) -> str:
    decision = str(case_result.get("decision") or "").strip()
    response_text = str(case_result.get("response_text") or "").strip()
    case_id = str(case_result.get("case_id") or "").strip() or "unknown_case"
    if response_text:
        return f"{case_id}:{decision}:{response_text}"
    return f"{case_id}:{decision}"


def _build_regression_summary_markdown(
    *,
    selected_user_id: str | None,
    selected_chat_id: str | None,
    case_payloads: list[dict[str, Any]],
    mismatches: list[dict[str, Any]],
    inspection_payload: dict[str, Any] | None,
    architecture_benchmark_payload: dict[str, Any] | None,
    architecture_live_comparison_payload: dict[str, Any] | None,
) -> str:
    category_counts = _build_category_counts(str(case.get("category") or "unknown") for case in case_payloads)
    bridge_mode_counts = _build_category_counts(str(case.get("bridge_mode") or "missing") for case in case_payloads)
    routing_decision_counts = _build_category_counts(
        str(case.get("routing_decision") or "missing") for case in case_payloads
    )
    quality_lanes = _build_quality_lanes(category_counts)
    inspection_records = _inspection_records(inspection_payload)
    live_architecture_summary = (
        architecture_live_comparison_payload.get("summary")
        if isinstance(architecture_live_comparison_payload, dict)
        and isinstance(architecture_live_comparison_payload.get("summary"), dict)
        else {}
    )
    benchmark_summary = (
        architecture_benchmark_payload.get("summary")
        if isinstance(architecture_benchmark_payload, dict)
        and isinstance(architecture_benchmark_payload.get("summary"), dict)
        else {}
    )

    lines = [
        "# Telegram Memory Regression Summary",
        "",
        f"- Selected user id: `{selected_user_id or 'unknown'}`",
        f"- Selected chat id: `{selected_chat_id or 'unknown'}`",
        f"- Total cases: `{len(case_payloads)}`",
        f"- Matched cases: `{len(case_payloads) - len(mismatches)}`",
        f"- Mismatched cases: `{len(mismatches)}`",
        "",
        "## Live Architecture Comparison",
        "",
        f"- ProductMemory contenders: `{', '.join(benchmark_summary.get('baseline_names') or []) or 'none'}`",
        f"- Live Telegram contenders: `{', '.join(live_architecture_summary.get('baseline_names') or []) or 'none'}`",
        f"- Compared cases: `{live_architecture_summary.get('case_count', 0)}`",
        f"- Leaders: `{', '.join(live_architecture_summary.get('leader_names') or []) or 'unknown'}`",
        f"- Recommended runtime architecture: `{live_architecture_summary.get('recommended_runtime_architecture') or 'undecided'}`",
        f"- Current runtime architecture: `{live_architecture_summary.get('current_runtime_memory_architecture') or 'unknown'}`",
        f"- Runtime matches live leader: `{'yes' if live_architecture_summary.get('runtime_matches_live_leader') else 'no'}`",
        "",
        "## Category Coverage",
        "",
    ]
    for category in sorted(category_counts):
        lines.append(f"- `{category}`: `{category_counts[category]}`")
    lines.extend(
        [
            "",
            "## Route Coverage",
            "",
            "### Bridge Modes",
            "",
        ]
    )
    for bridge_mode in sorted(bridge_mode_counts):
        lines.append(f"- `{bridge_mode}`: `{bridge_mode_counts[bridge_mode]}`")
    lines.extend(
        [
            "",
            "### Routing Decisions",
            "",
        ]
    )
    for routing_decision in sorted(routing_decision_counts):
        lines.append(f"- `{routing_decision}`: `{routing_decision_counts[routing_decision]}`")
    lines.extend(
        [
            "",
            "## Quality Lanes",
            "",
            f"- `staleness`: `{'yes' if quality_lanes['staleness'] else 'no'}`",
            f"- `overwrite`: `{'yes' if quality_lanes['overwrite'] else 'no'}`",
            f"- `abstention`: `{'yes' if quality_lanes['abstention'] else 'no'}`",
            "",
            "## Current Memory Snapshot",
            "",
        ]
    )
    if inspection_records:
        for record in inspection_records[:12]:
            predicate = str(record.get("predicate") or "unknown")
            value = str(record.get("normalized_value") or record.get("value") or "").strip() or "missing"
            lines.append(f"- `{predicate}`: `{value}`")
    else:
        lines.append("- No current-state records were available from the inspection step.")
    lines.extend(
        [
            "",
            "## Recommended Next Actions",
            "",
        ]
    )
    if mismatches:
        lines.append("- Fix the mismatched cases before promoting wider runtime memory behavior.")
    else:
        lines.append("- Keep this regression bundle as a green baseline and add the next benchmark-style lane.")
    lines.append("- Only promote a memory change after it stays green on both ProductMemory scorecards and live Telegram regression packs.")
    if live_architecture_summary.get("recommended_runtime_architecture") and not live_architecture_summary.get(
        "runtime_matches_live_leader"
    ):
        lines.append(
            f"- Promote `{live_architecture_summary.get('recommended_runtime_architecture')}` into the Builder runtime selector and rerun this bundle."
        )
        lines.append(
            f"- Treat `{ARCHITECTURE_PROMOTION_GAP_LABEL}` as unresolved until the declared runtime matches the live leader."
        )
    elif live_architecture_summary.get("recommended_runtime_architecture"):
        lines.append(
            f"- Keep `{live_architecture_summary.get('recommended_runtime_architecture')}` pinned while expanding benchmark coverage."
        )
    elif live_architecture_summary.get("leader_names"):
        lines.append("- Break the live architecture tie with more cases before pinning the Builder runtime selector.")
    if not quality_lanes["abstention"]:
        lines.append("- Add abstention cases before widening memory promotion beyond the current lane.")
    if not quality_lanes["overwrite"]:
        lines.append("- Add overwrite cases so newer facts keep winning without regressions.")
    if not quality_lanes["staleness"]:
        lines.append("- Add staleness cases to confirm explanation and query routing stay stable over time.")
    lines.extend(
        [
            "",
            "## Cases",
            "",
        ]
    )
    for case in case_payloads:
        case_id = str(case.get("case_id") or "unknown")
        category = str(case.get("category") or "unknown")
        decision = str(case.get("decision") or "unknown")
        matched = "yes" if case.get("matched_expectations", False) else "no"
        response_text = " ".join(str(case.get("response_text") or "").split())
        lines.append(f"### {case_id}")
        lines.append(f"- Category: `{category}`")
        lines.append(f"- Decision: `{decision}`")
        lines.append(f"- Matched: `{matched}`")
        if response_text:
            lines.append(f"- Response: {response_text}")
        mismatch_items = case.get("mismatches")
        if isinstance(mismatch_items, list) and mismatch_items:
            rendered = ", ".join(str(item) for item in mismatch_items)
            lines.append(f"- Mismatches: `{rendered}`")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _build_regression_issue_labels(
    *,
    kb_payload: dict[str, Any] | None,
    architecture_live_comparison_payload: dict[str, Any] | None,
) -> list[str]:
    labels: list[str] = []
    kb_issue_labels = _nested_get(kb_payload, "failure_taxonomy", "summary", "issue_labels", default=[])
    if isinstance(kb_issue_labels, list):
        for label in kb_issue_labels:
            normalized = str(label or "").strip()
            if normalized and normalized not in labels:
                labels.append(normalized)
    recommended_runtime = _nested_get(
        architecture_live_comparison_payload,
        "summary",
        "recommended_runtime_architecture",
        default=None,
    )
    runtime_matches_live_leader = bool(
        _nested_get(
            architecture_live_comparison_payload,
            "summary",
            "runtime_matches_live_leader",
            default=False,
        )
    )
    if recommended_runtime and not runtime_matches_live_leader and ARCHITECTURE_PROMOTION_GAP_LABEL not in labels:
        labels.append(ARCHITECTURE_PROMOTION_GAP_LABEL)
    return labels


def _build_category_counts(categories: Any) -> dict[str, int]:
    category_counts: dict[str, int] = {}
    for category in categories:
        rendered = str(category or "unknown")
        category_counts[rendered] = category_counts.get(rendered, 0) + 1
    return category_counts


def _build_quality_lanes(category_counts: dict[str, int]) -> dict[str, bool]:
    return {lane: bool(category_counts.get(lane)) for lane in QUALITY_LANE_KEYS}


def _inspection_records(inspection_payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(inspection_payload, dict):
        return []
    read_result = inspection_payload.get("read_result")
    if not isinstance(read_result, dict):
        return []
    records = read_result.get("records")
    if not isinstance(records, list):
        return []
    return [record for record in records if isinstance(record, dict)]
