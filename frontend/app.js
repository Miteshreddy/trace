let activeRun = null;
let poller = null;

const $ = (id) => document.getElementById(id);

function focusSection(id) {
  document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function applyPreset(num) {
  if (num === 1) {
    $('goal').value = "Find a blue running shoe under $100 and add it to the cart.";
    $('maxSteps').value = 16;
    $('passes').value = 1;
  } else if (num === 2) {
    $('goal').value = "Discover alternative paths to find and cart a blue running shoe (compare category filters vs search bar).";
    $('maxSteps').value = 18;
    $('passes').value = 2;
  } else if (num === 3) {
    $('goal').value = "Browse running shoes, dismiss the promotional VIP modal overlay when it appears, and add the Aero Blue Runner to cart.";
    $('maxSteps').value = 16;
    $('passes').value = 1;
  }
  $('goal').focus();
}

function setStatus(status) {
  const el = $('runStatus');
  el.className = 'run-status ' + status;
  el.textContent = status.toUpperCase();
}

/* AI Provider Mode Switching */
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
    }
  } catch (err) {
    console.error("Failed to switch mode:", err);
  }
}

function updateModeUI(mode) {
  const upper = (mode || 'AUTO').toUpperCase();
  $('btnModeAuto').classList.toggle('active', upper === 'AUTO');
  $('btnModeGroq').classList.toggle('active', upper === 'GROQ');
  $('btnModeGemini').classList.toggle('active', upper === 'GEMINI');
  $('activeModeBadge').textContent = `AI ENGINE: ${upper}`;
  $('sidebarEngineStatus').textContent = `${upper} · Multi-Provider Active`;
}

/* AI Provider Diagnostic Test */
async function testAIProviders() {
  const btn = $('btnTestProviders');
  const feedback = $('diagFeedback');
  btn.disabled = true;
  btn.innerHTML = '<span>⚡ Testing...</span>';
  feedback.textContent = 'Probing Groq and Google Gemini endpoints...';

  try {
    const res = await fetch('/api/providers/test', { method: 'POST' });
    const data = await res.json();
    const groq = data.results?.groq;
    const gemini = data.results?.gemini;

    const groqMsg = groq?.connected ? `Groq ✓ Connected (${groq.latency_ms}ms)` : `Groq ✗ Failed`;
    const geminiMsg = gemini?.connected ? `Gemini ✓ Connected (${gemini.latency_ms}ms)` : `Gemini ✗ Failed`;

    feedback.innerHTML = `<b>${groqMsg}</b> &nbsp;|&nbsp; <b>${geminiMsg}</b>`;
    fetchProviderHealth();
  } catch (err) {
    feedback.textContent = `Diagnostic error: ${err.message}`;
  } finally {
    btn.disabled = false;
    btn.innerHTML = '<span>⚡ Test AI Providers</span>';
  }
}

/* Fetch & Display Provider Health Telemetry */
async function fetchProviderHealth() {
  try {
    const res = await fetch('/api/providers');
    const data = await res.json();
    updateModeUI(data.mode);

    const groq = data.providers?.groq;
    if (groq) {
      $('groqStatus').textContent = groq.status.charAt(0).toUpperCase() + groq.status.slice(1);
      $('groqDot').className = `health-dot ${groq.status}`;
      $('groqLatency').textContent = groq.latency_ms > 0 ? `${groq.latency_ms}ms` : '—';
    }

    const gemini = data.providers?.gemini;
    if (gemini) {
      $('geminiStatus').textContent = gemini.status.charAt(0).toUpperCase() + gemini.status.slice(1);
      $('geminiDot').className = `health-dot ${gemini.status}`;
      $('geminiLatency').textContent = gemini.latency_ms > 0 ? `${gemini.latency_ms}ms` : '—';
    }
  } catch (err) {
    console.error("Failed to fetch provider health:", err);
  }
}

/* Render Trajectory Events */
function renderEvents(events) {
  const root = $('trajectory');
  if (!events.length) {
    root.innerHTML = '<div class="empty-state"><span class="empty-icon">◌</span><p>Waiting for agent perception...</p></div>';
    return;
  }

  root.innerHTML = events.slice(-75).map((e, idx) => {
    const action = (e.data?.action || e.kind).toLowerCase();
    const rationale = e.data?.rationale || '';
    const stepNum = e.data?.step || (idx + 1);
    const element = e.data?.element_id ? `Target: ${escapeHtml(e.data.element_id)}` : '';
    const provider = e.data?.provider ? String(e.data.provider).toLowerCase() : '';

    return `
      <div class="trajectory-item" onclick="previewStepScreenshot(${stepNum})">
        <div class="step-circle">${String(stepNum).padStart(2, '0')}</div>
        <div class="step-details">
          <div class="step-top">
            <span class="action-pill ${action}">${escapeHtml(action)}</span>
            ${provider ? `<span class="prov-badge ${provider}">${escapeHtml(provider.toUpperCase())}</span>` : ''}
            <span class="meta-val" style="font-size:10px;color:#71717a;">${element}</span>
          </div>
          <div class="step-message">${escapeHtml(e.message)}</div>
          ${rationale ? `<div class="step-rationale">“${escapeHtml(rationale)}”</div>` : ''}
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
  img.classList.remove('hidden');
  $('screenEmpty').classList.add('hidden');
  $('screenCaption').textContent = `Previewing Step ${stepNum} (Action Overlay Active)`;
}

function renderIssues(issues) {
  $('issueCount').textContent = `${issues.length} ISSUES`;
  const root = $('findingsList');
  if (!issues.length) {
    root.innerHTML = '<div class="muted-empty">No issues detected yet. Interface demonstrated zero friction!</div>';
    return;
  }

  root.innerHTML = issues.map(i => `
    <div class="finding-card">
      <div class="finding-top">
        <span class="sev-badge ${i.severity}">${escapeHtml(i.severity.toUpperCase())}</span>
        <span class="f-cat">${escapeHtml(i.category.toUpperCase())}</span>
      </div>
      <div class="f-title">${escapeHtml(i.title)}</div>
      <div class="f-desc">${escapeHtml(i.description)}</div>
      ${i.recommendation ? `<div class="f-rec"><b>Fix:</b> ${escapeHtml(i.recommendation)}</div>` : ''}
    </div>
  `).join('');
}

async function refresh() {
  if (!activeRun) return;
  try {
    const run = await fetch(`/api/runs/${activeRun}`).then(r => r.json());
    $('runId').textContent = run.id;
    $('goalMeta').textContent = run.goal;
    $('stepMeta').textContent = run.step_count;
    $('screenUrl').textContent = run.target_url;
    setStatus(run.status === 'completed' ? 'done' : run.status);

    if (run.latest_screenshot) {
      const img = $('screenImage');
      img.src = run.latest_screenshot + '?t=' + Date.now();
      img.classList.remove('hidden');
      $('screenEmpty').classList.add('hidden');
      $('screenCaption').textContent = `Latest visual observation · Step ${run.step_count}`;
    }

    // Metrics
    if (run.metrics) {
      const friction = run.metrics.friction_score ?? '—';
      $('frictionMetric').textContent = friction !== '—' ? `${friction}/100` : '—';
      $('wcagMetric').textContent = run.metrics.wcag_grade || '—';
      $('pathsMetric').textContent = run.metrics.paths ?? (run.paths_discovered || 1);
    }
    $('goalMetric').textContent = run.status === 'completed' ? (run.goal_completed ? 'COMPLETE ✓' : 'PARTIAL') : 'IN PROGRESS';

    renderIssues(run.issues || []);

    if (run.report_url) {
      $('reportLink').href = run.report_url;
      $('reportLink').classList.remove('hidden');
    }

    const eventsData = await fetch(`/api/runs/${activeRun}/events`).then(r => r.json());
    const evs = eventsData.events || [];
    renderEvents(evs);

    const lastAgent = [...evs].reverse().find(e => e.kind === 'agent');
    if (lastAgent && lastAgent.data?.confidence) {
      $('confMeta').textContent = `${(Number(lastAgent.data.confidence) * 100).toFixed(0)}%`;
    }

    if (run.status === 'failed') {
      $('runError').textContent = run.error || 'Run encountered a failure.';
      $('runError').classList.remove('hidden');
    }

    if (run.status === 'completed' || run.status === 'failed') {
      $('runBtn').disabled = false;
      $('runBtn').innerHTML = '<span class="btn-text">Run Autonomous Audit</span><span class="btn-arrow">➔</span>';
      if (poller) {
        clearInterval(poller);
        poller = null;
      }
      fetchProviderHealth();
    }
  } catch (err) {
    console.error("Refresh error:", err);
  }
}

$('runBtn').addEventListener('click', async () => {
  $('runError').classList.add('hidden');
  $('reportLink').classList.add('hidden');
  $('runBtn').disabled = true;
  $('runBtn').innerHTML = '<span class="btn-text">Agent Navigating…</span><span class="btn-arrow">◌</span>';

  try {
    const goalText = $('goal').value.trim();
    if (!goalText) throw new Error('Please enter a user goal first.');

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
    $('runId').textContent = run.id;
    $('goalMeta').textContent = run.goal;
    setStatus('running');
    $('trajectory').innerHTML = '';
    $('screenImage').classList.add('hidden');
    $('screenEmpty').classList.remove('hidden');
    $('screenCaption').textContent = 'Launching browser session...';

    await refresh();
    poller = setInterval(refresh, 650);
  } catch (err) {
    $('runError').textContent = err.message;
    $('runError').classList.remove('hidden');
    $('runBtn').disabled = false;
    $('runBtn').innerHTML = '<span class="btn-text">Run Autonomous Audit</span><span class="btn-arrow">➔</span>';
  }
});

function escapeHtml(val) {
  return String(val ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/'/g, '&#39;')
    .replace(/"/g, '&quot;');
}

// Initial fetch
fetchProviderHealth();
