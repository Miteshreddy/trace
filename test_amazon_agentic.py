"""
test_amazon_agentic.py

Executes Section 53 E2E test on Amazon India:
Target: https://www.amazon.in/
Goal: "Open Amazon website search for iPhone 17 and also evaluate its ratings"

Captures and displays:
- original URL
- normalized URL
- current URL after navigation
- redirect chain
- search action
- URL after search
- screenshot
- result count/evidence
- ratings evidence
- model decisions
- model agreement
- subgoal completion
- final goal verification
- audit status
"""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
import sys

BASE = "http://127.0.0.1:8000"
TARGET = "https://www.amazon.in/"
GOAL = "Open Amazon website search for iPhone 17 and also evaluate its ratings"


def run_amazon_e2e():
    print("=" * 60)
    print("TRACE//QA - Section 53 Amazon India Agentic Test")
    print("Target:", TARGET)
    print("Goal:", GOAL)
    print("=" * 60)

    # 1. Preflight check
    preflight_url = f"{BASE}/api/check-target?url={urllib.parse.quote(TARGET)}&browser_check=true"
    print("\n[1] Running target preflight check...")
    try:
        resp = urllib.request.urlopen(preflight_url, timeout=30)
        preflight_data = json.loads(resp.read().decode("utf-8"))
        print(f"Preflight result: reachable={preflight_data.get('reachable')} browser_reachable={preflight_data.get('browser_reachable')} error={preflight_data.get('error_type')}")
    except Exception as e:
        print(f"Preflight error: {e}")

    # 2. Create Run
    print("\n[2] Creating autonomous QA run...")
    payload = json.dumps({
        "goal": GOAL,
        "target_url": TARGET,
        "max_steps": 6,
        "exploration_passes": 1,
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{BASE}/api/runs",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    resp = urllib.request.urlopen(req, timeout=15)
    run = json.loads(resp.read().decode("utf-8"))
    run_id = run["id"]
    print(f"Run ID: {run_id}")
    print(f"Original Target: {run.get('original_target_url')}")
    print(f"Normalized Target: {run.get('normalized_target_url')}")
    print(f"Initial Current URL: {run.get('current_url')}")
    print(f"Decomposed Subgoals ({len(run.get('subgoals', []))}):")
    for sg in run.get("subgoals", []):
        print(f"  - [{sg['id']}] {sg['description']} (required={sg.get('required')})")

    # 3. Poll run events and state
    print(f"\n[3] Monitoring run execution in real time...")
    seen_events = 0
    final_state = None

    for attempt in range(60): # up to 300s
        time.sleep(5)
        try:
            r_resp = urllib.request.urlopen(f"{BASE}/api/runs/{run_id}", timeout=10)
            state = json.loads(r_resp.read().decode("utf-8"))

            ev_resp = urllib.request.urlopen(f"{BASE}/api/runs/{run_id}/events", timeout=10)
            events = json.loads(ev_resp.read().decode("utf-8")).get("events", [])

            # Print any new events
            if len(events) > seen_events:
                for ev in events[seen_events:]:
                    safe_msg = ev.get('message', '').encode('ascii', 'replace').decode('ascii')
                    kind = ev.get('kind', 'event')
                    step = ev.get('step')
                    step_str = f" [Step {step}]" if step else ""
                    print(f"  --> [{kind.upper()}]{step_str} {safe_msg[:100]}")
                seen_events = len(events)

            status = state.get("status")
            cur_url = state.get("current_url")
            steps = state.get("step_count", 0)
            print(f"  t={attempt*5}s | status={status} | step={steps} | current_url={cur_url[:70] if cur_url else ''}")

            if status in ("completed", "failed", "cancelled", "blocked"):
                final_state = state
                break
        except Exception as err:
            print(f"  Poll warning: {err}")

    if not final_state:
        print("\nRun timed out after 300 seconds!")
        sys.exit(1)

    # 4. Telemetry and Forensic Analysis
    print("\n" + "=" * 60)
    print("SECTION 53 FORENSIC TELEMETRY SUMMARY")
    print("=" * 60)
    print(f"Run ID:                      {final_state.get('id')}")
    print(f"Status:                      {final_state.get('status')}")
    print(f"Navigation State:            {final_state.get('navigation_state')}")
    print(f"Original URL:                {final_state.get('original_target_url')}")
    print(f"Normalized URL:              {final_state.get('normalized_target_url')}")
    print(f"Final/Current URL:           {final_state.get('final_url') or final_state.get('current_url')}")
    print(f"Step Count:                  {final_state.get('step_count')}")
    print(f"Goal Completed:              {final_state.get('goal_completed')}")
    print(f"Goal Verification Evidence:  {final_state.get('goal_verification_evidence')}")

    print("\nRedirect Chain:")
    redirects = final_state.get("redirect_chain", [])
    if redirects:
        for r in redirects:
            print(f"  {r.get('from')} -> {r.get('to')} ({r.get('type')})")
    else:
        print("  No redirects recorded.")

    print("\nSubgoal Milestones Progress:")
    for sg in final_state.get("subgoals", []):
        status_icon = "PASS" if sg.get("status") == "verified" else "PENDING"
        print(f"  [{status_icon}] {sg.get('id')}: {sg.get('description')}")
        if sg.get("evidence"):
            print(f"         Evidence: {sg.get('evidence')}")

    print("\nConsensus Decisions Recorded:", len(final_state.get("consensus_history", [])))
    for c in final_state.get("consensus_history", []):
        disagree = "DISAGREEMENT" if c.get("disagreement") else "AGREEMENT"
        print(f"  Step {c.get('step')}: {disagree} | Selected: {c.get('selected_provider')} | Conf: {c.get('confidence')}")

    print("\nIssues / Findings Generated:", len(final_state.get("issues", [])))
    for iss in final_state.get("issues", [])[:5]:
        print(f"  - [{iss.get('severity')}] {iss.get('title')} ({iss.get('category')})")

    print("\nArtifacts:")
    print(f"  Latest Screenshot: {final_state.get('latest_screenshot')}")
    print(f"  Report URL:        {final_state.get('report_url')}")

    # Determine classification
    nav_st = final_state.get("navigation_state")
    goal_comp = final_state.get("goal_completed")
    subgoals = {s["id"]: s for s in final_state.get("subgoals", [])}

    ratings_verified = subgoals.get("milestone_ratings", {}).get("status") == "verified"
    search_verified = subgoals.get("milestone_search", {}).get("status") == "verified"

    if nav_st == "blocked":
        print("\nFINAL CLASSIFICATION: BLOCKED (Target anti-bot challenge or security interstitial)")
    elif goal_comp and ratings_verified:
        print("\nFINAL CLASSIFICATION: SUCCESS (All milestones verified including ratings)")
    elif search_verified and not ratings_verified:
        print("\nFINAL CLASSIFICATION: PARTIAL (Search executed/verified, ratings unverified or below fold)")
    elif final_state.get("status") == "completed":
        print("\nFINAL CLASSIFICATION: COMPLETED (Run completed with verified evidence)")
    else:
        print(f"\nFINAL CLASSIFICATION: {final_state.get('status').upper()}")


if __name__ == "__main__":
    run_amazon_e2e()
