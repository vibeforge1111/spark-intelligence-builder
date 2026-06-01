from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal


MutationClass = Literal[
    "none",
    "read_only",
    "writes_memory",
    "writes_files",
    "launches_mission",
    "creates_schedule",
    "deletes_schedule",
    "creates_chip",
    "publishes",
    "external_network",
]


def _harness_core_source_candidates() -> list[Path]:
    candidates: list[Path] = []
    configured = os.environ.get("SPARK_HARNESS_CORE_SOURCE")
    if configured:
        source = Path(configured).expanduser()
        candidates.append(source / "src" if source.name != "src" else source)
    spark_home = Path(os.environ.get("SPARK_HOME", Path.home() / ".spark")).expanduser()
    candidates.append(spark_home / "modules" / "spark-harness-core" / "source" / "src")
    here = Path(__file__).resolve()
    for parent in here.parents:
        if parent.name == "modules":
            candidates.append(parent / "spark-harness-core" / "source" / "src")
            break
    return candidates


def _ensure_harness_core_importable() -> None:
    for candidate in _harness_core_source_candidates():
        if not candidate.exists():
            continue
        raw = str(candidate)
        if raw not in sys.path:
            sys.path.insert(0, raw)


_ensure_harness_core_importable()


try:
    from spark_harness_core.legacy_turn_intent import (
        HarnessDirective,
        HarnessExecutionPolicy,
        HarnessSelectedIntent,
        HarnessSessionScope,
        HarnessToolPolicy,
        LegacyToolAuthorization,
        TurnIntentEnvelope,
        authorize_legacy_tool_call,
        authorize_tool_call,
        parse_turn_intent_envelope,
    )

    HARNESS_CORE_AVAILABLE = True
    HARNESS_CORE_IMPORT_ERROR = ""
except Exception as exc:  # pragma: no cover - exercised only when the core package is absent.
    HARNESS_CORE_AVAILABLE = False
    HARNESS_CORE_IMPORT_ERROR = str(exc)

    @dataclass(frozen=True)
    class LegacyToolAuthorization:
        verdict: Literal["allowed", "blocked"]
        reason_codes: tuple[str, ...]
        turn_intent_envelope_vnext: dict[str, Any] | None = None
        proposed_action: dict[str, Any] | None = None
        authorization_decision: dict[str, Any] | None = None

    TurnIntentEnvelope = Any
    HarnessDirective = Any
    HarnessSelectedIntent = Any
    HarnessSessionScope = Any
    HarnessToolPolicy = Any
    HarnessExecutionPolicy = Any

    def parse_turn_intent_envelope(payload: dict[str, Any]) -> Any:
        raise ValueError(f"Spark Harness Core unavailable: {HARNESS_CORE_IMPORT_ERROR}")

    def authorize_legacy_tool_call(
        envelope: Any | None,
        *,
        tool_name: str,
        owner_system: str,
        mutation_class: MutationClass,
        publishes: bool = False,
        external_network: bool = False,
    ) -> LegacyToolAuthorization:
        return LegacyToolAuthorization("blocked", ("spark_harness_core_unavailable",))

    def authorize_tool_call(
        envelope: Any | None,
        *,
        tool_name: str,
        owner_system: str,
        mutation_class: MutationClass,
        publishes: bool = False,
        external_network: bool = False,
    ) -> tuple[Literal["allowed", "blocked"], tuple[str, ...]]:
        authorization = authorize_legacy_tool_call(
            envelope,
            tool_name=tool_name,
            owner_system=owner_system,
            mutation_class=mutation_class,
            publishes=publishes,
            external_network=external_network,
        )
        return authorization.verdict, authorization.reason_codes


__all__ = [
    "HARNESS_CORE_AVAILABLE",
    "HARNESS_CORE_IMPORT_ERROR",
    "HarnessDirective",
    "HarnessExecutionPolicy",
    "HarnessSelectedIntent",
    "HarnessSessionScope",
    "HarnessToolPolicy",
    "LegacyToolAuthorization",
    "MutationClass",
    "TurnIntentEnvelope",
    "authorize_legacy_tool_call",
    "authorize_tool_call",
    "parse_turn_intent_envelope",
]
