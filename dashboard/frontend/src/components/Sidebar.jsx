import React from 'react'

const NAV_ITEMS = [
  { view: 'compare', label: 'Compare (A/B)', key: '1' },
  { view: 'overview', label: 'Live Overview', key: '2' },
  { view: 'timeline', label: 'Prediction Timeline', key: '3' },
  { view: 'distributions', label: 'Reserved vs Holdout', key: '4' },
  { view: 'benefit', label: 'Benefit Estimator', key: '5' },
  { view: 'safety', label: 'Safety Governor', key: '6' },
  { view: 'experiments', label: 'Experiments', key: '7' },
  { view: 'decisions', label: 'Decision Log', key: '8' },
]

export default function Sidebar({
  currentView,
  onNavigate,
  connected,
  presentationMode,
  onTogglePresentation,
  performanceMode,
  onTogglePerformance,
  replayDataset,
  onChangeReplayDataset,
}) {
  return (
    <aside className="sidebar">
      {/* Brand */}
      <div className="brand">
        <div className="brand-logo">
          <div className="brand-icon">⚡</div>
          <div className="brand-name">PreCompile</div>
          <div className="brand-version">v1.5</div>
        </div>
        <div className="brand-sub">Anticipatory Priority Reservation</div>
      </div>

      {/* Persistent LIVE / REPLAY Badge (Item 8) */}
      <div style={{ margin: '12px 14px 4px' }}>
        <div className="source-indicator" style={{ margin: 0, justifyContent: 'space-between' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <div className={`source-dot ${connected ? '' : 'offline'}`} />
            <span style={{ fontSize: 11, fontFamily: 'var(--font-mono)', fontWeight: 600 }}>
              {connected ? 'LIVE: SQLite (1.0 Hz)' : 'REPLAY / OFFLINE'}
            </span>
          </div>
        </div>

        {/* Replay dataset selector */}
        <div style={{ marginTop: 6 }}>
          <select
            className="selector-select"
            style={{ width: '100%', fontSize: 10, padding: '4px 6px' }}
            value={replayDataset}
            onChange={e => onChangeReplayDataset(e.target.value)}
          >
            <option value="results_all">Dataset: All Benchmarks</option>
            <option value="results_tier1">Replay: Tier 1 (Small C/C++)</option>
            <option value="results_tier2">Replay: Tier 2 (Heavy C/C++)</option>
            <option value="results_tier3">Replay: Tier 3 (Rust)</option>
          </select>
        </div>
      </div>

      {/* Navigation */}
      <div className="nav-section-label" style={{ marginTop: 8 }}>Views</div>
      <ul className="nav-list">
        {NAV_ITEMS.map(item => (
          <li
            key={item.view}
            className={`nav-item ${currentView === item.view ? 'active' : ''}`}
            onClick={() => onNavigate(item.view)}
          >
            <div className="nav-bullet" />
            <span style={{ flex: 1 }}>{item.label}</span>
            <span className="nav-shortcut">{item.key}</span>
          </li>
        ))}
      </ul>

      {/* Bottom Controls: Performance Mode & Presentation Mode */}
      <div className="nav-bottom">
        {/* Performance Mode Toggle Button (Item 7) */}
        <button
          className={`pmode-btn ${performanceMode ? 'active' : ''}`}
          onClick={onTogglePerformance}
          style={{ marginBottom: 6, fontSize: 11 }}
          title="Disables WebGL background and heavy animations to reduce CPU load to 0%"
        >
          {performanceMode ? '⚡ Performance Mode: ON' : '✨ WebGL Effects: ON'}
        </button>

        {/* Presentation Mode Toggle Button (Item 9) */}
        <button
          className={`pmode-btn ${presentationMode ? 'active' : ''}`}
          onClick={onTogglePresentation}
        >
          {presentationMode ? '⬛ Exit Presentation' : '▣ Presentation Mode [P]'}
        </button>

        <div style={{ marginTop: 8, fontSize: 10, color: 'var(--fg-sub)', fontFamily: 'var(--font-mono)', textAlign: 'center' }}>
          Read-Only · 1.0 Hz Polling
        </div>
      </div>
    </aside>
  )
}
