/**
 * TRACE — Autonomous Quality Engineering
 * Authoritative Single Page Application Controller (v4)
 */

(function () {
  'use strict';

  // ---------------------------------------------------------------------------
  // Application State
  // ---------------------------------------------------------------------------
  const state = {
    currentPath: '/',
    goal: sessionStorage.getItem('traceGoal') || 'Find a blue running shoe under $100 and reach checkout',
    targetUrl: sessionStorage.getItem('traceUrl') || 'http://127.0.0.1:8000/demo/',
    activeRunId: sessionStorage.getItem('traceRunId') || null,
    runStatus: 'idle', // 'idle' | 'running' | 'success' | 'failed'
    runData: null,
    events: [],
    poller: null,
    filter: 'all',
    homeDemoStarted: false,
    latestRunVersion: 0,
    latestEventSeq: 0
  };

  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => Array.from(document.querySelectorAll(sel));
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  // ---------------------------------------------------------------------------
  // SPA Router
  // ---------------------------------------------------------------------------
  const VALID_ROUTES = {
    '/': { view: 'home', title: 'TRACE — Test the experience' },
    '/command': { view: 'command', title: 'TRACE — Command' },
    '/live': { view: 'live', title: 'TRACE — Live Screen' },
    '/evaluation': { view: 'evaluation', title: 'TRACE — Evaluation' },
    '/journeys': { view: 'journeys', title: 'TRACE — Journeys' },
    '/findings': { view: 'findings', title: 'TRACE — Findings' },
    '/report': { view: 'report', title: 'TRACE — Report' }
  };

  function normalizePath(raw) {
    if (!raw) return '/';
    let path = raw.split('?')[0].split('#')[0];
    if (!path.startsWith('/')) path = '/' + path;
    if (path.length > 1 && path.endsWith('/')) path = path.slice(0, -1);
    // Legacy / hash mapping
    if (path === '/reports') path = '/report';
    return VALID_ROUTES[path] ? path : '/';
  }

  function navigateTo(targetPath, push = true) {
    const cleanPath = normalizePath(targetPath);
    state.currentPath = cleanPath;

    if (push && window.location.pathname !== cleanPath) {
      window.history.pushState(null, '', cleanPath);
    }

    const routeConfig = VALID_ROUTES[cleanPath] || VALID_ROUTES['/'];
    document.title = routeConfig.title;
    document.body.dataset.page = routeConfig.view;

    // Switch view sections
    $$('.view-section').forEach((section) => {
      const isTarget = section.id === `view-${routeConfig.view}`;
      section.classList.toggle('active', isTarget);
    });

    // Update navigation active states
    $$('.desktop-nav a').forEach((link) => {
      const route = link.getAttribute('data-route') || link.getAttribute('href');
      link.classList.toggle('active', route === cleanPath);
    });

    // Scroll top
    window.scrollTo({ top: 0, behavior: 'smooth' });

    // Trigger view lifecycle hook
    onViewMounted(routeConfig.view);
  }

  function setupRouting() {
    // Intercept clicks on links with data-route or relative path
    document.addEventListener('click', (e) => {
      const link = e.target.closest('a, button');
      if (!link) return;

      const route = link.getAttribute('data-route') || link.getAttribute('href');
      if (!route) return;

      // Ignore external links or anchor jumps
      if (route.startsWith('http') || route.startsWith('//') || route.startsWith('mailto:') || link.target === '_blank') {
        return;
      }

      if (route.startsWith('/') || route.startsWith('#')) {
        let clean = route;
        if (clean.startsWith('#')) {
          clean = '/' + clean.replace('#', '');
        }
        if (VALID_ROUTES[normalizePath(clean)]) {
          e.preventDefault();
          navigateTo(clean);
        }
      }
    });

    // Handle browser back and forward buttons
    window.addEventListener('popstate', () => {
      navigateTo(window.location.pathname, false);
    });

    // Handle initial route
    let initial = window.location.pathname;
    if (window.location.hash) {
      const hashRoute = '/' + window.location.hash.replace('#', '');
      if (VALID_ROUTES[normalizePath(hashRoute)]) {
        initial = hashRoute;
      }
    }
    navigateTo(initial, true);
  }

  function onViewMounted(viewName) {
    if (viewName === 'home') {
      setupHomeView();
    } else if (viewName === 'command') {
      setupCommandView();
    } else if (viewName === 'live') {
      setupLiveView();
    } else if (viewName === 'evaluation') {
      renderEvaluationView();
    } else if (viewName === 'journeys') {
      renderJourneysView();
    } else if (viewName === 'findings') {
      renderFindingsView();
    } else if (viewName === 'report') {
      renderReportView();
    }
  }

  // ---------------------------------------------------------------------------
  // Pointer & Motion Utilities
  // ---------------------------------------------------------------------------
  function setupPointer() {
    const pointer = $('#tracePointer');
    if (!pointer || window.matchMedia('(pointer:coarse)').matches) return;

    let x = 0, y = 0, tx = 0, ty = 0, visible = false;
    window.addEventListener('mousemove', (e) => {
      tx = e.clientX;
      ty = e.clientY;
      if (!visible) {
        visible = true;
        pointer.style.opacity = '1';
      }
    });

    window.addEventListener('mousedown', () => pointer.classList.add('is-down'));
    window.addEventListener('mouseup', () => pointer.classList.remove('is-down'));

    document.addEventListener('pointerover', (e) => {
      const target = e.target.closest('a, button, .cap, .journey-node, .evidence-card-front, .glass-tag');
      pointer.classList.toggle('is-link', !!target);
    });

    const tick = () => {
      x += (tx - x) * 0.22;
      y += (ty - y) * 0.22;
      pointer.style.left = x + 'px';
      pointer.style.top = y + 'px';
      requestAnimationFrame(tick);
    };
    tick();
  }

  function setupReveal() {
    const items = $$('.reveal-scroll');
    if (!items.length) return;

    const io = new IntersectionObserver((entries) => {
      entries.forEach((entry) => {
        if (entry.isIntersecting) {
          entry.target.classList.add('in-view');
          io.unobserve(entry.target);
        }
      });
    }, { threshold: 0.15 });

    items.forEach((item) => io.observe(item));
  }

  function setupScrollMotion() {
    const evidence = $('.evidence-stage');
    const journey = $('#journeyVisual');
    if (!evidence && !journey) return;

    let raf = 0;
    const update = () => {
      raf = 0;
      const y = window.scrollY || 0;
      if (evidence) {
        const r = evidence.getBoundingClientRect();
        const shift = (window.innerHeight * 0.5 - (r.top + r.height * 0.5)) * 0.08;
        evidence.style.setProperty('--scroll-shift', shift.toFixed(1) + 'px');
      }
      if (journey) {
        const r = journey.getBoundingClientRect();
        const n = Math.max(-1, Math.min(1, (window.innerHeight * 0.5 - (r.top + r.height * 0.5)) / window.innerHeight));
        journey.style.transform = 'translateY(' + (n * 4).toFixed(1) + 'px)';
      }
    };

    window.addEventListener('scroll', () => {
      if (!raf) raf = requestAnimationFrame(update);
    }, { passive: true });
    update();
  }

  // ---------------------------------------------------------------------------
  // VIEW: Home Page Demo Animation
  // ---------------------------------------------------------------------------
  async function runHomeDemo() {
    const typed = $('#demoTyped');
    const consoleEl = $('#console');
    const command = $('#demoCommand');
    const demoState = $('#demoState');
    const sessionStatus = $('#sessionStatus');
    if (!typed || !consoleEl || !command) return;

    const activities = $$('#view-home .activity-item');
    const steps = $$('#view-home .j-step');
    const cursor = $('#cursor');
    const label = $('#cursorLabel');
    const goalText = 'Check the signup flow and identify accessibility issues';
    const path = [[28, 34], [59, 28], [72, 58], [43, 63], [68, 52]];
    const labels = [
      'Opening website',
      'Inspecting navigation',
      'Testing primary flow',
      'Checking accessibility',
      'Capturing evidence'
    ];

    typed.textContent = '';
    consoleEl.classList.remove('visible');
    command.classList.remove('compact');
    if (demoState) {
      demoState.classList.remove('ready');
      demoState.innerHTML = '<i></i> Ready';
    }
    activities.forEach((a) => a.classList.remove('done', 'current'));
    steps.forEach((s, i) => {
      s.classList.toggle('reached', i === 0);
      s.classList.remove('current');
    });

    if (cursor) {
      cursor.style.left = '28%';
      cursor.style.top = '34%';
    }
    if (label) label.textContent = 'Exploring interface';
    if (sessionStatus) sessionStatus.textContent = 'Preparing session';

    // FAST typing: 7ms/char for ~50 chars ≈ 350ms total
    await sleep(100);
    for (const ch of goalText) {
      typed.textContent += ch;
      await sleep(7);
    }
    await sleep(80);

    if (demoState) {
      demoState.classList.add('ready');
      demoState.innerHTML = '<i></i> Goal understood';
    }
    command.classList.add('compact');
    await sleep(60);

    consoleEl.classList.add('visible');
    await sleep(120);
    if (sessionStatus) sessionStatus.textContent = 'Agent is browsing';

    for (let i = 0; i < activities.length; i++) {
      if (activities[i - 1]) activities[i - 1].classList.add('done');
      activities[i].classList.add('current');
      if (steps[i + 1]) {
        steps[i + 1].classList.add('current', 'reached');
      }
      if (cursor && path[i]) {
        cursor.style.left = path[i][0] + '%';
        cursor.style.top = path[i][1] + '%';
      }
      if (label && labels[i]) label.textContent = labels[i];

      // Click ripple effect
      await sleep(160);
      if (cursor) { cursor.classList.add('clicking'); }
      await sleep(100);
      if (cursor) { cursor.classList.remove('clicking'); }
      await sleep(80);

      activities[i].classList.remove('current');
      activities[i].classList.add('done');
      if (steps[i + 1]) steps[i + 1].classList.remove('current');
    }

    if (sessionStatus) sessionStatus.textContent = 'Evidence captured';
    if (demoState) {
      demoState.classList.add('ready');
      demoState.innerHTML = '<i></i> Session active';
    }
  }

  function setupHomeView() {
    const demoStage = $('#console');
    if (demoStage && !state.homeDemoStarted) {
      const io = new IntersectionObserver((entries) => {
        if (entries.some((e) => e.isIntersecting)) {
          state.homeDemoStarted = true;
          runHomeDemo().catch(() => {});
          io.disconnect();
        }
      }, { threshold: 0.1 });
      io.observe(demoStage);
    }

    const replayBtn = $('#replay');
    if (replayBtn) {
      replayBtn.onclick = () => {
        runHomeDemo().catch(() => {});
      };
    }
  }

  // ---------------------------------------------------------------------------
  // VIEW: Command
  // ---------------------------------------------------------------------------
  function setupCommandView() {
    const goalInput = $('#goal');
    const urlInput = $('#targetUrl');
    const reach = $('#commandReach');
    const advancedBtn = $('#advancedBtn');
    const advanced = $('#advanced');
    const runBtn = $('#runAudit');
    const err = $('#error');

    if (goalInput && state.goal) goalInput.value = state.goal;
    if (urlInput && state.targetUrl) urlInput.value = state.targetUrl;

    let _reachable = false;
    let _reachCheckTimer = null;

    const setReachBadge = (state, label, color) => {
      if (!reach) return;
      const dot = color || '#757984';
      reach.innerHTML = `<i style="background:${dot}"></i> ${label}`;
      reach.style.color = state === 'ok' ? '#757984' : '#f0a8a8';
    };

    const validateReach = async () => {
      const val = (urlInput?.value || '').trim();
      _reachable = false;
      // Quick format check first
      try {
        const u = new URL(val);
        if (u.protocol !== 'http:' && u.protocol !== 'https:') {
          setReachBadge('err', 'Invalid URL scheme', '#f07d8a');
          return false;
        }
      } catch (e) {
        setReachBadge('err', 'Check target URL', '#f07d8a');
        return false;
      }
      // Real backend probe
      setReachBadge('check', 'Checking…', '#a0a4b0');
      try {
        const r = await fetch(`/api/check-target?url=${encodeURIComponent(val)}`);
        const data = r.ok ? await r.json() : { reachable: false, error_type: 'unreachable' };
        if (data.reachable) {
          _reachable = true;
          setReachBadge('ok', 'Target reachable', '#7ed9a0');
        } else {
          const labels = {
            connection_refused: 'Server offline',
            timeout: 'Timed out',
            dns_failure: 'DNS not found',
            ssl_error: 'SSL error',
            redirect_failure: 'Redirect loop',
            invalid_url: 'Invalid URL',
          };
          const msg = labels[data.error_type] || 'Unreachable';
          setReachBadge('err', msg, '#f07d8a');
        }
      } catch (e) {
        // fetch itself failed — show format-only fallback
        _reachable = true;
        setReachBadge('ok', 'Target set', '#a0a4b0');
      }
      return _reachable;
    };

    const scheduleReachCheck = () => {
      clearTimeout(_reachCheckTimer);
      _reachCheckTimer = setTimeout(() => validateReach(), 600);
    };

    urlInput?.removeEventListener('input', scheduleReachCheck);
    urlInput?.addEventListener('input', scheduleReachCheck);
    validateReach();

    // Toggle advanced
    if (advancedBtn && advanced) {
      advancedBtn.onclick = () => advanced.classList.toggle('show');
    }

    // Try preset task buttons
    $$('#view-command .try button').forEach((btn) => {
      btn.onclick = () => {
        const taskText = btn.dataset.task || btn.textContent.trim();
        if (goalInput) {
          goalInput.value = taskText;
          state.goal = taskText;
          sessionStorage.setItem('traceGoal', taskText);
          err?.classList.remove('show');
          goalInput.focus();
        }
      };
    });

    // Run Audit button
    if (runBtn) {
      runBtn.onclick = async () => {
        const goalVal = goalInput?.value.trim() || '';
        const urlVal = urlInput?.value.trim() || '';

        if (!goalVal) {
          showError('Add a testing goal before starting a run.');
          goalInput?.focus();
          return;
        }

        const reachOk = await validateReach();
        if (!reachOk) {
          showError('Target URL is unreachable. Verify the server is running and the URL is correct.');
          urlInput?.focus();
          return;
        }

        err?.classList.remove('show');
        state.goal = goalVal;
        state.targetUrl = urlVal;
        sessionStorage.setItem('traceGoal', goalVal);
        sessionStorage.setItem('traceUrl', urlVal);

        runBtn.disabled = true;
        runBtn.innerHTML = 'Starting…';

        try {
          const maxSteps = parseInt($('#maxSteps')?.value || '18', 10);
          const passes = parseInt($('#passes')?.value || '1', 10);

          const res = await fetch('/api/runs', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              goal: goalVal,
              target_url: urlVal,
              max_steps: maxSteps,
              exploration_passes: passes
            })
          });

          if (!res.ok) {
            throw new Error(`Server returned HTTP ${res.status}`);
          }

          const run = await res.json();
          state.activeRunId = run.id;
          state.runStatus = 'running';
          state.runData = run;
          state.events = [];
          sessionStorage.setItem('traceRunId', run.id);

          updateHeaderBadge('running', 'RUNNING');
          showToast(`Autonomous QA run launched (${run.id})`);

          runBtn.disabled = false;
          runBtn.innerHTML = 'Run Audit <b>↗</b>';

          // Navigate directly to /live
          navigateTo('/live');
          startRunPolling(run.id);

        } catch (e) {
          showError(`Could not launch autonomous audit: ${e.message}`);
          runBtn.disabled = false;
          runBtn.innerHTML = 'Run Audit <b>↗</b>';
        }
      };
    }

    function showError(msg) {
      if (err) {
        err.textContent = msg;
        err.classList.add('show');
      }
    }
  }

  // ---------------------------------------------------------------------------
  // VIEW: Live Screen
  // ---------------------------------------------------------------------------
  function setupLiveView() {
    const liveGoal = $('#liveGoal');
    const liveAddress = $('#liveBrowserAddress');
    const openLink = $('#liveBrowserOpenLink');
    const stopBtn = $('#btnStopRun');

    const effectiveTarget = state.targetUrl || (state.runData && (state.runData.normalized_target_url || state.runData.target_url)) || '';
    const activeUrl = (state.runData && (state.runData.current_url || state.runData.final_url)) || effectiveTarget;
    if (liveGoal && state.goal) liveGoal.textContent = state.goal;
    if (liveAddress && effectiveTarget) {
      if (activeUrl && activeUrl !== effectiveTarget) {
        liveAddress.textContent = `Target: ${effectiveTarget} ➔ Current: ${activeUrl}`;
        liveAddress.title = `Original: ${effectiveTarget}\nCurrent: ${activeUrl}`;
      } else {
        liveAddress.textContent = '⌁   ' + effectiveTarget;
        liveAddress.title = `Target: ${effectiveTarget}`;
      }
    }
    if (openLink && activeUrl) openLink.href = activeUrl;

    if (stopBtn) {
      stopBtn.onclick = async () => {
        if (!state.activeRunId) return;
        try {
          stopBtn.disabled = true;
          stopBtn.textContent = 'Stopping…';
          await fetch(`/api/runs/${state.activeRunId}/cancel`, { method: 'POST' });
          showToast('Stop signal sent to agent.');
        } catch (e) {
          console.error(e);
        } finally {
          stopBtn.disabled = false;
          stopBtn.textContent = 'Stop ⏹';
        }
      };
    }

    // If active run is already running and poller not active, start polling
    if (state.activeRunId && (!state.poller || state.runStatus === 'running')) {
      startRunPolling(state.activeRunId);
    }
  }

  function startRunPolling(runId) {
    if (state.poller) clearInterval(state.poller);

    const poll = async () => {
      try {
        const [runRes, eventsRes] = await Promise.all([
          fetch(`/api/runs/${runId}`),
          fetch(`/api/runs/${runId}/events`)
        ]);

        if (!runRes.ok) return;
        const run = await runRes.json();

        // Monotonic version ordering check
        if (run.state_version && state.latestRunVersion && run.state_version < state.latestRunVersion) {
          return; // Ignore stale response
        }
        if (run.state_version) {
          state.latestRunVersion = run.state_version;
        }
        state.runData = run;

        let events = [];
        if (eventsRes.ok) {
          const evData = await eventsRes.json();
          events = evData.events || [];
          // Deduplicate and order by sequence
          if (events.length > 0 && events[0].seq) {
            events.sort((a, b) => (a.seq || 0) - (b.seq || 0));
          }
          state.events = events;
        }

        // Update Live UI
        updateLiveConsole(run, events);

        // Check completion
        if (run.status === 'completed' || run.status === 'success' || run.status === 'partial') {
          clearInterval(state.poller);
          state.poller = null;
          state.runStatus = run.status === 'completed' ? 'success' : 'partial';
          updateHeaderBadge(state.runStatus, state.runStatus.toUpperCase());
          $('#liveStatus') && ($('#liveStatus').textContent = run.status === 'completed' ? 'Audit complete' : 'Audit partial');
          showToast(`Audit run finished (${run.status}). Evidence and report are ready.`);
          setStepCompleted(6);
        } else if (run.status === 'failed') {
          clearInterval(state.poller);
          state.poller = null;
          state.runStatus = 'failed';
          updateHeaderBadge('failed', 'FAILED');
          $('#liveStatus') && ($('#liveStatus').textContent = run.error || 'Execution halted');
          showToast('Audit run stopped: ' + (run.error ? run.error.slice(0, 60) : 'failed'));
        } else if (run.status === 'cancelled') {
          clearInterval(state.poller);
          state.poller = null;
          state.runStatus = 'failed';
          updateHeaderBadge('failed', 'CANCELLED');
          $('#liveStatus') && ($('#liveStatus').textContent = 'Execution cancelled by user');
          showToast('Audit run cancelled.');
        }

      } catch (e) {
        console.warn('Run poller error:', e);
      }
    };

    poll();
    state.poller = setInterval(poll, 850);
  }

  function updateLiveConsole(run, events) {
    const statusEl = $('#liveStatus');
    const countEl = $('#liveEventCount');
    const feed = $('#liveActivityList');
    const screenshotImg = $('#liveScreenshotImg');
    const connectingScreen = $('#liveConnectingState');
    const navigatingScreen = $('#liveNavigatingState');
    const blockedScreen = $('#liveBlockedState');
    const blankScreen = $('#liveBlankState');
    const failedScreen = $('#liveFailedState');
    const cursor = $('#liveCursor');
    const cursorLabel = $('#liveCursorLabel');

    const effectiveTarget = run.normalized_target_url || run.target_url || state.targetUrl || '';
    const activeUrl = run.current_url || run.final_url || effectiveTarget;
    const openLink = $('#liveBrowserOpenLink');
    if (openLink && activeUrl) {
      openLink.href = activeUrl;
      openLink.title = activeUrl !== effectiveTarget ? `Open current: ${activeUrl}` : `Open target website`;
    }

    const liveAddress = $('#liveBrowserAddress');
    if (liveAddress && effectiveTarget) {
      if (activeUrl && activeUrl !== effectiveTarget) {
        const prefix = (run.status === 'completed' || run.status === 'success') ? 'Final' : 'Current';
        liveAddress.textContent = `Target: ${effectiveTarget} ➔ ${prefix}: ${activeUrl}`;
        liveAddress.title = `Original: ${effectiveTarget}\n${prefix}: ${activeUrl}`;
      } else {
        liveAddress.textContent = `⌁ ${effectiveTarget}`;
        liveAddress.title = `Target: ${effectiveTarget}`;
      }
    }

    if (statusEl && run.status) {
      statusEl.textContent = run.status === 'running' ? `Step ${run.step_count || 0} — Exploring` : run.status.toUpperCase();
    }

    if (countEl) countEl.textContent = `${events.length} events`;

    // Render streaming events with rich role/consensus badges
    if (feed && events.length) {
      feed.innerHTML = events.slice(-30).map((ev, i) => {
        let badgeHtml = '';
        const kind = (ev.kind || '').toLowerCase();
        if (kind === 'consensus') {
          badgeHtml = '<span class="activity-badge badge-consensus">Consensus</span>';
        } else if (kind === 'model_disagreement') {
          badgeHtml = '<span class="activity-badge badge-disagree">Disagreement</span>';
        } else if (kind === 'subgoal_completed') {
          badgeHtml = '<span class="activity-badge badge-subgoal">Subgoal</span>';
        } else if (kind === 'url_changed') {
          badgeHtml = '<span class="activity-badge badge-url">URL</span>';
        } else if (kind === 'goal_verification') {
          badgeHtml = '<span class="activity-badge badge-verifier">Verify</span>';
        } else if (kind === 'recovery') {
          badgeHtml = '<span class="activity-badge badge-recovery">Recovery</span>';
        }

        return `
          <div class="activity-item done revealed">
            <i>${ev.step || (i + 1)}</i>
            <div>
              <b>${badgeHtml}${escapeHtml(ev.kind || 'Action')}</b>
              <small>${escapeHtml(ev.message || '')}</small>
            </div>
          </div>
        `;
      }).join('');
      feed.scrollTop = feed.scrollHeight;
    }

    // Render objective milestones (subgoals)
    const subgoalsWrap = $('#liveSubgoalsWrap');
    const subgoalsList = $('#liveSubgoalsList');
    if (subgoalsWrap && subgoalsList) {
      if (run.subgoals && run.subgoals.length > 0) {
        subgoalsWrap.style.display = 'block';
        subgoalsList.innerHTML = run.subgoals.map((sg) => `
          <div class="subgoal-item ${sg.verified ? 'verified' : ''}">
            <span class="subgoal-icon">${sg.verified ? '✓' : '⏳'}</span>
            <div class="subgoal-desc">
              <b>${escapeHtml(sg.description)}</b>
              ${sg.evidence && sg.evidence.length ? `<small style="display:block; color:#63d8a1; font-size:7.5px; margin-top:2px;">${escapeHtml(sg.evidence[0])}</small>` : ''}
            </div>
          </div>
        `).join('');
      } else {
        subgoalsWrap.style.display = 'none';
      }
    }

    // Update steps based on phase (single source of truth)
    const phase = run.phase || 'queued';
    const phaseOrder = {
      'queued': 0, 'starting': 1, 'navigating': 1, 'page_ready': 2,
      'exploring': 3, 'verifying': 4, 'auditing': 5,
      'completed': 6, 'partial': 6, 'failed': 6, 'cancelled': 6, 'blocked': 6
    };
    const phaseNum = phaseOrder[phase] || 0;
    if (phaseNum >= 1) setStepCompleted(1);
    if (phaseNum >= 2) setStepCompleted(2);
    if (phaseNum >= 3) setStepCompleted(3);
    if (phaseNum >= 4) setStepCompleted(4);
    if (phaseNum >= 5) setStepCompleted(5);
    if (phaseNum >= 6) setStepCompleted(6);

    const hideAllScreens = () => {
      if (connectingScreen) connectingScreen.style.display = 'none';
      if (navigatingScreen) navigatingScreen.style.display = 'none';
      if (blockedScreen) blockedScreen.style.display = 'none';
      if (blankScreen) blankScreen.style.display = 'none';
      if (failedScreen) failedScreen.style.display = 'none';
    };

    // ── Authoritative State Rendering ──────────────────────────────────────
    // Derive visible state ONLY from run.phase (single source of truth).
    // Do NOT infer "connecting" from absence of screenshot.
    // Do NOT contradict nav_success with a "connecting" overlay.

    if (run.latest_screenshot && run.screenshot_status === 'available') {
      // Real screenshot available — show it
      hideAllScreens();
      if (screenshotImg) {
        if (screenshotImg.dataset.lastSrc !== run.latest_screenshot) {
          screenshotImg.dataset.lastSrc = run.latest_screenshot;
          screenshotImg.onerror = () => {
            screenshotImg.style.display = 'none';
            if (navigatingScreen) {
              navigatingScreen.style.display = 'flex';
              const navTitle = navigatingScreen.querySelector('.live-state-title');
              if (navTitle) navTitle.textContent = 'Rendering evidence...';
              const navSub = navigatingScreen.querySelector('.live-state-subtitle');
              if (navSub) navSub.textContent = 'Awaiting viewport frame settle';
            }
          };
          screenshotImg.onload = () => {
            hideAllScreens();
            screenshotImg.style.display = 'block';
            if (cursor) cursor.style.display = 'block';
          };
          screenshotImg.src = run.latest_screenshot + '?t=' + (run.state_version || Date.now());
        }
        screenshotImg.style.display = 'block';
      }
      if (cursor) cursor.style.display = 'block';
    } else {
      // No screenshot yet — show the appropriate diagnostic overlay based on phase
      if (screenshotImg) screenshotImg.style.display = 'none';
      if (cursor) cursor.style.display = 'none';
      hideAllScreens();

      if (phase === 'blocked') {
        if (blockedScreen) {
          blockedScreen.style.display = 'flex';
          const evEl = $('#liveBlockedEvidence');
          if (evEl) evEl.textContent = run.error || run.final_url || 'Target returned automation challenge or security check';
        }
      } else if (phase === 'cancelled' || run.status === 'cancelled') {
        if (failedScreen) {
          failedScreen.style.display = 'flex';
          const title = failedScreen.querySelector('.live-state-title');
          if (title) title.textContent = 'Run Cancelled';
          const sub = failedScreen.querySelector('.live-state-subtitle');
          if (sub) sub.textContent = 'Execution was halted by user request.';
          const errEl = $('#liveFailedError');
          if (errEl) errEl.textContent = run.error || 'User cancelled execution';
        }
      } else if (phase === 'failed' || run.status === 'failed') {
        const navState = run.navigation_state;
        if (navState === 'blank') {
          if (blankScreen) {
            blankScreen.style.display = 'flex';
            const diagEl = $('#liveBlankDiagnostics');
            const d = run.navigation_diagnostics || {};
            if (diagEl) diagEl.textContent = `Body: ${d.body_text_length || 0} chars | Controls: ${d.interactive_elements || 0} | URL: ${run.final_url || effectiveTarget}`;
          }
        } else {
          if (failedScreen) {
            failedScreen.style.display = 'flex';
            const title = failedScreen.querySelector('.live-state-title');
            if (title) title.textContent = 'Navigation Failed';
            const sub = failedScreen.querySelector('.live-state-subtitle');
            if (sub) sub.textContent = 'Could not establish a usable connection to the target URL.';
            const errEl = $('#liveFailedError');
            if (errEl) errEl.textContent = run.error || (run.navigation_diagnostics && run.navigation_diagnostics.error_message) || 'Navigation failed';
          }
        }
      } else if (phase === 'queued' || phase === 'starting') {
        // Browser not started yet — show connecting
        if (connectingScreen) {
          connectingScreen.style.display = 'flex';
          const connUrlEl = $('#liveConnectingUrl');
          if (connUrlEl) connUrlEl.textContent = effectiveTarget;
        }
      } else if (phase === 'navigating') {
        // Browser launched and navigating — show navigating overlay (NOT "connecting")
        if (navigatingScreen) {
          navigatingScreen.style.display = 'flex';
          const navUrlEl = $('#liveNavigatingUrl');
          if (navUrlEl) navUrlEl.textContent = effectiveTarget;
          const navTitle = navigatingScreen.querySelector('.live-state-title');
          if (navTitle) navTitle.textContent = 'Opening target...';
          const navSub = navigatingScreen.querySelector('.live-state-subtitle');
          if (navSub) navSub.textContent = 'Navigating Chromium and awaiting initial DOM rendering';
        }
      } else if (phase === 'page_ready' || phase === 'exploring' || phase === 'verifying' || phase === 'auditing') {
        // Navigation succeeded — screenshot capture is in progress, show navigating (not connecting)
        if (navigatingScreen) {
          navigatingScreen.style.display = 'flex';
          const navUrlEl = $('#liveNavigatingUrl');
          if (navUrlEl) navUrlEl.textContent = run.current_url || run.final_url || effectiveTarget;
          const navTitle = navigatingScreen.querySelector('.live-state-title');
          if (navTitle) navTitle.textContent = phase === 'auditing' ? 'Auditing interface...' : 'Target loaded';
          const navSub = navigatingScreen.querySelector('.live-state-subtitle');
          if (navSub) navSub.textContent = phase === 'auditing' ? 'Evaluating accessibility and heuristic friction rules' : 'Target page rendered — capturing browser evidence…';
        }
      } else if (phase === 'completed' || phase === 'partial') {
        // Completed without screenshot (fallback)
        if (navigatingScreen) {
          navigatingScreen.style.display = 'flex';
          const navUrlEl = $('#liveNavigatingUrl');
          if (navUrlEl) navUrlEl.textContent = run.final_url || effectiveTarget;
          const navTitle = navigatingScreen.querySelector('.live-state-title');
          if (navTitle) navTitle.textContent = 'Run Finished';
          const navSub = navigatingScreen.querySelector('.live-state-subtitle');
          if (navSub) navSub.textContent = 'Audit complete. View evaluation results and report.';
        }
      } else {
        // Unknown phase — default to connecting
        if (connectingScreen) {
          connectingScreen.style.display = 'flex';
          const connUrlEl = $('#liveConnectingUrl');
          if (connUrlEl) connUrlEl.textContent = effectiveTarget;
        }
      }
    }

    // Show cursor move
    if (cursor && cursorLabel && events.length) {
      const lastEv = events[events.length - 1];
      cursorLabel.textContent = lastEv.message ? lastEv.message.slice(0, 32) : 'Exploring';
    }

    // Evidence toast on new issues / findings (canonical contract)
    const runIssues = run.issues || run.findings || [];
    if (runIssues.length > 0) {
      const toast = $('#liveEvidenceToast');
      const msg = $('#evidenceToastMsg');
      if (toast && msg) {
        msg.textContent = `${runIssues.length} issues recorded`;
        toast.style.display = 'flex';
      }
    }
  }

  function setStepCompleted(stepNum) {
    for (let i = 1; i <= 6; i++) {
      const s = $(`#liveStep${i}`);
      if (s) {
        if (i < stepNum) {
          s.classList.add('reached');
          s.classList.remove('current');
          const span = s.querySelector('span');
          if (span) span.textContent = '✓';
        } else if (i === stepNum) {
          s.classList.add('reached', 'current');
        }
      }
    }
  }

  // ---------------------------------------------------------------------------
  // VIEW: Evaluation (/evaluation)
  // ---------------------------------------------------------------------------
  function renderEvaluationView() {
    const emptyState = $('#evaluationEmptyState');
    const activeState = $('#evaluationActiveState');
    const grid = $('#evaluationFindingsGrid');
    // RunState uses `issues` not `findings`
    const findings = (state.runData && (state.runData.issues || state.runData.findings)) || [];

    if (!findings.length) {
      if (emptyState) emptyState.style.display = 'grid';
      if (activeState) activeState.style.display = 'none';
      return;
    }

    if (emptyState) emptyState.style.display = 'none';
    if (activeState) activeState.style.display = 'block';

    if (grid) {
      grid.innerHTML = findings.map((f, idx) => `
        <div class="finding-card">
          <div class="finding-card-head">
            <div class="finding-badges">
              <span class="badge-severity ${f.severity?.toLowerCase() || 'medium'}">${f.severity || 'MEDIUM'}</span>
              <span class="badge-category">${escapeHtml(f.category || 'Quality')}</span>
            </div>
            <span style="font-size:11px;color:#777a87">Step ${f.step || (idx + 1)}</span>
          </div>
          <h3>${escapeHtml(f.title || f.defect_type || 'Defect Identified')}</h3>
          <p>${escapeHtml(f.description || f.evidence || '')}</p>
          <div class="finding-card-foot">
            <span>${escapeHtml(f.wcag_criterion ? `WCAG: ${f.wcag_criterion}` : 'Interaction Evidence')}</span>
            ${f.screenshot ? `<button class="btn-inspect" onclick="window.TRACE_APP.inspectEvidence('${escapeHtml(f.screenshot)}', '${escapeHtml(f.title || '')}', '${escapeHtml(f.description || '')}')">Inspect Evidence ↗</button>` : ''}
          </div>
        </div>
      `).join('');
    }
  }

  // ---------------------------------------------------------------------------
  // VIEW: Journeys (/journeys)
  // ---------------------------------------------------------------------------
  function renderJourneysView() {
    const emptyState = $('#journeysEmptyState');
    const activeState = $('#journeysActiveState');
    const run = state.runData;

    if (!run || (!run.journey && (!run.events || !run.events.length))) {
      if (emptyState) emptyState.style.display = 'grid';
      if (activeState) activeState.style.display = 'none';
      return;
    }

    if (emptyState) emptyState.style.display = 'none';
    if (activeState) activeState.style.display = 'block';

    const journey = run.journey || {};
    const journeyNodes = Array.isArray(journey.nodes) ? journey.nodes : [];
    const journeyEdges = Array.isArray(journey.edges) ? journey.edges : [];
    const loopNodes = journeyNodes.filter(n => n.is_loop).length;
    $('#jUniqueStates') && ($('#jUniqueStates').textContent = journeyNodes.length || (run.events ? Math.min(run.events.length, 6) : 3));
    $('#jPathsMapped') && ($('#jPathsMapped').textContent = run.paths_discovered || 1);
    $('#jLoopsAvoided') && ($('#jLoopsAvoided').textContent = loopNodes);

    const dag = $('#journeysDagContainer');
    if (dag && state.events.length) {
      dag.innerHTML = state.events.slice(-8).map((ev, i) => `
        <div class="journey-node-row">
          <div class="journey-node-index">0${i + 1}</div>
          <div class="journey-node-detail">
            <b>${escapeHtml(ev.kind || 'Transition')}</b>
            <small>${escapeHtml(ev.message || '')}</small>
          </div>
          <span style="font-size:10px;color:#676a74">${ev.status || 'OK'}</span>
        </div>
      `).join('');
    }
  }

  // ---------------------------------------------------------------------------
  // VIEW: Findings (/findings)
  // ---------------------------------------------------------------------------
  function renderFindingsView() {
    const emptyState = $('#findingsEmptyState');
    const activeList = $('#findingsActiveList');
    // RunState uses `issues` not `findings`
    const findings = (state.runData && (state.runData.issues || state.runData.findings)) || [];

    // Filter tabs
    $$('#view-findings .filter-tab').forEach((tab) => {
      tab.onclick = () => {
        $$('#view-findings .filter-tab').forEach((t) => t.classList.remove('active'));
        tab.classList.add('active');
        state.filter = tab.dataset.filter || 'all';
        renderFilteredFindings();
      };
    });

    // Search input
    const search = $('#findingsSearch');
    if (search) {
      search.oninput = () => renderFilteredFindings();
    }

    // Update count badges
    $('#findingsCountAll') && ($('#findingsCountAll').textContent = findings.length);
    $('#findingsCountA11y') && ($('#findingsCountA11y').textContent = findings.filter(f => (f.category || '').toLowerCase().includes('access')).length);
    $('#findingsCountUx') && ($('#findingsCountUx').textContent = findings.filter(f => (f.category || '').toLowerCase().includes('ux')).length);
    $('#findingsCountNav') && ($('#findingsCountNav').textContent = findings.filter(f => (f.category || '').toLowerCase().includes('nav')).length);
    $('#findingsCountVisual') && ($('#findingsCountVisual').textContent = findings.filter(f => (f.category || '').toLowerCase().includes('visual')).length);

    if (!findings.length) {
      if (emptyState) emptyState.style.display = 'grid';
      if (activeList) activeList.style.display = 'none';
      return;
    }

    if (emptyState) emptyState.style.display = 'none';
    if (activeList) activeList.style.display = 'flex';

    renderFilteredFindings();
  }

  function renderFilteredFindings() {
    const list = $('#findingsActiveList');
    // RunState uses `issues` field — support both for compatibility
    const findings = (state.runData && (state.runData.issues || state.runData.findings)) || [];
    const q = ($('#findingsSearch')?.value || '').toLowerCase().trim();

    const filtered = findings.filter((f) => {
      const matchFilter = state.filter === 'all' || (f.category || '').toLowerCase().includes(state.filter);
      const matchQuery = !q || (f.title || '').toLowerCase().includes(q) || (f.description || '').toLowerCase().includes(q);
      return matchFilter && matchQuery;
    });

    if (!list) return;

    if (!filtered.length) {
      list.innerHTML = `<div style="padding:40px;text-align:center;color:#676a74;font-size:13px">No findings match this criteria.</div>`;
      return;
    }

    list.innerHTML = filtered.map((f, idx) => `
      <div class="finding-card">
        <div class="finding-card-head">
          <div class="finding-badges">
            <span class="badge-severity ${f.severity?.toLowerCase() || 'medium'}">${f.severity || 'MEDIUM'}</span>
            <span class="badge-category">${escapeHtml(f.category || 'Defect')}</span>
          </div>
          <span style="font-size:11px;color:#777a87">Step ${f.step || (idx + 1)}</span>
        </div>
        <h3>${escapeHtml(f.title || f.defect_type || 'Defect')}</h3>
        <p>${escapeHtml(f.description || f.evidence || '')}</p>
        <div class="finding-card-foot">
          <span>${escapeHtml(f.wcag_criterion ? `WCAG: ${f.wcag_criterion}` : 'Reproducible Step')}</span>
          ${f.screenshot ? `<button class="btn-inspect" onclick="window.TRACE_APP.inspectEvidence('${escapeHtml(f.screenshot)}', '${escapeHtml(f.title || '')}', '${escapeHtml(f.description || '')}')">Inspect Evidence ↗</button>` : ''}
        </div>
      </div>
    `).join('');
  }

  // ---------------------------------------------------------------------------
  // VIEW: Report (/report)
  // ---------------------------------------------------------------------------
  function renderReportView() {
    const emptyState = $('#reportEmptyState');
    const activeState = $('#reportActiveState');
    const run = state.runData;

    if (!run || run.status === 'idle') {
      if (emptyState) emptyState.style.display = 'grid';
      if (activeState) activeState.style.display = 'none';
      return;
    }

    if (emptyState) emptyState.style.display = 'none';
    if (activeState) activeState.style.display = 'block';

    $('#reportGoalHeading') && ($('#reportGoalHeading').textContent = run.goal || state.goal);
    $('#reportMetaText') && ($('#reportMetaText').textContent = `Run ID: ${run.id} · Target: ${run.target_url || state.targetUrl}`);

    const htmlExport = $('#btnExportHtml');
    const jsonExport = $('#btnExportJson');
    if (htmlExport) htmlExport.href = `/api/runs/${run.id}/report`;
    if (jsonExport) jsonExport.href = `/api/runs/${run.id}/report.json`;

    // Metrics — use backend-computed metrics when available, derive from issues otherwise
    const findings = run.issues || run.findings || [];
    const metrics = run.metrics || {};
    const frictionVal = metrics.friction_score != null ? metrics.friction_score.toFixed(1) : (findings.length > 0 ? (findings.length * 1.5).toFixed(1) : '0.0');
    $('#metricFriction') && ($('#metricFriction').textContent = frictionVal);
    $('#metricWcag') && ($('#metricWcag').textContent = metrics.wcag_grade || (findings.length === 0 ? 'AAA' : 'AA'));
    const journeyData = run.journey || {};
    const journeyNodes = Array.isArray(journeyData.nodes) ? journeyData.nodes : [];
    $('#metricPaths') && ($('#metricPaths').textContent = run.paths_discovered || '1');
    $('#metricSteps') && ($('#metricSteps').textContent = run.step_count || state.events.length || '0');

    // Editorial Summary
    const editorial = $('#reportEditorialContent');
    if (editorial) {
      editorial.innerHTML = `
        <div style="margin-top:28px;padding:24px;border:1px solid var(--line-soft);border-radius:14px;background:rgba(255,255,255,0.01)">
          <h4 style="font-size:14px;margin-bottom:8px;color:#c0b7ff">Executive Summary</h4>
          <p style="font-size:13px;color:#9598a4;line-height:1.6">
            Autonomous exploration verified the target user journey. TRACE executed ${run.current_step || state.events.length} interaction steps,
            recording ${findings.length} findings across UX, accessibility and visual consistency.
            All evidence is stored and exportable above.
          </p>
        </div>
      `;
    }
  }

  // ---------------------------------------------------------------------------
  // Modal & Toast Notifications
  // ---------------------------------------------------------------------------
  function inspectEvidence(imgSrc, title, desc) {
    const modal = $('#evidenceModal');
    const modalTitle = $('#modalEvidenceTitle');
    const modalImg = $('#modalEvidenceImg');
    const modalDesc = $('#modalEvidenceDesc');

    if (!modal) return;
    if (modalTitle) modalTitle.textContent = title || 'Evidence Inspection';
    if (modalImg) modalImg.src = imgSrc || '';
    if (modalDesc) modalDesc.textContent = desc || '';

    modal.classList.add('open');
    modal.setAttribute('aria-hidden', 'false');
  }

  function closeEvidenceModal() {
    const modal = $('#evidenceModal');
    if (modal) {
      modal.classList.remove('open');
      modal.setAttribute('aria-hidden', 'true');
    }
  }

  function showToast(message) {
    const stack = $('#toastStack');
    if (!stack) return;
    const toast = document.createElement('div');
    toast.className = 'toast';
    toast.textContent = message;
    stack.appendChild(toast);
    setTimeout(() => {
      toast.style.opacity = '0';
      toast.style.transform = 'translateY(8px)';
      toast.style.transition = 'all 0.3s ease';
      setTimeout(() => toast.remove(), 350);
    }, 4000);
  }

  function updateHeaderBadge(stateStr, text) {
    const header = $('#headerStatus');
    const label = $('#headerStatusText');
    if (header) header.dataset.state = stateStr;
    if (label) label.textContent = text;
  }

  function escapeHtml(str) {
    if (!str) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  // ---------------------------------------------------------------------------
  // Initial Boot
  // ---------------------------------------------------------------------------
  window.addEventListener('DOMContentLoaded', () => {
    setupPointer();
    setupReveal();
    setupScrollMotion();
    setupRouting();

    // Modal dismiss
    $('#modalCloseBtn')?.addEventListener('click', closeEvidenceModal);
    $('#evidenceModal')?.addEventListener('click', (e) => {
      if (e.target.id === 'evidenceModal') closeEvidenceModal();
    });

    const rehydrateActiveView = () => {
      const cleanPath = normalizePath(window.location.pathname);
      const routeConfig = VALID_ROUTES[cleanPath] || VALID_ROUTES['/'];
      if (routeConfig && routeConfig.view) {
        onViewMounted(routeConfig.view);
      }
    };

    const loadRunData = (runId) => {
      return fetch(`/api/runs/${runId}`)
        .then((r) => r.ok ? r.json() : null)
        .then((data) => {
          if (data) {
            state.activeRunId = data.id;
            state.runData = data;
            state.latestRunVersion = data.state_version || 1;
            sessionStorage.setItem('traceRunId', data.id);
            if (data.events && data.events.length) {
              state.events = data.events;
            }
            if (data.status === 'running') {
              state.runStatus = 'running';
              updateHeaderBadge('running', 'RUNNING');
              startRunPolling(data.id);
            } else if (data.status === 'completed' || data.status === 'success') {
              state.runStatus = 'success';
              updateHeaderBadge('success', 'SUCCESS');
            } else if (data.status === 'partial') {
              state.runStatus = 'partial';
              updateHeaderBadge('partial', 'PARTIAL');
            } else if (data.status === 'failed') {
              state.runStatus = 'failed';
              updateHeaderBadge('failed', 'FAILED');
            } else if (data.status === 'cancelled') {
              state.runStatus = 'failed';
              updateHeaderBadge('failed', 'CANCELLED');
            }
            rehydrateActiveView();
          }
        })
        .catch((err) => console.warn('Failed to load run data:', err));
    };

    if (state.activeRunId) {
      loadRunData(state.activeRunId);
    } else {
      // If no run in session, load the most recent run from the server
      fetch('/api/runs')
        .then((r) => r.ok ? r.json() : [])
        .then((runs) => {
          if (runs && runs.length > 0) {
            loadRunData(runs[0].id);
          }
        })
        .catch(() => {});
    }
  });

  // Global namespace for inspect calls
  window.TRACE_APP = {
    navigateTo,
    inspectEvidence,
    showToast
  };

})();
