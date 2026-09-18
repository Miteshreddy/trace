"""End-to-end test: launch a real run against the demo site."""
import urllib.request
import json
import time

BASE = "http://127.0.0.1:8000"

# Test 1: target health check
print("=== Testing target check ===")
url = BASE + "/api/check-target?url=http%3A%2F%2F127.0.0.1%3A8000%2Fdemo%2F"
resp = urllib.request.urlopen(url, timeout=10)
data = json.loads(resp.read())
print("Target check result:", data)
assert data["reachable"], f"Target should be reachable: {data}"
print("OK Target reachable")

# Test 2: launch a real run
print("\n=== Launching autonomous run ===")
payload = json.dumps({
    "goal": "Open the site, explore the primary navigation, inspect the main form, identify usability and accessibility issues.",
    "target_url": "http://127.0.0.1:8000/demo/",
    "max_steps": 8,
    "exploration_passes": 1
}).encode("utf-8")
req = urllib.request.Request(
    BASE + "/api/runs",
    data=payload,
    headers={"Content-Type": "application/json"},
    method="POST"
)
resp = urllib.request.urlopen(req, timeout=10)
run = json.loads(resp.read())
run_id = run["id"]
print("Run created:", run_id, "Status:", run["status"])
assert run["id"], "Run must have an ID"
print("OK Run created successfully")

print("\nPolling run for up to 240 seconds...")
for i in range(48):
    time.sleep(5)
    resp = urllib.request.urlopen(BASE + "/api/runs/" + run_id, timeout=5)
    state = json.loads(resp.read())
    resp2 = urllib.request.urlopen(BASE + "/api/runs/" + run_id + "/events", timeout=5)
    events = json.loads(resp2.read()).get("events", [])
    status = state["status"]
    steps = state["step_count"]
    n_events = len(events)
    print(f"  t={i*5}s status={status} steps={steps} events={n_events}")
    if events:
        last_ev = events[-1]
        print(f"    Last: [{last_ev['kind']}] {last_ev['message'][:90]}")
    if status in ("completed", "failed"):
        print("\n=== FINAL STATE ===")
        print("Status:", status)
        print("Steps:", steps)
        print("Goal completed:", state["goal_completed"])
        n_issues = len(state.get("issues", []))
        print("Issues:", n_issues)
        if state.get("error"):
            print("Error:", state["error"])
        print("Report URL:", state.get("report_url"))
        if state.get("issues"):
            print("Sample issue:", state["issues"][0]["title"])
        if status == "completed":
            print("PASS RUN COMPLETED SUCCESSFULLY")
        else:
            print("FAIL RUN FAILED")
        break
else:
    print("TIMEOUT — run still running after 240s")
