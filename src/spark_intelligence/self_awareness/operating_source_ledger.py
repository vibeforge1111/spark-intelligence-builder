from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


AGENT_SOURCE_LEDGER_SCHEMA_VERSION = "spark.agent_source_ledger.v1"
SourceFreshness = Literal["fresh", "stale", "contradicted", "unknown", "live_probed"]


@dataclass(frozen=True)
class AgentSourceLedgerItem:
    source: str
    role: str
    freshness: SourceFreshness
    present: bool
    summary: str = ""
    source_ref: str | None = None

    def to_payload(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "role": self.role,
            "freshness": self.freshness,
            "present": self.present,
            "summary": self.summary,
            "source_ref": self.source_ref,
        }


@dataclass(frozen=True)
class AgentSourceLedger:
    items: list[AgentSourceLedgerItem]
    source_policy: str = (
        "Freshness is display evidence only. Source hierarchy decides conflicts; live state and latest user turn win."
    )

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_version": AGENT_SOURCE_LEDGER_SCHEMA_VERSION,
            "counts": {
                "present": sum(1 for item in self.items if item.present),
                "fresh": sum(1 for item in self.items if item.freshness == "fresh"),
                "stale": sum(1 for item in self.items if item.freshness == "stale"),
                "contradicted": sum(1 for item in self.items if item.freshness == "contradicted"),
                "live_probed": sum(1 for item in self.items if item.freshness == "live_probed"),
                "unknown": sum(1 for item in self.items if item.freshness == "unknown"),
            },
            "items": [item.to_payload() for item in self.items],
            "source_policy": self.source_policy,
        }


def build_agent_source_ledger(
    *,
    aoc_payload: dict[str, Any],
    black_box_payload: dict[str, Any],
    memory_inbox_payload: dict[str, Any],
    stale_sweep_payload: dict[str, Any],
) -> AgentSourceLedger:
    frame = _dict(aoc_payload.get("conversation_frame"))
    access = _dict(aoc_payload.get("access"))
    runner = _dict(aoc_payload.get("runner"))
    route_confidence = _dict(aoc_payload.get("route_confidence"))
    spark_system_map = _dict(aoc_payload.get("spark_system_map"))
    black_box_counts = _dict(black_box_payload.get("counts"))
    memory_counts = _dict(memory_inbox_payload.get("counts"))
    stale_counts = _dict(stale_sweep_payload.get("counts"))
    return AgentSourceLedger(
        items=[
            AgentSourceLedgerItem(
                source="current_user_message",
                role="latest_turn_authority",
                freshness="fresh" if frame.get("latest_user_message_summary") else "unknown",
                present=bool(frame.get("latest_user_message_summary")),
                summary=str(frame.get("latest_user_message_summary") or ""),
                source_ref=str(_dict(frame.get("active_reference_list")).get("source_turn_id") or "") or None,
            ),
            AgentSourceLedgerItem(
                source="operator_supplied_access",
                role="permission_context",
                freshness="fresh" if access.get("spark_access_level") else "unknown",
                present=bool(access.get("spark_access_level")),
                summary=str(access.get("label") or ""),
            ),
            AgentSourceLedgerItem(
                source="runner_preflight",
                role="execution_capability_context",
                freshness="live_probed" if runner.get("writable") is not None else "unknown",
                present=runner.get("writable") is not None,
                summary=str(runner.get("label") or ""),
            ),
            AgentSourceLedgerItem(
                source="route_confidence",
                role="route_selection_evidence",
                freshness="fresh" if route_confidence.get("recommended_route") else "unknown",
                present=bool(route_confidence.get("recommended_route")),
                summary=str(route_confidence.get("confidence") or ""),
            ),
            AgentSourceLedgerItem(
                source="spark_os_system_map",
                role="cross_repo_system_truth_snapshot",
                freshness="fresh" if spark_system_map.get("present") else "unknown",
                present=bool(spark_system_map.get("present")),
                summary=_spark_system_map_summary(spark_system_map),
                source_ref=str(spark_system_map.get("source_ref") or "") or None,
            ),
            AgentSourceLedgerItem(
                source="agent_black_box",
                role="recent_agent_action_trace",
                freshness="fresh" if int(black_box_counts.get("entries") or 0) else "unknown",
                present=bool(int(black_box_counts.get("entries") or 0)),
                summary=f"{int(black_box_counts.get('entries') or 0)} event(s)",
            ),
            AgentSourceLedgerItem(
                source="memory_approval_inbox",
                role="memory_candidate_review_context",
                freshness="fresh" if int(memory_counts.get("pending") or 0) else "unknown",
                present=bool(int(memory_counts.get("pending") or 0)),
                summary=f"{int(memory_counts.get('pending') or 0)} pending",
            ),
            AgentSourceLedgerItem(
                source="stale_context_sweep",
                role="stale_or_contradicted_context",
                freshness=_stale_sweep_freshness(stale_counts),
                present=bool(int(stale_counts.get("stale") or 0) or int(stale_counts.get("contradicted") or 0)),
                summary=(
                    f"{int(stale_counts.get('stale') or 0)} stale, "
                    f"{int(stale_counts.get('contradicted') or 0)} contradicted"
                ),
            ),
        ]
    )


def _stale_sweep_freshness(counts: dict[str, Any]) -> SourceFreshness:
    if int(counts.get("contradicted") or 0):
        return "contradicted"
    if int(counts.get("stale") or 0):
        return "stale"
    return "fresh"


def _spark_system_map_summary(context: dict[str, Any]) -> str:
    if not context.get("present"):
        return "missing"
    counts = _dict(context.get("counts"))
    summary = (
        f"{int(counts.get('modules') or 0)} modules, "
        f"{int(counts.get('repos') or 0)} repos, "
        f"{int(counts.get('gaps') or 0)} gaps"
    )
    memory_rows = int(counts.get("memory_movement_rows") or 0)
    if memory_rows:
        summary += f", memory rows {memory_rows}"
    sample_count = int(counts.get("builder_event_samples") or 0)
    if sample_count:
        summary += f", black-box samples {sample_count}"
    trace_group_count = int(counts.get("builder_trace_groups") or 0)
    if trace_group_count:
        summary += f", trace groups {trace_group_count}"
    trace_health_flags = int(counts.get("trace_health_flags") or 0)
    if trace_health_flags:
        summary += f", trace health flags {trace_health_flags}"
    spawner_trace_refs = int(counts.get("spawner_prd_derived_trace_refs") or 0)
    if spawner_trace_refs:
        summary += f", spawner trace refs {spawner_trace_refs}"
    return summary


def _dict(value: object) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}
