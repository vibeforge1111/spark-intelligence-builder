"""Personality integration for Spark Intelligence Builder.

Loads personality chip profiles, manages per-user trait preferences
via runtime_state, provides personality context for the advisory pipeline,
and self-evolves based on observed user interactions.
"""

from spark_intelligence.personality.loader import (
    PersonalityQueryResult,
    build_personality_context,
    build_preference_acknowledgment,
    detect_and_persist_nl_preferences,
    detect_personality_query,
    load_personality_profile,
    maybe_evolve_traits,
    record_observation,
)

__all__ = [
    "PersonalityQueryResult",
    "build_personality_context",
    "build_preference_acknowledgment",
    "detect_and_persist_nl_preferences",
    "detect_personality_query",
    "load_personality_profile",
    "maybe_evolve_traits",
    "record_observation",
]
