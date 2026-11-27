import json
import runpy
from hashlib import sha256
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
CHECK = runpy.run_path(str(ROOT / "scripts/check_release_evaluations.py"))["check_report"]


def passing_report():
    fixture = (ROOT / "backend/canopy/evaluations/workflow-release-v1.json").read_bytes()
    cases = json.loads(fixture)
    return {
        "suite": "release",
        "fixture_sha256": sha256(fixture).hexdigest(),
        "cases": len(cases),
        "passed": len(cases),
        "provider_unavailable": 0,
        "maximum_turn_attempts": 1,
        "model": "gemini-3.5-flash-lite",
        "provider_max_attempts": 2,
        "provider_timeout_seconds": 20,
        "workflow_provider_budget_seconds": 80,
        "turn_latency_ms_excluding_pacing": {"p95": 3000},
        "results": [{"id": case["id"], "passed": True, "error": None} for case in cases],
    }


def test_complete_matching_evidence_passes():
    assert CHECK(passing_report(), "release", max_p95_ms=30000) == []


@pytest.mark.parametrize(
    "mutation",
    [
        {"fixture_sha256": "old"},
        {"passed": 12},
        {"provider_unavailable": 1},
        {"maximum_turn_attempts": 4},
        {"results": []},
        {"provider_max_attempts": None},
        {"provider_max_attempts": 0},
        {"provider_max_attempts": 2.5},
        {"turn_latency_ms_excluding_pacing": {"p95": 31000}},
        {"turn_latency_ms_excluding_pacing": {"p95": float("nan")}},
    ],
)
def test_gate_rejects_unavailable_stale_or_masked_results(mutation):
    assert CHECK({**passing_report(), **mutation}, "release", max_p95_ms=30000)
