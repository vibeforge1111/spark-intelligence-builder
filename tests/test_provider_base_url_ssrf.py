from __future__ import annotations

import pytest

from spark_intelligence.llm.direct_provider import _validate_base_url


class TestProviderBaseUrlSSRF:
    def test_http_scheme_rejected(self) -> None:
        with pytest.raises(ValueError, match="https://"):
            _validate_base_url("http://api.openai.com/v1")

    def test_ftp_scheme_rejected(self) -> None:
        with pytest.raises(ValueError, match="https://"):
            _validate_base_url("ftp://api.openai.com/v1")

    def test_localhost_rejected(self) -> None:
        with pytest.raises(ValueError, match="loopback"):
            _validate_base_url("https://localhost/v1")

    def test_localhost_localdomain_rejected(self) -> None:
        with pytest.raises(ValueError, match="loopback"):
            _validate_base_url("https://localhost.localdomain/v1")

    def test_loopback_ip_rejected(self) -> None:
        with pytest.raises(ValueError, match="private/reserved"):
            _validate_base_url("https://127.0.0.1/v1")

    def test_metadata_endpoint_rejected(self) -> None:
        with pytest.raises(ValueError, match="private/reserved"):
            _validate_base_url("https://169.254.169.254/latest/meta-data/")

    def test_private_class_a_rejected(self) -> None:
        with pytest.raises(ValueError, match="private/reserved"):
            _validate_base_url("https://10.0.0.1/v1")

    def test_private_class_b_rejected(self) -> None:
        with pytest.raises(ValueError, match="private/reserved"):
            _validate_base_url("https://172.16.0.1/v1")

    def test_private_class_c_rejected(self) -> None:
        with pytest.raises(ValueError, match="private/reserved"):
            _validate_base_url("https://192.168.1.100/v1")

    def test_ipv6_loopback_rejected(self) -> None:
        with pytest.raises(ValueError, match="private/reserved"):
            _validate_base_url("https://::1/v1")

    def test_non_allowlisted_domain_rejected(self) -> None:
        with pytest.raises(ValueError, match="allowlist"):
            _validate_base_url("https://evil.example.com/v1")

    def test_valid_openai_base_url_accepted(self) -> None:
        _validate_base_url("https://api.openai.com/v1")  # must not raise

    def test_valid_anthropic_base_url_accepted(self) -> None:
        _validate_base_url("https://api.anthropic.com")  # must not raise

    def test_valid_openrouter_base_url_accepted(self) -> None:
        _validate_base_url("https://openrouter.ai/api/v1")  # must not raise

    def test_valid_mistral_base_url_accepted(self) -> None:
        _validate_base_url("https://api.mistral.ai/v1")  # must not raise

    def test_valid_groq_base_url_accepted(self) -> None:
        _validate_base_url("https://api.groq.com/openai/v1")  # must not raise

    def test_valid_together_base_url_accepted(self) -> None:
        _validate_base_url("https://api.together.xyz/v1")  # must not raise
