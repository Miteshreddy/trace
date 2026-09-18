/* ==========================================================================
   TRACE//QA — Professional AI Quality Engineering Client Controller
   Real-Time Event Streaming, Observability Viewport, Journey Graph & Telemetry
   ========================================================================== */

let activeRun = null;
let poller = null;
let currentFilter = 'all';
let currentRunData = null;
let currentEvents = [];

const $ = (id) => document.getElementById(id);

// --------------------------------------------------------------------------
// Navigation & View Routing
// --------------------------------------------------------------------------

function switchView(viewName) {
  const views = ['command-deck', 'trajectory', 'journey', 'findings', 'reports', 'ai-engine', 'settings'];
  const titles = {
    'command-deck': 'Command Deck',
    'trajectory': 'Live Trajectory & Observability',
    'journey': 'Journey Explorer',
    'findings': 'Audit Findings Matrix',
    'reports': 'Executive Reports',
    'ai-engine': 'AI Provider Architecture',
    'settings': 'Settings & Targets',
  };

  views.forEach(v => {
    const el = $(`view-${v}`);
    const nav = $(`nav-${v}`);
    if (el) el.classList.toggle('active', v === viewName);
    if (nav) nav.classList.toggle('active', v === viewName);
  });

  const titleEl = $('topbarViewTitle');
  if (titleEl) titleEl.textContent = titles[viewName] || 'Command Deck';

  window.location.hash = viewName;
}

window.addEventListener('DOMContentLoaded', () => {
  const hash = window.location.hash.replace('#', '');
  if (hash && $(`view-${hash}`)) {
    switchView(hash);
  } else {
    switchView('command-deck');
  }
  fetchProviderHealth();
  setupGlobalShortcuts();
});

// --------------------------------------------------------------------------
// Interactive Tool Row & Parameter Cycling
// --------------------------------------------------------------------------

const MODES = ['AUTO', 'GROQ', 'GEMINI'];
function cycleProviderMode() {
  const cur = ($('toolPillModeText')?.textContent || 'AUTO').trim().toUpperCase();
  const nextIdx = (MODES.indexOf(cur) + 1) % MODES.length;
  const nextMode = MODES[nextIdx];
  switchProviderMode(nextMode);
}

const STEP_OPTIONS = [18, 24, 32, 12];
function cycleSteps() {
  const cur = Number($('maxSteps')?.value) || 18;
  const nextIdx = (STEP_OPTIONS.indexOf(cur) + 1) % STEP_OPTIONS.length;
  const nextVal = STEP_OPTIONS[nextIdx];
  $('maxSteps').value = nextVal;
  $('toolPillStepsText').textContent = `${nextVal} Steps`;
  showToast(`Max steps set to ${nextVal}`, 'info');
}

const PASS_OPTIONS = [1, 2, 3];
function cyclePasses() {
  const cur = Number($('passes')?.value) || 1;
  const nextIdx = (PASS_OPTIONS.indexOf(cur) + 1) % PASS_OPTIONS.length;
  const nextVal = PASS_OPTIONS[nextIdx];
  $('passes').value = nextVal;
  $('toolPillPassesText').textContent = `${nextVal} Pass${nextVal > 1 ? 'es' : ''}`;
  showToast(`Exploration passes set to ${nextVal}`, 'info');
}

function updateTargetToolPill(val) {
  const clean = val.trim();
  const pill = $('toolPillTarget');
  if (!pill) return;
  if (clean.includes('/demo/')) {
    pill.textContent = 'Target: Demo Store';
  } else {
    try {
      const u = new URL(clean);
      pill.textContent = `Target: ${u.hostname}`;
    } catch {
      pill.textContent = 'Target: Custom URL';
    }
  }
}

// --------------------------------------------------------------------------
// Preset Scenarios
// --------------------------------------------------------------------------

function applyPreset(num) {
  if (num === 1) {
    $('goal').value = "Find a blue running shoe under $100 and add it to the cart.";
    $('maxSteps').value = 16;
    $('toolPillStepsText').textContent = "16 Steps";
    $('passes').value = 1;
    $('toolPillPassesText').textContent = "1 Pass";
    showToast("Scenario 1 loaded: Blue shoe under $100 (Core Benchmark)", "accent");
  } else if (num === 2) {
    $('goal').value = "Discover alternative paths to find and cart a blue running shoe (compare category filters vs search bar).";
    $('maxSteps').value = 18;
    $('toolPillStepsText').textContent = "18 Steps";
    $('passes').value = 2;
    $('toolPillPassesText').textContent = "2 Passes";
    showToast("Scenario 2 loaded: Multi-Path Exploration (Passes: 2)", "accent");
  } else if (num === 3) {
    $('goal').value = "Browse running shoes, dismiss the promotional VIP modal overlay when it appears, and add the Aero Blue Runner to cart.";
    $('maxSteps').value = 16;
    $('toolPillStepsText').textContent = "16 Steps";
    $('passes').value = 1;
    $('toolPillPassesText').textContent = "1 Pass";
    showToast("Scenario 3 loaded: Modal Obstruction Recovery", "accent");
  }

  switchView('command-deck');
  const textarea = $('goal');
  textarea.focus();
  textarea.setSelectionRange(textarea.value.length, textarea.value.length);
}

function clearGoalInput() {
  $('goal').value = '';
  $('goal').focus();
}

// --------------------------------------------------------------------------
// Global Keyboard Shortcuts
// --------------------------------------------------------------------------

function setupGlobalShortcuts() {
  document.addEventListener('keydown', (e) => {
    // Focus goal on '/' or 'Cmd/Ctrl + K'
    if ((e.key === '/' && !['INPUT', 'TEXTAREA'].includes(document.activeElement.tagName)) || 
        ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k')) {
      e.preventDefault();
      switchView('command-deck');
      $('goal')?.focus();
    }

    // Submit on Cmd+Enter / Ctrl+Enter
    if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
      e.preventDefault();
      $('runBtn')?.click();
    }

    // Escape closes modals / blurs
    if (e.key === 'Escape') {
      closeEvidenceModal();
      if (document.activeElement === $('goal')) {
        $('goal').blur();
      }
    }
  });
}

// --------------------------------------------------------------------------
// AI Provider Mode Switching & Diagnostics
// --------------------------------------------------------------------------

async function switchProviderMode(mode) {
  try {
    const res = await fetch('/api/providers/select', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mode }),
    });
    const data = await res.json();
    if (data.ok) {
      updateModeUI(data.mode);
      showToast(`AI Engine switched to ${data.mode}`, 'info');
    }
  } catch (err) {
    console.error("Failed to switch mode:", err);
    showToast("Error switching provider mode", "error");
  }
}

function updateModeUI(mode) {
  const upper = (mode || 'AUTO').toUpperCase();
  $('btnModeAuto')?.classList.toggle('active', upper === 'AUTO');
  $('btnModeGroq')?.classList.toggle('active', upper === 'GROQ');
  $('btnModeGemini')?.classList.toggle('active', upper === 'GEMINI');

  if ($('toolPillModeText')) $('toolPillModeText').textContent = upper;
  if ($('activeModeBadge')) $('activeModeBadge').textContent = `AI ENGINE: ${upper}`;
  if ($('sidebarModeTag')) $('sidebarModeTag').textContent = upper;
}

async function testAIProviders() {
  const btn = $('btnTestProviders');
  const feedback = $('diagFeedback');
  btn.disabled = true;
  btn.innerHTML = '<span>⚡ Probing...</span>';
  feedback.textContent = 'Measuring roundtrip latency...';

  try {
    const res = await fetch('/api/providers/test', { method: 'POST' });
    const data = await res.json();
    const groq = data.results?.groq;
    const gemini = data.results?.gemini;

    const groqMsg = groq?.connected ? `Groq: Connected (${groq.latency_ms}ms)` : `Groq: Failed`;
    const geminiMsg = gemini?.connected ? `Gemini: Connected (${gemini.latency_ms}ms)` : `Gemini: Failed`;

    feedback.innerHTML = `<b>${escapeHtml(groqMsg)}</b> &nbsp;|&nbsp; <b>${escapeHtml(geminiMsg)}</b>`;
    fetchProviderHealth();
    showToast("Provider diagnostic ping complete", "success");
  } catch (err) {
    feedback.textContent = `Diagnostic error: ${err.message}`;
    showToast("Diagnostic probe failed", "error");
  } finally {
    btn.disabled = false;
    btn.innerHTML = '<span>⚡ Test AI Providers</span>';
  }
}

async function fetchProviderHealth() {
  try {
    const res = await fetch('/api/providers');
    const data = await res.json();
    updateModeUI(data.mode);

    const groq = data.providers?.groq;
    if (groq) {
      const statusText = groq.status.charAt(0).toUpperCase() + groq.status.slice(1);
      if ($('groqStatus')) $('groqStatus').textContent = statusText;
      if ($('groqLatency')) $('groqLatency').textContent = groq.latency_ms > 0 ? `${groq.latency_ms}ms` : '—';
    }

    const gemini = data.providers?.gemini;
    if (gemini) {
      const statusText = gemini.status.charAt(0).toUpperCase() + gemini.status.slice(1);
      if ($('geminiStatus')) $('geminiStatus').textContent = statusText;
      if ($('geminiLatency')) $('geminiLatency').textContent = gemini.latency_ms > 0 ? `${gemini.latency_ms}ms` : '—';
    }
  } catch (err) {
    console.error("Failed to fetch provider health:", err);
  }
}

// --------------------------------------------------------------------------
// Trajectory Events & Viewport Tracing
// --------------------------------------------------------------------------

function renderEvents(events) {
  currentEvents = events;
  const root = $('trajectory');
  if (!events.length) {
    root.innerHTML = '<div class="viewport-waiting-box"><span style="font-size:28px;color:var(--text-subtle);">◌</span><p>Waiting for agent perception...</p></div>';
    return;
  }

  root.innerHTML = events.slice(-80).map((e, idx) => {
    const action = (e.data?.action || e.kind || '').toLowerCase();
    const rationale = e.data?.rationale || '';
    const stepNum = e.data?.step || (idx + 1);
    const element = e.data?.element_id ? `Target: ${escapeHtml(e.data.element_id)}` : '';
    const provider = e.data?.provider ? String(e.data.provider).toLowerCase() : '';

    return `
      <div class="event-step-row" onclick="previewStepScreenshot(${stepNum})">
        <div class="step-bubble-num font-mono">${String(stepNum).padStart(2, '0')}</div>
        <div class="event-details-col">
          <div class="event-tags-bar">
            <span class="action-pill ${escapeHtml(action)}">${escapeHtml(action)}</span>
            ${provider ? `<span class="provider-chip-tag ${escapeHtml(provider)}">${escapeHtml(provider.toUpperCase())}</span>` : ''}
            ${element ? `<span class="font-mono" style="font-size:10px;color:var(--text-muted);margin-left:auto;">${element}</span>` : ''}
          </div>
          <div class="event-msg-line">${escapeHtml(e.message)}</div>
          ${rationale ? `<div class="event-rationale-box">“${escapeHtml(rationale)}”</div>` : ''}
        </div>
      </div>
    `;
  }).join('');

  root.scrollTop = root.scrollHeight;
}

function previewStepScreenshot(stepNum) {
  if (!activeRun) return;
  const pad = String(stepNum).padStart(3, '0');
  const imgUrl = `/artifacts/${activeRun}/step_${pad}.png?t=${Date.now()}`;
  const img = $('screenImage');
  img.src = imgUrl;
  img.style.display = 'block';
  $('screenEmpty').style.display = 'none';
  $('screenCaption').textContent = `Previewing Step ${stepNum} (Action Overlay Active)`;

  const ev = currentEvents.find(e => e.data?.step === stepNum);
  if (ev) {
    $('viewportOverlay').style.display = 'flex';
    $('overlayStep').textContent = `STEP ${String(stepNum).padStart(2, '0')}`;
    $('overlayAction').textContent = (ev.data?.action || ev.kind || 'OBSERVE').toUpperCase();
    $('overlayConfidence').textContent = ev.data?.confidence ? `${(Number(ev.data.confidence) * 100).toFixed(0)}% Conf` : '95% Conf';
  }
}

// --------------------------------------------------------------------------
// Interactive Journey Graph Renderer
// --------------------------------------------------------------------------

function renderJourney(journey) {
  const container = $('journeyGraphContainer');
  const nodes = journey?.nodes || [];
  const edges = journey?.edges || [];

  $('uniqueStatesCount').textContent = nodes.length;
  $('loopsCount').textContent = nodes.filter(n => n.is_loop).length;

  if (!nodes.length) {
    container.innerHTML = `
      <div class="viewport-waiting-box">
        <span style="font-size:32px;color:var(--text-subtle);">☊</span>
        <p>No journey data mapped yet. Start an autonomous run to explore paths.</p>
      </div>
    `;
    return;
  }

  let html = '<div class="journey-nodes-row">';
  nodes.forEach((node, idx) => {
    const isGoal = node.is_goal;
    const isLoop = node.is_loop;
    const stateClass = isGoal ? 'is-goal' : '';

    html += `
      <div class="journey-node-card ${stateClass}" onclick="previewStepScreenshot(${node.step})">
        <div class="node-step-badge font-mono">STEP ${String(node.step).padStart(2, '0')} ${isGoal ? '★ GOAL' : (isLoop ? '⟲ LOOP' : '')}</div>
        <div class="node-title-text">${escapeHtml(node.label || 'State View')}</div>
        <div class="node-url-mono font-mono">${escapeHtml(node.url || '')}</div>
      </div>
    `;

    if (idx < nodes.length - 1) {
      const edge = edges.find(e => e.source === node.id) || edges[idx];
      const edgeLabel = edge?.action || 'nav';
      html += `
        <div class="journey-edge-arrow">
          <span>──</span>
          <span class="action-pill click" style="font-size:8px;padding:1px 4px;">${escapeHtml(edgeLabel)}</span>
          <span>──➔</span>
        </div>
      `;
    }
  });
  html += '</div>';

  container.innerHTML = html;
}

// --------------------------------------------------------------------------
// Findings Matrix & Filtering
// --------------------------------------------------------------------------

function setFindingFilter(category, btn) {
  currentFilter = category;
  document.querySelectorAll('.filter-category-pill').forEach(b => b.classList.remove('active'));
  if (btn) btn.classList.add('active');
  filterFindings();
}

function filterFindings() {
  const issues = currentRunData?.issues || [];
  const query = ($('findingsSearch')?.value || '').toLowerCase().trim();

  $('countAll').textContent = issues.length;
  $('countA11y').textContent = issues.filter(i => i.category === 'accessibility').length;
  $('countUx').textContent = issues.filter(i => i.category === 'ux').length;
  $('countNav').textContent = issues.filter(i => i.category === 'navigation').length;
  $('countVisual').textContent = issues.filter(i => i.category === 'visual').length;

  const filtered = issues.filter(i => {
    const matchCategory = currentFilter === 'all' || i.category === currentFilter;
    const matchQuery = !query || 
      i.title.toLowerCase().includes(query) || 
      i.description.toLowerCase().includes(query) || 
      (i.evidence && i.evidence.toLowerCase().includes(query));
    return matchCategory && matchQuery;
  });

  const root = $('findingsList');
  if (!filtered.length) {
    root.innerHTML = `
      <div class="viewport-waiting-box" style="background:var(--bg-surface);border:1px solid var(--border-subtle);border-radius:var(--radius-md);">
        <span style="font-size:28px;color:var(--text-subtle);">✓</span>
        <p>No findings matching current filters. Target interface demonstrated zero friction.</p>
      </div>
    `;
    return;
  }

  root.innerHTML = filtered.map(i => {
    const sev = (i.severity || 'medium').toLowerCase();
    const cat = (i.category || 'general').toUpperCase();

    return `
      <div class="finding-row-card">
        <div class="finding-card-header">
          <div style="display:flex;align-items:center;gap:8px;">
            <span class="sev-badge ${escapeHtml(sev)}">${escapeHtml(sev.toUpperCase())}</span>
            <span style="font-size:10px;font-weight:600;color:var(--text-muted);text-transform:uppercase;">${escapeHtml(cat)}</span>
            ${i.step ? `<span class="font-mono" style="font-size:10px;color:var(--text-subtle);">STEP ${i.step}</span>` : ''}
          </div>
          <button class="btn-open-target-link" onclick="inspectFindingEvidence('${escapeHtml(i.id)}')">Inspect Evidence ⛶</button>
        </div>

        <div class="finding-title-text">${escapeHtml(i.title)}</div>
        <div class="finding-desc-copy">${escapeHtml(i.description)}</div>

        ${i.evidence ? `
          <div class="finding-evidence-strip font-mono">
            <span style="font-weight:700;color:var(--text-subtle);font-size:9px;">EVIDENCE:</span>
            <span>${escapeHtml(i.evidence)}</span>
          </div>
        ` : ''}

        ${i.recommendation ? `
          <div class="finding-remediation-lead">
            <b style="color:var(--success);">Code Remediation:</b> ${escapeHtml(i.recommendation)}
          </div>
        ` : ''}
      </div>
    `;
  }).join('');
}

function inspectFindingEvidence(findingId) {
  const finding = (currentRunData?.issues || []).find(f => f.id === findingId);
  if (!finding) return;

  const screenshotUrl = finding.screenshot || currentRunData?.latest_screenshot;
  openEvidenceModal(
    screenshotUrl,
    finding.title,
    finding.description,
    finding.recommendation
  );
}

// --------------------------------------------------------------------------
// Evidence Lightbox Modal
// --------------------------------------------------------------------------

function openEvidenceModal(imgSrc, title, desc, rec) {
  const modal = $('evidenceModal');
  const img = $('modalImg');
  const titleEl = $('modalTitle');
  const descEl = $('modalDesc');
  const recEl = $('modalRec');

  img.src = imgSrc || $('screenImage').src || '';
  titleEl.textContent = title || 'Visual Viewport Trace';
  descEl.textContent = desc || 'Observation captured during autonomous execution.';

  if (rec) {
    recEl.style.display = 'block';
    recEl.innerHTML = `<b style="color:var(--success);">Developer Remediation:</b> ${escapeHtml(rec)}`;
  } else {
    recEl.style.display = 'none';
  }

  modal.classList.add('open');
}

function closeEvidenceModal() {
  $('evidenceModal')?.classList.remove('open');
}

// --------------------------------------------------------------------------
// Autonomous Run Execution & State Polling
// --------------------------------------------------------------------------

$('runBtn').addEventListener('click', async () => {
  const goalText = $('goal').value.trim();
  if (!goalText) {
    showToast("Please describe a user goal first", "warning");
    $('goal').focus();
    return;
  }

  $('runError').style.display = 'none';
  $('runBtn').disabled = true;
  $('runBtn').innerHTML = '<span class="btn-text">Agent Navigating…</span><span>◌</span>';
  $('cancelBtn').classList.add('visible');

  // Reset viewport & feeds
  $('trajectory').innerHTML = '';
  $('screenImage').style.display = 'none';
  $('screenEmpty').style.display = 'flex';
  $('screenCaption').textContent = 'Launching Playwright session...';
  $('viewportOverlay').style.display = 'none';

  try {
    const body = {
      goal: goalText,
      target_url: $('target').value.trim() || "http://127.0.0.1:8000/demo/",
      max_steps: Number($('maxSteps').value) || 18,
      exploration_passes: Number($('passes').value) || 1,
    };

    const res = await fetch('/api/runs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });

    const run = await res.json();
    if (!res.ok) throw new Error(run.detail || 'Failed to initialize run');

    activeRun = run.id;
    currentRunData = run;

    $('runId').textContent = run.id;
    $('screenUrl').textContent = run.target_url;
    setGlobalRunState('running', run.id);

    // Smooth transition into Live Trajectory view
    switchView('trajectory');
    showToast(`Autonomous Run #${run.id} started`, "success");

    await refresh();
    poller = setInterval(refresh, 650);
  } catch (err) {
    $('runError').textContent = err.message;
    $('runError').style.display = 'flex';
    $('runBtn').disabled = false;
    $('runBtn').innerHTML = '<span class="btn-text">Run Audit</span><span>➔</span>';
    $('cancelBtn').classList.remove('visible');
    showToast(`Run error: ${err.message}`, "error");
  }
});

async function cancelActiveRun() {
  if (!activeRun) return;
  try {
    await fetch(`/api/runs/${activeRun}/cancel`, { method: 'POST' });
    showToast("Cancellation sent to agent", "warning");
    $('cancelBtn').classList.remove('visible');
  } catch (err) {
    console.error("Cancel error:", err);
  }
}

async function refresh() {
  if (!activeRun) return;
  try {
    const run = await fetch(`/api/runs/${activeRun}`).then(r => r.json());
    currentRunData = run;

    $('runId').textContent = run.id;
    $('stepMeta').textContent = run.step_count;
    $('navStepBadge').textContent = run.step_count;
    $('screenUrl').textContent = run.target_url;

    if (run.latest_screenshot) {
      const img = $('screenImage');
      img.src = run.latest_screenshot + '?t=' + Date.now();
      img.style.display = 'block';
      $('screenEmpty').style.display = 'none';
      $('screenCaption').textContent = `Latest visual observation · Step ${run.step_count}`;
    }

    if (run.metrics) {
      const friction = run.metrics.friction_score ?? '—';
      $('frictionMetric').textContent = friction !== '—' ? `${friction}/100` : '—';
      $('wcagMetric').textContent = run.metrics.wcag_grade || '—';
      $('pathsMetric').textContent = run.metrics.paths ?? (run.paths_discovered || 1);
      $('pathsMetricJourney').textContent = run.metrics.paths ?? (run.paths_discovered || 1);
    }

    const isComplete = run.status === 'completed';
    const isFailed = run.status === 'failed';

    $('goalMetric').textContent = isComplete ? (run.goal_completed ? 'COMPLETE ★' : 'PARTIAL') : (isFailed ? 'FAILED' : 'IN PROGRESS');
    $('goalMetricSub').textContent = isComplete ? 'All verification criteria met' : 'Agent executing steps';
    $('goalMetaStatus').textContent = isComplete ? (run.goal_completed ? 'COMPLETED' : 'PARTIAL') : run.status.toUpperCase();

    if (run.report_url) {
      $('reportLink').href = run.report_url;
      $('reportLink').style.display = 'inline-flex';
      $('downloadJsonBtn').href = `/api/runs/${activeRun}/report.json`;
      $('downloadJsonBtn').style.display = 'inline-flex';
    }

    $('reportGoalTitle').textContent = run.goal;
    $('reportMetaSubtitle').textContent = `Run ID: ${run.id} · Target: ${run.target_url}`;

    const issues = run.issues || [];
    $('navFindingsBadge').textContent = issues.length;
    filterFindings();
    renderJourney(run.journey);

    const eventsData = await fetch(`/api/runs/${activeRun}/events`).then(r => r.json());
    const evs = eventsData.events || [];
    renderEvents(evs);

    const lastAgent = [...evs].reverse().find(e => e.kind === 'agent');
    if (lastAgent && lastAgent.data?.confidence) {
      const confPct = `${(Number(lastAgent.data.confidence) * 100).toFixed(0)}%`;
      $('confMeta').textContent = confPct;
      $('overlayConfidence').textContent = `${confPct} Conf`;
    }

    if (lastAgent && lastAgent.data?.action) {
      $('viewportOverlay').style.display = 'flex';
      $('overlayStep').textContent = `STEP ${String(run.step_count).padStart(2, '0')}`;
      $('overlayAction').textContent = lastAgent.data.action.toUpperCase();
    }

    if (isComplete || isFailed) {
      setGlobalRunState(run.status, run.id);
      $('runBtn').disabled = false;
      $('runBtn').innerHTML = '<span class="btn-text">Run Audit</span><span>➔</span>';
      $('cancelBtn').classList.remove('visible');

      if (poller) {
        clearInterval(poller);
        poller = null;
      }

      showToast(isComplete ? `Audit #${run.id} Completed!` : `Audit halted: ${run.error || 'Failed'}`, isComplete ? 'success' : 'error');
      fetchProviderHealth();
    }
  } catch (err) {
    console.error("Refresh error:", err);
  }
}

function setGlobalRunState(status, runId) {
  const badge = $('topbarRunBadge');
  const text = $('topbarRunText');
  const trajStatus = $('runStatus');

  badge.className = `status-capsule ${status}`;
  if (status === 'running') {
    text.textContent = `RUNNING #${runId || ''}`;
    trajStatus.className = 'status-capsule running';
    trajStatus.textContent = 'RUNNING';
  } else if (status === 'completed') {
    text.textContent = `COMPLETED #${runId || ''}`;
    trajStatus.className = 'status-capsule completed';
    trajStatus.textContent = 'COMPLETED';
  } else if (status === 'failed') {
    text.textContent = `FAILED #${runId || ''}`;
    trajStatus.className = 'status-capsule failed';
    trajStatus.textContent = 'FAILED';
  } else {
    text.textContent = 'IDLE';
    trajStatus.className = 'status-capsule';
    trajStatus.textContent = 'IDLE';
  }
}

// --------------------------------------------------------------------------
// Toast Notification Engine
// --------------------------------------------------------------------------

function showToast(message, type = 'info', duration = 3000) {
  const stack = $('toastStack');
  if (!stack) return;

  const toast = document.createElement('div');
  toast.className = `toast-item ${type}`;

  const iconMap = {
    info: 'ℹ',
    success: '✓',
    warning: '⚠',
    error: '✕',
    accent: '✦',
  };

  toast.innerHTML = `
    <span style="font-weight:700;">${iconMap[type] || '✦'}</span>
    <span>${escapeHtml(message)}</span>
  `;

  stack.appendChild(toast);

  setTimeout(() => {
    toast.style.opacity = '0';
    toast.style.transform = 'translateY(8px)';
    toast.style.transition = 'all 0.25s ease';
    setTimeout(() => toast.remove(), 260);
  }, duration);
}

// --------------------------------------------------------------------------
// Utility
// --------------------------------------------------------------------------

function escapeHtml(val) {
  return String(val ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/'/g, '&#39;')
    .replace(/"/g, '&quot;');
}
