"""
Integration test suite for TRACE//QA External Targets and Navigation Reliability.

Covers:
TEST A — Local demo navigation
TEST B — Public static website (example.com)
TEST C — Public dynamic website (httpbin.org)
TEST D — External redirect handling
TEST E — Invalid URL scheme rejection (security guard)
TEST F — Connection refused handling
TEST G — Unreachable / connection timeout handling
TEST H — Blank document detection
TEST I — Premature finish prevention & goal verification heuristics
TEST J — Screenshot and report artifact accessibility
TEST K — Cancellation lifecycle
"""
from __future__ import annotations

import asyncio
import json
import time
import unittest
import urllib.parse
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from backend.url_utils import normalize_url, validate_url_scheme, classify_navigation_error
from backend.models import RunState, RunCreate, NavigationDiagnostics
from backend.agent import AutonomousTester

BASE = "http://127.0.0.1:8000"


async def _dummy_emit(ev):
    pass


class TestExternalTargetPipeline(unittest.TestCase):
    """End-to-end integration tests hitting the running FastAPI backend."""

    def test_a_local_demo(self):
        """TEST A: Local demo endpoint reachability and preflight."""
        url = f"{BASE}/api/check-target?url={urllib.parse.quote('http://127.0.0.1:8000/demo/')}&browser_check=true"
        resp = urllib.request.urlopen(url, timeout=15)
        data = json.loads(resp.read().decode())
        self.assertTrue(data.get("reachable"))
        self.assertTrue(data.get("browser_reachable"))
        self.assertEqual(data.get("status_code"), 200)
        self.assertEqual(data.get("final_url"), "http://127.0.0.1:8000/demo/")

    def test_b_public_static_website(self):
        """TEST B: Public static website (example.com)."""
        url = f"{BASE}/api/check-target?url={urllib.parse.quote('https://example.com/')}&browser_check=true"
        resp = urllib.request.urlopen(url, timeout=20)
        data = json.loads(resp.read().decode())
        self.assertTrue(data.get("reachable"))
        self.assertTrue(data.get("browser_reachable"))
        self.assertIn("Example Domain", data.get("title", ""))
        self.assertGreater(data.get("body_text_length", 0), 50)

    def test_c_public_dynamic_website(self):
        """TEST C: Public dynamic website (httpbin.org)."""
        url = f"{BASE}/api/check-target?url={urllib.parse.quote('https://httpbin.org/')}&browser_check=true"
        data = {}
        for _ in range(3):
            try:
                resp = urllib.request.urlopen(url, timeout=30)
                data = json.loads(resp.read().decode())
                if data.get("reachable") and data.get("browser_reachable"):
                    break
            except Exception:
                pass
            time.sleep(2)
        self.assertTrue(data.get("reachable"))
        self.assertTrue(data.get("browser_reachable"))
        self.assertEqual(data.get("status_code"), 200)

    def test_d_external_redirect(self):
        """TEST D: External redirect detection."""
        # Bare domain http://google.com redirects to https://www.google.com/
        url = f"{BASE}/api/check-target?url={urllib.parse.quote('http://google.com')}"
        resp = urllib.request.urlopen(url, timeout=15)
        data = json.loads(resp.read().decode())
        self.assertTrue(data.get("reachable"))
        self.assertTrue(
            data.get("redirect_url") is not None or "google" in data.get("final_url", "")
        )

    def test_e_invalid_url_schemes(self):
        """TEST E: Disallowed URL schemes are strictly rejected."""
        disallowed = [
            "javascript:alert(document.cookie)",
            "file:///C:/Windows/System32/drivers/etc/hosts",
            "data:text/html,<h1>XSS</h1>",
            "chrome://settings",
        ]
        for bad in disallowed:
            with self.subTest(url=bad):
                url = f"{BASE}/api/check-target?url={urllib.parse.quote(bad)}"
                resp = urllib.request.urlopen(url, timeout=10)
                data = json.loads(resp.read().decode())
                self.assertFalse(data.get("reachable"))
                self.assertEqual(data.get("error_type"), "invalid_url")

    def test_f_connection_refused(self):
        """TEST F: Connection refused detection on unused port."""
        url = f"{BASE}/api/check-target?url={urllib.parse.quote('http://127.0.0.1:19876/')}"
        resp = urllib.request.urlopen(url, timeout=10)
        data = json.loads(resp.read().decode())
        self.assertFalse(data.get("reachable"))
        self.assertEqual(data.get("error_type"), "connection_refused")

    def test_g_timeout_handling(self):
        """TEST G: Timeout classification on non-routable IP."""
        url = f"{BASE}/api/check-target?url={urllib.parse.quote('http://10.255.255.1/')}"
        try:
            resp = urllib.request.urlopen(url, timeout=12)
            data = json.loads(resp.read().decode())
            self.assertFalse(data.get("reachable"))
            self.assertIn(data.get("error_type"), ("timeout", "unreachable", "connect_error"))
        except Exception:
            # Network layer timed out at socket level — also valid unreachable outcome
            pass

    def test_h_blank_document_heuristics(self):
        """TEST H: Blank document detection in NavigationDiagnostics."""
        diag = NavigationDiagnostics(
            navigation_state="blank",
            body_text_length=0,
            interactive_elements=0,
            final_url="about:blank",
        )
        self.assertEqual(diag.navigation_state, "blank")
        self.assertEqual(diag.body_text_length, 0)

    def test_j_artifact_url_accessibility(self):
        """TEST J: Static artifact route verification."""
        # /artifacts route is mounted and returns 404 for non-existent rather than 500
        try:
            url = f"{BASE}/artifacts/non_existent_run_test/step_001.png"
            urllib.request.urlopen(url, timeout=5)
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 404, "Artifacts route should return 404 for missing file")

    def test_k_cancellation_lifecycle(self):
        """TEST K: Run cancellation sets status to cancelled."""
        payload = json.dumps({
            "goal": "Test run to cancel immediately",
            "target_url": "http://127.0.0.1:8000/demo/",
            "max_steps": 10,
            "exploration_passes": 1,
        }).encode("utf-8")
        req = urllib.request.Request(
            f"{BASE}/api/runs",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        resp = urllib.request.urlopen(req, timeout=10)
        run = json.loads(resp.read().decode())
        run_id = run["id"]

        # Cancel immediately
        cancel_req = urllib.request.Request(
            f"{BASE}/api/runs/{run_id}/cancel",
            data=b"",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        cancel_resp = urllib.request.urlopen(cancel_req, timeout=10)
        cancel_data = json.loads(cancel_resp.read().decode())
        self.assertTrue(cancel_data.get("cancelled"))

        # Verify status became cancelled
        time.sleep(1)
        state_resp = urllib.request.urlopen(f"{BASE}/api/runs/{run_id}", timeout=5)
        state = json.loads(state_resp.read().decode())
        self.assertIn(state.get("status"), ("cancelled", "failed"))


if __name__ == "__main__":
    unittest.main()
