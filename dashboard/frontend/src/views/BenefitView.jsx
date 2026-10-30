import React from 'react'
import { useAPI } from '../hooks/useData.js'
import { AnimatedNumber } from '../components/MetricCards.jsx'

export default function BenefitView() {
  const { data, loading } = useAPI('/api/benefit')
  const b = data || {}

  return (
    <div className="view">
      <div className="view-header">
        <div className="view-title">Benefit Estimator</div>
        <div className="view-desc">Projected time savings based on measured speedup and historical build frequency</div>
      </div>

      {loading && <div className="empty-state"><div>Loading…</div></div>}

      {!loading && (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: 16 }}>
          {[
            { label: 'Avg Build Time Saved', value: b.avg_saved_s, suffix: 's', decimals: 2, color: 'var(--with-bright)', note: 'per eligible build' },
            { label: 'Savings per Day', value: b.saved_per_day_min, suffix: ' min', decimals: 1, color: 'var(--success)', note: 'projected' },
            { label: 'Savings per Week', value: b.saved_per_week_min, suffix: ' min', decimals: 0, color: 'var(--success)', note: 'projected' },
            { label: 'Eligible Build Rate', value: b.eligible_pct, suffix: '%', decimals: 1, color: 'var(--gold)', note: 'gate passes' },
            { label: 'Reservation Success', value: b.reservation_success_pct, suffix: '%', decimals: 1, color: 'var(--with-bright)', note: 'of eligible' },
            { label: 'Net Speedup (median)', value: b.net_speedup_pct, suffix: '%', decimals: 1, color: 'var(--with-bright)', note: 'with vs without' },
          ].map((item, i) => (
            <div key={i} className="card card-pad fade-up" style={{ animationDelay: `${i * 60}ms` }}>
              <div className="info-item-label" style={{ marginBottom: 8 }}>{item.label}</div>
              <div style={{ fontSize: 34, fontWeight: 800, fontFamily: 'var(--font-mono)', color: item.color }}>
                {item.value != null
                  ? <AnimatedNumber value={item.value} suffix={item.suffix} decimals={item.decimals} />
                  : '—'
                }
              </div>
              <div className="metric-sub" style={{ marginTop: 4 }}>{item.note}</div>
            </div>
          ))}
        </div>
      )}

      {!loading && b.scenarios && (
        <div className="card" style={{ marginTop: 20 }}>
          <div style={{ padding: '14px 18px', borderBottom: '1px solid var(--border)', fontSize: 12, fontWeight: 600, fontFamily: 'var(--font-mono)', color: 'var(--fg-muted)' }}>
            Benefit by Scenario
          </div>
          <table className="data-table">
            <thead>
              <tr>
                <th>Scenario</th>
                <th>Contention</th>
                <th>Speedup</th>
                <th>p-value</th>
                <th>N</th>
                <th>Eligible?</th>
              </tr>
            </thead>
            <tbody>
              {b.scenarios.map((s, i) => (
                <tr key={i}>
                  <td>{s.scenario}</td>
                  <td>{s.contention}</td>
                  <td className="tabnum" style={{ color: s.speedup_pct > 0 ? 'var(--success)' : 'var(--fg-muted)' }}>
                    {s.speedup_pct != null ? `+${s.speedup_pct.toFixed(1)}%` : '—'}
                  </td>
                  <td className="tabnum">{s.p_value?.toFixed(4) ?? '—'}</td>
                  <td className="tabnum">{s.n ?? '—'}</td>
                  <td style={{ color: s.significant ? 'var(--success)' : 'var(--fg-sub)' }}>
                    {s.significant ? '✓' : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
