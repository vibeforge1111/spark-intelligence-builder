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
    text: str,
    username: str | None = None,
    chat_id: str | None = None,
    chat_type: str = "private",
) -> dict[str, object]:
    resolved_chat_id = chat_id or user_id
    return {
        "update_id": update_id,
        "message": {
            "message_id": update_id * 10,
            "text": text,
            "chat": {
                "id": resolved_chat_id,
                "type": chat_type,
            },
            "from": {
                "id": user_id,
                "username": username,
            },
        },
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
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("hook", choices=["evaluate", "suggest", "packets", "watchtower", "identity", "personality"])
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
                "capabilities": ["evaluate", "suggest", "packets", "watchtower", "identity", "personality"],
                "commands": {
                    "evaluate": ["python", "-m", "fake_startup_chip.chip_hooks", "evaluate"],
                    "suggest": ["python", "-m", "fake_startup_chip.chip_hooks", "suggest"],
                    "packets": ["python", "-m", "fake_startup_chip.chip_hooks", "packets"],
                    "watchtower": ["python", "-m", "fake_startup_chip.chip_hooks", "watchtower"],
                    "identity": ["python", "-m", "fake_startup_chip.chip_hooks", "identity"],
                    "personality": ["python", "-m", "fake_startup_chip.chip_hooks", "personality"],
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return repo_root


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
