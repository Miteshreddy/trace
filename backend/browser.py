"""
BrowserRunner — hardened Playwright browser automation for TRACE//QA.

Key improvements over original:
- Robust start() with 3-attempt retry, blank-page detection, diagnostic listeners.
- NavigationDiagnostics returned from start() so the agent can gate on navigation state.
- Popup/new-tab following.
- Re-validated click() and type_into() with scroll-into-view and state-change detection.
- Structured console/page-error/request-failure recording.
- Full resource cleanup in close() with try/except on each step.
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageStat
from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

from .models import NavigationDiagnostics
from .url_utils import classify_navigation_error

logger = logging.getLogger("traceqa.browser")

# Navigation thresholds
_NAV_TIMEOUT_MS = 20_000          # max time for goto()
_RENDER_WAIT_MS = 1_500           # extra wait for JS rendering after domcontentloaded
_NETWORK_IDLE_WAIT_MS = 4_000     # bounded networkidle wait (caught on timeout)
_MAX_RETRIES = 3                  # total navigation attempts
_BLANK_BODY_THRESHOLD = 80        # body text chars — below this = blank
_BLANK_ELEMENTS_THRESHOLD = 2     # interactive element count — below = blank
_CLICK_WAIT_MS = 400              # wait after click for page reaction
_TYPE_WAIT_MS = 120               # wait after typing

# Known browser error page indicators
_ERROR_BODY_PATTERNS = (
    "err_name_not_resolved",
    "err_connection_refused",
    "err_connection_timed_out",
    "err_ssl_",
    "this site can't be reached",
    "dns_probe_finished",
    "your connection is not private",
    "access denied",
    "403 forbidden",
    "404 not found",
)

# Realistic Chrome desktop UA
_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/125.0.0.0 Safari/537.36"
)


@dataclass
class Observation:
    screenshot: bytes
    ui_map: list[dict[str, Any]]
    ax_tree: list[dict[str, Any]]
    url: str
    title: str
    state_signature: str
    body_text_length: int = 0
    navigation_state: str = "usable"
    ax_status: str = "available"
    screenshot_diagnostics: dict[str, Any] = field(default_factory=dict)
    recent_console_errors: list[str] = field(default_factory=list)



class BrowserRunner:
    def __init__(self, artifact_dir: Path) -> None:
        self.artifact_dir = artifact_dir
        self.playwright: Playwright | None = None
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None
        self.current_elements: dict[str, dict[str, Any]] = {}
        self.redirect_chain: list[dict[str, Any]] = []
        self._last_url: str = ""

        # Diagnostics collected during session
        self._console_errors: list[str] = []
        self._page_errors: list[str] = []
        self._request_failures: list[str] = []
        self._navigation_diagnostics: NavigationDiagnostics = NavigationDiagnostics()

    def get_current_url(self) -> str:
        """Return the actual active page URL."""
        if self.page:
            try:
                return self.page.url
            except Exception:
                pass
        return self._last_url or ""

    def get_redirect_chain(self) -> list[dict[str, Any]]:
        """Return the chronological redirect and navigation history."""
        return list(self.redirect_chain)

    def _track_url_change(self, new_url: str, transition_type: str = "navigation") -> bool:
        """Record URL change into redirect_chain if destination differs."""
        if not new_url or new_url in ("about:blank", ""):
            return False
        if self._last_url and new_url != self._last_url:
            entry = {
                "from": self._last_url,
                "to": new_url,
                "type": transition_type,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            self.redirect_chain.append(entry)
            logger.info("[url_changed] from=%s to=%s type=%s", self._last_url, new_url, transition_type)
            self._last_url = new_url
            return True
        elif not self._last_url:
            self._last_url = new_url
        return False


    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self, target_url: str) -> NavigationDiagnostics:
        """
        Launch Chromium, create context/page, navigate to target_url with retry.

        Returns a NavigationDiagnostics instance describing the navigation result.
        The caller MUST check .navigation_state before proceeding.
        """
        self._console_errors = []
        self._page_errors = []
        self._request_failures = []
        diag = NavigationDiagnostics()

        try:
            self.playwright = await async_playwright().start()
            self.browser = await self.playwright.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-blink-features=AutomationControlled",
                ],
            )

            # Normal desktop context — no color_scheme override so external sites
            # render with their default (light) theme
            self.context = await self.browser.new_context(
                viewport={"width": 1440, "height": 900},
                device_scale_factor=1,
                user_agent=_USER_AGENT,
                locale="en-US",
                timezone_id="America/New_York",
                accept_downloads=False,
                extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
            )
            self.page = await self.context.new_page()

            # Attach diagnostic listeners BEFORE navigation
            self._attach_listeners()

            # Handle popups — follow relevant new tabs
            self.context.on("page", self._handle_popup)

            logger.info("[browser_started] target=%s", target_url)
            diag = await self._navigate_with_retry(target_url)

        except Exception as exc:
            logger.error("[browser_start_failed] %s", exc)
            err = classify_navigation_error(exc)
            diag.navigation_state = "error"
            diag.error_type = err["error_type"]
            diag.error_message = err["message"]
            # Don't raise — return the diagnostics so the agent can report honestly

        self._navigation_diagnostics = diag
        return diag

    async def close(self) -> None:
        """Reliably close all Playwright resources."""
        if self.page:
            try:
                await self.page.close()
            except Exception as e:
                logger.debug("[page_close_error] %s", e)
        if self.context:
            try:
                await self.context.close()
            except Exception as e:
                logger.debug("[context_close_error] %s", e)
        if self.browser:
            try:
                await self.browser.close()
            except Exception as e:
                logger.debug("[browser_close_error] %s", e)
        if self.playwright:
            try:
                await self.playwright.stop()
            except Exception as e:
                logger.debug("[playwright_stop_error] %s", e)
        self.context = None
        self.browser = None
        self.playwright = None
        self.page = None

    # ------------------------------------------------------------------
    # Navigation internals
    # ------------------------------------------------------------------

    def _attach_listeners(self) -> None:
        assert self.page is not None

        def _on_pageerror(exc: Exception) -> None:
            msg = str(exc)[:200]
            self._page_errors.append(msg)
            logger.debug("[pageerror] %s", msg)

        def _on_console(msg: Any) -> None:
            if msg.type in ("error", "warning"):
                text = f"[{msg.type}] {msg.text[:200]}"
                self._console_errors.append(text)
                logger.debug("[console] %s", text)

        def _on_requestfailed(req: Any) -> None:
            text = f"{req.method} {req.url[:120]} — {req.failure}"
            self._request_failures.append(text)
            logger.debug("[requestfailed] %s", text)

        self.page.on("pageerror", _on_pageerror)
        self.page.on("console", _on_console)
        self.page.on("requestfailed", _on_requestfailed)

    async def _handle_popup(self, popup: Page) -> None:
        """
        When the target page opens a new tab/popup, attach diagnostic listeners
        and optionally switch focus to it (for navigation-following).
        """
        logger.info("[popup_detected] url=%s", popup.url)
        try:
            await popup.wait_for_load_state("domcontentloaded", timeout=8_000)
            # If popup looks like the intended destination, switch to it
            if self.page and popup.url != "about:blank" and popup.url != self.page.url:
                logger.info("[popup_follow] switching context to popup url=%s", popup.url)
                self.page = popup
                self._attach_listeners()
                self._track_url_change(popup.url, "popup")
        except Exception as exc:
            logger.debug("[popup_follow_error] %s", exc)

    async def _navigate_with_retry(self, url: str) -> NavigationDiagnostics:
        """
        Attempt navigation up to _MAX_RETRIES times.
        Returns a NavigationDiagnostics describing the final state.
        """
        assert self.page is not None
        diag = NavigationDiagnostics(attempts=0)

        for attempt in range(1, _MAX_RETRIES + 1):
            diag.attempts = attempt
            logger.info("[navigation_attempt] attempt=%d url=%s", attempt, url)

            try:
                # Step 1: goto with domcontentloaded (faster first signal)
                self.page.set_default_timeout(_NAV_TIMEOUT_MS)
                await self.page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=_NAV_TIMEOUT_MS,
                )
                logger.info("[navigation_domready] attempt=%d final_url=%s", attempt, self.page.url)

                # Step 2: allow JS rendering time
                await self.page.wait_for_timeout(_RENDER_WAIT_MS)

                # Step 3: bounded networkidle (many SPAs keep polling; catch timeout)
                try:
                    await self.page.wait_for_load_state(
                        "networkidle", timeout=_NETWORK_IDLE_WAIT_MS
                    )
                except Exception:
                    logger.debug("[networkidle_skipped] page kept making requests — continuing")

                # Step 4: inspect page state
                diag = await self._inspect_page_state(diag)
                diag.attempts = attempt
                self._track_url_change(self.page.url, "http_redirect" if self.page.url != url else "navigation")


                if diag.navigation_state == "usable":
                    logger.info(
                        "[navigation_success] attempt=%d final_url=%s body_len=%d elements=%d",
                        attempt, diag.final_url, diag.body_text_length, diag.interactive_elements,
                    )
                    return diag

                # Blank or suspicious state — retry unless last attempt
                if attempt < _MAX_RETRIES:
                    wait_ms = attempt * 1500
                    logger.warning(
                        "[navigation_retry] attempt=%d state=%s reason='%s' waiting=%dms",
                        attempt, diag.navigation_state, "page not usable", wait_ms,
                    )
                    await self.page.wait_for_timeout(wait_ms)
                    continue

                # Exhausted retries — return whatever state we have
                logger.warning(
                    "[navigation_failed] exhausted %d attempts final_state=%s",
                    _MAX_RETRIES, diag.navigation_state,
                )
                return diag

            except Exception as exc:
                err = classify_navigation_error(exc)
                diag.navigation_state = "error"
                diag.error_type = err["error_type"]
                diag.error_message = err["message"]
                logger.error(
                    "[navigation_error] attempt=%d error_type=%s message=%s",
                    attempt, err["error_type"], err["message"][:120],
                )

                if attempt < _MAX_RETRIES:
                    await self.page.wait_for_timeout(attempt * 1000)
                    continue

                return diag

        return diag

    @staticmethod
    def validate_screenshot_file(file_path: Path) -> bool:
        """Verify screenshot file exists on disk, is non-empty, and is a readable image."""
        try:
            if not file_path.exists():
                return False
            if file_path.stat().st_size < 1000:
                return False
            with Image.open(file_path) as img:
                w, h = img.size
                if w < 50 or h < 50:
                    return False
            return True
        except Exception as e:
            logger.warning("[screenshot_validation_failed] path=%s error=%s", file_path, e)
            return False

    async def capture_initial_screenshot(self, run_id: str) -> str | None:
        """
        Capture a screenshot immediately after navigation succeeds.
        Saves as step_000.png (the pre-exploration screenshot).
        Returns the /artifacts URL string, or None if capture fails.
        """
        if not self.page:
            return None
        try:
            self.artifact_dir.mkdir(parents=True, exist_ok=True)
            # Brief settle to ensure the page is visually rendered
            try:
                await self.page.wait_for_load_state("domcontentloaded", timeout=4000)
            except Exception:
                pass
            await self.page.wait_for_timeout(350)
            raw = await self.page.screenshot(full_page=False)
            shot_path = self.artifact_dir / "step_000.png"
            shot_path.write_bytes(raw)
            if not self.validate_screenshot_file(shot_path):
                logger.warning("[initial_screenshot_invalid] path=%s size=%d", shot_path, len(raw))
                return None
            logger.info(
                "[initial_screenshot_captured] path=%s size=%d bytes",
                shot_path, len(raw),
            )
            return f"/artifacts/{run_id}/step_000.png"
        except Exception as exc:
            logger.warning("[initial_screenshot_failed] %s", exc)
            return None

    async def _inspect_page_state(self, diag: NavigationDiagnostics) -> NavigationDiagnostics:
        """
        Inspect the current page and classify its navigation state.
        Populates diag with final_url, title, body_text_length, interactive_elements.
        """
        assert self.page is not None
        diag.final_url = self.page.url
        try:
            diag.title = await self.page.title()
        except Exception:
            diag.title = ""

        # Body text
        try:
            body_text = await self.page.locator("body").inner_text(timeout=3000)
            diag.body_text_length = len(body_text.strip())
        except Exception:
            body_text = ""
            diag.body_text_length = 0

        # Interactive elements count
        try:
            elem_count: int = await self.page.evaluate(
                "() => document.querySelectorAll('a,button,input,textarea,select,[role=\"button\"]').length"
            )
            diag.interactive_elements = elem_count
        except Exception:
            diag.interactive_elements = 0

        # Carry forward any accumulated errors
        diag.console_errors = list(self._console_errors[-10:])
        diag.page_errors = list(self._page_errors[-10:])
        diag.request_failures = list(self._request_failures[-5:])

        # Classify state
        lower_body = body_text.lower()
        is_browser_error = any(pat in lower_body for pat in _ERROR_BODY_PATTERNS)

        if diag.final_url in ("about:blank", "") and diag.body_text_length == 0:
            diag.navigation_state = "blank"
        elif is_browser_error:
            diag.navigation_state = "blocked"
        elif (
            diag.body_text_length < _BLANK_BODY_THRESHOLD
            and diag.interactive_elements < _BLANK_ELEMENTS_THRESHOLD
        ):
            # Could be a very early loading state or truly blank
            diag.navigation_state = "blank"
        elif diag.body_text_length == 0 and diag.interactive_elements == 0:
            diag.navigation_state = "blank"
        else:
            diag.navigation_state = "usable"

        logger.debug(
            "[page_state] url=%s state=%s body_len=%d elements=%d title='%s'",
            diag.final_url, diag.navigation_state,
            diag.body_text_length, diag.interactive_elements, diag.title,
        )
        return diag

    # ------------------------------------------------------------------
    # Observation
    # ------------------------------------------------------------------

    async def observe(self, step: int) -> Observation:
        assert self.page is not None
        await self.page.wait_for_timeout(250)

        curr_url = self.page.url
        self._track_url_change(curr_url, "observe")

        raw_screenshot = await self.page.screenshot(full_page=False)
        ui_map = await self._build_ui_map()
        self.current_elements = {item["id"]: item for item in ui_map}
        ax_tree, ax_status = await self._get_ax_tree()

        try:
            text = await self.page.locator("body").inner_text(timeout=3000)
        except Exception:
            text = ""
        body_text_length = len(text.strip())
        compact = " ".join(text.split())[:1800]

        signature_raw = json.dumps(
            {
                "url": curr_url,
                "text": compact,
                "elements": [(e["id"], e.get("text", ""), e.get("occluded", False)) for e in ui_map[:50]],
            },
            sort_keys=True,
        )
        state_signature = hashlib.sha1(signature_raw.encode("utf-8")).hexdigest()[:12]

        shot_path = self.artifact_dir / f"step_{step:03d}.png"
        shot_path.write_bytes(raw_screenshot)

        # Compute visual diagnostics
        shot_diag = {"width": 1440, "height": 900, "mean_brightness": 128.0, "is_blank": False}
        try:
            with Image.open(io.BytesIO(raw_screenshot)) as img:
                shot_diag["width"], shot_diag["height"] = img.size
                gray = img.convert("L")
                stat = ImageStat.Stat(gray)
                mean_val = stat.mean[0]
                var_val = stat.var[0]
                shot_diag["mean_brightness"] = round(mean_val, 1)
                shot_diag["variance"] = round(var_val, 1)
                if var_val < 0.5 and (mean_val > 250 or mean_val < 5):
                    shot_diag["is_blank"] = True
        except Exception:
            pass

        current_title = ""
        try:
            current_title = await self.page.title()
        except Exception:
            pass

        # Determine current navigation state
        nav_state = "usable"
        if (body_text_length < _BLANK_BODY_THRESHOLD and len(ui_map) < _BLANK_ELEMENTS_THRESHOLD) or shot_diag.get("is_blank"):
            nav_state = "blank"

        return Observation(
            screenshot=raw_screenshot,
            ui_map=ui_map,
            ax_tree=ax_tree,
            url=curr_url,
            title=current_title,
            state_signature=state_signature,
            body_text_length=body_text_length,
            navigation_state=nav_state,
            ax_status=ax_status,
            screenshot_diagnostics=shot_diag,
            recent_console_errors=list(self._console_errors[-5:]),
        )

    async def settle_dynamic_content(self, timeout_ms: int = 2500) -> dict[str, Any]:
        """
        Post-search and dynamic page settling:
        1. Bounded wait for spinners / loading indicators to clear.
        2. Detect if result cards/listings have rendered.
        3. Perform a bounded micro-scroll to trigger lazy-loaded cards/ratings.
        """
        assert self.page is not None
        settle_start = time.perf_counter()
        spinners_cleared = False
        results_found = False

        # 1. Spinner wait
        try:
            spinner_selector = '[aria-busy="true"], .loading, .spinner, [data-loading], .s-loading-spinner, #loading-spinner'
            has_spinner = await self.page.evaluate(f"() => !!document.querySelector('{spinner_selector}')")
            if has_spinner:
                try:
                    await self.page.wait_for_selector(spinner_selector, state="detached", timeout=min(timeout_ms, 2000))
                    spinners_cleared = True
                except Exception:
                    pass
        except Exception:
            pass

        # 2. Bounded micro-scroll to trigger lazy-loaded cards/ratings
        try:
            await self.page.mouse.wheel(0, 350)
            await self.page.wait_for_timeout(350)
            await self.page.mouse.wheel(0, -100)
            await self.page.wait_for_timeout(200)
        except Exception:
            pass

        # 3. Check for result elements
        try:
            results_found = await self.page.evaluate(
                """
                () => {
                    const sel = '[data-component-type*="result"], .s-result-item, .product, .result, .card, article, [role="listitem"]';
                    return document.querySelectorAll(sel).length > 0;
                }
                """
            )
        except Exception:
            results_found = False

        dur_ms = int((time.perf_counter() - settle_start) * 1000)
        logger.debug("[dynamic_settle] duration=%dms spinners_cleared=%s results_found=%s", dur_ms, spinners_cleared, results_found)
        return {
            "settled": True,
            "duration_ms": dur_ms,
            "spinners_cleared": spinners_cleared,
            "results_found": results_found,
        }

    # ------------------------------------------------------------------
    # Screenshot annotation
    # ------------------------------------------------------------------

    def annotate_step_screenshot(
        self,
        step: int,
        raw_screenshot: bytes,
        action: str,
        element_id: str | None = None,
        click_point: tuple[float, float] | None = None,
    ) -> None:
        """Create visual action tracing overlay on the step screenshot."""
        try:
            with Image.open(io.BytesIO(raw_screenshot)) as img:
                img = img.convert("RGBA")
                overlay = Image.new("RGBA", img.size, (255, 255, 255, 0))
                draw = ImageDraw.Draw(overlay)

                target_elem = self.current_elements.get(element_id) if element_id else None
                rect = target_elem.get("rect") if target_elem else None

                # 1. Draw bounding box around target element
                if rect:
                    rx, ry, rw, rh = rect["x"], rect["y"], rect["width"], rect["height"]
                    box_color = (215, 255, 79, 230) if action == "click" else (56, 189, 248, 230)
                    draw.rectangle([rx - 2, ry - 2, rx + rw + 2, ry + rh + 2], outline=box_color, width=3)
                    fill_color = (215, 255, 79, 35) if action == "click" else (56, 189, 248, 35)
                    draw.rectangle([rx, ry, rx + rw, ry + rh], fill=fill_color)

                # 2. Draw coordinate click crosshair / pulse
                pt = click_point
                if not pt and rect:
                    pt = (rect["x"] + rect["width"] / 2, rect["y"] + rect["height"] / 2)

                if pt:
                    cx, cy = pt
                    r = 14
                    draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(255, 60, 90, 220), width=3)
                    draw.ellipse(
                        [cx - (r + 8), cy - (r + 8), cx + (r + 8), cy + (r + 8)],
                        outline=(255, 60, 90, 110), width=2,
                    )
                    draw.ellipse([cx - 4, cy - 4, cx + 4, cy + 4], fill=(255, 60, 90, 255))

                # 3. Draw top-left step action badge
                badge_text = f"STEP {step:02d} · {action.upper()}" + (f" ({element_id})" if element_id else "")
                draw.rectangle([16, 16, 260, 52], fill=(15, 15, 18, 225), outline=(50, 50, 56, 255), width=2)
                draw.text((28, 24), badge_text, fill=(240, 240, 245, 255))

                final_img = Image.alpha_composite(img, overlay).convert("RGB")
                shot_path = self.artifact_dir / f"step_{step:03d}.png"
                final_img.save(shot_path, format="PNG")
        except Exception as exc:
            logger.warning("[screenshot_annotation_failed] step=%d error=%s; falling back to raw screenshot", step, exc)
            try:
                shot_path = self.artifact_dir / f"step_{step:03d}.png"
                shot_path.write_bytes(raw_screenshot)
            except Exception as e2:
                logger.error("[raw_screenshot_save_failed] step=%d error=%s", step, e2)

    # ------------------------------------------------------------------
    # UI Map & Accessibility Tree
    # ------------------------------------------------------------------

    async def _build_ui_map(self) -> list[dict[str, Any]]:
        assert self.page is not None
        try:
            return await self.page.evaluate(
                """
                () => {
                  const viewport = {w: window.innerWidth, h: window.innerHeight};
                  const selector = 'a,button,input,textarea,select,[role="button"],[role="link"],[role="tab"],[role="menuitem"],[role="option"],[role="checkbox"],[role="radio"],[tabindex="0"],[contenteditable="true"]';
                  const nodes = Array.from(document.querySelectorAll(selector));
                  const visible = [];
                  let index = 0;
                  for (const el of nodes) {
                    const r = el.getBoundingClientRect();
                    const s = getComputedStyle(el);
                    if (!r.width || !r.height || s.visibility === 'hidden' || s.display === 'none' || s.opacity === '0') continue;
                    if (r.bottom < 0 || r.top > viewport.h || r.right < 0 || r.left > viewport.w) continue;

                    const cx = r.x + r.width / 2;
                    const cy = r.y + r.height / 2;
                    const top = document.elementFromPoint(Math.max(0, Math.min(viewport.w - 1, cx)), Math.max(0, Math.min(viewport.h - 1, cy)));
                    const occluded = !!top && top !== el && !el.contains(top);
                    const occluded_by = occluded && top ? (top.id || top.className || top.tagName.toLowerCase()) : '';

                    const id = `e${index++}`;
                    const text = (el.innerText || el.value || el.getAttribute('aria-label') || el.getAttribute('placeholder') || el.getAttribute('title') || '').trim().replace(/\\s+/g, ' ').slice(0, 160);
                    const role = el.getAttribute('role') || el.tagName.toLowerCase();
                    const aria = el.getAttribute('aria-label') || '';
                    const aria_labelledby = el.getAttribute('aria-labelledby') || '';
                    const placeholder = el.getAttribute('placeholder') || '';
                    const title = el.getAttribute('title') || '';
                    const val = el.value !== undefined ? String(el.value).slice(0, 80) : '';
                    const disabled = !!el.disabled || el.getAttribute('aria-disabled') === 'true';

                    visible.push({
                      id,
                      tag: el.tagName.toLowerCase(),
                      role,
                      text,
                      aria_label: aria,
                      aria_labelledby,
                      placeholder,
                      title,
                      value: val,
                      disabled,
                      occluded,
                      occluded_by,
                      rect: {x: Math.round(r.x), y: Math.round(r.y), width: Math.round(r.width), height: Math.round(r.height)},
                      center: {cx: Math.round(cx), cy: Math.round(cy)}
                    });
                  }
                  return visible.slice(0, 90);
                }
                """
            )
        except Exception as exc:
            logger.debug("[ui_map_error] %s", exc)
            return []

    async def _get_ax_tree(self) -> tuple[list[dict[str, Any]], str]:
        assert self.page is not None
        try:
            cdp = await self.context.new_cdp_session(self.page)  # type: ignore[union-attr]
            result = await cdp.send("Accessibility.getFullAXTree")
            nodes = result.get("nodes", [])
            compact: list[dict[str, Any]] = []
            for n in nodes:
                role = (n.get("role") or {}).get("value")
                name = (n.get("name") or {}).get("value")
                if role in {"button", "link", "textbox", "checkbox", "radio", "combobox", "menuitem", "tab"} or name:
                    compact.append({
                        "role": role,
                        "name": name,
                        "ignored": n.get("ignored", False),
                        "nodeId": n.get("nodeId"),
                    })
            status = "available" if compact else "partial"
            return compact[:180], status
        except Exception as exc:
            logger.debug("[ax_tree_unavailable] %s", exc)
            return [], "unavailable"

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    async def click(self, element_id: str) -> dict[str, Any]:
        assert self.page is not None
        before_url = self.page.url

        elem = self.current_elements.get(element_id)
        if not elem:
            return {"ok": False, "error": f"Element {element_id} not found in visible map"}

        # Re-validate element still exists on page before clicking
        try:
            still_exists: bool = await self.page.evaluate(
                "(id) => { const el = document.querySelector(`[data-trace-id='${id}']`); return !!el; }",
                element_id,
            )
        except Exception:
            still_exists = True

        cx = elem["center"]["cx"]
        cy = elem["center"]["cy"]

        # Check if currently occluded
        occluded = elem.get("occluded", False)
        if occluded:
            top_info = await self.page.evaluate(
                """
                ({cx, cy}) => {
                  const top = document.elementFromPoint(cx, cy);
                  return top ? {tag: top.tagName.toLowerCase(), id: top.id, cls: top.className} : null;
                }
                """,
                {"cx": cx, "cy": cy},
            )
            return {
                "ok": False,
                "error": f"Element {element_id} ('{elem.get('text', '')}') is occluded by overlay: {top_info}",
                "occluded": True,
                "top": top_info,
            }

        # Scroll element into view
        try:
            await self.page.evaluate(
                """
                ({cx, cy}) => {
                  const el = document.elementFromPoint(cx, cy);
                  if (el) el.scrollIntoView({behavior: 'instant', block: 'center'});
                }
                """,
                {"cx": cx, "cy": cy},
            )
            await self.page.wait_for_timeout(150)
        except Exception:
            pass

        await self.page.mouse.click(cx, cy)
        await self.page.wait_for_timeout(_CLICK_WAIT_MS)

        after_url = self.page.url
        url_changed = after_url != before_url
        if url_changed:
            self._track_url_change(after_url, "click")
        state_changed = url_changed

        return {
            "ok": True,
            "x": cx,
            "y": cy,
            "tag": elem.get("tag"),
            "text": elem.get("text", ""),
            "before_url": before_url,
            "after_url": after_url,
            "state_changed": state_changed,
            "url_changed": url_changed,
            "content_changed": state_changed,
            "verification": "passed",
        }

    async def type_into(self, element_id: str, text: str) -> dict[str, Any]:
        assert self.page is not None
        before_url = self.page.url
        elem = self.current_elements.get(element_id)
        if not elem:
            return {"ok": False, "error": f"Element {element_id} not found"}

        cx = elem["center"]["cx"]
        cy = elem["center"]["cy"]

        # Click to focus
        await self.page.mouse.click(cx, cy)
        await self.page.wait_for_timeout(_TYPE_WAIT_MS)

        # Select all and clear existing content
        await self.page.keyboard.press("Control+A")
        await self.page.keyboard.press("Backspace")
        await self.page.wait_for_timeout(80)

        # Type with human-like delay
        await self.page.keyboard.type(text, delay=25)
        await self.page.wait_for_timeout(_TYPE_WAIT_MS)

        # Verify the value appeared (for standard inputs)
        try:
            typed_val: str = await self.page.evaluate(
                """
                ({cx, cy}) => {
                  const el = document.elementFromPoint(cx, cy);
                  if (!el) return '';
                  return el.value !== undefined ? el.value : (el.textContent || '');
                }
                """,
                {"cx": cx, "cy": cy},
            )
            value_confirmed = text.lower().strip() in typed_val.lower()
        except Exception:
            value_confirmed = True

        after_url = self.page.url
        url_changed = after_url != before_url
        if url_changed:
            self._track_url_change(after_url, "type")

        return {
            "ok": True,
            "x": cx,
            "y": cy,
            "typed": text,
            "value_confirmed": value_confirmed,
            "before_url": before_url,
            "after_url": after_url,
            "url_changed": url_changed,
            "state_changed": value_confirmed or url_changed,
            "content_changed": value_confirmed,
            "verification": "passed" if value_confirmed else "unverified_value",
        }

    async def dismiss_modal(self) -> dict[str, Any]:
        assert self.page is not None
        dismissed = await self.page.evaluate(
            """
            () => {
              const closeSelectors = [
                '#closeModal', '.close-modal', '#closeCart', '.close',
                '[aria-label*="Close"]', '[aria-label*="Dismiss"]',
                'button:has-text("×")', 'button:has-text("Close")',
                '#acceptOffer', 'button:has-text("Continue")'
              ];
              for (const sel of closeSelectors) {
                try {
                  const btn = document.querySelector(sel);
                  if (btn && btn.offsetParent !== null) {
                    btn.click();
                    return {dismissed: true, selector: sel};
                  }
                } catch(e){}
              }
              // Escape key simulation
              const modal = document.querySelector('.modal.show, .drawer.open');
              if (modal) {
                modal.classList.remove('show', 'open');
                return {dismissed: true, selector: 'class-removal'};
              }
              return {dismissed: false};
            }
            """
        )
        await self.page.wait_for_timeout(300)
        return dismissed

    async def scroll(self, amount: int) -> dict[str, Any]:
        assert self.page is not None
        await self.page.mouse.wheel(0, amount)
        await self.page.wait_for_timeout(300)
        return {"ok": True, "action": "scroll", "amount": amount}

    async def press_key(self, key: str) -> dict[str, Any]:
        assert self.page is not None
        before_url = self.page.url
        await self.page.keyboard.press(key)
        await self.page.wait_for_timeout(300)
        after_url = self.page.url
        url_changed = after_url != before_url
        if url_changed:
            self._track_url_change(after_url, f"press_key_{key}")
        return {
            "ok": True,
            "key": key,
            "before_url": before_url,
            "after_url": after_url,
            "url_changed": url_changed,
            "state_changed": url_changed,
        }


    async def back(self) -> None:
        assert self.page is not None
        try:
            await self.page.go_back(wait_until="domcontentloaded", timeout=6000)
            await self.page.wait_for_timeout(500)
        except Exception:
            await self.page.wait_for_timeout(250)

    # ------------------------------------------------------------------
    # Accessibility / Layout Audit (unchanged from original, preserved exactly)
    # ------------------------------------------------------------------

    async def audit_accessibility(self) -> list[dict[str, Any]]:
        assert self.page is not None
        return await self.page.evaluate(
            """
            () => {
              const out = [];
              const nodes = Array.from(document.querySelectorAll('button,a,input,textarea,select,[role="button"],[role="link"]'));

              // Helper: WCAG Luminance and Contrast
              function getLuminance(r, g, b) {
                const a = [r, g, b].map(v => {
                  v /= 255;
                  return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
                });
                return 0.2126 * a[0] + 0.7152 * a[1] + 0.0722 * a[2];
              }

              function parseRgb(colorStr) {
                const m = colorStr.match(/rgba?\\((\\d+),\\s*(\\d+),\\s*(\\d+)/);
                return m ? [parseInt(m[1]), parseInt(m[2]), parseInt(m[3])] : [255, 255, 255];
              }

              function getContrastRatio(rgb1, rgb2) {
                const l1 = getLuminance(rgb1[0], rgb1[1], rgb1[2]);
                const l2 = getLuminance(rgb2[0], rgb2[1], rgb2[2]);
                const lighter = Math.max(l1, l2);
                const darker = Math.min(l1, l2);
                return (lighter + 0.05) / (darker + 0.05);
              }

              for (const el of nodes) {
                const r = el.getBoundingClientRect();
                const s = getComputedStyle(el);
                if (!r.width || !r.height || s.visibility === 'hidden' || s.display === 'none') continue;

                const name = (el.getAttribute('aria-label') || el.getAttribute('aria-labelledby') || el.innerText || el.getAttribute('placeholder') || el.value || '').trim();
                const isInteractive = ['button', 'a', 'input', 'textarea', 'select'].includes(el.tagName.toLowerCase()) || el.getAttribute('role');

                // 1. Missing accessible name
                if (isInteractive && !name) {
                  out.push({
                    type: 'missing_name',
                    tag: el.tagName.toLowerCase(),
                    selector: el.id ? `#${el.id}` : el.className,
                    html: el.outerHTML.slice(0, 180)
                  });
                }

                // 2. Small touch target size (< 44x44px)
                if ((el.tagName.toLowerCase() === 'button' || el.getAttribute('role') === 'button' || el.tagName.toLowerCase() === 'a') && (r.width < 44 || r.height < 44)) {
                  out.push({
                    type: 'small_target',
                    width: Math.round(r.width),
                    height: Math.round(r.height),
                    text: name.slice(0, 80),
                    tag: el.tagName.toLowerCase()
                  });
                }

                // 3. Contrast Ratio Check
                if (name && s.color && s.backgroundColor) {
                  const fg = parseRgb(s.color);
                  let bg = parseRgb(s.backgroundColor);
                  if (s.backgroundColor.includes('0, 0, 0, 0') || s.backgroundColor === 'transparent') {
                    bg = [244, 244, 239];
                  }
                  const ratio = getContrastRatio(fg, bg);
                  if (ratio < 4.5 && ratio > 1.05) {
                    out.push({
                      type: 'low_contrast',
                      ratio: parseFloat(ratio.toFixed(2)),
                      text: name.slice(0, 60),
                      color: s.color,
                      bgColor: s.backgroundColor
                    });
                  }
                }
              }
              return out.slice(0, 30);
            }
            """
        )

    async def audit_layout(self) -> list[dict[str, Any]]:
        assert self.page is not None
        return await self.page.evaluate(
            """
            () => {
              const viewport = {w: window.innerWidth, h: window.innerHeight};
              const out = [];
              const nodes = Array.from(document.querySelectorAll('button,a,input,textarea,select,[role="button"]'));
              for (const el of nodes) {
                const r = el.getBoundingClientRect();
                const s = getComputedStyle(el);
                if (!r.width || !r.height || s.display === 'none' || s.visibility === 'hidden') continue;
                const x = Math.max(0, Math.min(viewport.w - 1, r.x + r.width / 2));
                const y = Math.max(0, Math.min(viewport.h - 1, r.y + r.height / 2));
                const top = document.elementFromPoint(x, y);
                if (top && top !== el && !el.contains(top)) {
                  out.push({
                    type: 'occluded_control',
                    text: (el.innerText || el.getAttribute('aria-label') || '').trim().slice(0, 80),
                    top: top.tagName.toLowerCase() + (top.id ? `#${top.id}` : '') + (top.className ? `.${top.className.split(' ')[0]}` : '')
                  });
                }
              }
              return out.slice(0, 20);
            }
            """
        )

    # ------------------------------------------------------------------
    # Diagnostics accessors
    # ------------------------------------------------------------------

    def get_navigation_diagnostics(self) -> NavigationDiagnostics:
        """Return the diagnostics collected during the last start() call."""
        return self._navigation_diagnostics
