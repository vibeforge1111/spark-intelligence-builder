from __future__ import annotations

import pytest

from spark_intelligence.swarm_bridge.sync import _assert_supabase_url_is_https


# --- _assert_supabase_url_is_https unit tests ---

def test_https_url_passes():
    _assert_supabase_url_is_https("https://myproject.supabase.co")


def test_https_url_with_path_passes():
    _assert_supabase_url_is_https("https://myproject.supabase.co/auth/v1")


def test_http_url_rejected():
    with pytest.raises(ValueError, match="must use https://"):
        _assert_supabase_url_is_https("http://myproject.supabase.co")


def test_http_localhost_rejected():
    with pytest.raises(ValueError, match="must use https://"):
        _assert_supabase_url_is_https("http://localhost:54321")


def test_http_internal_ip_rejected():
    with pytest.raises(ValueError, match="must use https://"):
        _assert_supabase_url_is_https("http://192.168.1.10")


def test_ws_scheme_rejected():
    with pytest.raises(ValueError, match="must use https://"):
        _assert_supabase_url_is_https("ws://myproject.supabase.co")


def test_ftp_scheme_rejected():
    with pytest.raises(ValueError, match="must use https://"):
        _assert_supabase_url_is_https("ftp://myproject.supabase.co")


def test_error_message_includes_scheme():
    with pytest.raises(ValueError, match="http"):
        _assert_supabase_url_is_https("http://example.com")


def test_error_message_includes_url():
    with pytest.raises(ValueError, match="example.com"):
        _assert_supabase_url_is_https("http://example.com")


def test_https_with_port_passes():
    _assert_supabase_url_is_https("https://myproject.supabase.co:443")
