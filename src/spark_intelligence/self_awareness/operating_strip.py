from __future__ import annotations

from dataclasses import dataclass
from typing import Any


AGENT_OPERATING_STRIP_SCHEMA_VERSION = "spark.agent_operating_strip.v1"


@dataclass(frozen=True)
class AgentOperatingStrip:
    status: str
    best_route: str
    access: str
    runner: str
    memory: str
    builder: str
    last_verified: str

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_version": AGENT_OPERATING_STRIP_SCHEMA_VERSION,
            "status": self.status,
            "best_route": self.best_route,
            "access": self.access,
            "runner": self.runner,
            "memory": self.memory,
            "builder": self.builder,
            "last_verified": self.last_verified,
        }

    def to_text(self) -> str:
        return (
            f"AOC: {self.status} | "
            f"Best route: {self.best_route} | "
            f"Access: {self.access} | "
            f"Runner: {self.runner} | "
            f"Memory: {self.memory} | "
            f"Builder: {self.builder} | "
            f"Last verified: {self.last_verified}"
        )


def build_agent_operating_strip(aoc_payload: dict[str, Any]) -> AgentOperatingStrip:
    routes = [_dict(route) for route in _list(aoc_payload.get("routes"))]
    return AgentOperatingStrip(
        status=_display_status(str(aoc_payload.get("status") or "unknown")),
        best_route=str(_dict(aoc_payload.get("task_fit")).get("recommended_route_label") or "unknown"),
        access=str(_dict(aoc_payload.get("access")).get("label") or "unknown"),
        runner=str(_dict(aoc_payload.get("runner")).get("label") or "unknown"),
        memory=_display_status(_route_status(routes, "spark_memory")),
        builder=_display_status(_route_status(routes, "spark_intelligence_builder")),
        last_verified=str(aoc_payload.get("generated_at") or "unknown"),
    )


def _route_status(routes: list[dict[str, Any]], key: str) -> str:
    for route in routes:
        if str(route.get("key") or "") == key:
            return str(route.get("status") or "unknown")
    return "unknown"


def _display_status(value: str) -> str:
    return " ".join(part for part in str(value or "unknown").replace("_", " ").split()).capitalize()


def _dict(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _list(value: object) -> list[object]:
    return list(value) if isinstance(value, list) else []
