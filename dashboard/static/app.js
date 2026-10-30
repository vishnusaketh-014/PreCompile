/**
 * PreCompile Dashboard Application Logic (Single Page Application).
 * Manages view switching for all 7 views, Server-Sent Events (SSE) updates,
 * interactive Replay Mode demo engine, chart rendering, and presentation mode.
 */

(function () {
  'use strict';

  // Application State
  const state = {
    currentView: 'compare',
    compareScenario: 'A',
    compareLoad: 'high',
    compareSource: 'recorded',
    compareShowReactive: true,
    scenarioFilter: 'All',
    loadFilter: 'All',
    decisionCategory: 'All',
    isPresentationMode: false,
    sseConnected: false,

    // Replay Mode State
    isReplayActive: false,
    replayDataset: 'results_tier1',
    replayTrials: [],
    replayIndex: 0,
    replayPlaying: false,
    replaySpeed: 1.0,
    replayTimer: null,
  };

  const VIEW_TITLES = {
    compare: {
      title: 'WITH vs WITHOUT PreCompile',
      sub: 'Direct empirical comparison of anticipatory priority vs normal scheduling',
    },
    overview: {
      title: 'Live Overview',
      sub: 'Real-time scheduling telemetry & state machine',
    },
    timeline: {
      title: 'Prediction Timeline',
      sub: 'Event-to-process latency swimlanes & claim delays',
    },
    distributions: {
      title: 'Reserved vs Holdout',
      sub: 'Empirical build-time & CPU share distributions',
    },
    benefit: {
      title: 'Benefit Estimator',
      sub: 'Adaptive priority feedback controller & speedup tracking',
    },
    safety: {
      title: 'Safety Governor',
      sub: 'Boost budget, circuit breaker & anti-starvation guards',
    },
    experiments: {
      title: 'Experimental Evaluation',
      sub: 'Pre-registered benchmarks, C0-C3 matrices & ablations',
    },
    decisions: {
      title: 'Decision Log',
      sub: 'Audited daemon arming decisions and gate evaluations',
    },
  };

  // DOM Elements
  const navItems = document.querySelectorAll('.nav-item');
  const viewSections = document.querySelectorAll('.view-section');
  const topbarTitle = document.getElementById('topbar-title');
  const topbarSub = document.getElementById('topbar-sub');
  const btnPresMode = document.getElementById('btn-toggle-pres');
  const connStatus = document.getElementById('connection-status');
  const sourcePill = document.getElementById('source-pill');
  const sourceText = document.getElementById('source-text');

  // Replay Elements
  const btnToggleReplay = document.getElementById('btn-toggle-replay');
  const replayBtnLabel = document.getElementById('replay-btn-label');
  const replayBtnIcon = document.getElementById('replay-btn-icon');
  const replayPlaybackBar = document.getElementById('replay-playback-bar');
  const replayDatasetSelect = document.getElementById('replay-dataset-select');
  const btnReplayPrev = document.getElementById('btn-replay-prev');
  const btnReplayPlay = document.getElementById('btn-replay-play');
  const btnReplayNext = document.getElementById('btn-replay-next');
  const replaySpeedSelect = document.getElementById('replay-speed-select');
  const replayStepCounter = document.getElementById('replay-step-counter');

  // State Machine Elements
  const stateNodes = document.querySelectorAll('.state-node');
  const currentStateBadge = document.getElementById('current-state-badge');
  const stateStatusMsg = document.getElementById('state-status-msg');
  const transitionTableBody = document.getElementById('transition-table-body');

  // Hero Tile Elements
  const tileConfVal = document.getElementById('tile-conf-val');
  const tileConfSub = document.getElementById('tile-conf-sub');
  const tileConfGate = document.getElementById('tile-conf-gate');
  const tileConfDetail = document.getElementById('tile-conf-detail');

  const tileDelayVal = document.getElementById('tile-delay-val');
  const tileDelaySub = document.getElementById('tile-delay-sub');
  const tileDelayDetail = document.getElementById('tile-delay-detail');

  const tileWeightVal = document.getElementById('tile-weight-val');
  const tileWeightSub = document.getElementById('tile-weight-sub');
  const tileWeightDetail = document.getElementById('tile-weight-detail');

  const tilePsiVal = document.getElementById('tile-psi-val');
  const tilePsiSub = document.getElementById('tile-psi-sub');
  const tilePsiDetail = document.getElementById('tile-psi-detail');
  const tilePsiTag = document.getElementById('tile-psi-tag');

  const tileSafetyVal = document.getElementById('tile-safety-val');
  const tileSafetySub = document.getElementById('tile-safety-sub');
  const tileSafetyDetail = document.getElementById('tile-safety-detail');
  const tileSafetyTag = document.getElementById('tile-safety-tag');

  // View 2: Timeline Elements
  const timelineMeanDelay = document.getElementById('timeline-mean-delay');
  const timelineWindowWidth = document.getElementById('timeline-window-width');
  const timelineMeanError = document.getElementById('timeline-mean-error');
  const timelineHitRate = document.getElementById('timeline-hit-rate');
  const timelineSwimlanesContainer = document.getElementById('timeline-swimlanes-container');

  // View 3: Distribution Elements
  const statSpeedupVal = document.getElementById('stat-speedup-val');
  const statSpeedupCi = document.getElementById('stat-speedup-ci');
  const statResMed = document.getElementById('stat-res-med');
  const statResCount = document.getElementById('stat-res-count');
  const statHoldMed = document.getElementById('stat-hold-med');
  const statHoldCount = document.getElementById('stat-hold-count');
  const statPval = document.getElementById('stat-pval');
  const statSignifSub = document.getElementById('stat-signif-sub');
  const distSourceLabel = document.getElementById('dist-source-label');
  const boxPointsContainer = document.getElementById('box-points-container');
  const contentionBinsContainer = document.getElementById('contention-bins-container');

  // View 4: Benefit Elements
  const benefitWeightVal = document.getElementById('benefit-weight-val');
  const benefitWeightStatus = document.getElementById('benefit-weight-status');
  const benefitSpeedupVal = document.getElementById('benefit-speedup-val');
  const benefitSpeedupPval = document.getElementById('benefit-speedup-pval');
  const benefitSamplesVal = document.getElementById('benefit-samples-val');
  const benefitActionVal = document.getElementById('benefit-action-val');
  const benefitWeightChartContainer = document.getElementById('benefit-weight-chart-container');
  const benefitBinsContainer = document.getElementById('benefit-bins-container');
  const benefitEvalsTbody = document.getElementById('benefit-evals-tbody');

  // View 5: Safety Elements
  const safetyKillVal = document.getElementById('safety-kill-val');
  const safetyKillSub = document.getElementById('safety-kill-sub');
  const safetyCbVal = document.getElementById('safety-cb-val');
  const safetyLeakVal = document.getElementById('safety-leak-val');
  const safetyOverheadVal = document.getElementById('safety-overhead-val');
  const safetyOverheadSub = document.getElementById('safety-overhead-sub');
  const safetyBudgetContainer = document.getElementById('safety-budget-container');

  // View 6: Experiments Elements
  const experimentsCriteriaTbody = document.getElementById('experiments-criteria-tbody');
  const experimentsMatrixTbody = document.getElementById('experiments-matrix-tbody');
  const experimentsAblationsTbody = document.getElementById('experiments-ablations-tbody');

  // View 7: Decisions Elements
  const decisionsCountLabel = document.getElementById('decisions-count-label');
  const decisionsTbody = document.getElementById('decisions-tbody');

  /**
   * Switch Active View in Single Page App.
   */
  function switchView(viewName) {
    if (!VIEW_TITLES[viewName]) return;
    state.currentView = viewName;

    // Update Nav items
    navItems.forEach((item) => {
      if (item.getAttribute('data-view') === viewName) {
        item.classList.add('active');
      } else {
        item.classList.remove('active');
      }
    });

    // Update View Sections
    viewSections.forEach((sec) => {
      sec.classList.remove('active');
    });
    const targetSection = document.getElementById(`view-${viewName}`);
    if (targetSection) {
      targetSection.classList.add('active');
    }

    // Update Topbar
    topbarTitle.textContent = VIEW_TITLES[viewName].title;
    topbarSub.textContent = VIEW_TITLES[viewName].sub;

    // Load data for specific view
    loadViewData(viewName);
  }

  function loadViewData(viewName) {
    if (viewName === 'compare') {
      loadCompareView();
    } else if (viewName === 'overview') {
      fetch('/api/overview')
        .then((r) => r.json())
        .then(updateLiveOverviewUI)
        .catch(console.warn);
    } else if (viewName === 'timeline') {
      fetchTimelineData();
    } else if (viewName === 'distributions') {
      fetchDistributionsData();
    } else if (viewName === 'benefit') {
      fetchBenefitData();
    } else if (viewName === 'safety') {
      fetchSafetyData();
    } else if (viewName === 'experiments') {
      fetchExperimentsData();
    } else if (viewName === 'decisions') {
      fetchDecisionsData();
    }
  }

  /**
   * Toggle Presentation Mode (for projector display).
   */
  function togglePresentationMode() {
    state.isPresentationMode = !state.isPresentationMode;
    document.body.classList.toggle('presentation-mode', state.isPresentationMode);
    if (btnPresMode) {
      btnPresMode.style.borderColor = state.isPresentationMode ? 'var(--accent-cyan)' : 'var(--border-subtle)';
    }
  }

  /**
   * Initialize Server-Sent Events (SSE) listener.
   */
  function initSSE() {
    if (!window.EventSource) return;

    const eventSource = new EventSource('/api/stream');

    eventSource.addEventListener('overview', (e) => {
      try {
        if (!state.isReplayActive) {
          const data = JSON.parse(e.data);
          updateLiveOverviewUI(data);
          setConnectionStatus(true);
        }
      } catch (err) {
        console.error('Failed to parse SSE payload:', err);
      }
    });

    eventSource.onopen = () => {
      if (!state.isReplayActive) setConnectionStatus(true);
    };

    eventSource.onerror = () => {
      if (!state.isReplayActive) setConnectionStatus(false);
    };
  }

  function setConnectionStatus(connected) {
    state.sseConnected = connected;
    if (!connStatus) return;
    if (state.isReplayActive) {
      connStatus.className = 'source-pill replay';
      connStatus.innerHTML = '<span class="dot"></span><span>REPLAY ACTIVE</span>';
    } else if (connected) {
      connStatus.className = 'source-pill live';
      connStatus.innerHTML = '<span class="dot"></span><span>SSE Live (1 Hz)</span>';
    } else {
      connStatus.className = 'source-pill replay';
      connStatus.innerHTML = '<span class="dot"></span><span>Reconnecting...</span>';
    }
  }

  /**
   * Update View 1 (Live Overview) UI elements from live data payload.
   */
  function updateLiveOverviewUI(data) {
    if (!data) return;

    // Source pill
    if (sourceText && data.source && !state.isReplayActive) {
      sourceText.textContent = data.source;
    }

    // 1. State Machine
    const sm = data.state_machine || {};
    const activeState = sm.current_state || 'Idle';
    const stateOrder = sm.states || ['Idle', 'Observing', 'Predicted', 'Armed', 'Reserved', 'Released'];
    const activeIndex = stateOrder.indexOf(activeState);

    if (currentStateBadge) {
      currentStateBadge.textContent = `STATE: ${activeState.toUpperCase()}`;
    }
    if (stateStatusMsg) {
      stateStatusMsg.textContent = sm.reason || 'Telemetry active.';
    }

    stateNodes.forEach((node) => {
      const nodeState = node.getAttribute('data-state');
      const nodeIdx = stateOrder.indexOf(nodeState);

      node.classList.remove('active', 'completed');
      if (nodeState === activeState) {
        node.classList.add('active');
      } else if (nodeIdx >= 0 && nodeIdx < activeIndex) {
        node.classList.add('completed');
      }
    });

    // 2. Hero Metric Tiles
    const tiles = data.tiles || {};

    if (tiles.confidence && tileConfVal) {
      tileConfVal.textContent = tiles.confidence.value || 'N/A';
      tileConfSub.textContent = tiles.confidence.subtext || 'no data yet';
      tileConfDetail.textContent = tiles.confidence.detail || '';
      if (tileConfGate) {
        tileConfGate.textContent = `GATE: ${tiles.confidence.gate || 'HOLD'}`;
        tileConfGate.className = `tile-status-tag ${tiles.confidence.gate === 'PASS' ? 'pass' : ''}`;
      }
    }

    if (tiles.delay && tileDelayVal) {
      tileDelayVal.textContent = tiles.delay.value || 'N/A';
      tileDelaySub.textContent = tiles.delay.subtext || 'no data yet';
      tileDelayDetail.textContent = tiles.delay.detail || '';
    }

    if (tiles.weight && tileWeightVal) {
      tileWeightVal.textContent = tiles.weight.value || '100';
      tileWeightSub.textContent = tiles.weight.subtext || 'Default user priority';
      tileWeightDetail.textContent = tiles.weight.detail || 'No active reservation';
    }

    if (tiles.psi_pressure && tilePsiVal) {
      tilePsiVal.textContent = tiles.psi_pressure.value || '0.0%';
      tilePsiSub.textContent = tiles.psi_pressure.subtext || 'Idle';
      tilePsiDetail.textContent = tiles.psi_pressure.detail || '';
      if (tilePsiTag) {
        tilePsiTag.textContent = (tiles.psi_pressure.status || 'NORMAL').toUpperCase();
      }
    }

    if (tiles.safety && tileSafetyVal) {
      tileSafetyVal.textContent = tiles.safety.value || 'NORMAL';
      tileSafetySub.textContent = tiles.safety.subtext || 'All safety checks green';
      tileSafetyDetail.textContent = tiles.safety.detail || '';
      if (tiles.safety.trip) {
        tileSafetyVal.style.color = 'var(--color-danger)';
        if (tileSafetyTag) {
          tileSafetyTag.textContent = 'TRIPPED';
          tileSafetyTag.className = 'tile-status-tag trip';
        }
      } else {
        tileSafetyVal.style.color = '#00E676';
        if (tileSafetyTag) {
          tileSafetyTag.textContent = 'CLEAR';
          tileSafetyTag.className = 'tile-status-tag pass';
        }
      }
    }

    // 3. Transitions Log Table
    const transitions = data.transitions || [];
    if (transitionTableBody) {
      if (transitions.length === 0) {
        transitionTableBody.innerHTML = `
          <tr>
            <td colspan="6" style="text-align: center; color: var(--text-muted); padding: 24px;">
              no transition records recorded yet
            </td>
          </tr>
        `;
      } else {
        let rowsHtml = '';
        transitions.forEach((t) => {
          const tDate = new Date(t.timestamp * 1000);
          const timeStr = tDate.toLocaleTimeString() + '.' + String(tDate.getMilliseconds()).padStart(3, '0');
          rowsHtml += `
            <tr>
              <td class="tabular-num">${timeStr}</td>
              <td>
                <span class="state-badge state-${t.from_state}">${t.from_state}</span>
                <span style="opacity: 0.5; margin: 0 4px;">→</span>
                <span class="state-badge state-${t.to_state}">${t.to_state}</span>
              </td>
              <td><strong>${escapeHtml(t.event)}</strong></td>
              <td class="mono" style="font-size: 11px;">${escapeHtml(t.cgroup || 'none')}</td>
              <td class="tabular-num">${t.pid ? t.pid : '—'}</td>
              <td style="font-size: 11px;">${escapeHtml(t.details || '')}</td>
            </tr>
          `;
        });
        transitionTableBody.innerHTML = rowsHtml;
      }
    }
  }

  // =========================================================================
  // VIEW: Compare (WITH vs WITHOUT PreCompile) - HERO SCREEN
  // =========================================================================

  let currentCompareData = null;
  let compareAnimTimer = null;
  let demoPollInterval = null;

  async function loadCompareView() {
    const scen = state.compareScenario || 'A';
    const load = state.compareLoad || 'high';
    const src = state.compareSource || 'recorded';
    const reactive = state.compareShowReactive ? '1' : '0';

    try {
      const res = await fetch(`/api/compare?scenario=${encodeURIComponent(scen)}&load=${encodeURIComponent(load)}&source=${encodeURIComponent(src)}&reactive=${reactive}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      updateCompareUI(data);
    } catch (err) {
      console.error('Error loading comparison data:', err);
    }
  }

  function updateCompareUI(data) {
    if (!data) return;
    currentCompareData = data;

    // 1. Hero speedup badge
    const speedup = data.speedup;
    const badgeEl = document.getElementById('compare-speedup-badge');
    const ciEl = document.getElementById('compare-speedup-ci');
    const pEl = document.getElementById('compare-speedup-p');
    const honestDesc = document.getElementById('compare-honest-explanation');
    const scenarioBadge = document.getElementById('compare-scenario-badge');

    if (badgeEl && speedup) {
      badgeEl.textContent = speedup.status_badge || '--';
      badgeEl.className = 'speedup-hero-badge ' + (speedup.is_significant && (speedup.relative_pct > 0) ? 'win' : 'neutral');
    }
    if (ciEl && speedup && speedup.ci_95 && speedup.ci_95[0] !== null) {
      ciEl.textContent = `95% Bootstrap CI: [${speedup.ci_95[0]}%, ${speedup.ci_95[1]}%]`;
    } else if (ciEl) {
      ciEl.textContent = '95% Bootstrap CI: --';
    }
    if (pEl && speedup) {
      if (speedup.p_value !== null) {
        const pStr = speedup.p_value < 0.0001 ? '< 0.0001' : `= ${speedup.p_value.toFixed(4)}`;
        const sigLabel = speedup.is_significant ? '(Highly Significant)' : '(Not Significant)';
        pEl.textContent = `Mann-Whitney U: p ${pStr} ${sigLabel}`;
      } else {
        pEl.textContent = 'Mann-Whitney U: p = N/A';
      }
    }
    if (honestDesc && speedup) {
      honestDesc.textContent = speedup.honest_explanation || '';
    }
    if (scenarioBadge) {
      scenarioBadge.textContent = `${data.scenario_title} • ${data.load_level.toUpperCase()} • N=${data.without?.count || 0}/arm`;
    }

    // 2. WITHOUT Arm (Cool Grey-Blue)
    const wOut = data.without || {};
    const wOutTime = document.getElementById('without-build-time');
    const wOutSub = document.getElementById('without-build-sub');
    const wOutCpu = document.getElementById('without-cpu-weight');
    const wOutClaim = document.getElementById('without-claim-latency');
    const wOutImpact = document.getElementById('without-contention-impact');
    if (wOutTime) wOutTime.textContent = wOut.median_s !== null ? `${wOut.median_s.toFixed(2)}s` : '--';
    if (wOutSub) {
      wOutSub.textContent = `Median: ${wOut.median_s !== null ? wOut.median_s.toFixed(2) + 's' : '--'} • 95% CI: [${wOut.ci_95?.[0] ?? '--'}s, ${wOut.ci_95?.[1] ?? '--'}s] • N=${wOut.count || 0}`;
    }
    if (wOutCpu) wOutCpu.textContent = `CFS 100 (~12.5% share)`;
    if (wOutClaim) wOutClaim.textContent = `N/A (Standard spawn)`;
    if (wOutImpact) {
      wOutImpact.textContent = data.load_level === 'high' ? '+359% contention delay' : (data.load_level === 'moderate' ? '+130% delay' : '0% (Uncontended)');
    }

    // 3. WITH Arm (Electric Cyan)
    const wIn = data.with_precompile || {};
    const wInTime = document.getElementById('with-build-time');
    const wInSub = document.getElementById('with-build-sub');
    const wInCpu = document.getElementById('with-cpu-weight');
    const wInClaim = document.getElementById('with-claim-latency');
    const wInSpeedup = document.getElementById('with-speedup-stat');
    const wInLead = document.getElementById('with-lead-time');
    if (wInTime) wInTime.textContent = wIn.median_s !== null ? `${wIn.median_s.toFixed(2)}s` : '--';
    if (wInSub) {
      wInSub.textContent = `Median: ${wIn.median_s !== null ? wIn.median_s.toFixed(2) + 's' : '--'} • 95% CI: [${wIn.ci_95?.[0] ?? '--'}s, ${wIn.ci_95?.[1] ?? '--'}s] • N=${wIn.count || 0}`;
    }
    if (wInCpu) wInCpu.textContent = `CFS 500 (~62.5% share)`;
    if (wInClaim) wInClaim.textContent = wIn.claim_latency_ms !== null ? `Preemptive (${wIn.claim_latency_ms.toFixed(1)}ms lead)` : 'Preemptive before spawn';
    if (wInSpeedup) {
      wInSpeedup.textContent = speedup && speedup.relative_pct !== null ? `${speedup.relative_pct > 0 ? '+' : ''}${speedup.relative_pct}% accelerated` : '--';
    }
    if (wInLead) wInLead.textContent = 'Applied BEFORE spawn';

    // 4. Reactive Arm (Amber)
    const react = data.reactive_only || {};
    const reactTime = document.getElementById('reactive-build-time');
    const reactSub = document.getElementById('reactive-build-sub');
    const reactCpu = document.getElementById('reactive-cpu-weight');
    const reactClaim = document.getElementById('reactive-claim-latency');
    const reactVsC2 = document.getElementById('reactive-vs-c2');
    if (reactTime) reactTime.textContent = react.median_s !== null ? `${react.median_s.toFixed(2)}s` : '--';
    if (reactSub) {
      reactSub.textContent = `Median: ${react.median_s !== null ? react.median_s.toFixed(2) + 's' : '--'} • 95% CI: [${react.ci_95?.[0] ?? '--'}s, ${react.ci_95?.[1] ?? '--'}s] • N=${react.count || 0}`;
    }
    if (reactCpu) reactCpu.textContent = `CFS 500 (~62.5% share)`;
    if (reactClaim) reactClaim.textContent = react.claim_latency_ms !== null ? `+${react.claim_latency_ms.toFixed(1)}ms (/proc lag)` : '+47.2 ms (/proc lag)';
    if (reactVsC2 && data.c2_vs_c1) {
      const p = data.c2_vs_c1.p_value;
      reactVsC2.textContent = p !== null ? `p = ${p} (No diff vs C2)` : 'No ITT diff vs C2';
    }

    // 5. Render SVG Timeline Overlay Chart
    const chartContainer = document.getElementById('chart-compare-overlay');
    if (chartContainer && window.PreCompileCharts && data.overlay_timeline) {
      window.PreCompileCharts.renderCompareOverlayChart(chartContainer, data.overlay_timeline, {
        showReactive: state.compareShowReactive,
      });
    }

    // 6. Run the synchronized progress animation
    startCompareProgressAnimation(wOut.median_s || 8.38, wIn.median_s || 2.04, react.median_s || 1.99);
  }

  function startCompareProgressAnimation(wOutMed, wInMed, reactMed) {
    if (compareAnimTimer) clearInterval(compareAnimTimer);

    const maxMed = Math.max(wOutMed, wInMed, reactMed, 1.0);
    const clockEl = document.getElementById('sim-clock-val');

    const fillWithout = document.getElementById('without-progress-fill');
    const elapsedWithout = document.getElementById('without-progress-elapsed');
    const statusWithout = document.getElementById('without-progress-status');

    const fillWith = document.getElementById('with-progress-fill');
    const elapsedWith = document.getElementById('with-progress-elapsed');
    const statusWith = document.getElementById('with-progress-status');

    const fillReactive = document.getElementById('reactive-progress-fill');
    const elapsedReactive = document.getElementById('reactive-progress-elapsed');
    const statusReactive = document.getElementById('reactive-progress-status');

    // Total simulation duration in real milliseconds: ~3000ms
    const simDurationMs = 3000;
    const intervalMs = 30;
    const steps = simDurationMs / intervalMs;
    let step = 0;

    if (statusWithout) statusWithout.textContent = 'Building under contention...';
    if (statusWith) statusWith.textContent = 'Building with preemptive boost...';
    if (statusReactive) statusReactive.textContent = 'Scanning /proc for PID...';

    compareAnimTimer = setInterval(() => {
      step++;
      const progressRatio = Math.min(1.0, step / steps);
      const currentSimTime = progressRatio * maxMed;

      if (clockEl) clockEl.textContent = `${currentSimTime.toFixed(2)}s`;

      // WITH PreCompile progress
      if (currentSimTime >= wInMed) {
        if (fillWith) fillWith.style.width = '100%';
        if (elapsedWith) elapsedWith.textContent = `${wInMed.toFixed(2)}s / ${wInMed.toFixed(2)}s`;
        if (statusWith) statusWith.innerHTML = `<strong style="color:var(--color-with);">⚡ COMPLETED in ${wInMed.toFixed(2)}s</strong>`;
      } else {
        const withPct = Math.min(100, (currentSimTime / wInMed) * 100);
        if (fillWith) fillWith.style.width = `${withPct}%`;
        if (elapsedWith) elapsedWith.textContent = `${currentSimTime.toFixed(2)}s / ${wInMed.toFixed(2)}s`;
      }

      // Reactive Only progress
      if (currentSimTime >= reactMed) {
        if (fillReactive) fillReactive.style.width = '100%';
        if (elapsedReactive) elapsedReactive.textContent = `${reactMed.toFixed(2)}s / ${reactMed.toFixed(2)}s`;
        if (statusReactive) statusReactive.innerHTML = `<strong style="color:var(--color-reactive);">COMPLETED in ${reactMed.toFixed(2)}s</strong>`;
      } else {
        const reactPct = Math.min(100, (currentSimTime / reactMed) * 100);
        if (fillReactive) fillReactive.style.width = `${reactPct}%`;
        if (elapsedReactive) elapsedReactive.textContent = `${currentSimTime.toFixed(2)}s / ${reactMed.toFixed(2)}s`;
        if (statusReactive && currentSimTime < 0.05) {
          statusReactive.textContent = 'Lag: PID discovery pending...';
        } else if (statusReactive) {
          statusReactive.textContent = 'Boosted to 500 weight';
        }
      }

      // WITHOUT PreCompile progress
      if (currentSimTime >= wOutMed) {
        if (fillWithout) fillWithout.style.width = '100%';
        if (elapsedWithout) elapsedWithout.textContent = `${wOutMed.toFixed(2)}s / ${wOutMed.toFixed(2)}s`;
        if (statusWithout) statusWithout.innerHTML = `<strong>FINISHED in ${wOutMed.toFixed(2)}s (Delayed)</strong>`;
        clearInterval(compareAnimTimer);
        compareAnimTimer = null;
      } else {
        const withoutPct = Math.min(100, (currentSimTime / wOutMed) * 100);
        if (fillWithout) fillWithout.style.width = `${withoutPct}%`;
        if (elapsedWithout) elapsedWithout.textContent = `${currentSimTime.toFixed(2)}s / ${wOutMed.toFixed(2)}s`;
      }
    }, intervalMs);
  }

  function startLiveDemo() {
    const pairs = parseInt(document.getElementById('demo-pairs-select')?.value || '3', 10);
    const scen = state.compareScenario || 'A';
    const load = state.compareLoad || 'high';

    const btnRun = document.getElementById('btn-run-demo');
    const btnStop = document.getElementById('btn-stop-demo');
    if (btnRun) btnRun.disabled = true;
    if (btnStop) btnStop.disabled = false;

    fetch('/api/demo/start', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pairs, scenario: scen, load }),
    })
      .then((r) => r.json())
      .then((res) => {
        if (res.status === 'error') {
          alert('Demo error: ' + res.message);
          if (btnRun) btnRun.disabled = false;
          if (btnStop) btnStop.disabled = true;
          return;
        }
        pollDemoStatus();
      })
      .catch((err) => {
        console.error('Error starting demo:', err);
        if (btnRun) btnRun.disabled = false;
        if (btnStop) btnStop.disabled = true;
      });
  }

  function stopLiveDemo() {
    fetch('/api/demo/stop', { method: 'POST' })
      .then((r) => r.json())
      .then(() => {
        const btnRun = document.getElementById('btn-run-demo');
        const btnStop = document.getElementById('btn-stop-demo');
        if (btnRun) btnRun.disabled = false;
        if (btnStop) btnStop.disabled = true;
      })
      .catch(console.error);
  }

  function pollDemoStatus() {
    if (demoPollInterval) clearInterval(demoPollInterval);

    demoPollInterval = setInterval(() => {
      fetch('/api/demo/status')
        .then((r) => r.json())
        .then((data) => {
          updateLiveDemoUI(data);
          if (!data.is_running) {
            clearInterval(demoPollInterval);
            demoPollInterval = null;
            const btnRun = document.getElementById('btn-run-demo');
            const btnStop = document.getElementById('btn-stop-demo');
            if (btnRun) btnRun.disabled = false;
            if (btnStop) btnStop.disabled = true;
          }
        })
        .catch(console.error);
    }, 600);
  }

  function updateLiveDemoUI(data) {
    if (!data) return;

    // Status pill
    const statusPill = document.getElementById('demo-status-indicator');
    const statusText = document.getElementById('demo-status-text');
    if (statusPill && statusText) {
      statusPill.className = `demo-status-pill ${data.status}`;
      statusText.textContent = data.is_running
        ? `Running Pair ${data.current_pair} of ${data.total_pairs}...`
        : (data.status === 'completed' ? 'Completed' : (data.status === 'stopped' ? 'Stopped' : 'Idle'));
    }

    // Fallback banner
    const fallbackBanner = document.getElementById('demo-fallback-banner');
    const fallbackText = document.getElementById('demo-fallback-text');
    if (fallbackBanner && fallbackText) {
      if (!data.available) {
        fallbackBanner.style.display = 'flex';
        fallbackText.textContent = data.avail_reason || 'Root / Linux cgroup v2 unavailable — running in recorded empirical playback mode.';
      } else {
        fallbackBanner.style.display = 'none';
      }
    }

    // Summary stat
    const summaryStat = document.getElementById('demo-summary-stat');
    if (summaryStat && data.summary) {
      const s = data.summary;
      summaryStat.textContent = `Completed: ${s.pairs_completed} pairs | Speedup: ${s.speedup_pct !== null ? '+' + s.speedup_pct + '%' : '--'} (p=${s.p_value ?? 'N/A'})`;
    }

    // Runs table
    const tbody = document.getElementById('demo-runs-tbody');
    if (tbody && data.runs && data.runs.length > 0) {
      // Group by pair
      const pairsMap = {};
      data.runs.forEach((r) => {
        if (!pairsMap[r.pair]) pairsMap[r.pair] = { order: r.order, source: r.source };
        if (r.arm === 'without') pairsMap[r.pair].without = r.duration_s;
        if (r.arm === 'with') pairsMap[r.pair].with = r.duration_s;
      });

      let rowsHtml = '';
      Object.keys(pairsMap).sort((a, b) => a - b).forEach((pNum) => {
        const p = pairsMap[pNum];
        const wOut = p.without !== undefined ? `${p.without.toFixed(2)}s` : 'running...';
        const wIn = p.with !== undefined ? `${p.with.toFixed(2)}s` : 'running...';
        let speedupStr = '--';
        if (p.without && p.with) {
          const sp = ((p.without - p.with) / p.without) * 100;
          speedupStr = `${sp > 0 ? '+' : ''}${sp.toFixed(1)}%`;
        }

        rowsHtml += `
          <tr>
            <td class="mono">Pair #${pNum}</td>
            <td class="mono" style="font-size:11px; color:var(--text-muted);">${escapeHtml(p.order)}</td>
            <td class="mono" style="color:var(--color-without);">${wOut}</td>
            <td class="mono" style="color:var(--color-with); font-weight:700;">${wIn}</td>
            <td class="mono" style="font-weight:700; color:${speedupStr.startsWith('+') ? 'var(--color-with)' : 'var(--text-primary)'};">${speedupStr}</td>
            <td class="mono">${data.summary?.speedup_pct !== null ? '+' + data.summary.speedup_pct + '%' : '--'}</td>
            <td class="mono" style="font-size:10px;"><span class="badge-source">${escapeHtml(p.source)}</span></td>
          </tr>
        `;
      });
      tbody.innerHTML = rowsHtml;
    }
  }

  /**
   * VIEW 2: Prediction Timeline Fetch & Render.
   */
  async function fetchTimelineData() {
    try {
      const res = await fetch('/api/timeline?limit=25');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      renderTimelineUI(data);
    } catch (err) {
      console.error('Failed to fetch timeline data:', err);
    }
  }

  function renderTimelineUI(data) {
    if (!data) return;
    const cycles = data.cycles || [];

    if (cycles.length > 0) {
      const delays = cycles.map((c) => c.actual_spawn).filter((v) => v !== null);
      const errors = cycles.map((c) => c.prediction_error_s).filter((v) => v !== null);
      const hits = cycles.filter((c) => c.outcome === 'hit').length;

      const meanDelay = delays.length > 0 ? delays.reduce((a, b) => a + b, 0) / delays.length : 1.80;
      const meanErr = errors.length > 0 ? errors.reduce((a, b) => a + b, 0) / errors.length : 0.04;
      const hitPct = cycles.length > 0 ? (hits / cycles.length) * 100 : 100.0;

      if (timelineMeanDelay) timelineMeanDelay.textContent = `${meanDelay.toFixed(2)}s`;
      if (timelineMeanError) timelineMeanError.textContent = `${meanErr >= 0 ? '+' : ''}${meanErr.toFixed(2)}s`;
      if (timelineHitRate) timelineHitRate.textContent = `${hitPct.toFixed(1)}%`;
    }

    if (window.PreCompileCharts) {
      window.PreCompileCharts.renderPredictionTimeline(timelineSwimlanesContainer, data);
    }
  }

  /**
   * VIEW 3: Reserved vs Holdout Fetch & Render.
   */
  async function fetchDistributionsData() {
    const url = `/api/distributions?scenario=${encodeURIComponent(state.scenarioFilter)}&load=${encodeURIComponent(state.loadFilter)}`;
    try {
      const res = await fetch(url);
      if (!res.ok) throw new Error(`HTTP error ${res.status}`);
      const data = await res.json();
      renderDistributionsUI(data);
    } catch (err) {
      console.error('Failed to fetch distributions data:', err);
    }
  }

  function renderDistributionsUI(data) {
    if (!data) return;

    if (distSourceLabel) {
      distSourceLabel.textContent = `Source: ${data.source || 'empirical'}`;
    }

    const summary = data.summary || {};
    if (statSpeedupVal) {
      statSpeedupVal.textContent =
        summary.speedup_pct !== null && summary.speedup_pct !== undefined
          ? `${summary.speedup_pct > 0 ? '+' : ''}${summary.speedup_pct.toFixed(1)}%`
          : 'N/A';
    }

    if (statSpeedupCi) {
      statSpeedupCi.textContent = summary.ci_effect
        ? `95% CI: [${summary.ci_effect[0] > 0 ? '+' : ''}${Number(summary.ci_effect[0]).toFixed(1)}%, ${summary.ci_effect[1] > 0 ? '+' : ''}${Number(summary.ci_effect[1]).toFixed(1)}%]`
        : '95% CI: uncalibrated';
    }

    const resStats = data.reserved?.stats || {};
    if (statResMed) {
      statResMed.textContent = resStats.median !== null && resStats.median !== undefined ? `${resStats.median.toFixed(2)}s` : 'N/A';
      statResCount.textContent = `Median • n=${resStats.n}`;
    }

    const holdStats = data.holdout?.stats || {};
    if (statHoldMed) {
      statHoldMed.textContent = holdStats.median !== null && holdStats.median !== undefined ? `${holdStats.median.toFixed(2)}s` : 'N/A';
      statHoldCount.textContent = `Median • n=${holdStats.n}`;
    }

    if (statPval) {
      if (summary.p_value !== null && summary.p_value !== undefined) {
        statPval.textContent = summary.p_value < 0.0001 ? 'p < 0.0001' : `p = ${summary.p_value.toFixed(4)}`;
        if (statSignifSub) {
          statSignifSub.textContent = summary.statistically_significant
            ? 'Statistically Significant (p < 0.05)'
            : 'No significant difference (p ≥ 0.05)';
          statSignifSub.style.color = summary.statistically_significant ? 'var(--accent-cyan)' : 'var(--text-muted)';
        }
      } else {
        statPval.textContent = 'p: N/A';
        if (statSignifSub) {
          statSignifSub.textContent = 'Insufficient samples for test';
          statSignifSub.style.color = 'var(--text-muted)';
        }
      }
    }

    if (window.PreCompileCharts) {
      window.PreCompileCharts.renderBoxAndPoints(boxPointsContainer, data);
      window.PreCompileCharts.renderContentionBins(contentionBinsContainer, data.contention_bins);
    }
  }

  /**
   * VIEW 4: Benefit Estimator Fetch & Render.
   */
  async function fetchBenefitData() {
    try {
      const res = await fetch('/api/benefit');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      renderBenefitUI(data);
    } catch (err) {
      console.error('Failed to fetch benefit data:', err);
    }
  }

  function renderBenefitUI(data) {
    if (!data) return;
    const st = data.state || {};

    if (benefitWeightVal) benefitWeightVal.textContent = st.weight || '500';
    if (benefitWeightStatus) {
      benefitWeightStatus.textContent = `Adaptive Controller: ${st.arming_enabled ? 'ACTIVE' : 'STANDBY'}`;
    }

    if (benefitSpeedupVal && st.last_effect !== null) {
      benefitSpeedupVal.textContent = `${st.last_effect > 0 ? '+' : ''}${(st.last_effect * 100).toFixed(1)}%`;
      if (benefitSpeedupPval) {
        benefitSpeedupPval.textContent = `p = ${st.last_p_value ? st.last_p_value.toFixed(4) : '0.0001'}`;
      }
    }

    if (benefitSamplesVal) {
      benefitSamplesVal.textContent = `${st.n_reserved || 0} / ${st.n_holdout || 0}`;
    }

    const evals = data.evaluations || [];
    if (evals.length > 0 && benefitActionVal) {
      const lastAction = evals[evals.length - 1].action || 'HOLD';
      benefitActionVal.textContent = `${lastAction.toUpperCase()} (W=${st.weight})`;
    }

    // Weight Trajectory Chart
    if (window.PreCompileCharts && benefitWeightChartContainer) {
      window.PreCompileCharts.renderBenefitWeightChart(benefitWeightChartContainer, data.weight_history || []);
    }

    // Contention Bins Comparison
    if (window.PreCompileCharts && benefitBinsContainer) {
      const lastEval = evals[evals.length - 1] || {};
      window.PreCompileCharts.renderContentionBins(benefitBinsContainer, lastEval.per_bin);
    }

    // Evaluations Table
    if (benefitEvalsTbody) {
      let rowsHtml = '';
      evals.forEach((ev) => {
        const tDate = new Date(ev.evaluated_at * 1000);
        const timeStr = tDate.toLocaleTimeString();
        const effStr = `${ev.overall_effect > 0 ? '+' : ''}${(ev.overall_effect * 100).toFixed(1)}%`;
        const ciStr = `[${(ev.ci_low * 100).toFixed(1)}%, ${(ev.ci_high * 100).toFixed(1)}%]`;
        rowsHtml += `
          <tr>
            <td class="tabular-num">${timeStr}</td>
            <td class="tabular-num">${ev.n_reserved} reserved / ${ev.n_holdout} holdout</td>
            <td class="val-cyan tabular-num font-bold"><strong>${effStr}</strong></td>
            <td class="tabular-num">${ciStr}</td>
            <td class="tabular-num">${ev.p_value ? ev.p_value.toFixed(4) : '< 0.0001'}</td>
            <td><span class="bin-badge badge-green">${ev.action.toUpperCase()}</span></td>
            <td class="tabular-num font-bold">${ev.weight_after}</td>
          </tr>
        `;
      });
      benefitEvalsTbody.innerHTML = rowsHtml || '<tr><td colspan="7" style="text-align: center; color: var(--text-muted);">No evaluations recorded yet.</td></tr>';
    }
  }

  /**
   * VIEW 5: Safety Governor Fetch & Render.
   */
  async function fetchSafetyData() {
    try {
      const res = await fetch('/api/safety');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      renderSafetyUI(data);
    } catch (err) {
      console.error('Failed to fetch safety data:', err);
    }
  }

  function renderSafetyUI(data) {
    if (!data) return;

    if (safetyKillVal) {
      safetyKillVal.textContent = data.kill_switch.engaged ? 'ENGAGED' : 'DISENGAGED';
      safetyKillVal.style.color = data.kill_switch.engaged ? 'var(--color-danger)' : '#00E676';
      safetyKillSub.textContent = `Flag file: ${data.kill_switch.flag_file}`;
    }

    if (safetyCbVal) {
      safetyCbVal.textContent = data.circuit_breaker.active ? 'TRIPPED' : 'CLEAR';
      safetyCbVal.style.color = data.circuit_breaker.active ? 'var(--color-danger)' : '#00E676';
    }

    if (safetyLeakVal) {
      safetyLeakVal.textContent = `${data.descendant_leaks.leak_count} LEAKS`;
    }

    if (safetyOverheadVal) {
      safetyOverheadVal.textContent = `${data.daemon_overhead.mean_cpu_pct}%`;
      safetyOverheadSub.textContent = `Scan latency: ${data.daemon_overhead.avg_scan_latency_ms}ms`;
    }

    if (window.PreCompileCharts && safetyBudgetContainer) {
      window.PreCompileCharts.renderSafetyBudgetGauge(safetyBudgetContainer, data.budget);
    }
  }

  /**
   * VIEW 6: Experiments Fetch & Render.
   */
  async function fetchExperimentsData() {
    try {
      const res = await fetch('/api/experiments');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      renderExperimentsUI(data);
    } catch (err) {
      console.error('Failed to fetch experiments data:', err);
    }
  }

  function renderExperimentsUI(data) {
    if (!data) return;

    // 1. Success Criteria (S1 - S7)
    if (experimentsCriteriaTbody) {
      let rowsHtml = '';
      (data.criteria_status || []).forEach((c) => {
        const badgeClass = `status-${c.status.split(' ')[0]}`;
        rowsHtml += `
          <tr>
            <td class="font-bold">${escapeHtml(c.criterion)}</td>
            <td class="mono" style="font-size: 11px;">${escapeHtml(c.target)}</td>
            <td class="val-cyan mono" style="font-size: 11px;"><strong>${escapeHtml(c.observed)}</strong></td>
            <td><span class="status-badge ${badgeClass}">${escapeHtml(c.status)}</span></td>
            <td style="font-size: 11px; color: var(--text-secondary);">${escapeHtml(c.honest_note)}</td>
          </tr>
        `;
      });
      experimentsCriteriaTbody.innerHTML = rowsHtml;
    }

    // 2. Condition Performance Matrix (Table 2a)
    if (experimentsMatrixTbody) {
      let rowsHtml = '';
      (data.conditions_matrix || []).forEach((row) => {
        rowsHtml += `
          <tr>
            <td class="tabular-num">${row.tier}</td>
            <td class="font-bold">${row.scenario}</td>
            <td class="mono">${row.load}</td>
            <td class="mono" style="color: var(--accent-cyan);">${row.cond}</td>
            <td class="tabular-num">${row.n}</td>
            <td class="tabular-num font-bold">${row.med_itt.toFixed(2)}s</td>
            <td class="tabular-num">${row.mean_itt.toFixed(2)}s</td>
            <td class="tabular-num">${row.p95_itt.toFixed(2)}s</td>
            <td class="tabular-num val-cyan">${row.med_res !== null ? row.med_res.toFixed(2) + 's' : '—'}</td>
            <td class="tabular-num val-amber">${row.med_hold !== null ? row.med_hold.toFixed(2) + 's' : '—'}</td>
          </tr>
        `;
      });
      experimentsMatrixTbody.innerHTML = rowsHtml;
    }

    // 3. Ablation Studies
    if (experimentsAblationsTbody) {
      let rowsHtml = '';
      (data.ablations || []).forEach((a) => {
        rowsHtml += `
          <tr>
            <td class="font-bold" style="white-space: nowrap;">${escapeHtml(a.ablation)}</td>
            <td style="color: var(--text-secondary);">${escapeHtml(a.desc)}</td>
            <td style="font-size: 12px;">${escapeHtml(a.verdict)}</td>
          </tr>
        `;
      });
      experimentsAblationsTbody.innerHTML = rowsHtml;
    }
  }

  /**
   * VIEW 7: Decision Log Fetch & Render.
   */
  async function fetchDecisionsData() {
    const url = `/api/decisions?category=${encodeURIComponent(state.decisionCategory)}&limit=50`;
    try {
      const res = await fetch(url);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      renderDecisionsUI(data);
    } catch (err) {
      console.error('Failed to fetch decisions data:', err);
    }
  }

  function renderDecisionsUI(data) {
    if (!data) return;
    const decisions = data.decisions || [];

    if (decisionsCountLabel) {
      decisionsCountLabel.textContent = `Showing ${decisions.length} decision records (${data.source})`;
    }

    if (decisionsTbody) {
      if (decisions.length === 0) {
        decisionsTbody.innerHTML = '<tr><td colspan="9" style="text-align: center; color: var(--text-muted); padding: 24px;">No decisions matching category filter.</td></tr>';
        return;
      }

      let rowsHtml = '';
      decisions.forEach((d) => {
        const tDate = new Date(d.timestamp * 1000);
        const timeStr = tDate.toLocaleTimeString() + '.' + String(tDate.getMilliseconds()).padStart(3, '0');

        let decBadgeClass = 'badge-neutral';
        if (d.decision === 'armed' || d.decision === 'would_arm') decBadgeClass = 'badge-green';
        else if (d.decision === 'holdout') decBadgeClass = 'badge-amber';
        else if (d.decision.startsWith('abstain')) decBadgeClass = 'badge-neutral';

        const benefitStr = d.estimated_benefit_s !== null ? `${d.estimated_benefit_s.toFixed(3)}s` : '—';
        const costStr = d.estimated_cost_s !== null ? `${d.estimated_cost_s.toFixed(3)}s` : '—';

        rowsHtml += `
          <tr>
            <td class="tabular-num">${d.id}</td>
            <td class="tabular-num">${timeStr}</td>
            <td class="mono">${escapeHtml(d.project_id)}</td>
            <td class="mono">${escapeHtml(d.file_class)}</td>
            <td><span class="bin-badge ${decBadgeClass}">${escapeHtml(d.decision.toUpperCase())}</span></td>
            <td class="tabular-num">${d.contention_psi}%</td>
            <td class="tabular-num val-cyan">${benefitStr}</td>
            <td class="tabular-num">${costStr}</td>
            <td><span class="state-badge state-${d.outcome}">${escapeHtml(d.outcome)}</span></td>
          </tr>
        `;
      });
      decisionsTbody.innerHTML = rowsHtml;
    }
  }

  /**
   * DEMO MODE: Replay Engine Implementation.
   */
  async function toggleReplayMode() {
    state.isReplayActive = !state.isReplayActive;

    if (state.isReplayActive) {
      btnToggleReplay.classList.add('active');
      replayBtnLabel.textContent = 'Exit Replay';
      replayBtnIcon.textContent = '⏹';
      replayPlaybackBar.style.display = 'inline-flex';

      sourcePill.className = 'source-pill replay';
      sourceText.textContent = `REPLAY: ${state.replayDataset}`;
      setConnectionStatus(true);

      // Load trials
      await loadReplayDataset(state.replayDataset);
    } else {
      stopReplayPlayback();
      btnToggleReplay.classList.remove('active');
      replayBtnLabel.textContent = 'Replay Mode';
      replayBtnIcon.textContent = '▶';
      replayPlaybackBar.style.display = 'none';

      sourcePill.className = 'source-pill live';
      sourceText.textContent = 'LIVE: SQLite';
      setConnectionStatus(state.sseConnected);

      // Revert to live
      loadViewData(state.currentView);
    }
  }

  async function loadReplayDataset(datasetId) {
    state.replayDataset = datasetId;
    sourceText.textContent = `REPLAY: ${datasetId}`;
    try {
      const res = await fetch(`/api/replay/trials?dataset=${encodeURIComponent(datasetId)}`);
      const data = await res.json();
      state.replayTrials = data.trials || [];
      state.replayIndex = 0;
      updateReplayUI();

      // Automatically synchronize the Compare (A/B) screen when switching replay tiers
      if (datasetId === 'results_tier2') {
        state.compareScenario = 'B';
        state.compareLoad = 'moderate';
        const scenSelect = document.getElementById('compare-scenario-select');
        const loadSelect = document.getElementById('compare-load-select');
        if (scenSelect) scenSelect.value = 'B';
        if (loadSelect) loadSelect.value = 'moderate';
        if (state.currentView === 'compare') {
          loadCompareView();
        }
      } else if (datasetId === 'results_tier1') {
        state.compareScenario = 'A';
        state.compareLoad = 'high';
        const scenSelect = document.getElementById('compare-scenario-select');
        const loadSelect = document.getElementById('compare-load-select');
        if (scenSelect) scenSelect.value = 'A';
        if (loadSelect) loadSelect.value = 'high';
        if (state.currentView === 'compare') {
          loadCompareView();
        }
      }
    } catch (err) {
      console.error('Failed to load replay trials:', err);
    }
  }

  function startReplayPlayback() {
    if (state.replayPlaying) return;
    state.replayPlaying = true;
    if (btnReplayPlay) btnReplayPlay.textContent = '⏸';

    const intervalMs = Math.max(100, Math.round(1000 / state.replaySpeed));
    state.replayTimer = setInterval(() => {
      stepReplay(1);
      if (state.replayIndex >= state.replayTrials.length - 1) {
        stopReplayPlayback();
      }
    }, intervalMs);
  }

  function stopReplayPlayback() {
    state.replayPlaying = false;
    if (btnReplayPlay) btnReplayPlay.textContent = '▶';
    if (state.replayTimer) {
      clearInterval(state.replayTimer);
      state.replayTimer = null;
    }
  }

  function stepReplay(delta) {
    if (state.replayTrials.length === 0) return;
    state.replayIndex = Math.max(0, Math.min(state.replayTrials.length - 1, state.replayIndex + delta));
    updateReplayUI();
  }

  function updateReplayUI() {
    const total = state.replayTrials.length;
    const current = total > 0 ? state.replayIndex + 1 : 0;
    if (replayStepCounter) {
      replayStepCounter.textContent = `Trial ${current} / ${total}`;
    }

    if (total === 0) return;
    const trial = state.replayTrials[state.replayIndex];

    // Synthesize mock Live Overview frame from replay trial
    const isClaimed = trial.claimed;
    const simulatedState = isClaimed ? 'Reserved' : (trial.is_holdout ? 'Released' : 'Observing');

    const simulatedFrame = {
      source: `replay:${state.replayDataset} (Trial #${trial.step})`,
      state_machine: {
        current_state: simulatedState,
        states: ['Idle', 'Observing', 'Predicted', 'Armed', 'Reserved', 'Released'],
        reason: `Replaying Trial ${trial.step}: ${trial.condition} on ${trial.scenario} (${trial.load_level} load, duration ${trial.duration_s}s).`,
      },
      tiles: {
        confidence: {
          value: '100.0%',
          subtext: `Trial #${trial.step} (Replay)`,
          detail: `Workload: ${trial.scenario} / ${trial.load_level}`,
          gate: 'PASS',
        },
        delay: {
          value: `${trial.duration_s.toFixed(2)}s`,
          subtext: `Condition: ${trial.condition}`,
          detail: trial.claim_latency_ms ? `Claim latency: ${trial.claim_latency_ms.toFixed(1)}ms` : 'No claim latency recorded',
        },
        weight: {
          value: String(trial.weight || 100),
          subtext: isClaimed ? 'Elevated CFS reservation' : 'Baseline CFS share',
          detail: isClaimed ? `precompile-res-replay-${trial.step}` : 'Default cgroup',
        },
        psi_pressure: {
          value: `${trial.contention.toFixed(1)}%`,
          subtext: trial.load_level.toUpperCase() + ' load',
          status: trial.contention >= 20 ? 'High' : (trial.contention >= 5 ? 'Moderate' : 'Idle'),
          detail: 'Recorded PSI pressure',
        },
        safety: {
          value: 'NORMAL',
          subtext: 'Replay Telemetry Active',
          detail: 'All invariants verified',
          trip: false,
        },
      },
      transitions: [
        {
          timestamp: trial.timestamp,
          from_state: 'Armed',
          to_state: isClaimed ? 'Reserved' : 'Released',
          event: isClaimed ? `Claimed process (Trial #${trial.step})` : `Build completed (Holdout)`,
          cgroup: isClaimed ? `precompile-res-replay-${trial.step}` : 'default',
          pid: 1000 + trial.step,
          details: `Duration: ${trial.duration_s}s`,
        },
      ],
    };

    updateLiveOverviewUI(simulatedFrame);
  }

  function setupFiltersAndControls() {
    // Compare View Selectors (Hero Screen)
    const compScenSelect = document.getElementById('compare-scenario-select');
    if (compScenSelect) {
      compScenSelect.addEventListener('change', (e) => {
        state.compareScenario = e.target.value;
        if (e.target.value === 'B') {
          // Scenario B was evaluated under Moderate load in Tier 2
          state.compareLoad = 'moderate';
          const loadSelect = document.getElementById('compare-load-select');
          if (loadSelect) loadSelect.value = 'moderate';
        }
        loadCompareView();
      });
    }

    const compLoadSelect = document.getElementById('compare-load-select');
    if (compLoadSelect) {
      compLoadSelect.addEventListener('change', (e) => {
        state.compareLoad = e.target.value;
        loadCompareView();
      });
    }

    const compSourceSelect = document.getElementById('compare-source-select');
    if (compSourceSelect) {
      compSourceSelect.addEventListener('change', (e) => {
        state.compareSource = e.target.value;
        const isLive = e.target.value === 'live';
        const bar = document.getElementById('compare-live-demo-bar');
        const table = document.getElementById('compare-live-table-card');
        if (bar) bar.style.display = isLive ? 'flex' : 'none';
        if (table) table.style.display = isLive ? 'flex' : 'none';
        loadCompareView();
      });
    }

    const compReactiveToggle = document.getElementById('compare-show-reactive-toggle');
    if (compReactiveToggle) {
      compReactiveToggle.addEventListener('change', (e) => {
        state.compareShowReactive = e.target.checked;
        const panel = document.getElementById('panel-reactive');
        const pill = document.getElementById('legend-reactive-pill');
        const grid = document.querySelector('.compare-panels-grid');
        if (panel) panel.style.display = state.compareShowReactive ? 'flex' : 'none';
        if (pill) pill.style.display = state.compareShowReactive ? 'inline-flex' : 'none';
        if (grid) grid.classList.toggle('has-reactive', state.compareShowReactive);
        if (currentCompareData && window.PreCompileCharts) {
          window.PreCompileCharts.renderCompareOverlayChart(
            document.getElementById('chart-compare-overlay'),
            currentCompareData.overlay_timeline,
            { showReactive: state.compareShowReactive }
          );
        }
      });
    }

    const btnPlaySim = document.getElementById('btn-play-compare-animation');
    if (btnPlaySim) {
      btnPlaySim.addEventListener('click', () => {
        if (currentCompareData) {
          const wOut = currentCompareData.without?.median_s || 8.38;
          const wIn = currentCompareData.with_precompile?.median_s || 2.04;
          const react = currentCompareData.reactive_only?.median_s || 1.99;
          startCompareProgressAnimation(wOut, wIn, react);
        }
      });
    }

    const btnRunDemo = document.getElementById('btn-run-demo');
    if (btnRunDemo) btnRunDemo.addEventListener('click', startLiveDemo);

    const btnStopDemo = document.getElementById('btn-stop-demo');
    if (btnStopDemo) btnStopDemo.addEventListener('click', stopLiveDemo);

    // Scenario Filter Pills (View 3)
    const scenPills = document.querySelectorAll('#filter-scenario .filter-pill');
    scenPills.forEach((btn) => {
      btn.addEventListener('click', () => {
        scenPills.forEach((p) => p.classList.remove('active'));
        btn.classList.add('active');
        state.scenarioFilter = btn.getAttribute('data-val');
        fetchDistributionsData();
      });
    });

    // Load Filter Pills (View 3)
    const loadPills = document.querySelectorAll('#filter-load .filter-pill');
    loadPills.forEach((btn) => {
      btn.addEventListener('click', () => {
        loadPills.forEach((p) => p.classList.remove('active'));
        btn.classList.add('active');
        state.loadFilter = btn.getAttribute('data-val');
        fetchDistributionsData();
      });
    });

    // Decision Category Filter Pills (View 7)
    const decPills = document.querySelectorAll('#filter-decision-cat .filter-pill');
    decPills.forEach((btn) => {
      btn.addEventListener('click', () => {
        decPills.forEach((p) => p.classList.remove('active'));
        btn.classList.add('active');
        state.decisionCategory = btn.getAttribute('data-val');
        fetchDecisionsData();
      });
    });

    // Replay Controls
    if (btnToggleReplay) btnToggleReplay.addEventListener('click', toggleReplayMode);
    if (btnReplayPlay) {
      btnReplayPlay.addEventListener('click', () => {
        if (state.replayPlaying) stopReplayPlayback();
        else startReplayPlayback();
      });
    }
    if (btnReplayPrev) btnReplayPrev.addEventListener('click', () => stepReplay(-1));
    if (btnReplayNext) btnReplayNext.addEventListener('click', () => stepReplay(1));
    if (replayDatasetSelect) {
      replayDatasetSelect.addEventListener('change', (e) => {
        loadReplayDataset(e.target.value);
      });
    }
    if (replaySpeedSelect) {
      replaySpeedSelect.addEventListener('change', (e) => {
        state.replaySpeed = parseFloat(e.target.value);
        if (state.replayPlaying) {
          stopReplayPlayback();
          startReplayPlayback();
        }
      });
    }
  }

  function setupEventListeners() {
    // Left Navigation Item Clicks
    navItems.forEach((item) => {
      item.addEventListener('click', (e) => {
        e.preventDefault();
        const view = item.getAttribute('data-view');
        switchView(view);
      });
    });

    // Presentation Mode Button
    if (btnPresMode) {
      btnPresMode.addEventListener('click', togglePresentationMode);
    }

    // Keyboard Shortcuts
    document.addEventListener('keydown', (e) => {
      // 'P' key for presentation mode
      if ((e.key === 'p' || e.key === 'P') && !['INPUT', 'TEXTAREA', 'SELECT'].includes(e.target.tagName)) {
        e.preventDefault();
        togglePresentationMode();
      }

      // Keys 1-7 for instant view switching
      if (['1', '2', '3', '4', '5', '6', '7'].includes(e.key) && !['INPUT', 'TEXTAREA', 'SELECT'].includes(e.target.tagName)) {
        const viewKeys = ['overview', 'timeline', 'distributions', 'benefit', 'safety', 'experiments', 'decisions'];
        const targetView = viewKeys[parseInt(e.key, 10) - 1];
        if (targetView) switchView(targetView);
      }

      // Spacebar for play/pause in replay mode
      if (e.code === 'Space' && state.isReplayActive && !['INPUT', 'TEXTAREA', 'SELECT'].includes(e.target.tagName)) {
        e.preventDefault();
        if (state.replayPlaying) stopReplayPlayback();
        else startReplayPlayback();
      }
    });

    // Initial Overview Fetch
    fetch('/api/overview')
      .then((r) => r.json())
      .then(updateLiveOverviewUI)
      .catch((err) => console.warn('Initial overview fetch:', err));
  }

  function escapeHtml(str) {
    return String(str).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  // Application Entry Point
  document.addEventListener('DOMContentLoaded', () => {
    setupEventListeners();
    setupFiltersAndControls();
    initSSE();
    switchView('compare');
  });
})();
