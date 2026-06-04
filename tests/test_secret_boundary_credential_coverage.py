from __future__ import annotations

from spark_intelligence.observability.policy import looks_secret_like

# looks_secret_like is the gate for the outbound secret-boundary guardrail
# (gateway/guardrails.py: a reply that looks secret-like is blocked and
# quarantined as a secret_boundary_violation before delivery) and for the
# connector probe redactor (self_awareness/connector_harness.py). Its
# _DIRECT_SECRET_PATTERNS had drifted into a narrower set than the canonical
# SECRET_PATTERNS in security/redaction.py, so several credential families
# bypassed the boundary. These assertions pin the closed gap.

# Fake-but-shaped credentials (no real secrets).
FILLER = "F0fake0F0fake0F0fake0F0fake0F0"


def test_connection_strings_are_secret_like() -> None:
    for scheme in ("postgres", "postgresql", "mysql", "mongodb", "mongodb+srv", "redis"):
        url = f"{scheme}://svc:{FILLER}@host.internal:5432/app"
        assert looks_secret_like(url), f"{scheme} connection string not flagged"


def test_token_families_are_secret_like() -> None:
    cases = {
        "google_api_key": "AIza" + FILLER + "abcd",
        "stripe_live": "sk_live_" + FILLER.replace("-", ""),
        "stripe_test": "sk_test_" + FILLER.replace("-", ""),
        "huggingface": "hf_" + FILLER.replace("-", ""),
        "npm": "npm_" + FILLER.replace("-", ""),
        "pypi": "pypi-" + FILLER,
        "doppler": "dop_v1_" + FILLER,
        "fernet": "gAAAA" + FILLER,
    }
    for name, value in cases.items():
        assert looks_secret_like(f"here is the value {value}"), f"{name} not flagged"


def test_already_covered_shapes_still_secret_like() -> None:
    # Regression guard: shapes that already passed must keep passing.
    for value in ("ghp_" + FILLER.replace("-", ""), "AKIA0000FAKE0000FAKE", "sk-proj-" + FILLER):
        assert looks_secret_like(value)


def test_benign_text_not_flagged() -> None:
    # The guardrail must not start blocking ordinary replies.
    for value in (
        "Your order ships tomorrow morning.",
        "See the guide at https://docs.example.com/getting-started",
        "request id 1f2e3d4c-5b6a-7890-abcd-ef0011223344",
        "The meeting is at 3pm in room redis-lab",  # 'redis' word, not a URL
    ):
        assert not looks_secret_like(value), f"benign text wrongly flagged: {value}"
