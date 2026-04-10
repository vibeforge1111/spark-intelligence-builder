from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import ANY, patch

from spark_intelligence.attachments.snapshot import build_attachment_context
from spark_intelligence.auth.runtime import RuntimeProviderResolution
from spark_intelligence.memory import write_profile_fact_to_memory
from spark_intelligence.observability.store import latest_events_by_type, record_event
from spark_intelligence.researcher_bridge.advisory import (
    _build_browser_search_context,
    _browser_reply_denies_browsing,
    _build_contextual_task,
    _load_recent_conversation_context,
    _clean_messaging_reply,
    _normalize_browser_search_query,
    _should_collect_browser_search_context,
    _render_direct_provider_chat_fallback,
    _rewrite_browser_search_capability_denial,
    _sanitize_browser_search_reply,
    _select_search_result_candidate_from_interactives_result,
    _select_search_result_candidate_from_text_result,
    build_researcher_reply,
)
from spark_intelligence.system_registry import build_system_registry_prompt_context

from tests.test_support import SparkTestCase, create_fake_hook_chip


class ResearcherBridgeProviderResolutionTests(SparkTestCase):
    def test_normalize_browser_search_query_strips_source_citation_suffix(self) -> None:
        query = _normalize_browser_search_query(
            "Search the web for Example Domain and cite the source you used."
        )

        self.assertEqual(query, "Example Domain")

    def test_normalize_browser_search_query_strips_imperative_prefix(self) -> None:
        query = _normalize_browser_search_query(
            "I want you to search the web for Example Domain"
        )

        self.assertEqual(query, "Example Domain")

    def test_normalize_browser_search_query_extracts_domain_from_browse_request(self) -> None:
        query = _normalize_browser_search_query(
            "Go to vibeship.co and tell me what you think."
        )

        self.assertEqual(query, "vibeship.co")

    def test_should_collect_browser_search_context_for_domain_browse_request(self) -> None:
        self.assertTrue(_should_collect_browser_search_context("browse vibeship.co"))
        self.assertTrue(
            _should_collect_browser_search_context(
                "Open https://vibeship.co and tell me what you think."
            )
        )
        self.assertFalse(_should_collect_browser_search_context("open the discussion"))

    def test_select_search_result_candidate_from_text_result_prefers_external_domain(self) -> None:
        candidate = _select_search_result_candidate_from_text_result(
            {
                "result": {
                    "visible_text": {
                        "summary": "Example Domain www.example.com Example Domain",
                        "excerpt": "Example Domain www.example.com Example Domain is reserved for documentation.",
                    }
                }
            },
            search_url="https://duckduckgo.com/?q=Example%20Domain&ia=web",
        )

        self.assertEqual(candidate, {"href": "https://www.example.com", "text_summary": ""})

    def test_select_search_result_candidate_from_text_result_skips_search_engine_hosts(self) -> None:
        candidate = _select_search_result_candidate_from_text_result(
            {
                "result": {
                    "visible_text": {
                        "summary": "DuckDuckGo https://duck.ai/?q=Example%20Domain Example Domain www.example.com",
                        "excerpt": "Example Domain is reserved for documentation.",
                    }
                }
            },
            search_url="https://duckduckgo.com/?q=Example%20Domain&ia=web",
        )

        self.assertEqual(candidate, {"href": "https://www.example.com", "text_summary": ""})

    def test_select_search_result_candidate_from_interactives_result_prefers_external_href(self) -> None:
        candidate = _select_search_result_candidate_from_interactives_result(
            {
                "result": {
                    "interactives": [
                        {
                            "label": "DuckDuckGo search box",
                            "href": "https://duckduckgo.com/?q=BTC&ia=web",
                        },
                        {
                            "label": "CoinMarketCap Bitcoin price",
                            "href": "https://coinmarketcap.com/currencies/bitcoin/",
                        },
                    ]
                }
            },
            search_url="https://duckduckgo.com/?q=BTC&ia=web",
        )

        self.assertEqual(
            candidate,
            {
                "href": "https://coinmarketcap.com/currencies/bitcoin/",
                "text_summary": "CoinMarketCap Bitcoin price",
            },
        )

    def test_sanitize_browser_search_reply_replaces_search_engine_citation_with_external_source(self) -> None:
        cleaned, actions = _sanitize_browser_search_reply(
            (
                "Example Domain is reserved for documentation and examples.\n\n"
                "Source: https://duckduckgo.com/?q=Example%20Domain&ia=web (DuckDuckGo search)"
            ),
            source_url="https://www.iana.org/domains/reserved",
        )

        self.assertNotIn("duckduckgo.com", cleaned)
        self.assertIn("Source: https://www.iana.org/domains/reserved", cleaned)
        self.assertIn("strip_search_engine_citation", actions)
        self.assertIn("append_external_source_citation", actions)

    def test_sanitize_browser_search_reply_warns_when_external_source_missing(self) -> None:
        cleaned, actions = _sanitize_browser_search_reply(
            (
                "Example Domain is reserved for documentation and examples.\n\n"
                "Source: https://duckduckgo.com/?q=Example%20Domain&ia=web (DuckDuckGo search)"
            ),
            source_url=None,
        )

        self.assertNotIn("duckduckgo.com", cleaned)
        self.assertIn("Source capture failed on the result page", cleaned)
        self.assertIn("strip_search_engine_citation", actions)
        self.assertIn("append_source_capture_warning", actions)

    def test_sanitize_browser_search_reply_rewrites_weak_source_capture_body(self) -> None:
        cleaned, actions = _sanitize_browser_search_reply(
            (
                "Can't pull actual content from that search-the external source capture came back empty. "
                "The DuckDuckGo search page returned no extractable data, so I can't cite meaningful information on BTC."
            ),
            source_url=None,
        )

        self.assertIn("Web search ran, but source capture failed on the result page.", cleaned)
        self.assertIn("Reason: the search result page did not yield usable external content to cite.", cleaned)
        self.assertIn("Next: retry with a more specific query or open a stronger source page.", cleaned)
        self.assertIn("rewrite_weak_source_capture_reply", actions)
        self.assertNotIn("Can't pull actual content from that search", cleaned)

    def test_sanitize_browser_search_reply_rewrites_live_source_capture_variant(self) -> None:
        cleaned, actions = _sanitize_browser_search_reply(
            (
                "I don't have actual BTC content from the search. The browser evidence shows a search was performed "
                "but the source content wasn't captured-just metadata about the search itself."
            ),
            source_url=None,
        )

        self.assertIn("Web search ran, but source capture failed on the result page.", cleaned)
        self.assertIn("rewrite_weak_source_capture_reply", actions)
        self.assertNotIn("I don't have actual BTC content from the search", cleaned)

    def test_sanitize_browser_search_reply_rewrites_empty_external_source_variant(self) -> None:
        cleaned, actions = _sanitize_browser_search_reply(
            (
                "The browser search for BTC ran but the actual page content wasn't captured - "
                "the external source came back empty."
            ),
            source_url=None,
        )

        self.assertIn("Web search ran, but source capture failed on the result page.", cleaned)
        self.assertIn("rewrite_weak_source_capture_reply", actions)
        self.assertNotIn("actual page content wasn't captured", cleaned)

    def test_sanitize_browser_search_reply_rewrites_empty_capture_results_variant(self) -> None:
        cleaned, actions = _sanitize_browser_search_reply(
            (
                "I ran the search for BTC on DuckDuckGo, but the actual source content didn't come through - "
                "the capture returned empty results."
            ),
            source_url=None,
        )

        self.assertIn("Web search ran, but source capture failed on the result page.", cleaned)
        self.assertIn("rewrite_weak_source_capture_reply", actions)
        self.assertNotIn("capture returned empty results", cleaned)

    def test_sanitize_browser_search_reply_rewrites_missing_source_variant(self) -> None:
        cleaned, actions = _sanitize_browser_search_reply(
            (
                "The search was attempted but the actual content from the source couldn't be captured - "
                "the external source came back missing. I don't have a live excerpt or page content to pull from."
            ),
            source_url=None,
        )

        self.assertIn("Web search ran, but source capture failed on the result page.", cleaned)
        self.assertIn("rewrite_weak_source_capture_reply", actions)
        self.assertNotIn("external source came back missing", cleaned)

    def test_sanitize_browser_search_reply_strips_internal_search_markup(self) -> None:
        cleaned, actions = _sanitize_browser_search_reply(
            (
                "Let me search for Bitcoin BTC now.\n\n"
                "<search>\n"
                "<query>BTC Bitcoin price today 2025</query>\n"
                "</search>"
            ),
            source_url=None,
        )

        self.assertEqual(
            cleaned,
            (
                "Let me search for Bitcoin BTC now.\n\n"
                "Source capture failed on the result page, so retry the search if you need an authoritative citation."
            ),
        )
        self.assertIn("strip_internal_search_markup", actions)
        self.assertIn("append_source_capture_warning", actions)

    def test_build_browser_search_context_retries_transient_status_probe_failure(self) -> None:
        with (
            patch("spark_intelligence.researcher_bridge.advisory.time.sleep"),
            patch(
                "spark_intelligence.researcher_bridge.advisory._execute_browser_hook",
                side_effect=[
                    (
                        {
                            "status": "failed",
                            "error": {
                                "code": "BROWSER_SESSION_STALE",
                                "message": "Live browser session is not currently connected.",
                            },
                        },
                        "spark-browser",
                    ),
                    (
                        {
                            "status": "succeeded",
                            "result": {
                                "extension": {
                                    "running": True,
                                }
                            },
                        },
                        "spark-browser",
                    ),
                    (None, "spark-browser"),
                ],
            ) as hook_mock,
        ):
            result = _build_browser_search_context(
                config_manager=self.config_manager,
                state_db=self.state_db,
                user_message="Search the web for BTC and cite the source.",
                request_id="req-browser-retry",
                channel_kind="telegram",
                agent_id="agent:human:telegram:111",
                human_id="human:telegram:111",
                session_id="session:telegram:dm:111",
            )

        self.assertEqual(result, {"context": "", "blocked_reply": None, "blocked_code": None})
        self.assertEqual(hook_mock.call_count, 3)
        self.assertEqual(hook_mock.call_args_list[0].kwargs["hook"], "browser.status")
        self.assertEqual(hook_mock.call_args_list[1].kwargs["hook"], "browser.status")
        self.assertEqual(hook_mock.call_args_list[2].kwargs["hook"], "browser.navigate")

    def test_build_browser_search_context_uses_interactives_fallback_for_external_result(self) -> None:
        with patch(
            "spark_intelligence.researcher_bridge.advisory._execute_browser_hook",
            side_effect=[
                (
                    {
                        "status": "succeeded",
                        "result": {
                            "extension": {
                                "running": True,
                            }
                        },
                    },
                    "spark-browser",
                ),
                (
                    {
                        "status": "succeeded",
                        "result": {
                            "origin": "https://duckduckgo.com",
                            "tab": {"id": "42"},
                            "wait_hint": {"target": {"origin": "https://duckduckgo.com", "tab_id": "42"}},
                        },
                    },
                    "spark-browser",
                ),
                (
                    {
                        "status": "succeeded",
                        "result": {},
                    },
                    "spark-browser",
                ),
                (
                    {
                        "status": "succeeded",
                        "result": {
                            "dom_outline": {"nodes": []},
                            "title": "BTC at DuckDuckGo",
                        },
                    },
                    "spark-browser",
                ),
                (
                    {
                        "status": "succeeded",
                        "result": {
                            "interactives": [
                                {
                                    "label": "CoinMarketCap Bitcoin price",
                                    "href": "https://coinmarketcap.com/currencies/bitcoin/",
                                }
                            ]
                        },
                    },
                    "spark-browser",
                ),
                (
                    {
                        "status": "succeeded",
                        "result": {
                            "origin": "https://coinmarketcap.com",
                            "tab": {"id": "43"},
                            "wait_hint": {"target": {"origin": "https://coinmarketcap.com", "tab_id": "43"}},
                        },
                    },
                    "spark-browser",
                ),
                (
                    {
                        "status": "succeeded",
                        "result": {},
                    },
                    "spark-browser",
                ),
                (
                    {
                        "status": "succeeded",
                        "result": {
                            "title": "Bitcoin price today",
                            "origin": "https://coinmarketcap.com",
                            "visible_text": {
                                "summary": "Bitcoin price is live on CoinMarketCap.",
                                "excerpt": "BTC price and market cap details.",
                            },
                        },
                    },
                    "spark-browser",
                ),
            ],
        ) as hook_mock:
            result = _build_browser_search_context(
                config_manager=self.config_manager,
                state_db=self.state_db,
                user_message="Search the web for BTC and cite the source.",
                request_id="req-browser-interactives-fallback",
                channel_kind="telegram",
                agent_id="agent:human:telegram:111",
                human_id="human:telegram:111",
                session_id="session:telegram:dm:111",
            )

        self.assertIn("source_url=https://coinmarketcap.com/currencies/bitcoin/", str(result["context"]))
        self.assertIsNone(result["blocked_reply"])
        self.assertIsNone(result["blocked_code"])
        called_hooks = [call.kwargs["hook"] for call in hook_mock.call_args_list]
        self.assertEqual(
            called_hooks,
            [
                "browser.status",
                "browser.navigate",
                "browser.tab.wait",
                "browser.page.dom_extract",
                "browser.page.interactives.list",
                "browser.navigate",
                "browser.tab.wait",
                "browser.page.text_extract",
            ],
        )

    def test_browser_reply_denies_browsing_detects_false_capability_claim(self) -> None:
        self.assertTrue(
            _browser_reply_denies_browsing(
                "I don't have real-time web browsing capability, so I can't pull live BTC price data right now."
            )
        )
        self.assertTrue(
            _browser_reply_denies_browsing(
                "I don't have real-time web search, so I can't pull live BTC data for you."
            )
        )
        self.assertFalse(_browser_reply_denies_browsing("I searched the web and found a result."))

    def test_rewrite_browser_search_capability_denial_when_source_capture_is_weak(self) -> None:
        rewritten = _rewrite_browser_search_capability_denial(
            "I don't have real-time web browsing capability, so I can't pull live BTC price data right now.",
            browser_search_context_extra=(
                "[Browser search evidence]\n"
                "search_query=BTC\n"
                "search_url=https://duckduckgo.com/?q=BTC&ia=web\n"
                "external_source_captured=no\n"
                "source_capture_status=external_source_missing\n"
                "source_summary=\n"
                "source_excerpt=\n"
            ),
        )

        self.assertIn('I did run a browser search for "BTC"', rewritten)
        self.assertIn('Next: retry with a more specific query like "BTC price today"', rewritten)
        self.assertNotIn("I don't have real-time web browsing capability", rewritten)

    def test_sanitize_browser_search_reply_polishes_quote_spacing_and_generic_tail(self) -> None:
        cleaned, actions = _sanitize_browser_search_reply(
            (
                'VibeShip is a toolkit for "vibe coders"developers using AI assistants.\n\n'
                'The bundle is broad rather than a single wedge"they\'re stacking memory, security, and backend scaffolding.\n\n'
                "What problem are you trying to solve with this?"
            ),
            source_url="https://vibeship.co",
        )

        self.assertIn('"vibe coders" developers', cleaned)
        self.assertIn('wedge" they\'re', cleaned)
        self.assertNotIn("What problem are you trying to solve with this?", cleaned)
        self.assertIn("Source: https://vibeship.co", cleaned)
        self.assertIn("repair_quote_spacing", actions)
        self.assertIn("strip_generic_followup_question", actions)
        self.assertIn("append_external_source_citation", actions)

    def test_clean_messaging_reply_rewrites_structured_chip_memo_for_telegram(self) -> None:
        cleaned = _clean_messaging_reply(
            (
                "# Revised: Decision Clarity Infrastructure\n\n"
                "**Recommendation**: Prioritize interpretation scaffolding for no actionable diff outputs.\n\n"
                "## Primary Focus\n"
                "Build operator-facing guidance for no actionable diff outputs.\n\n"
                "## Why This Works\n"
                "Undocumented silent outcomes create operator doubt.\n\n"
                "- Confidence: 0.45\n"
                "- Evidence gap: No verification operators lack this layer.\n\n"
                "trace_ref: trace:internal-cleanup\n"
                "packet_refs: packet-1\n\n"
                "## Next Step\n"
                "Survey 2-3 operator workflows."
            ),
            channel_kind="telegram",
        )

        self.assertEqual(
            cleaned,
            (
                'Prioritize interpretation scaffolding for no actionable diff outputs.\n\n'
                'Build operator-facing guidance for no actionable diff outputs. '
                'Undocumented silent outcomes create operator doubt.\n\n'
                'Next: Survey 2-3 operator workflows.'
            ),
        )

    def test_clean_messaging_reply_strips_internal_research_note_prefixes(self) -> None:
        cleaned = _clean_messaging_reply(
            "Based on the research notes provided, the strongest next move is to tighten operator docs.",
            channel_kind="telegram",
        )

        self.assertEqual(cleaned, "the strongest next move is to tighten operator docs.")

    def test_clean_messaging_reply_strips_think_blocks_before_delivery(self) -> None:
        cleaned = _clean_messaging_reply(
            "<think>private reasoning</think>\n\nUse the startup signal, not vanity metrics.",
            channel_kind="telegram",
        )

        self.assertEqual(cleaned, "Use the startup signal, not vanity metrics.")

    def test_clean_messaging_reply_strips_inline_markdown_emphasis_for_telegram(self) -> None:
        cleaned = _clean_messaging_reply(
            "**The 4 churned agencies** matter more than the headline MRR right now.",
            channel_kind="telegram",
        )

        self.assertEqual(cleaned, "The 4 churned agencies matter more than the headline MRR right now.")

    def test_build_contextual_task_summarizes_chip_guidance_without_meta_scaffolding(self) -> None:
        prompt = _build_contextual_task(
            user_message="what next",
            attachment_context={
                "active_chip_keys": ["startup-yc"],
                "pinned_chip_keys": [],
                "active_path_key": "startup-operator",
            },
            active_chip_evaluate={
                "chip_key": "startup-yc",
                "task_type": "diagnostic_questioning",
                "stage": "idea",
                "analysis": (
                    "# Revised: Decision Clarity Infrastructure\n\n"
                    "**Recommendation**: Prioritize interpretation scaffolding.\n\n"
                    "## Primary Focus\n"
                    "Build operator-facing guidance for no actionable diff outputs.\n\n"
                    "- Confidence: 0.45\n"
                    "- Evidence gap: No verification operators lack this layer.\n\n"
                    "## Next Step\n"
                    "Survey a few operator workflows."
                ),
            },
        )

        self.assertIn("[Active chip guidance]", prompt)
        self.assertIn("Use this guidance as background context only.", prompt)
        self.assertIn("Prioritize interpretation scaffolding.", prompt)
        self.assertNotIn("Confidence:", prompt)
        self.assertNotIn("Evidence gap:", prompt)
        self.assertNotIn("## Primary Focus", prompt)

    def test_build_contextual_task_prefers_agent_persona_over_global_personality_name(self) -> None:
        prompt = _build_contextual_task(
            user_message="what next",
            attachment_context={
                "active_chip_keys": ["startup-yc"],
                "pinned_chip_keys": [],
                "active_path_key": "startup-operator",
            },
            personality_profile={
                "personality_name": "Alice",
                "agent_persona_name": "Operator",
                "traits": {
                    "warmth": 0.7,
                    "directness": 1.0,
                    "playfulness": 0.5,
                    "pacing": 0.55,
                    "assertiveness": 0.3,
                },
                "style_labels": {
                    "warmth": "warm",
                    "directness": "very direct",
                    "playfulness": "balanced playfulness",
                    "pacing": "brisk",
                    "assertiveness": "balanced assertiveness",
                },
                "agent_persona_applied": True,
                "user_deltas_applied": False,
            },
        )

        self.assertIn("agent_persona=Operator", prompt)
        self.assertNotIn("active_personality=Alice", prompt)

    def test_build_contextual_task_includes_attached_chip_inventory_for_self_knowledge_queries(self) -> None:
        create_fake_hook_chip(self.home, chip_key="startup-yc")
        create_fake_hook_chip(self.home, chip_key="spark-browser")
        create_fake_hook_chip(self.home, chip_key="spark-personality-chip-labs")
        create_fake_hook_chip(self.home, chip_key="spark-swarm")
        create_fake_hook_chip(self.home, chip_key="domain-chip-voice-comms")
        self.config_manager.set_path("spark.chips.roots", [str(self.home)])
        self.config_manager.set_path("spark.chips.active_keys", ["startup-yc", "spark-browser"])
        self.config_manager.set_path("spark.chips.pinned_keys", ["startup-yc"])
        attachment_context = build_attachment_context(self.config_manager)
        system_registry_context = build_system_registry_prompt_context(
            config_manager=self.config_manager,
            state_db=self.state_db,
            user_message="What chips are active around you right now?",
        )
        prompt = _build_contextual_task(
            user_message="What chips are active around you right now?",
            attachment_context=attachment_context,
            system_registry_context=system_registry_context,
        )

        self.assertIn("active_chip_keys=startup-yc,spark-browser", prompt)
        self.assertIn("attached_chip_keys=domain-chip-voice-comms,spark-browser,spark-personality-chip-labs,spark-swarm,startup-yc", prompt)
        self.assertIn("[Attached chip inventory]", prompt)
        self.assertIn("spark-browser mode=active", prompt)
        self.assertIn("[Spark system registry]", prompt)
        self.assertIn("Spark Researcher: status=", prompt)
        self.assertIn("Spark Swarm: status=", prompt)
        self.assertIn("[Current capabilities]", prompt)

    def test_load_recent_conversation_context_reads_prior_telegram_turns(self) -> None:
        record_event(
            self.state_db,
            event_type="intent_committed",
            component="telegram_runtime",
            summary="Older user message committed.",
            channel_id="telegram",
            session_id="sess-1",
            request_id="req-0",
            facts={"message_text": "Keep the thread continuity hot."},
        )
        record_event(
            self.state_db,
            event_type="intent_committed",
            component="telegram_runtime",
            summary="User message committed.",
            channel_id="telegram",
            session_id="sess-1",
            request_id="req-1",
            facts={"message_text": "I want this to feel less scripted."},
        )
        record_event(
            self.state_db,
            event_type="delivery_succeeded",
            component="telegram_runtime",
            summary="Reply delivered.",
            channel_id="telegram",
            session_id="sess-1",
            request_id="req-1",
            reason_code="telegram_bridge_outbound",
            facts={"delivered_text": "The main issue is continuity, not just tone."},
        )
        record_event(
            self.state_db,
            event_type="intent_committed",
            component="telegram_runtime",
            summary="Current user message committed.",
            channel_id="telegram",
            session_id="sess-1",
            request_id="req-2",
            facts={"message_text": "Now answer like you remember what I said."},
        )

        context = _load_recent_conversation_context(
            state_db=self.state_db,
            session_id="sess-1",
            channel_kind="telegram",
            request_id="req-2",
        )

        self.assertIn("[Recent conversation]", context)
        self.assertIn("user: I want this to feel less scripted.", context)
        self.assertIn("assistant: The main issue is continuity, not just tone.", context)
        self.assertIn("latest_visible_turn.role=assistant", context)
        self.assertIn("latest_visible_turn.text=The main issue is continuity, not just tone.", context)
        self.assertIn("previous_visible_turn.role=user", context)
        self.assertIn("previous_visible_turn.text=I want this to feel less scripted.", context)
        self.assertIn("turn_before_previous_visible_turn.role=user", context)
        self.assertIn("turn_before_previous_visible_turn.text=Keep the thread continuity hot.", context)
        self.assertIn("latest_user_message=I want this to feel less scripted.", context)
        self.assertIn("previous_user_message=Keep the thread continuity hot.", context)
        self.assertNotIn("Now answer like you remember what I said.", context)

    def test_build_researcher_reply_includes_recent_telegram_turns_in_provider_prompt(self) -> None:
        self.config_manager.set_path("spark.researcher.enabled", True)
        connect_exit, _, connect_stderr = self.run_cli(
            "auth",
            "connect",
            "custom",
            "--home",
            str(self.home),
            "--api-key",
            "test-secret",
            "--model",
            "MiniMax-M2.7",
            "--base-url",
            "https://api.minimax.io/v1",
        )
        self.assertEqual(connect_exit, 0, connect_stderr)

        record_event(
            self.state_db,
            event_type="intent_committed",
            component="telegram_runtime",
            summary="User message committed.",
            channel_id="telegram",
            session_id="sess-telegram",
            request_id="old-1",
            facts={"message_text": "I'm trying to make this agent feel more natural and less scripted."},
        )
        record_event(
            self.state_db,
            event_type="delivery_succeeded",
            component="telegram_runtime",
            summary="Reply delivered.",
            channel_id="telegram",
            session_id="sess-telegram",
            request_id="old-1",
            reason_code="telegram_bridge_outbound",
            facts={"delivered_text": "The main problem is continuity, not just tone."},
        )

        captured: dict[str, str] = {}

        def fake_execute_direct_provider_prompt(*, user_prompt, **kwargs):
            captured["user_prompt"] = user_prompt
            return {"raw_response": "Stay anchored to the thread."}

        with (
            patch(
                "spark_intelligence.researcher_bridge.advisory._build_browser_search_context",
                return_value={
                    "context": "[Browser evidence]\nsource_url=https://example.com",
                    "blocked_reply": None,
                    "blocked_code": None,
                    "source_url": "https://example.com",
                },
            ),
            patch(
                "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
                side_effect=fake_execute_direct_provider_prompt,
            ),
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-current",
                agent_id="agent:human:telegram:1",
                human_id="human:telegram:1",
                session_id="sess-telegram",
                channel_kind="telegram",
                user_message="Now answer like you actually remember what I just said.",
            )

        self.assertEqual(result.mode, "browser_evidence")
        prompt = captured["user_prompt"]
        self.assertIn("[Recent conversation]", prompt)
        self.assertIn("user: I'm trying to make this agent feel more natural and less scripted.", prompt)
        self.assertIn("assistant: The main problem is continuity, not just tone.", prompt)
        self.assertIn("latest_visible_turn.role=assistant", prompt)
        self.assertIn("previous_visible_turn.role=user", prompt)
        self.assertIn("latest_user_message=I'm trying to make this agent feel more natural and less scripted.", prompt)
        self.assertIn("[User message]", prompt)
        self.assertIn("Now answer like you actually remember what I just said.", prompt)

    def test_build_researcher_reply_cleans_memo_style_execution_reply_for_telegram(self) -> None:
        self.config_manager.set_path("spark.researcher.enabled", True)
        connect_exit, _, connect_stderr = self.run_cli(
            "auth",
            "connect",
            "custom",
            "--home",
            str(self.home),
            "--api-key",
            "minimax-secret",
            "--model",
            "MiniMax-M2.7",
            "--base-url",
            "https://api.minimax.io/v1",
        )
        self.assertEqual(connect_exit, 0, connect_stderr)

        runtime_root = self.home / "fake-researcher"
        runtime_root.mkdir(parents=True, exist_ok=True)
        config_path = runtime_root / "spark-researcher.project.json"
        config_path.write_text("{}", encoding="utf-8")

        def fake_build_advisory(path: Path, task: str, *, model: str = "generic", limit: int = 4, domain: str | None = None):
            return {
                "guidance": ["Use evidence-backed guidance."],
                "epistemic_status": {"status": "grounded", "packet_stability": {"status": "durable_supported"}},
                "selected_packet_ids": ["packet-1"],
                "trace_path": "trace:test",
            }

        def fake_execute_with_research(*args, **kwargs):
            return {
                "status": "ok",
                "decision": "approve",
                "response": {
                    "raw_response": (
                        "# Revised: Decision Clarity Infrastructure\n\n"
                        "## Primary Focus\n"
                        "Build operator-facing guidance for no actionable diff outputs.\n\n"
                        "- Confidence: 0.45\n"
                        "- Evidence gap: No verification operators lack this layer.\n\n"
                        "trace_ref: trace:execution-internal\n"
                        "memory_refs: memory-1\n\n"
                        "## Next Step\n"
                        "Survey operator workflows."
                    )
                },
                "trace_path": "trace:execution",
            }

        with patch(
            "spark_intelligence.researcher_bridge.advisory.discover_researcher_runtime_root",
            return_value=(runtime_root, "configured"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.resolve_researcher_config_path",
            return_value=config_path,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._import_build_advisory",
            return_value=fake_build_advisory,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._import_execute_with_research",
            return_value=fake_execute_with_research,
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-clean-telegram",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-1",
                channel_kind="telegram",
                user_message="What should we focus on next?",
            )

        self.assertEqual(result.routing_decision, "provider_execution")
        self.assertEqual(
            result.reply_text,
            (
                "Build operator-facing guidance for no actionable diff outputs.\n\n"
                "Next: Survey operator workflows."
            ),
        )
        self.assertEqual(result.output_keepability, "ephemeral_context")
        self.assertEqual(result.promotion_disposition, "not_promotable")
        self.assertNotIn("Confidence:", result.reply_text)
        self.assertNotIn("Evidence gap:", result.reply_text)
        self.assertNotIn("Primary Focus", result.reply_text)
        self.assertNotIn("trace:", result.reply_text)
        self.assertNotIn("memory_refs", result.reply_text)
        events = latest_events_by_type(self.state_db, event_type="quarantine_recorded", limit=10)
        self.assertTrue(
            any(
                (event.get("facts_json") or {}).get("source_kind") == "reply_residue"
                for event in events
            )
        )

    def test_build_researcher_reply_uses_selected_draft_when_verifier_returns_no_response(self) -> None:
        self.config_manager.set_path("spark.researcher.enabled", True)
        connect_exit, _, connect_stderr = self.run_cli(
            "auth",
            "connect",
            "custom",
            "--home",
            str(self.home),
            "--api-key",
            "minimax-secret",
            "--model",
            "MiniMax-M2.7",
            "--base-url",
            "https://api.minimax.io/v1",
        )
        self.assertEqual(connect_exit, 0, connect_stderr)

        runtime_root = self.home / "fake-researcher"
        runtime_root.mkdir(parents=True, exist_ok=True)
        config_path = runtime_root / "spark-researcher.project.json"
        config_path.write_text("{}", encoding="utf-8")

        def fake_build_advisory(path: Path, task: str, *, model: str = "generic", limit: int = 4, domain: str | None = None):
            return {
                "guidance": [
                    "A lower learning rate should improve val_loss.",
                    "Operators need a documented interpretation for successful backend runs that yield no actionable diff.",
                ],
                "epistemic_status": {"status": "partial", "packet_stability": {"status": "provisional_only"}},
                "selected_packet_ids": ["packet-1"],
                "trace_path": "trace:test",
            }

        def fake_execute_with_research(*args, **kwargs):
            return {
                "status": "needs_verification",
                "decision": "needs_verification",
                "response": None,
                "draft": {
                    "response": {
                        "raw_response": (
                            "<think>private reasoning</think>\n\n"
                            "8% weekly churn is an emergency. Pick one ICP, talk to three churned users this week, "
                            "and pause net-new features until you know why they leave."
                        )
                    }
                },
                "drafts": {"selected": "a"},
                "critique": {
                    "decision": "needs_verification",
                    "selected": "a",
                    "issues": ["Verifier did not return parseable JSON."],
                    "best_next_question": "",
                },
                "trace_path": "trace:execution",
            }

        with patch(
            "spark_intelligence.researcher_bridge.advisory.discover_researcher_runtime_root",
            return_value=(runtime_root, "configured"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.resolve_researcher_config_path",
            return_value=config_path,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._import_build_advisory",
            return_value=fake_build_advisory,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._import_execute_with_research",
            return_value=fake_execute_with_research,
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-draft-fallback",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-1",
                channel_kind="telegram",
                user_message="We have 8 percent weekly churn and two ICPs. What should we prioritize this week?",
            )

        self.assertIn("8% weekly churn is an emergency.", result.reply_text)
        self.assertNotIn("learning rate", result.reply_text.lower())
        self.assertNotIn("<think>", result.reply_text)

    def test_build_researcher_reply_uses_direct_provider_chat_fallback_for_under_supported_conversation(self) -> None:
        self.config_manager.set_path("spark.researcher.enabled", True)
        connect_exit, _, connect_stderr = self.run_cli(
            "auth",
            "connect",
            "custom",
            "--home",
            str(self.home),
            "--api-key",
            "minimax-secret",
            "--model",
            "MiniMax-M2.7",
            "--base-url",
            "https://api.minimax.io/v1",
        )
        self.assertEqual(connect_exit, 0, connect_stderr)

        runtime_root = self.home / "fake-researcher"
        runtime_root.mkdir(parents=True, exist_ok=True)
        config_path = runtime_root / "spark-researcher.project.json"
        config_path.write_text("{}", encoding="utf-8")
        captured: dict[str, object] = {}

        def fake_build_advisory(path: Path, task: str, *, model: str = "generic", limit: int = 4, domain: str | None = None):
            captured["advisory_model"] = model
            return {
                "guidance": [],
                "epistemic_status": {
                    "status": "under_supported",
                    "packet_stability": {"status": "no_belief_packets"},
                },
                "selected_packet_ids": [],
                "trace_path": "trace:under-supported",
            }

        def fake_direct_provider_prompt(*, provider, system_prompt: str, user_prompt: str, governance=None):
            captured["provider_id"] = provider.provider_id
            captured["provider_model"] = provider.model
            captured["system_prompt"] = system_prompt
            captured["user_prompt"] = user_prompt
            captured["governance"] = governance
            return {"raw_response": "Hey there. How can I help?"}

        def fail_execute_with_research(*args, **kwargs):
            raise AssertionError("execute_with_research should not run for direct conversational fallback")

        with patch(
            "spark_intelligence.researcher_bridge.advisory.discover_researcher_runtime_root",
            return_value=(runtime_root, "configured"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.resolve_researcher_config_path",
            return_value=config_path,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._import_build_advisory",
            return_value=fake_build_advisory,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._import_execute_with_research",
            return_value=fail_execute_with_research,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=fake_direct_provider_prompt,
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-fallback",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-1",
                channel_kind="telegram",
                user_message="hey",
            )

        self.assertEqual(captured["provider_id"], "custom")
        self.assertEqual(captured["provider_model"], "MiniMax-M2.7")
        self.assertIn("1:1 messaging conversation", str(captured["system_prompt"]))
        self.assertNotIn("decisive startup operator", str(captured["system_prompt"]))
        self.assertIn("[fallback_mode=conversational_under_supported]", str(captured["user_prompt"]))
        self.assertIsNotNone(captured["governance"])
        self.assertEqual(result.output_keepability, "ephemeral_context")
        self.assertEqual(result.promotion_disposition, "not_promotable")
        self.assertEqual(result.reply_text, "Hey there. How can I help?")
        self.assertEqual(result.trace_ref, "fast-greeting-req-fallback")
        self.assertEqual(result.provider_id, "custom")
        self.assertEqual(result.provider_execution_transport, "direct_http")
        self.assertEqual(result.evidence_summary, "status=under_supported provider_fallback=direct_http_chat")

    def test_render_direct_provider_chat_fallback_adds_startup_operator_contract_for_startup_chip(self) -> None:
        provider = RuntimeProviderResolution(
            provider_id="custom",
            provider_kind="custom",
            auth_profile_id="custom:default",
            auth_method="api_key_env",
            api_mode="chat_completions",
            execution_transport="direct_http",
            base_url="https://api.minimax.io/v1",
            default_model="MiniMax-M2.7",
            secret_ref=None,
            secret_value="secret",
            source="config+env",
        )
        captured: dict[str, object] = {}

        def fake_direct_provider_prompt(*, provider, system_prompt: str, user_prompt: str, governance=None):
            captured["system_prompt"] = system_prompt
            captured["user_prompt"] = user_prompt
            return {"raw_response": "Focus on in-house teams for now."}

        with patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=fake_direct_provider_prompt,
        ):
            reply = _render_direct_provider_chat_fallback(
                config_manager=self.config_manager,
                state_db=self.state_db,
                provider=provider,
                user_message="Should we drop agencies entirely?",
                channel_kind="telegram",
                attachment_context={
                    "active_chip_keys": ["startup-yc"],
                    "pinned_chip_keys": ["startup-yc"],
                    "active_path_key": "startup-operator",
                },
                active_chip_evaluate={
                    "chip_key": "startup-yc",
                    "task_type": "boundary_detection",
                    "stage": "post_launch",
                    "analysis": "Agencies churn fast. In-house teams activate slower and retain longer.",
                },
            )

        self.assertEqual(reply, "Focus on in-house teams for now.")
        self.assertIn("decisive startup operator", str(captured["system_prompt"]))
        self.assertIn("Do not invent numbers", str(captured["system_prompt"]))
        self.assertIn("Avoid numeric ranges like 3-5 or 2-3", str(captured["system_prompt"]))
        self.assertIn("no bold emphasis", str(captured["system_prompt"]))
        self.assertIn("Should we drop agencies entirely?", str(captured["user_prompt"]))

    def test_render_direct_provider_chat_fallback_adds_telegram_persona_contract(self) -> None:
        provider = RuntimeProviderResolution(
            provider_id="custom",
            provider_kind="custom",
            auth_profile_id="custom:default",
            auth_method="api_key_env",
            api_mode="chat_completions",
            execution_transport="direct_http",
            base_url="https://api.minimax.io/v1",
            default_model="MiniMax-M2.7",
            secret_ref=None,
            secret_value="secret",
            source="config+env",
        )
        captured: dict[str, object] = {}

        def fake_direct_provider_prompt(*, provider, system_prompt: str, user_prompt: str, governance=None):
            captured["system_prompt"] = system_prompt
            captured["user_prompt"] = user_prompt
            return {"raw_response": "Start with the wedge."}

        with patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=fake_direct_provider_prompt,
        ):
            reply = _render_direct_provider_chat_fallback(
                config_manager=self.config_manager,
                state_db=self.state_db,
                provider=provider,
                user_message="How should we answer this founder?",
                channel_kind="telegram",
                attachment_context={
                    "active_chip_keys": [],
                    "pinned_chip_keys": [],
                    "active_path_key": None,
                },
                personality_profile={
                    "traits": {
                        "warmth": 0.7,
                        "directness": 0.8,
                        "playfulness": 0.2,
                        "pacing": 0.75,
                        "assertiveness": 0.7,
                    },
                    "personality_name": "Spark",
                    "agent_persona_name": "Founder Operator",
                    "agent_persona_summary": "Sharp, concise, decision-oriented",
                    "agent_behavioral_rules": [
                        "Keep replies shorter unless asked for depth",
                        "Identify the key split before giving advice",
                    ],
                },
            )

        self.assertEqual(reply, "Start with the wedge.")
        self.assertIn("steady voice of 'Founder Operator'", str(captured["system_prompt"]))
        self.assertIn("Lead with the answer, recommendation, or key split in the first sentence.", str(captured["system_prompt"]))
        self.assertIn("Honor these saved Telegram reply rules", str(captured["system_prompt"]))
        self.assertIn("[Telegram reply contract]", str(captured["user_prompt"]))
        self.assertIn("Sharp, concise, decision-oriented", str(captured["user_prompt"]))
        self.assertIn("Keep replies shorter unless asked for depth.", str(captured["user_prompt"]))

    def test_build_researcher_reply_persists_city_profile_fact_before_bridge_execution(self) -> None:
        self.config_manager.set_path("spark.researcher.enabled", True)
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)
        connect_exit, _, connect_stderr = self.run_cli(
            "auth",
            "connect",
            "custom",
            "--home",
            str(self.home),
            "--api-key",
            "minimax-secret",
            "--model",
            "MiniMax-M2.7",
            "--base-url",
            "https://api.minimax.io/v1",
        )
        self.assertEqual(connect_exit, 0, connect_stderr)

        runtime_root = self.home / "fake-researcher"
        runtime_root.mkdir(parents=True, exist_ok=True)
        config_path = runtime_root / "spark-researcher.project.json"
        config_path.write_text("{}", encoding="utf-8")

        def fake_build_advisory(path: Path, task: str, *, model: str = "generic", limit: int = 4, domain: str | None = None):
            return {
                "guidance": [],
                "epistemic_status": {
                    "status": "under_supported",
                    "packet_stability": {"status": "no_belief_packets"},
                },
                "selected_packet_ids": [],
                "trace_path": "trace:city-under-supported",
            }

        def fake_direct_provider_prompt(*, provider, system_prompt: str, user_prompt: str, governance=None):
            return {"raw_response": "Noted."}

        def fail_execute_with_research(*args, **kwargs):
            raise AssertionError("execute_with_research should not run for direct conversational fallback")

        with patch(
            "spark_intelligence.researcher_bridge.advisory.discover_researcher_runtime_root",
            return_value=(runtime_root, "configured"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.resolve_researcher_config_path",
            return_value=config_path,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._import_build_advisory",
            return_value=fake_build_advisory,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._import_execute_with_research",
            return_value=fail_execute_with_research,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=fake_direct_provider_prompt,
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-city-fact",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-city",
                channel_kind="telegram",
                user_message="I moved to Dubai.",
            )

        self.assertEqual(result.reply_text, "Noted.")
        write_events = latest_events_by_type(self.state_db, event_type="memory_write_requested", limit=10)
        self.assertTrue(write_events)
        observations = (write_events[0]["facts_json"] or {}).get("observations") or []
        self.assertEqual(observations[0]["predicate"], "profile.city")
        self.assertEqual(observations[0]["value"], "Dubai")
        self.assertEqual(observations[0]["text"], "I moved to Dubai.")
        influence_events = latest_events_by_type(self.state_db, event_type="plugin_or_chip_influence_recorded", limit=10)
        self.assertTrue(influence_events)
        detected = (influence_events[0]["facts_json"] or {}).get("detected_profile_fact") or {}
        self.assertEqual(detected.get("predicate"), "profile.city")
        self.assertEqual(detected.get("value"), "Dubai")

    def test_build_researcher_reply_injects_memory_backed_city_fact_for_city_query(self) -> None:
        self.config_manager.set_path("spark.researcher.enabled", True)
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)
        connect_exit, _, connect_stderr = self.run_cli(
            "auth",
            "connect",
            "custom",
            "--home",
            str(self.home),
            "--api-key",
            "minimax-secret",
            "--model",
            "MiniMax-M2.7",
            "--base-url",
            "https://api.minimax.io/v1",
        )
        self.assertEqual(connect_exit, 0, connect_stderr)

        write_profile_fact_to_memory(
            config_manager=self.config_manager,
            state_db=self.state_db,
            human_id="human-1",
            predicate="profile.city",
            value="Dubai",
            evidence_text="I moved to Dubai.",
            fact_name="profile_city",
            session_id="session-city-query",
            turn_id="turn-city-query-write",
            channel_kind="telegram",
        )

        runtime_root = self.home / "fake-researcher"
        runtime_root.mkdir(parents=True, exist_ok=True)
        config_path = runtime_root / "spark-researcher.project.json"
        config_path.write_text("{}", encoding="utf-8")
        captured: dict[str, object] = {}

        def fake_build_advisory(path: Path, task: str, *, model: str = "generic", limit: int = 4, domain: str | None = None):
            return {
                "guidance": [],
                "epistemic_status": {
                    "status": "under_supported",
                    "packet_stability": {"status": "no_belief_packets"},
                },
                "selected_packet_ids": [],
                "trace_path": "trace:city-query",
            }

        def fake_direct_provider_prompt(*, provider, system_prompt: str, user_prompt: str, governance=None):
            captured["user_prompt"] = user_prompt
            return {"raw_response": "You're in Dubai."}

        def fail_execute_with_research(*args, **kwargs):
            raise AssertionError("execute_with_research should not run for direct conversational fallback")

        with patch(
            "spark_intelligence.researcher_bridge.advisory.discover_researcher_runtime_root",
            return_value=(runtime_root, "configured"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.resolve_researcher_config_path",
            return_value=config_path,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._import_build_advisory",
            return_value=fake_build_advisory,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._import_execute_with_research",
            return_value=fail_execute_with_research,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=fake_direct_provider_prompt,
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-city-query",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-city-query",
                channel_kind="telegram",
                user_message="What city do you have for me?",
            )

        self.assertEqual(result.reply_text, "You're in Dubai.")
        self.assertIn("[Memory action: PROFILE_FACT_STATUS]", str(captured["user_prompt"]))
        self.assertIn("city: Dubai", str(captured["user_prompt"]))
        read_events = latest_events_by_type(self.state_db, event_type="memory_read_requested", limit=10)
        self.assertTrue(read_events)
        self.assertEqual((read_events[0]["facts_json"] or {}).get("predicate"), "profile.city")

    def test_build_researcher_reply_prefers_exact_startup_explanation_over_founder_fallback(self) -> None:
        self.config_manager.set_path("spark.researcher.enabled", True)
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        startup_result = SimpleNamespace(
            read_result=SimpleNamespace(
                abstained=False,
                records=[{"answer": "Seedify"}],
                answer_explanation={
                    "answer": "Seedify",
                    "evidence": [{"text": "My startup is Seedify."}],
                },
            )
        )
        founder_result = SimpleNamespace(
            read_result=SimpleNamespace(
                abstained=False,
                records=[{"answer": "Spark Swarm"}],
                answer_explanation={
                    "answer": "Spark Swarm",
                    "evidence": [{"text": "I am the founder of Spark Swarm."}],
                },
            )
        )

        def _explain_side_effect(*, predicate: str, **kwargs):
            if predicate == "profile.startup_name":
                return startup_result
            if predicate == "profile.founder_of":
                return founder_result
            raise AssertionError(f"unexpected predicate {predicate}")

        with patch(
            "spark_intelligence.researcher_bridge.advisory.explain_memory_answer_in_memory",
            side_effect=_explain_side_effect,
        ) as explain_memory, patch(
            "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
            side_effect=AssertionError("provider resolution should not run for direct memory explanation replies"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("provider execution should not run for direct memory explanation replies"),
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-startup-explanation-prefer-exact",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-startup-explanation-prefer-exact",
                channel_kind="telegram",
                user_message="How do you know my startup?",
            )

        self.assertEqual(
            result.reply_text,
            'Because I have a saved memory record from when you said: "My startup is Seedify." You created Seedify.',
        )
        self.assertEqual(explain_memory.call_count, 1)
        self.assertEqual(explain_memory.call_args.kwargs["predicate"], "profile.startup_name")
        bridge_events = latest_events_by_type(self.state_db, event_type="tool_result_received", limit=10)
        self.assertTrue(bridge_events)
        self.assertEqual((bridge_events[0]["facts_json"] or {}).get("read_method"), "explain_answer")

    def test_build_researcher_reply_preserves_uncertainty_for_missing_city_query_fact(self) -> None:
        self.config_manager.set_path("spark.researcher.enabled", True)
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)
        connect_exit, _, connect_stderr = self.run_cli(
            "auth",
            "connect",
            "custom",
            "--home",
            str(self.home),
            "--api-key",
            "minimax-secret",
            "--model",
            "MiniMax-M2.7",
            "--base-url",
            "https://api.minimax.io/v1",
        )
        self.assertEqual(connect_exit, 0, connect_stderr)

        runtime_root = self.home / "fake-researcher"
        runtime_root.mkdir(parents=True, exist_ok=True)
        config_path = runtime_root / "spark-researcher.project.json"
        config_path.write_text("{}", encoding="utf-8")
        captured: dict[str, object] = {}

        def fake_build_advisory(path: Path, task: str, *, model: str = "generic", limit: int = 4, domain: str | None = None):
            return {
                "guidance": [],
                "epistemic_status": {
                    "status": "under_supported",
                    "packet_stability": {"status": "no_belief_packets"},
                },
                "selected_packet_ids": [],
                "trace_path": "trace:city-query-missing",
            }

        def fake_direct_provider_prompt(*, provider, system_prompt: str, user_prompt: str, governance=None):
            captured["user_prompt"] = user_prompt
            return {"raw_response": "I don't currently have a saved city for you."}

        def fail_execute_with_research(*args, **kwargs):
            raise AssertionError("execute_with_research should not run for direct conversational fallback")

        with patch(
            "spark_intelligence.researcher_bridge.advisory.discover_researcher_runtime_root",
            return_value=(runtime_root, "configured"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.resolve_researcher_config_path",
            return_value=config_path,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._import_build_advisory",
            return_value=fake_build_advisory,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._import_execute_with_research",
            return_value=fail_execute_with_research,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=fake_direct_provider_prompt,
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-city-query-missing",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-city-query-missing",
                channel_kind="telegram",
                user_message="What city do you have saved for me?",
            )

        self.assertEqual(result.reply_text, "I don't currently have a saved city for you.")
        self.assertIn("[Memory action: PROFILE_FACT_STATUS_MISSING]", str(captured["user_prompt"]))
        self.assertIn("Do not pretend you know.", str(captured["user_prompt"]))

    def test_build_researcher_reply_persists_timezone_profile_fact_before_bridge_execution(self) -> None:
        self.config_manager.set_path("spark.researcher.enabled", True)
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)
        connect_exit, _, connect_stderr = self.run_cli(
            "auth",
            "connect",
            "custom",
            "--home",
            str(self.home),
            "--api-key",
            "minimax-secret",
            "--model",
            "MiniMax-M2.7",
            "--base-url",
            "https://api.minimax.io/v1",
        )
        self.assertEqual(connect_exit, 0, connect_stderr)

        runtime_root = self.home / "fake-researcher"
        runtime_root.mkdir(parents=True, exist_ok=True)
        config_path = runtime_root / "spark-researcher.project.json"
        config_path.write_text("{}", encoding="utf-8")

        def fake_build_advisory(path: Path, task: str, *, model: str = "generic", limit: int = 4, domain: str | None = None):
            return {
                "guidance": [],
                "epistemic_status": {
                    "status": "under_supported",
                    "packet_stability": {"status": "no_belief_packets"},
                },
                "selected_packet_ids": [],
                "trace_path": "trace:timezone-under-supported",
            }

        def fake_direct_provider_prompt(*, provider, system_prompt: str, user_prompt: str, governance=None):
            return {"raw_response": "Noted."}

        def fail_execute_with_research(*args, **kwargs):
            raise AssertionError("execute_with_research should not run for direct conversational fallback")

        with patch(
            "spark_intelligence.researcher_bridge.advisory.discover_researcher_runtime_root",
            return_value=(runtime_root, "configured"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.resolve_researcher_config_path",
            return_value=config_path,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._import_build_advisory",
            return_value=fake_build_advisory,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._import_execute_with_research",
            return_value=fail_execute_with_research,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=fake_direct_provider_prompt,
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-timezone-fact",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-timezone",
                channel_kind="telegram",
                user_message="My timezone is Asia/Dubai.",
            )

        self.assertEqual(result.reply_text, "Noted.")
        write_events = latest_events_by_type(self.state_db, event_type="memory_write_requested", limit=10)
        self.assertTrue(write_events)
        observations = (write_events[0]["facts_json"] or {}).get("observations") or []
        self.assertEqual(observations[0]["predicate"], "profile.timezone")
        self.assertEqual(observations[0]["value"], "Asia/Dubai")
        influence_events = latest_events_by_type(self.state_db, event_type="plugin_or_chip_influence_recorded", limit=10)
        self.assertTrue(influence_events)
        detected = (influence_events[0]["facts_json"] or {}).get("detected_profile_fact") or {}
        self.assertEqual(detected.get("predicate"), "profile.timezone")
        self.assertEqual(detected.get("value"), "Asia/Dubai")

    def test_build_researcher_reply_injects_memory_backed_timezone_fact_for_timezone_query(self) -> None:
        self.config_manager.set_path("spark.researcher.enabled", True)
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)
        connect_exit, _, connect_stderr = self.run_cli(
            "auth",
            "connect",
            "custom",
            "--home",
            str(self.home),
            "--api-key",
            "minimax-secret",
            "--model",
            "MiniMax-M2.7",
            "--base-url",
            "https://api.minimax.io/v1",
        )
        self.assertEqual(connect_exit, 0, connect_stderr)

        write_profile_fact_to_memory(
            config_manager=self.config_manager,
            state_db=self.state_db,
            human_id="human-1",
            predicate="profile.timezone",
            value="Asia/Dubai",
            evidence_text="My timezone is Asia/Dubai.",
            fact_name="profile_timezone",
            session_id="session-timezone-query",
            turn_id="turn-timezone-query-write",
            channel_kind="telegram",
        )

        runtime_root = self.home / "fake-researcher"
        runtime_root.mkdir(parents=True, exist_ok=True)
        config_path = runtime_root / "spark-researcher.project.json"
        config_path.write_text("{}", encoding="utf-8")
        captured: dict[str, object] = {}

        def fake_build_advisory(path: Path, task: str, *, model: str = "generic", limit: int = 4, domain: str | None = None):
            return {
                "guidance": [],
                "epistemic_status": {
                    "status": "under_supported",
                    "packet_stability": {"status": "no_belief_packets"},
                },
                "selected_packet_ids": [],
                "trace_path": "trace:timezone-query",
            }

        def fake_direct_provider_prompt(*, provider, system_prompt: str, user_prompt: str, governance=None):
            captured["user_prompt"] = user_prompt
            return {"raw_response": "Your timezone is Asia/Dubai."}

        def fail_execute_with_research(*args, **kwargs):
            raise AssertionError("execute_with_research should not run for direct conversational fallback")

        with patch(
            "spark_intelligence.researcher_bridge.advisory.discover_researcher_runtime_root",
            return_value=(runtime_root, "configured"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.resolve_researcher_config_path",
            return_value=config_path,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._import_build_advisory",
            return_value=fake_build_advisory,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._import_execute_with_research",
            return_value=fail_execute_with_research,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=fake_direct_provider_prompt,
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-timezone-query",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-timezone-query",
                channel_kind="telegram",
                user_message="What timezone do you have for me?",
            )

        self.assertEqual(result.reply_text, "Your timezone is Asia/Dubai.")
        self.assertIn("[Memory action: PROFILE_FACT_STATUS]", str(captured["user_prompt"]))
        self.assertIn("timezone: Asia/Dubai", str(captured["user_prompt"]))
        read_events = latest_events_by_type(self.state_db, event_type="memory_read_requested", limit=10)
        self.assertTrue(read_events)
        self.assertEqual((read_events[0]["facts_json"] or {}).get("predicate"), "profile.timezone")

    def test_build_researcher_reply_persists_home_country_profile_fact_before_bridge_execution(self) -> None:
        self.config_manager.set_path("spark.researcher.enabled", True)
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)
        connect_exit, _, connect_stderr = self.run_cli(
            "auth",
            "connect",
            "custom",
            "--home",
            str(self.home),
            "--api-key",
            "minimax-secret",
            "--model",
            "MiniMax-M2.7",
            "--base-url",
            "https://api.minimax.io/v1",
        )
        self.assertEqual(connect_exit, 0, connect_stderr)

        runtime_root = self.home / "fake-researcher"
        runtime_root.mkdir(parents=True, exist_ok=True)
        config_path = runtime_root / "spark-researcher.project.json"
        config_path.write_text("{}", encoding="utf-8")

        def fake_build_advisory(path: Path, task: str, *, model: str = "generic", limit: int = 4, domain: str | None = None):
            return {
                "guidance": [],
                "epistemic_status": {
                    "status": "under_supported",
                    "packet_stability": {"status": "no_belief_packets"},
                },
                "selected_packet_ids": [],
                "trace_path": "trace:country-under-supported",
            }

        def fake_direct_provider_prompt(*, provider, system_prompt: str, user_prompt: str, governance=None):
            return {"raw_response": "Noted."}

        def fail_execute_with_research(*args, **kwargs):
            raise AssertionError("execute_with_research should not run for direct conversational fallback")

        with patch(
            "spark_intelligence.researcher_bridge.advisory.discover_researcher_runtime_root",
            return_value=(runtime_root, "configured"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.resolve_researcher_config_path",
            return_value=config_path,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._import_build_advisory",
            return_value=fake_build_advisory,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._import_execute_with_research",
            return_value=fail_execute_with_research,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=fake_direct_provider_prompt,
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-country-fact",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-country",
                channel_kind="telegram",
                user_message="My country is UAE.",
            )

        self.assertEqual(result.reply_text, "Noted.")
        write_events = latest_events_by_type(self.state_db, event_type="memory_write_requested", limit=10)
        self.assertTrue(write_events)
        observations = (write_events[0]["facts_json"] or {}).get("observations") or []
        self.assertEqual(observations[0]["predicate"], "profile.home_country")
        self.assertEqual(observations[0]["value"], "UAE")
        influence_events = latest_events_by_type(self.state_db, event_type="plugin_or_chip_influence_recorded", limit=10)
        self.assertTrue(influence_events)
        detected = (influence_events[0]["facts_json"] or {}).get("detected_profile_fact") or {}
        self.assertEqual(detected.get("predicate"), "profile.home_country")
        self.assertEqual(detected.get("value"), "UAE")

    def test_build_researcher_reply_injects_memory_backed_home_country_fact_for_country_query(self) -> None:
        self.config_manager.set_path("spark.researcher.enabled", True)
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)
        connect_exit, _, connect_stderr = self.run_cli(
            "auth",
            "connect",
            "custom",
            "--home",
            str(self.home),
            "--api-key",
            "minimax-secret",
            "--model",
            "MiniMax-M2.7",
            "--base-url",
            "https://api.minimax.io/v1",
        )
        self.assertEqual(connect_exit, 0, connect_stderr)

        write_profile_fact_to_memory(
            config_manager=self.config_manager,
            state_db=self.state_db,
            human_id="human-1",
            predicate="profile.home_country",
            value="UAE",
            evidence_text="My country is UAE.",
            fact_name="profile_home_country",
            session_id="session-country-query",
            turn_id="turn-country-query-write",
            channel_kind="telegram",
        )

        runtime_root = self.home / "fake-researcher"
        runtime_root.mkdir(parents=True, exist_ok=True)
        config_path = runtime_root / "spark-researcher.project.json"
        config_path.write_text("{}", encoding="utf-8")
        captured: dict[str, object] = {}

        def fake_build_advisory(path: Path, task: str, *, model: str = "generic", limit: int = 4, domain: str | None = None):
            return {
                "guidance": [],
                "epistemic_status": {
                    "status": "under_supported",
                    "packet_stability": {"status": "no_belief_packets"},
                },
                "selected_packet_ids": [],
                "trace_path": "trace:country-query",
            }

        def fake_direct_provider_prompt(*, provider, system_prompt: str, user_prompt: str, governance=None):
            captured["user_prompt"] = user_prompt
            return {"raw_response": "Your country is UAE."}

        def fail_execute_with_research(*args, **kwargs):
            raise AssertionError("execute_with_research should not run for direct conversational fallback")

        with patch(
            "spark_intelligence.researcher_bridge.advisory.discover_researcher_runtime_root",
            return_value=(runtime_root, "configured"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.resolve_researcher_config_path",
            return_value=config_path,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._import_build_advisory",
            return_value=fake_build_advisory,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._import_execute_with_research",
            return_value=fail_execute_with_research,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=fake_direct_provider_prompt,
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-country-query",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-country-query",
                channel_kind="telegram",
                user_message="What country do you have for me?",
            )

        self.assertEqual(result.reply_text, "Your country is UAE.")
        self.assertIn("[Memory action: PROFILE_FACT_STATUS]", str(captured["user_prompt"]))
        self.assertIn("country: UAE", str(captured["user_prompt"]))
        read_events = latest_events_by_type(self.state_db, event_type="memory_read_requested", limit=10)
        self.assertTrue(read_events)
        self.assertEqual((read_events[0]["facts_json"] or {}).get("predicate"), "profile.home_country")
        bridge_events = latest_events_by_type(self.state_db, event_type="tool_result_received", limit=10)
        self.assertTrue(bridge_events)
        self.assertEqual((bridge_events[0]["facts_json"] or {}).get("read_method"), "explain_answer")
        self.assertTrue(bool((bridge_events[0]["facts_json"] or {}).get("explanation_found")))

    def test_build_researcher_reply_persists_preferred_name_profile_fact_before_bridge_execution(self) -> None:
        self.config_manager.set_path("spark.researcher.enabled", True)
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)
        connect_exit, _, connect_stderr = self.run_cli(
            "auth",
            "connect",
            "custom",
            "--home",
            str(self.home),
            "--api-key",
            "minimax-secret",
            "--model",
            "MiniMax-M2.7",
            "--base-url",
            "https://api.minimax.io/v1",
        )
        self.assertEqual(connect_exit, 0, connect_stderr)

        runtime_root = self.home / "fake-researcher"
        runtime_root.mkdir(parents=True, exist_ok=True)
        config_path = runtime_root / "spark-researcher.project.json"
        config_path.write_text("{}", encoding="utf-8")

        def fake_build_advisory(path: Path, task: str, *, model: str = "generic", limit: int = 4, domain: str | None = None):
            return {
                "guidance": [],
                "epistemic_status": {
                    "status": "under_supported",
                    "packet_stability": {"status": "no_belief_packets"},
                },
                "selected_packet_ids": [],
                "trace_path": "trace:name-under-supported",
            }

        def fake_direct_provider_prompt(*, provider, system_prompt: str, user_prompt: str, governance=None):
            return {"raw_response": "Noted."}

        def fail_execute_with_research(*args, **kwargs):
            raise AssertionError("execute_with_research should not run for direct conversational fallback")

        with patch(
            "spark_intelligence.researcher_bridge.advisory.discover_researcher_runtime_root",
            return_value=(runtime_root, "configured"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.resolve_researcher_config_path",
            return_value=config_path,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._import_build_advisory",
            return_value=fake_build_advisory,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._import_execute_with_research",
            return_value=fail_execute_with_research,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=fake_direct_provider_prompt,
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-name-fact",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-name",
                channel_kind="telegram",
                user_message="My name is Sarah.",
            )

        self.assertEqual(result.reply_text, "Noted.")
        write_events = latest_events_by_type(self.state_db, event_type="memory_write_requested", limit=10)
        self.assertTrue(write_events)
        observations = (write_events[0]["facts_json"] or {}).get("observations") or []
        self.assertEqual(observations[0]["predicate"], "profile.preferred_name")
        self.assertEqual(observations[0]["value"], "Sarah")
        influence_events = latest_events_by_type(self.state_db, event_type="plugin_or_chip_influence_recorded", limit=10)
        self.assertTrue(influence_events)
        detected = (influence_events[0]["facts_json"] or {}).get("detected_profile_fact") or {}
        self.assertEqual(detected.get("predicate"), "profile.preferred_name")
        self.assertEqual(detected.get("value"), "Sarah")

    def test_build_researcher_reply_injects_memory_backed_preferred_name_for_name_query(self) -> None:
        self.config_manager.set_path("spark.researcher.enabled", True)
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)
        connect_exit, _, connect_stderr = self.run_cli(
            "auth",
            "connect",
            "custom",
            "--home",
            str(self.home),
            "--api-key",
            "minimax-secret",
            "--model",
            "MiniMax-M2.7",
            "--base-url",
            "https://api.minimax.io/v1",
        )
        self.assertEqual(connect_exit, 0, connect_stderr)

        write_profile_fact_to_memory(
            config_manager=self.config_manager,
            state_db=self.state_db,
            human_id="human-1",
            predicate="profile.preferred_name",
            value="Sarah",
            evidence_text="My name is Sarah.",
            fact_name="profile_preferred_name",
            session_id="session-name-query",
            turn_id="turn-name-query-write",
            channel_kind="telegram",
        )

        runtime_root = self.home / "fake-researcher"
        runtime_root.mkdir(parents=True, exist_ok=True)
        config_path = runtime_root / "spark-researcher.project.json"
        config_path.write_text("{}", encoding="utf-8")
        captured: dict[str, object] = {}

        def fake_build_advisory(path: Path, task: str, *, model: str = "generic", limit: int = 4, domain: str | None = None):
            return {
                "guidance": [],
                "epistemic_status": {
                    "status": "under_supported",
                    "packet_stability": {"status": "no_belief_packets"},
                },
                "selected_packet_ids": [],
                "trace_path": "trace:name-query",
            }

        def fake_direct_provider_prompt(*, provider, system_prompt: str, user_prompt: str, governance=None):
            captured["user_prompt"] = user_prompt
            return {"raw_response": "Your name is Sarah."}

        def fail_execute_with_research(*args, **kwargs):
            raise AssertionError("execute_with_research should not run for direct conversational fallback")

        with patch(
            "spark_intelligence.researcher_bridge.advisory.discover_researcher_runtime_root",
            return_value=(runtime_root, "configured"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.resolve_researcher_config_path",
            return_value=config_path,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._import_build_advisory",
            return_value=fake_build_advisory,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._import_execute_with_research",
            return_value=fail_execute_with_research,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=fake_direct_provider_prompt,
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-name-query",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-name-query",
                channel_kind="telegram",
                user_message="What name do you have for me?",
            )

        self.assertEqual(result.reply_text, "Your name is Sarah.")
        self.assertIn("[Memory action: PROFILE_FACT_STATUS]", str(captured["user_prompt"]))
        self.assertIn("name: Sarah", str(captured["user_prompt"]))
        read_events = latest_events_by_type(self.state_db, event_type="memory_read_requested", limit=10)
        self.assertTrue(read_events)
        self.assertEqual((read_events[0]["facts_json"] or {}).get("predicate"), "profile.preferred_name")

    def test_build_researcher_reply_injects_memory_backed_startup_for_startup_query(self) -> None:
        self.config_manager.set_path("spark.researcher.enabled", True)
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)
        with patch(
            "spark_intelligence.researcher_bridge.advisory.lookup_current_state_in_memory",
            return_value=SimpleNamespace(
                read_result=SimpleNamespace(
                    abstained=False,
                    records=[
                        {
                            "predicate": "profile.startup_name",
                            "normalized_value": "Seedify",
                            "value": "Seedify",
                        }
                    ],
                )
            ),
        ) as lookup_mock, patch(
            "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
            side_effect=AssertionError("provider resolution should not run for direct memory fact replies"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("provider execution should not run for direct memory fact replies"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._load_recent_conversation_context",
            side_effect=AssertionError("recent conversation context should not run for direct memory fact replies"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.build_system_registry_prompt_context",
            side_effect=AssertionError("system registry context should not run for direct memory fact replies"),
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-startup-query",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-startup-query",
                channel_kind="telegram",
                user_message="What startup did I create?",
            )

        self.assertEqual(result.reply_text, "You created Seedify.")
        self.assertEqual(result.mode, "memory_profile_fact")
        self.assertEqual(result.routing_decision, "memory_profile_fact_query")
        lookup_mock.assert_any_call(
            config_manager=self.config_manager,
            state_db=self.state_db,
            subject="human:human-1",
            predicate="profile.startup_name",
            actor_id=ANY,
        )

    def test_build_researcher_reply_injects_identity_summary_from_memory_for_who_am_i_query(self) -> None:
        self.config_manager.set_path("spark.researcher.enabled", True)
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        write_profile_fact_to_memory(
            config_manager=self.config_manager,
            state_db=self.state_db,
            human_id="human-1",
            predicate="profile.occupation",
            value="entrepreneur",
            evidence_text="I am an entrepreneur.",
            fact_name="profile_occupation",
            session_id="session-identity-query",
            turn_id="turn-identity-query-write-1",
            channel_kind="telegram",
        )
        write_profile_fact_to_memory(
            config_manager=self.config_manager,
            state_db=self.state_db,
            human_id="human-1",
            predicate="profile.startup_name",
            value="Seedify",
            evidence_text="My startup is Seedify.",
            fact_name="profile_startup_name",
            session_id="session-identity-query",
            turn_id="turn-identity-query-write-2",
            channel_kind="telegram",
        )
        write_profile_fact_to_memory(
            config_manager=self.config_manager,
            state_db=self.state_db,
            human_id="human-1",
            predicate="profile.current_mission",
            value="survive the hack and revive the companies",
            evidence_text="I am trying to survive the hack and revive the companies.",
            fact_name="profile_current_mission",
            session_id="session-identity-query",
            turn_id="turn-identity-query-write-3",
            channel_kind="telegram",
        )

        with patch(
            "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
            side_effect=AssertionError("provider resolution should not run for direct identity replies"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("provider execution should not run for direct identity replies"),
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-identity-query",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-identity-query",
                channel_kind="telegram",
                user_message="Who am I?",
            )

        self.assertEqual(
            result.reply_text,
            "You're an entrepreneur. Your startup is Seedify. Your current mission is to survive the hack and revive the companies.",
        )
        self.assertEqual(result.mode, "memory_profile_identity")
        self.assertEqual(result.routing_decision, "memory_profile_identity_summary")
        read_events = latest_events_by_type(self.state_db, event_type="memory_read_requested", limit=10)
        self.assertTrue(read_events)
        read_methods = {str((event["facts_json"] or {}).get("method") or "") for event in read_events[:2]}
        self.assertIn("get_current_state", read_methods)
        bridge_events = latest_events_by_type(self.state_db, event_type="tool_result_received", limit=10)
        self.assertTrue(bridge_events)
        self.assertEqual((bridge_events[0]["facts_json"] or {}).get("read_method"), "get_current_state+retrieve_evidence")

    def test_build_researcher_reply_uses_identity_evidence_when_current_state_is_empty(self) -> None:
        self.config_manager.set_path("spark.researcher.enabled", True)
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        inspection_result = SimpleNamespace(
            read_result=SimpleNamespace(
                abstained=False,
                records=[],
            )
        )
        evidence_result = SimpleNamespace(
            read_result=SimpleNamespace(
                abstained=False,
                records=[
                    {"predicate": "profile.occupation", "value": "entrepreneur"},
                    {"predicate": "profile.startup_name", "value": "Seedify"},
                ],
            )
        )

        with patch(
            "spark_intelligence.researcher_bridge.advisory.inspect_human_memory_in_memory",
            return_value=inspection_result,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.retrieve_memory_evidence_in_memory",
            return_value=evidence_result,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
            side_effect=AssertionError("provider resolution should not run for direct identity replies"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("provider execution should not run for direct identity replies"),
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-identity-query-evidence",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-identity-query",
                channel_kind="telegram",
                user_message="What do you remember about me?",
            )

        self.assertEqual(result.reply_text, "You're an entrepreneur. Your startup is Seedify.")
        self.assertEqual(result.mode, "memory_profile_identity")
        self.assertEqual(result.routing_decision, "memory_profile_identity_summary")

    def test_build_researcher_reply_answers_single_fact_mission_query_directly_from_memory(self) -> None:
        self.config_manager.set_path("spark.researcher.enabled", True)
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        with patch(
            "spark_intelligence.researcher_bridge.advisory.lookup_current_state_in_memory",
            return_value=SimpleNamespace(
                read_result=SimpleNamespace(
                    abstained=False,
                    records=[
                        {
                            "predicate": "profile.current_mission",
                            "normalized_value": "survive the hack and revive the companies",
                            "value": "survive the hack and revive the companies",
                        }
                    ],
                )
            ),
        ) as lookup_mock, patch(
            "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
            side_effect=AssertionError("provider resolution should not run for direct memory fact replies"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("provider execution should not run for direct memory fact replies"),
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-mission-query-direct",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-mission-query",
                channel_kind="telegram",
                user_message="What am I trying to do now?",
            )

        self.assertEqual(result.reply_text, "Right now you're trying to survive the hack and revive the companies.")
        self.assertEqual(result.mode, "memory_profile_fact")
        self.assertEqual(result.routing_decision, "memory_profile_fact_query")
        lookup_mock.assert_any_call(
            config_manager=self.config_manager,
            state_db=self.state_db,
            subject="human:human-1",
            predicate="profile.current_mission",
            actor_id=ANY,
        )

    def test_build_researcher_reply_falls_back_to_inspection_for_single_fact_query_when_lookup_is_empty(self) -> None:
        self.config_manager.set_path("spark.researcher.enabled", True)
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        lookup_result = SimpleNamespace(
            read_result=SimpleNamespace(
                abstained=False,
                records=[],
            )
        )
        inspection_result = SimpleNamespace(
            read_result=SimpleNamespace(
                abstained=False,
                records=[
                    {
                        "predicate": "profile.current_mission",
                        "value": "survive the hack and revive the companies",
                        "timestamp": "2026-04-10T10:00:00+00:00",
                        "turn_ids": ["turn-mission-write"],
                    }
                ],
            )
        )

        with patch(
            "spark_intelligence.researcher_bridge.advisory.lookup_current_state_in_memory",
            return_value=lookup_result,
        ) as lookup_mock, patch(
            "spark_intelligence.researcher_bridge.advisory.inspect_human_memory_in_memory",
            return_value=inspection_result,
        ) as inspect_mock, patch(
            "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
            side_effect=AssertionError("provider resolution should not run for inspection-backed memory replies"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("provider execution should not run for inspection-backed memory replies"),
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-mission-query-inspection",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-mission-query",
                channel_kind="telegram",
                user_message="What am I trying to do now?",
            )

        self.assertEqual(result.reply_text, "Right now you're trying to survive the hack and revive the companies.")
        self.assertEqual(result.mode, "memory_profile_fact")
        self.assertEqual(result.routing_decision, "memory_profile_fact_query")
        lookup_mock.assert_any_call(
            config_manager=self.config_manager,
            state_db=self.state_db,
            subject="human:human-1",
            predicate="profile.current_mission",
            actor_id=ANY,
        )
        inspect_mock.assert_called_once()

    def test_build_researcher_reply_falls_back_to_inspection_for_profile_fact_explanation(self) -> None:
        self.config_manager.set_path("spark.researcher.enabled", True)
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        explanation_result = SimpleNamespace(
            read_result=SimpleNamespace(
                abstained=False,
                records=[],
                answer_explanation={},
            )
        )
        inspection_result = SimpleNamespace(
            read_result=SimpleNamespace(
                abstained=False,
                records=[
                    {
                        "predicate": "profile.current_mission",
                        "value": "survive the hack and revive the companies",
                        "timestamp": "2026-04-10T10:00:00+00:00",
                        "turn_ids": ["turn-mission-write"],
                    }
                ],
            )
        )

        with patch(
            "spark_intelligence.researcher_bridge.advisory.explain_memory_answer_in_memory",
            return_value=explanation_result,
        ) as explain_mock, patch(
            "spark_intelligence.researcher_bridge.advisory.inspect_human_memory_in_memory",
            return_value=inspection_result,
        ) as inspect_mock, patch(
            "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
            side_effect=AssertionError("provider resolution should not run for inspection-backed explanation replies"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("provider execution should not run for inspection-backed explanation replies"),
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-mission-explanation-inspection",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-mission-explanation",
                channel_kind="telegram",
                user_message="How do you know what I'm trying to do now?",
            )

        self.assertIn("saved memory record", result.reply_text)
        self.assertIn("survive the hack and revive the companies", result.reply_text)
        self.assertEqual(result.mode, "memory_profile_fact_explanation")
        self.assertEqual(result.routing_decision, "memory_profile_fact_explanation")
        explain_mock.assert_called_once()
        inspect_mock.assert_called_once()

    def test_build_researcher_reply_answers_hack_actor_query_directly_from_memory(self) -> None:
        self.config_manager.set_path("spark.researcher.enabled", True)
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        with patch(
            "spark_intelligence.researcher_bridge.advisory.lookup_current_state_in_memory",
            return_value=SimpleNamespace(
                read_result=SimpleNamespace(
                    abstained=False,
                    records=[
                        {
                            "predicate": "profile.hack_actor",
                            "normalized_value": "North Korea",
                            "value": "North Korea",
                        }
                    ],
                )
            ),
        ) as lookup_mock, patch(
            "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
            side_effect=AssertionError("provider resolution should not run for direct memory fact replies"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("provider execution should not run for direct memory fact replies"),
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-hack-actor-query-direct",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-hack-actor-query",
                channel_kind="telegram",
                user_message="Who hacked us?",
            )

        self.assertEqual(result.reply_text, "The hack actor was North Korea.")
        self.assertEqual(result.mode, "memory_profile_fact")
        self.assertEqual(result.routing_decision, "memory_profile_fact_query")
        lookup_mock.assert_any_call(
            config_manager=self.config_manager,
            state_db=self.state_db,
            subject="human:human-1",
            predicate="profile.hack_actor",
            actor_id=ANY,
        )

    def test_build_researcher_reply_answers_spark_role_query_directly_from_memory(self) -> None:
        self.config_manager.set_path("spark.researcher.enabled", True)
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        with patch(
            "spark_intelligence.researcher_bridge.advisory.lookup_current_state_in_memory",
            return_value=SimpleNamespace(
                read_result=SimpleNamespace(
                    abstained=False,
                    records=[
                        {
                            "predicate": "profile.spark_role",
                            "normalized_value": "important part of the rebuild",
                            "value": "important part of the rebuild",
                        }
                    ],
                )
            ),
        ) as lookup_mock, patch(
            "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
            side_effect=AssertionError("provider resolution should not run for direct memory fact replies"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("provider execution should not run for direct memory fact replies"),
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-spark-role-query-direct",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-spark-role-query",
                channel_kind="telegram",
                user_message="What role will Spark play in this?",
            )

        self.assertEqual(result.reply_text, "Spark will be an important part of the rebuild.")
        self.assertEqual(result.mode, "memory_profile_fact")
        self.assertEqual(result.routing_decision, "memory_profile_fact_query")
        lookup_mock.assert_any_call(
            config_manager=self.config_manager,
            state_db=self.state_db,
            subject="human:human-1",
            predicate="profile.spark_role",
            actor_id=ANY,
        )

    def test_build_researcher_reply_answers_founder_query_directly_from_memory(self) -> None:
        self.config_manager.set_path("spark.researcher.enabled", True)
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        with patch(
            "spark_intelligence.researcher_bridge.advisory.lookup_current_state_in_memory",
            return_value=SimpleNamespace(
                read_result=SimpleNamespace(
                    abstained=False,
                    records=[
                        {
                            "predicate": "profile.founder_of",
                            "normalized_value": "Spark Swarm",
                            "value": "Spark Swarm",
                        }
                    ],
                )
            ),
        ) as lookup_mock, patch(
            "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
            side_effect=AssertionError("provider resolution should not run for direct memory fact replies"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("provider execution should not run for direct memory fact replies"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._load_recent_conversation_context",
            side_effect=AssertionError("recent conversation context should not run for direct memory fact replies"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.build_system_registry_prompt_context",
            side_effect=AssertionError("system registry context should not run for direct memory fact replies"),
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-founder-query-direct",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-founder-query",
                channel_kind="telegram",
                user_message="What company did I found?",
            )

        self.assertEqual(result.reply_text, "You founded Spark Swarm.")
        self.assertEqual(result.mode, "memory_profile_fact")
        self.assertEqual(result.routing_decision, "memory_profile_fact_query")
        lookup_mock.assert_any_call(
            config_manager=self.config_manager,
            state_db=self.state_db,
            subject="human:human-1",
            predicate="profile.founder_of",
            actor_id=ANY,
        )

    def test_build_researcher_reply_answers_occupation_query_directly_from_memory(self) -> None:
        self.config_manager.set_path("spark.researcher.enabled", True)
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        with patch(
            "spark_intelligence.researcher_bridge.advisory.lookup_current_state_in_memory",
            return_value=SimpleNamespace(
                read_result=SimpleNamespace(
                    abstained=False,
                    records=[
                        {
                            "predicate": "profile.occupation",
                            "normalized_value": "entrepreneur",
                            "value": "entrepreneur",
                        }
                    ],
                )
            ),
        ) as lookup_mock, patch(
            "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
            side_effect=AssertionError("provider resolution should not run for direct memory fact replies"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("provider execution should not run for direct memory fact replies"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._load_recent_conversation_context",
            side_effect=AssertionError("recent conversation context should not run for direct memory fact replies"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.build_system_registry_prompt_context",
            side_effect=AssertionError("system registry context should not run for direct memory fact replies"),
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-occupation-query-direct",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-occupation-query",
                channel_kind="telegram",
                user_message="What is my occupation?",
            )

        self.assertEqual(result.reply_text, "You're an entrepreneur.")
        self.assertEqual(result.mode, "memory_profile_fact")
        self.assertEqual(result.routing_decision, "memory_profile_fact_query")
        lookup_mock.assert_any_call(
            config_manager=self.config_manager,
            state_db=self.state_db,
            subject="human:human-1",
            predicate="profile.occupation",
            actor_id=ANY,
        )

    def test_build_researcher_reply_answers_missing_country_query_directly_from_memory(self) -> None:
        self.config_manager.set_path("spark.researcher.enabled", True)
        self.config_manager.set_path("spark.memory.enabled", True)
        self.config_manager.set_path("spark.memory.shadow_mode", False)

        with patch(
            "spark_intelligence.researcher_bridge.advisory._resolve_bridge_provider",
            side_effect=AssertionError("provider resolution should not run for direct memory fact replies"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("provider execution should not run for direct memory fact replies"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._load_recent_conversation_context",
            side_effect=AssertionError("recent conversation context should not run for direct memory fact replies"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.build_system_registry_prompt_context",
            side_effect=AssertionError("system registry context should not run for direct memory fact replies"),
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-country-query-direct-missing",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-country-query-missing",
                channel_kind="telegram",
                user_message="What country do I live in?",
            )

        self.assertEqual(result.reply_text, "I don't currently have that saved.")
        self.assertEqual(result.mode, "memory_profile_fact")
        self.assertEqual(result.routing_decision, "memory_profile_fact_query")
        read_events = latest_events_by_type(self.state_db, event_type="memory_read_requested", limit=10)
        self.assertTrue(read_events)
        self.assertEqual((read_events[0]["facts_json"] or {}).get("predicate"), "profile.home_country")
    def test_build_researcher_reply_appends_swarm_recommendation_for_explicit_delegation(self) -> None:
        self.config_manager.set_path("spark.researcher.enabled", True)
        connect_exit, _, connect_stderr = self.run_cli(
            "auth",
            "connect",
            "custom",
            "--home",
            str(self.home),
            "--api-key",
            "minimax-secret",
            "--model",
            "MiniMax-M2.7",
            "--base-url",
            "https://api.minimax.io/v1",
        )
        self.assertEqual(connect_exit, 0, connect_stderr)

        runtime_root = self.home / "fake-researcher"
        runtime_root.mkdir(parents=True, exist_ok=True)
        config_path = runtime_root / "spark-researcher.project.json"
        config_path.write_text("{}", encoding="utf-8")

        def fake_build_advisory(path: Path, task: str, *, model: str = "generic", limit: int = 4, domain: str | None = None):
            return {
                "guidance": ["Use evidence-backed guidance."],
                "epistemic_status": {"status": "grounded", "packet_stability": {"status": "durable_supported"}},
                "selected_packet_ids": ["packet-1"],
                "trace_path": "trace:test",
            }

        def fake_execute_with_research(*args, **kwargs):
            return {
                "status": "ok",
                "decision": "approve",
                "response": {"raw_response": "We should break this into steps and assign owners."},
                "trace_path": "trace:execution",
            }

        with patch(
            "spark_intelligence.researcher_bridge.advisory.discover_researcher_runtime_root",
            return_value=(runtime_root, "configured"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.resolve_researcher_config_path",
            return_value=config_path,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._import_build_advisory",
            return_value=fake_build_advisory,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._import_execute_with_research",
            return_value=fake_execute_with_research,
        ), patch(
            "spark_intelligence.swarm_bridge.evaluate_swarm_escalation",
            return_value=type(
                "Decision",
                (),
                {
                    "escalate": True,
                    "mode": "manual_recommended",
                    "triggers": ["explicit_swarm", "parallel_work"],
                },
            )(),
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-swarm-recommend",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-1",
                channel_kind="telegram",
                user_message="Please delegate this as parallel swarm work.",
            )

        self.assertEqual(result.escalation_hint, "manual_recommended")
        self.assertEqual(result.routing_decision, "provider_execution+manual_recommended")
        self.assertIn("Swarm: recommended for this task", result.reply_text)
        self.assertIn("swarm=manual_recommended", result.evidence_summary)

    def test_build_researcher_reply_appends_swarm_recommendation_to_fallback_chat(self) -> None:
        self.config_manager.set_path("spark.researcher.enabled", True)
        connect_exit, _, connect_stderr = self.run_cli(
            "auth",
            "connect",
            "custom",
            "--home",
            str(self.home),
            "--api-key",
            "minimax-secret",
            "--model",
            "MiniMax-M2.7",
            "--base-url",
            "https://api.minimax.io/v1",
        )
        self.assertEqual(connect_exit, 0, connect_stderr)

        runtime_root = self.home / "fake-researcher"
        runtime_root.mkdir(parents=True, exist_ok=True)
        config_path = runtime_root / "spark-researcher.project.json"
        config_path.write_text("{}", encoding="utf-8")

        def fake_build_advisory(path: Path, task: str, *, model: str = "generic", limit: int = 4, domain: str | None = None):
            return {
                "guidance": [],
                "epistemic_status": {
                    "status": "under_supported",
                    "packet_stability": {"status": "no_belief_packets"},
                },
                "selected_packet_ids": [],
                "trace_path": "trace:under-supported",
            }

        with patch(
            "spark_intelligence.researcher_bridge.advisory.discover_researcher_runtime_root",
            return_value=(runtime_root, "configured"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.resolve_researcher_config_path",
            return_value=config_path,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._import_build_advisory",
            return_value=fake_build_advisory,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            return_value={"raw_response": "We can do that."},
        ), patch(
            "spark_intelligence.swarm_bridge.evaluate_swarm_escalation",
            return_value=type(
                "Decision",
                (),
                {
                    "escalate": True,
                    "mode": "manual_recommended",
                    "triggers": ["explicit_swarm"],
                },
            )(),
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-swarm-fallback",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-1",
                channel_kind="telegram",
                user_message="swarm this",
            )

        self.assertEqual(result.escalation_hint, "manual_recommended")
        self.assertEqual(result.routing_decision, "provider_fallback_chat+manual_recommended")
        self.assertIn("Swarm: recommended for this task", result.reply_text)
        self.assertIn("swarm=manual_recommended", result.evidence_summary)

    def test_build_researcher_reply_respects_disabled_conversational_fallback_policy(self) -> None:
        self.config_manager.set_path("spark.researcher.enabled", True)
        self.config_manager.set_path("spark.researcher.routing.conversational_fallback_enabled", False)
        connect_exit, _, connect_stderr = self.run_cli(
            "auth",
            "connect",
            "custom",
            "--home",
            str(self.home),
            "--api-key",
            "minimax-secret",
            "--model",
            "MiniMax-M2.7",
            "--base-url",
            "https://api.minimax.io/v1",
        )
        self.assertEqual(connect_exit, 0, connect_stderr)

        runtime_root = self.home / "fake-researcher"
        runtime_root.mkdir(parents=True, exist_ok=True)
        config_path = runtime_root / "spark-researcher.project.json"
        config_path.write_text("{}", encoding="utf-8")

        def fake_build_advisory(path: Path, task: str, *, model: str = "generic", limit: int = 4, domain: str | None = None):
            return {
                "guidance": [],
                "epistemic_status": {
                    "status": "under_supported",
                    "packet_stability": {"status": "no_belief_packets"},
                },
                "selected_packet_ids": [],
                "trace_path": "trace:under-supported",
            }

        def fake_execute_with_research(*args, **kwargs):
            return {
                "status": "ok",
                "decision": "approve",
                "response": {"raw_response": "Researcher-side provider execution reply"},
                "trace_path": "trace:execution",
            }

        with patch(
            "spark_intelligence.researcher_bridge.advisory.discover_researcher_runtime_root",
            return_value=(runtime_root, "configured"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.resolve_researcher_config_path",
            return_value=config_path,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._import_build_advisory",
            return_value=fake_build_advisory,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._import_execute_with_research",
            return_value=fake_execute_with_research,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.execute_direct_provider_prompt",
            side_effect=AssertionError("direct provider fallback should be disabled"),
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-fallback-disabled",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-1",
                channel_kind="telegram",
                user_message="hey",
            )

        self.assertEqual(result.routing_decision, "provider_execution")
        self.assertEqual(result.reply_text, "Researcher-side provider execution reply")

    def test_build_researcher_reply_uses_resolved_provider_model_family(self) -> None:
        self.config_manager.set_path("spark.researcher.enabled", True)
        connect_exit, _, connect_stderr = self.run_cli(
            "auth",
            "connect",
            "anthropic",
            "--home",
            str(self.home),
            "--api-key",
            "anthropic-secret",
            "--model",
            "claude-opus-4-6",
        )
        self.assertEqual(connect_exit, 0, connect_stderr)

        runtime_root = self.home / "fake-researcher"
        runtime_root.mkdir(parents=True, exist_ok=True)
        config_path = runtime_root / "spark-researcher.project.json"
        config_path.write_text("{}", encoding="utf-8")
        captured: dict[str, str] = {}
        captured_execution: dict[str, object] = {}

        def fake_build_advisory(path: Path, task: str, *, model: str = "generic", limit: int = 4, domain: str | None = None):
            captured["model"] = model
            return {
                "guidance": ["Use evidence-backed guidance."],
                "epistemic_status": {"status": "grounded", "packet_stability": {"status": "durable_supported"}},
                "selected_packet_ids": ["packet-1"],
                "trace_path": "trace:test",
            }

        def fake_execute_with_research(
            runtime_root: Path,
            *,
            advisory: dict[str, object],
            model: str,
            command_override: list[str] | None = None,
            dry_run: bool = False,
        ) -> dict[str, object]:
            captured_execution["model"] = model
            captured_execution["command_override"] = list(command_override or [])
            return {
                "status": "ok",
                "decision": "approve",
                "response": {"raw_response": "Provider-backed reply"},
                "trace_path": "trace:execution",
            }

        with patch(
            "spark_intelligence.researcher_bridge.advisory.discover_researcher_runtime_root",
            return_value=(runtime_root, "configured"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.resolve_researcher_config_path",
            return_value=config_path,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._import_build_advisory",
            return_value=fake_build_advisory,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._import_execute_with_research",
            return_value=fake_execute_with_research,
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-1",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-1",
                channel_kind="telegram",
                user_message="How should I answer this?",
            )

        self.assertEqual(captured["model"], "claude")
        self.assertEqual(captured_execution["model"], "claude")
        self.assertEqual(
            captured_execution["command_override"],
            [
                sys.executable,
                "-m",
                "spark_intelligence.llm.provider_wrapper",
                "{system_prompt_path}",
                "{user_prompt_path}",
                "{response_path}",
            ],
        )
        self.assertEqual(result.mode, "external_configured")
        self.assertEqual(result.reply_text, "Provider-backed reply")
        self.assertEqual(result.provider_id, "anthropic")
        self.assertEqual(result.provider_auth_method, "api_key_env")
        self.assertEqual(result.provider_model, "claude-opus-4-6")
        self.assertEqual(result.provider_model_family, "claude")
        self.assertEqual(result.provider_execution_transport, "direct_http")

    def test_build_researcher_reply_uses_waiting_message_for_research_needed_without_clarifiers(self) -> None:
        self.config_manager.set_path("spark.researcher.enabled", True)
        connect_exit, _, connect_stderr = self.run_cli(
            "auth",
            "connect",
            "anthropic",
            "--home",
            str(self.home),
            "--api-key",
            "anthropic-secret",
            "--model",
            "claude-opus-4-6",
        )
        self.assertEqual(connect_exit, 0, connect_stderr)

        runtime_root = self.home / "fake-researcher"
        runtime_root.mkdir(parents=True, exist_ok=True)
        config_path = runtime_root / "spark-researcher.project.json"
        config_path.write_text("{}", encoding="utf-8")
        observed_queries: dict[str, str] = {}

        def fake_build_advisory(path: Path, task: str, *, model: str = "generic", limit: int = 4, domain: str | None = None):
            return {
                "guidance": [],
                "epistemic_status": {"status": "under_supported", "packet_stability": {"status": "no_belief_packets"}},
                "selected_packet_ids": [],
                "intent": {"resource_modes": ["web"], "query": task},
                "trace_path": "trace:test",
            }

        def fake_execute_with_research(
            runtime_root: Path,
            *,
            advisory: dict[str, object],
            model: str,
            command_override: list[str] | None = None,
            dry_run: bool = False,
        ) -> dict[str, object]:
            intent = advisory.get("intent")
            if isinstance(intent, dict):
                observed_queries["intent_query"] = str(intent.get("query") or "")
            observed_queries["original_user_message"] = str(advisory.get("original_user_message") or "")
            return {
                "status": "research_needed",
                "decision": "research_needed",
                "research_query": str(advisory.get("original_user_message") or ""),
                "clarifying_questions": [],
                "trace_path": "trace:execution",
            }

        with patch(
            "spark_intelligence.researcher_bridge.advisory.discover_researcher_runtime_root",
            return_value=(runtime_root, "configured"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.resolve_researcher_config_path",
            return_value=config_path,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._import_build_advisory",
            return_value=fake_build_advisory,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._import_execute_with_research",
            return_value=fake_execute_with_research,
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-research-needed",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-1",
                channel_kind="telegram",
                user_message="Search the web and tell me the current BTC price in USD with the source you used.",
            )

        self.assertEqual(result.routing_decision, "provider_execution")
        self.assertEqual(result.evidence_summary, "status=research_needed provider_execution=yes")
        self.assertIn("I need live web evidence before I answer that", result.reply_text)
        self.assertIn("Search the web and tell me the current BTC price in USD with the source you used.", result.reply_text)
        self.assertEqual(
            observed_queries["intent_query"],
            "Search the web and tell me the current BTC price in USD with the source you used.",
        )
        self.assertEqual(
            observed_queries["original_user_message"],
            "Search the web and tell me the current BTC price in USD with the source you used.",
        )

    def test_build_researcher_reply_keeps_codex_on_external_wrapper_transport(self) -> None:
        self.config_manager.set_path("spark.researcher.enabled", True)
        start_exit, start_stdout, start_stderr = self.run_cli(
            "auth",
            "login",
            "openai-codex",
            "--home",
            str(self.home),
            "--json",
        )
        self.assertEqual(start_exit, 0, start_stderr)

        import json

        start_payload = json.loads(start_stdout)
        callback_url = (
            "http://127.0.0.1:1455/auth/callback"
            f"?state={start_payload['callback_state']}&code=test-oauth-code"
        )

        with patch(
            "spark_intelligence.auth.service.exchange_oauth_authorization_code",
            return_value={
                "access_token": "oauth-access-token",
                "refresh_token": "oauth-refresh-token",
                "expires_in": 3600,
            },
        ):
            complete_exit, _, complete_stderr = self.run_cli(
                "auth",
                "login",
                "openai-codex",
                "--home",
                str(self.home),
                "--callback-url",
                callback_url,
            )
        self.assertEqual(complete_exit, 0, complete_stderr)

        runtime_root = self.home / "fake-researcher"
        runtime_root.mkdir(parents=True, exist_ok=True)
        config_path = runtime_root / "spark-researcher.project.json"
        config_path.write_text("{}", encoding="utf-8")
        captured: dict[str, object] = {}

        def fake_build_advisory(path: Path, task: str, *, model: str = "generic", limit: int = 4, domain: str | None = None):
            captured["advisory_model"] = model
            return {
                "guidance": ["Use evidence-backed guidance."],
                "epistemic_status": {"status": "grounded", "packet_stability": {"status": "durable_supported"}},
                "selected_packet_ids": ["packet-1"],
                "trace_path": "trace:test",
            }

        def fake_execute_with_research(
            runtime_root: Path,
            *,
            advisory: dict[str, object],
            model: str,
            command_override: list[str] | None = None,
            dry_run: bool = False,
        ) -> dict[str, object]:
            captured["execution_model"] = model
            captured["command_override"] = command_override
            return {
                "status": "ok",
                "decision": "approve",
                "response": {"raw_response": "Codex-backed reply"},
                "trace_path": "trace:execution",
            }

        with patch(
            "spark_intelligence.researcher_bridge.advisory.discover_researcher_runtime_root",
            return_value=(runtime_root, "configured"),
        ), patch(
            "spark_intelligence.researcher_bridge.advisory.resolve_researcher_config_path",
            return_value=config_path,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._import_build_advisory",
            return_value=fake_build_advisory,
        ), patch(
            "spark_intelligence.researcher_bridge.advisory._import_execute_with_research",
            return_value=fake_execute_with_research,
        ):
            result = build_researcher_reply(
                config_manager=self.config_manager,
                state_db=self.state_db,
                request_id="req-3",
                agent_id="agent-1",
                human_id="human-1",
                session_id="session-1",
                channel_kind="telegram",
                user_message="Should codex run through the shared bridge?",
            )

        self.assertEqual(captured["advisory_model"], "codex")
        self.assertEqual(captured["execution_model"], "codex")
        self.assertEqual(captured["command_override"], None)
        self.assertEqual(result.reply_text, "Codex-backed reply")
        self.assertEqual(result.provider_id, "openai-codex")
        self.assertEqual(result.provider_auth_method, "oauth")
        self.assertEqual(result.provider_model_family, "codex")
        self.assertEqual(result.provider_execution_transport, "external_cli_wrapper")

    def test_build_researcher_reply_fails_closed_when_provider_auth_is_unresolved(self) -> None:
        self.config_manager.set_path("spark.researcher.enabled", True)
        connect_exit, _, connect_stderr = self.run_cli(
            "auth",
            "connect",
            "openrouter",
            "--home",
            str(self.home),
            "--api-key-env",
            "MISSING_OPENROUTER_KEY",
            "--model",
            "anthropic/claude-3.7-sonnet",
        )
        self.assertEqual(connect_exit, 0, connect_stderr)

        result = build_researcher_reply(
            config_manager=self.config_manager,
            state_db=self.state_db,
            request_id="req-2",
            agent_id="agent-1",
            human_id="human-1",
            session_id="session-1",
            channel_kind="telegram",
            user_message="Should I reply?",
        )

        self.assertEqual(result.mode, "bridge_error")
        self.assertEqual(result.provider_id, None)
        self.assertEqual(result.provider_model_family, "generic")
        self.assertIn("missing secret value", result.reply_text)
