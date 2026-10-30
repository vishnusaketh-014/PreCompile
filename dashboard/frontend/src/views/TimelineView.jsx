import React from 'react'
import { useAPI } from '../hooks/useData.js'

export default function TimelineView() {
  const { data, loading } = useAPI('/api/timeline')

  const events = data?.events || []

  return (
    <div className="view">
      <div className="view-header">
        <div className="view-title">Prediction Timeline</div>
        <div className="view-desc">Event sequence: save → predict → reserve → spawn · All times from DB</div>
      </div>

      {loading && <div className="empty-state"><div>Loading…</div></div>}

      {!loading && events.length === 0 && (
        <div className="empty-state">
          <div className="empty-icon">📋</div>
          <div>No timeline events yet</div>
        </div>
      )}

      {events.length > 0 && (
        <div className="card">
          <table className="data-table">
            <thead>
              <tr>
                <th>Event Type</th>
                <th>Build ID</th>
                <th>t (s)</th>
                <th>Δ to spawn (ms)</th>
                <th>Prediction Confidence</th>
                <th>Outcome</th>
              </tr>
            </thead>
            <tbody>
              {events.map((ev, i) => {
                const typeColor = {
                  'save': 'var(--fg-muted)',
                  'predict': 'var(--gold)',
                  'reserve': 'var(--with-bright)',
                  'spawn': 'var(--success)',
                  'abstain': 'var(--fg-sub)',
                }[ev.type] || 'var(--fg-muted)'

                return (
                  <tr key={i}>
                    <td><span style={{ color: typeColor, fontFamily: 'var(--font-mono)' }}>{ev.type}</span></td>
                    <td className="tabnum" style={{ color: 'var(--fg-sub)' }}>{ev.build_id?.slice(-8) ?? '—'}</td>
                    <td className="tabnum">{ev.t?.toFixed(3) ?? '—'}</td>
                    <td className="tabnum">{ev.delta_ms != null ? `${ev.delta_ms.toFixed(0)}ms` : '—'}</td>
                    <td className="tabnum">{ev.confidence != null ? `${(ev.confidence * 100).toFixed(1)}%` : '—'}</td>
                    <td style={{ color: ev.outcome === 'correct' ? 'var(--success)' : ev.outcome === 'miss' ? 'var(--danger)' : 'var(--fg-muted)' }}>
                      {ev.outcome ?? '—'}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
