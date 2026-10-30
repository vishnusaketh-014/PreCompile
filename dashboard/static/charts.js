/**
 * Pure-JS, zero-dependency SVG chart helpers for PreCompile Dashboard.
 * Offline-ready, optimized for dark mission-control visual aesthetics.
 */

window.PreCompileCharts = (function() {
  const COLOR_RESERVED = '#00E5FF'; // Electric Cyan (WITH PreCompile)
  const COLOR_HOLDOUT = '#90A4AE';  // Cool Grey-Blue (WITHOUT PreCompile)
  const COLOR_REACTIVE = '#FFB300'; // Amber (Reactive Only C1)
  const COLOR_BG_CARD = '#16202A';
  const COLOR_BORDER = '#253444';
  const COLOR_TEXT = '#94A3B8';
  const COLOR_TEXT_BOLD = '#F1F5F9';

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  }

  /**
   * Render side-by-side box & jittered points distribution chart.
   */
  function renderBoxAndPoints(container, data) {
    if (!container) return;
    container.innerHTML = '';

    const resStats = data.reserved?.stats;
    const holdStats = data.holdout?.stats;
    const resPoints = data.reserved?.points || [];
    const holdPoints = data.holdout?.points || [];

    if (!resStats || !holdStats || (resStats.n === 0 && holdStats.n === 0)) {
      container.innerHTML = `
        <div class="empty-chart-state">
          <div class="empty-icon">📊</div>
          <div class="empty-title">No Distribution Data Available</div>
          <div class="empty-desc">No recorded build points match the current filter criteria.</div>
        </div>
      `;
      return;
    }

    const rect = container.getBoundingClientRect();
    const width = Math.max(rect.width || 640, 560);
    const height = 340;
    const padding = { top: 40, right: 30, bottom: 50, left: 70 };
    const chartW = width - padding.left - padding.right;
    const chartH = height - padding.top - padding.bottom;

    // Determine scale bounds
    const allVals = [
      ...(resPoints.map(p => p.duration_s)),
      ...(holdPoints.map(p => p.duration_s)),
      resStats.max || 0,
      holdStats.max || 0
    ].filter(v => v !== null && v > 0);

    const minVal = 0;
    const maxVal = Math.max(Math.ceil((Math.max(...allVals, 5.0) * 1.15) * 10) / 10, 3.0);

    function scaleY(v) {
      return chartH - ((v - minVal) / (maxVal - minVal)) * chartH;
    }

    // SVG Canvas
    let svg = `<svg width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" class="dist-svg" xmlns="http://www.w3.org/2000/svg">`;

    // Grid lines
    const ticks = 5;
    for (let i = 0; i <= ticks; i++) {
      const v = minVal + (maxVal - minVal) * (i / ticks);
      const y = padding.top + scaleY(v);
      svg += `
        <line x1="${padding.left}" y1="${y}" x2="${width - padding.right}" y2="${y}" stroke="${COLOR_BORDER}" stroke-dasharray="3,3" opacity="0.6"/>
        <text x="${padding.left - 12}" y="${y + 4}" fill="${COLOR_TEXT}" font-size="11" font-family="'JetBrains Mono', monospace" text-anchor="end">${v.toFixed(1)}s</text>
      `;
    }

    // Y Axis Label
    svg += `
      <text x="${-height / 2}" y="18" transform="rotate(-90)" fill="${COLOR_TEXT}" font-size="11" font-family="'Space Grotesk', sans-serif" text-anchor="middle" font-weight="600">
        BUILD DURATION (SECONDS)
      </text>
    `;

    // Columns: Reserved (Left), Holdout (Right)
    const colW = chartW / 2;
    const colCenters = {
      reserved: padding.left + colW * 0.5,
      holdout: padding.left + colW * 1.5
    };

    // Draw Column Headers
    svg += `
      <g transform="translate(${colCenters.reserved}, ${padding.top - 16})">
        <circle cx="-50" cy="-4" r="5" fill="${COLOR_RESERVED}"/>
        <text x="-40" y="0" fill="${COLOR_RESERVED}" font-size="13" font-family="'Space Grotesk', sans-serif" font-weight="700">RESERVED (C2-FULL)</text>
        <text x="75" y="0" fill="${COLOR_TEXT}" font-size="11" font-family="'JetBrains Mono', monospace">n=${resStats.n}</text>
      </g>
      <g transform="translate(${colCenters.holdout}, ${padding.top - 16})">
        <circle cx="-50" cy="-4" r="5" fill="${COLOR_HOLDOUT}"/>
        <text x="-40" y="0" fill="${COLOR_HOLDOUT}" font-size="13" font-family="'Space Grotesk', sans-serif" font-weight="700">HOLDOUT (CONTROL)</text>
        <text x="75" y="0" fill="${COLOR_TEXT}" font-size="11" font-family="'JetBrains Mono', monospace">n=${holdStats.n}</text>
      </g>
    `;

    // Render group: Reserved
    svg += renderBoxGroup(colCenters.reserved, resStats, resPoints, COLOR_RESERVED, scaleY, padding.top);

    // Render group: Holdout
    svg += renderBoxGroup(colCenters.holdout, holdStats, holdPoints, COLOR_HOLDOUT, scaleY, padding.top);

    svg += `</svg>`;
    container.innerHTML = svg;

    // Attach tooltips
    setupPointTooltips(container);
  }

  function renderBoxGroup(cx, stats, points, color, scaleY, topOffset) {
    if (!stats || stats.n === 0) return '';
    let out = '';
    const boxW = 70;
    const halfBox = boxW / 2;

    const yMin = topOffset + scaleY(stats.min);
    const yMax = topOffset + scaleY(stats.max);
    const yP25 = topOffset + scaleY(stats.p25);
    const yP75 = topOffset + scaleY(stats.p75);
    const yMed = topOffset + scaleY(stats.median);

    // Whiskers line (min to max)
    out += `
      <line x1="${cx}" y1="${yMin}" x2="${cx}" y2="${yMax}" stroke="${color}" stroke-width="1.5" stroke-dasharray="2,2" opacity="0.8"/>
      <line x1="${cx - 15}" y1="${yMin}" x2="${cx + 15}" y2="${yMin}" stroke="${color}" stroke-width="2"/>
      <line x1="${cx - 15}" y1="${yMax}" x2="${cx + 15}" y2="${yMax}" stroke="${color}" stroke-width="2"/>
    `;

    // Box (P25 to P75)
    const boxH = Math.max(yP25 - yP75, 4);
    out += `
      <rect x="${cx - halfBox}" y="${yP75}" width="${boxW}" height="${boxH}" rx="4"
            fill="${color}" fill-opacity="0.15" stroke="${color}" stroke-width="1.8"/>
    `;

    // Median line
    out += `
      <line x1="${cx - halfBox}" y1="${yMed}" x2="${cx + halfBox}" y2="${yMed}" stroke="${color}" stroke-width="3"/>
      <text x="${cx + halfBox + 10}" y="${yMed + 4}" fill="${color}" font-size="12" font-family="'JetBrains Mono', monospace" font-weight="700">
        med: ${stats.median.toFixed(2)}s
      </text>
    `;

    // Jittered points (offset slightly to the left/right of center)
    const pointJitterW = 40;
    points.forEach((pt, idx) => {
      // Deterministic pseudo-jitter from index
      const jitter = ((Math.sin(idx * 7.5 + 3.1) * 0.5)) * pointJitterW;
      const px = cx + jitter;
      const py = topOffset + scaleY(pt.duration_s);
      out += `
        <circle cx="${px}" cy="${py}" r="3.5" fill="${color}" fill-opacity="0.65" stroke="#0B0F14" stroke-width="0.8"
                class="chart-point" data-dur="${pt.duration_s}" data-psi="${pt.contention.toFixed(1)}"
                data-cond="${pt.condition}" data-scen="${pt.scenario || 'A'}" data-load="${pt.load_level || 'mod'}"/>
      `;
    });

    return out;
  }

  function setupPointTooltips(container) {
    const points = container.querySelectorAll('.chart-point');
    let tooltip = document.getElementById('chart-tooltip');
    if (!tooltip) {
      tooltip = document.createElement('div');
      tooltip.id = 'chart-tooltip';
      tooltip.className = 'chart-tooltip';
      document.body.appendChild(tooltip);
    }

    points.forEach(p => {
      p.addEventListener('mouseenter', e => {
        const dur = p.getAttribute('data-dur');
        const psi = p.getAttribute('data-psi');
        const cond = p.getAttribute('data-cond');
        const scen = p.getAttribute('data-scen');
        const load = p.getAttribute('data-load');

        tooltip.innerHTML = `
          <div class="tt-header">${escapeHtml(cond)} [${escapeHtml(scen)} / ${escapeHtml(load)}]</div>
          <div class="tt-row"><span>Build Duration:</span> <strong>${dur}s</strong></div>
          <div class="tt-row"><span>Contention PSI:</span> <strong>${psi}%</strong></div>
        `;
        tooltip.style.opacity = '1';
        positionTooltip(e, tooltip);
      });

      p.addEventListener('mousemove', e => positionTooltip(e, tooltip));

      p.addEventListener('mouseleave', () => {
        tooltip.style.opacity = '0';
      });
    });
  }

  function positionTooltip(e, tooltip) {
    const x = e.clientX + 14;
    const y = e.clientY - 30;
    tooltip.style.left = `${x}px`;
    tooltip.style.top = `${y}px`;
  }

  /**
   * Render side-by-side contention bin comparative cards.
   */
  function renderContentionBins(container, bins) {
    if (!container) return;
    if (!bins) {
      container.innerHTML = '<div class="empty-text">No contention bin data available.</div>';
      return;
    }

    const binLabels = {
      idle: { title: 'Idle Contention', psi: '< 5% PSI', desc: 'No background CPU stress' },
      moderate: { title: 'Moderate Contention', psi: '5% – 20% PSI', desc: '2 hogs per active core' },
      high: { title: 'High Contention', psi: '≥ 20% PSI', desc: '4 hogs per active core' }
    };

    let html = '<div class="contention-bins-grid">';
    for (const [key, meta] of Object.entries(binLabels)) {
      const b = bins[key] || {};
      const nR = b.n_reserved || 0;
      const nH = b.n_holdout || 0;
      const medR = b.median_reserved !== null ? `${b.median_reserved.toFixed(2)}s` : 'N/A';
      const medH = b.median_holdout !== null ? `${b.median_holdout.toFixed(2)}s` : 'N/A';
      const eff = b.effect_pct !== null ? `${b.effect_pct > 0 ? '+' : ''}${b.effect_pct.toFixed(1)}%` : 'N/A';
      const pVal = b.p_value !== null ? `p = ${b.p_value.toFixed(4)}` : 'p: N/A';
      const isSig = b.p_value !== null && b.p_value < 0.05;

      html += `
        <div class="bin-card">
          <div class="bin-header">
            <div>
              <div class="bin-title">${meta.title}</div>
              <div class="bin-sub">${meta.psi} • ${meta.desc}</div>
            </div>
            <div class="bin-badge ${eff.startsWith('+') ? 'badge-green' : 'badge-neutral'}">${eff} speedup</div>
          </div>
          <div class="bin-stats-row">
            <div class="bin-stat">
              <span class="bin-stat-label">Reserved (med)</span>
              <span class="bin-stat-val val-cyan">${medR}</span>
              <span class="bin-stat-count">n=${nR}</span>
            </div>
            <div class="bin-stat">
              <span class="bin-stat-label">Holdout (med)</span>
              <span class="bin-stat-val val-amber">${medH}</span>
              <span class="bin-stat-count">n=${nH}</span>
            </div>
          </div>
          <div class="bin-footer">
            <span class="bin-significance ${isSig ? 'text-cyan' : 'text-muted'}">
              ${isSig ? '★ Statistically Significant' : '• Insufficient / Baseline delta'}
            </span>
            <span class="bin-pval">${pVal}</span>
          </div>
        </div>
      `;
    }
    html += '</div>';
    container.innerHTML = html;
  }

  /**
   * Render horizontal swimlanes for View 2 (Prediction Timeline).
   */
  function renderPredictionTimeline(container, data) {
    if (!container) return;
    container.innerHTML = '';
    const cycles = data.cycles || [];

    if (cycles.length === 0) {
      container.innerHTML = `
        <div class="empty-chart-state">
          <div class="empty-icon">⏱</div>
          <div class="empty-title">No Timeline Cycles Recorded</div>
          <div class="empty-desc">No save-to-build prediction cycles recorded in current window.</div>
        </div>
      `;
      return;
    }

    let html = '<div class="timeline-swimlanes-list">';

    // Global scale: find max time bound for swimlane scale
    const maxT = Math.max(
      3.0,
      ...cycles.map(c => Math.max(c.window_hi || 2.5, c.actual_spawn || 2.0) * 1.15)
    );

    cycles.slice(-20).reverse().forEach(c => {
      const wLoPct = Math.min(100, (c.window_lo / maxT) * 100);
      const wHiPct = Math.min(100, (c.window_hi / maxT) * 100);
      const wWidthPct = Math.max(2, wHiPct - wLoPct);

      const spawnPct = c.actual_spawn !== null ? Math.min(100, (c.actual_spawn / maxT) * 100) : null;
      const predPct = c.predicted_spawn !== null ? Math.min(100, (c.predicted_spawn / maxT) * 100) : null;

      const isHit = c.outcome === 'hit';
      const isHold = c.outcome === 'holdout';
      const isMiss = c.outcome === 'miss';

      const outcomeClass = isHit ? 'badge-green' : (isHold ? 'badge-amber' : (isMiss ? 'badge-red' : 'badge-neutral'));
      const errorStr = c.prediction_error_s !== null ? `${c.prediction_error_s > 0 ? '+' : ''}${c.prediction_error_s.toFixed(2)}s` : 'N/A';
      const errorColor = (c.prediction_error_s !== null && Math.abs(c.prediction_error_s) <= 0.3) ? 'var(--accent-cyan)' : 'var(--color-holdout)';

      html += `
        <div class="swimlane-card">
          <div class="swimlane-meta">
            <div class="swimlane-id-row">
              <span class="swimlane-id">${escapeHtml(c.cycle_id)}</span>
              <span class="bin-badge ${outcomeClass}">${(c.outcome || 'unscored').toUpperCase()}</span>
            </div>
            <div class="swimlane-sub">
              <span>Spawn: <strong>${c.actual_spawn !== null ? c.actual_spawn.toFixed(2) + 's' : 'none'}</strong></span>
              <span>Err: <strong style="color: ${errorColor};">${errorStr}</strong></span>
              ${c.claim_latency_ms !== null ? `<span>Claim: <strong class="val-cyan">${c.claim_latency_ms.toFixed(1)}ms</strong></span>` : ''}
            </div>
          </div>
          <div class="swimlane-track-wrap">
            <div class="swimlane-track">
              <!-- Save marker at 0.0s -->
              <div class="track-marker marker-save" style="left: 0%;" title="File Save (0.00s)">💾</div>

              <!-- Predicted Window Shading -->
              <div class="track-window" style="left: ${wLoPct}%; width: ${wWidthPct}%;"
                   title="Predicted Window: [${c.window_lo.toFixed(2)}s, ${c.window_hi.toFixed(2)}s]">
                ${predPct !== null ? `<div class="window-center-line" style="left: ${((predPct - wLoPct) / wWidthPct) * 100}%;"></div>` : ''}
              </div>

              <!-- Actual Spawn Pip -->
              ${spawnPct !== null ? `
                <div class="track-marker marker-spawn ${isHit ? 'spawn-hit' : 'spawn-miss'}" style="left: ${spawnPct}%;"
                     title="Process Spawn: ${c.actual_spawn.toFixed(3)}s">
                  🚀
                </div>
              ` : ''}
            </div>
            <div class="swimlane-axis">
              <span>0.0s (Save)</span>
              <span>${(maxT * 0.5).toFixed(1)}s</span>
              <span>${maxT.toFixed(1)}s</span>
            </div>
          </div>
        </div>
      `;
    });

    html += '</div>';
    container.innerHTML = html;
  }

  /**
   * Render Adaptive Weight Progression Chart for View 4 (Benefit Estimator).
   */
  function renderBenefitWeightChart(container, weightHistory) {
    if (!container) return;
    container.innerHTML = '';

    if (!weightHistory || weightHistory.length === 0) {
      container.innerHTML = '<div class="empty-text">No weight adaptation steps recorded.</div>';
      return;
    }

    const rect = container.getBoundingClientRect();
    const width = Math.max(rect.width || 600, 500);
    const height = 220;
    const padding = { top: 20, right: 30, bottom: 40, left: 60 };
    const chartW = width - padding.left - padding.right;
    const chartH = height - padding.top - padding.bottom;

    const minW = 100;
    const maxW = 1000;

    function scaleY(w) {
      return chartH - ((w - minW) / (maxW - minW)) * chartH;
    }

    let svg = `<svg width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" xmlns="http://www.w3.org/2000/svg">`;

    // Grid lines for weight levels
    [100, 250, 500, 750, 1000].forEach(w => {
      const y = padding.top + scaleY(w);
      svg += `
        <line x1="${padding.left}" y1="${y}" x2="${width - padding.right}" y2="${y}" stroke="${COLOR_BORDER}" stroke-dasharray="2,2"/>
        <text x="${padding.left - 8}" y="${y + 4}" fill="${COLOR_TEXT}" font-size="10" font-family="'JetBrains Mono', monospace" text-anchor="end">${w}</text>
      `;
    });

    // Step line path
    const n = weightHistory.length;
    let pathD = '';
    const stepW = chartW / Math.max(n - 1, 1);

    weightHistory.forEach((pt, i) => {
      const x = padding.left + i * stepW;
      const y = padding.top + scaleY(pt.weight);
      if (i === 0) {
        pathD += `M ${x} ${y}`;
      } else {
        const prevY = padding.top + scaleY(weightHistory[i - 1].weight);
        pathD += ` L ${x} ${prevY} L ${x} ${y}`;
      }
    });

    svg += `
      <path d="${pathD}" fill="none" stroke="${COLOR_RESERVED}" stroke-width="2.5"/>
    `;

    // Step points
    weightHistory.forEach((pt, i) => {
      const x = padding.left + i * stepW;
      const y = padding.top + scaleY(pt.weight);
      svg += `
        <circle cx="${x}" cy="${y}" r="5" fill="${COLOR_RESERVED}" stroke="#0B0F14" stroke-width="1.5"/>
        <text x="${x}" y="${y - 10}" fill="${COLOR_TEXT_BOLD}" font-size="11" font-family="'JetBrains Mono', monospace" text-anchor="middle" font-weight="700">
          ${pt.weight}
        </text>
      `;
    });

    svg += `</svg>`;
    container.innerHTML = svg;
  }

  /**
   * Render Safety Governor Radial / Bar Budget Gauge for View 5.
   */
  function renderSafetyBudgetGauge(container, budgetData) {
    if (!container) return;
    const b = budgetData || { limit_seconds: 900, used_seconds: 0, consumed_percent: 0 };
    const pct = Math.min(100, Math.max(0, b.consumed_percent || 0));

    const color = pct >= 90 ? 'var(--color-danger)' : (pct >= 70 ? 'var(--color-holdout)' : 'var(--accent-cyan)');

    container.innerHTML = `
      <div class="budget-gauge-box">
        <div class="gauge-bar-track">
          <div class="gauge-bar-fill" style="width: ${pct}%; background-color: ${color};"></div>
        </div>
        <div class="gauge-labels-row">
          <span class="mono">0s</span>
          <span class="mono"><strong>${b.used_seconds}s</strong> consumed (${pct}%)</span>
          <span class="mono">${b.limit_seconds}s (Ceiling)</span>
        </div>
      </div>
    `;
  }

  /**
   * Render side-by-side overlaid CPU-share timeline and event markers for the Compare screen.
   */
  function renderCompareOverlayChart(container, overlayData, options) {
    if (!container) return;
    container.innerHTML = '';

    if (!overlayData || !overlayData.traces) {
      container.innerHTML = `
        <div class="empty-chart-state">
          <div class="empty-icon">📈</div>
          <div class="empty-title">Timeline Data Unavailable</div>
          <div class="empty-desc">No trace points recorded for this configuration.</div>
        </div>
      `;
      return;
    }

    const showReactive = options && options.showReactive;
    const rect = container.getBoundingClientRect();
    const width = Math.max(rect.width || 800, 680);
    const height = 300;
    const padding = { top: 45, right: 40, bottom: 50, left: 75 };
    const chartW = width - padding.left - padding.right;
    const chartH = height - padding.top - padding.bottom;

    const tMax = Math.max(overlayData.t_max || 12.0, 6.0);
    const tMin = 0.0;
    const shareMax = 75.0; // 0% to 75% CPU Share

    function scaleX(t) {
      return padding.left + ((t - tMin) / (tMax - tMin)) * chartW;
    }

    function scaleY(share) {
      return padding.top + chartH - (share / shareMax) * chartH;
    }

    let svg = `<svg width="100%" height="${height}" viewBox="0 0 ${width} ${height}" xmlns="http://www.w3.org/2000/svg">`;

    // Definitions for gradients & glow filters
    svg += `
      <defs>
        <linearGradient id="grad-with" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="${COLOR_RESERVED}" stop-opacity="0.35"/>
          <stop offset="100%" stop-color="${COLOR_RESERVED}" stop-opacity="0.0"/>
        </linearGradient>
        <linearGradient id="grad-without" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="${COLOR_HOLDOUT}" stop-opacity="0.25"/>
          <stop offset="100%" stop-color="${COLOR_HOLDOUT}" stop-opacity="0.0"/>
        </linearGradient>
        <linearGradient id="grad-reactive" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="${COLOR_REACTIVE}" stop-opacity="0.25"/>
          <stop offset="100%" stop-color="${COLOR_REACTIVE}" stop-opacity="0.0"/>
        </linearGradient>
        <filter id="glow-cyan" x="-20%" y="-20%" width="140%" height="140%">
          <feGaussianBlur stdDeviation="3" result="blur" />
          <feComposite in="SourceGraphic" in2="blur" operator="over" />
        </filter>
      </defs>
    `;

    // Horizontal grid lines and Y-axis labels
    const yTicks = [
      { val: 0, label: '0%' },
      { val: 12.5, label: '12.5% (CFS 100)' },
      { val: 25.0, label: '25%' },
      { val: 50.0, label: '50%' },
      { val: 62.5, label: '62.5% (CFS 500)' },
    ];

    yTicks.forEach(tick => {
      const y = scaleY(tick.val);
      svg += `
        <line x1="${padding.left}" y1="${y}" x2="${width - padding.right}" y2="${y}" stroke="${COLOR_BORDER}" stroke-dasharray="3,3" />
        <text x="${padding.left - 10}" y="${y + 4}" fill="${COLOR_TEXT}" font-size="11" font-family="JetBrains Mono, monospace" text-anchor="end">${tick.label}</text>
      `;
    });

    // Time X-axis ticks (every 1s or 2s)
    const tickStep = tMax > 15 ? 4.0 : (tMax > 8 ? 2.0 : 1.0);
    for (let t = 0; t <= tMax; t += tickStep) {
      const x = scaleX(t);
      svg += `
        <line x1="${x}" y1="${padding.top + chartH}" x2="${x}" y2="${padding.top + chartH + 6}" stroke="${COLOR_BORDER}" />
        <text x="${x}" y="${padding.top + chartH + 20}" fill="${COLOR_TEXT}" font-size="11" font-family="JetBrains Mono, monospace" text-anchor="middle">${t.toFixed(0)}s</text>
      `;
    }

    // Axis Labels
    svg += `
      <text x="${padding.left}" y="${padding.top - 18}" fill="${COLOR_TEXT}" font-size="11" font-family="JetBrains Mono, monospace">CPU SHARE (% CFS Capacity)</text>
      <text x="${width - padding.right}" y="${padding.top + chartH + 38}" fill="${COLOR_TEXT}" font-size="11" font-family="JetBrains Mono, monospace" text-anchor="end">TIMELINE (seconds from file save)</text>
    `;

    // Helper to generate path d string
    function makePath(points) {
      if (!points || points.length === 0) return '';
      return points.map((p, idx) => `${idx === 0 ? 'M' : 'L'} ${scaleX(p.t).toFixed(1)} ${scaleY(p.share).toFixed(1)}`).join(' ');
    }

    function makeAreaPath(points) {
      if (!points || points.length === 0) return '';
      const lineD = points.map((p, idx) => `${idx === 0 ? 'M' : 'L'} ${scaleX(p.t).toFixed(1)} ${scaleY(p.share).toFixed(1)}`).join(' ');
      const lastX = scaleX(points[points.length - 1].t).toFixed(1);
      const firstX = scaleX(points[0].t).toFixed(1);
      const baseY = scaleY(0).toFixed(1);
      return `${lineD} L ${lastX} ${baseY} L ${firstX} ${baseY} Z`;
    }

    // 1. Draw WITHOUT PreCompile trace (Cool Grey-Blue)
    const withoutPoints = overlayData.traces.without || [];
    if (withoutPoints.length > 0) {
      svg += `
        <path d="${makeAreaPath(withoutPoints)}" fill="url(#grad-without)" />
        <path d="${makePath(withoutPoints)}" fill="none" stroke="${COLOR_HOLDOUT}" stroke-width="2.5" stroke-dasharray="6,4" opacity="0.9" />
      `;
    }

    // 2. Draw Reactive trace (if enabled)
    const reactivePoints = overlayData.traces.reactive || [];
    if (showReactive && reactivePoints.length > 0) {
      svg += `
        <path d="${makeAreaPath(reactivePoints)}" fill="url(#grad-reactive)" />
        <path d="${makePath(reactivePoints)}" fill="none" stroke="${COLOR_REACTIVE}" stroke-width="2.2" stroke-dasharray="3,3" opacity="0.85" />
      `;
    }

    // 3. Draw WITH PreCompile trace (Electric Cyan with Glow)
    const withPoints = overlayData.traces.with || [];
    if (withPoints.length > 0) {
      svg += `
        <path d="${makeAreaPath(withPoints)}" fill="url(#grad-with)" />
        <path d="${makePath(withPoints)}" fill="none" stroke="${COLOR_RESERVED}" stroke-width="3" filter="url(#glow-cyan)" />
      `;
    }

    // Vertical Event Markers
    const events = overlayData.events || [];
    events.forEach(ev => {
      if (ev.source === 'reactive' && !showReactive) return;
      const x = scaleX(ev.t_s);
      let evColor = COLOR_TEXT;
      let dash = '2,2';
      let yOffset = padding.top + 10;

      if (ev.source === 'with') {
        evColor = COLOR_RESERVED;
        dash = '4,2';
        yOffset = padding.top + 26;
      } else if (ev.source === 'without') {
        evColor = COLOR_HOLDOUT;
        dash = '4,4';
        yOffset = padding.top + 42;
      } else if (ev.source === 'reactive') {
        evColor = COLOR_REACTIVE;
        yOffset = padding.top + 58;
      }

      svg += `
        <line x1="${x}" y1="${padding.top}" x2="${x}" y2="${padding.top + chartH}" stroke="${evColor}" stroke-width="1.5" stroke-dasharray="${dash}" opacity="0.75" />
        <circle cx="${x}" cy="${padding.top + chartH}" r="3.5" fill="${evColor}" />
      `;

      // Event Label Badge
      const isRightAligned = (x > width - 150);
      const textAnchor = isRightAligned ? 'end' : 'start';
      const textX = isRightAligned ? x - 6 : x + 6;

      svg += `
        <text x="${textX}" y="${yOffset}" fill="${evColor}" font-size="10" font-weight="600" font-family="JetBrains Mono, monospace" text-anchor="${textAnchor}">
          ${escapeHtml(ev.name)} (${ev.t_s.toFixed(2)}s)
        </text>
      `;
    });

    svg += `</svg>`;
    container.innerHTML = svg;
  }

  return {
    renderBoxAndPoints,
    renderContentionBins,
    renderPredictionTimeline,
    renderBenefitWeightChart,
    renderSafetyBudgetGauge,
    renderCompareOverlayChart,
  };
})();
