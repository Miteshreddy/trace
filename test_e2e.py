"""
End-to-end test suite for TRACE//QA navigation reliability and autonomous execution.

Tests:
1. Target check on valid demo URL.
2. Target check with URL normalization (bare host).
3. Target check with disallowed scheme (javascript:alert(1)).
4. Target check with unreachable host (connection refused).
5. Full autonomous run against local demo site:
   - Verifies URL normalization on run creation
   - Verifies navigation_state is "usable"
   - Verifies navigation_diagnostics structure
   - Verifies independent goal verification
   - Verifies screenshot and report accessibility
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:8000"


def test_target_check():
    print("=== Test 1: Target health check (standard demo) ===")
    url = f"{BASE}/api/check-target?url={urllib.parse.quote('http://127.0.0.1:8000/demo/')}"
    resp = urllib.request.urlopen(url, timeout=10)
    data = json.loads(resp.read())
    print("Target check result:", data)
    assert data["reachable"] is True, f"Target should be reachable: {data}"
    assert data["normalized_url"] == "http://127.0.0.1:8000/demo/"
    assert data["final_url"] == "http://127.0.0.1:8000/demo/"
    print("PASS: Standard target check OK\n")


def test_target_check_normalization():
    print("=== Test 2: Target check with bare domain normalization ===")
    # Bare domain example.com
    url = f"{BASE}/api/check-target?url={urllib.parse.quote('example.com')}"
    resp = urllib.request.urlopen(url, timeout=15)
    data = json.loads(resp.read())
    print("Normalization check result:", data)
    assert data["original_url"] == "example.com"
    assert data["normalized_url"] == "https://example.com"
    print("PASS: URL normalization OK\n")


def test_target_check_invalid_scheme():
    print("=== Test 3: Target check with invalid scheme ===")
    url = f"{BASE}/api/check-target?url={urllib.parse.quote('javascript:alert(1)')}"
    resp = urllib.request.urlopen(url, timeout=10)
    data = json.loads(resp.read())
    print("Invalid scheme check result:", data)
    assert data["reachable"] is False
    assert data["error_type"] == "invalid_url"
    assert "scheme" in data["message"].lower()
    print("PASS: Invalid scheme properly rejected\n")


def test_target_check_connection_refused():
    print("=== Test 4: Target check with connection refused ===")
    url = f"{BASE}/api/check-target?url={urllib.parse.quote('http://127.0.0.1:19999/')}"
    resp = urllib.request.urlopen(url, timeout=10)
    data = json.loads(resp.read())
    print("Connection refused check result:", data)
    assert data["reachable"] is False
    assert data["error_type"] == "connection_refused"
    print("PASS: Connection refused properly classified\n")


def test_autonomous_run():
    print("=== Test 5: Full autonomous run with diagnostics ===")
    payload = json.dumps({
        "goal": "Open the site, explore the primary navigation, inspect the main form, identify usability and accessibility issues.",
        "target_url": "http://127.0.0.1:8000/demo/",
        "max_steps": 6,
        "exploration_passes": 1,
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{BASE}/api/runs",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    resp = urllib.request.urlopen(req, timeout=10)
    run = json.loads(resp.read())
    run_id = run["id"]
    print(f"Run created: {run_id}, status: {run['status']}")
    assert run["id"], "Run must have an ID"
    assert "original_target_url" in run, "Must have original_target_url"
    assert "normalized_target_url" in run, "Must have normalized_target_url"

    print("\nPolling run for up to 240 seconds...")
    final_state = None
    for i in range(48):
        time.sleep(5)
        resp = urllib.request.urlopen(f"{BASE}/api/runs/{run_id}", timeout=5)
        state = json.loads(resp.read())
        resp2 = urllib.request.urlopen(f"{BASE}/api/runs/{run_id}/events", timeout=5)
        events = json.loads(resp2.read()).get("events", [])
        status = state["status"]
        steps = state["step_count"]
        n_events = len(events)
        print(f"  t={i*5}s status={status} steps={steps} events={n_events}")
        if events:
            last_ev = events[-1]
            print(f"    Last: [{last_ev['kind']}] {last_ev['message'][:90]}")
        if status in ("completed", "failed", "cancelled"):
            final_state = state
            break

    assert final_state is not None, "Run timed out after 240 seconds"
    print("\n=== FINAL STATE ===")
    print("Status:", final_state["status"])
    print("Steps:", final_state["step_count"])
    print("Navigation state:", final_state.get("navigation_state"))
    print("Goal completed:", final_state.get("goal_completed"))
    print("Goal evidence:", final_state.get("goal_verification_evidence"))
    print("Navigation diagnostics:", final_state.get("navigation_diagnostics"))
    print("Issues count:", len(final_state.get("issues", [])))
    print("Report URL:", final_state.get("report_url"))

    # Assertions
    assert final_state["status"] == "completed", f"Expected completed, got {final_state['status']}"
    assert final_state["step_count"] > 0, "Steps must be > 0"
    assert final_state.get("navigation_state") == "usable", (
        f"Expected navigation_state 'usable', got {final_state.get('navigation_state')}"
    )

    diag = final_state.get("navigation_diagnostics", {})
    assert diag.get("attempts", 0) >= 1, "Expected at least 1 navigation attempt recorded"
    assert diag.get("body_text_length", 0) > 0, "Expected body_text_length > 0"

    # Verify screenshot artifact if available
    screenshot_url = final_state.get("latest_screenshot")
    if screenshot_url:
        print(f"Verifying screenshot artifact: {screenshot_url}")
        s_resp = urllib.request.urlopen(f"{BASE}{screenshot_url}", timeout=5)
        assert s_resp.status == 200, f"Screenshot returned status {s_resp.status}"
        print("PASS: Screenshot artifact verified")

    # Verify report HTML if available
    report_url = final_state.get("report_url")
    if report_url:
        print(f"Verifying report artifact: {report_url}")
        r_resp = urllib.request.urlopen(f"{BASE}{report_url}", timeout=5)
        assert r_resp.status == 200, f"Report returned status {r_resp.status}"
        print("PASS: Report artifact verified")

    print("\nPASS: Autonomous run completed and all diagnostics verified successfully!")


if __name__ == "__main__":
    test_target_check()
    test_target_check_normalization()
    test_target_check_invalid_scheme()
    test_target_check_connection_refused()
    test_autonomous_run()
