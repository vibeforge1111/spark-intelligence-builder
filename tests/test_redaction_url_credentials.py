from __future__ import annotations

from spark_intelligence.security.redaction import redact_text


def test_redacts_credentials_in_https_url() -> None:
    password = "S3cretPassw0rd123456"
    text = f"fetch failed for https://svc-user:{password}@internal.example.com/api/v1"
    redacted = redact_text(text)
    assert password not in redacted
    assert "svc-user" not in redacted
    assert "internal.example.com" in redacted


def test_redacts_credentials_in_http_url() -> None:
    password = "anotherSecret9999"
    redacted = redact_text(f"http://admin:{password}@10.0.0.5:8080/hook")
    assert password not in redacted
    assert "admin:" + password not in redacted


def test_matches_db_scheme_consistency() -> None:
    password = "DbPassw0rd000000"
    assert password not in redact_text(f"postgres://u:{password}@db.internal/app")


def test_does_not_touch_benign_urls() -> None:
    assert redact_text("see https://example.com:8443/path?x=1") == "see https://example.com:8443/path?x=1"
    assert redact_text("https://docs.example.com/guide") == "https://docs.example.com/guide"
    assert "git@" in redact_text("clone git@github.com:org/repo.git")
