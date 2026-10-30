import React, { useEffect, useRef } from 'react'
import { motion, useMotionValue, animate } from 'framer-motion'

/**
 * AnimatedNumber — smoothly counts up to a target value.
 */
export function AnimatedNumber({ value, suffix = '', prefix = '', decimals = 2, className = '' }) {
  const ref = useRef(null)
  const motionVal = useMotionValue(0)

  useEffect(() => {
    if (value == null || isNaN(value)) return
    const controls = animate(motionVal, value, {
      duration: 1.0,
      ease: [0.4, 0, 0.2, 1],
    })
    const unsub = motionVal.on('change', (v) => {
      if (ref.current) {
        ref.current.textContent = `${prefix}${v.toFixed(decimals)}${suffix}`
      }
    })
    return () => { controls.stop(); unsub() }
  }, [value, suffix, prefix, decimals])

  return (
    <span ref={ref} className={`tabnum ${className}`}>
      {prefix}{(value ?? 0).toFixed(decimals)}{suffix}
    </span>
  )
}

/**
 * BuildProgressBar — animated progress track.
 */
export function BuildProgressBar({ elapsed, total, variant = 'without', label = '', done = false }) {
  const pct = total > 0 ? Math.min(100, (elapsed / total) * 100) : (done ? 100 : 0)

  return (
    <div className="build-progress-wrap">
      <div className="progress-label">
        <span>{label || 'Build Execution Progress'}</span>
        <span className="tabnum">{typeof elapsed === 'number' ? elapsed.toFixed(2) : elapsed}s / {typeof total === 'number' ? total.toFixed(2) : total}s</span>
      </div>
      <div className="progress-track">
        <motion.div
          className={`progress-fill ${variant}`}
          initial={{ width: '0%' }}
          animate={{ width: `${pct}%` }}
          transition={{ duration: 0.6, ease: [0.4, 0, 0.2, 1] }}
        />
      </div>
      {done && (
        <div className="progress-done-label">
          ✦ COMPLETED in {typeof elapsed === 'number' ? elapsed.toFixed(2) : elapsed}s
        </div>
      )}
      {!done && elapsed > 0 && (
        <div className="progress-done-label" style={{ color: 'var(--fg-sub)' }}>
          Building under contention...
        </div>
      )}
    </div>
  )
}

/**
 * SpeedupBadge — central hero number showing relative improvement.
 */
export function SpeedupBadge({ speedupPct, ci, pvalue, significant }) {
  if (!significant) {
    return (
      <div className="speedup-badge not-significant">
        <div className="speedup-number">≈ Same</div>
        <div className="speedup-label">No Significant Difference</div>
        <div className="speedup-ci" style={{ color: 'var(--fg-sub)' }}>
          p = {pvalue != null ? pvalue.toFixed(4) : '—'} · (Gate Abstains / No Contention)
        </div>
      </div>
    )
  }

  const num = typeof speedupPct === 'number' ? speedupPct.toFixed(1) : speedupPct

  return (
    <motion.div
      className="speedup-badge"
      initial={{ scale: 0.85, opacity: 0 }}
      animate={{ scale: 1, opacity: 1 }}
      transition={{ duration: 0.5, ease: [0.34, 1.56, 0.64, 1] }}
    >
      <div className="speedup-number">+{num}%</div>
      <div className="speedup-label">Speedup vs Baseline</div>
      {ci && (
        <div className="speedup-ci">
          95% Bootstrap CI: [{ci[0]}%, {ci[1]}%]
        </div>
      )}
      {pvalue != null && (
        <div className="speedup-ci" style={{ marginTop: 4 }}>
          Mann-Whitney U: p {pvalue < 0.0001 ? '< 0.0001' : `= ${pvalue.toFixed(4)}`} (Highly Significant)
        </div>
      )}
    </motion.div>
  )
}

/**
 * MetricCard — big-number card for WITHOUT or WITH column.
 */
export function MetricCard({
  label,
  sublabel,
  buildTime,
  median,
  ci,
  cpuShare,
  claimLatency,
  mechanism,
  contention,
  variant,
  n,
  badgeText,
}) {
  return (
    <motion.div
      className={`card card-pad-lg ${variant}-card fade-up`}
      initial={{ opacity: 0, y: 15 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4 }}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
        <div className={`section-label ${variant}`}>{label}</div>
        {badgeText && (
          <span style={{
            fontSize: 10,
            fontFamily: 'var(--font-mono)',
            padding: '2px 8px',
            borderRadius: 8,
            background: variant === 'with' ? 'rgba(124,58,237,0.15)' : 'rgba(74,111,165,0.15)',
            border: `1px solid ${variant === 'with' ? 'var(--with)' : 'var(--without)'}`,
            color: variant === 'with' ? 'var(--with-bright)' : 'var(--without)',
          }}>
            {badgeText}
          </span>
        )}
      </div>

      <div style={{ fontSize: 11, color: 'var(--fg-sub)', fontFamily: 'var(--font-mono)', marginBottom: 4 }}>
        {sublabel}
      </div>

      <div className={`metric-big ${variant}`}>
        <AnimatedNumber value={buildTime} suffix="s" decimals={2} />
      </div>

      <div className="metric-sub">
        Median: {median != null ? `${median.toFixed(2)}s` : '—'}
        {ci && ` · 95% CI: [${ci[0]}s, ${ci[1]}s]`}
        {n != null && ` · N=${n}`}
      </div>

      {/* Progress bar */}
      <BuildProgressBar
        elapsed={buildTime ?? 0}
        total={Math.max(buildTime ?? 0, 10)}
        variant={variant}
        done={true}
      />

      {/* CPU / latency info grid */}
      <div className="info-grid">
        <div className="info-item">
          <div className="info-item-label">CPU SHARE / WEIGHT</div>
          <div className="info-item-value">{cpuShare ?? '—'}</div>
        </div>
        <div className="info-item">
          <div className="info-item-label">CLAIM LATENCY</div>
          <div className="info-item-value">{claimLatency ?? '—'}</div>
        </div>
        {contention && (
          <div className="info-item">
            <div className="info-item-label">CONTENTION IMPACT</div>
            <div className="info-item-value" style={{ color: 'var(--fg-muted)' }}>{contention}</div>
          </div>
        )}
        {mechanism && (
          <div className="info-item">
            <div className="info-item-label">RESERVATION STATE</div>
            <div className="info-item-value" style={{ color: 'var(--fg-muted)' }}>{mechanism}</div>
          </div>
        )}
      </div>
    </motion.div>
  )
}
