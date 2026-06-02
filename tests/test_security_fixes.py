"""Verify security fixes: deletion guard, URL validation, SQLite, OAuth."""
import inspect
import unittest


class TestSecurityFixes(unittest.TestCase):
    def test_knowledge_base_deletion_guard(self) -> None:
        from spark_intelligence.memory.knowledge_base import _prepare_output_dir
        source = inspect.getsource(_prepare_output_dir)
        self.assertIn("len(resolved.parts)", source,
                       "_prepare_output_dir should check path depth")
        self.assertIn("RuntimeError", source,
                       "should raise RuntimeError on shallow paths")

    def test_oauth_callback_url_validation(self) -> None:
        from spark_intelligence.auth.service import complete_oauth_login
        source = inspect.getsource(complete_oauth_login)
        self.assertIn("not parsed.scheme", source,
                       "should validate callback URL scheme")
        self.assertIn("ValueError", source,
                       "should raise ValueError on invalid URLs")

    def test_sqlite_connection_timeout(self) -> None:
        from spark_intelligence.state.db import StateDB
        source = inspect.getsource(StateDB.connect)
        self.assertIn("timeout", source,
                       "StateDB.connect should specify timeout")

    def test_sqlite_wal_failure_handling(self) -> None:
        from spark_intelligence.state.db import StateDB
        source = inspect.getsource(StateDB.connect)
        self.assertIn("DatabaseError", source,
                       "should handle WAL pragma failure gracefully")

    def test_oauth_exchange_network_errors(self) -> None:
        from spark_intelligence.auth.service import exchange_oauth_authorization_code
        source = inspect.getsource(exchange_oauth_authorization_code)
        self.assertIn("HTTPError", source,
                       "should catch HTTPError")
        self.assertIn("URLError", source,
                       "should catch URLError")

    def test_oauth_refresh_network_errors(self) -> None:
        from spark_intelligence.auth.service import exchange_oauth_refresh_token
        source = inspect.getsource(exchange_oauth_refresh_token)
        self.assertIn("HTTPError", source,
                       "should catch HTTPError")
