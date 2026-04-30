from __future__ import annotations

import re
from hashlib import sha1
from dataclasses import asdict, dataclass, field
from typing import Any

from spark_intelligence.creator.contracts import CREATOR_INTENT_SCHEMA_VERSION


SCHEMA_VERSION = CREATOR_INTENT_SCHEMA_VERSION


_STOPWORDS = {
    "a",
    "an",
    "and",
    "around",
    "as",
    "at",
    "benchmark",
    "benchmarked",
    "build",
    "create",
    "creator",
    "domain",
    "for",
    "from",
    "in",
    "into",
    "loop",
    "make",
    "me",
    "of",
    "on",
    "path",
    "please",
    "specialization",
    "specialisation",
    "system",
    "that",
    "the",
    "this",
    "to",
    "with",
}


@dataclass(frozen=True)
class CreatorIntentPacket:
    schema_version: str
    user_goal: str
    target_domain: str
    target_operator_surface: str
    expected_agent_capability: str
    success_examples: list[str] = field(default_factory=list)
    failure_examples: list[str] = field(default_factory=list)
    tools_in_scope: list[str] = field(default_factory=list)
    data_sources_allowed: list[str] = field(default_factory=list)
    risk_level: str = "low"
    privacy_mode: str = "local_only"
    desired_outputs: dict[str, bool] = field(default_factory=dict)
    intent_id: str = ""
    artifact_targets: list[str] = field(default_factory=list)
    usage_surfaces: list[str] = field(default_factory=list)
    success_claim: str = ""
    capabilities_to_prove: list[str] = field(default_factory=list)
    benchmark_requirements: dict[str, bool | int] = field(default_factory=dict)
    network_contribution_policy: str = "workspace_only"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_text(self) -> str:
        outputs = [
            key.replace("_", " ")
            for key, enabled in self.desired_outputs.items()
            if enabled
        ]
        lines = [
            "Creator intent packet",
            f"Domain: {self.target_domain}",
            f"Surface: {self.target_operator_surface}",
            f"Privacy: {self.privacy_mode}",
            f"Risk: {self.risk_level}",
            f"Intent: {self.intent_id}",
            "Build: " + (", ".join(outputs) if outputs else "none"),
            "Artifacts: " + (", ".join(self.artifact_targets) if self.artifact_targets else "none"),
            f"Capability: {self.expected_agent_capability}",
        ]
        if self.tools_in_scope:
            lines.append("Tools: " + ", ".join(self.tools_in_scope))
        if self.data_sources_allowed:
            lines.append("Data sources: " + ", ".join(self.data_sources_allowed))
        return "\n".join(lines)


def build_creator_intent_packet(
    brief: str,
    *,
    privacy_mode: str | None = None,
    risk_level: str | None = None,
) -> CreatorIntentPacket:
    clean = _compact(brief)
    lower = clean.lower()
    desired_outputs = _infer_desired_outputs(lower)
    target_domain = _infer_domain(lower)
    tools = _infer_tools(lower)
    inferred_privacy = privacy_mode or _infer_privacy_mode(lower)
    inferred_risk = risk_level or _infer_risk_level(lower, inferred_privacy, desired_outputs)
    surfaces = _operator_surface(tools, desired_outputs)
    data_sources = _infer_data_sources(lower, inferred_privacy)
    capability = _expected_capability(target_domain, desired_outputs)
    usage_surfaces = _usage_surfaces(surfaces)
    artifact_targets = _artifact_targets(desired_outputs)

    return CreatorIntentPacket(
        schema_version=SCHEMA_VERSION,
        user_goal=clean,
        target_domain=target_domain,
        target_operator_surface=surfaces,
        expected_agent_capability=capability,
        success_examples=_success_examples(target_domain, desired_outputs),
        failure_examples=_failure_examples(desired_outputs),
        tools_in_scope=tools,
        data_sources_allowed=data_sources,
        risk_level=inferred_risk,
        privacy_mode=inferred_privacy,
        desired_outputs=desired_outputs,
        intent_id=_intent_id(clean, target_domain),
        artifact_targets=artifact_targets,
        usage_surfaces=usage_surfaces,
        success_claim=capability,
        capabilities_to_prove=_capabilities_to_prove(target_domain, desired_outputs),
        benchmark_requirements=_benchmark_requirements(desired_outputs, inferred_privacy),
        network_contribution_policy=_network_contribution_policy(inferred_privacy),
    )


def _compact(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip())


def _slug(text: str) -> str:
    parts = re.findall(r"[a-z0-9]+", text.lower())
    useful = [p for p in parts if p not in _STOPWORDS]
    return "-".join(useful[:6]) or "custom-domain"


def _intent_id(clean: str, target_domain: str) -> str:
    digest = sha1(clean.encode("utf-8")).hexdigest()[:8]
    return f"creator-intent-{target_domain}-{digest}"


def _infer_domain(lower: str) -> str:
    known = {
        "startup yc": "startup-yc",
        "yc startup": "startup-yc",
        "spark telegram bot": "spark-telegram-bot",
        "telegram bot": "telegram-bot",
        "spark intelligence builder": "spark-intelligence-builder",
        "spawner ui": "spawner-ui",
        "founder arena": "founder-arena",
        "startup bench": "startup-bench",
    }
    for phrase, domain in known.items():
        if phrase in lower:
            return domain

    match = re.search(
        r"\b(?:for|around|about|on)\s+([a-z0-9][a-z0-9 _/-]{2,80}?)(?:\s+(?:that|with|which|so|to|and|from)\b|[,.]|$)",
        lower,
    )
    if match:
        return _slug(match.group(1))
    return _slug(lower)


def _has_any(lower: str, words: tuple[str, ...]) -> bool:
    return any(w in lower for w in words)


def _infer_desired_outputs(lower: str) -> dict[str, bool]:
    wants_full_path = _has_any(
        lower,
        (
            "full path",
            "mastery",
            "super intelligent",
            "superintelligent",
            "specialization path",
            "specialisation path",
            "spark swarm",
        ),
    )
    wants_chip = wants_full_path or _has_any(
        lower,
        ("domain chip", "chip", "operator", "tool usage", "good at", "make spark"),
    )
    wants_path = wants_full_path or _has_any(lower, ("specialization", "specialisation", "learning path"))
    wants_autoloop = wants_full_path or _has_any(
        lower,
        ("autoloop", "auto loop", "recursive", "self-improve", "self improve", "mutation"),
    )
    wants_telegram = _has_any(lower, ("telegram", "bot", "chat"))
    wants_spawner = _has_any(lower, ("spawner", "canvas", "kanban", "mission", "trackable"))

    return {
        "domain_chip": wants_chip or not (wants_path or wants_autoloop),
        "specialization_path": wants_path,
        "benchmark_pack": True,
        "autoloop_policy": wants_autoloop,
        "telegram_flow": wants_telegram,
        "spawner_mission": wants_spawner,
        "swarm_publish_packet": wants_full_path or _has_any(lower, ("swarm", "network", "share")),
    }


def _infer_tools(lower: str) -> list[str]:
    tool_map = [
        ("spark_telegram_bot", ("telegram", "bot")),
        ("spark_intelligence_builder", ("builder", "runtime")),
        ("spawner_ui", ("spawner", "mission")),
        ("spark_canvas", ("canvas",)),
        ("kanban", ("kanban", "board")),
        ("spark_swarm", ("swarm", "collective", "network")),
        ("github", ("github", "pr", "pull request", "repo")),
        ("startup_bench", ("startup bench", "startup-bench")),
        ("founder_arena", ("founder arena", "founder-arena")),
    ]
    tools: list[str] = []
    for tool, needles in tool_map:
        if any(n in lower for n in needles):
            tools.append(tool)
    return tools


def _infer_privacy_mode(lower: str) -> str:
    if _has_any(lower, ("private", "local only", "local-only", "do not share", "don't share")):
        return "local_only"
    if _has_any(lower, ("spark swarm", "swarm", "network", "collective", "share with other agents")):
        return "swarm_shared"
    if _has_any(lower, ("github", "pull request", " pr ", "repo")):
        return "github_pr"
    return "local_only"


def _infer_risk_level(
    lower: str,
    privacy_mode: str,
    desired_outputs: dict[str, bool],
) -> str:
    if _has_any(lower, ("secret", "token", "auth", "security", "money", "finance", "trading", "production")):
        return "high"
    if privacy_mode != "local_only" or desired_outputs.get("autoloop_policy"):
        return "medium"
    return "low"


def _operator_surface(tools: list[str], desired_outputs: dict[str, bool]) -> str:
    surfaces: list[str] = []
    if "spark_telegram_bot" in tools or desired_outputs.get("telegram_flow"):
        surfaces.append("telegram")
    surfaces.append("builder")
    if "spawner_ui" in tools or "spark_canvas" in tools or "kanban" in tools or desired_outputs.get("spawner_mission"):
        surfaces.append("spawner")
    if "spark_swarm" in tools or desired_outputs.get("swarm_publish_packet"):
        surfaces.append("swarm")
    return "+".join(dict.fromkeys(surfaces))


def _usage_surfaces(target_operator_surface: str) -> list[str]:
    return [surface for surface in target_operator_surface.split("+") if surface]


def _artifact_targets(desired_outputs: dict[str, bool]) -> list[str]:
    targets: list[str] = []
    for key in ("domain_chip", "benchmark_pack", "specialization_path", "autoloop_policy"):
        if desired_outputs.get(key):
            targets.append(key)
    if desired_outputs.get("telegram_flow") or desired_outputs.get("spawner_mission"):
        targets.append("tool_integration")
    if desired_outputs.get("swarm_publish_packet"):
        targets.append("swarm_publish_packet")
    return targets


def _infer_data_sources(lower: str, privacy_mode: str) -> list[str]:
    sources = ["local_repo"]
    if _has_any(lower, ("github", "repo", "pull request", " pr ")):
        sources.append("github")
    if _has_any(lower, ("web", "research", "sources", "internet")):
        sources.append("web_research")
    if privacy_mode == "swarm_shared":
        sources.append("spark_swarm")
    return sources


def _expected_capability(target_domain: str, desired_outputs: dict[str, bool]) -> str:
    parts = [f"Improve Spark's measurable capability in {target_domain}"]
    if desired_outputs.get("domain_chip"):
        parts.append("through a domain chip")
    if desired_outputs.get("benchmark_pack"):
        parts.append("validated by benchmarks")
    if desired_outputs.get("autoloop_policy"):
        parts.append("with bounded recursive improvement")
    if desired_outputs.get("swarm_publish_packet"):
        parts.append("and Swarm-shareable mastery packets")
    return " ".join(parts) + "."


def _capabilities_to_prove(target_domain: str, desired_outputs: dict[str, bool]) -> list[str]:
    if target_domain == "startup-yc":
        capabilities = [
            "prioritize retention proof over shallow acquisition",
            "detect default-dead risk",
            "choose narrow design partners",
            "avoid premature hiring before product-market fit",
        ]
    else:
        capabilities = [
            f"perform realistic {target_domain} tasks better than baseline",
            f"explain {target_domain} decisions with evidence and limits",
        ]
    if desired_outputs.get("autoloop_policy"):
        capabilities.append("keep or reject recursive mutations with benchmark evidence")
    if desired_outputs.get("swarm_publish_packet"):
        capabilities.append("package reusable lessons with provenance for Swarm review")
    return capabilities


def _benchmark_requirements(
    desired_outputs: dict[str, bool],
    privacy_mode: str,
) -> dict[str, bool | int]:
    wants_mastery = desired_outputs.get("specialization_path") or desired_outputs.get("swarm_publish_packet")
    return {
        "visible_cases": 20 if wants_mastery else 5,
        "fixed_suite": True,
        "held_out_cases": bool(wants_mastery),
        "trap_cases": True,
        "simulator_transfer": bool(wants_mastery),
        "fresh_agent_absorption": bool(desired_outputs.get("specialization_path") or privacy_mode == "swarm_shared"),
        "human_calibration": False,
    }


def _network_contribution_policy(privacy_mode: str) -> str:
    if privacy_mode in {"github_pr", "swarm_shared"}:
        return "github_pr_required"
    return "workspace_only"


def _success_examples(target_domain: str, desired_outputs: dict[str, bool]) -> list[str]:
    examples = [
        f"Agent performs a realistic {target_domain} task better than baseline.",
        "Benchmark report shows component scores, delta, and held-out result.",
    ]
    if desired_outputs.get("autoloop_policy"):
        examples.append("One loop round can keep or revert a candidate with a recorded reason.")
    if desired_outputs.get("swarm_publish_packet"):
        examples.append("Validated insight packet can be synced or staged for Spark Swarm.")
    return examples


def _failure_examples(desired_outputs: dict[str, bool]) -> list[str]:
    examples = [
        "Score improves only because of formatting or confidence inflation.",
        "Router keywords hijack unrelated conversations.",
        "Benchmark has no adversarial or held-out cases.",
    ]
    if desired_outputs.get("autoloop_policy"):
        examples.append("Autoloop mutates files outside its declared mutation surface.")
    if desired_outputs.get("swarm_publish_packet"):
        examples.append("Raw operational residue is published as durable intelligence.")
    return examples
