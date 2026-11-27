"""Reject incomplete, stale-fixture, or evaluation-only-retry release evidence."""

import argparse
import json
import math
from hashlib import sha256
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = {"baseline": "workflow-v1.json", "release": "workflow-release-v1.json"}


def check_report(report, suite, *, max_p95_ms):
    fixture = (ROOT / "backend/canopy/evaluations" / FIXTURES[suite]).read_bytes()
    cases = json.loads(fixture)
    errors = []
    if report.get("suite") != suite or report.get("fixture_sha256") != sha256(fixture).hexdigest():
        errors.append("suite or fixture hash differs from the release source")
    rows = report.get("results", [])
    if (
        report.get("cases") != len(cases)
        or report.get("passed") != len(cases)
        or report.get("provider_unavailable") != 0
        or len(rows) != len(cases)
        or sorted(row.get("id", "") for row in rows) != sorted(case["id"] for case in cases)
        or any(row.get("passed") is not True or row.get("error") is not None for row in rows)
    ):
        errors.append("every fixture case must pass, including provider availability")
    if report.get("maximum_turn_attempts") != 1:
        errors.append("release evidence must use one turn attempt; runtime retries still apply")
    if report.get("model") != "gemini-3.5-flash-lite":
        errors.append("model differs from the evaluated workflow model")
    for key, maximum in (
        ("provider_max_attempts", 3),
        ("provider_timeout_seconds", 60),
        ("workflow_provider_budget_seconds", 80),
    ):
        value = report.get(key)
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(value)
            or not 1 <= value <= maximum
            or (key == "provider_max_attempts" and not isinstance(value, int))
        ):
            errors.append(f"missing or invalid runtime setting: {key}")
    latency = report.get("turn_latency_ms_excluding_pacing", {}).get("p95")
    if (
        isinstance(latency, bool)
        or not isinstance(latency, (int, float))
        or not math.isfinite(latency)
        or not 0 <= latency <= max_p95_ms
    ):
        errors.append("turn latency p95 is missing or exceeds the release limit")
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--release", type=Path, required=True)
    parser.add_argument("--max-p95-ms", type=float, required=True)
    args = parser.parse_args()
    if not math.isfinite(args.max_p95_ms) or args.max_p95_ms <= 0:
        parser.error("--max-p95-ms must be positive and finite")
    try:
        reports = {
            "baseline": json.loads(args.baseline.read_text()),
            "release": json.loads(args.release.read_text()),
        }
        failures = {
            suite: check_report(report, suite, max_p95_ms=args.max_p95_ms)
            for suite, report in reports.items()
        }
        settings = (
            "provider_max_attempts",
            "provider_timeout_seconds",
            "workflow_provider_budget_seconds",
        )
        if any(reports["baseline"].get(key) != reports["release"].get(key) for key in settings):
            failures["release"].append("runtime retry/timeout settings differ between reports")
    except (OSError, ValueError, TypeError, AttributeError, KeyError):
        print('{"status":"failed","check":"evaluation reports are missing or malformed"}')
        return 1
    passed = not any(failures.values())
    print(json.dumps({"status": "passed" if passed else "failed", "checks": failures}, indent=2))
    return int(not passed)


if __name__ == "__main__":
    raise SystemExit(main())
