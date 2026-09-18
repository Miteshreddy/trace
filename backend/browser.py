from __future__ import annotations

import hashlib
import io
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from PIL import Image, ImageDraw, ImageFont

from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright


@dataclass
class Observation:
    screenshot: bytes
    ui_map: list[dict[str, Any]]
    ax_tree: list[dict[str, Any]]
    url: str
    title: str
    state_signature: str


class BrowserRunner:
    def __init__(self, artifact_dir: Path) -> None:
        self.artifact_dir = artifact_dir
        self.playwright: Playwright | None = None
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None
        self.current_elements: dict[str, dict[str, Any]] = {}

    async def start(self, target_url: str) -> None:
        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(headless=True)
        self.context = await self.browser.new_context(
            viewport={"width": 1440, "height": 900},
            device_scale_factor=1,
            color_scheme="dark",
        )
        self.page = await self.context.new_page()
        self.page.set_default_timeout(7000)
        await self.page.goto(target_url, wait_until="domcontentloaded")
        await self.page.wait_for_timeout(400)

    async def close(self) -> None:
        if self.context:
            await self.context.close()
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()

    async def observe(self, step: int) -> Observation:
        assert self.page is not None
        await self.page.wait_for_timeout(250)
        raw_screenshot = await self.page.screenshot(full_page=False)
        ui_map = await self._build_ui_map()
        self.current_elements = {item["id"]: item for item in ui_map}
        ax_tree = await self._get_ax_tree()

        text = await self.page.locator("body").inner_text(timeout=3000)
        compact = " ".join(text.split())[:1800]
        signature_raw = json.dumps(
            {
                "url": self.page.url,
                "text": compact,
                "elements": [(e["id"], e.get("text", ""), e.get("occluded", False)) for e in ui_map[:50]],
            },
            sort_keys=True,
        )
        state_signature = hashlib.sha1(signature_raw.encode("utf-8")).hexdigest()[:12]

        shot_path = self.artifact_dir / f"step_{step:03d}.png"
        shot_path.write_bytes(raw_screenshot)

        return Observation(
            screenshot=raw_screenshot,
            ui_map=ui_map,
            ax_tree=ax_tree,
            url=self.page.url,
            title=await self.page.title(),
            state_signature=state_signature,
        )

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
                    # Neon cyan / lime border
                    box_color = (215, 255, 79, 230) if action == "click" else (56, 189, 248, 230)
                    draw.rectangle([rx - 2, ry - 2, rx + rw + 2, ry + rh + 2], outline=box_color, width=3)
                    # Semi-transparent fill
                    fill_color = (215, 255, 79, 35) if action == "click" else (56, 189, 248, 35)
                    draw.rectangle([rx, ry, rx + rw, ry + rh], fill=fill_color)

                # 2. Draw coordinate click crosshair / pulse
                pt = click_point
                if not pt and rect:
                    pt = (rect["x"] + rect["width"] / 2, rect["y"] + rect["height"] / 2)

                if pt:
                    cx, cy = pt
                    r = 14
                    # Outer glowing pulse
                    draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=(255, 60, 90, 220), width=3)
                    draw.ellipse([cx - (r + 8), cy - (r + 8), cx + (r + 8), cy + (r + 8)], outline=(255, 60, 90, 110), width=2)
                    # Center bullseye dot
                    draw.ellipse([cx - 4, cy - 4, cx + 4, cy + 4], fill=(255, 60, 90, 255))

                # 3. Draw top-left step action badge
                badge_text = f"STEP {step:02d} · {action.upper()}" + (f" ({element_id})" if element_id else "")
                draw.rectangle([16, 16, 260, 52], fill=(15, 15, 18, 225), outline=(50, 50, 56, 255), width=2)
                draw.text((28, 24), badge_text, fill=(240, 240, 245, 255))

                # Merge overlay
                final_img = Image.alpha_composite(img, overlay).convert("RGB")
                shot_path = self.artifact_dir / f"step_{step:03d}.png"
                final_img.save(shot_path, format="PNG")
        except Exception:
            pass

    async def _build_ui_map(self) -> list[dict[str, Any]]:
        assert self.page is not None
        return await self.page.evaluate(
            """
            () => {
              const viewport = {w: window.innerWidth, h: window.innerHeight};
              const nodes = Array.from(document.querySelectorAll('a,button,input,textarea,select,[role="button"],[role="link"],[role="tab"],[tabindex="0"]'));
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
                const text = (el.innerText || el.value || el.getAttribute('aria-label') || el.getAttribute('placeholder') || '').trim().replace(/\\s+/g, ' ').slice(0, 160);
                const role = el.getAttribute('role') || el.tagName.toLowerCase();
                const aria = el.getAttribute('aria-label') || '';
                const placeholder = el.getAttribute('placeholder') || '';
                const disabled = !!el.disabled || el.getAttribute('aria-disabled') === 'true';

                visible.push({
                  id,
                  tag: el.tagName.toLowerCase(),
                  role,
                  text,
                  aria_label: aria,
                  placeholder,
                  disabled,
                  occluded,
                  occluded_by,
                  rect: {x: Math.round(r.x), y: Math.round(r.y), width: Math.round(r.width), height: Math.round(r.height)},
                  center: {cx: Math.round(cx), cy: Math.round(cy)}
                });
              }
              return visible.slice(0, 85);
            }
            """
        )

    async def _get_ax_tree(self) -> list[dict[str, Any]]:
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
            return compact[:180]
        except Exception:
            return []

    async def click(self, element_id: str) -> dict[str, Any]:
        assert self.page is not None
        elem = self.current_elements.get(element_id)
        if not elem:
            return {"ok": False, "error": f"Element {element_id} not found in visible map"}

        cx = elem["center"]["cx"]
        cy = elem["center"]["cy"]

        # Check if currently occluded
        occluded = elem.get("occluded", False)
        if occluded:
            # Check what's on top right now
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

        await self.page.mouse.click(cx, cy)
        await self.page.wait_for_timeout(300)
        return {"ok": True, "x": cx, "y": cy, "tag": elem.get("tag"), "text": elem.get("text", "")}

    async def type_into(self, element_id: str, text: str) -> dict[str, Any]:
        assert self.page is not None
        elem = self.current_elements.get(element_id)
        if not elem:
            return {"ok": False, "error": f"Element {element_id} not found"}

        cx = elem["center"]["cx"]
        cy = elem["center"]["cy"]

        await self.page.mouse.click(cx, cy)
        await self.page.wait_for_timeout(100)
        await self.page.keyboard.press("Control+A")
        await self.page.keyboard.press("Backspace")
        await self.page.keyboard.type(text, delay=20)
        return {"ok": True, "x": cx, "y": cy, "typed": text}

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

    async def scroll(self, amount: int) -> None:
        assert self.page is not None
        await self.page.mouse.wheel(0, amount)
        await self.page.wait_for_timeout(300)

    async def press_key(self, key: str) -> None:
        assert self.page is not None
        await self.page.keyboard.press(key)
        await self.page.wait_for_timeout(300)

    async def back(self) -> None:
        assert self.page is not None
        try:
            await self.page.go_back(wait_until="domcontentloaded", timeout=4000)
        except Exception:
            await self.page.wait_for_timeout(250)

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
                  // If background is transparent, assume parent background or white
                  if (s.backgroundColor.includes('0, 0, 0, 0') || s.backgroundColor === 'transparent') {
                    bg = [244, 244, 239]; // Default light surface
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
