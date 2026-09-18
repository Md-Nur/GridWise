#!/usr/bin/env python3
"""
Test runner for GridWise public sample cases.
Tests all 10 sample cases against the /optimize-energy endpoint,
verifying API contract, directive interpretation, physical constraints,
and optimization cost quality.
"""

import argparse
import json
import os
import sys
import time
from typing import Dict, Any

from app.models import OptimizeEnergyRequest, OptimizeEnergyResponse, BatteryInput, DirectiveInterpretation
from app.replayer import replay_and_verify_schedule, recalculate_metrics


def run_tests(base_url: str = None):
    # Determine test transport: live HTTP or in-process TestClient
    if base_url:
        import httpx
        client = httpx.Client(base_url=base_url, timeout=30.0)
        use_http = True
        print(f"Connecting to live GridWise service at: {base_url}")
    else:
        from fastapi.testclient import TestClient
        from app.main import app
        client = TestClient(app)
        use_http = False
        print("Running in-process using FastAPI TestClient")

    # 1. Test /health
    t0 = time.perf_counter()
    health_res = client.get("/health")
    health_dur = (time.perf_counter() - t0) * 1000
    if health_res.status_code != 200 or health_res.json() != {"status": "ok"}:
        print(f"FAILED /health check! Status: {health_res.status_code}, body: {health_res.text}")
        sys.exit(1)
    print(f"✓ /health endpoint OK ({health_dur:.1f}ms)\n")

    # 2. Load public sample cases
    cases_file = "BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json"
    if not os.path.exists(cases_file):
        print(f"Error: {cases_file} not found!")
        sys.exit(1)

    with open(cases_file, "r") as f:
        pack = json.load(f)

    cases = pack["cases"]
    total_cases = len(cases)
    passed_count = 0

    print(f"{'Case ID':<12} | {'Label':<32} | {'Status':<6} | {'Time (s)':<8} | {'Cost (BDT)':<12} | {'Ref Cost':<12}")
    print("-" * 92)

    for case in cases:
        case_id = case["id"]
        label = case["label"][:32]
        payload = case["input"]
        expected_output = case["expected_output"]
        ref_cost = expected_output["total_cost_bdt"]

        start_time = time.perf_counter()
        res = client.post("/optimize-energy", json=payload)
        elapsed = time.perf_counter() - start_time

        if res.status_code != 200:
            print(f"{case_id:<12} | {label:<32} | FAIL   | {elapsed:6.2f}s  | HTTP {res.status_code}")
            print(f"   Error: {res.text}")
            continue

        data = res.json()

        # Validate response schema
        try:
            resp_obj = OptimizeEnergyResponse(**data)
        except Exception as e:
            print(f"{case_id:<12} | {label:<32} | FAIL   | {elapsed:6.2f}s  | Schema error: {e}")
            continue

        # Check scenario_id echo
        if resp_obj.scenario_id != payload["scenario_id"]:
            print(f"{case_id:<12} | {label:<32} | FAIL   | {elapsed:6.2f}s  | scenario_id mismatch")
            continue

        # Verify physical constraints and replay
        req_obj = OptimizeEnergyRequest(**payload)
        is_valid, violations = replay_and_verify_schedule(
            req_obj.hours, req_obj.battery, resp_obj.directive_interpretation, resp_obj.hourly_plan
        )

        if not is_valid:
            print(f"{case_id:<12} | {label:<32} | FAIL   | {elapsed:6.2f}s  | Constraint violations:")
            for v in violations:
                print(f"      - {v}")
            continue

        # Recalculate metrics
        recalc_grid, recalc_cost, recalc_peak = recalculate_metrics(req_obj.hours, resp_obj.hourly_plan)
        if abs(recalc_cost - resp_obj.total_cost_bdt) > 0.05:
            print(f"{case_id:<12} | {label:<32} | FAIL   | {elapsed:6.2f}s  | Recalculated cost mismatch")
            continue

        # Compare with reference cost
        cost_diff = resp_obj.total_cost_bdt - ref_cost
        if cost_diff > 0.05:
            status_str = "SUBOPT"
        else:
            status_str = "PASS"
            passed_count += 1

        print(f"{case_id:<12} | {label:<32} | {status_str:<6} | {elapsed:6.2f}s  | {resp_obj.total_cost_bdt:<12.2f} | {ref_cost:<12.2f}")

    print("-" * 92)
    print(f"Summary: {passed_count}/{total_cases} cases passed perfectly within tolerance.\n")

    if passed_count == total_cases:
        print("🎉 ALL PUBLIC SAMPLE CASES PASSED!")
        return 0
    else:
        print("⚠️ Some cases did not pass perfectly.")
        return 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test GridWise Service against Public Sample Cases")
    parser.add_argument("--url", default=None, help="Base URL of running service (e.g. http://localhost:7860)")
    args = parser.parse_args()

    sys.exit(run_tests(args.url))
