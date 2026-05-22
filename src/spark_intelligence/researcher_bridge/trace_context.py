from __future__ import annotations


def researcher_bridge_trace_ref(
    *,
    agent_id: str | None,
    human_id: str | None,
    request_id: str | None,
) -> str:
    normalized_request = str(request_id or "").strip() or "unknown"
    return f"trace:{agent_id or 'agent'}:{human_id or 'human'}:{normalized_request}"
