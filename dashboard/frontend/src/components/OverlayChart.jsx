import React, { useRef, useEffect } from 'react'

const COLOR_RESERVED = '#9461FF'
const COLOR_HOLDOUT = '#4A6FA5'
const COLOR_REACTIVE = '#F59E0B'
const COLOR_BORDER = '#1E2D40'
const COLOR_TEXT = '#94A3B8'

/**
 * OverlayChart — Renders the overlaid CPU share timeline matching overlay_timeline traces & events.
 */
export default function OverlayChart({ data, showReactive = true }) {
  const containerRef = useRef(null)

  useEffect(() => {
    const container = containerRef.current
    if (!container) return
    container.innerHTML = ''

    if (!data || !data.traces) {
      container.innerHTML = `
        <div style="height: 160px; display: flex; align-items: center; justify-content: center; color: var(--fg-sub); font-size: 12px; font-family: var(--font-mono);">
          No trace points recorded for this configuration.
        </div>
      `
      return
    }

    const rect = container.getBoundingClientRect()
    const width = Math.max(rect.width || 800, 680)
    const height = 280
    const padding = { top: 40, right: 30, bottom: 45, left: 65 }
    const chartW = width - padding.left - padding.right
    const chartH = height - padding.top - padding.bottom

    const tMax = Math.max(data.t_max || 12.0, 6.0)
    const tMin = 0.0
    const shareMax = 75.0 // 0% to 75% CPU Share

    function scaleX(t) {
      return padding.left + ((t - tMin) / (tMax - tMin)) * chartW
    }

    function scaleY(share) {
      return padding.top + chartH - (share / shareMax) * chartH
    }

    let svg = `<svg width="100%" height="${height}" viewBox="0 0 ${width} ${height}" xmlns="http://www.w3.org/2000/svg">`

    // Definitions for gradients & glow filters
    svg += `
      <defs>
        <linearGradient id="grad-with" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="${COLOR_RESERVED}" stop-opacity="0.35"/>
          <stop offset="100%" stop-color="${COLOR_RESERVED}" stop-opacity="0.0"/>
        </linearGradient>
        <linearGradient id="grad-without" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="${COLOR_HOLDOUT}" stop-opacity="0.25"/>
          <stop offset="100%" stop-color="${COLOR_HOLDOUT}" stop-opacity="0.0"/>
        </linearGradient>
        <linearGradient id="grad-reactive" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="${COLOR_REACTIVE}" stop-opacity="0.25"/>
          <stop offset="100%" stop-color="${COLOR_REACTIVE}" stop-opacity="0.0"/>
        </linearGradient>
        <filter id="glow-cyan" x="-20%" y="-20%" width="140%" height="140%">
          <feGaussianBlur stdDeviation="3" result="blur" />
          <feComposite in="SourceGraphic" in2="blur" operator="over" />
        </filter>
      </defs>
    `

    // Horizontal grid lines and Y-axis labels
    const yTicks = [
      { val: 0, label: '0%' },
      { val: 12.5, label: '12.5% (CFS 100)' },
      { val: 25.0, label: '25%' },
      { val: 50.0, label: '50%' },
      { val: 62.5, label: '62.5% (CFS 500)' },
    ]

    yTicks.forEach(tick => {
      const y = scaleY(tick.val)
      svg += `
        <line x1="${padding.left}" y1="${y}" x2="${width - padding.right}" y2="${y}" stroke="${COLOR_BORDER}" stroke-dasharray="3,3" />
        <text x="${padding.left - 10}" y="${y + 4}" fill="${COLOR_TEXT}" font-size="10" font-family="monospace" text-anchor="end">${tick.label}</text>
      `
    })

    // Time X-axis ticks (every 1s or 2s)
    const tickStep = tMax > 15 ? 4.0 : (tMax > 8 ? 2.0 : 1.0)
    for (let t = 0; t <= tMax; t += tickStep) {
      const x = scaleX(t)
      svg += `
        <line x1="${x}" y1="${padding.top + chartH}" x2="${x}" y2="${padding.top + chartH + 6}" stroke="${COLOR_BORDER}" />
        <text x="${x}" y="${padding.top + chartH + 20}" fill="${COLOR_TEXT}" font-size="10" font-family="monospace" text-anchor="middle">${t.toFixed(0)}s</text>
      `
    }

    // Axis Labels
    svg += `
      <text x="${padding.left}" y="${padding.top - 18}" fill="${COLOR_TEXT}" font-size="11" font-family="monospace">CPU SHARE (% CFS Capacity)</text>
      <text x="${width - padding.right}" y="${padding.top + chartH + 36}" fill="${COLOR_TEXT}" font-size="11" font-family="monospace" text-anchor="end">TIMELINE (seconds from file save)</text>
    `

    // Helper to generate path d string
    function makePath(points) {
      if (!points || points.length === 0) return ''
      return points.map((p, idx) => `${idx === 0 ? 'M' : 'L'} ${scaleX(p.t).toFixed(1)} ${scaleY(p.share).toFixed(1)}`).join(' ')
    }

    function makeAreaPath(points) {
      if (!points || points.length === 0) return ''
      const lineD = points.map((p, idx) => `${idx === 0 ? 'M' : 'L'} ${scaleX(p.t).toFixed(1)} ${scaleY(p.share).toFixed(1)}`).join(' ')
      const lastX = scaleX(points[points.length - 1].t).toFixed(1)
      const firstX = scaleX(points[0].t).toFixed(1)
      const baseY = scaleY(0).toFixed(1)
      return `${lineD} L ${lastX} ${baseY} L ${firstX} ${baseY} Z`
    }

    // 1. Draw WITHOUT PreCompile trace
    const withoutPoints = data.traces.without || []
    if (withoutPoints.length > 0) {
      svg += `
        <path d="${makeAreaPath(withoutPoints)}" fill="url(#grad-without)" />
        <path d="${makePath(withoutPoints)}" fill="none" stroke="${COLOR_HOLDOUT}" stroke-width="2.5" stroke-dasharray="6,4" opacity="0.9" />
      `
    }

    // 2. Draw Reactive trace (if enabled)
    const reactivePoints = data.traces.reactive || []
    if (showReactive && reactivePoints.length > 0) {
      svg += `
        <path d="${makeAreaPath(reactivePoints)}" fill="url(#grad-reactive)" />
        <path d="${makePath(reactivePoints)}" fill="none" stroke="${COLOR_REACTIVE}" stroke-width="2.2" stroke-dasharray="3,3" opacity="0.85" />
      `
    }

    // 3. Draw WITH PreCompile trace
    const withPoints = data.traces.with || []
    if (withPoints.length > 0) {
      svg += `
        <path d="${makeAreaPath(withPoints)}" fill="url(#grad-with)" />
        <path d="${makePath(withPoints)}" fill="none" stroke="${COLOR_RESERVED}" stroke-width="3" filter="url(#glow-cyan)" />
      `
    }

    // Vertical Event Markers
    const events = data.events || []
    events.forEach(ev => {
      if (ev.source === 'reactive' && !showReactive) return
      const x = scaleX(ev.t_s)
      let evColor = COLOR_TEXT
      let dash = '2,2'
      let yOffset = padding.top + 10

      if (ev.source === 'with') {
        evColor = COLOR_RESERVED
        dash = '4,2'
        yOffset = padding.top + 26
      } else if (ev.source === 'without') {
        evColor = COLOR_HOLDOUT
        dash = '4,4'
        yOffset = padding.top + 42
      } else if (ev.source === 'reactive') {
        evColor = COLOR_REACTIVE
        yOffset = padding.top + 58
      }

      svg += `
        <line x1="${x}" y1="${padding.top}" x2="${x}" y2="${padding.top + chartH}" stroke="${evColor}" stroke-width="1.5" stroke-dasharray="${dash}" opacity="0.75" />
        <circle cx="${x}" cy="${padding.top + chartH}" r="3.5" fill="${evColor}" />
      `

      // Event Label Badge
      const isRightAligned = (x > width - 150)
      const textAnchor = isRightAligned ? 'end' : 'start'
      const textX = isRightAligned ? x - 6 : x + 6

      svg += `
        <text x="${textX}" y="${yOffset}" fill="${evColor}" font-size="10" font-weight="600" font-family="monospace" text-anchor="${textAnchor}">
          ${ev.name} (${ev.t_s.toFixed(2)}s)
        </text>
      `
    })

    svg += `</svg>`
    container.innerHTML = svg
  }, [data, showReactive])

  return (
    <div
      ref={containerRef}
      style={{ width: '100%', minHeight: 280, display: 'block' }}
    />
  )
}
