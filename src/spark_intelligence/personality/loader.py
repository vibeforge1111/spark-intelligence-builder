"""Personality profile loading, agent-base authoring, and per-user preference management.

Reads personality chip state from the filesystem (written by spark-personality-chip-labs
hooks) and manages per-user trait deltas via typed state tables with runtime_state
mirrors kept only for compatibility.

Trait names follow PersonalityEvolver's 5-trait system:
  warmth, directness, playfulness, pacing, assertiveness

Each trait is a float in [0.0, 1.0]. Agent-base traits are persisted per agent_id
in agent_persona_profiles. Human-specific deltas shift the active baseline traits
and are persisted per human_id in personality_trait_profiles with a compatibility mirror
in runtime_state using the key pattern:
  personality:{human_id}:trait_deltas

Self-evolution: After each interaction, the system records what traits were
active and infers the user's emotional state from their message. Periodically,
accumulated observations are analyzed and small trait adjustments are applied
automatically (bounded to +-0.05 per evolution cycle).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.identity.service import read_canonical_agent_state, rename_agent_identity
from spark_intelligence.memory.orchestrator import (
    delete_personality_preferences_from_memory,
    read_personality_preferences_from_memory,
    write_personality_preferences_to_memory,
)
from spark_intelligence.observability.store import record_event
from spark_intelligence.state.db import StateDB
from spark_intelligence.state.hygiene import (
    clear_registered_state_keys,
    clear_reset_sensitive_scope,
    upsert_runtime_state,
)


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

_AGENT_NAME_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\byour name is (?P<name>[A-Za-z0-9][A-Za-z0-9 _-]{1,40})\.?$", re.I),
    re.compile(r"\bi(?: want to)? call you (?P<name>[A-Za-z0-9][A-Za-z0-9 _-]{1,40})\.?$", re.I),
    re.compile(r"\brename yourself to (?P<name>[A-Za-z0-9][A-Za-z0-9 _-]{1,40})\.?$", re.I),
]

_AGENT_PERSONA_MARKERS = (
    "your personality",
    "your style",
    "as my agent",
    "as an agent",
    "agent persona",
    "let's define your personality",
    "lets define your personality",
)


@dataclass
class AgentPersonaMutationResult:
    agent_id: str
    agent_name: str | None
    trait_deltas: dict[str, float]
    persona_profile: dict[str, Any]
    context_injection: str


@dataclass
class LegacyPersonalityMigrationResult:
    human_id: str
    agent_id: str
    status: str
    migrated_traits: dict[str, float]
    cleared_overlay: bool
    persona_profile: dict[str, Any]


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


def _extract_agent_name(text: str) -> str | None:
    for pattern in _AGENT_NAME_PATTERNS:
        match = pattern.search(text.strip())
        if not match:
            continue
        candidate = str(match.group("name") or "").strip().strip(".")
        if candidate:
            return candidate
    return None


def _is_agent_persona_authoring_message(text: str) -> bool:
    lowered = text.strip().lower()
    if not lowered:
        return False
    if _extract_agent_name(text):
        return True
    if any(marker in lowered for marker in _AGENT_PERSONA_MARKERS) and _has_personality_signal(text):
        return True
    if lowered.startswith("/agent persona "):
        return True
    return False


# ── Profile loading ──


def load_personality_profile(
    *,
    human_id: str,
    agent_id: str | None = None,
    state_db: StateDB | None = None,
    config_manager: ConfigManager | None = None,
) -> dict[str, Any] | None:
    """Load the active personality profile with per-user trait overrides.

    Returns a dict with:
      - traits: {warmth, directness, playfulness, pacing, assertiveness}
      - style_labels: human-readable labels for each trait
      - personality_id: id of the active personality chip (if any)
      - personality_name: name of the active personality chip (if any)
      - agent_persona_applied: bool, whether agent-base persona traits were merged
      - user_deltas_applied: bool, whether per-user NL deltas were merged
      - source: where the base traits came from

    Returns None if personality is disabled in config.
    """
    if config_manager is not None:
        enabled = config_manager.get_path("spark.personality.enabled", default=True)
        if not enabled:
            return None

    # 1. Load base traits from personality_evolution_v1.json (written by personality hooks)
    base_traits = dict(_DEFAULT_TRAITS)
    personality_id = None
    personality_name = None
    source = "defaults"

    configured_path = (
        config_manager.get_path(
            "spark.personality.evolver_state_path",
            default=None,
        )
        if config_manager is not None
        else None
    )
    evolver_path = Path(configured_path) if configured_path else _PERSONALITY_EVOLUTION_FILE

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

    # 2. Merge optional agent-base persona traits
    agent_persona = load_agent_persona_profile(agent_id=agent_id, state_db=state_db) if agent_id else {}
    agent_base_traits = agent_persona.get("base_traits") or {}
    agent_persona_applied = bool(agent_base_traits)
    merged_base_traits = dict(base_traits)
    for trait in _DEFAULT_TRAITS:
        if trait in agent_base_traits:
            merged_base_traits[trait] = max(0.0, min(1.0, float(agent_base_traits[trait])))

    # 3. Load per-user trait deltas from runtime_state
    user_deltas = _load_user_trait_deltas(human_id=human_id, state_db=state_db)
    user_deltas_applied = bool(user_deltas)

    # 4. Merge: base + user deltas, clamp to [0.0, 1.0]
    final_traits = {}
    for trait, base_val in merged_base_traits.items():
        merged = base_val + user_deltas.get(trait, 0.0)
        final_traits[trait] = max(0.0, min(1.0, merged))

    # 5. Generate style labels
    style_labels = {trait: _label_for_trait(trait, val) for trait, val in final_traits.items()}

    return {
        "traits": final_traits,
        "style_labels": style_labels,
        "personality_id": personality_id,
        "personality_name": personality_name,
        "agent_id": agent_id,
        "agent_persona_name": agent_persona.get("persona_name"),
        "agent_persona_summary": agent_persona.get("persona_summary"),
        "agent_persona_applied": agent_persona_applied,
        "agent_base_traits": agent_base_traits,
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
    if profile.get("agent_persona_name"):
        lines.append(f"agent_persona={profile['agent_persona_name']}")

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
    elif profile.get("agent_persona_applied"):
        lines.append("note=style adjusted by agent persona")

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
    if profile.get("agent_persona_name"):
        parts.append(f"Your agent persona is '{profile['agent_persona_name']}'.")

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
    elif profile.get("agent_persona_applied"):
        parts.append("These style settings reflect the saved agent persona.")

    return " ".join(parts)


def load_agent_persona_profile(*, agent_id: str | None, state_db: StateDB | None) -> dict[str, Any]:
    if not agent_id or state_db is None:
        return {}
    try:
        with state_db.connect() as conn:
            row = conn.execute(
                """
                SELECT persona_name, persona_summary, base_traits_json, behavioral_rules_json, provenance_json, updated_at
                FROM agent_persona_profiles
                WHERE agent_id = ?
                LIMIT 1
                """,
                (agent_id,),
            ).fetchone()
        if not row:
            return {}
        base_traits = json.loads(row["base_traits_json"] or "{}")
        behavioral_rules = json.loads(row["behavioral_rules_json"] or "[]") if row["behavioral_rules_json"] else []
        provenance = json.loads(row["provenance_json"] or "{}") if row["provenance_json"] else {}
        return {
            "agent_id": agent_id,
            "persona_name": _read_optional_text(row["persona_name"]),
            "persona_summary": _read_optional_text(row["persona_summary"]),
            "base_traits": {k: float(v) for k, v in base_traits.items() if k in _DEFAULT_TRAITS},
            "behavioral_rules": behavioral_rules if isinstance(behavioral_rules, list) else [],
            "provenance": provenance if isinstance(provenance, dict) else {},
            "updated_at": row["updated_at"],
        }
    except Exception:
        return {}


def save_agent_persona_profile(
    *,
    agent_id: str,
    human_id: str,
    state_db: StateDB,
    base_traits: dict[str, float],
    persona_name: str | None = None,
    persona_summary: str | None = None,
    behavioral_rules: list[str] | None = None,
    provenance: dict[str, Any] | None = None,
    mutation_kind: str = "authoring",
    source_surface: str = "builder",
    source_ref: str | None = None,
) -> dict[str, Any]:
    updated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    normalized_traits = {
        key: round(max(0.0, min(1.0, float(value))), 3)
        for key, value in base_traits.items()
        if key in _DEFAULT_TRAITS
    }
    mutation_id = f"agent-persona-{uuid4().hex[:12]}"
    with state_db.connect() as conn:
        conn.execute(
            """
            INSERT INTO agent_persona_profiles(
                agent_id, persona_name, persona_summary, base_traits_json, behavioral_rules_json, provenance_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(agent_id) DO UPDATE SET
                persona_name=COALESCE(excluded.persona_name, agent_persona_profiles.persona_name),
                persona_summary=COALESCE(excluded.persona_summary, agent_persona_profiles.persona_summary),
                base_traits_json=excluded.base_traits_json,
                behavioral_rules_json=COALESCE(excluded.behavioral_rules_json, agent_persona_profiles.behavioral_rules_json),
                provenance_json=COALESCE(excluded.provenance_json, agent_persona_profiles.provenance_json),
                updated_at=excluded.updated_at
            """,
            (
                agent_id,
                persona_name,
                persona_summary,
                json.dumps(normalized_traits, sort_keys=True),
                json.dumps(behavioral_rules or [], sort_keys=True),
                json.dumps(provenance or {}, sort_keys=True),
                updated_at,
            ),
        )
        conn.execute(
            """
            INSERT INTO agent_persona_mutations(
                mutation_id, agent_id, human_id, mutation_kind, delta_traits_json, persona_name, persona_summary, source_surface, source_ref, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                mutation_id,
                agent_id,
                human_id,
                mutation_kind,
                json.dumps(normalized_traits, sort_keys=True),
                persona_name,
                persona_summary,
                source_surface,
                source_ref,
                updated_at,
            ),
        )
        conn.commit()
    return load_agent_persona_profile(agent_id=agent_id, state_db=state_db)


def migrate_legacy_human_personality_to_agent_persona(
    *,
    human_id: str,
    state_db: StateDB,
    agent_id: str | None = None,
    clear_overlay: bool = True,
    force: bool = False,
    source_surface: str = "agent_cli",
    source_ref: str | None = None,
) -> LegacyPersonalityMigrationResult:
    resolved_agent_id = agent_id or read_canonical_agent_state(state_db=state_db, human_id=human_id).agent_id
    existing_persona = load_agent_persona_profile(agent_id=resolved_agent_id, state_db=state_db)
    if existing_persona and not force:
        return LegacyPersonalityMigrationResult(
            human_id=human_id,
            agent_id=resolved_agent_id,
            status="existing_agent_persona",
            migrated_traits={},
            cleared_overlay=False,
            persona_profile=existing_persona,
        )

    user_deltas = _load_user_trait_deltas(human_id=human_id, state_db=state_db)
    if not user_deltas:
        return LegacyPersonalityMigrationResult(
            human_id=human_id,
            agent_id=resolved_agent_id,
            status="no_legacy_deltas",
            migrated_traits={},
            cleared_overlay=False,
            persona_profile=existing_persona,
        )

    effective_profile = load_personality_profile(
        human_id=human_id,
        agent_id=resolved_agent_id,
        state_db=state_db,
        config_manager=None,
    )
    effective_traits = (effective_profile or {}).get("traits") or {}
    migrated_traits = {
        trait: round(
            max(
                0.0,
                min(
                    1.0,
                    float(
                        effective_traits.get(
                            trait,
                            _DEFAULT_TRAITS[trait] + float(user_deltas.get(trait, 0.0)),
                        )
                    ),
                ),
            ),
            3,
        )
        for trait in _DEFAULT_TRAITS
    }
    persona_profile = save_agent_persona_profile(
        agent_id=resolved_agent_id,
        human_id=human_id,
        state_db=state_db,
        base_traits=migrated_traits,
        persona_name=(existing_persona.get("persona_name") if existing_persona else None),
        persona_summary=(existing_persona.get("persona_summary") if existing_persona else _compact_persona_summary(migrated_traits)),
        provenance={
            "source_surface": source_surface,
            "source_ref": source_ref,
            "migration": "legacy_human_trait_deltas",
            "legacy_deltas": user_deltas,
        },
        mutation_kind="legacy_human_migration",
        source_surface=source_surface,
        source_ref=source_ref,
    )
    cleared_overlay = False
    if clear_overlay:
        _clear_legacy_user_trait_overlay(human_id=human_id, state_db=state_db)
        cleared_overlay = True
    return LegacyPersonalityMigrationResult(
        human_id=human_id,
        agent_id=resolved_agent_id,
        status="migrated",
        migrated_traits=migrated_traits,
        cleared_overlay=cleared_overlay,
        persona_profile=persona_profile,
    )


# ── NL preference detection and persistence ──


def detect_and_persist_nl_preferences(
    *,
    human_id: str,
    user_message: str,
    state_db: StateDB,
    config_manager: ConfigManager | None = None,
    session_id: str | None = None,
    turn_id: str | None = None,
    channel_kind: str | None = None,
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
    if config_manager is not None:
        try:
            write_personality_preferences_to_memory(
                config_manager=config_manager,
                state_db=state_db,
                human_id=human_id,
                detected_deltas=deltas,
                merged_deltas=merged,
                session_id=session_id,
                turn_id=turn_id,
                channel_kind=channel_kind,
            )
        except Exception:
            pass

    return deltas


def detect_and_persist_agent_persona_preferences(
    *,
    agent_id: str,
    human_id: str,
    user_message: str,
    state_db: StateDB,
    source_surface: str,
    source_ref: str | None = None,
) -> AgentPersonaMutationResult | None:
    if not _is_agent_persona_authoring_message(user_message):
        return None

    trait_deltas = _extract_trait_deltas(user_message)
    current_profile = load_agent_persona_profile(agent_id=agent_id, state_db=state_db)
    existing_traits = current_profile.get("base_traits") or dict(_DEFAULT_TRAITS)
    next_traits = dict(existing_traits)
    for trait, delta in trait_deltas.items():
        next_traits[trait] = max(0.0, min(1.0, float(next_traits.get(trait, _DEFAULT_TRAITS[trait])) + delta))

    new_name = _extract_agent_name(user_message)
    if new_name:
        rename_agent_identity(
            state_db=state_db,
            human_id=human_id,
            new_name=new_name,
            source_surface=source_surface,
            source_ref=source_ref,
        )

    persona_profile = current_profile
    if trait_deltas:
        persona_profile = save_agent_persona_profile(
            agent_id=agent_id,
            human_id=human_id,
            state_db=state_db,
            base_traits=next_traits,
            persona_name=current_profile.get("persona_name") or new_name,
            persona_summary=_compact_persona_summary(next_traits),
            provenance={
                "source_surface": source_surface,
                "source_ref": source_ref,
                "authoring_text": user_message,
            },
            mutation_kind="explicit_authoring",
            source_surface=source_surface,
            source_ref=source_ref,
        )

    if not new_name and not trait_deltas:
        return None

    parts: list[str] = ["[Personality action: AGENT_PERSONA_UPDATED]"]
    if new_name:
        parts.append(
            f"The agent's saved name is now '{new_name}'. Acknowledge the rename briefly and continue naturally."
        )
    if trait_deltas:
        changes = ", ".join(
            f"{'more' if delta > 0 else 'less'} {trait.replace('_', ' ')}"
            for trait, delta in sorted(trait_deltas.items())
        )
        parts.append(
            "The user updated the agent's base persona. "
            f"Acknowledge this briefly and adopt the saved agent persona going forward: {changes}."
        )
    return AgentPersonaMutationResult(
        agent_id=agent_id,
        agent_name=new_name,
        trait_deltas=trait_deltas,
        persona_profile=persona_profile if isinstance(persona_profile, dict) else {},
        context_injection="\n".join(parts),
    )


# ── Per-user delta persistence via runtime_state ──


def _state_key(human_id: str) -> str:
    return f"personality:{human_id}:trait_deltas"


def _load_user_trait_deltas(*, human_id: str, state_db: StateDB | None) -> dict[str, float]:
    """Load persisted per-user trait deltas from typed storage or compatibility state."""
    if state_db is None:
        return {}
    try:
        with state_db.connect() as conn:
            row = conn.execute(
                """
                SELECT deltas_json
                FROM personality_trait_profiles
                WHERE human_id = ?
                LIMIT 1
                """,
                (human_id,),
            ).fetchone()
        if row and row["deltas_json"]:
            data = json.loads(row["deltas_json"])
            return {k: float(v) for k, v in data.items() if k in _DEFAULT_TRAITS}
    except Exception:
        pass
    try:
        with state_db.connect() as conn:
            row = conn.execute(
                "SELECT value FROM runtime_state WHERE state_key = ? LIMIT 1",
                (_state_key(human_id),),
            ).fetchone()
        if not row or not row["value"]:
            return {}
        data = json.loads(row["value"])
        # Deltas are nested under "deltas" key
        deltas_dict = data.get("deltas", data) if isinstance(data, dict) else {}
        return {k: float(v) for k, v in deltas_dict.items() if k in _DEFAULT_TRAITS}
    except Exception:
        return {}


def _save_user_trait_deltas(
    *,
    human_id: str,
    deltas: dict[str, float],
    state_db: StateDB,
) -> None:
    """Persist per-user trait deltas to typed storage and a compatibility mirror."""
    updated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    normalized = {k: round(v, 3) for k, v in deltas.items() if k in _DEFAULT_TRAITS}
    payload = json.dumps(
        {
            "deltas": normalized,
            "updated_at": updated_at,
        },
        sort_keys=True,
    )
    with state_db.connect() as conn:
        conn.execute(
            """
            INSERT INTO personality_trait_profiles(human_id, deltas_json, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(human_id) DO UPDATE SET
                deltas_json=excluded.deltas_json,
                updated_at=excluded.updated_at
            """,
            (human_id, json.dumps(normalized, sort_keys=True), updated_at),
        )
        upsert_runtime_state(
            conn,
            state_key=_state_key(human_id),
            value=payload,
            component="personality_profile",
            reset_sensitive_scope=("human", human_id, "personality_preference_reset"),
        )
        conn.commit()


def _compact_persona_summary(traits: dict[str, float]) -> str:
    labels = [_label_for_trait(trait, float(traits.get(trait, _DEFAULT_TRAITS[trait]))) for trait in _DEFAULT_TRAITS]
    return ", ".join(labels)


def _read_optional_text(value: object) -> str | None:
    if value in {None, ""}:
        return None
    return str(value)


def _label_for_trait(trait: str, value: float) -> str:
    """Convert a trait value to a human-readable label."""
    ranges = _TRAIT_LABELS.get(trait, {})
    for (low, high), label in ranges.items():
        if low <= value < high:
            return label
    return "balanced"


# ── Personality queries (status, reset) ──

_QUERY_STATUS_PATTERNS = [
    re.compile(r"\b(?:what(?:'s| is) my (?:personality|style|config))", re.I),
    re.compile(r"\b(?:how am i|how are you) configured\b", re.I),
    re.compile(r"\bshow (?:my |your )?personality\b", re.I),
    re.compile(r"\bcurrent (?:personality|style)\b", re.I),
    re.compile(r"\bmy (?:style|personality) (?:settings|preferences)\b", re.I),
]

_QUERY_RESET_PATTERNS = [
    re.compile(r"\breset (?:my )?(?:personality|style|preferences)\b", re.I),
    re.compile(r"\bgo back to (?:default|normal|original)\b", re.I),
    re.compile(r"\bclear (?:my )?(?:personality|style) (?:settings|preferences|changes)\b", re.I),
    re.compile(r"\bundo (?:personality|style) changes\b", re.I),
    re.compile(r"\bremove (?:my )?(?:personality|style) (?:preferences|customization)\b", re.I),
]


@dataclass
class PersonalityQueryResult:
    """Result of a personality query detection."""
    kind: str  # "status", "reset", "preference_ack", or "none"
    context_injection: str  # text to inject into contextual task for the LLM
    deltas_applied: dict[str, float] | None = None


def detect_personality_query(
    *,
    user_message: str,
    human_id: str,
    agent_id: str | None = None,
    state_db: StateDB,
    profile: dict[str, Any] | None = None,
    config_manager: ConfigManager | None = None,
    session_id: str | None = None,
    turn_id: str | None = None,
) -> PersonalityQueryResult:
    """Detect if the user is asking about or managing their personality settings.

    Returns a PersonalityQueryResult with context to inject into the LLM prompt.
    """
    text = user_message.strip()
    if not text:
        return PersonalityQueryResult(kind="none", context_injection="")

    # Check for reset
    for pattern in _QUERY_RESET_PATTERNS:
        if pattern.search(text):
            existing_deltas = _load_user_trait_deltas(human_id=human_id, state_db=state_db)
            cleared_state_keys = _clear_user_trait_deltas(human_id=human_id, state_db=state_db)
            if config_manager is not None:
                try:
                    delete_personality_preferences_from_memory(
                        config_manager=config_manager,
                        state_db=state_db,
                        human_id=human_id,
                        existing_deltas=existing_deltas,
                        session_id=session_id,
                        turn_id=turn_id,
                        channel_kind=None,
                    )
                except Exception:
                    pass
            record_event(
                state_db,
                event_type="session_reset_performed",
                component="personality_profile",
                summary="Personality reset cleared reset-sensitive preference state.",
                request_id=turn_id,
                session_id=session_id,
                human_id=human_id,
                actor_id="personality_profile",
                reason_code="personality_reset",
                facts={
                    "scope_kind": "human",
                    "scope_ref": human_id,
                    "reset_reason": "personality_preference_reset",
                    "cleared_state_keys": cleared_state_keys,
                    "cleared_state_key_count": len(cleared_state_keys),
                },
                provenance={"source_kind": "personality_query"},
            )
            return PersonalityQueryResult(
                kind="reset",
                context_injection=(
                    "[Personality action: RESET]\n"
                    "The user has asked to reset their personality preferences. "
                    "All custom style adjustments have been cleared. "
                    "Confirm to the user that their personality preferences have been "
                    "reset to default and you'll respond with the base personality style going forward."
                ),
            )

    # Check for status query
    for pattern in _QUERY_STATUS_PATTERNS:
        if pattern.search(text):
            status_text = _format_profile_status(
                profile,
                human_id=human_id,
                agent_id=agent_id,
                state_db=state_db,
                config_manager=config_manager,
                session_id=session_id,
                turn_id=turn_id,
            )
            return PersonalityQueryResult(
                kind="status",
                context_injection=(
                    f"[Personality action: STATUS]\n"
                    f"The user wants to know their current personality/style settings. "
                    f"Share this information naturally:\n{status_text}\n"
                    f"Explain that they can adjust these by telling you things like "
                    f"'be more direct', 'slow down', 'stop hedging', etc. "
                    f"They can also say 'reset personality' to go back to defaults."
                ),
            )

    return PersonalityQueryResult(kind="none", context_injection="")


def build_preference_acknowledgment(deltas: dict[str, float]) -> str:
    """Build context injection that tells the LLM to acknowledge a preference change.

    Called when NL preference detection fires, so the LLM can confirm the change
    naturally in its reply.
    """
    if not deltas:
        return ""

    descriptions = []
    for trait, delta in sorted(deltas.items()):
        direction = "more" if delta > 0 else "less"
        descriptions.append(f"{direction} {trait.replace('_', ' ')}")

    changes = ", ".join(descriptions)
    return (
        f"[Personality action: PREFERENCE_UPDATED]\n"
        f"The user just expressed a style preference. You adjusted: {changes}. "
        f"Briefly acknowledge this change in your reply (one short sentence like "
        f"\"Got it, I'll be more direct.\" or \"Noted, I'll slow down.\"). "
        f"Then continue responding to whatever else they said. "
        f"Do not over-explain the personality system."
    )


def _format_profile_status(
    profile: dict[str, Any] | None,
    *,
    human_id: str,
    agent_id: str | None,
    state_db: StateDB,
    config_manager: ConfigManager | None = None,
    session_id: str | None = None,
    turn_id: str | None = None,
) -> str:
    """Format current personality status for the user."""
    if not profile:
        return "Personality is not active. Using default balanced style."

    lines = []
    name = profile.get("personality_name")
    if name:
        lines.append(f"Base personality: {name}")
    else:
        lines.append("Base personality: default (balanced)")
    if agent_id:
        lines.append(f"Agent id: {agent_id}")
    if profile.get("agent_persona_name"):
        lines.append(f"Agent persona: {profile['agent_persona_name']}")

    lines.append(f"Source: {profile.get('source', 'defaults')}")
    lines.append("Current style:")
    labels = profile.get("style_labels") or {}
    traits = profile.get("traits") or {}
    for trait in ("warmth", "directness", "playfulness", "pacing", "assertiveness"):
        label = labels.get(trait, "balanced")
        val = traits.get(trait, 0.5)
        lines.append(f"  {trait}: {label} ({val:.2f})")

    user_deltas = _load_user_trait_deltas(human_id=human_id, state_db=state_db)
    agent_base_traits = profile.get("agent_base_traits") or {}
    if agent_base_traits:
        lines.append("Agent base persona:")
        for trait, value in sorted((str(key), float(val)) for key, val in agent_base_traits.items()):
            lines.append(f"  {trait}: {value:.2f}")
    if user_deltas:
        lines.append("User adjustments applied:")
        for trait, delta in sorted(user_deltas.items()):
            sign = "+" if delta >= 0 else ""
            lines.append(f"  {trait}: {sign}{delta:.2f}")
    else:
        lines.append("No user adjustments applied.")

    if config_manager is not None:
        try:
            memory_state = read_personality_preferences_from_memory(
                config_manager=config_manager,
                state_db=state_db,
                human_id=human_id,
                session_id=session_id,
                turn_id=turn_id,
            )
            if not memory_state.abstained and not memory_state.shadow_only and memory_state.records:
                lines.append("Memory-backed current-state facts:")
                for record in memory_state.records:
                    predicate = str(record.get("predicate") or "")
                    value = record.get("value")
                    trait = predicate.rsplit(".", 1)[-1] if "." in predicate else predicate
                    if trait:
                        lines.append(f"  {trait}: {value}")
        except Exception:
            pass

    return "\n".join(lines)


def _clear_user_trait_deltas(*, human_id: str, state_db: StateDB) -> list[str]:
    """Clear all per-user trait deltas (reset to base personality)."""
    with state_db.connect() as conn:
        conn.execute(
            "DELETE FROM personality_trait_profiles WHERE human_id = ?",
            (human_id,),
        )
        cleared_state_keys = clear_reset_sensitive_scope(
            conn,
            scope_kind="human",
            scope_ref=human_id,
            component="personality_profile",
        )
        conn.commit()
    return cleared_state_keys


def _clear_legacy_user_trait_overlay(*, human_id: str, state_db: StateDB) -> None:
    with state_db.connect() as conn:
        conn.execute(
            "DELETE FROM personality_trait_profiles WHERE human_id = ?",
            (human_id,),
        )
        conn.execute(
            "DELETE FROM runtime_state WHERE state_key = ?",
            (_state_key(human_id),),
        )
        conn.commit()


# ── Self-observation ──

def _observation_state_key(human_id: str) -> str:
    return f"personality:{human_id}:observations"


def _evolution_log_state_key(human_id: str) -> str:
    return f"personality:{human_id}:evolution_log"


def _load_recent_observations(*, human_id: str, state_db: StateDB) -> list[dict[str, Any]]:
    try:
        with state_db.connect() as conn:
            rows = conn.execute(
                """
                SELECT observed_at, user_state, confidence, traits_json
                FROM personality_observations
                WHERE human_id = ?
                ORDER BY observed_at ASC, created_at ASC
                """,
                (human_id,),
            ).fetchall()
        if rows:
            return [
                {
                    "ts": row["observed_at"],
                    "user_state": row["user_state"],
                    "confidence": float(row["confidence"]),
                    "traits": json.loads(row["traits_json"] or "{}"),
                }
                for row in rows
            ]
    except Exception:
        pass
    try:
        with state_db.connect() as conn:
            row = conn.execute(
                "SELECT value FROM runtime_state WHERE state_key = ? LIMIT 1",
                (_observation_state_key(human_id),),
            ).fetchone()
        if row and row["value"]:
            data = json.loads(row["value"])
            return data.get("observations", []) if isinstance(data, dict) else []
    except Exception:
        pass
    return []


def _store_observation_runtime_mirror(
    *,
    human_id: str,
    observations: list[dict[str, Any]],
    state_db: StateDB,
    conn: Any | None = None,
) -> None:
    payload = json.dumps({"observations": observations}, sort_keys=True)
    if conn is not None:
        upsert_runtime_state(
            conn,
            state_key=_observation_state_key(human_id),
            value=payload,
            component="personality_profile",
            reset_sensitive_scope=("human", human_id, "personality_preference_reset"),
        )
        return
    with state_db.connect() as mirror_conn:
        upsert_runtime_state(
            mirror_conn,
            state_key=_observation_state_key(human_id),
            value=payload,
            component="personality_profile",
            reset_sensitive_scope=("human", human_id, "personality_preference_reset"),
        )
        mirror_conn.commit()


def _load_evolution_events(*, human_id: str, state_db: StateDB) -> list[dict[str, Any]]:
    try:
        with state_db.connect() as conn:
            rows = conn.execute(
                """
                SELECT evolved_at, deltas_json, state_weights_json, observation_count
                FROM personality_evolution_events
                WHERE human_id = ?
                ORDER BY evolved_at ASC, created_at ASC
                """,
                (human_id,),
            ).fetchall()
        if rows:
            return [
                {
                    "ts": row["evolved_at"],
                    "deltas": json.loads(row["deltas_json"] or "{}"),
                    "state_weights": json.loads(row["state_weights_json"] or "{}"),
                    "observation_count": int(row["observation_count"] or 0),
                }
                for row in rows
            ]
    except Exception:
        pass
    try:
        with state_db.connect() as conn:
            row = conn.execute(
                "SELECT value FROM runtime_state WHERE state_key = ? LIMIT 1",
                (_evolution_log_state_key(human_id),),
            ).fetchone()
        if row and row["value"]:
            data = json.loads(row["value"])
            return data.get("events", []) if isinstance(data, dict) else []
    except Exception:
        pass
    return []


def _store_evolution_runtime_mirror(
    *,
    human_id: str,
    events: list[dict[str, Any]],
    state_db: StateDB,
    conn: Any | None = None,
) -> None:
    payload = json.dumps({"events": events[-20:]}, sort_keys=True)
    if conn is not None:
        upsert_runtime_state(
            conn,
            state_key=_evolution_log_state_key(human_id),
            value=payload,
            component="personality_profile",
            reset_sensitive_scope=("human", human_id, "personality_preference_reset"),
        )
        return
    with state_db.connect() as mirror_conn:
        upsert_runtime_state(
            mirror_conn,
            state_key=_evolution_log_state_key(human_id),
            value=payload,
            component="personality_profile",
            reset_sensitive_scope=("human", human_id, "personality_preference_reset"),
        )
        mirror_conn.commit()


_OBSERVATION_WINDOW = 50  # keep last N observations per user

# Lightweight emotional state inference from user text (subset of room reader patterns)
_USER_STATE_PATTERNS: list[tuple[re.Pattern[str], str, float]] = [
    (re.compile(r"\b(?:thanks?|thank you|perfect|great|awesome|nice|love it)\b", re.I), "satisfied", 0.7),
    (re.compile(r"\b(?:exactly|yes|that's? (?:it|right)|spot on|nailed it)\b", re.I), "satisfied", 0.6),
    (re.compile(r"\b(?:broken|failing|error|bug|crash|doesn't work|not working)\b", re.I), "frustrated", 0.5),
    (re.compile(r"\b(?:still|again|keeps?|always|never works)\b", re.I), "frustrated", 0.4),
    (re.compile(r"\b(?:confused|lost|don't understand|makes? no sense|what\?)\b", re.I), "confused", 0.6),
    (re.compile(r"\b(?:how does|what is|can you explain|help me understand)\b", re.I), "curious", 0.4),
    (re.compile(r"\b(?:amazing|incredible|wow|brilliant|this is great)\b", re.I), "excited", 0.7),
    (re.compile(r"\b(?:urgent|asap|hurry|quickly|right now|ship it)\b", re.I), "rushed", 0.6),
    (re.compile(r"\b(?:whatever|fine|ok|sure|i guess)\b", re.I), "disengaged", 0.3),
    (re.compile(r"\b(?:too (?:much|long|slow|fast|verbose|brief))\b", re.I), "style_friction", 0.6),
]


def _infer_user_state(text: str) -> tuple[str, float]:
    """Infer the user's emotional state from their message.

    Returns (state_name, confidence). Falls back to ("neutral", 0.0).
    """
    if not text:
        return ("neutral", 0.0)

    best_state = "neutral"
    best_score = 0.0

    for pattern, state, weight in _USER_STATE_PATTERNS:
        if pattern.search(text):
            if weight > best_score:
                best_state = state
                best_score = weight

    return (best_state, best_score)


def record_observation(
    *,
    human_id: str,
    user_message: str,
    traits_active: dict[str, float],
    state_db: StateDB,
) -> dict[str, Any]:
    """Record a personality observation after an interaction.

    Stores what traits were active and what the user's inferred state was.
    Used later by self-evolution to identify what's working.
    """
    user_state, confidence = _infer_user_state(user_message)

    observation = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "user_state": user_state,
        "confidence": round(confidence, 2),
        "traits": {k: round(v, 3) for k, v in traits_active.items()},
    }
    existing = _load_recent_observations(human_id=human_id, state_db=state_db)
    existing.append(observation)
    if len(existing) > _OBSERVATION_WINDOW:
        existing = existing[-_OBSERVATION_WINDOW:]
    with state_db.connect() as conn:
        conn.execute(
            """
            INSERT INTO personality_observations(
                observation_id,
                human_id,
                observed_at,
                user_state,
                confidence,
                traits_json
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                f"personality-observation-{uuid4().hex}",
                human_id,
                observation["ts"],
                user_state,
                float(observation["confidence"]),
                json.dumps(observation["traits"], sort_keys=True),
            ),
        )
        total_rows = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM personality_observations
            WHERE human_id = ?
            """,
            (human_id,),
        ).fetchone()
        delete_count = max(0, int(total_rows["count"]) - _OBSERVATION_WINDOW)
        if delete_count > 0:
            conn.execute(
                """
                DELETE FROM personality_observations
                WHERE observation_id IN (
                    SELECT observation_id
                    FROM personality_observations
                    WHERE human_id = ?
                    ORDER BY observed_at ASC, created_at ASC
                    LIMIT ?
                )
                """,
                (human_id, delete_count),
            )
        _store_observation_runtime_mirror(
            human_id=human_id,
            observations=existing,
            state_db=state_db,
            conn=conn,
        )
        conn.commit()

    return observation


# ── Self-evolution ──

# How much a single evolution cycle can shift a trait
_EVOLUTION_STEP = 0.05
# Minimum observations before evolution triggers
_EVOLUTION_MIN_OBSERVATIONS = 10

# State → trait signal mapping: if users are in this state, these traits
# should be nudged in this direction to improve the interaction
_STATE_TRAIT_SIGNALS: dict[str, dict[str, float]] = {
    "frustrated": {"warmth": 0.5, "pacing": -0.3, "assertiveness": -0.2},
    "confused": {"directness": -0.5, "pacing": -0.5},
    "satisfied": {},  # positive signal — reinforce current traits
    "excited": {},  # positive signal
    "rushed": {"directness": 0.3, "pacing": 0.5},
    "disengaged": {"playfulness": 0.3, "warmth": 0.3},
    "style_friction": {},  # detected by NL preferences, not auto-adjusted
    "curious": {"pacing": -0.2},  # slow down to explain
}


def maybe_evolve_traits(
    *,
    human_id: str,
    state_db: StateDB,
) -> dict[str, float] | None:
    """Analyze accumulated observations and apply small trait adjustments.

    Returns the evolution deltas applied, or None if no evolution occurred.
    Only runs when enough observations have accumulated.
    Evolution deltas are bounded to +-_EVOLUTION_STEP per trait per cycle.
    """
    observations = _load_recent_observations(human_id=human_id, state_db=state_db)
    if not observations:
        return None

    if len(observations) < _EVOLUTION_MIN_OBSERVATIONS:
        return None

    # Count state occurrences weighted by confidence
    state_weights: dict[str, float] = {}
    total_weight = 0.0
    for obs in observations:
        state = obs.get("user_state", "neutral")
        conf = obs.get("confidence", 0.0)
        if state != "neutral" and conf > 0.3:
            state_weights[state] = state_weights.get(state, 0.0) + conf
            total_weight += conf

    if total_weight < 2.0:
        # Not enough signal to evolve
        return None

    # Compute trait adjustment signals
    trait_signals: dict[str, float] = {}
    for state, weight in state_weights.items():
        signals = _STATE_TRAIT_SIGNALS.get(state, {})
        normalized_weight = weight / total_weight
        for trait, direction in signals.items():
            trait_signals[trait] = trait_signals.get(trait, 0.0) + direction * normalized_weight

    if not trait_signals:
        return None

    # Clamp each signal to evolution step size
    evolution_deltas: dict[str, float] = {}
    for trait, signal in trait_signals.items():
        if abs(signal) < 0.1:
            continue  # below noise threshold
        clamped = max(-_EVOLUTION_STEP, min(_EVOLUTION_STEP, signal * _EVOLUTION_STEP))
        evolution_deltas[trait] = round(clamped, 4)

    if not evolution_deltas:
        return None

    # Apply evolution deltas to existing user deltas
    existing = _load_user_trait_deltas(human_id=human_id, state_db=state_db)
    merged = dict(existing)
    for trait, delta in evolution_deltas.items():
        merged[trait] = max(-0.5, min(0.5, merged.get(trait, 0.0) + delta))

    _save_user_trait_deltas(human_id=human_id, deltas=merged, state_db=state_db)

    # Log the evolution event
    _record_evolution_event(
        human_id=human_id,
        state_db=state_db,
        evolution_deltas=evolution_deltas,
        state_weights=state_weights,
        observation_count=len(observations),
    )

    # Clear observations after evolution (start fresh)
    with state_db.connect() as conn:
        conn.execute(
            "DELETE FROM personality_observations WHERE human_id = ?",
            (human_id,),
        )
        clear_registered_state_keys(
            conn,
            state_keys=[_observation_state_key(human_id)],
        )
        conn.commit()

    return evolution_deltas


def _record_evolution_event(
    *,
    human_id: str,
    state_db: StateDB,
    evolution_deltas: dict[str, float],
    state_weights: dict[str, float],
    observation_count: int,
) -> None:
    """Record an evolution event for auditability."""
    existing_log = _load_evolution_events(human_id=human_id, state_db=state_db)
    event = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "deltas": evolution_deltas,
        "state_weights": {k: round(v, 2) for k, v in state_weights.items()},
        "observation_count": observation_count,
    }
    existing_log.append(event)
    existing_log = existing_log[-20:]
    with state_db.connect() as conn:
        conn.execute(
            """
            INSERT INTO personality_evolution_events(
                evolution_id,
                human_id,
                evolved_at,
                deltas_json,
                state_weights_json,
                observation_count
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                f"personality-evolution-{uuid4().hex}",
                human_id,
                event["ts"],
                json.dumps(event["deltas"], sort_keys=True),
                json.dumps(event["state_weights"], sort_keys=True),
                observation_count,
            ),
        )
        _store_evolution_runtime_mirror(
            human_id=human_id,
            events=existing_log,
            state_db=state_db,
            conn=conn,
        )
        conn.commit()
