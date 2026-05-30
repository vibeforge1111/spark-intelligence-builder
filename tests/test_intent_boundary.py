from __future__ import annotations

from spark_intelligence.build_quality_review import (
    looks_like_build_quality_review_query,
    looks_like_memory_quality_dashboard_operator_query,
)
from spark_intelligence.capability_router import looks_like_capability_router_query
from spark_intelligence.intent_boundary import denies_intent, has_conversation_only_boundary
from spark_intelligence.mission_control import looks_like_mission_control_query
from spark_intelligence.system_registry import looks_like_system_registry_query

from tests.test_support import SparkTestCase


class IntentBoundaryTests(SparkTestCase):
    def test_conversation_only_boundary_catches_meta_language(self) -> None:
        for prompt in (
            "mentioning build and agent does not mean build an agent",
            "build is only a keyword in this sentence",
            "The phrase what tools appears in our prompt; do not self-introspect.",
            "memory quality dashboard is just a repo name in this sentence; do not open it",
            "We are discussing the phrase mission control, not opening a panel.",
            "Spark Swarm is a term here, not a route request.",
            "quality and build are words here; do not review a build",
            "Codex, repo, memory, wiki, access, and provider are just words here.",
        ):
            with self.subTest(prompt=prompt):
                self.assertTrue(has_conversation_only_boundary(prompt))

    def test_denies_intent_requires_named_action(self) -> None:
        self.assertTrue(denies_intent("do not route anything", ("route",)))
        self.assertTrue(denies_intent("not asking you to open mission control", ("open mission control",)))
        self.assertFalse(denies_intent("do not execute; should this go to Swarm?", ("route",)))

    def test_builder_detectors_do_not_hijack_words_or_meta_discussion(self) -> None:
        cases = (
            ("capability", looks_like_capability_router_query, "mentioning build and agent does not mean build an agent"),
            ("capability", looks_like_capability_router_query, "build + access + memory are keywords in this regression; stay in chat"),
            ("capability", looks_like_capability_router_query, "Do not route anything. Explain why the word build hijacked this."),
            ("capability", looks_like_capability_router_query, "Codex is relevant but do not route this task."),
            ("system", looks_like_system_registry_query, "the phrase what tools appears in our prompt; do not self-introspect"),
            ("system", looks_like_system_registry_query, "Spark Swarm is a term here, not a route request."),
            ("system", looks_like_system_registry_query, "We can talk here about what providers means as a phrase."),
            ("mission", looks_like_mission_control_query, "we keep saying mission control, but do not open mission control; explain the bug"),
            ("mission", looks_like_mission_control_query, "What is active right now is just quoted text in this sentence."),
            ("mission", looks_like_mission_control_query, "No need to launch; explain the launch status wording bug."),
            ("quality", looks_like_build_quality_review_query, "quality and build are words here; do not review a build"),
            ("quality", looks_like_build_quality_review_query, "Do not rate anything. Explain the build quality phrase."),
            ("dashboard", looks_like_memory_quality_dashboard_operator_query, "memory quality dashboard is just a repo name in this sentence; do not open it"),
            ("dashboard", looks_like_memory_quality_dashboard_operator_query, "Open is a keyword here, not an instruction for spark-memory-quality-dashboard."),
        )

        for lane, detector, prompt in cases:
            with self.subTest(lane=lane, prompt=prompt):
                self.assertFalse(detector(prompt))

    def test_builder_detectors_keep_real_operator_questions(self) -> None:
        positives = (
            ("capability", looks_like_capability_router_query, "Which system should handle this task?"),
            ("capability", looks_like_capability_router_query, "Can you add a capability for Spark to read my emails?"),
            ("capability", looks_like_capability_router_query, "Should you browse this?"),
            ("system", looks_like_system_registry_query, "What tools and adapters do you have?"),
            ("system", looks_like_system_registry_query, "Is Spark Swarm ready?"),
            ("mission", looks_like_mission_control_query, "Give me a one-line Telegram launch health check."),
            ("mission", looks_like_mission_control_query, "What jobs are running?"),
            ("quality", looks_like_build_quality_review_query, "Review the quality of the /memory-quality build in spawner-ui."),
            ("quality", looks_like_build_quality_review_query, "How good is this build?"),
            ("dashboard", looks_like_memory_quality_dashboard_operator_query, "Where is the memory quality dashboard?"),
        )

        for lane, detector, prompt in positives:
            with self.subTest(lane=lane, prompt=prompt):
                self.assertTrue(detector(prompt))
