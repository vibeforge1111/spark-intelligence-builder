"""Personality integration for Spark Intelligence Builder.

Loads personality chip profiles, manages per-user trait preferences
via runtime_state, and provides personality context for the advisory pipeline.
"""

from spark_intelligence.personality.loader import (
    build_personality_context,
    detect_and_persist_nl_preferences,
    load_personality_profile,
)

__all__ = [
    "build_personality_context",
    "detect_and_persist_nl_preferences",
    "load_personality_profile",
]
