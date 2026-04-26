from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

from spark_intelligence.channel.service import add_channel
from spark_intelligence.cli import main
from spark_intelligence.config.loader import ConfigManager
from spark_intelligence.state.db import StateDB


def make_telegram_update(
    *,
    update_id: int,
    user_id: str,
    text: str | None = None,
    username: str | None = None,
    chat_id: str | None = None,
    chat_type: str = "private",
    voice: dict[str, object] | None = None,
    audio: dict[str, object] | None = None,
) -> dict[str, object]:
    resolved_chat_id = chat_id or user_id
    message_payload: dict[str, object] = {
        "message_id": update_id * 10,
        "chat": {
            "id": resolved_chat_id,
            "type": chat_type,
        },
        "from": {
            "id": user_id,
            "username": username,
        },
    }
    if text is not None:
        message_payload["text"] = text
    if voice is not None:
        message_payload["voice"] = voice
    if audio is not None:
        message_payload["audio"] = audio
    return {
        "update_id": update_id,
        "message": message_payload,
    }


def create_fake_hook_chip(root: Path, *, chip_key: str = "startup-yc") -> Path:
    repo_root = root / f"domain-chip-{chip_key}"
    package_root = repo_root / "src" / "fake_startup_chip"
    package_root.mkdir(parents=True, exist_ok=True)
    (package_root / "__init__.py").write_text("", encoding="utf-8")
    (package_root / "chip_hooks.py").write_text(
        """
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "hook",
        choices=[
            "evaluate",
            "suggest",
            "packets",
            "watchtower",
            "identity",
            "personality",
            "browser.status",
            "browser.navigate",
            "browser.tab.wait",
            "browser.page.dom_extract",
            "browser.page.text_extract",
            "browser.page.snapshot",
        ],
    )
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    if args.hook == "evaluate":
        situation = payload.get("situation", "")
        result = {
            "returncode": 0,
            "stdout": "task_type: diagnostic_questioning\\nstage: pmf_search\\ncontext_packets: 2\\nactivations: 1\\nhas_analysis: True",
            "stderr": "",
            "metrics": {"context_packet_count": 2, "activation_count": 1, "task_type": "diagnostic_questioning"},
            "result": {
                "analysis": f"Startup YC doctrine: focus on the narrowest urgent founder pain first. Situation: {situation}",
                "task_type": "diagnostic_questioning",
                "stage": "pmf_search",
                "context_packet_ids": ["packet-1", "packet-2"],
                "activations": [{"name": "focus_gap"}],
                "detected_state_updates": [{"field": "stage", "value": "pmf_search"}],
                "stage_transition_suggested": None,
            },
        }
    elif args.hook == "packets":
        packet_bundle = payload.get("packet_bundle", {})
        packets = packet_bundle.get("packets", [])
        packet_kinds = sorted({str(packet.get("packet_kind") or "unknown") for packet in packets})
        result = {
            "returncode": 0,
            "stdout": f"packet_count: {len(packets)}\\npacket_kinds: {','.join(packet_kinds)}",
            "stderr": "",
            "metrics": {"packet_count": len(packets), "packet_kind_count": len(packet_kinds)},
            "result": {
                "packet_count": len(packets),
                "packet_kinds": packet_kinds,
                "analysis": "Observer doctrine: convert bounded packet evidence into diagnosis and repair workstreams.",
                "recommended_actions": [
                    "review latest incident_report packets",
                    "prioritize repair_plan items with high-severity provenance drift",
                ],
            },
        }
    elif args.hook == "identity":
        human_id = str(payload.get("human_id") or "human:unknown")
        current_identity = payload.get("current_identity") or {}
        current_name = str(current_identity.get("agent_name") or "Spark Agent").strip() or "Spark Agent"
        suffix = human_id.split(":")[-1]
        result = {
            "returncode": 0,
            "stdout": f"swarm_agent_id: swarm-agent:{suffix}\\nagent_name: {current_name}",
            "stderr": "",
            "metrics": {"session_count": len(payload.get("sessions") or []), "pairing_count": len(payload.get("pairings") or [])},
            "result": {
                "human_id": human_id,
                "external_system": "spark_swarm",
                "swarm_agent_id": f"swarm-agent:{suffix}",
                "agent_name": current_name,
                "confirmed_at": "2026-03-28T12:00:00+00:00",
                "metadata": {
                    "workspace_id": payload.get("workspace_id"),
                    "source": "fake_swarm_runtime",
                },
            },
        }
    elif args.hook == "personality":
        human_id = str(payload.get("human_id") or "human:unknown")
        agent_id = str(payload.get("agent_id") or "agent:unknown")
        current_identity = payload.get("identity") or {}
        current_name = str(current_identity.get("agent_name") or "Founder Operator").strip() or "Founder Operator"
        result = {
            "returncode": 0,
            "stdout": f"persona_name: {current_name}\\nagent_id: {agent_id}",
            "stderr": "",
            "metrics": {"observation_count": len(payload.get("recent_observations") or []), "evolution_count": len(payload.get("recent_evolutions") or [])},
            "result": {
                "human_id": human_id,
                "agent_id": agent_id,
                "persona_name": current_name,
                "persona_summary": "Direct, calm, low-fluff, strategic.",
                "base_traits": {
                    "warmth": 0.46,
                    "directness": 0.82,
                    "playfulness": 0.18,
                    "pacing": 0.63,
                    "assertiveness": 0.79,
                },
                "behavioral_rules": [
                    "Prefer clear decisions over open-ended brainstorming unless asked.",
                    "Avoid emotional padding.",
                    "Push toward execution.",
                ],
                "evolver_state": {
                    "traits": {
                        "warmth": 0.46,
                        "directness": 0.82,
                        "playfulness": 0.18,
                        "pacing": 0.63,
                        "assertiveness": 0.79,
                    },
                    "last_signals": {
                        "personality_id": "founder_operator",
                        "personality_name": current_name,
                    },
                },
            },
        }
    elif args.hook == "browser.status":
        target = payload.get("target") or {}
        result = {
            "status": "succeeded",
            "risk_class": payload.get("risk_class") or "read_only",
            "approval_state": "not_required",
            "result": {
                "extension": {
                    "installed": True,
                    "running": True,
                    "version": "0.1.0",
                },
                "browser": {
                    "family": target.get("browser_family") or "brave",
                    "page_scope": "active_tab_only",
                },
                "profile": {
                    "key": target.get("profile_key") or "spark-default",
                    "mode": target.get("profile_mode") or "dedicated",
                },
                "native_host": {
                    "connectivity": "connected",
                    "supported": True,
                    "version": "0.1.0",
                },
                "runtime_mode": os.environ.get("SPARK_BROWSER_ATTACHMENT_MODE"),
            },
            "artifacts": [],
            "provenance": {
                "browser_family": target.get("browser_family") or "brave",
                "profile_key": target.get("profile_key") or "spark-default",
                "extension_version": "0.1.0",
                "native_host_version": "0.1.0",
                "executed_at": "2026-03-29T12:00:00.000Z",
                "origin": target.get("origin"),
                "tab_id": target.get("tab_id"),
            },
            "policy_flags": {
                "sensitive_domain": False,
                "redacted_fields": [],
                "quarantine_recommended": False,
            },
            "error": None,
        }
    elif args.hook == "browser.navigate":
        arguments = payload.get("arguments") or {}
        requested_url = str(arguments.get("url") or "https://duckduckgo.com/?q=spark").strip()
        is_source = "coingecko.com" in requested_url
        tab_id = "tab-source-1" if is_source else "tab-search-1"
        result = {
            "status": "succeeded",
            "risk_class": payload.get("risk_class") or "read_only",
            "approval_state": "not_required",
            "result": {
                "url": requested_url,
                "origin": requested_url,
                "disposition": arguments.get("disposition") or "new_background_tab",
                "tab": {
                    "id": tab_id,
                    "active": False,
                    "status": "complete",
                    "title": "BTC price search" if not is_source else "Bitcoin price today | CoinGecko",
                },
                "wait_hint": {
                    "available": True,
                    "named_action": "browser.tab.wait",
                    "approval_mode": "not_required",
                    "target": {
                        "origin": requested_url,
                        "tab_id": tab_id,
                    },
                    "arguments": {
                        "wait_until": "complete",
                        "timeout_ms": 2000,
                        "poll_interval_ms": 50,
                    },
                },
                "activation_hint": None,
                "operator_surface": {
                    "named_action": "browser.navigate",
                    "avoids_screen_hijack": True,
                },
            },
            "artifacts": [],
            "provenance": {
                "browser_family": "brave",
                "profile_key": "spark-default",
                "extension_version": "0.1.0",
                "native_host_version": "0.1.0",
                "executed_at": "2026-03-29T12:00:00.000Z",
                "origin": requested_url,
                "tab_id": tab_id,
            },
            "policy_flags": {
                "sensitive_domain": False,
                "redacted_fields": [],
                "quarantine_recommended": False,
            },
            "error": None,
        }
    elif args.hook == "browser.tab.wait":
        target = payload.get("target") or {}
        result = {
            "status": "succeeded",
            "risk_class": payload.get("risk_class") or "read_only",
            "approval_state": "not_required",
            "result": {
                "origin": target.get("origin") or "https://duckduckgo.com/?q=btc",
                "tab": {
                    "id": target.get("tab_id") or "tab-search-1",
                    "active": False,
                    "status": "complete",
                },
                "wait_status": {
                    "target_status": "complete",
                    "observed_status": "complete",
                    "condition_met": True,
                    "timed_out": False,
                    "waited_ms": 120,
                    "poll_attempts": 2,
                },
                "continuation_hint": None,
                "wait_surface": {
                    "named_action": "browser.tab.wait",
                    "bounded_capture": True,
                    "execution_performed": False,
                    "waited_for_known_tab": True,
                },
            },
            "artifacts": [],
            "provenance": {
                "browser_family": "brave",
                "profile_key": "spark-default",
                "extension_version": "0.1.0",
                "native_host_version": "0.1.0",
                "executed_at": "2026-03-29T12:00:00.000Z",
                "origin": target.get("origin"),
                "tab_id": target.get("tab_id"),
            },
            "policy_flags": {
                "sensitive_domain": False,
                "redacted_fields": [],
                "quarantine_recommended": False,
            },
            "error": None,
        }
    elif args.hook == "browser.page.dom_extract":
        target = payload.get("target") or {}
        origin = str(target.get("origin") or "https://duckduckgo.com/?q=btc")
        if "duckduckgo.com" in origin:
            title = "BTC price search at DuckDuckGo"
            nodes = [
                {
                    "tag": "a",
                    "role": "link",
                    "text_summary": "Bitcoin price today, BTC to USD live price, market cap and chart | CoinGecko",
                    "href": "https://www.coingecko.com/en/coins/bitcoin",
                },
                {
                    "tag": "a",
                    "role": "link",
                    "text_summary": "Bitcoin USD price live | CoinMarketCap",
                    "href": "https://coinmarketcap.com/currencies/bitcoin/",
                },
            ]
        else:
            title = "Bitcoin price today | CoinGecko"
            nodes = [
                {
                    "tag": "p",
                    "role": None,
                    "text_summary": "Bitcoin price today is $84,321.18 with a 24-hour trading volume of $31B.",
                    "href": None,
                }
            ]
        result = {
            "status": "succeeded",
            "risk_class": payload.get("risk_class") or "read_only",
            "approval_state": "not_required",
            "result": {
                "title": title,
                "origin": origin,
                "headings": [{"level": 1, "text": title}],
                "landmarks": [{"role": "main", "label": "Main content"}],
                "dom_outline": {
                    "nodes": nodes,
                },
                "sensitive_surface_hints": {
                    "likely_sensitive_domain": False,
                },
                "extraction_surface": {
                    "named_action": "browser.page.dom_extract",
                    "bounded_capture": True,
                    "execution_performed": False,
                },
            },
            "artifacts": [],
            "provenance": {
                "browser_family": "brave",
                "profile_key": "spark-default",
                "extension_version": "0.1.0",
                "native_host_version": "0.1.0",
                "executed_at": "2026-03-29T12:00:00.000Z",
                "origin": origin,
                "tab_id": target.get("tab_id") or "tab-search-1",
            },
            "policy_flags": {
                "sensitive_domain": False,
                "redacted_fields": [],
                "quarantine_recommended": False,
            },
            "error": None,
        }
    elif args.hook == "browser.page.text_extract":
        target = payload.get("target") or {}
        origin = str(target.get("origin") or "https://www.coingecko.com/en/coins/bitcoin")
        result = {
            "status": "succeeded",
            "risk_class": payload.get("risk_class") or "read_only",
            "approval_state": "not_required",
            "result": {
                "title": "Bitcoin price today | CoinGecko",
                "origin": origin,
                "visible_text": {
                    "summary": "Bitcoin price today is $84,321.18 USD according to the CoinGecko Bitcoin page.",
                    "excerpt": "CoinGecko lists the live BTC to USD price and market summary.",
                    "character_count": 112,
                    "truncated": False,
                    "redacted": False,
                },
                "page_classification_hints": {
                    "content_type": "reference",
                },
                "sensitive_surface_hints": {
                    "likely_sensitive_domain": False,
                },
                "provenance_fields": {
                    "bounded_capture": True,
                    "text_only": True,
                },
            },
            "artifacts": [],
            "provenance": {
                "browser_family": "brave",
                "profile_key": "spark-default",
                "extension_version": "0.1.0",
                "native_host_version": "0.1.0",
                "executed_at": "2026-03-29T12:00:00.000Z",
                "origin": origin,
                "tab_id": target.get("tab_id") or "tab-source-1",
            },
            "policy_flags": {
                "sensitive_domain": False,
                "redacted_fields": [],
                "quarantine_recommended": False,
            },
            "error": None,
        }
    elif args.hook == "browser.page.snapshot":
        target = payload.get("target") or {}
        policy_context = payload.get("policy_context") or {}
        sensitive_domain = bool(policy_context.get("sensitive_domain"))
        result = {
            "status": "succeeded",
            "risk_class": payload.get("risk_class") or "read_only",
            "approval_state": "not_required",
            "result": {
                "title": "Spark Browser Guide",
                "origin": target.get("origin") or "https://docs.example.com/guide",
                "visible_text": {
                    "summary": "[redacted]" if sensitive_domain else "Governed browser hooks keep the browsing layer bounded and auditable.",
                    "excerpt": "[redacted]" if sensitive_domain else "Bounded page capture for Spark Builder.",
                    "redacted": sensitive_domain,
                },
                "forms_summary": {
                    "form_count": 1,
                },
                "important_controls": [
                    {"kind": "link", "label": "Docs"},
                    {"kind": "button", "label": "Continue"},
                ],
                "sensitive_surface_hints": {
                    "likely_sensitive_domain": sensitive_domain,
                },
            },
            "artifacts": [
                {
                    "type": "page_snapshot",
                    "path": "artifacts/browser/page_snapshot.json",
                }
            ],
            "provenance": {
                "browser_family": target.get("browser_family") or "brave",
                "profile_key": target.get("profile_key") or "spark-default",
                "extension_version": "0.1.0",
                "native_host_version": "0.1.0",
                "executed_at": "2026-03-29T12:00:00.000Z",
                "origin": target.get("origin"),
                "tab_id": target.get("tab_id"),
            },
            "policy_flags": {
                "sensitive_domain": sensitive_domain,
                "redacted_fields": [
                    "result.visible_text.summary",
                    "result.visible_text.excerpt",
                ] if sensitive_domain else [],
                "quarantine_recommended": sensitive_domain,
            },
            "error": None,
        }
    else:
        result = {"result": {}}

    Path(args.output).write_text(json.dumps(result, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
        """.strip(),
        encoding="utf-8",
    )
    (repo_root / "spark-chip.json").write_text(
        json.dumps(
            {
                "schema_version": "spark-chip.v1",
                "io_protocol": "spark-hook-io.v1",
                "chip_name": chip_key,
                "domain": "startup-advisory",
                "description": "Fake startup chip for hook tests.",
                "capabilities": [
                    "evaluate",
                    "suggest",
                    "packets",
                    "watchtower",
                    "identity",
                    "personality",
                    "browser.status",
                    "browser.navigate",
                    "browser.tab.wait",
                    "browser.page.dom_extract",
                    "browser.page.text_extract",
                    "browser.page.snapshot",
                ],
                "task_topics": ["startup", "founder"] if chip_key == "startup-yc" else [],
                "task_keywords": ["startup", "focus", "founder"] if chip_key == "startup-yc" else [],
                "commands": {
                    "evaluate": ["python", "-m", "fake_startup_chip.chip_hooks", "evaluate"],
                    "suggest": ["python", "-m", "fake_startup_chip.chip_hooks", "suggest"],
                    "packets": ["python", "-m", "fake_startup_chip.chip_hooks", "packets"],
                    "watchtower": ["python", "-m", "fake_startup_chip.chip_hooks", "watchtower"],
                    "identity": ["python", "-m", "fake_startup_chip.chip_hooks", "identity"],
                    "personality": ["python", "-m", "fake_startup_chip.chip_hooks", "personality"],
                    "browser.status": ["python", "-m", "fake_startup_chip.chip_hooks", "browser.status"],
                    "browser.navigate": ["python", "-m", "fake_startup_chip.chip_hooks", "browser.navigate"],
                    "browser.tab.wait": ["python", "-m", "fake_startup_chip.chip_hooks", "browser.tab.wait"],
                    "browser.page.dom_extract": ["python", "-m", "fake_startup_chip.chip_hooks", "browser.page.dom_extract"],
                    "browser.page.text_extract": ["python", "-m", "fake_startup_chip.chip_hooks", "browser.page.text_extract"],
                    "browser.page.snapshot": ["python", "-m", "fake_startup_chip.chip_hooks", "browser.page.snapshot"],
                },
                "frontier": (
                    {
                        "runtime_family": "browser-capability",
                        "runtime_mode": "governed_adapter",
                    }
                    if chip_key == "spark-browser"
                    else None
                ),
                "onboarding": _default_fake_chip_onboarding(chip_key),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return repo_root


def _default_fake_chip_onboarding(chip_key: str) -> dict[str, object]:
    if chip_key == "spark-browser":
        return {
            "role": "Governed browser and search chip for web inspection and source capture.",
            "surfaces": ["researcher_bridge", "cli", "telegram"],
            "permissions": ["browser_session", "origin_access"],
            "harnesses": ["browser.grounded"],
            "health_checks": ["browser.status"],
            "example_intents": [
                "Open a page and inspect it.",
                "Search the web and summarize findings.",
            ],
            "limitations": ["Requires a live browser session and host access for the target origin."],
        }
    if chip_key == "domain-chip-voice-comms":
        return {
            "role": "Speech I/O chip for transcription and spoken replies.",
            "surfaces": ["telegram", "cli", "researcher_bridge"],
            "permissions": ["audio_io", "tts_provider"],
            "harnesses": ["voice.io"],
            "health_checks": ["voice.status"],
            "example_intents": [
                "Reply in voice.",
                "Transcribe this voice note.",
            ],
            "limitations": ["Quality depends on STT/TTS provider health and channel delivery behavior."],
        }
    if chip_key == "spark-swarm":
        return {
            "role": "Collective-execution chip for Swarm escalation and multi-agent coordination.",
            "surfaces": ["researcher_bridge", "cli"],
            "permissions": ["swarm_api"],
            "harnesses": ["swarm.escalation"],
            "health_checks": ["identity", "watchtower"],
            "example_intents": [
                "Escalate this to Swarm.",
                "Coordinate parallel work across agents.",
            ],
            "limitations": ["Requires Swarm payload readiness and valid auth."],
        }
    if chip_key == "startup-yc":
        return {
            "role": "Doctrine chip for founder/operator guidance and startup diagnosis.",
            "surfaces": ["researcher_bridge"],
            "permissions": ["advisory_only"],
            "harnesses": ["builder.direct", "researcher.advisory"],
            "health_checks": ["evaluate"],
            "example_intents": [
                "Diagnose the startup bottleneck.",
                "Pressure-test the go-to-market wedge.",
            ],
            "limitations": ["Guidance-oriented only; it does not execute external actions itself."],
        }
    return {
        "role": f"{chip_key} attached capability contract.",
        "surfaces": ["cli"],
        "permissions": ["hook_execution"],
        "health_checks": ["evaluate"],
        "example_intents": [f"Use {chip_key} for its attached capability."],
    }


def create_fake_researcher_runtime(root: Path) -> Path:
    runtime_root = root / "spark-researcher"
    package_root = runtime_root / "src" / "spark_researcher"
    package_root.mkdir(parents=True, exist_ok=True)
    (package_root / "__init__.py").write_text("", encoding="utf-8")
    (package_root / "advisory.py").write_text(
        (
            "from __future__ import annotations\n\n"
            "def build_advisory(*args, **kwargs):\n"
            "    return {\n"
            "        'guidance': ['Fake researcher advisory guidance.'],\n"
            "        'epistemic_status': {'status': 'ok', 'packet_stability': {'status': 'stable'}},\n"
            "        'selected_packet_ids': [],\n"
            "        'trace_id': 'trace:fake-researcher',\n"
            "    }\n"
        ),
        encoding="utf-8",
    )
    (package_root / "research.py").write_text(
        (
            "from __future__ import annotations\n\n"
            "def execute_with_research(*args, **kwargs):\n"
            "    return {\n"
            "        'decision': 'completed',\n"
            "        'reply_text': 'Fake researcher execution reply.',\n"
            "        'trace_path': 'trace:fake-researcher',\n"
            "    }\n"
        ),
        encoding="utf-8",
    )
    (runtime_root / "spark-researcher.project.json").write_text(
        json.dumps({"name": "fake-spark-researcher"}, indent=2) + "\n",
        encoding="utf-8",
    )
    return runtime_root


class SparkTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tempdir = tempfile.TemporaryDirectory()
        self.home = Path(self._tempdir.name)
        self.config_manager = ConfigManager.from_home(str(self.home))
        self.config_manager.bootstrap()
        self.state_db = StateDB(self.config_manager.paths.state_db)
        self.state_db.initialize()
        self.config_manager.set_path("spark.researcher.enabled", False)

    def tearDown(self) -> None:
        self._tempdir.cleanup()

    def add_telegram_channel(
        self,
        *,
        pairing_mode: str = "pairing",
        allowed_users: list[str] | None = None,
        bot_token: str | None = None,
    ) -> None:
        add_channel(
            config_manager=self.config_manager,
            state_db=self.state_db,
            channel_kind="telegram",
            bot_token=bot_token,
            allowed_users=allowed_users or [],
            pairing_mode=pairing_mode,
        )

    def run_cli(self, *argv: str) -> tuple[int, str, str]:
        stdout_buffer = io.StringIO()
        stderr_buffer = io.StringIO()
        with redirect_stdout(stdout_buffer), redirect_stderr(stderr_buffer):
            exit_code = main(list(argv))
        return exit_code, stdout_buffer.getvalue(), stderr_buffer.getvalue()

    def read_json(self, text: str) -> dict[str, object]:
        return json.loads(text)
