"""Starter authoring must expose legitimate alternatives without rewriting protected tasks."""
from collections import Counter
import copy
import json

import pytest

from spark_intelligence.chip_create import pipeline


@pytest.mark.parametrize("domain_id", ["independent-example", *pipeline._R30_DOMAIN_FIXTURE_PACKS])
def test_written_starter_contains_authored_borderline_choice(tmp_path, domain_id):
    original_packs = copy.deepcopy(pipeline._R30_DOMAIN_FIXTURE_PACKS)
    brief = {"domain_id": domain_id, "domain_name": domain_id.replace("-", " "),
             "description": "Private support triage", "primary_metric": "task_quality"}
    pipeline._write_loop_proof_starter_assets(tmp_path, brief, chip_key=f"domain-chip-{domain_id}")
    cases = [json.loads(line) for line in (tmp_path / "benchmark/cases.jsonl").read_text(encoding="utf-8").splitlines()]
    alternatives = [case for case in cases if len(case.get("allowed_mutation_outcomes", [])) > 1]
    assert len(alternatives) == 1
    borderline = alternatives[0]
    assert borderline["lane"] == "development"
    assert borderline["expected_outcome"] == "abstain"
    assert borderline["allowed_mutation_outcomes"] == ["abstain", "pass"]
    assert "no acceptance criterion" in borderline["prompt"]
    assert "Use pass only" in borderline["expected_behavior"]
    assert "Use abstain" in borderline["expected_behavior"]
    assert borderline["promotion_blocked"] is True
    assert borderline["network_absorbable"] is False
    assert len({c["case_id"] for c in cases}) == len(cases)
    manifest = json.loads((tmp_path / "benchmark/manifest.json").read_text(encoding="utf-8"))
    assert manifest["case_count"] == len(cases)
    assert manifest["case_lanes"] == dict(Counter(c["lane"] for c in cases))
    assert manifest["sealed_evaluation_required"] is True
    for case in cases:
        if case["lane"] != "development":
            assert "allowed_mutation_outcomes" not in case
    for line in (tmp_path / "benchmark/traps.jsonl").read_text(encoding="utf-8").splitlines():
        assert "allowed_mutation_outcomes" not in json.loads(line)
    assert pipeline._R30_DOMAIN_FIXTURE_PACKS == original_packs
    if domain_id in original_packs:
        by_id = {c["case_id"]: c for c in cases}
        for original in original_packs[domain_id]["cases"]:
            expected = dict(original)
            expected["case_id"] = f"{domain_id}-{expected.pop('suffix')}"
            assert by_id[expected["case_id"]] == expected
        pack = json.loads((tmp_path / "fixtures/domain-fixture-pack.json").read_text(encoding="utf-8"))
        assert pack["cases"] == cases
        assert pack["case_count"] == len(cases)


def test_staging_fixture_alias_gets_its_own_unique_borderline_case():
    pack = pipeline._r30_domain_fixture_pack("daily-schedule-reliability-r30-staging", "Schedule", "quality")
    alternatives = [c for c in pack["cases"] if c.get("allowed_mutation_outcomes")]
    assert len(alternatives) == 1
    assert alternatives[0]["case_id"].startswith("daily-schedule-reliability-r30-staging-")
