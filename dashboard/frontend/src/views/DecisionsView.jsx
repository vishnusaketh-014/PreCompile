import React from 'react'
import { useAPI } from '../hooks/useData.js'

export default function DecisionsView() {
  const { data, loading } = useAPI('/api/decisions')
  const decisions = data?.decisions || []

  const decisionColor = {
    'reserve': 'var(--with-bright)',
    'abstain_no_contention': 'var(--fg-sub)',
    'abstain_contention': 'var(--gold)',
    'abstain': 'var(--fg-sub)',
    'holdout': 'var(--without)',
    'safety_trip': 'var(--danger)',
    'error': 'var(--danger)',
  }

  return (
    <div className="view">
      <div className="view-header">
        <div className="view-title">Decision Log</div>
        <div className="view-desc">Every gate decision with reason, confidence, and outcome</div>
      </div>

      {loading && <div className="empty-state"><div>Loading…</div></div>}

      {!loading && decisions.length === 0 && (
        <div className="empty-state">
          <div className="empty-icon">📝</div>
          <div>No decisions logged yet</div>
        </div>
      )}

      {decisions.length > 0 && (
        <div className="card">
          <table className="data-table">
            <thead>
              <tr>
                <th>Timestamp</th>
                <th>Build ID</th>
                <th>Decision</th>
                <th>Reason</th>
                <th>Confidence</th>
                <th>Lead Time</th>
                <th>Outcome</th>
              </tr>
            </thead>
            <tbody>
              {decisions.map((d, i) => (
                <tr key={i}>
                  <td className="tabnum" style={{ color: 'var(--fg-sub)', fontSize: 11 }}>{d.timestamp}</td>
                  <td className="tabnum" style={{ color: 'var(--fg-sub)' }}>{d.build_id?.slice(-8) ?? '—'}</td>
                  <td style={{ color: decisionColor[d.decision] || 'var(--fg-muted)', fontWeight: 600 }}>
                    {d.decision}
                  </td>
                  <td style={{ color: 'var(--fg-muted)', fontSize: 11 }}>{d.reason ?? '—'}</td>
                  <td className="tabnum">
                    {d.confidence != null ? `${(d.confidence * 100).toFixed(1)}%` : '—'}
                  </td>
                  <td className="tabnum">
                    {d.lead_time_ms != null ? `${d.lead_time_ms.toFixed(0)}ms` : '—'}
                  </td>
                  <td style={{ color: d.outcome === 'correct' ? 'var(--success)' : d.outcome === 'miss' ? 'var(--danger)' : 'var(--fg-muted)' }}>
                    {d.outcome ?? '—'}
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
