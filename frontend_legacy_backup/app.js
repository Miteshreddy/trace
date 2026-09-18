/* ==========================================================================
   TRACE — Autonomous Software Quality Platform
   Client Application Controller
   ========================================================================== */

let activeRunId = null;
let runPoller = null;
let currentFilter = 'all';
let currentRunState = null;
let currentRunEvents = [];

const $ = (id) => document.getElementById(id);

// --------------------------------------------------------------------------
// Navigation & View Routing
// --------------------------------------------------------------------------


// --------------------------------------------------------------------------
// Navigation & View Routing (updated to include #home)
// --------------------------------------------------------------------------

function switchView(viewName) {
  const workspaceViews = ['command', 'live', 'journeys', 'findings', 'reports'];
  const allViews = ['home', ...workspaceViews];

  // Toggle home vs workspace chrome
  const homeSection   = $('view-home');
  const topbar        = $('workspaceTopbar');
  const workspaceMain = $('workspaceMain');

  if (viewName === 'home') {
    if (homeSection)   homeSection.classList.add('active');
    if (topbar)        topbar.style.display = 'none';
    if (workspaceMain) workspaceMain.style.display = 'none';
    document.documentElement.classList.add('home-page-active');
    document.body.classList.add('home-page');
    window.location.hash = 'home';
    syncHeaderStatusBadge();
    return;
  }

  // Show workspace chrome, hide home view
  if (homeSection)   homeSection.classList.remove('active');
  if (topbar)        topbar.style.display = 'flex';
  if (workspaceMain) { workspaceMain.style.display = 'block'; }
  document.documentElement.classList.remove('home-page-active');
  document.body.classList.remove('home-page');

  // Toggle inner workspace views
  workspaceViews.forEach(v => {
    const viewEl = $(`view-${v}`);
    const navBtn = $(`nav-${v}`);
    if (viewEl) viewEl.classList.toggle('active', v === viewName);
    if (navBtn) navBtn.classList.toggle('active', v === viewName);
  });

  // Also handle evaluation as alias for findings
  if (viewName === 'evaluation') {
    const fEl = $('view-findings');
    const fNav = $('nav-findings');
    if (fEl) fEl.classList.add('active');
    if (fNav) fNav.classList.add('active');
    const eNav = $('nav-evaluation');
    if (eNav) eNav.classList.add('active');
  }

  // Hide topbar target pill on command (has its own inline target)
  const topbarTarget = $('topbarTargetPill');
  if (topbarTarget) {
    topbarTarget.style.display = viewName === 'command' ? 'none' : 'inline-flex';
  }

  window.location.hash = viewName;
}

window.addEventListener('DOMContentLoaded', () => {
  const hash = window.location.hash.replace('#', '');
  const workspaceViews = ['command', 'live', 'journeys', 'findings', 'reports', 'evaluation'];

  if (hash && workspaceViews.includes(hash)) {
    switchView(hash);
  } else {
    // Default: show the TRACE v4 homepage
    switchView('home');
  }

  setupShortcuts();
  setupCommandComposerVFX();
  setupTargetValidationListeners();
  validateCurrentTarget();
  setupAdvancedDrawerDismiss();
  loadLatestRunIfAvailable();

  // Init home-page interactivity
  initHomeView();
});

// --------------------------------------------------------------------------
// Home View — TRACE v4 Animation & Interactivity
// --------------------------------------------------------------------------

function initHomeView() {
  setupTracePointer();
  setupHmReveal();
  setupHmScrollMotion();

  // Start demo when console enters viewport
  const consoleEl = $('hmConsole');
  if (consoleEl) {
    let started = false;
    const startDemo = () => {
      if (started) return;
      started = true;
      runHomeDemo().catch(() => {
        const badge = $('headerStatusBadge');
        if (badge) { badge.dataset.state = 'failed'; $('headerStatusText').textContent = 'FAILED'; }
      });
    };
    const io = new IntersectionObserver(entries => {
      if (entries.some(e => e.isIntersecting)) { startDemo(); io.disconnect(); }
    }, { threshold: 0.08 });
    io.observe(consoleEl);
  }

  // Replay button
  $('hmReplayBtn')?.addEventListener('click', () => {
    runHomeDemo().catch(() => {});
  });
}

// --------------------------------------------------------------------------
// runHomeDemo — TRACE PROMPT → LIVE CONSOLE ANIMATION
// Ported verbatim from integrate/TRACE_FRONTEND/app.js, adapted for merged IDs
// --------------------------------------------------------------------------

async function runHomeDemo() {
  function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

  const typed     = $('hmDemoTyped');
  const consoleEl = $('hmConsole');
  const command   = $('hmDemoCommand');
  const state     = $('hmDemoState');
  const status    = $('hmSessionStatus');

  if (!typed || !consoleEl || !command) return;

  const badge = $('headerStatusBadge');
  const statusText = $('headerStatusText');

  const activities = Array.from(document.querySelectorAll('[data-hm-activity]'));
  const steps = [$('hmStep1'),$('hmStep2'),$('hmStep3'),$('hmStep4'),$('hmStep5'),$('hmStep6')].filter(Boolean);
  const cursor = $('hmCursor');
  const label  = $('hmCursorLabel');

  const goal   = 'Check the signup flow and identify accessibility issues';
  const path   = [[28,34],[59,28],[72,58],[43,63],[68,52]];
  const labels = ['Opening website','Inspecting navigation','Testing primary flow','Checking accessibility','Capturing evidence'];

  // Reset state
  typed.textContent = '';
  consoleEl.classList.remove('visible');
  command.classList.remove('compact');
  state.classList.remove('ready');
  state.innerHTML = '<i></i> Ready';
  activities.forEach(a => a.classList.remove('done','current'));
  steps.forEach((s, i) => { s.classList.toggle('reached', i === 0); s.classList.remove('current'); });
  if (cursor) { cursor.style.left = '28%'; cursor.style.top = '34%'; }
  if (label)  label.textContent = 'Exploring interface';
  if (status) status.textContent = 'Preparing session';
  if (badge)  { badge.dataset.state = 'running'; if (statusText) statusText.textContent = 'RUNNING'; }

  await sleep(500);

  // Type the goal character by character
  for (const ch of goal) { typed.textContent += ch; await sleep(23); }

  await sleep(320);
  state.classList.add('ready');
  state.innerHTML = '<i></i> Goal understood';
  command.classList.add('compact');

  await sleep(180);
  consoleEl.classList.add('visible');

  await sleep(420);
  if (status) status.textContent = 'Agent is browsing';

  // Step through activities and cursor positions
  for (let i = 0; i < activities.length; i++) {
    if (activities[i - 1]) activities[i - 1].classList.add('done');
    activities[i].classList.add('current');

    const stepIdx = Math.min(i + 1, steps.length - 1);
    steps[stepIdx].classList.add('current', 'reached');

    if (cursor) { cursor.style.left = path[i][0] + '%'; cursor.style.top = path[i][1] + '%'; }
    if (label)  label.textContent = labels[i];

    await sleep(430);
    activities[i].classList.remove('current');
    activities[i].classList.add('done');
    steps[stepIdx].classList.remove('current');
  }

  if (status) status.textContent = 'Evidence captured';
  if (state)  { state.classList.add('ready'); state.innerHTML = '<i></i> Session active'; }
  if (badge)  { badge.dataset.state = 'success'; if (statusText) statusText.textContent = 'EVIDENCE READY'; }
}

// --------------------------------------------------------------------------
// Custom TRACE Cursor (Home Page Only)
// --------------------------------------------------------------------------

function setupTracePointer() {
  const pointer = $('tracePointer');
  if (!pointer || matchMedia('(pointer:coarse)').matches) return;

  let x = 0, y = 0, tx = 0, ty = 0, visible = false;

  window.addEventListener('mousemove', e => {
    tx = e.clientX; ty = e.clientY;
    if (!visible) { visible = true; pointer.style.opacity = '1'; }
  });
  window.addEventListener('mousedown', () => pointer.classList.add('is-down'));
  window.addEventListener('mouseup',   () => pointer.classList.remove('is-down'));

  document.addEventListener('pointerover', e => {
    const t = e.target.closest('a, button, .hm-cap, .hm-journey-node, .hm-evidence-card, .glass-tag');
    pointer.classList.toggle('is-link', !!t);
  });

  const tick = () => {
    x += (tx - x) * 0.22;
    y += (ty - y) * 0.22;
    pointer.style.left = x + 'px';
    pointer.style.top  = y + 'px';
    requestAnimationFrame(tick);
  };
  tick();
}

// --------------------------------------------------------------------------
// Scroll Reveal (Home Page)
// --------------------------------------------------------------------------

function setupHmReveal() {
  const items = Array.from(document.querySelectorAll('.hm-reveal-scroll'));
  if (!items.length) return;
  const io = new IntersectionObserver(entries => {
    entries.forEach(entry => {
      if (entry.isIntersecting) {
        entry.target.classList.add('in-view');
        io.unobserve(entry.target);
      }
    });
  }, { threshold: 0.18 });
  items.forEach(item => io.observe(item));
}

// --------------------------------------------------------------------------
// Scroll Parallax Motion (Evidence Cards + Journey Section)
// --------------------------------------------------------------------------

function setupHmScrollMotion() {
  const evidenceShell = document.querySelector('.hm-evidence-stage');
  const journeyVis    = $('hmJourneyVisual');
  if (!evidenceShell && !journeyVis) return;

  let raf = 0;
  const update = () => {
    raf = 0;
    if (evidenceShell) {
      const r = evidenceShell.getBoundingClientRect();
      const shift = (window.innerHeight * 0.5 - (r.top + r.height * 0.5)) * 0.08;
      evidenceShell.style.setProperty('--hm-scroll-shift', shift.toFixed(1) + 'px');
    }
    if (journeyVis) {
      const r = journeyVis.getBoundingClientRect();
      const n = Math.max(-1, Math.min(1, (window.innerHeight * 0.5 - (r.top + r.height * 0.5)) / window.innerHeight));
      journeyVis.style.transform = 'translateY(' + (n * 4).toFixed(1) + 'px)';
    }
  };

  window.addEventListener('scroll', () => { if (!raf) raf = requestAnimationFrame(update); }, { passive: true });
  update();
}

// --------------------------------------------------------------------------
// Header Status Badge Sync (Home Page Run State)
// --------------------------------------------------------------------------

function syncHeaderStatusBadge() {
  const badge = $('headerStatusBadge');
  const text  = $('headerStatusText');
  if (!badge || !text) return;

  if (!currentRunState) {
    badge.dataset.state = 'idle';
    text.textContent = 'READY';
    return;
  }

  const status = currentRunState.status;
  if (status === 'running') {
    badge.dataset.state = 'running';
    text.textContent = 'RUNNING';
  } else if (status === 'completed') {
    badge.dataset.state = 'success';
    text.textContent = 'EVIDENCE READY';
  } else if (status === 'failed') {
    badge.dataset.state = 'failed';
    text.textContent = 'HALTED';
  } else {
    badge.dataset.state = 'idle';
    text.textContent = 'READY';
  }
}

function escapeHtml(val) {
  return String(val ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/'/g, '&#39;')
    .replace(/"/g, '&quot;');
}




// --------------------------------------------------------------------------
// Command Surface VFX (Effect 4 Cursor Proximity)
// --------------------------------------------------------------------------

function setupCommandComposerVFX() {
  const surface = $('commandSurface');
  if (!surface) return;

  surface.addEventListener('mousemove', (e) => {
    const rect = surface.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const y = e.clientY - rect.top;
    surface.style.setProperty('--mouse-x', `${x}px`);
    surface.style.setProperty('--mouse-y', `${y}px`);
  });
}

// --------------------------------------------------------------------------
// Target URL Validation (Part 2A, Part 8, Part 11)
// --------------------------------------------------------------------------

let targetValidationTimer = null;
let currentTargetStatus = 'unknown';

function setupTargetValidationListeners() {
  const input = $('targetUrlInput');
  if (!input) return;

  input.addEventListener('input', () => {
    const dot = $('targetStatusDot');
    const label = $('targetStatusLabel');
    const banner = $('targetErrorBanner');
    if (dot) dot.className = 'target-status-dot checking';
    if (label) label.textContent = 'Checking...';
    if (banner) banner.style.display = 'none';

    clearTimeout(targetValidationTimer);
    targetValidationTimer = setTimeout(() => {
      validateCurrentTarget();
    }, 380);
  });

  input.addEventListener('blur', () => {
    clearTimeout(targetValidationTimer);
    validateCurrentTarget();
  });

  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      clearTimeout(targetValidationTimer);
      validateCurrentTarget();
    }
  });
}

async function validateCurrentTarget(userInitiated = false) {
  const input = $('targetUrlInput');
  const dot = $('targetStatusDot');
  const label = $('targetStatusLabel');
  const banner = $('targetErrorBanner');
  const openLink = $('targetOpenLink');

  if (!input) return false;

  let urlStr = input.value.trim();
  if (!urlStr) {
    urlStr = "http://127.0.0.1:8000/demo/";
    input.value = urlStr;
  }

  // Prepend http:// if user omitted scheme
  if (!/^https?:\/\//i.test(urlStr)) {
    urlStr = 'http://' + urlStr;
    input.value = urlStr;
  }

  if (openLink) {
    openLink.href = urlStr;
  }

  // Syntax validation
  let parsedUrl;
  try {
    parsedUrl = new URL(urlStr);
  } catch {
    markUnavailable();
    return false;
  }

  // Visual checking state
  currentTargetStatus = 'checking';
  if (dot) {
    dot.className = 'target-status-dot checking';
    dot.title = 'Checking target...';
  }
  if (label) label.textContent = 'Checking...';
  if (banner) banner.style.display = 'none';

  function markReachable() {
    currentTargetStatus = 'reachable';
    if (dot) {
      dot.className = 'target-status-dot reachable';
      dot.title = 'Target reachable';
    }
    if (label) label.textContent = 'Reachable';
    if (banner) banner.style.display = 'none';
  }

  function markUnavailable() {
    currentTargetStatus = 'unavailable';
    if (dot) {
      dot.className = 'target-status-dot unavailable';
      dot.title = 'Target unavailable';
    }
    if (label) label.textContent = 'Unavailable';
    if (banner) banner.style.display = 'flex';
  }

  try {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 3500);

    const isSameOrigin = (parsedUrl.origin === window.location.origin) ||
                         (parsedUrl.hostname === '127.0.0.1' && window.location.hostname === '127.0.0.1') ||
                         (parsedUrl.hostname === 'localhost' && window.location.hostname === 'localhost');

    if (isSameOrigin) {
      const res = await fetch(urlStr, {
        method: 'HEAD',
        cache: 'no-store',
        signal: controller.signal
      });
      clearTimeout(timeoutId);
      if (res.status < 500) {
        markReachable();
        return true;
      } else {
        markUnavailable();
        return false;
      }
    } else {
      await fetch(urlStr, {
        method: 'GET',
        mode: 'no-cors',
        cache: 'no-store',
        signal: controller.signal
      });
      clearTimeout(timeoutId);
      markReachable();
      return true;
    }
  } catch (err) {
    markUnavailable();
    return false;
  }
}

// --------------------------------------------------------------------------
// Preset Tasks (Part 2B)
// --------------------------------------------------------------------------

function applyPreset(num) {
  const input = $('goalInput');
  if (!input) return;

  if (num === 1) {
    input.value = "Find a blue running shoe under $100 and reach checkout.";
    const maxSteps = $('maxStepsSelect');
    if (maxSteps) maxSteps.value = "16";
  } else if (num === 2) {
    input.value = "Discover alternate paths to find and cart a blue running shoe (compare category filters vs search bar).";
    const maxSteps = $('maxStepsSelect');
    if (maxSteps) maxSteps.value = "18";
  } else if (num === 3) {
    input.value = "Browse running shoes, dismiss the promotional VIP modal overlay when it appears, and add the Aero Blue Runner to cart.";
    const maxSteps = $('maxStepsSelect');
    if (maxSteps) maxSteps.value = "16";
  }

  switchView('command');
  input.focus();
  input.setSelectionRange(input.value.length, input.value.length);
  showToast(`Loaded Preset Task 0${num}`, 'info');
}

// --------------------------------------------------------------------------
// Advanced Configuration Drawer (Part 2C & Part 9)
// --------------------------------------------------------------------------

function toggleAdvancedOptions() {
  const drawer = $('advancedDrawer');
  const btn = $('advancedBtn');
  if (drawer) drawer.classList.toggle('open');
  if (btn) btn.classList.toggle('open');
}

function resetAdvancedOptions() {
  const maxSteps = $('maxStepsSelect');
  const passes = $('passesSelect');
  if (maxSteps) maxSteps.value = "18";
  if (passes) passes.value = "1";
  showToast("Advanced configuration reset to defaults", "info");
}

function setupAdvancedDrawerDismiss() {
  document.addEventListener('click', (e) => {
    const drawer = $('advancedDrawer');
    const btn = $('advancedBtn');
    if (drawer && drawer.classList.contains('open')) {
      if (!drawer.contains(e.target) && !btn?.contains(e.target)) {
        drawer.classList.remove('open');
        if (btn) btn.classList.remove('open');
      }
    }
  });

  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      const drawer = $('advancedDrawer');
      const btn = $('advancedBtn');
      if (drawer && drawer.classList.contains('open')) {
        drawer.classList.remove('open');
        if (btn) btn.classList.remove('open');
      }
    }
  });
}

// --------------------------------------------------------------------------
// Run Execution & Premium Launch Transition (Part 2D & Part 4)
// --------------------------------------------------------------------------

async function startAuditRun() {
  const goalInput = $('goalInput');
  const goal = goalInput ? goalInput.value.trim() : '';
  if (!goal) {
    showToast("Please describe what a real user should accomplish", "warning");
    if (goalInput) goalInput.focus();
    return;
  }

  const targetInput = $('targetUrlInput');
  const targetUrl = (targetInput ? targetInput.value.trim() : '') || "http://127.0.0.1:8000/demo/";
  const maxSteps = Number($('maxStepsSelect')?.value) || 18;
  const passes = Number($('passesSelect')?.value) || 1;

  const runBtn = $('runAuditBtn');
  const commandSurface = $('commandSurface');
  const commandCenter = $('commandCenter');
  const heroLogo = $('heroTraceLogo');

  // STEP 1: Validate target (Real validation)
  runBtn.disabled = true;
  runBtn.innerHTML = '<span>Checking target...</span>';
  const isReachable = await validateCurrentTarget();
  if (!isReachable) {
    runBtn.disabled = false;
    runBtn.innerHTML = '<span>Run Audit</span><span class="btn-arrow">→</span>';
    showToast("Target URL is unreachable. Check the target and try again.", "warning");
    return;
  }

  // STEP 2: PREPARING state
  runBtn.innerHTML = '<span>Preparing...</span>';
  if (commandSurface) commandSurface.classList.add('contracting');
  if (commandCenter) commandCenter.classList.add('launching');
  if (heroLogo) heroLogo.classList.add('illuminating');

  // STEP 3: RUNNING state transition (Atmosphere & Surface Morph)
  await new Promise(r => setTimeout(r, 180));
  runBtn.innerHTML = '<span>Starting audit...</span>';

  const runningState = $('commandRunningState');
  const runningStateLabel = $('runningStateLabel');
  const runningGoal = $('runningGoalText');
  const runningTarget = $('runningTargetText');
  if (runningState) {
    if (runningStateLabel) runningStateLabel.textContent = "STARTING AUDIT";
    if (runningGoal) runningGoal.textContent = `Goal: ${goal}`;
    if (runningTarget) {
      try {
        const parsedUrl = new URL(targetUrl);
        runningTarget.textContent = `Target: ${parsedUrl.hostname}${parsedUrl.pathname}`;
      } catch {
        runningTarget.textContent = `Target: ${targetUrl}`;
      }
    }
    runningState.style.display = 'flex';
  }

  // Initialize Live Session placeholders
  $('sessionGoalTitle').textContent = goal;
  const badge = $('sessionStatusBadge');
  if (badge) {
    badge.className = 'session-status running';
    badge.innerHTML = '<span class="pulse-dot"></span> RUNNING';
  }
  $('sessionStepCount').textContent = '0';
  $('activityFeed').innerHTML = `<div class="log-empty"><span>◌</span><p>Initializing browser session...</p></div>`;
  $('viewportScreenImg').style.display = 'none';
  $('viewportEmptyState').style.display = 'flex';
  $('targetCrosshair').style.display = 'none';

  // STEP 4: Real audit initiation via Backend API
  try {
    const res = await fetch('/api/runs', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        goal: goal,
        target_url: targetUrl,
        max_steps: maxSteps,
        exploration_passes: passes,
      }),
    });

    const run = await res.json();
    if (!res.ok) throw new Error(run.detail || 'Failed to initialize run');

    // SUCCESS state
    activeRunId = run.id;
    currentRunState = run;
    $('viewportUrlText').textContent = run.target_url;

    runBtn.innerHTML = '<span>Audit started</span>';
    showToast(`Audit #${run.id} started`, 'success');

    // Step 5: Smooth handoff into existing Live Session
    await new Promise(r => setTimeout(r, 260));
    switchView('live');

    // Reset command surface styling for clean return
    if (commandSurface) commandSurface.classList.remove('contracting');
    if (commandCenter) commandCenter.classList.remove('launching');
    if (heroLogo) heroLogo.classList.remove('illuminating');
    if (runningState) runningState.style.display = 'none';
    runBtn.disabled = false;
    runBtn.innerHTML = '<span>Run Audit</span><span class="btn-arrow">→</span>';

    // Begin live polling
    await pollRun();
    runPoller = setInterval(pollRun, 600);
  } catch (err) {
    // ERROR state
    console.error("Execution error:", err);
    showToast(`Execution error: ${err.message}`, 'critical');

    if (commandSurface) commandSurface.classList.remove('contracting');
    if (commandCenter) commandCenter.classList.remove('launching');
    if (heroLogo) heroLogo.classList.remove('illuminating');
    if (runningState) runningState.style.display = 'none';

    runBtn.disabled = false;
    runBtn.innerHTML = '<span>Retry</span><span class="btn-arrow">→</span>';

    const failBadge = $('sessionStatusBadge');
    if (failBadge) {
      failBadge.className = 'session-status failed';
      failBadge.textContent = 'FAILED';
    }
  }
}

async function cancelCurrentRun() {
  if (!activeRunId) return;
  try {
    await fetch(`/api/runs/${activeRunId}/cancel`, { method: 'POST' });
    showToast("Audit cancellation sent to agent", "warning");
  } catch (err) {
    console.error("Cancel error:", err);
  }
}

async function pollRun() {
  if (!activeRunId) return;
  try {
    const run = await fetch(`/api/runs/${activeRunId}`).then(r => r.json());

    // Trajectory events
    let events = [];
    try {
      const evRes = await fetch(`/api/runs/${activeRunId}/events`);
      if (evRes.ok) {
        const evData = await evRes.json();
        events = evData.events || [];
      }
    } catch (e) {}

    applyRunDataToUI(run, events);

    const isComplete = run.status === 'completed';
    const isFailed = run.status === 'failed';

    if (isComplete || isFailed) {
      clearInterval(runPoller);
      runPoller = null;

      $('runAuditBtn').disabled = false;
      $('runAuditBtn').innerHTML = '<span>Run Audit</span><span class="btn-arrow">→</span>';
      document.querySelectorAll('.trace-t-mark').forEach(m => m.classList.remove('running'));
      if ($('commandRunningState')) $('commandRunningState').style.display = 'none';

      showToast(isComplete ? `Audit #${run.id} completed successfully` : `Audit halted: ${run.error || 'Failed'}`, isComplete ? 'success' : 'critical');
    }
  } catch (err) {
    console.error("Polling error:", err);
  }
}

function applyRunDataToUI(run, events = []) {
  if (!run) return;
  activeRunId = run.id;
  currentRunState = run;
  if (events && events.length) currentRunEvents = events;

  // Sync home page header status badge
  syncHeaderStatusBadge();

  // Header & Live status
  if ($('sessionGoalTitle')) $('sessionGoalTitle').textContent = run.goal || 'Autonomous Quality Audit';
  if ($('sessionStepCount')) $('sessionStepCount').textContent = run.step_count ?? 0;
  if ($('viewportUrlText')) $('viewportUrlText').textContent = run.target_url || 'http://127.0.0.1:8000/demo/';

  const isComplete = run.status === 'completed';
  const isFailed = run.status === 'failed';
  const isRunning = run.status === 'running';

  const badge = $('sessionStatusBadge');
  if (badge) {
    badge.removeAttribute('style');
    if (isRunning) {
      badge.className = 'session-status running';
      badge.innerHTML = '<span class="pulse-dot"></span> RUNNING';
    } else if (isComplete) {
      badge.className = 'session-status completed';
      badge.textContent = '✓ COMPLETED';
    } else if (isFailed) {
      badge.className = 'session-status failed';
      badge.textContent = '✕ HALTED';
    }
  }

  // Viewport Screenshot Update
  if (run.latest_screenshot) {
    const img = $('viewportScreenImg');
    if (img) {
      img.src = run.latest_screenshot + (run.status === 'running' ? '?t=' + Date.now() : '');
      img.style.display = 'block';
    }
    if ($('viewportEmptyState')) $('viewportEmptyState').style.display = 'none';
  }

  // Reports and metrics
  if (run.metrics) {
    if ($('scoreFriction')) $('scoreFriction').textContent = run.metrics.friction_score !== undefined ? `${run.metrics.friction_score}/100` : '—';
    if ($('scoreWcag')) $('scoreWcag').textContent = run.metrics.wcag_grade || '—';
    if ($('scorePaths')) $('scorePaths').textContent = run.metrics.paths ?? (run.paths_discovered || 1);
    if ($('scoreSteps')) $('scoreSteps').textContent = run.step_count ?? 0;
  }

  if (run.report_url) {
    if ($('btnExportHtml')) {
      $('btnExportHtml').href = run.report_url;
      $('btnExportHtml').style.display = 'inline-flex';
    }
    if ($('btnExportJson')) {
      $('btnExportJson').href = `/api/runs/${run.id}/report.json`;
      $('btnExportJson').style.display = 'inline-flex';
    }
  }

  if ($('reportGoalHeading')) $('reportGoalHeading').textContent = run.goal || 'Autonomous Quality Audit';
  if ($('reportSubline')) $('reportSubline').textContent = `Run ID: ${run.id} · Target: ${run.target_url}`;

  // Findings
  const issues = run.issues || [];
  if ($('findingsNavBadge')) $('findingsNavBadge').textContent = issues.length;
  if ($('countAll')) $('countAll').textContent = issues.length;
  renderFindingsList(issues);

  // Journey
  if (run.journey) {
    renderJourneys(run.journey);
  }

  // Activity events
  if (events && events.length) {
    renderActivityFeed(events);
    const lastAgent = [...events].reverse().find(e => e.kind === 'agent');
    if (lastAgent && lastAgent.data?.action) {
      const marker = $('targetCrosshair');
      if (marker) {
        marker.style.display = 'block';
        const rx = 35 + (run.step_count * 13) % 40;
        const ry = 30 + (run.step_count * 17) % 45;
        marker.style.left = `${rx}%`;
        marker.style.top = `${ry}%`;
      }
    }
  }

  // Editorial Remediation Summary
  renderReportRemediation(run);
}

function renderReportRemediation(run) {
  const container = $('reportEditorialContainer');
  if (!container) return;

  const issues = run.issues || [];
  const metrics = run.metrics || {};
  const isComplete = run.status === 'completed' || run.goal_completed;

  const highIssues = issues.filter(i => (i.severity || '').toLowerCase() === 'high' || (i.severity || '').toLowerCase() === 'critical');
  const mediumIssues = issues.filter(i => (i.severity || '').toLowerCase() === 'medium');
  const goalStatus = isComplete ? '✓ GOAL ACHIEVED' : '✕ AUDIT INTERRUPTED';
  const goalColor = isComplete ? 'var(--green)' : 'var(--red)';

  container.innerHTML = `
    <div class="report-section">
      <div class="report-section-title">Executive Summary
        <span style="float:right;font-size:11px;font-weight:700;color:${goalColor};letter-spacing:0.06em;">${goalStatus}</span>
      </div>
      <p class="report-summary-prose">
        TRACE autonomously navigated <code>${escapeHtml(run.target_url)}</code> over
        <strong>${run.step_count ?? 0} execution steps</strong> across
        <strong>${run.paths_discovered ?? 1} path(s)</strong>.
        During exploration, TRACE identified <strong>${issues.length} defect(s)</strong>
        spanning accessibility, visual, and navigation dimensions.
      </p>
      <div class="report-quick-stats">
        <div class="quick-stat">
          <span class="quick-stat-val" style="color:${metrics.wcag_grade === 'Pass' ? 'var(--green)' : 'var(--amber)'}">${escapeHtml(metrics.wcag_grade || 'Needs Review')}</span>
          <span class="quick-stat-lbl">WCAG Grade</span>
        </div>
        <div class="quick-stat">
          <span class="quick-stat-val" style="color:var(--red)">${highIssues.length}</span>
          <span class="quick-stat-lbl">High Priority</span>
        </div>
        <div class="quick-stat">
          <span class="quick-stat-val" style="color:var(--amber)">${mediumIssues.length}</span>
          <span class="quick-stat-lbl">Medium Priority</span>
        </div>
        <div class="quick-stat">
          <span class="quick-stat-val" style="color:var(--accent-light)">${metrics.friction_score !== undefined ? `${metrics.friction_score}/100` : '—'}</span>
          <span class="quick-stat-lbl">Friction Index</span>
        </div>
      </div>
    </div>

    <div class="report-section">
      <div class="report-section-title">Remediation Roadmap <span style="float:right;font-weight:400;letter-spacing:0;text-transform:none;font-size:11px;">${issues.length} action items</span></div>
      ${issues.length === 0 ? `<p style="color:var(--green);font-size:13px;">No remediation required. All assertions satisfied.</p>` : issues.map(iss => `
        <div class="remediation-item">
          <div class="remediation-header">
            <div style="display:flex;align-items:center;gap:8px;">
              <span class="severity-tag ${(iss.severity || 'medium').toLowerCase()}">${(iss.severity || 'medium').toUpperCase()}</span>
              <span class="remediation-title">${escapeHtml(iss.title)}</span>
            </div>
            <button class="evidence-btn" onclick="inspectEvidence('${escapeHtml(iss.id)}')">Evidence ↗</button>
          </div>
          <div class="remediation-desc">${escapeHtml(iss.description)}</div>
          ${iss.recommendation ? `<div class="remediation-fix"><strong>Fix:</strong> ${escapeHtml(iss.recommendation)}</div>` : ''}
        </div>
      `).join('')}
    </div>
  `;
}


// --------------------------------------------------------------------------
// Activity Feed Renderer
// --------------------------------------------------------------------------

function renderActivityFeed(events) {
  const feed = $('activityFeed');
  if (!feed) return;

  const agentEvents = events.filter(e => e.kind === 'agent' || e.data?.action);
  $('activityEventCount').textContent = `${agentEvents.length} events`;

  if (!agentEvents.length) {
    feed.innerHTML = `<div class="log-empty"><span>◌</span><p>Waiting for agent perception...</p></div>`;
    return;
  }

  feed.innerHTML = agentEvents.slice(-60).map((ev, idx) => {
    const stepNum = ev.data?.step || (idx + 1);
    const action = (ev.data?.action || ev.kind || 'observe').toLowerCase();
    const element = ev.data?.element_id ? `Target: ${ev.data.element_id}` : '';
    const confidence = ev.data?.confidence ? `${(Number(ev.data.confidence) * 100).toFixed(0)}%` : '';

    let cleanMsg = (ev.message || '')
      .replace(/\[(?:GEMINI|GROQ|QWEN|AUTO)\]:?/gi, '')
      .replace(/Step \d+\s*:?/gi, '')
      .trim();
    if (!cleanMsg) cleanMsg = `Executing ${action}`;
    else cleanMsg = cleanMsg.charAt(0).toUpperCase() + cleanMsg.slice(1);

    const metaParts = [element, confidence ? `Conf: ${confidence}` : ''].filter(Boolean);

    return `
      <div class="log-item" onclick="previewEventStep(${stepNum})">
        <span class="log-step-num">${String(stepNum).padStart(2, '0')}</span>
        <div class="log-item-body">
          <span class="log-action-tag ${escapeHtml(action)}">${escapeHtml(action)}</span>
          <div class="log-msg">${escapeHtml(cleanMsg)}</div>
          ${metaParts.length ? `<div class="log-meta">${metaParts.map(escapeHtml).join(' · ')}</div>` : ''}
        </div>
      </div>
    `;
  }).join('');

  feed.scrollTop = feed.scrollHeight;
}

function previewEventStep(stepNum) {
  if (!activeRunId) return;
  const pad = String(stepNum).padStart(3, '0');
  const imgUrl = `/artifacts/${activeRunId}/step_${pad}.png?t=${Date.now()}`;
  $('viewportScreenImg').src = imgUrl;
  $('viewportScreenImg').style.display = 'block';
  if ($('viewportEmptyState')) $('viewportEmptyState').style.display = 'none';
}

// --------------------------------------------------------------------------
// Journey Graph Renderer
// --------------------------------------------------------------------------

function renderJourneys(journey) {
  const container = $('journeyCanvas');
  if (!container) return;

  const nodes = journey?.nodes || [];
  const edges = journey?.edges || [];

  if ($('uniqueStatesVal')) $('uniqueStatesVal').textContent = nodes.length;
  if ($('loopsVal')) $('loopsVal').textContent = nodes.filter(n => n.is_loop).length;
  if ($('pathsVal')) $('pathsVal').textContent = journey?.paths_discovered || 1;

  if (!nodes.length) {
    container.innerHTML = `<div class="journey-empty">No journey nodes mapped yet. Start an audit to generate the state map.</div>`;
    return;
  }

  container.innerHTML = nodes.map((n, idx) => {
    const isGoal = n.is_goal;
    const isLoop = n.is_loop;
    const nodeClass = isGoal ? 'dag-node goal-node' : 'dag-node';
    const labelText = isGoal ? 'GOAL REACHED' : (isLoop ? 'LOOP DETECTED' : `STEP ${String(n.step).padStart(2, '0')}`);

    return `
      <div class="${nodeClass}" onclick="previewEventStep(${n.step})">
        <span class="dag-node-label">${labelText}</span>
        <span class="dag-node-title">${escapeHtml(n.label || 'State View')}</span>
        <span class="dag-node-url">${escapeHtml(n.url || '')}</span>
      </div>
      ${idx < nodes.length - 1 ? '<div class="dag-edge"></div>' : ''}
    `;
  }).join('');
}

// --------------------------------------------------------------------------
// Findings Matrix & Evidence Lightbox
// --------------------------------------------------------------------------

function filterFindings(cat, btn) {
  currentFilter = cat;
  document.querySelectorAll('.filter-tab').forEach(b => b.classList.remove('active'));
  if (btn) btn.classList.add('active');
  searchFindings();
}

function searchFindings() {
  const issues = currentRunState?.issues || [];
  const query = ($('findingsSearchInput')?.value || '').toLowerCase().trim();

  if ($('countAll')) $('countAll').textContent = issues.length;
  if ($('countA11y')) $('countA11y').textContent = issues.filter(i => i.category === 'accessibility').length;
  if ($('countUx')) $('countUx').textContent = issues.filter(i => i.category === 'ux').length;
  if ($('countNav')) $('countNav').textContent = issues.filter(i => i.category === 'navigation').length;
  if ($('countVisual')) $('countVisual').textContent = issues.filter(i => i.category === 'visual').length;

  const filtered = issues.filter(i => {
    const matchCat = currentFilter === 'all' || i.category === currentFilter;
    const matchQuery = !query || i.title.toLowerCase().includes(query) || i.description.toLowerCase().includes(query);
    return matchCat && matchQuery;
  });

  const root = $('findingsList');
  if (!root) return;

  if (!filtered.length) {
    root.innerHTML = `<div class="findings-empty"><svg width="20" height="20" viewBox="0 0 24 24" fill="none"><path d="M20 6L9 17l-5-5" stroke="var(--green)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg> No findings matching current criteria.</div>`;
    return;
  }

  root.innerHTML = `<div class="findings-list">${filtered.map(i => {
    const sev = (i.severity || 'medium').toLowerCase();
    const cat = (i.category || 'general').toUpperCase();

    return `
      <div class="finding-row">
        <div class="finding-meta">
          <div class="finding-badges">
            <span class="severity-tag ${sev}">${sev.toUpperCase()}</span>
            <span class="category-tag">${cat}</span>
            ${i.step ? `<span class="mono" style="font-size:10px;color:var(--ink-2);">Step ${i.step}</span>` : ''}
          </div>
          <button class="evidence-btn" onclick="inspectEvidence('${escapeHtml(i.id)}')">Evidence ↗</button>
        </div>
        <div class="finding-title">${escapeHtml(i.title)}</div>
        <div class="finding-desc">${escapeHtml(i.description)}</div>
        ${i.evidence ? `<div class="finding-evidence-text">EVIDENCE: ${escapeHtml(i.evidence)}</div>` : ''}
        ${i.recommendation ? `<div class="finding-fix"><strong>Fix:</strong> ${escapeHtml(i.recommendation)}</div>` : ''}
      </div>
    `;
  }).join('')}</div>`;
}

function renderFindingsList(issues) {
  searchFindings();
}

function inspectEvidence(findingId) {
  const finding = (currentRunState?.issues || []).find(f => f.id === findingId);
  if (!finding) return;

  const screenshotUrl = finding.screenshot || currentRunState?.latest_screenshot || $('viewportScreenImg').src;
  openEvidenceModal(
    screenshotUrl,
    finding.title,
    finding.description,
    finding.recommendation
  );
}

function openEvidenceModal(imgSrc, title, desc, rec) {
  const modal = $('evidenceModal');
  $('modalEvidenceImg').src = imgSrc || '';
  $('modalEvidenceTitle').textContent = title || 'Forensic Evidence Inspection';
  $('modalEvidenceDesc').textContent = desc || '';

  const recEl = $('modalEvidenceRec');
  if (rec) {
    recEl.style.display = 'block';
    recEl.innerHTML = `<b style="color:var(--status-success);">Developer Remediation:</b> ${escapeHtml(rec)}`;
  } else {
    recEl.style.display = 'none';
  }

  modal.classList.add('open');
}

function closeEvidenceModal() {
  $('evidenceModal')?.classList.remove('open');
}

// --------------------------------------------------------------------------
// Settings Modal
// --------------------------------------------------------------------------

function openSettingsModal() {
  $('settingsModal')?.classList.add('open');
}

function closeSettingsModal() {
  $('settingsModal')?.classList.remove('open');
}

function saveSettings() {
  closeSettingsModal();
  showToast("Preferences saved", "info");
}

// --------------------------------------------------------------------------
// Pre-load Latest Run If Available
// --------------------------------------------------------------------------

async function loadLatestRunIfAvailable() {
  try {
    const res = await fetch('/api/runs');
    if (!res.ok) return;
    const runs = await res.json();
    if (runs && runs.length > 0) {
      const latest = runs[0];
      let events = latest.events || [];
      if (!events.length) {
        try {
          const evRes = await fetch(`/api/runs/${latest.id}/events`);
          if (evRes.ok) {
            const evData = await evRes.json();
            events = evData.events || [];
          }
        } catch (e) {}
      }
      applyRunDataToUI(latest, events);
    }
  } catch (err) {
    console.error("Initial load error:", err);
  }
}

// --------------------------------------------------------------------------
// Toast Notification Engine
// --------------------------------------------------------------------------

function showToast(message, type = 'info', duration = 2800) {
  const stack = $('toastStack');
  if (!stack) return;

  const pill = document.createElement('div');
  pill.className = 'toast';

  const iconMap = { info: '✦', success: '✓', warning: '⚠', critical: '✕' };
  const colorMap = { info: 'var(--accent-light)', success: 'var(--green)', warning: 'var(--amber)', critical: 'var(--red)' };

  pill.innerHTML = `
    <span style="font-weight:700;color:${colorMap[type] || 'var(--accent-light)'}">${iconMap[type] || '✦'}</span>
    <span>${escapeHtml(message)}</span>
  `;

  stack.appendChild(pill);
  setTimeout(() => {
    pill.style.opacity = '0';
    pill.style.transform = 'translateY(6px)';
    pill.style.transition = 'all 0.2s ease';
    setTimeout(() => pill.remove(), 210);
  }, duration);
}

// --------------------------------------------------------------------------
// Global Shortcuts
// --------------------------------------------------------------------------

function setupShortcuts() {
  document.addEventListener('keydown', (e) => {
    // Focus command on Cmd+K or Ctrl+K or '/'
    if (((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') ||
        (e.key === '/' && !['INPUT', 'TEXTAREA'].includes(document.activeElement.tagName))) {
      e.preventDefault();
      switchView('command');
      $('goalInput')?.focus();
    }

    // Submit on Enter inside goal input
    if (document.activeElement === $('goalInput') && e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      startAuditRun();
    }

    // Escape closes modals
    if (e.key === 'Escape') {
      closeEvidenceModal();
      closeSettingsModal();
    }
  });
}

