import React from 'react'
import { motion } from 'framer-motion'
import { useAPI } from '../hooks/useData.js'

export default function OverviewView({ tick }) {
  const { data, loading } = useAPI('/api/overview')
  const live = tick?.overview || data

  if (loading && !live) {
    return (
      <div className="view">
        <div className="view-header">
          <div className="view-title">Live Overview</div>
        </div>
        <div className="empty-state"><div>Loading…</div></div>
      </div>
    )
  }

  const sm = live?.state_machine || {
    current_state: 'Idle',
    states: ['Idle', 'Observing', 'Predicted', 'Armed', 'Reserved', 'Released'],
    active_index: 0,
    reason: 'Observation mode',
  }

  const tiles = live?.tiles || {}

  return (
    <div className="view">
      <div className="view-header">
        <div className="view-title">Live Overview</div>
        <div className="view-desc">Real-time system state · Polled at 1.0 Hz from daemon DB</div>
      </div>

      {/* State Machine Flow */}
      <div className="card card-pad" style={{ marginBottom: 20 }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 14 }}>
          <div className="section-label with">DAEMON LIFECYCLE STATE</div>
          <span style={{ fontSize: 11, fontFamily: 'var(--font-mono)', color: 'var(--fg-sub)' }}>
            {sm.reason || 'Active state machine'}
          </span>
        </div>

        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          {(sm.states || ['Idle', 'Observing', 'Predicted', 'Armed', 'Reserved', 'Released']).map((st, i) => {
            const isActive = st.toLowerCase() === (sm.current_state || '').toLowerCase()
            return (
              <React.Fragment key={st}>
                <div style={{
                  padding: '6px 14px',
                  borderRadius: 16,
                  fontSize: 12,
                  fontFamily: 'var(--font-mono)',
                  fontWeight: 600,
                  background: isActive ? 'linear-gradient(135deg, rgba(124,58,237,0.3), rgba(148,97,255,0.15))' : 'var(--surface)',
                  border: `1px solid ${isActive ? 'var(--with-bright)' : 'var(--border)'}`,
                  color: isActive ? 'var(--with-bright)' : 'var(--fg-sub)',
                  boxShadow: isActive ? '0 0 15px var(--with-glow)' : 'none',
                }}>
                  {st}
                </div>
                {i < (sm.states?.length || 6) - 1 && (
                  <span style={{ color: 'var(--border-bright)', fontSize: 12 }}>→</span>
                )}
              </React.Fragment>
            )
          })}
        </div>
      </div>

      {/* 5 Core Status Tiles */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: 16 }}>
        {/* Confidence */}
        <div className="card card-pad fade-up">
          <div className="info-item-label">PREDICTOR CONFIDENCE</div>
          <div style={{ fontSize: 32, fontWeight: 800, fontFamily: 'var(--font-mono)', color: 'var(--with-bright)', margin: '8px 0 4px' }}>
            {tiles.confidence?.value || 'N/A'}
          </div>
          <div className="metric-sub">{tiles.confidence?.subtext || 'no data yet'}</div>
          <div style={{ fontSize: 10, color: 'var(--fg-sub)', fontFamily: 'var(--font-mono)', marginTop: 8 }}>
            Gate: {tiles.confidence?.gate || 'HOLD'} · {tiles.confidence?.detail || ''}
          </div>
        </div>

        {/* Delay Calibration */}
        <div className="card card-pad fade-up" style={{ animationDelay: '50ms' }}>
          <div className="info-item-label">SAVE-TO-BUILD DELAY</div>
          <div style={{ fontSize: 32, fontWeight: 800, fontFamily: 'var(--font-mono)', color: 'var(--gold)', margin: '8px 0 4px' }}>
            {tiles.delay?.value || 'N/A'}
          </div>
          <div className="metric-sub">{tiles.delay?.subtext || 'uncalibrated'}</div>
          <div style={{ fontSize: 10, color: 'var(--fg-sub)', fontFamily: 'var(--font-mono)', marginTop: 8 }}>
            Window: {tiles.delay?.window || '[0.00s, 0.00s]'}
          </div>
        </div>

        {/* Current Weight */}
        <div className="card card-pad fade-up" style={{ animationDelay: '100ms' }}>
          <div className="info-item-label">CGROUP CPU WEIGHT</div>
          <div style={{ fontSize: 32, fontWeight: 800, fontFamily: 'var(--font-mono)', color: 'var(--fg)', margin: '8px 0 4px' }}>
            {tiles.weight?.value || '100'}
          </div>
          <div className="metric-sub">{tiles.weight?.subtext || 'Default CFS weight'}</div>
          <div style={{ fontSize: 10, color: 'var(--fg-sub)', fontFamily: 'var(--font-mono)', marginTop: 8 }}>
            {tiles.weight?.detail || 'No active reservation'}
          </div>
        </div>

        {/* CPU Pressure */}
        <div className="card card-pad fade-up" style={{ animationDelay: '150ms' }}>
          <div className="info-item-label">CPU PSI PRESSURE</div>
          <div style={{ fontSize: 32, fontWeight: 800, fontFamily: 'var(--font-mono)', color: 'var(--without)', margin: '8px 0 4px' }}>
            {tiles.psi_pressure?.value || '0.0%'}
          </div>
          <div className="metric-sub">{tiles.psi_pressure?.subtext || 'No CPU contention'}</div>
          <div style={{ fontSize: 10, color: 'var(--fg-sub)', fontFamily: 'var(--font-mono)', marginTop: 8 }}>
            Status: {tiles.psi_pressure?.status || 'Idle / Low'}
          </div>
        </div>

        {/* Safety Governor */}
        <div className="card card-pad fade-up" style={{ animationDelay: '200ms' }}>
          <div className="info-item-label">SAFETY GOVERNOR</div>
          <div style={{ fontSize: 32, fontWeight: 800, fontFamily: 'var(--font-mono)', color: tiles.safety?.trip ? 'var(--danger)' : 'var(--success)', margin: '8px 0 4px' }}>
            {tiles.safety?.value || 'NORMAL'}
          </div>
          <div className="metric-sub">{tiles.safety?.subtext || 'All checks green'}</div>
          <div style={{ fontSize: 10, color: 'var(--fg-sub)', fontFamily: 'var(--font-mono)', marginTop: 8 }}>
            {tiles.safety?.detail || 'Boost budget healthy'}
          </div>
        </div>
      </div>

      {/* Projects summary */}
      <div style={{
        marginTop: 20,
        padding: '12px 16px',
        background: 'var(--surface)',
        border: '1px solid var(--border)',
        borderRadius: 'var(--radius-sm)',
        fontSize: 12,
        fontFamily: 'var(--font-mono)',
        color: 'var(--fg-sub)',
        display: 'flex',
        gap: 20,
      }}>
        <span>Registered Projects: {live?.projects_summary?.total || 0}</span>
        <span>Enabled Projects: {live?.projects_summary?.enabled || 0}</span>
        <span>Database Mode: READ-ONLY</span>
      </div>
    </div>
  )
}
