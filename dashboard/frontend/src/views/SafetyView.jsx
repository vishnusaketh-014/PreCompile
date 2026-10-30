import React from 'react'
import { useAPI } from '../hooks/useData.js'

export default function SafetyView() {
  const { data, loading } = useAPI('/api/safety')
  const s = data || {}

  const statusColor = s.status === 'ok' ? 'ok' : s.status === 'tripped' ? 'danger' : 'warn'

  return (
    <div className="view">
      <div className="view-header">
        <div className="view-title">Safety Governor</div>
        <div className="view-desc">Reservation rate limiter · Trip log · All actions from DB</div>
      </div>

      {loading && <div className="empty-state"><div>Loading…</div></div>}

      {!loading && (
        <>
          <div style={{ display: 'flex', gap: 16, alignItems: 'center', marginBottom: 20 }}>
            <div className={`safety-chip ${statusColor}`}>
              {s.status === 'ok' ? '✓ OPERATING NORMALLY' : s.status === 'tripped' ? '⚠ SAFETY TRIPPED' : '⚠ WARNING'}
            </div>
            {s.armed !== undefined && (
              <span style={{ fontSize: 12, fontFamily: 'var(--font-mono)', color: 'var(--fg-muted)' }}>
                Armed: {s.armed ? 'YES' : 'NO'}
              </span>
            )}
          </div>

          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))', gap: 14, marginBottom: 20 }}>
            {[
              { label: 'Total Trips', value: s.total_trips, danger: true },
              { label: 'Trips Last Hour', value: s.trips_last_hour, danger: true },
              { label: 'Reservations Applied', value: s.total_reservations },
              { label: 'Trip Rate', value: s.trip_rate_pct != null ? `${s.trip_rate_pct.toFixed(2)}%` : '—' },
              { label: 'Max Reservation Rate', value: s.max_reservation_rate_pct != null ? `${s.max_reservation_rate_pct.toFixed(0)}%` : '—' },
              { label: 'Current Window', value: s.current_window_s != null ? `${s.current_window_s}s` : '—' },
            ].map((item, i) => (
              <div key={i} className="card card-pad" style={item.danger && item.value > 0 ? { borderColor: 'rgba(239,68,68,0.4)', background: 'rgba(239,68,68,0.05)' } : {}}>
                <div className="info-item-label">{item.label}</div>
                <div style={{ fontSize: 24, fontWeight: 700, fontFamily: 'var(--font-mono)', marginTop: 6, color: item.danger && item.value > 0 ? 'var(--danger)' : 'var(--fg)' }}>
                  {item.value ?? '—'}
                </div>
              </div>
            ))}
          </div>

          {s.trip_log && s.trip_log.length > 0 ? (
            <div className="card">
              <div style={{ padding: '14px 18px', borderBottom: '1px solid var(--border)', fontSize: 12, fontWeight: 600, fontFamily: 'var(--font-mono)', color: 'var(--danger)' }}>
                Trip Log
              </div>
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Timestamp</th>
                    <th>Reason</th>
                    <th>Build ID</th>
                    <th>Metric</th>
                  </tr>
                </thead>
                <tbody>
                  {s.trip_log.map((t, i) => (
                    <tr key={i}>
                      <td className="tabnum">{t.timestamp}</td>
                      <td style={{ color: 'var(--danger)' }}>{t.reason}</td>
                      <td className="tabnum" style={{ color: 'var(--fg-sub)' }}>{t.build_id?.slice(-8) ?? '—'}</td>
                      <td className="tabnum">{t.metric ?? '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : !loading && (
            <div className="card card-pad" style={{ textAlign: 'center', padding: 40, borderColor: 'rgba(16,185,129,0.3)' }}>
              <div style={{ fontSize: 32, marginBottom: 12 }}>✓</div>
              <div style={{ fontSize: 14, color: 'var(--success)', fontFamily: 'var(--font-mono)' }}>No safety trips recorded</div>
            </div>
          )}
        </>
      )}
    </div>
  )
}
