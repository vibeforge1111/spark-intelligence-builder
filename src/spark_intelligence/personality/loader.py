"""Personality profile loading, agent-base authoring, and per-user preference management.

Reads personality chip state from the filesystem (written by spark-personality-chip-labs
hooks) and manages per-user trait deltas via typed state tables with runtime_state
mirrors kept only for compatibility.

Trait names follow PersonalityEvolver's 5-trait system:
  warmth, directness, playfulness, pacing, assertiveness

Each trait is a float in [0.0, 1.0]. Agent-base traits are persisted per Builder-owned
agent_id in agent_persona_profiles. Human-specific deltas shift the active baseline traits
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
from spark_intelligence.identity.service import (
    cancel_agent_onboarding,
    read_canonical_agent_state,
    rename_agent_identity,
    set_human_user_address,
)
from spark_intelligence.memory.orchestrator import (
    delete_personality_preferences_from_memory,
    read_personality_preferences_from_memory,
    write_personality_preferences_to_memory,
)
from spark_intelligence.observability.store import (
    recent_personality_evolution_events,
    recent_personality_observations,
    record_event,
)
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

# Anchor phrases shown to the operator during the P2-7 guided onboarding
# sub-state. Each trait maps five ordinal ratings (1..5) to a short phrase
# the operator can pick between. These are intentionally not used as
# trait-value ranges — they're only human-readable anchors for the
# 1-5 picker. The actual trait delta mapping lives in the P2-7 handler.
# Source: Q-F decision in docs/PERSONALITY_ONBOARDING_V2_DESIGN_2026-04-10.md §11.
_GUIDED_TRAIT_ANCHORS: dict[str, dict[int, str]] = {
    "warmth": {
        1: "reserved and formal",
        2: "mostly reserved",
        3: "balanced warmth",
        4: "warm and friendly",
        5: "very warm, uses your name often",
    },
    "directness": {
        1: "gentle and exploratory",
        2: "mostly gentle",
        3: "balanced directness",
        4: "direct and concise",
        5: "very direct, skips preamble",
    },
    "playfulness": {
        1: "serious and measured",
        2: "mostly serious",
        3: "balanced playfulness",
        4: "playful and witty",
        5: "very playful, cracks jokes",
    },
    "pacing": {
        1: "deliberate, explains thoroughly",
        2: "thoughtful and thorough",
        3: "balanced pacing",
        4: "brisk and efficient",
        5: "rapid, one-line answers",
    },
    "assertiveness": {
        1: "gentle, hedges opinions",
        2: "measured and polite",
        3: "balanced assertiveness",
        4: "assertive and confident",
        5: "very assertive, states views plainly",
    },
}

# ── NL preference patterns (inline fallback) ──
# These are a minimal subset. The full set lives in personality_engine.nl_traits.

_NL_TRAIT_PATTERNS: list[tuple[re.Pattern[str], dict[str, float]]] = [
    (re.compile(r"\b(?:be\s+|more\s+|too\s+)direct\b", re.I), {"directness": 0.4}),
    (re.compile(r"\b(?:be\s+|more\s+|too\s+)concise\b", re.I), {"directness": 0.3, "pacing": 0.2}),
    (re.compile(r"\b(?:preferred\s+(?:spark\s+)?)?(?:reply|response|answer)\s+style\s+(?:is|should\s+be|to\s+be)\s+[^.?!]*\bconcise\b", re.I), {"directness": 0.3, "pacing": 0.2}),
    (re.compile(r"\b(?:preferred\s+(?:spark\s+)?)?(?:reply|response|answer)\s+style\s+(?:is|should\s+be|to\s+be)\s+[^.?!]*\bwarm\b", re.I), {"warmth": 0.4}),
    (re.compile(r"\bkeep\s+(?:replies|responses?)\s+short(?:er)?\b", re.I), {"directness": 0.2, "pacing": 0.3}),
    (re.compile(r"\bavoid\s+long\s+(?:monologues|responses?|answers?)\b", re.I), {"directness": 0.2, "pacing": 0.3}),
    (re.compile(r"\bskip\b.*(?:explain|preamble|intro)", re.I), {"directness": 0.4, "pacing": 0.3}),
    (re.compile(r"\bget\s+to\s+the\s+point\b", re.I), {"directness": 0.4, "pacing": 0.3}),
    (re.compile(r"\bless\s+verbose\b", re.I), {"directness": 0.3, "pacing": 0.2}),
    (re.compile(r"\bmore\s+(?:detail|thorough|verbose)\b", re.I), {"directness": -0.3, "pacing": -0.3}),
    (re.compile(r"\b(?:be\s+|more\s+)casual\b|\bmore\s+human\b", re.I), {"warmth": 0.2, "playfulness": 0.1}),
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

_BEHAVIORAL_RULE_PREFIXES = (
    "be ",
    "keep ",
    "sound ",
    "avoid ",
    "give ",
    "skip ",
    "continue ",
    "answer ",
    "use ",
    "treat ",
    "do not ",
    "when ",
    "adjust ",
    "default ",
    "push ",
    "prefer ",
    "ask ",
    "start ",
    "first ",
    "identify ",
    "narrow ",
    "focus ",
    "stay ",
    "use ",
    "skip ",
    "respond ",
    "treat ",
    "when i ",
    "if i ",
    "if the topic ",
    "for simple questions",
    "for broad topics",
    "don't ",
    "dont ",
    "do not ",
)

_TELEGRAM_GENERIC_OPENER_REWRITES: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"(?i)(?:^|\s+)(?:and\s+)?what(?:'s| is)\s+on your mind\?(?:\s|$)"),
        " ",
    ),
    (
        re.compile(r"(?i)(?:^|\s+)(?:and\s+)?what are you working on\?(?:\s|$)"),
        " ",
    ),
    (
        re.compile(r"(?i)(?:^|\s+)(?:and\s+)?how can i help(?: you)?(?: today)?\?(?:\s|$)"),
        " ",
    ),
)

_TELEGRAM_TRIVIAL_GREETING_RE = re.compile(
    r"(?i)^(?:hey|hi|hello|hello there|hi there|hey there)[!. ]*$"
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def resolve_builder_persona_agent_id(*, human_id: str | None) -> str | None:
    normalized = str(human_id or "").strip()
    if not normalized:
        return None
    return f"agent:{normalized}"


def _persona_lookup_agent_ids(*, agent_id: str | None, human_id: str | None) -> list[str]:
    candidate_ids: list[str] = []
    builder_agent_id = resolve_builder_persona_agent_id(human_id=human_id)
    if builder_agent_id:
        candidate_ids.append(builder_agent_id)
    normalized_agent_id = str(agent_id or "").strip()
    if normalized_agent_id and normalized_agent_id not in candidate_ids:
        candidate_ids.append(normalized_agent_id)
    return candidate_ids


@dataclass
class AgentPersonaMutationResult:
    agent_id: str
    agent_name: str | None
    trait_deltas: dict[str, float]
    behavioral_rules: list[str]
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


@dataclass
class AgentOnboardingTurnResult:
    human_id: str
    agent_id: str
    step: str
    reply_text: str
    agent_name: str
    persona_profile: dict[str, Any]
    completed: bool


@dataclass
class PersonalityImportResult:
    human_id: str
    agent_id: str
    persona_name: str | None
    persona_summary: str | None
    base_traits: dict[str, float]
    behavioral_rules: list[str]
    evolver_state: dict[str, Any]


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


def _split_behavioral_rule_candidates(text: str) -> list[str]:
    normalized = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    candidates: list[str] = []
    for raw_line in normalized.split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        line = re.sub(r"^[\-\*\u2022]+\s*", "", line)
        for chunk in re.split(r"(?<=[.!])\s+(?=[A-Z\"'`])", line):
            compact = " ".join(chunk.strip().split())
            if compact:
                candidates.append(compact)
    return candidates


def _normalize_behavioral_rule(rule: str) -> str:
    compact = " ".join(str(rule or "").strip().split())
    compact = compact.strip("\"'`")
    compact = compact.rstrip(".! ")
    if not compact:
        return ""
    return compact[0].upper() + compact[1:]


def _extract_behavioral_rules(text: str) -> list[str]:
    rules: list[str] = []
    for candidate in _split_behavioral_rule_candidates(text):
        lowered = candidate.lower().replace("’", "'")
        if candidate.startswith("/") or candidate.endswith("?"):
            continue
        if len(candidate) < 12 or len(candidate) > 180:
            continue
        if not any(lowered.startswith(prefix) for prefix in _BEHAVIORAL_RULE_PREFIXES):
            continue
        normalized = _normalize_behavioral_rule(candidate)
        if normalized and normalized.lower() not in {item.lower() for item in rules}:
            rules.append(normalized)
    return rules


def _merge_behavioral_rules(existing: list[str], incoming: list[str], *, limit: int = 8) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for rule in [*existing, *incoming]:
        normalized = _normalize_behavioral_rule(rule)
        if not normalized:
            continue
        lowered = normalized.lower()
        if lowered in seen:
            continue
        merged.append(normalized)
        seen.add(lowered)
    return merged[-limit:]


def _behavioral_rule_summary(rules: list[str], *, limit: int = 160) -> str | None:
    if not rules:
        return None
    summary = "; ".join(_normalize_behavioral_rule(rule) for rule in rules[:3] if _normalize_behavioral_rule(rule))
    summary = " ".join(summary.split()).strip()
    if not summary:
        return None
    if len(summary) <= limit:
        return summary
    return summary[: limit - 1].rstrip() + "…"


def _directive_sentence(text: str) -> str:
    normalized = _normalize_behavioral_rule(text)
    if not normalized:
        return ""
    if normalized.endswith((".", "!", "?")):
        return normalized
    return f"{normalized}."


def _is_agent_persona_authoring_message(text: str) -> bool:
    lowered = text.strip().lower()
    if not lowered:
        return False
    behavioral_rules = _extract_behavioral_rules(text)
    if _extract_agent_name(text):
        return True
    if len(behavioral_rules) >= 2:
        return True
    if any(marker in lowered for marker in _AGENT_PERSONA_MARKERS) and (behavioral_rules or _has_personality_signal(text)):
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
    agent_persona = (
        load_agent_persona_profile(agent_id=agent_id, human_id=human_id, state_db=state_db)
        if agent_id or human_id
        else {}
    )
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
        "agent_id": agent_persona.get("agent_id") or agent_id,
        "agent_persona_name": agent_persona.get("persona_name"),
        "agent_persona_summary": agent_persona.get("persona_summary"),
        "agent_behavioral_rules": agent_persona.get("behavioral_rules") or [],
        "agent_persona_applied": agent_persona_applied,
        "agent_base_traits": agent_base_traits,
        "user_deltas_applied": user_deltas_applied,
        "source": source,
    }


def resolve_personality_evolver_state_path(*, config_manager: ConfigManager | None) -> Path:
    configured_path = (
        config_manager.get_path(
            "spark.personality.evolver_state_path",
            default=None,
        )
        if config_manager is not None
        else None
    )
    return Path(configured_path) if configured_path else _PERSONALITY_EVOLUTION_FILE


def build_personality_import_payload(
    *,
    human_id: str,
    agent_id: str,
    state_db: StateDB,
    config_manager: ConfigManager | None = None,
    observation_limit: int = 10,
    evolution_limit: int = 10,
) -> dict[str, Any]:
    canonical_state = read_canonical_agent_state(state_db=state_db, human_id=human_id)
    current_profile = load_personality_profile(
        human_id=human_id,
        agent_id=agent_id,
        state_db=state_db,
        config_manager=config_manager,
    )
    current_agent_persona = load_agent_persona_profile(agent_id=agent_id, human_id=human_id, state_db=state_db)
    observations = recent_personality_observations(state_db, human_id=human_id, limit=observation_limit)
    evolutions = recent_personality_evolution_events(state_db, human_id=human_id, limit=evolution_limit)
    evolver_path = resolve_personality_evolver_state_path(config_manager=config_manager)
    return {
        "schema_version": "spark-personality-import-request.v1",
        "requested_at": _utc_now_iso(),
        "hook": "personality",
        "human_id": human_id,
        "agent_id": agent_id,
        "identity": canonical_state.to_payload(),
        "current_profile": current_profile or {},
        "current_agent_persona": current_agent_persona,
        "recent_observations": observations,
        "recent_evolutions": evolutions,
        "evolver_state_path": str(evolver_path),
    }


def normalize_personality_import(
    *,
    human_id: str,
    agent_id: str,
    hook_output: dict[str, Any],
) -> PersonalityImportResult:
    result = hook_output.get("result")
    if not isinstance(result, dict):
        raise ValueError("Personality hook must return a JSON object under result.")

    result_human_id = str(result.get("human_id") or human_id).strip() or human_id
    if result_human_id != human_id:
        raise ValueError(f"Personality hook returned human_id '{result_human_id}' but expected '{human_id}'.")

    result_agent_id = str(result.get("agent_id") or agent_id).strip() or agent_id
    if result_agent_id != agent_id:
        raise ValueError(f"Personality hook returned agent_id '{result_agent_id}' but expected '{agent_id}'.")

    base_traits_candidate = result.get("base_traits")
    if not isinstance(base_traits_candidate, dict):
        evolver_state_candidate = result.get("evolver_state")
        if isinstance(evolver_state_candidate, dict) and isinstance(evolver_state_candidate.get("traits"), dict):
            base_traits_candidate = evolver_state_candidate.get("traits")
        else:
            raise ValueError("Personality hook must return base_traits or evolver_state.traits.")

    base_traits = {
        trait: round(max(0.0, min(1.0, float(base_traits_candidate.get(trait, _DEFAULT_TRAITS[trait])))), 3)
        for trait in _DEFAULT_TRAITS
    }
    behavioral_rules_candidate = result.get("behavioral_rules") or []
    if behavioral_rules_candidate and not isinstance(behavioral_rules_candidate, list):
        raise ValueError("Personality hook behavioral_rules must be a list when provided.")
    behavioral_rules = [str(item).strip() for item in behavioral_rules_candidate if str(item).strip()]
    persona_name = _read_optional_text(result.get("persona_name"))
    persona_summary = _read_optional_text(result.get("persona_summary"))
    personality_id = _read_optional_text(result.get("personality_id"))
    personality_name = _read_optional_text(result.get("personality_name")) or persona_name
    evolver_state_candidate = result.get("evolver_state")
    if evolver_state_candidate is not None and not isinstance(evolver_state_candidate, dict):
        raise ValueError("Personality hook evolver_state must be a JSON object when provided.")
    evolver_state = dict(evolver_state_candidate) if isinstance(evolver_state_candidate, dict) else {}
    if not evolver_state:
        evolver_state = {
            "traits": base_traits,
            "last_signals": {
                "personality_id": personality_id,
                "personality_name": personality_name,
            },
        }
    elif not isinstance(evolver_state.get("traits"), dict):
        evolver_state["traits"] = dict(base_traits)
    last_signals = evolver_state.get("last_signals")
    if not isinstance(last_signals, dict):
        last_signals = {}
    if personality_id and not last_signals.get("personality_id"):
        last_signals["personality_id"] = personality_id
    if personality_name and not last_signals.get("personality_name"):
        last_signals["personality_name"] = personality_name
    evolver_state["last_signals"] = last_signals

    return PersonalityImportResult(
        human_id=human_id,
        agent_id=agent_id,
        persona_name=persona_name,
        persona_summary=persona_summary,
        base_traits=base_traits,
        behavioral_rules=behavioral_rules,
        evolver_state=evolver_state,
    )


def write_personality_evolver_state(
    *,
    config_manager: ConfigManager | None,
    evolver_state: dict[str, Any],
    write_path: str | Path | None = None,
) -> str:
    target_path = Path(write_path) if write_path is not None else resolve_personality_evolver_state_path(config_manager=config_manager)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text(json.dumps(evolver_state, indent=2, ensure_ascii=True), encoding="utf-8")
    return str(target_path)


def build_personality_context(profile: dict[str, Any]) -> str:
    """Build a personality context string for injection into LLM prompts.

    Returns a compact section suitable for system_prompt or contextual_task injection.
    """
    if not profile:
        return ""

    traits = profile.get("traits") or {}
    labels = profile.get("style_labels") or {}
    name = profile.get("personality_name")
    agent_persona_name = profile.get("agent_persona_name")
    agent_persona_summary = str(profile.get("agent_persona_summary") or "").strip()
    behavioral_rules = [str(rule).strip() for rule in list(profile.get("agent_behavioral_rules") or []) if str(rule).strip()]

    lines = ["[Personality context]"]
    # When an agent persona is explicitly configured, keep the prompt centered on
    # that agent identity instead of surfacing a global personality chip name.
    if name and not agent_persona_name:
        lines.append(f"active_personality={name}")
    if agent_persona_name:
        lines.append(f"agent_persona={agent_persona_name}")
    if agent_persona_summary:
        lines.append(f"agent_persona_summary={agent_persona_summary}")

    style_parts = []
    for trait in ("warmth", "directness", "playfulness", "pacing", "assertiveness"):
        label = labels.get(trait, "balanced")
        style_parts.append(label)
    lines.append(f"style={', '.join(style_parts)}")

    # Compact trait values for transparency
    trait_vals = " ".join(f"{t}={v:.2f}" for t, v in sorted(traits.items()))
    lines.append(f"trait_values={trait_vals}")
    if behavioral_rules:
        lines.append("behavioral_rules=" + " | ".join(behavioral_rules[:5]))

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
    agent_persona_name = profile.get("agent_persona_name")
    agent_persona_summary = str(profile.get("agent_persona_summary") or "").strip()
    behavioral_rules = [str(rule).strip() for rule in list(profile.get("agent_behavioral_rules") or []) if str(rule).strip()]

    parts = []
    if name and not agent_persona_name:
        parts.append(f"Your active personality profile is '{name}'.")
    if agent_persona_name:
        parts.append(f"Your agent persona is '{agent_persona_name}'.")
    if agent_persona_summary:
        parts.append(f"Saved persona summary: {agent_persona_summary}.")

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
    if behavioral_rules:
        rules_text = " ".join(_directive_sentence(rule) for rule in behavioral_rules[:6] if _directive_sentence(rule))
        if rules_text:
            parts.append(
                "Follow these saved agent style rules unless the user explicitly redirects this turn: "
                f"{rules_text}"
            )

    if profile.get("user_deltas_applied"):
        parts.append("These style preferences were set by the user through conversation.")
    elif profile.get("agent_persona_applied"):
        parts.append("These style settings reflect the saved agent persona.")

    return " ".join(parts)


def build_telegram_persona_reply_contract(profile: dict[str, Any]) -> str:
    """Build a Telegram-facing reply contract from saved persona state.

    This is stronger than the generic personality directive: it tells Builder-owned
    conversational surfaces how the agent should *feel* in visible Telegram replies.
    """
    if not profile:
        return ""

    traits = profile.get("traits") or {}
    personality_name = str(profile.get("personality_name") or "").strip()
    agent_persona_name = str(profile.get("agent_persona_name") or "").strip()
    agent_persona_summary = str(profile.get("agent_persona_summary") or "").strip()
    behavioral_rules = [str(rule).strip() for rule in list(profile.get("agent_behavioral_rules") or []) if str(rule).strip()]

    parts: list[str] = [
        "Let the saved persona shape the visible Telegram reply, not just hidden reasoning."
    ]
    if agent_persona_name:
        parts.append(f"Speak with the steady voice of '{agent_persona_name}'.")
    elif personality_name:
        parts.append(f"Keep the visible Telegram voice consistent with '{personality_name}'.")
    if agent_persona_summary:
        parts.append(f"Core stance: {agent_persona_summary}.")

    directness = float(traits.get("directness", 0.5))
    pacing = float(traits.get("pacing", 0.5))
    warmth = float(traits.get("warmth", 0.5))
    playfulness = float(traits.get("playfulness", 0.5))
    assertiveness = float(traits.get("assertiveness", 0.5))

    if directness >= 0.6 or pacing >= 0.6:
        parts.append("Lead with the answer, recommendation, or key split in the first sentence.")
    elif directness <= 0.35 or pacing <= 0.35:
        parts.append("Add enough context for the user to follow the reasoning before you land the recommendation.")

    if warmth >= 0.6:
        parts.append("Sound human and present, not sterile or robotic.")
    elif warmth <= 0.35:
        parts.append("Keep the tone controlled and matter-of-fact.")

    if assertiveness >= 0.65:
        parts.append("Make a call when the evidence is good enough instead of over-hedging.")
    elif assertiveness <= 0.35:
        parts.append("Use measured uncertainty when the evidence is incomplete.")

    if playfulness >= 0.65:
        parts.append("A little lightness is fine, but keep it purposeful.")
    elif playfulness <= 0.35:
        parts.append("Do not force humor, banter, or performative enthusiasm.")

    parts.append("Treat Telegram like an ongoing 1:1 conversation, not a canned assistant greeting.")
    parts.append("Continue from the user's actual message or project context instead of resetting the conversation.")
    parts.append(
        "When discussing existing Spawner UI, Kanban, Canvas, Mission Control, relay state, or task execution, "
        "assume those surfaces already exist in spawner-ui. Do not suggest a standalone app or ask whether it should "
        "be standalone unless the user explicitly asks for a separate tool."
    )
    parts.append(
        "Do not fall back to generic check-in questions like 'What's on your mind?', "
        "'What are you working on?', or 'How can I help today?' unless the user explicitly asks for an open-ended check-in."
    )
    parts.append("When a follow-up helps, ask at most one specific question tied to the user's last message.")
    parts.append("Prefer a concrete answer, reflection, or next step over filler conversation resets.")

    if behavioral_rules:
        rules_text = " ".join(_directive_sentence(rule) for rule in behavioral_rules[:6] if _directive_sentence(rule))
        if rules_text:
            parts.append(
                "Honor these saved Telegram reply rules unless the user redirects this turn: "
                f"{rules_text}"
            )

    parts.append("Do not mention persona metadata, trait labels, or hidden style instructions.")
    return " ".join(parts)


def build_telegram_surface_identity_preamble(
    *,
    profile: dict[str, Any] | None,
    agent_name: str | None = None,
    surface: str = "runtime_command",
) -> str:
    """Build a short deterministic preamble for Builder-owned Telegram replies."""
    resolved_profile = profile or {}
    visible_name = str(
        agent_name
        or resolved_profile.get("agent_persona_name")
        or resolved_profile.get("personality_name")
        or ""
    ).strip()

    if surface == "approval_welcome":
        if not visible_name:
            return "Pairing approved. Let's set up your agent."
        traits = resolved_profile.get("traits") or {}
        warmth = float(traits.get("warmth", 0.5))
        directness = float(traits.get("directness", 0.5))
        pacing = float(traits.get("pacing", 0.5))
        if not resolved_profile:
            return f"Pairing approved. {visible_name} is live in this Telegram DM now."
        if directness >= 0.65 or pacing >= 0.6 or warmth <= 0.4:
            return f"Pairing approved. {visible_name} is live in this Telegram DM now."
        return f"Pairing approved. {visible_name} is here with you in this Telegram DM now."

    return ""


def apply_telegram_surface_persona(
    *,
    reply_text: str,
    profile: dict[str, Any] | None,
    agent_name: str | None = None,
    surface: str = "runtime_command",
) -> str:
    """Apply light identity/tone framing to deterministic Telegram replies."""
    normalized_text = _normalize_telegram_conversation_reply(str(reply_text or ""))
    if not normalized_text:
        return ""
    if surface == "approval_welcome":
        preamble = build_telegram_surface_identity_preamble(
            profile=profile,
            agent_name=agent_name,
            surface=surface,
        )
        return preamble or normalized_text

    preamble = build_telegram_surface_identity_preamble(
        profile=profile,
        agent_name=agent_name,
        surface=surface,
    )
    if not preamble:
        return normalized_text

    lowered_text = normalized_text.lower()
    visible_name = str(
        agent_name
        or (profile or {}).get("agent_persona_name")
        or (profile or {}).get("personality_name")
        or ""
    ).strip()
    if visible_name and (
        lowered_text.startswith(f"{visible_name.lower()}:")
        or lowered_text.startswith(f"{visible_name.lower()} here.")
        or lowered_text.startswith(f"`{visible_name.lower()}`")
    ):
        return normalized_text

    lines = normalized_text.splitlines()
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        lines[index] = f"{preamble} {line.strip()}".strip()
        break
    return "\n".join(lines)


def _normalize_telegram_conversation_reply(reply_text: str) -> str:
    normalized = str(reply_text or "").strip()
    if not normalized:
        return ""

    for pattern, replacement in _TELEGRAM_GENERIC_OPENER_REWRITES:
        normalized = pattern.sub(replacement, normalized)

    normalized = re.sub(r"[ \t]{2,}", " ", normalized)
    normalized = re.sub(r" *\n *", "\n", normalized)
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    normalized = re.sub(r"\s+([?.!,])", r"\1", normalized)
    normalized = re.sub(r"([.!?]){2,}", r"\1", normalized)
    normalized = normalized.strip()

    if _TELEGRAM_TRIVIAL_GREETING_RE.fullmatch(normalized):
        return "Ready when you are."

    return normalized


def load_agent_persona_profile(
    *,
    agent_id: str | None,
    human_id: str | None = None,
    state_db: StateDB | None,
) -> dict[str, Any]:
    candidate_ids = _persona_lookup_agent_ids(agent_id=agent_id, human_id=human_id)
    if not candidate_ids or state_db is None:
        return {}
    try:
        with state_db.connect() as conn:
            for candidate_agent_id in candidate_ids:
                row = conn.execute(
                    """
                    SELECT persona_name, persona_summary, base_traits_json, behavioral_rules_json, provenance_json, updated_at
                    FROM agent_persona_profiles
                    WHERE agent_id = ?
                    LIMIT 1
                    """,
                    (candidate_agent_id,),
                ).fetchone()
                if not row:
                    continue
                base_traits = json.loads(row["base_traits_json"] or "{}")
                behavioral_rules = json.loads(row["behavioral_rules_json"] or "[]") if row["behavioral_rules_json"] else []
                provenance = json.loads(row["provenance_json"] or "{}") if row["provenance_json"] else {}
                return {
                    "agent_id": candidate_agent_id,
                    "persona_name": _read_optional_text(row["persona_name"]),
                    "persona_summary": _read_optional_text(row["persona_summary"]),
                    "base_traits": {k: float(v) for k, v in base_traits.items() if k in _DEFAULT_TRAITS},
                    "behavioral_rules": behavioral_rules if isinstance(behavioral_rules, list) else [],
                    "provenance": provenance if isinstance(provenance, dict) else {},
                    "updated_at": row["updated_at"],
                }
        return {}
    except Exception:
        return {}


def push_agent_persona_undo_snapshot(
    *,
    agent_id: str,
    human_id: str,
    state_db: StateDB,
    source_surface: str,
    source_ref: str | None = None,
) -> str:
    storage_agent_id = resolve_builder_persona_agent_id(human_id=human_id) or agent_id
    current_profile = load_agent_persona_profile(agent_id=storage_agent_id, human_id=human_id, state_db=state_db)
    base_traits = dict(_DEFAULT_TRAITS)
    base_traits.update({k: float(v) for k, v in dict(current_profile.get("base_traits") or {}).items() if k in _DEFAULT_TRAITS})
    snapshot_id = f"agent-persona-snapshot-{uuid4().hex[:12]}"
    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with state_db.connect() as conn:
        conn.execute(
            """
            INSERT INTO agent_persona_undo_snapshots(
                snapshot_id, agent_id, human_id, persona_name, persona_summary, base_traits_json,
                behavioral_rules_json, provenance_json, source_surface, source_ref, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot_id,
                storage_agent_id,
                human_id,
                current_profile.get("persona_name"),
                current_profile.get("persona_summary"),
                json.dumps(base_traits, sort_keys=True),
                json.dumps(list(current_profile.get("behavioral_rules") or []), sort_keys=True),
                json.dumps(dict(current_profile.get("provenance") or {}), sort_keys=True),
                source_surface,
                source_ref,
                created_at,
            ),
        )
        conn.commit()
    return snapshot_id


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
    push_undo_snapshot: bool = False,
) -> dict[str, Any]:
    storage_agent_id = resolve_builder_persona_agent_id(human_id=human_id) or agent_id
    updated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    normalized_traits = {
        key: round(max(0.0, min(1.0, float(value))), 3)
        for key, value in base_traits.items()
        if key in _DEFAULT_TRAITS
    }
    mutation_id = f"agent-persona-{uuid4().hex[:12]}"
    if push_undo_snapshot:
        push_agent_persona_undo_snapshot(
            agent_id=storage_agent_id,
            human_id=human_id,
            state_db=state_db,
            source_surface=source_surface,
            source_ref=source_ref,
        )
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
                storage_agent_id,
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
                storage_agent_id,
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
    return load_agent_persona_profile(agent_id=storage_agent_id, human_id=human_id, state_db=state_db)


def pop_agent_persona_undo_snapshot(
    *,
    agent_id: str,
    human_id: str,
    state_db: StateDB,
    source_surface: str,
    source_ref: str | None = None,
) -> dict[str, Any] | None:
    storage_agent_id = resolve_builder_persona_agent_id(human_id=human_id) or agent_id
    with state_db.connect() as conn:
        row = conn.execute(
            """
            SELECT snapshot_id, persona_name, persona_summary, base_traits_json, behavioral_rules_json, provenance_json
            FROM agent_persona_undo_snapshots
            WHERE agent_id = ? AND human_id = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (storage_agent_id, human_id),
        ).fetchone()
        if not row:
            return None
        conn.execute(
            "DELETE FROM agent_persona_undo_snapshots WHERE snapshot_id = ?",
            (row["snapshot_id"],),
        )
        conn.commit()
    base_traits = json.loads(row["base_traits_json"] or "{}") if row["base_traits_json"] else {}
    behavioral_rules = json.loads(row["behavioral_rules_json"] or "[]") if row["behavioral_rules_json"] else []
    provenance = json.loads(row["provenance_json"] or "{}") if row["provenance_json"] else {}
    return save_agent_persona_profile(
        agent_id=storage_agent_id,
        human_id=human_id,
        state_db=state_db,
        base_traits={**_DEFAULT_TRAITS, **{k: float(v) for k, v in dict(base_traits).items() if k in _DEFAULT_TRAITS}},
        persona_name=_read_optional_text(row["persona_name"]),
        persona_summary=_read_optional_text(row["persona_summary"]),
        behavioral_rules=behavioral_rules if isinstance(behavioral_rules, list) else [],
        provenance=provenance if isinstance(provenance, dict) else {},
        mutation_kind="rollback",
        source_surface=source_surface,
        source_ref=source_ref,
        push_undo_snapshot=False,
    )


def create_agent_persona_savepoint(
    *,
    agent_id: str,
    human_id: str,
    savepoint_name: str,
    state_db: StateDB,
    source_surface: str,
    source_ref: str | None = None,
) -> str:
    storage_agent_id = resolve_builder_persona_agent_id(human_id=human_id) or agent_id
    current_profile = load_agent_persona_profile(agent_id=storage_agent_id, human_id=human_id, state_db=state_db)
    base_traits = dict(_DEFAULT_TRAITS)
    base_traits.update({k: float(v) for k, v in dict(current_profile.get("base_traits") or {}).items() if k in _DEFAULT_TRAITS})
    savepoint_id = f"agent-persona-savepoint-{uuid4().hex[:12]}"
    created_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    normalized_name = " ".join(str(savepoint_name or "").strip().split())
    with state_db.connect() as conn:
        conn.execute(
            """
            INSERT INTO agent_persona_savepoints(
                savepoint_id, agent_id, human_id, savepoint_name, persona_name, persona_summary, base_traits_json,
                behavioral_rules_json, provenance_json, source_surface, source_ref, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                savepoint_id,
                storage_agent_id,
                human_id,
                normalized_name,
                current_profile.get("persona_name"),
                current_profile.get("persona_summary"),
                json.dumps(base_traits, sort_keys=True),
                json.dumps(list(current_profile.get("behavioral_rules") or []), sort_keys=True),
                json.dumps(dict(current_profile.get("provenance") or {}), sort_keys=True),
                source_surface,
                source_ref,
                created_at,
            ),
        )
        conn.commit()
    return savepoint_id


def list_agent_persona_savepoints(
    *,
    agent_id: str,
    human_id: str,
    state_db: StateDB,
    limit: int = 10,
) -> list[dict[str, Any]]:
    storage_agent_id = resolve_builder_persona_agent_id(human_id=human_id) or agent_id
    with state_db.connect() as conn:
        rows = conn.execute(
            """
            SELECT savepoint_id, savepoint_name, persona_name, persona_summary, created_at
            FROM agent_persona_savepoints
            WHERE agent_id = ? AND human_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (storage_agent_id, human_id, limit),
        ).fetchall()
    return [dict(row) for row in rows]


def load_agent_persona_savepoint(
    *,
    agent_id: str,
    human_id: str,
    savepoint_name: str,
    state_db: StateDB,
) -> dict[str, Any] | None:
    storage_agent_id = resolve_builder_persona_agent_id(human_id=human_id) or agent_id
    normalized_name = " ".join(str(savepoint_name or "").strip().split())
    with state_db.connect() as conn:
        row = conn.execute(
            """
            SELECT savepoint_id, savepoint_name, persona_name, persona_summary, base_traits_json, behavioral_rules_json, provenance_json, created_at
            FROM agent_persona_savepoints
            WHERE agent_id = ? AND human_id = ? AND lower(savepoint_name) = lower(?)
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (storage_agent_id, human_id, normalized_name),
        ).fetchone()
    if not row:
        return None
    base_traits = json.loads(row["base_traits_json"] or "{}") if row["base_traits_json"] else {}
    behavioral_rules = json.loads(row["behavioral_rules_json"] or "[]") if row["behavioral_rules_json"] else []
    provenance = json.loads(row["provenance_json"] or "{}") if row["provenance_json"] else {}
    return {
        "savepoint_id": row["savepoint_id"],
        "savepoint_name": row["savepoint_name"],
        "persona_name": _read_optional_text(row["persona_name"]),
        "persona_summary": _read_optional_text(row["persona_summary"]),
        "base_traits": {**_DEFAULT_TRAITS, **{k: float(v) for k, v in dict(base_traits).items() if k in _DEFAULT_TRAITS}},
        "behavioral_rules": behavioral_rules if isinstance(behavioral_rules, list) else [],
        "provenance": provenance if isinstance(provenance, dict) else {},
        "created_at": row["created_at"],
    }


def restore_agent_persona_savepoint(
    *,
    agent_id: str,
    human_id: str,
    savepoint_name: str,
    state_db: StateDB,
    source_surface: str,
    source_ref: str | None = None,
) -> dict[str, Any] | None:
    storage_agent_id = resolve_builder_persona_agent_id(human_id=human_id) or agent_id
    normalized_name = " ".join(str(savepoint_name or "").strip().split())
    with state_db.connect() as conn:
        row = conn.execute(
            """
            SELECT persona_name, persona_summary, base_traits_json, behavioral_rules_json, provenance_json
            FROM agent_persona_savepoints
            WHERE agent_id = ? AND human_id = ? AND lower(savepoint_name) = lower(?)
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (storage_agent_id, human_id, normalized_name),
        ).fetchone()
    if not row:
        return None
    base_traits = json.loads(row["base_traits_json"] or "{}") if row["base_traits_json"] else {}
    behavioral_rules = json.loads(row["behavioral_rules_json"] or "[]") if row["behavioral_rules_json"] else []
    provenance = json.loads(row["provenance_json"] or "{}") if row["provenance_json"] else {}
    return save_agent_persona_profile(
        agent_id=storage_agent_id,
        human_id=human_id,
        state_db=state_db,
        base_traits={**_DEFAULT_TRAITS, **{k: float(v) for k, v in dict(base_traits).items() if k in _DEFAULT_TRAITS}},
        persona_name=_read_optional_text(row["persona_name"]),
        persona_summary=_read_optional_text(row["persona_summary"]),
        behavioral_rules=behavioral_rules if isinstance(behavioral_rules, list) else [],
        provenance=provenance if isinstance(provenance, dict) else {},
        mutation_kind="savepoint_restore",
        source_surface=source_surface,
        source_ref=source_ref,
        push_undo_snapshot=True,
    )


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
    resolved_agent_id = (
        resolve_builder_persona_agent_id(human_id=human_id)
        or agent_id
        or read_canonical_agent_state(state_db=state_db, human_id=human_id).agent_id
    )
    existing_persona = load_agent_persona_profile(agent_id=resolved_agent_id, human_id=human_id, state_db=state_db)
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


def agent_has_reonboard_candidate(
    *,
    human_id: str,
    agent_id: str,
    state_db: StateDB,
) -> bool:
    """Return True if the caller should start the P2-12 reonboard offer.

    P2-13 of docs/PERSONALITY_PHASE2_PLAN_2026-04-10.md. The Telegram
    runtime uses this helper as an entry gate so existing users with
    a saved persona profile but no in-progress onboarding state blob
    still get the P2-12 one-tap skip offer on their next DM — not
    only freshly-paired users whose pairing welcome is still pending.

    Returns True only when BOTH of the following hold:

    1. There is no existing onboarding state blob for this human
       (so we never re-fire the offer once the user has interacted
       with the state machine at all).
    2. There is a saved persona profile for this agent/human pair
       (so genuinely new pairings still flow through the standard
       `awaiting_name` entry, not the reonboard offer).
    """
    if not human_id or not agent_id or state_db is None:
        return False
    onboarding_state = _load_agent_onboarding_state(
        human_id=human_id, state_db=state_db
    )
    if onboarding_state:
        return False
    existing_persona = load_agent_persona_profile(
        agent_id=agent_id, human_id=human_id, state_db=state_db
    )
    return bool(existing_persona)


def maybe_handle_agent_persona_onboarding_turn(
    *,
    human_id: str,
    agent_id: str,
    user_message: str,
    state_db: StateDB,
    source_surface: str,
    source_ref: str | None = None,
    start_if_eligible: bool = False,
) -> AgentOnboardingTurnResult | None:
    canonical_state = read_canonical_agent_state(state_db=state_db, human_id=human_id)
    onboarding_state = _load_agent_onboarding_state(human_id=human_id, state_db=state_db)

    if canonical_state.preferred_source != "builder_local" or canonical_state.external_system == "spark_swarm":
        if onboarding_state:
            _delete_agent_onboarding_state(human_id=human_id, state_db=state_db)
        return None

    existing_persona = load_agent_persona_profile(agent_id=agent_id, human_id=human_id, state_db=state_db)
    if onboarding_state.get("status") == "completed":
        return None

    if not onboarding_state:
        if not start_if_eligible:
            return None
        if existing_persona:
            # P2-12: existing users with a saved persona see a
            # one-tap skip offer instead of being silently skipped
            # (Q-H of docs/PERSONALITY_ONBOARDING_V2_DESIGN_2026-04-10.md
            # §11). Any reply other than yes closes out the
            # offer without touching the persona profile.
            onboarding_state = {
                "status": "active",
                "step": "awaiting_reonboard_consent",
                "agent_id": agent_id,
                "started_at": _utc_now_iso(),
                "updated_at": _utc_now_iso(),
            }
            _save_agent_onboarding_state(
                human_id=human_id, payload=onboarding_state, state_db=state_db
            )
            return AgentOnboardingTurnResult(
                human_id=human_id,
                agent_id=agent_id,
                step="awaiting_reonboard_consent",
                reply_text=_build_reonboard_consent_offer_text(
                    canonical_state.agent_name
                ),
                agent_name=canonical_state.agent_name,
                persona_profile=existing_persona,
                completed=False,
            )
        onboarding_state = {
            "status": "active",
            "step": "awaiting_name",
            "agent_id": agent_id,
            "started_at": _utc_now_iso(),
            "updated_at": _utc_now_iso(),
        }
        _save_agent_onboarding_state(human_id=human_id, payload=onboarding_state, state_db=state_db)
        if canonical_state.has_user_defined_name:
            name_prompt = (
                f"What should I call your agent? Right now it's `{canonical_state.agent_name}`. "
                "Reply with a new name, or say `keep` to keep the current one."
            )
        else:
            name_prompt = (
                "What should I call your agent? Reply with a name like `Atlas`, `Nova`, or `Lyra`."
            )
        return AgentOnboardingTurnResult(
            human_id=human_id,
            agent_id=agent_id,
            step="awaiting_name",
            reply_text=(
                "Let's set up your agent. Short conversation: name first, then personality.\n"
                "You can say `/cancel` at any time to stop and reset.\n\n"
                f"{name_prompt}"
            ),
            agent_name=canonical_state.agent_name,
            persona_profile=existing_persona,
            completed=False,
        )

    step = str(onboarding_state.get("step") or "awaiting_name")
    normalized_message = " ".join(str(user_message or "").strip().split())
    lowered = normalized_message.lower()

    # P2-11: /cancel escape hatch. Q-E of the v2 design doc
    # (docs/PERSONALITY_ONBOARDING_V2_DESIGN_2026-04-10.md §11)
    # decided that /cancel wipes BOTH the in-progress onboarding
    # state blob AND the saved agent name (back to the empty-string
    # sentinel), while leaving the persona profile untouched per
    # Q-J. The caller-facing reply confirms the wipe and points
    # toward restarting from pairing on the next DM.
    if lowered in _ONBOARDING_CANCEL_TOKENS:
        _delete_agent_onboarding_state(human_id=human_id, state_db=state_db)
        cancelled_state = cancel_agent_onboarding(
            state_db=state_db,
            human_id=human_id,
            source_ref=source_ref,
        )
        return AgentOnboardingTurnResult(
            human_id=human_id,
            agent_id=agent_id,
            step="cancelled",
            reply_text=_build_onboarding_cancelled_reply_text(),
            agent_name=cancelled_state.agent_name,
            persona_profile=existing_persona,
            completed=True,
        )

    if step == "awaiting_reonboard_consent":
        # P2-12: one-tap skip offer for existing users with a saved
        # persona. Accept only explicit opt-ins; anything else closes
        # out the state (status=completed) so the offer never nags
        # again, and returns None so the researcher bridge handles
        # the original message normally.
        if lowered in _REONBOARD_CONSENT_YES_TOKENS:
            onboarding_state["step"] = "awaiting_name"
            onboarding_state["updated_at"] = _utc_now_iso()
            _save_agent_onboarding_state(
                human_id=human_id, payload=onboarding_state, state_db=state_db
            )
            if canonical_state.has_user_defined_name:
                name_prompt = (
                    f"What should I call your agent? Right now it's "
                    f"`{canonical_state.agent_name}`. Reply with a new name, "
                    "or say `keep` to keep the current one."
                )
            else:
                name_prompt = (
                    "What should I call your agent? Reply with a name like "
                    "`Atlas`, `Nova`, or `Lyra`."
                )
            return AgentOnboardingTurnResult(
                human_id=human_id,
                agent_id=agent_id,
                step="awaiting_name",
                reply_text=(
                    "Restarting setup. Short conversation: name first, then "
                    "personality.\n"
                    "You can say `/cancel` at any time to stop and reset.\n\n"
                    f"{name_prompt}"
                ),
                agent_name=canonical_state.agent_name,
                persona_profile=existing_persona,
                completed=False,
            )
        # Skip / silence path: close out the offer without touching
        # the persona profile and let the normal reply path handle
        # the user's message.
        _complete_agent_onboarding_state(
            human_id=human_id,
            state_db=state_db,
            agent_id=agent_id,
            agent_name=canonical_state.agent_name,
            persona_summary=(
                existing_persona.get("persona_summary") if existing_persona else None
            ),
        )
        return None

    if step == "awaiting_name":
        if lowered in {"keep", "keep it", "keep current name", "keep the current name"}:
            if not canonical_state.has_user_defined_name:
                return AgentOnboardingTurnResult(
                    human_id=human_id,
                    agent_id=agent_id,
                    step="awaiting_name",
                    reply_text=(
                        "There's no current name to keep yet. Give your agent a name first, "
                        "something like `Atlas`, `Nova`, or `Lyra`."
                    ),
                    agent_name=canonical_state.agent_name,
                    persona_profile=existing_persona,
                    completed=False,
                )
            onboarding_state["step"] = "awaiting_user_address"
            onboarding_state["updated_at"] = _utc_now_iso()
            _save_agent_onboarding_state(human_id=human_id, payload=onboarding_state, state_db=state_db)
            return AgentOnboardingTurnResult(
                human_id=human_id,
                agent_id=agent_id,
                step="awaiting_user_address",
                reply_text=(
                    f"Keeping the current name `{canonical_state.agent_name}`.\n\n"
                    + _build_user_address_prompt()
                ),
                agent_name=canonical_state.agent_name,
                persona_profile=existing_persona,
                completed=False,
            )

        candidate_name = _extract_agent_name(normalized_message) or _extract_onboarding_agent_name(normalized_message)
        if not candidate_name:
            return AgentOnboardingTurnResult(
                human_id=human_id,
                agent_id=agent_id,
                step="awaiting_name",
                reply_text=(
                    "I need a short agent name first. "
                    "Reply with something like `Atlas`, `Nova`, or `Lyra`."
                ),
                agent_name=canonical_state.agent_name,
                persona_profile=existing_persona,
                completed=False,
            )

        renamed_state = rename_agent_identity(
            state_db=state_db,
            human_id=human_id,
            new_name=candidate_name,
            source_surface=source_surface,
            source_ref=source_ref,
        )
        onboarding_state["step"] = "awaiting_user_address"
        onboarding_state["agent_name"] = renamed_state.agent_name
        onboarding_state["updated_at"] = _utc_now_iso()
        _save_agent_onboarding_state(human_id=human_id, payload=onboarding_state, state_db=state_db)
        return AgentOnboardingTurnResult(
            human_id=human_id,
            agent_id=agent_id,
            step="awaiting_user_address",
            reply_text=(
                f"Saved. Your agent is now `{renamed_state.agent_name}`.\n\n"
                + _build_user_address_prompt()
            ),
            agent_name=renamed_state.agent_name,
            persona_profile=existing_persona,
            completed=False,
        )

    if step == "awaiting_user_address":
        if lowered in _ONBOARDING_ADDRESS_SKIP_TOKENS or not normalized_message:
            stored_address = set_human_user_address(
                state_db=state_db,
                human_id=human_id,
                user_address=None,
            )
        else:
            stored_address = set_human_user_address(
                state_db=state_db,
                human_id=human_id,
                user_address=normalized_message,
            )
        onboarding_state["step"] = "awaiting_persona_mode"
        onboarding_state["user_address"] = stored_address
        onboarding_state["updated_at"] = _utc_now_iso()
        _save_agent_onboarding_state(human_id=human_id, payload=onboarding_state, state_db=state_db)
        if stored_address:
            ack = format_address_aware_line(
                "{salutation}got it — I'll address you as "
                f"`{stored_address}`.",
                stored_address,
            )
        else:
            ack = "Got it — no salutation, I'll keep replies neutral."
        return AgentOnboardingTurnResult(
            human_id=human_id,
            agent_id=agent_id,
            step="awaiting_persona_mode",
            reply_text=f"{ack}\n\n" + _build_persona_mode_prompt(),
            agent_name=canonical_state.agent_name,
            persona_profile=existing_persona,
            completed=False,
        )

    if step == "awaiting_persona_mode":
        persona_mode = _parse_onboarding_persona_mode(lowered)
        if persona_mode is None:
            return AgentOnboardingTurnResult(
                human_id=human_id,
                agent_id=agent_id,
                step="awaiting_persona_mode",
                reply_text=(
                    "I didn't catch that. Reply with `guided`, `express`, "
                    "`freestyle`, or the number `1`, `2`, or `3`."
                ),
                agent_name=canonical_state.agent_name,
                persona_profile=existing_persona,
                completed=False,
            )
        onboarding_state["persona_mode"] = persona_mode
        onboarding_state["updated_at"] = _utc_now_iso()
        if persona_mode == "guided":
            onboarding_state["step"] = "awaiting_persona_guided"
            onboarding_state["persona_guided_trait_index"] = 0
            onboarding_state["persona_guided_ratings"] = {}
            _save_agent_onboarding_state(human_id=human_id, payload=onboarding_state, state_db=state_db)
            first_trait = _ONBOARDING_GUIDED_TRAIT_ORDER[0]
            return AgentOnboardingTurnResult(
                human_id=human_id,
                agent_id=agent_id,
                step="awaiting_persona_guided",
                reply_text=_build_guided_trait_question(first_trait, 0),
                agent_name=canonical_state.agent_name,
                persona_profile=existing_persona,
                completed=False,
            )
        if persona_mode == "express":
            onboarding_state["step"] = "awaiting_persona_express"
            _save_agent_onboarding_state(human_id=human_id, payload=onboarding_state, state_db=state_db)
            return AgentOnboardingTurnResult(
                human_id=human_id,
                agent_id=agent_id,
                step="awaiting_persona_express",
                reply_text=_build_persona_express_catalog_text(),
                agent_name=canonical_state.agent_name,
                persona_profile=existing_persona,
                completed=False,
            )
        onboarding_state["step"] = "awaiting_persona_freestyle"
        _save_agent_onboarding_state(human_id=human_id, payload=onboarding_state, state_db=state_db)
        return AgentOnboardingTurnResult(
            human_id=human_id,
            agent_id=agent_id,
            step="awaiting_persona_freestyle",
            reply_text=(
                "Now describe the personality you want. "
                "You can say something like `calm, strategic, very direct, low-fluff`."
            ),
            agent_name=canonical_state.agent_name,
            persona_profile=existing_persona,
            completed=False,
        )

    if step == "awaiting_persona_guided":
        try:
            current_index = int(onboarding_state.get("persona_guided_trait_index") or 0)
        except (TypeError, ValueError):
            current_index = 0
        raw_ratings = onboarding_state.get("persona_guided_ratings") or {}
        ratings: dict[str, int] = {}
        if isinstance(raw_ratings, dict):
            for key, value in raw_ratings.items():
                try:
                    ratings[str(key)] = int(value)
                except (TypeError, ValueError):
                    continue

        if current_index < 0 or current_index >= len(_ONBOARDING_GUIDED_TRAIT_ORDER):
            # Defensive: restart the guided flow if the stored index is bogus.
            current_index = 0
            ratings = {}
            onboarding_state["persona_guided_trait_index"] = 0
            onboarding_state["persona_guided_ratings"] = {}
            onboarding_state["updated_at"] = _utc_now_iso()
            _save_agent_onboarding_state(human_id=human_id, payload=onboarding_state, state_db=state_db)
            return AgentOnboardingTurnResult(
                human_id=human_id,
                agent_id=agent_id,
                step="awaiting_persona_guided",
                reply_text=_build_guided_trait_question(_ONBOARDING_GUIDED_TRAIT_ORDER[0], 0),
                agent_name=canonical_state.agent_name,
                persona_profile=existing_persona,
                completed=False,
            )

        current_trait = _ONBOARDING_GUIDED_TRAIT_ORDER[current_index]
        rating = _parse_onboarding_guided_rating(normalized_message)
        if rating is None:
            return AgentOnboardingTurnResult(
                human_id=human_id,
                agent_id=agent_id,
                step="awaiting_persona_guided",
                reply_text=(
                    "I need a number from 1 to 5 (or the word `one`, `two`, `three`, `four`, or `five`).\n\n"
                    + _build_guided_trait_question(current_trait, current_index)
                ),
                agent_name=canonical_state.agent_name,
                persona_profile=existing_persona,
                completed=False,
            )

        ratings[current_trait] = rating
        next_index = current_index + 1
        if next_index < len(_ONBOARDING_GUIDED_TRAIT_ORDER):
            onboarding_state["persona_guided_trait_index"] = next_index
            onboarding_state["persona_guided_ratings"] = ratings
            onboarding_state["updated_at"] = _utc_now_iso()
            _save_agent_onboarding_state(human_id=human_id, payload=onboarding_state, state_db=state_db)
            next_trait = _ONBOARDING_GUIDED_TRAIT_ORDER[next_index]
            return AgentOnboardingTurnResult(
                human_id=human_id,
                agent_id=agent_id,
                step="awaiting_persona_guided",
                reply_text=_build_guided_trait_question(next_trait, next_index),
                agent_name=canonical_state.agent_name,
                persona_profile=existing_persona,
                completed=False,
            )

        guided_traits = {
            trait: _GUIDED_RATING_TO_VALUE[ratings[trait]]
            for trait in _ONBOARDING_GUIDED_TRAIT_ORDER
            if trait in ratings
        }
        persona_summary = _compact_persona_summary(guided_traits)
        persona_profile = save_agent_persona_profile(
            agent_id=agent_id,
            human_id=human_id,
            state_db=state_db,
            base_traits=guided_traits,
            persona_name=canonical_state.agent_name,
            persona_summary=persona_summary,
            provenance={
                "source_surface": source_surface,
                "source_ref": source_ref,
                "onboarding": True,
                "persona_mode": "guided",
                "guided_ratings": ratings,
            },
            mutation_kind="onboarding_guided",
            source_surface=source_surface,
            source_ref=source_ref,
        )
        onboarding_state["step"] = "awaiting_guardrails_ack"
        onboarding_state["agent_id"] = agent_id
        onboarding_state["agent_name"] = canonical_state.agent_name
        onboarding_state["persona_summary"] = persona_profile.get("persona_summary")
        onboarding_state["updated_at"] = _utc_now_iso()
        _save_agent_onboarding_state(
            human_id=human_id, payload=onboarding_state, state_db=state_db
        )
        return AgentOnboardingTurnResult(
            human_id=human_id,
            agent_id=agent_id,
            step="awaiting_guardrails_ack",
            reply_text=_build_guardrails_ack_card_text(canonical_state.agent_name),
            agent_name=canonical_state.agent_name,
            persona_profile=persona_profile,
            completed=False,
        )

    if step == "awaiting_persona_express":
        preset_key = _parse_onboarding_persona_express_choice(normalized_message)
        if preset_key is None:
            return AgentOnboardingTurnResult(
                human_id=human_id,
                agent_id=agent_id,
                step="awaiting_persona_express",
                reply_text=(
                    "I didn't catch that preset. Reply with the name or number.\n\n"
                    + _build_persona_express_catalog_text()
                ),
                agent_name=canonical_state.agent_name,
                persona_profile=existing_persona,
                completed=False,
            )
        preset = _ONBOARDING_EXPRESS_PRESETS[preset_key]
        preset_traits = {
            str(trait): float(value)
            for trait, value in (preset.get("base_traits") or {}).items()
        }
        persona_summary = _compact_persona_summary(preset_traits)
        persona_profile = save_agent_persona_profile(
            agent_id=agent_id,
            human_id=human_id,
            state_db=state_db,
            base_traits=preset_traits,
            persona_name=canonical_state.agent_name,
            persona_summary=persona_summary,
            provenance={
                "source_surface": source_surface,
                "source_ref": source_ref,
                "onboarding": True,
                "persona_mode": "express",
                "express_preset": preset_key,
            },
            mutation_kind="onboarding_express",
            source_surface=source_surface,
            source_ref=source_ref,
        )
        label = str(preset.get("label") or preset_key)
        onboarding_state["step"] = "awaiting_guardrails_ack"
        onboarding_state["agent_id"] = agent_id
        onboarding_state["agent_name"] = canonical_state.agent_name
        onboarding_state["persona_summary"] = persona_profile.get("persona_summary")
        onboarding_state["express_preset_label"] = label
        onboarding_state["updated_at"] = _utc_now_iso()
        _save_agent_onboarding_state(
            human_id=human_id, payload=onboarding_state, state_db=state_db
        )
        return AgentOnboardingTurnResult(
            human_id=human_id,
            agent_id=agent_id,
            step="awaiting_guardrails_ack",
            reply_text=_build_guardrails_ack_card_text(canonical_state.agent_name),
            agent_name=canonical_state.agent_name,
            persona_profile=persona_profile,
            completed=False,
        )

    if step == "awaiting_guardrails_ack":
        # P2-10: Show-but-don't-gate guardrails card (Q-C decision,
        # docs/PERSONALITY_ONBOARDING_V2_DESIGN_2026-04-10.md §11).
        # Silence / any non-`change` reply is treated as acceptance and
        # moves onboarding to the completed terminal state. The `change`
        # / `adjust` branch stays pinned to awaiting_guardrails_ack and
        # surfaces a short pointer toward the NL preference path, which
        # will land in a later P2 step.
        if lowered in {"change", "adjust", "edit", "modify"}:
            return AgentOnboardingTurnResult(
                human_id=human_id,
                agent_id=agent_id,
                step="awaiting_guardrails_ack",
                reply_text=(
                    "No problem. Reply `ok` to keep the commitments as-is, or say "
                    "things like `be gentler` / `be more direct` any time after "
                    "onboarding to shape tone."
                ),
                agent_name=canonical_state.agent_name,
                persona_profile=existing_persona,
                completed=False,
            )
        stored_persona_summary = onboarding_state.get("persona_summary")
        stored_user_address = onboarding_state.get("user_address")
        _complete_agent_onboarding_state(
            human_id=human_id,
            state_db=state_db,
            agent_id=agent_id,
            agent_name=canonical_state.agent_name,
            persona_summary=(
                str(stored_persona_summary) if stored_persona_summary else None
            ),
        )
        return AgentOnboardingTurnResult(
            human_id=human_id,
            agent_id=agent_id,
            step="completed",
            reply_text=_build_onboarding_completion_recap_text(
                agent_name=canonical_state.agent_name,
                user_address=(
                    str(stored_user_address) if stored_user_address else None
                ),
                persona_summary=(
                    str(stored_persona_summary) if stored_persona_summary else None
                ),
            ),
            agent_name=canonical_state.agent_name,
            persona_profile=existing_persona,
            completed=True,
        )

    if step != "awaiting_persona_freestyle":
        _delete_agent_onboarding_state(human_id=human_id, state_db=state_db)
        return None

    if lowered in {"skip", "skip for now", "later", "use default"}:
        completed_profile = existing_persona or {}
        onboarding_state["step"] = "awaiting_guardrails_ack"
        onboarding_state["agent_id"] = agent_id
        onboarding_state["agent_name"] = canonical_state.agent_name
        onboarding_state["persona_summary"] = (
            completed_profile.get("persona_summary") if completed_profile else None
        )
        onboarding_state["persona_mode"] = onboarding_state.get("persona_mode") or "freestyle"
        onboarding_state["persona_skip"] = True
        onboarding_state["updated_at"] = _utc_now_iso()
        _save_agent_onboarding_state(
            human_id=human_id, payload=onboarding_state, state_db=state_db
        )
        return AgentOnboardingTurnResult(
            human_id=human_id,
            agent_id=agent_id,
            step="awaiting_guardrails_ack",
            reply_text=_build_guardrails_ack_card_text(canonical_state.agent_name),
            agent_name=canonical_state.agent_name,
            persona_profile=completed_profile,
            completed=False,
        )

    base_traits = dict(existing_persona.get("base_traits") or _DEFAULT_TRAITS)
    trait_deltas = _extract_trait_deltas(normalized_message)
    for trait, delta in _extract_onboarding_descriptor_deltas(normalized_message).items():
        trait_deltas[trait] = round(trait_deltas.get(trait, 0.0) + delta, 3)
    next_traits = dict(base_traits)
    for trait, delta in trait_deltas.items():
        next_traits[trait] = max(0.0, min(1.0, float(next_traits.get(trait, _DEFAULT_TRAITS[trait])) + delta))
    persona_summary = _compact_onboarding_persona_summary(normalized_message) or _compact_persona_summary(next_traits)
    persona_profile = save_agent_persona_profile(
        agent_id=agent_id,
        human_id=human_id,
        state_db=state_db,
        base_traits=next_traits,
        persona_name=canonical_state.agent_name,
        persona_summary=persona_summary,
        provenance={
            "source_surface": source_surface,
            "source_ref": source_ref,
            "onboarding": True,
            "authoring_text": normalized_message,
            "trait_deltas": trait_deltas,
        },
        mutation_kind="onboarding_authoring",
        source_surface=source_surface,
        source_ref=source_ref,
    )
    onboarding_state["step"] = "awaiting_guardrails_ack"
    onboarding_state["agent_id"] = agent_id
    onboarding_state["agent_name"] = canonical_state.agent_name
    onboarding_state["persona_summary"] = persona_profile.get("persona_summary")
    onboarding_state["updated_at"] = _utc_now_iso()
    _save_agent_onboarding_state(
        human_id=human_id, payload=onboarding_state, state_db=state_db
    )
    return AgentOnboardingTurnResult(
        human_id=human_id,
        agent_id=agent_id,
        step="awaiting_guardrails_ack",
        reply_text=_build_guardrails_ack_card_text(canonical_state.agent_name),
        agent_name=canonical_state.agent_name,
        persona_profile=persona_profile,
        completed=False,
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
    push_undo_snapshot: bool = False,
) -> AgentPersonaMutationResult | None:
    if not _is_agent_persona_authoring_message(user_message):
        return None

    trait_deltas = _extract_trait_deltas(user_message)
    behavioral_rules = _extract_behavioral_rules(user_message)
    current_profile = load_agent_persona_profile(agent_id=agent_id, human_id=human_id, state_db=state_db)
    existing_traits = current_profile.get("base_traits") or dict(_DEFAULT_TRAITS)
    next_traits = dict(existing_traits)
    for trait, delta in trait_deltas.items():
        next_traits[trait] = max(0.0, min(1.0, float(next_traits.get(trait, _DEFAULT_TRAITS[trait])) + delta))

    new_name = _extract_agent_name(user_message)
    merged_rules = _merge_behavioral_rules(
        list(current_profile.get("behavioral_rules") or []),
        behavioral_rules,
    )
    if new_name:
        rename_agent_identity(
            state_db=state_db,
            human_id=human_id,
            new_name=new_name,
            source_surface=source_surface,
            source_ref=source_ref,
        )

    persona_profile = current_profile
    if trait_deltas or behavioral_rules:
        persona_summary = current_profile.get("persona_summary")
        if trait_deltas:
            persona_summary = _compact_persona_summary(next_traits)
        if behavioral_rules:
            persona_summary = _behavioral_rule_summary(merged_rules) or persona_summary
        persona_profile = save_agent_persona_profile(
            agent_id=agent_id,
            human_id=human_id,
            state_db=state_db,
            base_traits=next_traits,
            persona_name=current_profile.get("persona_name") or new_name,
            persona_summary=persona_summary,
            behavioral_rules=merged_rules,
            provenance={
                "source_surface": source_surface,
                "source_ref": source_ref,
                "authoring_text": user_message,
                "behavioral_rules": merged_rules,
            },
            mutation_kind="explicit_authoring",
            source_surface=source_surface,
            source_ref=source_ref,
            push_undo_snapshot=push_undo_snapshot,
        )

    if not new_name and not trait_deltas and not behavioral_rules:
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
    if behavioral_rules:
        parts.append(
            "The user added saved style rules for the agent. "
            "Acknowledge this briefly and apply them going forward: "
            + " | ".join(merged_rules[:5])
            + "."
        )
    return AgentPersonaMutationResult(
        agent_id=agent_id,
        agent_name=new_name,
        trait_deltas=trait_deltas,
        behavioral_rules=merged_rules,
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


def _compact_onboarding_persona_summary(text: str, *, limit: int = 240) -> str:
    compact = " ".join(str(text or "").strip().split())
    if not compact:
        return ""
    if len(compact) <= limit:
        return compact
    return f"{compact[: limit - 3].rstrip()}..."


def _extract_onboarding_descriptor_deltas(text: str) -> dict[str, float]:
    lowered = str(text or "").lower()
    deltas: dict[str, float] = {}

    def add(trait: str, delta: float) -> None:
        deltas[trait] = round(deltas.get(trait, 0.0) + delta, 3)

    if "direct" in lowered:
        add("directness", 0.35)
    if "concise" in lowered or "low-fluff" in lowered or "low fluff" in lowered or "no-fluff" in lowered:
        add("directness", 0.2)
        add("pacing", 0.15)
    if "warm" in lowered or "friendly" in lowered:
        add("warmth", 0.25)
    if "playful" in lowered:
        add("playfulness", 0.3)
    if "serious" in lowered:
        add("playfulness", -0.25)
    if "calm" in lowered:
        add("warmth", 0.1)
        add("assertiveness", -0.1)
    if "assertive" in lowered or "confident" in lowered:
        add("assertiveness", 0.3)
    if "gentle" in lowered or "cautious" in lowered:
        add("assertiveness", -0.2)
    if "strategic" in lowered:
        add("pacing", -0.1)
        add("assertiveness", 0.1)

    return deltas


_ONBOARDING_NAME_STOPWORDS = frozenset(
    {
        # Pronouns / articles
        "i", "me", "my", "mine", "we", "us", "our", "ours", "you", "your",
        "yours", "it", "its", "he", "she", "they", "them", "their", "a",
        "an", "the", "this", "that", "these", "those",
        # Auxiliaries / copulas
        "am", "is", "are", "was", "were", "be", "been", "being", "have",
        "has", "had", "do", "does", "did", "will", "would", "can", "could",
        "should", "might", "may", "must", "shall",
        # Common verbs that appear in sentences about agents
        "want", "need", "think", "know", "like", "love", "hate", "see",
        "say", "said", "get", "got", "make", "made", "take", "took",
        "call", "called", "name", "named", "use", "using", "actually",
        "really", "maybe", "kind", "sort",
        # Spark-specific words that would be odd as an agent name
        "spark", "swarm", "agent", "bot", "assistant", "terminal",
        # Conjunctions / prepositions
        "and", "or", "but", "so", "because", "if", "then", "for",
        "of", "in", "on", "at", "to", "from", "with", "by", "as",
        "about", "into", "out",
        # Question words
        "what", "who", "where", "when", "why", "how",
        # Onboarding hesitation
        "hmm", "hmmm", "idk", "dunno", "tbh", "ok", "okay", "sure",
        "yes", "yeah", "yep", "nope", "no",
    }
)


def _extract_onboarding_agent_name(text: str) -> str | None:
    """Extract a candidate agent name from a free-text onboarding reply.

    Conservative by design: rejects anything that looks like a sentence
    rather than a name. Historical bug: this accepted any 2-40 char
    alphanumeric string, so a message like 'we actually have a spark
    swarm agent' got captured as the agent's literal name. Now we
    require 1 or 2 tokens and reject any response containing common
    English function words. The explicit naming path
    (`_extract_agent_name`) still handles 'call me X' / 'my name is X'
    patterns separately, so users who phrase their answer naturally
    still get their name picked up.
    """
    compact = " ".join(str(text or "").strip().split()).strip("\"'")
    if not compact or compact.startswith("/"):
        return None
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _-]{1,39}", compact):
        return None

    tokens = compact.split()
    if not tokens or len(tokens) > 2:
        return None

    # Reject if any token is a common function word / stopword — those
    # are signals the user is answering in a sentence, not naming.
    for token in tokens:
        if token.lower() in _ONBOARDING_NAME_STOPWORDS:
            return None

    return compact


def _agent_onboarding_state_key(human_id: str) -> str:
    return f"agent_onboarding:{human_id}"


def _load_agent_onboarding_state(*, human_id: str, state_db: StateDB) -> dict[str, Any]:
    with state_db.connect() as conn:
        row = conn.execute(
            "SELECT value FROM runtime_state WHERE state_key = ? LIMIT 1",
            (_agent_onboarding_state_key(human_id),),
        ).fetchone()
    if not row or not row["value"]:
        return {}
    try:
        payload = json.loads(str(row["value"]))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _save_agent_onboarding_state(*, human_id: str, payload: dict[str, Any], state_db: StateDB) -> None:
    with state_db.connect() as conn:
        upsert_runtime_state(
            conn,
            state_key=_agent_onboarding_state_key(human_id),
            value=json.dumps(payload, sort_keys=True),
            component="personality_profile",
            reset_sensitive_scope=("human", human_id, "agent_onboarding_reset"),
        )
        conn.commit()


def _delete_agent_onboarding_state(*, human_id: str, state_db: StateDB) -> None:
    with state_db.connect() as conn:
        conn.execute(
            "DELETE FROM runtime_state WHERE state_key = ?",
            (_agent_onboarding_state_key(human_id),),
        )
        conn.commit()


def _complete_agent_onboarding_state(
    *,
    human_id: str,
    state_db: StateDB,
    agent_id: str,
    agent_name: str,
    persona_summary: str | None,
) -> None:
    _save_agent_onboarding_state(
        human_id=human_id,
        state_db=state_db,
        payload={
            "status": "completed",
            "step": "completed",
            "agent_id": agent_id,
            "agent_name": agent_name,
            "persona_summary": persona_summary,
            "completed_at": _utc_now_iso(),
            "updated_at": _utc_now_iso(),
        },
    )


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


def format_address_aware_line(template: str, user_address: str | None) -> str:
    """Format a reply template using the operator's preferred salutation.

    P2-4 of docs/PERSONALITY_PHASE2_PLAN_2026-04-10.md. The v2 onboarding
    state machine stores an optional salutation on `humans.user_address`
    (P2-1) and the address-aware formatter is how every v2 reply renders
    it without falling back to a default label like "Operator".

    Template conventions:
        {salutation}        — prefix form. Expands to "<Address>, "
                               when set, or "" otherwise.
        {salutation_suffix} — suffix form. Expands to ", <Address>"
                               when set, or "" otherwise.

    On the empty-address path (`user_address` is None, empty, or
    whitespace), both placeholders collapse to the empty string. When a
    `{salutation}` placeholder sat at the very start of the template,
    the helper additionally capitalizes the first alphabetic character
    of what remains so a template like "{salutation}got it." renders as
    "Got it." rather than the broken "got it.".

    Q-D decision of docs/PERSONALITY_ONBOARDING_V2_DESIGN_2026-04-10.md §11.
    """
    address = str(user_address or "").strip()
    prefix = f"{address}, " if address else ""
    suffix = f", {address}" if address else ""
    starts_with_salutation = template.startswith("{salutation}")
    formatted = template.replace("{salutation}", prefix).replace(
        "{salutation_suffix}", suffix
    )
    if not address and starts_with_salutation and formatted:
        for i, ch in enumerate(formatted):
            if ch.isalpha():
                formatted = formatted[:i] + ch.upper() + formatted[i + 1 :]
                break
    return formatted


_ONBOARDING_ADDRESS_SKIP_TOKENS: frozenset[str] = frozenset(
    {
        "skip",
        "none",
        "blank",
        "no",
        "nope",
        "(blank)",
        "(skip)",
        "(none)",
        "no address",
        "no salutation",
        "don't",
        "dont",
        "nothing",
    }
)


def _build_user_address_prompt() -> str:
    return (
        "How should your agent address you? "
        "Reply with a name or salutation like `Alice`, `Boss`, or `Captain`. "
        "Say `skip` to keep replies neutral."
    )


def _build_persona_mode_prompt() -> str:
    return (
        "How should we shape your personality?\n"
        "1. Guided — I ask 5 quick questions\n"
        "2. Express — pick a preset style\n"
        "3. Freestyle — describe it in your own words\n\n"
        "Reply with `guided`, `express`, `freestyle`, or the number `1`, `2`, or `3`."
    )


_ONBOARDING_PERSONA_MODE_TOKENS: dict[str, frozenset[str]] = {
    "guided": frozenset({"guided", "guide", "questions", "question", "1", "one"}),
    "express": frozenset({"express", "preset", "presets", "2", "two"}),
    "freestyle": frozenset({"freestyle", "free", "freeform", "describe", "3", "three"}),
}


def _parse_onboarding_persona_mode(lowered: str) -> str | None:
    text = " ".join(str(lowered or "").strip().split())
    for mode, tokens in _ONBOARDING_PERSONA_MODE_TOKENS.items():
        if text in tokens:
            return mode
    return None


# P2-7: Guided persona sub-state helpers. The guided flow walks the operator
# through five ordinal (1-5) trait questions using the anchor phrases from
# _GUIDED_TRAIT_ANCHORS (P2-3). Each rating maps to a trait value in
# [0.10, 0.30, 0.50, 0.70, 0.90] and the accumulated ratings become the
# base_traits passed to save_agent_persona_profile.
# Source: Q-F decision in docs/PERSONALITY_ONBOARDING_V2_DESIGN_2026-04-10.md §11.
_ONBOARDING_GUIDED_TRAIT_ORDER: tuple[str, ...] = (
    "warmth",
    "directness",
    "playfulness",
    "pacing",
    "assertiveness",
)

_GUIDED_RATING_TO_VALUE: dict[int, float] = {
    1: 0.10,
    2: 0.30,
    3: 0.50,
    4: 0.70,
    5: 0.90,
}

_GUIDED_NUMBER_WORDS: dict[str, int] = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
}

_GUIDED_TRAIT_PROMPTS: dict[str, str] = {
    "warmth": "How warm should your agent feel?",
    "directness": "How direct should your agent be?",
    "playfulness": "How playful should your agent sound?",
    "pacing": "How should your agent pace its replies?",
    "assertiveness": "How assertive should your agent be?",
}


def _parse_onboarding_guided_rating(text: str) -> int | None:
    """Parse a 1..5 rating from a user message.

    Accepts plain digits ("3"), digits embedded in a short phrase
    ("rating 3", "3 please"), and the English words "one" through
    "five" (case-insensitive). Returns None if no 1..5 rating can be
    extracted.
    """
    compact = " ".join(str(text or "").strip().lower().split())
    if not compact:
        return None
    if compact in {"1", "2", "3", "4", "5"}:
        return int(compact)
    tokens = compact.split()
    for token in tokens:
        if token.isdigit():
            value = int(token)
            if 1 <= value <= 5:
                return value
    for word, value in _GUIDED_NUMBER_WORDS.items():
        if word in tokens:
            return value
    return None


def _build_guided_trait_question(trait: str, index: int) -> str:
    """Render the guided persona question for the given trait.

    `index` is 0-based; the "Question N of 5" label uses 1-based
    numbering. The question body lists the five anchor phrases from
    _GUIDED_TRAIT_ANCHORS numbered 1..5 and instructs the operator to
    reply with a number.
    """
    prompt = _GUIDED_TRAIT_PROMPTS.get(trait, f"How should `{trait}` feel?")
    anchors = _GUIDED_TRAIT_ANCHORS.get(trait, {})
    lines = [f"Question {index + 1} of 5. {prompt}"]
    for rating in range(1, 6):
        label = anchors.get(rating, "")
        lines.append(f"  {rating}. {label}")
    lines.append("")
    lines.append("Reply with a number from 1 to 5.")
    return "\n".join(lines)


# P2-8: Express persona sub-state presets. Each preset maps to a trait
# vector that becomes the base_traits passed to save_agent_persona_profile
# on completion. Labels/descriptions mirror the style preset catalog
# exposed by `/style preset` in the Telegram runtime (operator,
# claude-like, concise, warm) so the "pick a preset" onboarding path uses
# the same named identities the operator will see later in conversation.
# Source: P2-8 in docs/PERSONALITY_PHASE2_PLAN_2026-04-10.md.
_ONBOARDING_EXPRESS_PRESET_ORDER: tuple[str, ...] = (
    "operator",
    "claude-like",
    "concise",
    "warm",
)

_ONBOARDING_EXPRESS_PRESETS: dict[str, dict[str, object]] = {
    "operator": {
        "label": "Operator",
        "description": "Direct, grounded, and concrete. Prefers next steps over filler.",
        "base_traits": {
            "warmth": 0.40,
            "directness": 0.85,
            "playfulness": 0.25,
            "pacing": 0.70,
            "assertiveness": 0.80,
        },
    },
    "claude-like": {
        "label": "Claude-like",
        "description": "Strong continuity, grounded follow-ups, low canned phrasing.",
        "base_traits": {
            "warmth": 0.65,
            "directness": 0.55,
            "playfulness": 0.45,
            "pacing": 0.50,
            "assertiveness": 0.55,
        },
    },
    "concise": {
        "label": "Concise",
        "description": "Short answers, brisk pacing, low filler.",
        "base_traits": {
            "warmth": 0.45,
            "directness": 0.80,
            "playfulness": 0.30,
            "pacing": 0.75,
            "assertiveness": 0.65,
        },
    },
    "warm": {
        "label": "Warm",
        "description": "Friendly and human, calm without losing structure.",
        "base_traits": {
            "warmth": 0.85,
            "directness": 0.50,
            "playfulness": 0.60,
            "pacing": 0.40,
            "assertiveness": 0.45,
        },
    },
}

_EXPRESS_NUMBER_WORDS: dict[str, int] = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
}


def _build_persona_express_catalog_text() -> str:
    """Render the express-preset catalog for awaiting_persona_express."""
    lines = ["Pick a preset by number or name."]
    for index, key in enumerate(_ONBOARDING_EXPRESS_PRESET_ORDER, start=1):
        preset = _ONBOARDING_EXPRESS_PRESETS[key]
        label = str(preset.get("label") or key)
        description = str(preset.get("description") or "")
        lines.append(f"  {index}. `{key}` \u2014 {label}: {description}")
    lines.append("")
    lines.append("Reply with the name (for example `warm`) or the number.")
    return "\n".join(lines)


def _parse_onboarding_persona_express_choice(text: str) -> str | None:
    """Match a user's express-preset choice against the catalog.

    Accepts the canonical preset key ("warm"), the preset label
    case-insensitive ("Warm"), a 1..4 digit, or the words "one".."four".
    Returns the canonical preset key (e.g. "warm") or None when no match
    can be made.
    """
    compact = " ".join(str(text or "").strip().lower().split())
    if not compact:
        return None
    if compact in _ONBOARDING_EXPRESS_PRESETS:
        return compact
    for key, preset in _ONBOARDING_EXPRESS_PRESETS.items():
        label = str(preset.get("label") or "").strip().lower()
        if label and compact == label:
            return key
    if compact in {"1", "2", "3", "4"}:
        return _ONBOARDING_EXPRESS_PRESET_ORDER[int(compact) - 1]
    tokens = compact.split()
    for token in tokens:
        if token.isdigit():
            value = int(token)
            if 1 <= value <= len(_ONBOARDING_EXPRESS_PRESET_ORDER):
                return _ONBOARDING_EXPRESS_PRESET_ORDER[value - 1]
    for word, value in _EXPRESS_NUMBER_WORDS.items():
        if word in tokens:
            return _ONBOARDING_EXPRESS_PRESET_ORDER[value - 1]
    for key, preset in _ONBOARDING_EXPRESS_PRESETS.items():
        key_lc = key.lower()
        label_lc = str(preset.get("label") or "").strip().lower()
        if key_lc and key_lc in tokens:
            return key
        if label_lc and label_lc in tokens:
            return key
    return None


def _build_guardrails_ack_card_text(agent_name: str) -> str:
    """Render the awaiting_guardrails_ack card (P2-10, Q-C/Q-G).

    Q-C decision: Show but don't gate — the user may reply `ok` (or
    anything that is not `change`) to accept, and the card is shown
    uniformly across guided/express/freestyle modes per Q-G.
    See docs/PERSONALITY_ONBOARDING_V2_DESIGN_2026-04-10.md §11.
    """
    name = f"`{agent_name}`"
    return (
        f"One more thing. Here's what {name} commits to:\n\n"
        f"  1. No glazing. {name} won't say 'great idea!' unless it's "
        "actually a great idea — and will say so when it isn't.\n"
        "  2. Better-way surfacing. If there's a clearly better approach, "
        f"{name} will say so, not just go along.\n"
        "  3. Honest failure reporting. If something isn't working, "
        f"{name} will tell you, not pretend.\n\n"
        "Reply `ok` to accept, or `change` to adjust. You can always "
        "say `be gentler` or `be more direct` later."
    )


def _build_onboarding_completion_recap_text(
    *,
    agent_name: str,
    user_address: str | None,
    persona_summary: str | None,
) -> str:
    """Render the final recap shown when onboarding transitions to completed.

    P2-10 of docs/PERSONALITY_PHASE2_PLAN_2026-04-10.md. The recap is the
    single terminal reply for every v2 onboarding path — guided, express,
    freestyle authoring, and the freestyle skip branch.
    """
    address_line = user_address.strip() if user_address else "(no salutation)"
    personality_line = persona_summary or "balanced"
    name = f"`{agent_name}`"
    return (
        "Locked in. Here's the recap:\n\n"
        f"  Agent:        {agent_name}\n"
        f"  Calls you:    {address_line}\n"
        f"  Personality:  {personality_line}\n"
        "  Commitments:  anti-glazing, better-way surfacing, honest failure reporting\n\n"
        f"You can shape {name}'s personality any time:\n"
        "  - `be more direct`, `be warmer`, `slow down`, `be playful`\n"
        "  - `what's my personality` to see current traits\n"
        "  - `reset personality` to go back to balanced\n\n"
        "Recommended later: connect your Spark Swarm agent so Builder can "
        "link the external identity too.\n\n"
        "Ready when you are."
    )


_ONBOARDING_CANCEL_TOKENS: frozenset[str] = frozenset(
    {
        "/cancel",
        "/quit",
        "/stop",
    }
)


def _build_onboarding_cancelled_reply_text() -> str:
    """Render the reply for the P2-11 /cancel escape hatch.

    Q-E of docs/PERSONALITY_ONBOARDING_V2_DESIGN_2026-04-10.md §11:
    /cancel wipes BOTH the in-progress onboarding state AND the
    saved agent name back to the empty-string sentinel. The persona
    profile is left untouched per Q-J default.
    """
    return (
        "Onboarding cancelled. I cleared the agent name and stopped the setup "
        "conversation.\n\n"
        "Your pairing is still active — say `hi` any time to start over. "
        "Any existing personality traits you had before stay as-is."
    )


_REONBOARD_CONSENT_YES_TOKENS: frozenset[str] = frozenset(
    {
        "yes",
        "y",
        "yeah",
        "yep",
        "yup",
        "sure",
        "restart",
        "redo",
        "re-run",
        "rerun",
    }
)


def _build_reonboard_consent_offer_text(agent_name: str) -> str:
    """Render the P2-12 awaiting_reonboard_consent offer card.

    Q-H of docs/PERSONALITY_ONBOARDING_V2_DESIGN_2026-04-10.md §11:
    existing users with a saved persona get a one-tap skip offer
    instead of being silently bypassed. Any reply other than `yes`
    (or a close variant) is treated as "keep things as they are".
    """
    if agent_name:
        name_label = f"`{agent_name}`"
    else:
        name_label = "your agent"
    return (
        f"Want to re-run setup for {name_label}? Your current personality "
        "stays put unless you say `yes`.\n\n"
        "Reply `yes` to start the short setup conversation, or anything "
        "else to keep things as they are."
    )


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
