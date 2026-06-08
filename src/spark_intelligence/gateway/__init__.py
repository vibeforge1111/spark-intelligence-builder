from typing import TYPE_CHECKING, Any

from spark_intelligence.gateway.guardrails import (
    apply_inbound_rate_limit,
    is_duplicate_event,
    load_channel_security_policy,
    looks_secret_like,
    prepare_outbound_text,
    set_runtime_state_value,
)

if TYPE_CHECKING:
    from spark_intelligence.gateway.simulated_dm import SimulatedDmBridgeResult, resolve_simulated_dm

__all__ = [
    "apply_inbound_rate_limit",
    "is_duplicate_event",
    "load_channel_security_policy",
    "looks_secret_like",
    "prepare_outbound_text",
    "resolve_simulated_dm",
    "SimulatedDmBridgeResult",
    "set_runtime_state_value",
]


def __getattr__(name: str) -> Any:
    if name in {"resolve_simulated_dm", "SimulatedDmBridgeResult"}:
        from spark_intelligence.gateway.simulated_dm import SimulatedDmBridgeResult, resolve_simulated_dm

        exports = {
            "resolve_simulated_dm": resolve_simulated_dm,
            "SimulatedDmBridgeResult": SimulatedDmBridgeResult,
        }
        return exports[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
