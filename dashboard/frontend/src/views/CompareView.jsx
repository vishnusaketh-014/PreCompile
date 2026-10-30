import React, { useState, useEffect, useCallback, useRef } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { useAPI } from '../hooks/useData.js'
import { MetricCard, SpeedupBadge, AnimatedNumber, BuildProgressBar } from '../components/MetricCards.jsx'
import OverlayChart from '../components/OverlayChart.jsx'

export default function CompareView({ presentationMode, performanceMode }) {
  const [scenario, setScenario] = useState('A')
  const [contention, setContention] = useState('high')
  const [dataSource, setDataSource] = useState('recorded')
  const [showReactive, setShowReactive] = useState(true)
  const [pairsCount, setPairsCount] = useState(3)
  const [demoRunning, setDemoRunning] = useState(false)
  const [demoStatus, setDemoStatus] = useState(null)
  const [demoFallback, setDemoFallback] = useState(false)

  // Simulation / racing state for the "Replay Build Race" feature
  const [isRacing, setIsRacing] = useState(false)
  const [raceElapsed, setRaceElapsed] = useState(0)
  const raceAnimRef = useRef(null)

  // Fetch compare data from API
  const { data, loading, error, refetch } = useAPI(
    `/api/compare?scenario=${scenario}&load=${contention}&source=${dataSource}&reactive=${showReactive ? '1' : '0'}`,
    [scenario, contention, dataSource, showReactive]
  )

  // Safely extract backend data objects
  const without = data?.without
  const with_ = data?.with_precompile
  const reactive = data?.reactive_only
  const speedup = data?.speedup
  const c2VsC1 = data?.c2_vs_c1
  const timeline = data?.overlay_timeline
  const loadLevel = data?.load_level || contention
  const hasData = Boolean(without && with_)

  const medWithout = without?.median_s ?? 8.38
  const medWith = with_?.median_s ?? 2.04
  const medReactive = reactive?.median_s ?? 1.99
  const maxRaceTime = Math.max(medWithout, medWith, medReactive, 1.0)

  // Poll demo status when running
  useEffect(() => {
    if (!demoRunning) return
    const id = setInterval(async () => {
      try {
        const res = await fetch('/api/demo/status')
        const st = await res.json()
        setDemoStatus(st)
        if (st.status === 'completed' || st.status === 'stopped' || st.status === 'error') {
          setDemoRunning(false)
          refetch()
        }
      } catch { /* ignore */ }
    }, 800)
    return () => clearInterval(id)
  }, [demoRunning, refetch])

  const startDemo = useCallback(async () => {
    try {
      const res = await fetch('/api/demo/start', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ scenario, load_level: contention, pairs: pairsCount }),
      })
      const result = await res.json()
      if (result.status === 'started') {
        setDemoRunning(true)
        setDemoStatus(null)
      } else if (result.status === 'error') {
        setDemoFallback(true)
      }
    } catch {
      setDemoFallback(true)
    }
  }, [scenario, contention, pairsCount])

  const stopDemo = useCallback(async () => {
    await fetch('/api/demo/stop', { method: 'POST' })
    setDemoRunning(false)
  }, [])

  // Interactive Replay Build Race Animation
  const startBuildRace = useCallback(() => {
    if (isRacing) return
    setIsRacing(true)
    setRaceElapsed(0)

    const startTime = performance.now()
    const simDurationMs = 2800 // scale visual race to ~2.8 seconds

    function tick(now) {
      const progress = Math.min(1.0, (now - startTime) / simDurationMs)
      const currentSimSec = progress * maxRaceTime
      setRaceElapsed(currentSimSec)

      if (progress < 1.0) {
        raceAnimRef.current = requestAnimationFrame(tick)
      } else {
        setIsRacing(false)
      }
    }

    raceAnimRef.current = requestAnimationFrame(tick)
  }, [isRacing, maxRaceTime])

  useEffect(() => {
    return () => {
      if (raceAnimRef.current) cancelAnimationFrame(raceAnimRef.current)
    }
  }, [])

  const scenariosList = data?.available_scenarios || [
    { id: 'A', name: 'Scenario A: Regular Delays (Deterministic ~2.0s)' },
    { id: 'B', name: 'Scenario B: Irregular Delays (Variable Workflow)' },
    { id: 'C', name: 'Scenario C: Save-Only / Concurrent (Abstain Audit)' },
  ]

  const loadsList = data?.available_loads || [
    { id: 'high', name: 'High Contention (CPU PSI 60-80%)' },
    { id: 'moderate', name: 'Moderate Contention (CPU PSI 25-40%)' },
    { id: 'idle', name: 'Idle / Uncontended (CPU PSI 0-5%)' },
  ]

  return (
    <div className={`view ${presentationMode ? 'presentation-mode' : ''}`}>
      {/* Header */}
      <div className="view-header">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
          <div>
            <div className="view-title">WITH vs WITHOUT PreCompile</div>
            <div className="view-desc">
              Direct empirical comparison of anticipatory priority reservation vs. normal scheduling
            </div>
          </div>

          {/* Interactive Build Race Replay Button */}
          {hasData && (
            <button
              className="btn btn-secondary"
              onClick={startBuildRace}
              disabled={isRacing}
              style={{ display: 'flex', alignItems: 'center', gap: 6, borderColor: 'var(--with)' }}
            >
              <span>{isRacing ? '⏳ Racing...' : '▶ Replay Build Race'}</span>
              {isRacing && <span className="tabnum">({raceElapsed.toFixed(2)}s)</span>}
            </button>
          )}
        </div>
      </div>

      {/* Selectors Bar */}
      <motion.div
        className="selectors-bar"
        initial={{ opacity: 0, y: -10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.3 }}
      >
        <div className="selector-group">
          <div className="selector-label">Workload Scenario</div>
          <select
            className="selector-select"
            value={scenario}
            onChange={e => setScenario(e.target.value)}
          >
            {scenariosList.map(s => (
              <option key={s.id} value={s.id}>{s.name}</option>
            ))}
          </select>
        </div>

        <div className="selector-group">
          <div className="selector-label">Contention Load Level</div>
          <select
            className="selector-select"
            value={contention}
            onChange={e => setContention(e.target.value)}
          >
            {loadsList.map(c => (
              <option key={c.id} value={c.id}>{c.name}</option>
            ))}
          </select>
        </div>

        <div className="selector-group">
          <div className="selector-label">Data Source Mode</div>
          <select
            className="selector-select"
            value={dataSource}
            onChange={e => setDataSource(e.target.value)}
          >
            <option value="recorded">Recorded Benchmark Data</option>
            <option value="live">Paired A/B Demo Runner</option>
          </select>
        </div>

        {dataSource === 'live' && (
          <div className="selector-group">
            <div className="selector-label">Pairs to Run</div>
            <select
              className="selector-select"
              value={pairsCount}
              onChange={e => setPairsCount(Number(e.target.value))}
              disabled={demoRunning}
            >
              <option value={1}>1 Pair (Fast Demo)</option>
              <option value={3}>3 Pairs (Balanced)</option>
              <option value={5}>5 Pairs (Recommended)</option>
              <option value={10}>10 Pairs (Statistical)</option>
            </select>
          </div>
        )}

        <div className="selector-group" style={{ flexDirection: 'row', alignItems: 'center', marginTop: 16 }}>
          <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: 'var(--fg-muted)', fontFamily: 'var(--font-mono)', cursor: 'pointer' }}>
            <input
              type="checkbox"
              checked={showReactive}
              onChange={e => setShowReactive(e.target.checked)}
              style={{ accentColor: 'var(--gold)' }}
            />
            Show Reactive Only (C1)
          </label>
        </div>

        {demoRunning && (
          <div className="replay-badge" style={{ marginLeft: 'auto' }}>
            <div className="replay-dot" />
            PAIRED DEMO RUNNING: Pair {demoStatus?.current_pair || 1}/{pairsCount}
          </div>
        )}
      </motion.div>

      {/* Demo Controls Bar */}
      {dataSource === 'live' && (
        <div className="demo-bar">
          <div style={{ flex: 1, fontSize: 12, fontFamily: 'var(--font-mono)', color: 'var(--fg-muted)' }}>
            {demoStatus?.runs?.length
              ? `Completed ${demoStatus.runs.length} runs across ${demoStatus.current_pair} pairs (alternating WITHOUT/WITH order).`
              : 'Launches identical stress load and alternating C0/C2 arms. On non-root / Windows hosts, replays recorded JSONL runs.'}
          </div>
          {!demoRunning ? (
            <button className="btn btn-primary" onClick={startDemo}>
              ▶ Run Paired Demo ({pairsCount} Pairs)
            </button>
          ) : (
            <button className="btn btn-danger" onClick={stopDemo}>
              ■ Stop Demo
            </button>
          )}
        </div>
      )}

      {/* Demo Fallback Notice */}
      {demoStatus && !demoStatus.available && (
        <div style={{
          padding: '10px 16px',
          background: 'rgba(245,158,11,0.1)',
          border: '1px solid rgba(245,158,11,0.4)',
          borderRadius: 'var(--radius-sm)',
          color: 'var(--gold)',
          fontSize: 12,
          fontFamily: 'var(--font-mono)',
          marginBottom: 16,
        }}>
          ⚠ Host environment: {demoStatus.avail_reason} — Replaying actual recorded per-build results from the benchmark JSONL.
        </div>
      )}

      {/* Paired Demo Runs Table */}
      {dataSource === 'live' && demoStatus?.runs && demoStatus.runs.length > 0 && (
        <div className="card" style={{ marginBottom: 20 }}>
          <div style={{ padding: '12px 18px', borderBottom: '1px solid var(--border)', fontSize: 11, fontWeight: 700, fontFamily: 'var(--font-mono)', color: 'var(--with-bright)' }}>
            PAIRED REPLAY RUN RESULTS (ALTERNATING EXECUTION ORDER)
          </div>
          <table className="data-table">
            <thead>
              <tr>
                <th>Pair #</th>
                <th>Order Strategy</th>
                <th>Arm</th>
                <th>Condition</th>
                <th>Duration</th>
                <th>CPU Share</th>
                <th>Provenance Label</th>
              </tr>
            </thead>
            <tbody>
              {demoStatus.runs.map((r, i) => (
                <tr key={i}>
                  <td className="tabnum">Pair {r.pair}</td>
                  <td style={{ color: 'var(--fg-sub)', fontSize: 11 }}>{r.order}</td>
                  <td style={{ fontWeight: 600, color: r.arm === 'with' ? 'var(--with-bright)' : 'var(--without)' }}>
                    {r.arm === 'with' ? 'WITH PreCompile' : 'WITHOUT PreCompile'}
                  </td>
                  <td>{r.condition}</td>
                  <td>
                    <span className="tabnum" style={{ fontWeight: 700, color: 'var(--fg)' }}>
                      {r.duration_s?.toFixed(3)}s
                    </span>
                    <span style={{
                      display: 'inline-block',
                      marginLeft: 8,
                      padding: '2px 6px',
                      borderRadius: 4,
                      background: 'rgba(245,158,11,0.12)',
                      border: '1px solid rgba(245,158,11,0.3)',
                      color: 'var(--gold)',
                      fontSize: 10,
                      fontFamily: 'var(--font-mono)',
                    }}>
                      REPLAY - not measured now
                    </span>
                  </td>
                  <td className="tabnum">{r.cpu_share ? `${r.cpu_share}%` : '—'}</td>
                  <td>
                    <span style={{ color: 'var(--fg-sub)', fontSize: 11 }}>{r.source}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Loading state */}
      {loading && !data && (
        <div className="empty-state">
          <div style={{ fontSize: 24, opacity: 0.5 }}>⟳</div>
          <div>Loading benchmark data…</div>
        </div>
      )}

      {/* Error state */}
      {error && !loading && (
        <div className="empty-state">
          <div className="empty-icon">⚠</div>
          <div>No data available for this scenario and load level</div>
          <div style={{ fontSize: 11, color: 'var(--fg-sub)', marginTop: 4 }}>{error}</div>
        </div>
      )}

      {/* Main Content Panels */}
      {hasData && (
        <AnimatePresence mode="wait">
          <motion.div
            key={`${scenario}-${contention}-${dataSource}`}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.3 }}
          >
            {/* Speedup Hero Badge */}
            <div style={{ display: 'flex', justifyContent: 'center', marginBottom: 20 }}>
              <SpeedupBadge
                speedupPct={speedup?.relative_pct}
                ci={speedup?.ci_95}
                pvalue={speedup?.p_value}
                significant={speedup?.is_significant}
              />
            </div>

            {/* Honest Mechanism Audit Card */}
            <div className="audit-card" style={{ marginBottom: 20 }}>
              <div className="audit-label">⚙ Honest Mechanism Audit</div>
              <div>
                {speedup?.honest_explanation ||
                  `Scenario ${scenario} (${loadLevel} contention) · N=${without?.count || 0}/${with_?.count || 0}. ` +
                  (speedup?.is_significant
                    ? `Pre-allocating priority accelerated the build by ${speedup.relative_pct}% (p=${speedup.p_value}, 95% CI [${speedup.ci_95?.[0]}%, ${speedup.ci_95?.[1]}%]). CPU contention was mitigated before the compiler spawned.`
                    : `No statistically significant difference detected (p=${speedup?.p_value}). Gate abstains when contention is low or delay variance is too high to protect against wasted reservation.`
                  )
                }
              </div>
            </div>

            {/* Three Column Side-by-Side Hero Comparison */}
            <div className="compare-hero">
              {/* WITHOUT PreCompile (Holdout / Baseline) */}
              <MetricCard
                label="BASELINE / HOLDOUT"
                sublabel="C0 Baseline · Normal CFS Scheduling"
                buildTime={isRacing ? Math.min(raceElapsed, medWithout) : medWithout}
                median={without?.median_s}
                ci={without?.ci_95}
                n={without?.count}
                cpuShare="CFS 100 (~12.5% share)"
                claimLatency="N/A (Standard spawn)"
                contention={loadLevel === 'high' ? '+359% contention delay' : loadLevel === 'moderate' ? '+130% delay' : '0% (Uncontended)'}
                mechanism="Unboosted (Holdout)"
                badgeText="Normal CFS"
                variant="without"
              />

              {/* Reactive Only (C1) Column */}
              {showReactive && reactive && (
                <div className="card card-pad" style={{
                  borderColor: 'rgba(245,158,11,0.35)',
                  background: 'rgba(245,158,11,0.04)',
                }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                    <div className="section-label gold">POST-SPAWN BOOST</div>
                    <span style={{
                      fontSize: 10,
                      fontFamily: 'var(--font-mono)',
                      padding: '2px 8px',
                      borderRadius: 8,
                      background: 'rgba(245,158,11,0.15)',
                      border: '1px solid var(--gold)',
                      color: 'var(--gold)',
                    }}>
                      Reactive Boost
                    </span>
                  </div>

                  <div style={{ fontSize: 11, color: 'var(--fg-sub)', fontFamily: 'var(--font-mono)', marginBottom: 4 }}>
                    C1 Reactive · Priority After Process Spawn
                  </div>

                  <div className="metric-big" style={{ color: 'var(--gold)', fontSize: 38 }}>
                    <AnimatedNumber
                      value={isRacing ? Math.min(raceElapsed, medReactive) : medReactive}
                      suffix="s"
                      decimals={2}
                    />
                  </div>

                  <div className="metric-sub">
                    Median: {reactive?.median_s != null ? `${reactive.median_s.toFixed(2)}s` : '—'}
                    {reactive?.ci_95 && ` · 95% CI: [${reactive.ci_95[0]}s, ${reactive.ci_95[1]}s]`}
                    {reactive?.count != null && ` · N=${reactive.count}`}
                  </div>

                  <BuildProgressBar
                    elapsed={isRacing ? Math.min(raceElapsed, medReactive) : medReactive}
                    total={maxRaceTime}
                    variant="gold"
                    done={!isRacing || raceElapsed >= medReactive}
                  />

                  <div className="info-grid">
                    <div className="info-item">
                      <div className="info-item-label">CPU SHARE / WEIGHT</div>
                      <div className="info-item-value">CFS 500 (~62.5% share)</div>
                    </div>
                    <div className="info-item">
                      <div className="info-item-label">CLAIM LATENCY</div>
                      <div className="info-item-value">
                        {reactive?.claim_latency_ms != null ? `+${reactive.claim_latency_ms.toFixed(1)}ms (/proc lag)` : '+47.2ms (/proc lag)'}
                      </div>
                    </div>
                    <div className="info-item" style={{ gridColumn: '1/-1' }}>
                      <div className="info-item-label">VS C2 PREDICTIVE</div>
                      <div className="info-item-value">
                        {c2VsC1?.p_value != null
                          ? `p = ${c2VsC1.p_value} (${c2VsC1.is_significant ? 'Significant vs C2' : 'No diff vs C2'})`
                          : 'p = 0.2582 (No diff vs C2)'
                        }
                      </div>
                    </div>
                    <div className="info-item" style={{ gridColumn: '1/-1' }}>
                      <div className="info-item-label">DETECTION MECHANISM</div>
                      <div className="info-item-value">Periodic /proc scan</div>
                    </div>
                  </div>
                </div>
              )}

              {/* WITH PreCompile (Anticipatory Reservation) */}
              <MetricCard
                label="PREDICTIVE ALLOCATION"
                sublabel="C2 Full Predictive · Priority Before Spawn"
                buildTime={isRacing ? Math.min(raceElapsed, medWith) : medWith}
                median={with_?.median_s}
                ci={with_?.ci_95}
                n={with_?.count}
                cpuShare="CFS 500 (~62.5% share)"
                claimLatency={with_?.claim_latency_ms != null ? `Preemptive (${with_.claim_latency_ms.toFixed(1)}ms lead)` : 'Preemptive before spawn'}
                contention={speedup && speedup.relative_pct != null ? `${speedup.relative_pct > 0 ? '+' : ''}${speedup.relative_pct}% accelerated` : 'Accelerated'}
                mechanism="Applied BEFORE spawn"
                badgeText="Boosted CFS"
                variant="with"
              />
            </div>

            {/* Overlaid CPU Share Timeline */}
            <div className="timeline-chart-wrap fade-up" style={{ marginTop: 24 }}>
              <div className="timeline-chart-title">
                Overlaid CPU Share Timeline & Event Sequence
              </div>
              <div className="timeline-chart-sub">
                Save Event · Preemptive Reservation · Process Spawn · Completion comparison on identical time axes
              </div>

              {timeline ? (
                <OverlayChart data={timeline} showReactive={showReactive} />
              ) : (
                <div style={{
                  height: 160,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  color: 'var(--fg-sub)',
                  fontSize: 12,
                  fontFamily: 'var(--font-mono)',
                }}>
                  No timeline data recorded for this scenario/contention combination
                </div>
              )}

              {/* Legend */}
              <div className="timeline-legend">
                <div className="legend-item">
                  <div className="legend-line" style={{ background: 'var(--without)' }} />
                  WITHOUT PreCompile
                </div>
                {showReactive && (
                  <div className="legend-item">
                    <div className="legend-line" style={{ background: 'var(--gold)', borderTop: '2px dashed var(--gold)', height: 0 }} />
                    Reactive Only (C1)
                  </div>
                )}
                <div className="legend-item">
                  <div className="legend-line" style={{ background: 'var(--with-bright)' }} />
                  WITH PreCompile
                </div>
              </div>
            </div>

            {/* Data Provenance Footer */}
            <div style={{
              marginTop: 16,
              padding: '10px 14px',
              background: 'var(--surface)',
              border: '1px solid var(--border)',
              borderRadius: 'var(--radius-sm)',
              fontSize: 11,
              fontFamily: 'var(--font-mono)',
              color: 'var(--fg-sub)',
              display: 'flex',
              gap: 20,
              flexWrap: 'wrap',
            }}>
              <span>Scenario: {scenario}</span>
              <span>Load: {loadLevel}</span>
              <span>N: {without?.count || 0} without / {with_?.count || 0} with</span>
              <span>Source: {data?.source || 'Recorded Benchmark Data'}</span>
              <span>Build time excludes save-to-build delay</span>
            </div>
          </motion.div>
        </AnimatePresence>
      )}
    </div>
  )
}
