from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from .models import Issue


def generate_report(
    run_dir: Path,
    run_id: str,
    goal: str,
    target_url: str,
    status: str,
    trajectory: list[dict[str, Any]],
    issues: list[Issue],
    summary: str,
    metrics: dict[str, Any],
    journey: dict[str, Any] | None = None,
    original_target_url: str = "",
    normalized_target_url: str = "",
    final_url: str = "",
    navigation_state: str = "",
    navigation_diagnostics: dict[str, Any] | None = None,
    goal_verification_evidence: str = "",
) -> None:
    # 1. Save machine-readable report.json
    data = {
        "run_id": run_id,
        "goal": goal,
        "target_url": target_url,
        "original_target_url": original_target_url or target_url,
        "normalized_target_url": normalized_target_url or target_url,
        "final_url": final_url or target_url,
        "navigation_state": navigation_state or "usable",
        "navigation_diagnostics": navigation_diagnostics or {},
        "goal_verification_evidence": goal_verification_evidence,
        "status": status,
        "metrics": metrics,
        "summary": summary,
        "issues": [x.model_dump() for x in issues],
        "trajectory": trajectory,
        "journey": journey or {"nodes": [], "edges": []},
    }
    (run_dir / "report.json").write_text(json.dumps(data, indent=2), encoding="utf-8")

    # 2. Build Issue Cards HTML
    issue_cards = []
    for i in issues:
        rec_html = (
            f"<div class='rec'><b>Developer Recommendation:</b> {html.escape(i.recommendation)}</div>"
            if i.recommendation
            else ""
        )
        card = (
            f"<article class='issue-card' data-category='{html.escape(i.category)}'>"
            f"<div class='issue-top'>"
            f"<span class='badge sev-{html.escape(i.severity)}'>{html.escape(i.severity.upper())}</span>"
            f"<span class='cat-label'>{html.escape(i.category.upper())}</span>"
            f"<span class='src-pill'>{html.escape(i.source)}</span>"
            f"</div>"
            f"<h3 class='issue-title'>{html.escape(i.title)}</h3>"
            f"<p class='issue-desc'>{html.escape(i.description)}</p>"
            f"<div class='evidence-block'><span class='ev-label'>Observed Evidence:</span><code>{html.escape(i.evidence)}</code></div>"
            f"{rec_html}"
            f"</article>"
        )
        issue_cards.append(card)

    issues_html = "".join(issue_cards) if issue_cards else "<div class='empty-note'>No issues detected. Interface demonstrated zero friction!</div>"

    # 3. Build Trajectory Step Cards with Annotated Screenshots
    step_cards = []
    for t in trajectory:
        step_no = t.get("step", 1)
        action = str(t.get("action", "")).upper()
        rationale = t.get("rationale", "")
        conf = int(float(t.get("confidence", 0.5)) * 100)
        img_name = f"step_{step_no:03d}.png"
        has_img = (run_dir / img_name).exists()
        img_html = f"<a href='{html.escape(img_name)}' target='_blank'><img src='{html.escape(img_name)}' alt='Step {step_no}' class='step-thumb' /></a>" if has_img else ""

        step_card = (
            f"<div class='step-row'>"
            f"<div class='step-badge'><span>{step_no:02d}</span></div>"
            f"<div class='step-body'>"
            f"<div class='step-header'><span class='action-tag'>{html.escape(action)}</span> <span class='conf-tag'>CONF {conf}%</span></div>"
            f"<p class='step-reason'>{html.escape(rationale)}</p>"
            f"<div class='step-meta'>Target: <code>{html.escape(str(t.get('element_id') or 'Viewport'))}</code> | Expected: {html.escape(str(t.get('expected_result') or '—'))}</div>"
            f"</div>"
            f"<div class='step-visual'>{img_html}</div>"
            f"</div>"
        )
        step_cards.append(step_card)

    trajectory_html = "".join(step_cards)

    # 4. Metric calculations for display
    friction_score = metrics.get("friction_score", 20)
    friction_color = "#34d399" if friction_score < 30 else ("#fbbf24" if friction_score < 60 else "#f87171")
    a11y_score = metrics.get("accessibility_score", 85)
    a11y_color = "#34d399" if a11y_score >= 80 else ("#fbbf24" if a11y_score >= 60 else "#f87171")

    # 5. Assemble Executive-Grade HTML Page
    page = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>TRACE//QA Audit Report · {html.escape(run_id)}</title>
  <style>
    :root {{
      --bg: #09090b;
      --card: #121215;
      --border: #27272a;
      --accent: #d7ff4f;
      --text: #f4f4f5;
      --muted: #a1a1aa;
      --blue: #38bdf8;
      --danger: #ef4444;
      --warning: #f59e0b;
      --success: #10b981;
    }}
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
      background: var(--bg);
      color: var(--text);
      font-family: Inter, system-ui, -apple-system, sans-serif;
      line-height: 1.5;
      padding: 40px 24px;
    }}
    .container {{
      max-width: 1200px;
      margin: 0 auto;
    }}
    .header {{
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      border-bottom: 1px solid var(--border);
      padding-bottom: 28px;
      margin-bottom: 32px;
      flex-wrap: wrap;
      gap: 16px;
    }}
    .brand-tag {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.14em;
      color: var(--accent);
      font-weight: 700;
      margin-bottom: 8px;
    }}
    .brand-tag::before {{
      content: "";
      display: inline-block;
      width: 8px;
      height: 8px;
      background: var(--accent);
      border-radius: 2px;
      box-shadow: 0 0 12px var(--accent);
    }}
    h1 {{
      font-size: 32px;
      letter-spacing: -0.03em;
      line-height: 1.2;
      color: #fff;
      max-width: 800px;
    }}
    .target-url {{
      margin-top: 8px;
      font-size: 13px;
      color: var(--muted);
      display: flex;
      align-items: center;
      gap: 12px;
    }}
    .target-url code {{
      background: #18181b;
      padding: 3px 8px;
      border-radius: 6px;
      color: var(--blue);
    }}
    .header-actions {{
      display: flex;
      gap: 10px;
    }}
    .btn {{
      background: #27272a;
      color: #fff;
      text-decoration: none;
      font-size: 12px;
      font-weight: 600;
      padding: 10px 16px;
      border-radius: 8px;
      display: inline-flex;
      align-items: center;
      gap: 6px;
      transition: background 0.2s;
    }}
    .btn:hover {{ background: #3f3f46; }}
    .btn-primary {{
      background: var(--accent);
      color: #09090b;
    }}
    .btn-primary:hover {{ background: #e5ff80; }}

    /* Scorecards Grid */
    .scorecards {{
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 16px;
      margin-bottom: 36px;
    }}
    .scorecard {{
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 14px;
      padding: 20px;
      position: relative;
      overflow: hidden;
    }}
    .card-label {{
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.12em;
      color: var(--muted);
      margin-bottom: 6px;
    }}
    .card-val {{
      font-size: 36px;
      font-weight: 800;
      letter-spacing: -0.04em;
      line-height: 1;
    }}
    .card-sub {{
      font-size: 11px;
      color: var(--muted);
      margin-top: 6px;
    }}

    /* Section Panels */
    .panel {{
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 16px;
      padding: 24px;
      margin-bottom: 28px;
    }}
    .panel-title {{
      font-size: 18px;
      font-weight: 700;
      letter-spacing: -0.02em;
      margin-bottom: 16px;
      display: flex;
      align-items: center;
      justify-content: space-between;
    }}
    .summary-text {{
      font-size: 15px;
      line-height: 1.6;
      color: #e4e4e7;
    }}

    /* Issues Grid */
    .issues-grid {{
      display: grid;
      grid-template-columns: repeat(2, 1fr);
      gap: 16px;
    }}
    .issue-card {{
      background: #16161a;
      border: 1px solid var(--border);
      border-radius: 12px;
      padding: 18px;
      display: flex;
      flex-direction: column;
    }}
    .issue-top {{
      display: flex;
      align-items: center;
      gap: 8px;
      margin-bottom: 10px;
    }}
    .badge {{
      font-size: 9px;
      font-weight: 800;
      letter-spacing: 0.1em;
      padding: 3px 7px;
      border-radius: 999px;
    }}
    .sev-critical {{ background: #451a1c; color: #f87171; border: 1px solid #7f1d1d; }}
    .sev-high {{ background: #451a1c; color: #f87171; border: 1px solid #7f1d1d; }}
    .sev-medium {{ background: #3b2d13; color: #fbbf24; border: 1px solid #78350f; }}
    .sev-low {{ background: #143523; color: #34d399; border: 1px solid #065f46; }}
    .cat-label {{ font-size: 10px; color: var(--muted); font-weight: 600; }}
    .src-pill {{ font-size: 9px; margin-left: auto; color: #71717a; text-transform: uppercase; }}
    .issue-title {{
      font-size: 14px;
      font-weight: 700;
      color: #fff;
      margin-bottom: 6px;
    }}
    .issue-desc {{
      font-size: 12px;
      color: #a1a1aa;
      margin-bottom: 12px;
      line-height: 1.5;
    }}
    .evidence-block {{
      background: #0d0d10;
      border: 1px solid #202025;
      border-radius: 8px;
      padding: 8px 10px;
      font-size: 11px;
      margin-bottom: 10px;
    }}
    .ev-label {{ display: block; font-size: 9px; text-transform: uppercase; color: #71717a; margin-bottom: 2px; }}
    .evidence-block code {{ color: #d4d4d8; word-break: break-word; font-family: monospace; }}
    .rec {{
      margin-top: auto;
      background: #111a24;
      border: 1px solid #1e3a5f;
      border-radius: 8px;
      padding: 8px 10px;
      font-size: 11px;
      color: #93c5fd;
      line-height: 1.45;
    }}

    /* Trajectory Row */
    .step-row {{
      display: grid;
      grid-template-columns: 48px 1fr 180px;
      gap: 16px;
      align-items: center;
      padding: 16px 0;
      border-bottom: 1px solid #202025;
    }}
    .step-badge span {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 38px;
      height: 38px;
      background: #18181b;
      border: 1px solid #27272a;
      border-radius: 10px;
      font-size: 13px;
      font-weight: 700;
      color: var(--accent);
    }}
    .action-tag {{
      display: inline-block;
      background: #27272a;
      padding: 3px 8px;
      border-radius: 6px;
      font-size: 11px;
      font-weight: 700;
      color: #fff;
    }}
    .conf-tag {{
      font-size: 11px;
      color: var(--muted);
      margin-left: 8px;
    }}
    .step-reason {{
      font-size: 13px;
      color: #e4e4e7;
      margin: 4px 0;
    }}
    .step-meta {{
      font-size: 11px;
      color: var(--muted);
    }}
    .step-meta code {{
      background: #1c1c21;
      padding: 2px 5px;
      border-radius: 4px;
      color: var(--blue);
    }}
    .step-thumb {{
      width: 100%;
      height: 90px;
      object-fit: cover;
      border-radius: 8px;
      border: 1px solid #2c2c34;
      transition: transform 0.2s;
    }}
    .step-thumb:hover {{ transform: scale(1.04); }}

    @media (max-width: 900px) {{
      .scorecards {{ grid-template-columns: 1fr 1fr; }}
      .issues-grid {{ grid-template-columns: 1fr; }}
      .step-row {{ grid-template-columns: 40px 1fr; }}
      .step-visual {{ display: none; }}
    }}
  </style>
</head>
<body>
  <div class="container">
    <header class="header">
      <div>
        <div class="brand-tag">TRACE//QA · Executive Audit Report</div>
        <h1>{html.escape(goal)}</h1>
        <div class="target-url">
          <span>Target: <code>{html.escape(target_url)}</code></span>
          {f'<span>Observed Final: <code>{html.escape(final_url)}</code></span>' if final_url and final_url != target_url else ''}
          <span>Nav State: <b style="color:{"#34d399" if navigation_state == "usable" else "#f87171"}">{html.escape((navigation_state or "usable").upper())}</b></span>
          <span>Status: <b>{html.escape(status.upper())}</b></span>
          <span>Run ID: <code>{html.escape(run_id)}</code></span>
        </div>
        {f'<div style="margin-top:8px;font-size:12px;color:#a1a1aa;">Goal Verification Evidence: <code>{html.escape(goal_verification_evidence)}</code></div>' if goal_verification_evidence else ''}
      </div>
      <div class="header-actions">
        <a href="report.json" download class="btn">Download report.json</a>
        <a href="/" class="btn btn-primary">Return to Console</a>
      </div>
    </header>

    <!-- Executive Scorecards -->
    <section class="scorecards">
      <div class="scorecard">
        <div class="card-label">UX Friction Index</div>
        <div class="card-val" style="color: {friction_color};">{friction_score}<span style="font-size:20px;font-weight:500;">/100</span></div>
        <div class="card-sub">{'Low friction' if friction_score < 30 else ('Moderate friction' if friction_score < 60 else 'High friction')}</div>
      </div>
      <div class="scorecard">
        <div class="card-label">WCAG Compliance</div>
        <div class="card-val" style="color: {a11y_color};">{metrics.get('wcag_grade', 'AA')}</div>
        <div class="card-sub">{metrics.get('accessibility_score', 85)}% Accessibility Score</div>
      </div>
      <div class="scorecard">
        <div class="card-label">Paths Discovered</div>
        <div class="card-val" style="color: var(--blue);">{metrics.get('paths', 1)}</div>
        <div class="card-sub">Goal: {'Completed' if metrics.get('goal_completed') else 'Partial'}</div>
      </div>
      <div class="scorecard">
        <div class="card-label">Issues Detected</div>
        <div class="card-val" style="color: #fff;">{len(issues)}</div>
        <div class="card-sub">{metrics.get('steps', len(trajectory))} autonomous steps taken</div>
      </div>
    </section>

    <!-- Executive Summary -->
    <section class="panel">
      <div class="panel-title">Executive Summary</div>
      <p class="summary-text">{html.escape(summary)}</p>
    </section>

    <!-- Issues Matrix with Code Recommendations -->
    <section class="panel">
      <div class="panel-title">
        <span>Audited Friction & Accessibility Findings ({len(issues)})</span>
      </div>
      <div class="issues-grid">
        {issues_html}
      </div>
    </section>

    <!-- Step-by-Step Trajectory & Visual Evidence -->
    <section class="panel">
      <div class="panel-title">Visual Trajectory & Action Traces</div>
      <div class="trajectory-list">
        {trajectory_html}
      </div>
    </section>
  </div>
</body>
</html>
"""
    (run_dir / "report.html").write_text(page, encoding="utf-8")
