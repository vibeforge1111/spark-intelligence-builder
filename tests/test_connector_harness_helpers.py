"""Direct coverage for connector_harness surface-detection and redaction helpers.

The integration shape of ``build_connector_harness_envelope`` is checked in
``test_self_awareness``, but the surface-rule routing and per-key redaction
paths are not exercised in isolation. These tests pin the mapping table so
adding or renaming a connector surface forces a tests update.
"""
from __future__ import annotations

import pytest

from spark_intelligence.self_awareness.connector_harness import (
    CONNECTOR_HARNESS_SCHEMA_VERSION,
    build_connector_harness_envelope,
    redact_connector_probe_sample,
)


class TestBuildEnvelopeRouting:
    def test_email_surface_detected_from_goal(self) -> None:
        env = build_connector_harness_envelope(
            goal="check my Gmail inbox for new messages",
            implementation_route="capability_connector",
        )
        assert env is not None
        assert env.connector_key == "email"
        assert "email_account_access" in env.permissions_required

    def test_calendar_surface_detected_from_goal(self) -> None:
        env = build_connector_harness_envelope(
            goal="show next meeting on my calendar",
            implementation_route="capability_connector",
        )
        assert env is not None
        assert env.connector_key == "calendar"
        assert "create_event" in env.blocked_live_actions

    def test_files_surface_detected_from_permission_only(self) -> None:
        env = build_connector_harness_envelope(
            goal="generic ask without a surface keyword",
            implementation_route="capability_connector",
            permissions_required=["filesystem_read_scope"],
        )
        assert env is not None
        assert env.connector_key == "files"

    def test_workflow_route_default_falls_back_to_workflow(self) -> None:
        env = build_connector_harness_envelope(
            goal="ambient request",
            implementation_route="workflow_automation",
        )
        assert env is not None
        assert env.connector_key == "workflow"

    def test_no_surface_and_no_known_route_returns_none(self) -> None:
        env = build_connector_harness_envelope(
            goal="brainstorm a name",
            implementation_route="conversational",
        )
        assert env is None

    def test_schema_version_pinned_on_envelope_payload(self) -> None:
        env = build_connector_harness_envelope(
            goal="email me when something happens",
            implementation_route="capability_connector",
        )
        assert env is not None
        payload = env.to_payload()
        assert payload["schema_version"] == CONNECTOR_HARNESS_SCHEMA_VERSION
        assert payload["authority_stage"] == "proposal_only"
        assert payload["truth_boundary"].startswith("connector_harness_is_not_live_access")


class TestPermissionDedup:
    def test_duplicate_caller_permissions_collapsed(self) -> None:
        env = build_connector_harness_envelope(
            goal="check my Gmail inbox",
            implementation_route="capability_connector",
            permissions_required=["email_account_access", "email_account_access"],
        )
        assert env is not None
        assert env.permissions_required.count("email_account_access") == 1

    def test_caller_added_permissions_appended_after_defaults(self) -> None:
        env = build_connector_harness_envelope(
            goal="check my Gmail inbox",
            implementation_route="capability_connector",
            permissions_required=["extra_custom_scope"],
        )
        assert env is not None
        assert "extra_custom_scope" in env.permissions_required


class TestRedactConnectorProbeSample:
    def test_redact_dict_sensitive_key_replaced(self) -> None:
        out = redact_connector_probe_sample(
            connector_key="email", sample={"subject": "secret subject text"}
        )
        assert out["subject"].startswith("<redacted email")

    def test_redact_non_sensitive_key_preserved(self) -> None:
        out = redact_connector_probe_sample(
            connector_key="email", sample={"id": "abc-123", "subject": "x"}
        )
        assert out["id"] == "abc-123"
        # subject is in _SENSITIVE_KEYS even when value isn't secret-like
        assert "redacted" in out["subject"]

    def test_redact_list_recurses(self) -> None:
        out = redact_connector_probe_sample(
            connector_key="files", sample=[{"sender": "alice@example.com"}]
        )
        assert out[0]["sender"].startswith("<redacted files")

    def test_redact_scalar_string_secret_like_replaced(self) -> None:
        secret_blob = "ghp_" + "abcdefghijklmnopqrst1234"
        out = redact_connector_probe_sample(connector_key="api", sample=secret_blob)
        assert out == "<redacted secret-like value>"

    def test_redact_scalar_non_secret_string_passthrough(self) -> None:
        out = redact_connector_probe_sample(connector_key="api", sample="plain string")
        assert out == "plain string"

    def test_redact_integer_passthrough(self) -> None:
        assert redact_connector_probe_sample(connector_key="api", sample=42) == 42

    def test_redact_nested_sensitive_substring_key(self) -> None:
        # "user_email" should match because "email" is in the parts.
        out = redact_connector_probe_sample(
            connector_key="email", sample={"user_email": "alice@example.com"}
        )
        assert out["user_email"].startswith("<redacted email")
