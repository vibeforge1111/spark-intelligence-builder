from __future__ import annotations

from spark_intelligence.observability.policy import looks_secret_like


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
    for value in ("ghp_" + FILLER.replace("-", ""), "AKIA0000FAKE0000FAKE", "sk-proj-" + FILLER):
        assert looks_secret_like(value)


def test_http_url_credentials_follow_canonical_secret_authority() -> None:
    assert looks_secret_like("https://operator:aaaaaaaaaaaa@internal.example.com/status")
    assert looks_secret_like("https://:aaaaaaaaaaaa@internal.example.com/status")


def test_benign_text_and_phone_only_pii_are_not_secret_boundary_hits() -> None:
    for value in (
        "Your order ships tomorrow morning.",
        "See the guide at https://docs.example.com/getting-started",
        "request id 1f2e3d4c-5b6a-7890-abcd-ef0011223344",
        "The meeting is at 3pm in room redis-lab",
        "Call the operator at 415-555-0142.",
    ):
        assert not looks_secret_like(value), f"benign text wrongly flagged: {value}"
