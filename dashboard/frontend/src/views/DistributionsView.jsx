import React from 'react'
import { useAPI } from '../hooks/useData.js'
import { AnimatedNumber } from '../components/MetricCards.jsx'

export default function DistributionsView() {
  const { data, loading } = useAPI('/api/distributions')
  const tiers = data?.tiers || []

  return (
    <div className="view">
      <div className="view-header">
        <div className="view-title">Reserved vs Holdout</div>
        <div className="view-desc">Build time distributions — reserved (WITH) vs holdout (WITHOUT) per tier</div>
      </div>

      {loading && <div className="empty-state"><div>Loading…</div></div>}

      {!loading && tiers.length === 0 && (
        <div className="empty-state">
          <div className="empty-icon">📊</div>
          <div>No distribution data yet</div>
        </div>
      )}

      <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
        {tiers.map((tier, i) => (
          <div key={i} className="card card-pad">
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 16 }}>
              <div>
                <div className="section-label muted">{tier.label || `Tier ${i+1}`}</div>
                <div style={{ fontSize: 12, color: 'var(--fg-sub)', fontFamily: 'var(--font-mono)', marginTop: 2 }}>
                  N={tier.n_reserved ?? '—'} reserved · N={tier.n_holdout ?? '—'} holdout
                </div>
              </div>
              <div className={`significance-pill ${tier.significant ? 'sig' : 'not-sig'}`}>
                {tier.significant ? '✓ Significant' : 'Not Significant'}
              </div>
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
              <div>
                <div className="section-label without" style={{ marginBottom: 8 }}>WITHOUT (Holdout)</div>
                <div style={{ fontSize: 28, fontWeight: 800, fontFamily: 'var(--font-mono)', color: 'var(--without)' }}>
                  {tier.holdout_median != null
                    ? <AnimatedNumber value={tier.holdout_median} suffix="s" decimals={2} />
                    : '—'
                  }
                </div>
                <div className="metric-sub">
                  Median · CI [{tier.holdout_ci?.[0]?.toFixed(2) ?? '—'}, {tier.holdout_ci?.[1]?.toFixed(2) ?? '—'}]s
                </div>
              </div>
              <div>
                <div className="section-label with" style={{ marginBottom: 8 }}>WITH (Reserved)</div>
                <div style={{ fontSize: 28, fontWeight: 800, fontFamily: 'var(--font-mono)', color: 'var(--with-bright)' }}>
                  {tier.reserved_median != null
                    ? <AnimatedNumber value={tier.reserved_median} suffix="s" decimals={2} />
                    : '—'
                  }
                </div>
                <div className="metric-sub">
                  Median · CI [{tier.reserved_ci?.[0]?.toFixed(2) ?? '—'}, {tier.reserved_ci?.[1]?.toFixed(2) ?? '—'}]s
                </div>
              </div>
            </div>

            {tier.speedup_pct != null && (
              <div style={{ marginTop: 12, padding: '8px 12px', background: 'var(--surface)', borderRadius: 'var(--radius-sm)', fontSize: 12, fontFamily: 'var(--font-mono)', color: 'var(--fg-muted)' }}>
                Speedup: <span style={{ color: tier.significant ? 'var(--success)' : 'var(--fg-muted)' }}>
                  {tier.speedup_pct > 0 ? '+' : ''}{tier.speedup_pct.toFixed(1)}%
                </span>
                {tier.p_value != null && ` · p = ${tier.p_value.toFixed(4)}`}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>
  )
}
