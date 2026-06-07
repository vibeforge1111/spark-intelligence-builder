from __future__ import annotations

from spark_intelligence.security.redaction import redact_text

# redact_text is the canonical outbound/loggable-text scrubber (it sanitizes
# Telegram reply text/captions and the Telegram API error description in
# adapters/telegram/client.py, plus gateway guardrails). It already strips
# credentials embedded in DB-scheme URLs (postgres/mysql/mongodb/redis://
# user:pass@...), and the project's own observability policy looks_secret_like
# treats http(s)://user:pass@... as secret-like. But redact_text left
# credentials in http(s) URLs untouched, so a reply/error containing such a URL
# leaked the password into the chat.


def test_redacts_credentials_in_https_url() -> None:
    pw = "S3cretPassw0rd123456"
    text = f"fetch failed for https://svc-user:{pw}@internal.example.com/api/v1"
    redacted = redact_text(text)
    assert pw not in redacted
    assert "svc-user" not in redacted
    # host stays visible so the message is still useful
    assert "internal.example.com" in redacted


def test_redacts_credentials_in_http_url() -> None:
    pw = "anotherSecret9999"
    redacted = redact_text(f"http://admin:{pw}@10.0.0.5:8080/hook")
    assert pw not in redacted
    assert "admin:" + pw not in redacted


def test_matches_db_scheme_consistency() -> None:
    # the DB-scheme case already worked and must keep working
    pw = "DbPassw0rd000000"
    assert pw not in redact_text(f"postgres://u:{pw}@db.internal/app")


def test_does_not_touch_benign_urls() -> None:
    # no userinfo -> not a credential; must be left intact
    assert redact_text("see https://example.com:8443/path?x=1") == "see https://example.com:8443/path?x=1"
    assert redact_text("https://docs.example.com/guide") == "https://docs.example.com/guide"
    # user without a password is not a leaked secret
    assert "git@" in redact_text("clone git@github.com:org/repo.git")
