"""
Unit and integration tests for TRACE//QA navigation reliability.

Tests:
1. URL normalization (bare domains, whitespace, path preservation, loopback preservation).
2. URL validation and scheme blocking (javascript:, file:, data:, etc.).
3. Loopback detection.
4. Error classification.
5. NavigationDiagnostics model serialization and defaults.
6. ActionValidator rejection of unsafe URL schemes in typed text.
7. Agent goal verification heuristic logic.
"""
from __future__ import annotations

import unittest
from backend.url_utils import (
    normalize_url,
    validate_url_scheme,
    is_loopback,
    classify_navigation_error,
)
from backend.models import NavigationDiagnostics, RunState, RunCreate, Issue
from backend.action_validator import ActionValidator, _contains_unsafe_url
from backend.agent import AutonomousTester


async def _dummy_emit(ev):
    pass


class TestUrlNormalization(unittest.TestCase):
    """Test URL normalization rules."""

    def test_bare_domain_gets_https(self):
        orig, norm = normalize_url("example.com")
        self.assertEqual(norm, "https://example.com")
        self.assertEqual(orig, "example.com")

    def test_bare_subdomain_gets_https(self):
        _, norm = normalize_url("www.example.com")
        self.assertEqual(norm, "https://www.example.com")

    def test_bare_domain_with_path_and_query(self):
        _, norm = normalize_url("example.com/search?q=test#top")
        self.assertEqual(norm, "https://example.com/search?q=test#top")

    def test_whitespace_trimmed(self):
        orig, norm = normalize_url("   https://example.com/path   ")
        self.assertEqual(norm, "https://example.com/path")

    def test_http_preserved(self):
        _, norm = normalize_url("http://127.0.0.1:8000/demo/")
        self.assertEqual(norm, "http://127.0.0.1:8000/demo/")

    def test_https_preserved(self):
        _, norm = normalize_url("https://github.com/trending")
        self.assertEqual(norm, "https://github.com/trending")

    def test_empty_string_raises(self):
        with self.assertRaises(ValueError):
            normalize_url("")
        with self.assertRaises(ValueError):
            normalize_url("   ")

    def test_blocked_schemes_raise(self):
        blocked = [
            "javascript:alert(1)",
            "file:///etc/passwd",
            "data:text/html,<h1>hi</h1>",
            "chrome://settings",
            "ftp://files.example.com",
            "blob:https://example.com/123",
            "ws://localhost:8000",
            "wss://localhost:8000",
        ]
        for url in blocked:
            with self.subTest(url=url):
                with self.assertRaises(ValueError):
                    normalize_url(url)


class TestUrlValidation(unittest.TestCase):
    """Test validate_url_scheme and is_loopback."""

    def test_validate_url_scheme(self):
        self.assertTrue(validate_url_scheme("http://example.com"))
        self.assertTrue(validate_url_scheme("https://example.com"))
        self.assertFalse(validate_url_scheme("ftp://example.com"))
        self.assertFalse(validate_url_scheme("javascript:alert(1)"))
        self.assertFalse(validate_url_scheme("file:///c:/path"))
        self.assertFalse(validate_url_scheme("not_a_url"))

    def test_is_loopback(self):
        self.assertTrue(is_loopback("http://localhost:8000/demo/"))
        self.assertTrue(is_loopback("http://127.0.0.1:8000"))
        self.assertTrue(is_loopback("http://127.0.0.2:3000"))
        self.assertTrue(is_loopback("http://0.0.0.0:8000"))
        self.assertFalse(is_loopback("https://example.com"))
        self.assertFalse(is_loopback("https://google.com"))


class TestErrorClassification(unittest.TestCase):
    """Test Playwright/network exception classification."""

    def test_timeout_error(self):
        res = classify_navigation_error(Exception("Timeout 30000ms exceeded"))
        self.assertEqual(res["error_type"], "navigation_timeout")

    def test_dns_error(self):
        res = classify_navigation_error(Exception("net::ERR_NAME_NOT_RESOLVED"))
        self.assertEqual(res["error_type"], "dns_failure")

    def test_connection_refused(self):
        res = classify_navigation_error(Exception("net::ERR_CONNECTION_REFUSED"))
        self.assertEqual(res["error_type"], "connection_refused")

    def test_ssl_error(self):
        res = classify_navigation_error(Exception("net::ERR_CERT_AUTHORITY_INVALID"))
        self.assertEqual(res["error_type"], "ssl_error")


class TestModelsAndDiagnostics(unittest.TestCase):
    """Test NavigationDiagnostics and RunState schemas."""

    def test_navigation_diagnostics_defaults(self):
        diag = NavigationDiagnostics()
        self.assertEqual(diag.navigation_state, "unknown")
        self.assertEqual(diag.attempts, 0)
        self.assertEqual(diag.final_url, "")
        self.assertEqual(diag.body_text_length, 0)
        self.assertEqual(diag.interactive_elements, 0)
        self.assertEqual(diag.console_errors, [])

    def test_run_state_with_diagnostics(self):
        run = RunState(
            id="test-run-1",
            goal="Test navigation",
            target_url="http://127.0.0.1:8000/demo/",
            original_target_url="127.0.0.1:8000/demo/",
            normalized_target_url="http://127.0.0.1:8000/demo/",
            final_url="http://127.0.0.1:8000/demo/",
            navigation_state="usable",
            navigation_diagnostics=NavigationDiagnostics(
                navigation_state="usable",
                attempts=1,
                body_text_length=1200,
                interactive_elements=8,
                title="TRACE Demo Store",
            ),
        )
        data = run.model_dump()
        self.assertEqual(data["navigation_state"], "usable")
        self.assertEqual(data["navigation_diagnostics"]["attempts"], 1)
        self.assertEqual(data["navigation_diagnostics"]["title"], "TRACE Demo Store")


class TestActionValidatorUnsafeUrl(unittest.TestCase):
    """Test that ActionValidator prevents disallowed URL schemes in typed text."""

    def test_contains_unsafe_url_helper(self):
        self.assertTrue(_contains_unsafe_url("javascript:alert(1)"))
        self.assertTrue(_contains_unsafe_url("file:///etc/passwd"))
        self.assertTrue(_contains_unsafe_url("data:text/html,test"))
        self.assertFalse(_contains_unsafe_url("https://example.com"))
        self.assertFalse(_contains_unsafe_url("hello world"))

    def test_type_action_with_unsafe_url_rejected(self):
        validator = ActionValidator()
        ui_map = [
            {"id": "e_search", "tag": "input", "type": "text", "text": ""}
        ]
        decision = {
            "action": "type",
            "element_id": "e_search",
            "text": "file:///etc/passwd",
        }
        ok, safe, reason = validator.validate(decision, ui_map)
        self.assertFalse(ok)
        self.assertEqual(safe["action"], "wait")
        self.assertIn("Unsafe URL scheme", reason)


class TestGoalVerification(unittest.IsolatedAsyncioTestCase):
    """Test the independent goal verification heuristics in AutonomousTester."""

    def setUp(self):
        self.run = RunState(
            id="test-goal-verify",
            goal="Open the product page and add an item to cart",
            target_url="http://127.0.0.1:8000/demo/",
            normalized_target_url="http://127.0.0.1:8000/demo/",
        )
        req = RunCreate(goal=self.run.goal, target_url=self.run.target_url)
        self.tester = AutonomousTester(self.run, req, _dummy_emit)

    async def test_verify_rejects_blank_page(self):
        from unittest.mock import AsyncMock, MagicMock
        from types import SimpleNamespace

        mock_browser = MagicMock()
        mock_browser.page.locator.return_value.inner_text = AsyncMock(return_value="")
        obs = SimpleNamespace(
            body_text_length=0,
            ui_map=[],
            url="about:blank",
            navigation_state="blank",
        )
        verified, evidence = await self.tester._verify_goal_completion(mock_browser, obs)
        self.assertFalse(verified)

    async def test_verify_accepts_when_sufficient_evidence(self):
        from unittest.mock import AsyncMock, MagicMock
        from types import SimpleNamespace

        mock_browser = MagicMock()
        mock_browser.page.locator.return_value.inner_text = AsyncMock(
            return_value="Shopping cart item added product page checkout"
        )
        obs = SimpleNamespace(
            body_text_length=1500,
            ui_map=[{"id": "e1"}, {"id": "e2"}, {"id": "e3"}],
            url="http://127.0.0.1:8000/demo/cart",
            navigation_state="usable",
        )
        verified, evidence = await self.tester._verify_goal_completion(mock_browser, obs)
        self.assertTrue(verified)
        self.assertIn("Page has 1500 chars", evidence)

    async def test_verify_rejects_without_substantive_signals(self):
        from unittest.mock import AsyncMock, MagicMock
        from types import SimpleNamespace

        mock_browser = MagicMock()
        mock_browser.page.locator.return_value.inner_text = AsyncMock(
            return_value="Nothing relevant"
        )
        obs = SimpleNamespace(
            body_text_length=50,
            ui_map=[{"id": "e1"}],
            url="http://127.0.0.1:8000/demo/",
            navigation_state="loading",
        )
        verified, evidence = await self.tester._verify_goal_completion(mock_browser, obs)
        self.assertFalse(verified)


if __name__ == "__main__":
    unittest.main()
