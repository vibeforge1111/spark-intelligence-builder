from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class MemoryTestBatch:
    batch_id: str
    description: str
    pytest_targets: tuple[str, ...]
    covered_tracks: tuple[str, ...]
    use_when: str

    def pytest_args(self, extra_args: Iterable[str] = ()) -> tuple[str, ...]:
        return ("-q", *self.pytest_targets, *tuple(extra_args))


MEMORY_TEST_BATCHES: tuple[MemoryTestBatch, ...] = (
    MemoryTestBatch(
        batch_id="fast-contract",
        description="Fast Builder memory contracts for salience, current/entity state, session summaries, capsules, and regression packaging.",
        pytest_targets=(
            "tests/test_session_summaries.py",
            "tests/test_pending_tasks.py",
            "tests/test_procedural_lessons.py",
            "tests/test_memory_orchestrator.py",
            "tests/test_context_capsule.py",
            "tests/test_telegram_episodic_memory.py",
            "tests/test_memory_regression.py",
            "tests/test_memory_architecture_live_comparison.py",
        ),
        covered_tracks=(
            "salience",
            "entity_state",
            "episodic_summary",
            "pending_task_recovery",
            "procedural_lessons",
            "source_attribution",
            "acceptance_packaging",
        ),
        use_when="Run after each memory integration slice before Telegram acceptance.",
    ),
    MemoryTestBatch(
        batch_id="telegram-memory-unit",
        description="Telegram-facing memory unit/regression suite with generic entity state, source explanations, stale/current conflict, and acceptance packaging.",
        pytest_targets=(
            "tests/test_telegram_generic_memory.py",
            "tests/test_telegram_episodic_memory.py",
            "tests/test_context_capsule.py",
            "tests/test_memory_regression.py",
        ),
        covered_tracks=(
            "telegram_memory",
            "natural_recall",
            "entity_state",
            "source_explanation",
            "stale_current_conflict",
        ),
        use_when="Run before supervised Spark AGI/Tester loops or bot restarts.",
    ),
    MemoryTestBatch(
        batch_id="architecture-promotion",
        description="Architecture promotion checks for benchmark packs, live comparison, soak orchestration, and validation artifacts.",
        pytest_targets=(
            "tests/test_memory_architecture_benchmark.py",
            "tests/test_memory_architecture_live_comparison.py",
            "tests/test_memory_architecture_soak.py",
            "tests/test_memory_benchmark_packs.py",
            "tests/test_memory_validation_wrapper.py",
            "tests/test_memory_validation_artifact_rendering.py",
            "tests/test_memory_baseline_doc_rendering.py",
        ),
        covered_tracks=(
            "architecture_selection",
            "benchmark_packs",
            "promotion_gates",
            "validation_artifacts",
        ),
        use_when="Run before changing the selected runtime architecture or promotion gates.",
    ),
    MemoryTestBatch(
        batch_id="diagnostics-ledgers",
        description="Diagnostics, watchtower, quality-gate, lane, and CLI smoke contracts.",
        pytest_targets=(
            "tests/test_diagnostics_agent.py",
            "tests/test_builder_prelaunch_contracts.py",
            "tests/test_cli_smoke.py",
            "tests/test_acceptance_result_to_text.py",
        ),
        covered_tracks=(
            "diagnostics",
            "memory_lanes",
            "quality_gates",
            "operator_visibility",
        ),
        use_when="Run after changing diagnostics, ledgers, quality gates, or operator output.",
    ),
    MemoryTestBatch(
        batch_id="full-memory-local",
        description="Local Builder memory suite for contract, Telegram, architecture, diagnostics, and validation wrappers.",
        pytest_targets=(
            "tests/test_session_summaries.py",
            "tests/test_pending_tasks.py",
            "tests/test_procedural_lessons.py",
            "tests/test_memory_orchestrator.py",
            "tests/test_context_capsule.py",
            "tests/test_telegram_generic_memory.py",
            "tests/test_telegram_episodic_memory.py",
            "tests/test_memory_regression.py",
            "tests/test_memory_architecture_benchmark.py",
            "tests/test_memory_architecture_live_comparison.py",
            "tests/test_memory_architecture_soak.py",
            "tests/test_memory_benchmark_packs.py",
            "tests/test_memory_validation_wrapper.py",
            "tests/test_memory_validation_artifact_rendering.py",
            "tests/test_memory_baseline_doc_rendering.py",
            "tests/test_diagnostics_agent.py",
            "tests/test_builder_prelaunch_contracts.py",
            "tests/test_cli_smoke.py",
            "tests/test_acceptance_result_to_text.py",
        ),
        covered_tracks=(
            "full_local_memory",
            "telegram_memory",
            "architecture_promotion",
            "diagnostics",
            "quality_gates",
            "pending_task_recovery",
            "procedural_lessons",
        ),
        use_when="Run before pushing a larger memory milestone.",
    ),
)


def list_memory_test_batches() -> tuple[MemoryTestBatch, ...]:
    return MEMORY_TEST_BATCHES


def get_memory_test_batch(batch_id: str) -> MemoryTestBatch:
    normalized = str(batch_id or "").strip().casefold()
    for batch in MEMORY_TEST_BATCHES:
        if batch.batch_id.casefold() == normalized:
            return batch
    known = ", ".join(batch.batch_id for batch in MEMORY_TEST_BATCHES)
    raise ValueError(f"unknown_memory_test_batch:{batch_id}; known: {known}")


def memory_test_batch_pytest_args(batch_id: str, *, extra_args: Iterable[str] = ()) -> tuple[str, ...]:
    return get_memory_test_batch(batch_id).pytest_args(extra_args=extra_args)


def memory_test_batch_command(
    batch_id: str,
    *,
    python_executable: str = "python",
    extra_args: Iterable[str] = (),
) -> tuple[str, ...]:
    return (python_executable, "-m", "pytest", *memory_test_batch_pytest_args(batch_id, extra_args=extra_args))


def missing_memory_test_targets(batch_id: str, *, repo_root: Path) -> tuple[str, ...]:
    batch = get_memory_test_batch(batch_id)
    return tuple(target for target in batch.pytest_targets if not (repo_root / target).exists())
