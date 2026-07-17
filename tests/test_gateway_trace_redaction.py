from __future__ import annotations

from spark_intelligence.gateway.tracing import redact_trace_payload


def test_trace_redacts_credential_shapes_in_ordinary_fields() -> None:
    aws = "AKIA" + "FAKE0FAKE0FAKE00"
    pg = "postgres://user:" + "p" * 24 + "@db.internal:5432/app"
    gh_pat = "github_pat_" + "F" * 30
    hf = "hf_" + "F" * 30
    npm = "npm_" + "F" * 30
    pypi = "pypi-" + "F" * 30
    doppler = "dop_v1_" + "F" * 30
    phone = "415-555-0142"

    record = {
        "event": "telegram_poll_failure",
        "failure_message": f"connect failed using {pg}",
        "detail": f"aws creds {aws} rejected",
        "evidence_summary": f"see {gh_pat} and {hf}",
        "output": {"line": f"{npm} {pypi}"},
        "items": [{"stderr": f"token {doppler}"}],
        "note": f"call operator at {phone}",
    }

    redacted = redact_trace_payload(record)
    blob = str(redacted)

    for secret in (aws, pg, gh_pat, hf, npm, pypi, doppler, phone):
        assert secret not in blob, f"secret leaked into trace payload: {secret[:12]}..."


def test_trace_still_redacts_sensitive_keys_and_known_shapes() -> None:
    record = {
        "token": "anything-here-stays-redacted",
        "api_key": "sk-proj-" + "A" * 30,
        "detail": "openai key sk-proj-" + "C" * 30,
    }
    redacted = redact_trace_payload(record)
    assert redacted["token"] == "[REDACTED]"
    assert redacted["api_key"] == "[REDACTED]"
    assert "sk-proj-" + "C" * 30 not in str(redacted["detail"])
