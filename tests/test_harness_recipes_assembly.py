from __future__ import annotations

import unittest

from spark_intelligence.harness_registry.service import (
    HarnessContract,
    _build_harness_recipes,
)


def _contract(
    *,
    harness_id: str,
    available: bool = True,
    degraded: bool = False,
    limitations: list[str] | None = None,
) -> HarnessContract:
    return HarnessContract(
        harness_id=harness_id,
        label=f"{harness_id} label",
        owner_system=f"Owner for {harness_id}",
        route_modes=[],
        backend_kind=f"{harness_id}_backend",
        session_scope="current_conversation",
        prompt_strategy="strategy",
        toolsets=[],
        required_capabilities=[],
        artifacts=[],
        retry_policy="retry",
        approval_mode="operator_governed",
        limitations=limitations or [],
        available=available,
        degraded=degraded,
    )


class BuildHarnessRecipesTests(unittest.TestCase):
    def _all_contracts_available(self) -> list[HarnessContract]:
        return [
            _contract(harness_id="researcher.advisory", limitations=["researcher limit"]),
            _contract(harness_id="voice.io", limitations=["voice limit"]),
            _contract(harness_id="swarm.escalation", limitations=["swarm limit"]),
            _contract(harness_id="browser.grounded", limitations=["browser limit"]),
        ]

    def test_recipe_count_matches_spec(self) -> None:
        recipes = _build_harness_recipes(self._all_contracts_available())
        recipe_ids = [r["recipe_id"] for r in recipes]
        self.assertEqual(
            sorted(recipe_ids),
            ["advisory_voice_reply", "browser_then_advisory", "research_then_swarm"],
        )

    def test_recipe_available_when_all_contracts_ready(self) -> None:
        recipes = _build_harness_recipes(self._all_contracts_available())
        for recipe in recipes:
            self.assertTrue(recipe["available"], f"{recipe['recipe_id']} expected available")

    def test_recipe_unavailable_when_primary_missing(self) -> None:
        contracts = [
            _contract(harness_id="voice.io"),
            _contract(harness_id="swarm.escalation"),
            _contract(harness_id="browser.grounded"),
        ]
        recipes = {r["recipe_id"]: r for r in _build_harness_recipes(contracts)}
        # advisory_voice_reply needs researcher.advisory (missing) as primary
        self.assertFalse(recipes["advisory_voice_reply"]["available"])
        # research_then_swarm needs researcher.advisory (missing) as primary
        self.assertFalse(recipes["research_then_swarm"]["available"])

    def test_recipe_unavailable_when_follow_up_unavailable(self) -> None:
        contracts = [
            _contract(harness_id="researcher.advisory"),
            _contract(harness_id="voice.io", available=False),
            _contract(harness_id="swarm.escalation"),
            _contract(harness_id="browser.grounded"),
        ]
        recipes = {r["recipe_id"]: r for r in _build_harness_recipes(contracts)}
        # advisory_voice_reply chains researcher.advisory -> voice.io
        self.assertFalse(recipes["advisory_voice_reply"]["available"])
        # research_then_swarm still works (no voice dep)
        self.assertTrue(recipes["research_then_swarm"]["available"])

    def test_recipe_limitations_gathered_from_primary_and_followup(self) -> None:
        contracts = [
            _contract(harness_id="researcher.advisory", limitations=["researcher limit"]),
            _contract(harness_id="voice.io", limitations=["voice limit"]),
            _contract(harness_id="swarm.escalation", limitations=["swarm limit"]),
            _contract(harness_id="browser.grounded", limitations=["browser limit"]),
        ]
        recipes = {r["recipe_id"]: r for r in _build_harness_recipes(contracts)}
        advisory_voice = recipes["advisory_voice_reply"]
        self.assertIn("researcher limit", advisory_voice["limitations"])
        self.assertIn("voice limit", advisory_voice["limitations"])

    def test_recipe_limitations_dedupe_preserves_order(self) -> None:
        # Identical limitation string on both primary and follow-up -> appears once
        contracts = [
            _contract(harness_id="researcher.advisory", limitations=["shared", "researcher unique"]),
            _contract(harness_id="voice.io", limitations=["shared", "voice unique"]),
            _contract(harness_id="swarm.escalation"),
            _contract(harness_id="browser.grounded"),
        ]
        recipes = {r["recipe_id"]: r for r in _build_harness_recipes(contracts)}
        advisory_voice_limits = recipes["advisory_voice_reply"]["limitations"]
        self.assertEqual(advisory_voice_limits.count("shared"), 1)
        # Order: primary's "shared" then "researcher unique" then "voice unique" (shared already seen)
        self.assertEqual(advisory_voice_limits[0], "shared")
        self.assertIn("researcher unique", advisory_voice_limits)
        self.assertIn("voice unique", advisory_voice_limits)

    def test_recipe_limitations_capped_at_six(self) -> None:
        many_limits = [f"limit_{i}" for i in range(10)]
        contracts = [
            _contract(harness_id="researcher.advisory", limitations=many_limits),
            _contract(harness_id="voice.io", limitations=many_limits),
            _contract(harness_id="swarm.escalation"),
            _contract(harness_id="browser.grounded"),
        ]
        recipes = {r["recipe_id"]: r for r in _build_harness_recipes(contracts)}
        # The dedupe + cap rule caps at 6
        self.assertLessEqual(len(recipes["advisory_voice_reply"]["limitations"]), 6)


if __name__ == "__main__":
    unittest.main()
