from __future__ import annotations

import os
import sys

from spark_intelligence import bridge_authority
from spark_intelligence.memory import orchestrator as memory_orchestrator

from tests.test_support import SparkTestCase


class ProcessGlobalIsolationTests(SparkTestCase):
    """Guards the CI-shard isolation fix in :class:`SparkTestCase`.

    The suite is sharded by global test index, so a file's state-mutating test and
    the test that would normally reset that state can land in different shards. Each
    test must therefore start from a clean process-global state and clean up after
    itself, otherwise leaked state (an env var, a sys.path entry, the bridge-authority
    ledger ContextVar, a cached memory SDK client) silently breaks a later, unrelated
    test in the same shard. This drives a fresh lifecycle that leaks each of those and
    asserts tearDown restored them.
    """

    def test_teardown_restores_leaked_process_globals(self) -> None:
        probe_env = "SPARK_ISOLATION_PROBE_ENV"
        probe_path = "/spark-isolation-probe-path-guard"
        probe_cache_key = ("isolation-probe", "isolation-probe")

        os.environ.pop(probe_env, None)
        while probe_path in sys.path:
            sys.path.remove(probe_path)
        memory_orchestrator._SDK_CLIENT_CACHE.pop(probe_cache_key, None)

        leaker = SparkTestCase(methodName="run")
        leaker.setUp()
        try:
            os.environ[probe_env] = "1"
            sys.path.insert(0, probe_path)
            bridge_authority._BRIDGE_LEDGER_CONTEXT.set({"leaked": True})
            memory_orchestrator._SDK_CLIENT_CACHE[probe_cache_key] = object()
        finally:
            leaker.tearDown()

        self.assertNotIn(probe_env, os.environ)
        self.assertNotIn(probe_path, sys.path)
        self.assertEqual(bridge_authority._BRIDGE_LEDGER_CONTEXT.get(), {})
        self.assertNotIn(probe_cache_key, memory_orchestrator._SDK_CLIENT_CACHE)
