"""Personality profile loading and per-user preference management.

Reads personality chip state from the filesystem (written by spark-personality-chip-labs
hooks) and manages per-user trait deltas via the runtime_state table.

Trait names follow PersonalityEvolver's 5-trait system:
  warmth, directness, playfulness, pacing, assertiveness

Each trait is a float in [0.0, 1.0]. Deltas shift baseline traits and are
persisted per human_id in runtime_state with key pattern:
  personality:{human_id}:trait_deltas
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.state.db import StateDB


# ── Filesystem paths ──

_PERSONALITY_EVOLUTION_FILE = Path.home() / ".spark" / "personality_evolution_v1.json"
_BRIDGE_FILE = Path.home() / ".spark" / "bridges" / "consciousness" / "emotional_context.v1.json"

# ── Trait defaults ──

_DEFAULT_TRAITS = {
    "warmth": 0.50,
    "directness": 0.50,
    "playfulness": 0.50,
    "pacing": 0.50,
    "assertiveness": 0.50,
}

_TRAIT_LABELS = {
    "warmth": {(0.0, 0.3): "reserved", (0.3, 0.55): "balanced warmth", (0.55, 0.75): "warm", (0.75, 1.01): "very warm"},
    "directness": {(0.0, 0.3): "gentle", (0.3, 0.55): "balanced directness", (0.55, 0.75): "direct", (0.75, 1.01): "very direct"},
    "playfulness": {(0.0, 0.3): "serious", (0.3, 0.55): "balanced playfulness", (0.55, 0.75): "playful", (0.75, 1.01): "very playful"},
    "pacing": {(0.0, 0.3): "deliberate", (0.3, 0.55): "balanced pacing", (0.55, 0.75): "brisk", (0.75, 1.01): "rapid"},
    "assertiveness": {(0.0, 0.3): "gentle", (0.3, 0.55): "balanced assertiveness", (0.55, 0.75): "assertive", (0.75, 1.01): "very assertive"},
}

# ── NL preference patterns (inline fallback) ──
# These are a minimal subset. The full set lives in personality_engine.nl_traits.

_NL_TRAIT_PATTERNS: list[tuple[re.Pattern[str], dict[str, float]]] = [
    (re.compile(r"\b(?:be\s+|more\s+|too\s+)direct\b", re.I), {"directness": 0.4}),
    (re.compile(r"\b(?:be\s+|more\s+|too\s+)concise\b", re.I), {"directness": 0.3, "pacing": 0.2}),
    (re.compile(r"\bskip\b.*(?:explain|preamble|intro)", re.I), {"directness": 0.4, "pacing": 0.3}),
    (re.compile(r"\bget\s+to\s+the\s+point\b", re.I), {"directness": 0.4, "pacing": 0.3}),
    (re.compile(r"\bless\s+verbose\b", re.I), {"directness": 0.3, "pacing": 0.2}),
    (re.compile(r"\bmore\s+(?:detail|thorough|verbose)\b", re.I), {"directness": -0.3, "pacing": -0.3}),
    (re.compile(r"\b(?:be\s+|more\s+|too\s+)warm(?:er)?\b", re.I), {"warmth": 0.4}),
    (re.compile(r"\b(?:too\s+|less\s+)formal\b", re.I), {"warmth": 0.3, "playfulness": 0.2}),
    (re.compile(r"\bloosen\s+up\b", re.I), {"warmth": 0.2, "playfulness": 0.3}),
    (re.compile(r"\b(?:be\s+|more\s+)friendly\b", re.I), {"warmth": 0.3}),
    (re.compile(r"\b(?:more\s+|too\s+)professional\b", re.I), {"warmth": -0.2, "playfulness": -0.2}),
    (re.compile(r"\b(?:be\s+|more\s+|too\s+)playful\b", re.I), {"playfulness": 0.4}),
    (re.compile(r"\bmore\s+energy\b|more\s+enthusias", re.I), {"playfulness": 0.3, "assertiveness": 0.2}),
    (re.compile(r"\btone\s+it\s+down\b", re.I), {"assertiveness": -0.3, "playfulness": -0.2}),
    (re.compile(r"\bdial\s*.*(?:back|down)\b", re.I), {"playfulness": -0.2, "assertiveness": -0.2}),
    (re.compile(r"\b(?:be\s+|more\s+)serious\b", re.I), {"playfulness": -0.3}),
    (re.compile(r"\bslow(?:er)?\s+down\b", re.I), {"pacing": -0.4, "directness": -0.2}),
    (re.compile(r"\bexplain\s+more\b", re.I), {"pacing": -0.3, "directness": -0.2}),
    (re.compile(r"\bspeed\s+(?:it\s+)?up\b", re.I), {"pacing": 0.3}),
    (re.compile(r"\btake\s+(?:your\s+)?time\b", re.I), {"pacing": -0.3}),
    (re.compile(r"\bstop\s+hedg", re.I), {"assertiveness": 0.4, "directness": 0.3}),
    (re.compile(r"\bmore\s+assertive\b", re.I), {"assertiveness": 0.4}),
    (re.compile(r"\b(?:less\s+assertive|more\s+gentle)\b", re.I), {"assertiveness": -0.3, "warmth": 0.2}),
    (re.compile(r"\b(?:be\s+|more\s+)confident\b", re.I), {"assertiveness": 0.3}),
    (re.compile(r"\bstop\s+(?:apologiz|saying\s+sorry)", re.I), {"assertiveness": 0.3, "directness": 0.2}),
    (re.compile(r"\bjust\s+(?:tell|give)\s+me\b", re.I), {"directness": 0.4, "assertiveness": 0.2}),
    (re.compile(r"\bcalm(?:er)?\b|relax\b", re.I), {"assertiveness": -0.2, "warmth": 0.1}),
]


def _extract_trait_deltas(text: str) -> dict[str, float]:
    """Extract personality trait deltas from natural language text.

    Tries the full personality_engine.nl_traits module first, falls
    back to inline patterns if the personality engine is not installed.
    """
    if not text or not text.strip():
        return {}

    # Try the full NL traits module from personality engine
    try:
        from personality_engine.nl_traits import extract_trait_deltas  # type: ignore[import-untyped]
        return extract_trait_deltas(text)
    except ImportError:
        pass

    # Inline fallback
    combined: dict[str, float] = {}
    for pattern, deltas in _NL_TRAIT_PATTERNS:
        if pattern.search(text):
            for trait, delta in deltas.items():
                combined[trait] = combined.get(trait, 0.0) + delta
    for trait in combined:
        combined[trait] = max(-1.0, min(1.0, combined[trait]))
    return combined


def _has_personality_signal(text: str) -> bool:
    """Quick check: does text contain any personality preference signal?"""
    if not text or not text.strip():
        return False
    try:
        from personality_engine.nl_traits import has_personality_preference  # type: ignore[import-untyped]
        return has_personality_preference(text)
    except ImportError:
        pass
    for pattern, _ in _NL_TRAIT_PATTERNS:
        if pattern.search(text):
            return True
    return False


# ── Profile loading ──


def load_personality_profile(
    *,
    human_id: str,
    state_db: StateDB,
    config_manager: ConfigManager,
) -> dict[str, Any] | None:
    """Load the active personality profile with per-user trait overrides.

    Returns a dict with:
      - traits: {warmth, directness, playfulness, pacing, assertiveness}
      - style_labels: human-readable labels for each trait
      - personality_id: id of the active personality chip (if any)
      - personality_name: name of the active personality chip (if any)
      - user_deltas_applied: bool, whether per-user NL deltas were merged
      - source: where the base traits came from

    Returns None if personality is disabled in config.
    """
    enabled = config_manager.get_path("spark.personality.enabled", default=True)
    if not enabled:
        return None

    # 1. Load base traits from personality_evolution_v1.json (written by personality hooks)
    base_traits = dict(_DEFAULT_TRAITS)
    personality_id = None
    personality_name = None
    source = "defaults"

    evolver_path = Path(
        config_manager.get_path(
            "spark.personality.evolver_state_path",
            default=str(_PERSONALITY_EVOLUTION_FILE),
        )
    )

    if evolver_path.exists():
        try:
            data = json.loads(evolver_path.read_text(encoding="utf-8"))
            if isinstance(data.get("traits"), dict):
                for trait in _DEFAULT_TRAITS:
                    if trait in data["traits"]:
                        base_traits[trait] = float(data["traits"][trait])
                source = "personality_chip"
            signals = data.get("last_signals") or {}
            personality_id = signals.get("personality_id")
            personality_name = signals.get("personality_name")
        except Exception:
            pass

    # 2. Load per-user trait deltas from runtime_state
    user_deltas = _load_user_trait_deltas(human_id=human_id, state_db=state_db)
    user_deltas_applied = bool(user_deltas)

    # 3. Merge: base + user deltas, clamp to [0.0, 1.0]
    final_traits = {}
    for trait, base_val in base_traits.items():
        merged = base_val + user_deltas.get(trait, 0.0)
        final_traits[trait] = max(0.0, min(1.0, merged))

    # 4. Generate style labels
    style_labels = {trait: _label_for_trait(trait, val) for trait, val in final_traits.items()}

    return {
        "traits": final_traits,
        "style_labels": style_labels,
        "personality_id": personality_id,
        "personality_name": personality_name,
        "user_deltas_applied": user_deltas_applied,
        "source": source,
    }


def build_personality_context(profile: dict[str, Any]) -> str:
    """Build a personality context string for injection into LLM prompts.

    Returns a compact section suitable for system_prompt or contextual_task injection.
    """
    if not profile:
        return ""

    traits = profile.get("traits") or {}
    labels = profile.get("style_labels") or {}
    name = profile.get("personality_name")

    lines = ["[Personality context]"]
    if name:
        lines.append(f"active_personality={name}")

    style_parts = []
    for trait in ("warmth", "directness", "playfulness", "pacing", "assertiveness"):
        label = labels.get(trait, "balanced")
        style_parts.append(label)
    lines.append(f"style={', '.join(style_parts)}")

    # Compact trait values for transparency
    trait_vals = " ".join(f"{t}={v:.2f}" for t, v in sorted(traits.items()))
    lines.append(f"trait_values={trait_vals}")

    if profile.get("user_deltas_applied"):
        lines.append("note=style adjusted by user preference")

    return "\n".join(lines)


def build_personality_system_directive(profile: dict[str, Any]) -> str:
    """Build a personality directive for the system prompt.

    Returns natural-language instructions that shape the LLM's tone and style.
    """
    if not profile:
        return ""

    traits = profile.get("traits") or {}
    labels = profile.get("style_labels") or {}
    name = profile.get("personality_name")

    parts = []
    if name:
        parts.append(f"Your active personality profile is '{name}'.")

    # Map traits to behavioral instructions
    directives = []

    warmth = traits.get("warmth", 0.5)
    if warmth >= 0.7:
        directives.append("Be warm and approachable in tone.")
    elif warmth <= 0.3:
        directives.append("Keep a professional, matter-of-fact tone.")

    directness = traits.get("directness", 0.5)
    if directness >= 0.7:
        directives.append("Be direct and concise. Skip preamble.")
    elif directness <= 0.3:
        directives.append("Explain thoroughly with context and reasoning.")

    playfulness = traits.get("playfulness", 0.5)
    if playfulness >= 0.7:
        directives.append("Feel free to be playful and inject personality.")
    elif playfulness <= 0.3:
        directives.append("Stay focused and serious. Minimal humor.")

    pacing = traits.get("pacing", 0.5)
    if pacing >= 0.7:
        directives.append("Move quickly. Keep responses tight.")
    elif pacing <= 0.3:
        directives.append("Take your time. Walk through things step by step.")

    assertiveness = traits.get("assertiveness", 0.5)
    if assertiveness >= 0.7:
        directives.append("State conclusions confidently. Don't hedge unnecessarily.")
    elif assertiveness <= 0.3:
        directives.append("Present options gently. Use qualifiers when uncertain.")

    if directives:
        parts.append(" ".join(directives))

    if profile.get("user_deltas_applied"):
        parts.append("These style preferences were set by the user through conversation.")

    return " ".join(parts)


# ── NL preference detection and persistence ──


def detect_and_persist_nl_preferences(
    *,
    human_id: str,
    user_message: str,
    state_db: StateDB,
) -> dict[str, float] | None:
    """Detect NL personality preferences in a user message and persist them.

    Returns the extracted trait deltas if any were found, None otherwise.
    """
    if not _has_personality_signal(user_message):
        return None

    deltas = _extract_trait_deltas(user_message)
    if not deltas:
        return None

    # Load existing user deltas
    existing = _load_user_trait_deltas(human_id=human_id, state_db=state_db)

    # Merge: additive with clamping
    merged = dict(existing)
    for trait, delta in deltas.items():
        merged[trait] = max(-0.5, min(0.5, merged.get(trait, 0.0) + delta))

    # Persist
    _save_user_trait_deltas(human_id=human_id, deltas=merged, state_db=state_db)

    return deltas


# ── Per-user delta persistence via runtime_state ──


def _state_key(human_id: str) -> str:
    return f"personality:{human_id}:trait_deltas"


def _load_user_trait_deltas(*, human_id: str, state_db: StateDB) -> dict[str, float]:
    """Load persisted per-user trait deltas from runtime_state."""
    try:
        with state_db.connect() as conn:
            row = conn.execute(
                "SELECT value FROM runtime_state WHERE state_key = ? LIMIT 1",
                (_state_key(human_id),),
            ).fetchone()
        if not row or not row["value"]:
            return {}
        data = json.loads(row["value"])
        return {k: float(v) for k, v in data.items() if k in _DEFAULT_TRAITS}
    except Exception:
        return {}


def _save_user_trait_deltas(
    *,
    human_id: str,
    deltas: dict[str, float],
    state_db: StateDB,
) -> None:
    """Persist per-user trait deltas to runtime_state."""
    payload = json.dumps(
        {
            "deltas": {k: round(v, 3) for k, v in deltas.items()},
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        },
        sort_keys=True,
    )
    with state_db.connect() as conn:
        conn.execute(
            """
            INSERT INTO runtime_state(state_key, value)
            VALUES (?, ?)
            ON CONFLICT(state_key) DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP
            """,
            (_state_key(human_id), payload),
        )
        conn.commit()


def _label_for_trait(trait: str, value: float) -> str:
    """Convert a trait value to a human-readable label."""
    ranges = _TRAIT_LABELS.get(trait, {})
    for (low, high), label in ranges.items():
        if low <= value < high:
            return label
    return "balanced"
