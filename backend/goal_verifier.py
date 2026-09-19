"""
GoalVerificationEngine — independent goal decomposition and observable milestone verification.

Responsibilities:
1. Decomposes natural language goals into verifiable observable subgoals.
2. Evaluates real browser DOM, UI map, AX tree, and text for concrete evidence.
3. Guards against premature finish claims (e.g. searching for a product does NOT
   satisfy a goal that requires evaluating ratings).
4. Provides deterministic evidence scoring without relying blindly on LLM claims.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from .models import GoalPlan, SubgoalState

logger = logging.getLogger("traceqa.goal_verifier")

# Common stop words for keyword extraction
_STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
    "has", "he", "in", "is", "it", "its", "of", "on", "or", "that",
    "the", "to", "was", "were", "will", "with", "also", "open", "website",
    "site", "page", "its", "and", "can", "you", "please",
}

# Regex patterns for rating detection across e-commerce and general websites
_RATING_PATTERNS = [
    re.compile(r"\b([1-5](?:\.[0-9])?)\s*(?:out of 5|stars?|★|stars? & up)\b", re.IGNORECASE),
    re.compile(r"\b([1-5](?:\.[0-9])?)\s*/\s*5\b", re.IGNORECASE),
    re.compile(r"\b([1-9][0-9]*(?:,[0-9]{3})*)\s*(?:ratings?|reviews?|customer reviews?)\b", re.IGNORECASE),
    re.compile(r"\((?:[1-9][0-9]*(?:,[0-9]{3})*)\)", re.IGNORECASE),  # e.g. (1,234)
]


class GoalVerificationEngine:
    @staticmethod
    def decompose_goal(goal: str) -> GoalPlan:
        """
        Decompose a natural language goal into structured, verifiable subgoals.
        Does NOT invent implementation-specific CSS selectors.
        """
        lower = goal.lower()
        subgoals: list[SubgoalState] = []
        success_conditions: list[str] = []
        evidence_requirements: list[str] = []

        # 1. Milestone: Navigation
        subgoals.append(SubgoalState(
            id="milestone_nav",
            description="Navigate to target URL and confirm interface is usable",
            status="pending",
            required=True,
            observable_signals=["navigation_state == usable", "body_text_length >= 200", "ui_elements > 0"],
        ))
        success_conditions.append("Target page loads with interactive elements and content")
        evidence_requirements.append("Navigation state must be 'usable'")

        # 2. Milestone: Search (if requested)
        has_search = any(k in lower for k in ["search", "find", "look for", "query", "type"])
        query_match = re.search(r'(?:search for|search|find)\s+([^,.]+?)(?:\s+(?:and|also|with|on)|$)', goal, re.IGNORECASE)
        extracted_query = query_match.group(1).strip() if query_match else ""

        if has_search:
            subgoals.append(SubgoalState(
                id="milestone_search",
                description=f"Submit search query{f' for {extracted_query}' if extracted_query else ''}",
                status="pending",
                required=True,
                observable_signals=["input value contains query", "search submission action taken"],
            ))
            subgoals.append(SubgoalState(
                id="milestone_results",
                description="Confirm search result listings or product cards have rendered",
                status="pending",
                required=True,
                observable_signals=["result cards detected", "result count > 0", "search query in page text"],
            ))
            success_conditions.append(f"Search results for '{extracted_query or 'query'}' are visible")
            evidence_requirements.append("At least 1 product/result card or result header must be observable")

        # 3. Milestone: Ratings / Reviews (if requested)
        has_ratings = any(k in lower for k in ["rating", "ratings", "review", "reviews", "stars", "evaluate its rating"])
        if has_ratings:
            subgoals.append(SubgoalState(
                id="milestone_ratings",
                description="Inspect observable rating information (star scores, review count, or rating badges)",
                status="pending",
                required=True,
                observable_signals=["rating regex match", "star rating aria-label", "review count in product card"],
            ))
            success_conditions.append("Product rating or customer review score is observed and evaluated")
            evidence_requirements.append("Observable star rating or review count text must be detected")

        # 4. Milestone: Cart / Checkout (if requested)
        has_cart = any(k in lower for k in ["cart", "basket", "bag", "checkout", "buy", "purchase", "add to cart"])
        if has_cart:
            subgoals.append(SubgoalState(
                id="milestone_cart",
                description="Add item to cart or reach checkout interface",
                status="pending",
                required=True,
                observable_signals=["cart count updated", "checkout header/button visible", "url contains cart/checkout"],
            ))
            success_conditions.append("Item added to cart or checkout reached")
            evidence_requirements.append("Cart confirmation or checkout URL observed")

        # 5. Milestone: General inspection (if no specific subgoals matched)
        if not has_search and not has_ratings and not has_cart:
            # Extract meaningful keywords for generic verification
            words = [w for w in re.findall(r'\b[a-zA-Z]{3,}\b', lower) if w not in _STOP_WORDS]
            subgoals.append(SubgoalState(
                id="milestone_inspect",
                description=f"Interact with target page and confirm content matching '{' '.join(words[:4])}'",
                status="pending",
                required=True,
                observable_signals=["keyword match >= 30%", "target state changed"],
            ))
            success_conditions.append("Key elements corresponding to user goal are inspected")
            evidence_requirements.append("Keyword presence and state transition confirmed")

        return GoalPlan(
            primary_goal=goal,
            subgoals=subgoals,
            success_conditions=success_conditions,
            evidence_requirements=evidence_requirements,
        )

    @staticmethod
    def verify_observation(
        plan: GoalPlan,
        obs: Any,
        page: Any = None,
        last_action: dict[str, Any] | None = None,
    ) -> tuple[GoalPlan, dict[str, Any]]:
        """
        Deterministically inspect page state and update subgoal progress.
        Returns (updated_plan, evidence_summary).
        """
        body_len = getattr(obs, "body_text_length", 0)
        nav_state = getattr(obs, "navigation_state", "usable")
        ui_map = getattr(obs, "ui_map", [])
        current_url = getattr(obs, "url", "")
        title = getattr(obs, "title", "")

        evidence_signals: list[str] = []
        ratings_found: list[str] = []
        result_cards_count = 0
        query_present = False
        results_detected = False

        # Extract visible text from ui_map and page
        ui_texts = " ".join(e.get("text", "") for e in ui_map if e.get("text"))
        all_text = (ui_texts + " " + title).lower()

        # Check 1: Navigation milestone
        nav_subgoal = next((s for s in plan.subgoals if s.id == "milestone_nav"), None)
        if nav_subgoal and nav_subgoal.status != "verified":
            if nav_state == "usable" and body_len > 100:
                nav_subgoal.status = "verified"
                nav_subgoal.evidence = f"Target loaded usably (body={body_len} chars, elements={len(ui_map)})"
                evidence_signals.append("navigation_verified")

        # Check 2: Search query presence
        search_subgoal = next((s for s in plan.subgoals if s.id == "milestone_search"), None)
        if search_subgoal:
            # Check if an input field currently has a value matching query or action was type
            search_query = ""
            for s in plan.subgoals:
                if "for " in s.description:
                    search_query = s.description.split("for ")[-1].strip().lower()
                    break

            # Search in UI map inputs and recent action
            for e in ui_map:
                val = (e.get("text") or "").lower()
                placeholder = (e.get("placeholder") or "").lower()
                if search_query and search_query in val:
                    query_present = True
                    break

            if last_action and last_action.get("action") == "type" and search_query:
                typed_text = (last_action.get("text") or "").lower()
                if search_query in typed_text:
                    query_present = True

            if query_present or ("?k=" in current_url or "search" in current_url.lower()):
                if search_subgoal.status != "verified":
                    search_subgoal.status = "verified"
                    search_subgoal.evidence = f"Search query '{search_query}' entered and submitted"
                    evidence_signals.append("search_query_verified")

        # Check 3: Results detection
        results_subgoal = next((s for s in plan.subgoals if s.id == "milestone_results"), None)
        if results_subgoal:
            # Indicators of search result presence:
            # 1. URL contains search parameters (/s?k=, /search, /results)
            # 2. Result headers: "results for", "showing ... results", "results"
            # 3. Product items or repeated cards in UI map
            url_has_search = any(k in current_url.lower() for k in ["/s?", "/search", "k=", "q=", "results"])
            text_has_result_header = any(k in all_text for k in ["results for", "results of", "showing", "items found", "results"])
            
            # Count result cards by checking UI map items that look like products/listings
            product_card_count = 0
            for e in ui_map:
                txt = (e.get("text") or "")
                # Products typically have prices (₹, $, £, €) or rating patterns
                if any(curr in txt for curr in ["₹", "$", "£", "€", "USD", "INR"]) or any(p.search(txt) for p in _RATING_PATTERNS[:2]):
                    product_card_count += 1

            result_cards_count = product_card_count
            if url_has_search or text_has_result_header or product_card_count >= 2:
                results_detected = True
                if results_subgoal.status != "verified":
                    results_subgoal.status = "verified"
                    results_subgoal.evidence = f"Search results detected (url={current_url}, cards={product_card_count})"
                    evidence_signals.append("results_page_verified")

        # Check 4: Ratings detection
        ratings_subgoal = next((s for s in plan.subgoals if s.id == "milestone_ratings"), None)
        if ratings_subgoal:
            # Scan UI map texts, aria labels, and title
            for e in ui_map:
                aria = (e.get("aria_label") or "")
                txt = (e.get("text") or "")
                combined = f"{aria} {txt}"
                for pat in _RATING_PATTERNS:
                    m = pat.search(combined)
                    if m:
                        match_str = m.group(0).strip()
                        if match_str not in ratings_found:
                            ratings_found.append(match_str)

            # Also inspect visible text snippet if available
            for pat in _RATING_PATTERNS:
                matches = pat.findall(all_text)
                for match in matches[:3]:
                    if isinstance(match, tuple):
                        match = " ".join(match)
                    if match and match not in ratings_found:
                        ratings_found.append(str(match))

            if ratings_found:
                if ratings_subgoal.status != "verified":
                    ratings_subgoal.status = "verified"
                    ratings_subgoal.evidence = f"Observable rating information evaluated: {', '.join(ratings_found[:4])}"
                    evidence_signals.append("ratings_verified")

        # Check 5: General inspection fallback
        inspect_subgoal = next((s for s in plan.subgoals if s.id == "milestone_inspect"), None)
        if inspect_subgoal and inspect_subgoal.status != "verified":
            goal_words = set(plan.primary_goal.lower().split()) - _STOP_WORDS
            matched = sum(1 for w in goal_words if w in all_text)
            ratio = matched / max(len(goal_words), 1)
            if ratio >= 0.25 and body_len > 250:
                inspect_subgoal.status = "verified"
                inspect_subgoal.evidence = f"Observed matching content (matched {matched}/{len(goal_words)} keywords)"
                evidence_signals.append("content_inspected")

        # Compute overall status
        required_subgoals = [s for s in plan.subgoals if s.required]
        verified_count = sum(1 for s in required_subgoals if s.status == "verified")
        all_required_verified = (verified_count == len(required_subgoals))

        evidence_strength = verified_count / max(len(required_subgoals), 1)
        if nav_state == "blocked":
            overall_status = "blocked"
        elif all_required_verified:
            overall_status = "verified"
        elif verified_count > 0:
            overall_status = "partially_verified"
        else:
            overall_status = "not_verified"

        summary = {
            "search_subgoal": bool(search_subgoal),
            "query_present": query_present,
            "results_detected": results_detected,
            "visible_result_count": result_cards_count,
            "ratings_detected": bool(ratings_found),
            "ratings_evidence": ratings_found[:5],
            "evidence_strength": round(evidence_strength, 2),
            "verified_subgoals": f"{verified_count}/{len(required_subgoals)}",
            "all_required_verified": all_required_verified,
            "status": overall_status,
            "signals": evidence_signals,
        }

        logger.debug(
            "[goal_verifier_eval] status=%s verified=%s strength=%.2f ratings=%d cards=%d",
            overall_status, summary["verified_subgoals"], evidence_strength,
            len(ratings_found), result_cards_count,
        )

        return plan, summary

    @staticmethod
    def can_finish(plan: GoalPlan, obs: Any) -> tuple[bool, str]:
        """
        Conservative completion guard.
        Returns (can_finish: bool, rationale: str).
        """
        # Run verification pass against current observation to ensure latest state is reflected
        plan, _ = GoalVerificationEngine.verify_observation(plan, obs)

        required_subgoals = [s for s in plan.subgoals if s.required]
        unverified = [s for s in required_subgoals if s.status != "verified"]

        if not unverified:
            verified_descriptions = [f"✓ {s.description} ({s.evidence})" for s in required_subgoals]
            return True, "All required milestones verified: " + " | ".join(verified_descriptions)

        # There are unverified subgoals!
        missing = [s.description for s in unverified]
        missing_str = "; ".join(missing)

        # Check specifically if ratings subgoal is blocking
        ratings_missing = any(s.id == "milestone_ratings" for s in unverified)
        if ratings_missing:
            return False, (
                f"Cannot finish yet: Goal requires evaluating product ratings/reviews, but no observable "
                f"star ratings or review metrics have been confirmed on page. Missing: [{missing_str}]. "
                f"Agent must inspect/scroll to rating elements."
            )

        return False, f"Cannot finish: Remaining unverified subgoals: [{missing_str}]."
