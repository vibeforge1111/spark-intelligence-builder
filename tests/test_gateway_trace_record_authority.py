from __future__ import annotations

import json

from spark_intelligence.gateway.tracing import (
    outbound_log_path,
    read_gateway_traces,
    read_outbound_audit,
    redact_gateway_trace_log,
    repair_gateway_trace_proof_continuity,
    trace_log_path,
)

from tests.test_support import SparkTestCase


class GatewayTraceRecordAuthorityTests(SparkTestCase):
    def test_readers_skip_valid_json_that_is_not_a_record(self) -> None:
        rows = [[], "not-a-record", None, {"event": "valid"}]
        payload = "\n".join(json.dumps(row) for row in rows) + "\n"
        trace_log_path(self.config_manager).write_text(payload, encoding="utf-8")
        outbound_log_path(self.config_manager).write_text(payload, encoding="utf-8")

        self.assertEqual(read_gateway_traces(self.config_manager), [{"event": "valid"}])
        self.assertEqual(read_outbound_audit(self.config_manager), [{"event": "valid"}])

    def test_rewrite_and_repair_classify_non_record_json_as_parse_errors(self) -> None:
        trace_path = trace_log_path(self.config_manager)
        trace_path.write_text('[]\n{"event":"valid"}\n', encoding="utf-8")

        redaction = redact_gateway_trace_log(self.config_manager, backup=False)
        self.assertEqual(redaction["parse_errors"], 1)
        self.assertEqual(redaction["rows_written"], 1)
        self.assertEqual(json.loads(trace_path.read_text(encoding="utf-8")), {"event": "valid"})

        trace_path.write_text('[]\n{"event":"valid"}\n', encoding="utf-8")
        repair = repair_gateway_trace_proof_continuity(self.config_manager, backup=False)
        self.assertEqual(repair["parse_errors"], 1)
        self.assertEqual(repair["rows_written"], 2)
        self.assertEqual(trace_path.read_text(encoding="utf-8").splitlines()[0], "[]")
