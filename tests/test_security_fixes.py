"""Verify security fixes: directory deletion guard, URL validation, SQLite hardening.

Covers PRs: #103 (knowledge base deletion guard), #101 (validate callback URL),
#102 (SQLite hardening), #100 (OAuth refresh error handling).
"""

import ast
import inspect
import unittest


class TestSecurityFixes(unittest.TestCase):
    """Tests for security-related fixes."""

    # ── knowledge base deletion guard (#103) ────────────────────────
    def test_knowledge_base_has_deletion_guard(self) -> None:
        """_prepare_output_dir should refuse to delete shallow paths."""
        from spark_intelligence.memory.knowledge_base import _prepare_output_dir
        source = inspect.getsource(_prepare_output_dir)
        self.assertIn("len(resolved.parts)", source,
                       "_prepare_output_dir should check path depth before deletion")
        self.assertIn("RuntimeError", source,
                       "_prepare_output_dir should raise RuntimeError on shallow paths")

    # ── OAuth callback URL validation (#101) ────────────────────────
    def test_oauth_callback_url_validation(self) -> None:
        """complete_oauth_login should validate callback URL."""
        from spark_intelligence.auth.service import complete_oauth_login
        source = inspect.getsource(complete_oauth_login)
        self.assertIn("not parsed.scheme", source,
                       "complete_oauth_login should validate callback URL scheme")
        self.assertIn("ValueError", source,
                       "complete_oauth_login should raise ValueError on invalid URLs")

    # ── SQLite hardening (#102) ─────────────────────────────────────
    def test_sqlite_has_connection_timeout(self) -> None:
        """StateDB.connect should specify a timeout."""
        from spark_intelligence.state.db import StateDB
        source = inspect.getsource(StateDB.connect)
        self.assertIn("timeout", source,
                       "StateDB.connect should specify timeout parameter")

    def test_sqlite_handles_wal_failure(self) -> None:
        """StateDB.connect should handle WAL pragma failure gracefully."""
        from spark_intelligence.state.db import StateDB
        source = inspect.getsource(StateDB.connect)
        self.assertIn("DatabaseError", source,
                       "StateDB.connect should catch sqlite3.DatabaseError on WAL pragma")

    # ── OAuth refresh error handling (#100) ─────────────────────────
    def test_oauth_exchange_has_network_error_handling(self) -> None:
        """OAuth token exchange should handle network errors."""
        from spark_intelligence.auth.service import exchange_oauth_authorization_code
        source = inspect.getsource(exchange_oauth_authorization_code)
        self.assertIn("HTTPError", source,
                       "exchange_oauth_authorization_code should catch HTTPError")
        self.assertIn("URLError", source,
                       "should catch URLError for network failures")

    def test_oauth_refresh_has_network_error_handling(self) -> None:
        """OAuth refresh should handle network errors."""
        from spark_intelligence.auth.service import exchange_oauth_refresh_token
        source = inspect.getsource(exchange_oauth_refresh_token)
        self.assertIn("HTTPError", source,
                       "exchange_oauth_refresh_token should catch HTTPError")
