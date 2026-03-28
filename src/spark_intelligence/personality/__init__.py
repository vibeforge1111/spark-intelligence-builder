"""Personality integration for Spark Intelligence Builder.

Loads personality chip profiles, manages per-user trait preferences
via runtime_state, provides personality context for the advisory pipeline,
and self-evolves based on observed user interactions.
"""

from spark_intelligence.personality.loader import (
    AgentPersonaMutationResult,
    LegacyPersonalityMigrationResult,
    PersonalityQueryResult,
    build_personality_context,
    build_preference_acknowledgment,
    detect_and_persist_agent_persona_preferences,
    detect_and_persist_nl_preferences,
    detect_personality_query,
    load_agent_persona_profile,
    load_personality_profile,
    migrate_legacy_human_personality_to_agent_persona,
    maybe_evolve_traits,
    record_observation,
    save_agent_persona_profile,
)

__all__ = [
    "AgentPersonaMutationResult",
    "LegacyPersonalityMigrationResult",
    "PersonalityQueryResult",
    "build_personality_context",
    "build_preference_acknowledgment",
    "detect_and_persist_agent_persona_preferences",
    "detect_and_persist_nl_preferences",
    "detect_personality_query",
    "load_agent_persona_profile",
    "load_personality_profile",
    "migrate_legacy_human_personality_to_agent_persona",
    "maybe_evolve_traits",
    "record_observation",
    "save_agent_persona_profile",
]
