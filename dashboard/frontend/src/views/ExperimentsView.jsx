import React from 'react'
import { motion } from 'framer-motion'
import { useAPI } from '../hooks/useData.js'

export default function ExperimentsView() {
  const { data, loading } = useAPI('/api/experiments')

  if (loading && !data) {
    return (
      <div className="view">
        <div className="view-header">
          <div className="view-title">Experiments Evaluation</div>
        </div>
        <div className="empty-state"><div>Loading evaluation matrix…</div></div>
      </div>
    )
  }

  const criteria = data?.criteria_status || []
  const matrix = data?.conditions_matrix || []
  const ablations = data?.ablations || []

  return (
    <div className="view">
      <div className="view-header">
        <div className="view-title">Experiments & Success Criteria</div>
        <div className="view-desc">
          Pre-registered evaluation results (S1–S7) across all scenario × contention × mode combinations
        </div>
      </div>

      {/* S1 - S7 Success Criteria Matrix */}
      <div className="card" style={{ marginBottom: 24 }}>
        <div style={{
          padding: '16px 20px',
          borderBottom: '1px solid var(--border)',
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
        }}>
          <div>
            <div className="section-label with">PRE-REGISTERED SUCCESS CRITERIA (S1 - S7)</div>
            <div style={{ fontSize: 11, color: 'var(--fg-sub)', fontFamily: 'var(--font-mono)' }}>
              Evaluation against specification targets with equal visual weight for PASS and FAIL
            </div>
          </div>
        </div>

        <table className="data-table">
          <thead>
            <tr>
              <th style={{ width: '22%' }}>Criterion</th>
              <th style={{ width: '22%' }}>Target</th>
              <th style={{ width: '24%' }}>Observed Result</th>
              <th style={{ width: '12%' }}>Status</th>
              <th style={{ width: '20%' }}>Honest Mechanism Audit</th>
            </tr>
          </thead>
          <tbody>
            {criteria.map((c, i) => {
              const isPass = c.status === 'PASS'
              const isFail = c.status === 'FAIL'
              const isInvalid = c.status?.includes('INVALID')

              const statusColor = isPass ? 'var(--success)' : isFail ? 'var(--danger)' : isInvalid ? 'var(--gold)' : 'var(--with-bright)'
              const statusBg = isPass ? 'rgba(16,185,129,0.12)' : isFail ? 'rgba(239,68,68,0.12)' : isInvalid ? 'rgba(245,158,11,0.12)' : 'rgba(124,58,237,0.12)'
              const statusBorder = isPass ? 'rgba(16,185,129,0.4)' : isFail ? 'rgba(239,68,68,0.4)' : isInvalid ? 'rgba(245,158,11,0.4)' : 'rgba(124,58,237,0.4)'

              return (
                <tr key={i} style={isFail ? { background: 'rgba(239,68,68,0.03)' } : {}}>
                  <td style={{ fontWeight: 600, color: 'var(--fg)' }}>{c.criterion}</td>
                  <td style={{ color: 'var(--fg-muted)', fontSize: 11 }}>{c.target}</td>
                  <td className="tabnum" style={{ color: 'var(--fg)' }}>{c.observed}</td>
                  <td>
                    <span style={{
                      display: 'inline-block',
                      padding: '3px 10px',
                      borderRadius: 10,
                      fontSize: 10,
                      fontWeight: 700,
                      fontFamily: 'var(--font-mono)',
                      color: statusColor,
                      background: statusBg,
                      border: `1px solid ${statusBorder}`,
                    }}>
                      {c.status}
                    </span>
                  </td>
                  <td style={{ color: 'var(--fg-sub)', fontSize: 11 }}>{c.honest_note}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>

      {/* Conditions Matrix Table */}
      {matrix.length > 0 && (
        <div className="card" style={{ marginBottom: 24 }}>
          <div style={{ padding: '16px 20px', borderBottom: '1px solid var(--border)' }}>
            <div className="section-label without">PER-CELL BUILD PERFORMANCE MATRIX</div>
            <div style={{ fontSize: 11, color: 'var(--fg-sub)', fontFamily: 'var(--font-mono)' }}>
              Intention-to-Treat (ITT) medians, means, and percentiles across all tested workloads
            </div>
          </div>

          <table className="data-table">
            <thead>
              <tr>
                <th>Tier</th>
                <th>Scenario</th>
                <th>Load</th>
                <th>Condition</th>
                <th>N</th>
                <th>Median (s)</th>
                <th>Mean (s)</th>
                <th>P95 (s)</th>
                <th>Speedup vs C0</th>
              </tr>
            </thead>
            <tbody>
              {matrix.map((row, i) => {
                const condColor = row.condition?.includes('c2') ? 'var(--with-bright)' : row.condition?.includes('c1') ? 'var(--gold)' : 'var(--without)'
                return (
                  <tr key={i}>
                    <td>{row.tier}</td>
                    <td>{row.scenario}</td>
                    <td>{row.load}</td>
                    <td style={{ color: condColor, fontWeight: 600 }}>{row.condition}</td>
                    <td className="tabnum">{row.n}</td>
                    <td className="tabnum">{row.median_s?.toFixed(3)}s</td>
                    <td className="tabnum">{row.mean_s?.toFixed(3)}s</td>
                    <td className="tabnum">{row.p95_s?.toFixed(3)}s</td>
                    <td className="tabnum" style={{
                      color: row.speedup_pct > 0 ? 'var(--success)' : row.speedup_pct < 0 ? 'var(--danger)' : 'var(--fg-sub)'
                    }}>
                      {row.speedup_pct != null ? `${row.speedup_pct > 0 ? '+' : ''}${row.speedup_pct.toFixed(1)}%` : '—'}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* Ablation Studies */}
      {ablations.length > 0 && (
        <div className="card">
          <div style={{ padding: '16px 20px', borderBottom: '1px solid var(--border)' }}>
            <div className="section-label gold">ABLATION STUDIES (A2 - A5)</div>
            <div style={{ fontSize: 11, color: 'var(--fg-sub)', fontFamily: 'var(--font-mono)' }}>
              Component isolation: isolating the value of the confidence gate, contention gate, and holdouts
            </div>
          </div>

          <table className="data-table">
            <thead>
              <tr>
                <th>Ablation</th>
                <th>Description</th>
                <th>Median (s)</th>
                <th>Observed Outcome</th>
                <th>Conclusion</th>
              </tr>
            </thead>
            <tbody>
              {ablations.map((ab, i) => (
                <tr key={i}>
                  <td style={{ fontWeight: 600, color: 'var(--gold)' }}>{ab.code}</td>
                  <td style={{ color: 'var(--fg-muted)' }}>{ab.description}</td>
                  <td className="tabnum">{ab.median_s?.toFixed(3)}s</td>
                  <td>{ab.outcome}</td>
                  <td style={{ color: 'var(--fg-sub)', fontSize: 11 }}>{ab.conclusion}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
