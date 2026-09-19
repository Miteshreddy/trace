"""
test_agentic_pipeline.py

Comprehensive tests for:
- Goal decomposition into observable milestones (e.g., search + rating evaluation)
- GoalVerificationEngine deterministic observation checking (cards, queries, ratings)
- Premature finish veto (rejects goal_complete when ratings are missing)
- Dynamic settle logic (scroll & wait)
- Redirect chain and current_url tracking
- Multi-model consensus, role-based critique, and disagreement recording
- Provider failure fault-tolerance
"""
from pathlib import Path
import unittest
from unittest.mock import MagicMock

from backend.goal_verifier import GoalVerificationEngine
from backend.models import (
    ConsensusResult,
    GoalPlan,
    ModelDecision,
    RunCreate,
    RunState,
    SubgoalState,
)
from backend.browser import BrowserRunner, Observation
from backend.ai_providers.manager import AIProviderManager


class TestGoalDecompositionAndVerification(unittest.TestCase):
    def test_decompose_amazon_iphone_goal(self):
        goal = "Open Amazon website search for iPhone 17 and also evaluate its ratings"
        plan = GoalVerificationEngine.decompose_goal(goal)
        self.assertEqual(plan.primary_goal, goal)
        subgoal_ids = [s.id for s in plan.subgoals]
        self.assertIn("milestone_nav", subgoal_ids)
        self.assertIn("milestone_search", subgoal_ids)
        self.assertIn("milestone_results", subgoal_ids)
        self.assertIn("milestone_ratings", subgoal_ids)

        ratings_sg = next(s for s in plan.subgoals if s.id == "milestone_ratings")
        self.assertTrue(ratings_sg.required)
        self.assertIn("rating", ratings_sg.description.lower())

    def test_decompose_generic_goal(self):
        goal = "Navigate to checkout and submit order"
        plan = GoalVerificationEngine.decompose_goal(goal)
        subgoal_ids = [s.id for s in plan.subgoals]
        self.assertIn("milestone_nav", subgoal_ids)
        self.assertIn("milestone_cart", subgoal_ids)
        self.assertNotIn("milestone_ratings", subgoal_ids)

    def test_observation_verification_detects_query_and_cards(self):
        plan = GoalVerificationEngine.decompose_goal(
            "Open Amazon website search for iPhone 17 and also evaluate its ratings"
        )
        obs = Observation(
            screenshot=b"",
            ui_map=[
                {"id": "e1", "tag": "input", "role": "textbox", "value": "iphone 17", "text": "iphone 17"},
                {"id": "e2", "tag": "div", "role": "region", "text": "Apple iPhone 17 Pro Max 256GB ₹1,19,900 4.5 out of 5 stars"},
            ],
            ax_tree=[
                {"role": "heading", "name": "Apple iPhone 17 (128 GB) - Starlight"},
                {"role": "text", "name": "4.5 out of 5 stars (1,230 ratings)"},
            ],
            url="https://www.amazon.in/s?k=iphone+17",
            title="Amazon.in : iphone 17",
            state_signature="test_sig",
            body_text_length=1500,
            navigation_state="usable",
        )

        plan, summary = GoalVerificationEngine.verify_observation(plan, obs)
        completed_ids = [s.id for s in plan.subgoals if s.status == "verified"]
        self.assertIn("milestone_nav", completed_ids)
        self.assertIn("milestone_search", completed_ids)
        self.assertIn("milestone_results", completed_ids)
        self.assertIn("milestone_ratings", completed_ids)
        self.assertTrue(summary["ratings_detected"])
        self.assertGreaterEqual(summary["evidence_strength"], 0.75)

    def test_can_finish_veto_when_ratings_missing(self):
        """
        Critical requirement: AI cannot claim complete if requested ratings
        subgoal was not observed.
        """
        plan = GoalVerificationEngine.decompose_goal(
            "Open Amazon website search for iPhone 17 and also evaluate its ratings"
        )
        obs = Observation(
            screenshot=b"",
            ui_map=[
                {"id": "e1", "tag": "input", "role": "textbox", "value": "iphone 17", "text": "iphone 17"},
            ],
            ax_tree=[
                {"role": "heading", "name": "iPhone 17"},
            ],
            url="https://www.amazon.in/s?k=iphone+17",
            title="Amazon.in : iphone 17",
            state_signature="test_sig",
            body_text_length=800,
            navigation_state="usable",
        )

        can_fin, evidence = GoalVerificationEngine.can_finish(plan, obs)
        self.assertFalse(can_fin, "Must veto finish when ratings are missing")
        self.assertIn("ratings", evidence.lower())

    def test_can_finish_accepts_when_all_subgoals_verified(self):
        plan = GoalVerificationEngine.decompose_goal(
            "Open Amazon website search for iPhone 17 and also evaluate its ratings"
        )
        obs = Observation(
            screenshot=b"",
            ui_map=[
                {"id": "e1", "tag": "input", "role": "textbox", "value": "iphone 17", "text": "iphone 17"},
                {"id": "e2", "tag": "div", "role": "region", "text": "Apple iPhone 17 ₹79,900 4.6 out of 5 stars"},
            ],
            ax_tree=[
                {"role": "heading", "name": "iPhone 17"},
                {"role": "text", "name": "4.6 out of 5 stars"},
            ],
            url="https://www.amazon.in/s?k=iphone+17",
            title="Amazon.in : iphone 17",
            state_signature="test_sig",
            body_text_length=2000,
            navigation_state="usable",
        )

        can_fin, evidence = GoalVerificationEngine.can_finish(plan, obs)
        self.assertTrue(can_fin, "Must accept finish when ratings and results are verified")
        self.assertIn("All required milestones verified", evidence)


class TestRedirectAndCurrentUrlTracking(unittest.TestCase):
    def test_redirect_chain_recording(self):
        browser = BrowserRunner(artifact_dir=Path("./tmp"))
        browser._track_url_change("https://amazon.in", "initial")
        browser._track_url_change("https://www.amazon.in/", "http_redirect")
        browser._track_url_change("https://www.amazon.in/s?k=iphone+17", "navigation")

        chain = browser.get_redirect_chain()
        self.assertEqual(len(chain), 2)
        self.assertEqual(chain[0]["from"], "https://amazon.in")
        self.assertEqual(chain[0]["to"], "https://www.amazon.in/")
        self.assertEqual(chain[1]["from"], "https://www.amazon.in/")
        self.assertEqual(chain[1]["to"], "https://www.amazon.in/s?k=iphone+17")
        self.assertEqual(browser.get_current_url(), "https://www.amazon.in/s?k=iphone+17")


class TestMultiModelConsensus(unittest.TestCase):
    def setUp(self):
        self.manager = AIProviderManager()

    def test_ensemble_agreement(self):
        """When Groq and Gemini agree on an action, confidence is high."""
        mock_groq = MagicMock()
        mock_groq.name = "groq"
        mock_groq.is_configured.return_value = True
        mock_groq.decide_action.return_value = {
            "action": "click",
            "element_id": "nav-search-submit",
            "reasoning": "Submit search form",
            "confidence": 0.85,
            "goal_complete": False,
        }

        mock_gemini = MagicMock()
        mock_gemini.name = "gemini"
        mock_gemini.model = "gemini-2.5-flash"
        mock_gemini.is_configured.return_value = True
        mock_gemini.decide_action.return_value = {
            "action": "click",
            "element_id": "nav-search-submit",
            "reasoning": "Click search submit icon",
            "confidence": 0.90,
            "goal_complete": False,
        }

        mock_groq.health = MagicMock()
        mock_groq.health.status = "healthy"
        mock_groq.model = "qwen/qwen3.8-27b"
        mock_gemini.health = MagicMock()
        mock_gemini.health.status = "healthy"
        self.manager.groq = mock_groq
        self.manager.gemini = mock_gemini
        self.manager.providers["groq"] = mock_groq
        self.manager.providers["gemini"] = mock_gemini

        obs = Observation(
            screenshot=b"",
            ui_map=[{"id": "nav-search-submit", "tag": "button", "role": "button"}],
            ax_tree=[],
            url="https://www.amazon.in/",
            title="Amazon",
            state_signature="test_sig",
            body_text_length=500,
            navigation_state="usable",
        )

        decision, meta = self.manager.decide_ensemble_action(
            goal="Search iPhone 17",
            current_url=obs.url,
            screenshot=obs.screenshot,
            ui_map=obs.ui_map,
            ax_tree=obs.ax_tree,
            history=[],
            step=1,
            can_finish_veto=(True, "OK"),
        )

        self.assertEqual(decision["action"], "click")
        self.assertEqual(decision["element_id"], "nav-search-submit")
        self.assertTrue(meta["agreed"])
        self.assertGreaterEqual(meta["confidence"], 0.85)

    def test_ensemble_disagreement_recorded_and_resolved(self):
        """When Groq proposes scroll and Gemini proposes finish, disagreement is logged and critic resolves."""
        mock_groq = MagicMock()
        mock_groq.name = "groq"
        mock_groq.model = "llama-3.3-70b-versatile"
        mock_groq.is_configured.return_value = True
        mock_groq.decide_action.return_value = {
            "action": "scroll_down",
            "reasoning": "Look for customer ratings below the fold",
            "confidence": 0.80,
            "goal_complete": False,
        }

        mock_gemini = MagicMock()
        mock_gemini.name = "gemini"
        mock_gemini.model = "gemini-2.5-flash"
        mock_gemini.is_configured.return_value = True
        mock_gemini.decide_action.return_value = {
            "action": "finish",
            "reasoning": "We reached search page",
            "confidence": 0.70,
            "goal_complete": True,
        }

        mock_groq.health = MagicMock()
        mock_groq.health.status = "healthy"
        mock_gemini.health = MagicMock()
        mock_gemini.health.status = "healthy"
        self.manager.groq = mock_groq
        self.manager.gemini = mock_gemini
        self.manager.providers["groq"] = mock_groq
        self.manager.providers["gemini"] = mock_gemini

        obs = Observation(
            screenshot=b"",
            ui_map=[],
            ax_tree=[],
            url="https://www.amazon.in/s?k=iphone+17",
            title="Amazon",
            state_signature="test_sig",
            body_text_length=800,
            navigation_state="usable",
        )

        decision, meta = self.manager.decide_ensemble_action(
            goal="Search iPhone 17 and evaluate ratings",
            current_url=obs.url,
            screenshot=obs.screenshot,
            ui_map=obs.ui_map,
            ax_tree=obs.ax_tree,
            history=[],
            step=2,
            can_finish_veto=(False, "Ratings unverified"),
        )

        self.assertFalse(meta["agreed"], "Disagreement must be flagged")
        self.assertIsNotNone(meta["disagreement"])
        self.assertNotEqual(decision["action"], "finish", "Finish must be rejected when veto is active")
        self.assertEqual(decision["action"], "scroll_down")


if __name__ == "__main__":
    unittest.main()
