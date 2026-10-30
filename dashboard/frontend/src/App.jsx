import React, { useState, useEffect, useRef, Suspense, lazy } from 'react'
import { AnimatePresence, motion } from 'framer-motion'
import Sidebar from './components/Sidebar.jsx'
import ShaderBackground from './components/ShaderBackground.jsx'
import { useSSE } from './hooks/useData.js'
import { useLenis } from './hooks/useLenis.js'

// Lazy-load views for performance
const CompareView = lazy(() => import('./views/CompareView.jsx'))
const OverviewView = lazy(() => import('./views/OverviewView.jsx'))
const TimelineView = lazy(() => import('./views/TimelineView.jsx'))
const DistributionsView = lazy(() => import('./views/DistributionsView.jsx'))
const BenefitView = lazy(() => import('./views/BenefitView.jsx'))
const SafetyView = lazy(() => import('./views/SafetyView.jsx'))
const ExperimentsView = lazy(() => import('./views/ExperimentsView.jsx'))
const DecisionsView = lazy(() => import('./views/DecisionsView.jsx'))

const VIEW_GRADIENT = {
  compare: 'compare',
  overview: 'overview',
  safety: 'safety',
}

const KEY_MAP = {
  '1': 'compare', '2': 'overview', '3': 'timeline',
  '4': 'distributions', '5': 'benefit', '6': 'safety',
  '7': 'experiments', '8': 'decisions',
}

function LoadingFallback() {
  return (
    <div className="empty-state" style={{ height: '100%' }}>
      <div style={{ fontSize: 18, opacity: 0.4 }}>⟳</div>
    </div>
  )
}

export default function App() {
  const [view, setView] = useState('compare')
  const [presentationMode, setPresentationMode] = useState(false)
  const [performanceMode, setPerformanceMode] = useState(false)
  const [replayDataset, setReplayDataset] = useState('results_all')

  const { tick, connected } = useSSE()
  const mainRef = useRef(null)
  const lenis = useLenis(mainRef)

  // Force performance mode ON during presentation mode to guarantee 0% rendering overhead
  const effectivePerformanceMode = performanceMode || presentationMode

  // Keyboard shortcuts (Keys 1-8 for views, P for Presentation Mode, Esc to exit)
  useEffect(() => {
    function handleKey(e) {
      if (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT') return

      if (KEY_MAP[e.key]) {
        setView(KEY_MAP[e.key])
      }
      if (e.key === 'p' || e.key === 'P') {
        setPresentationMode(prev => !prev)
      }
      if (e.key === 'Escape') {
        setPresentationMode(false)
      }
    }
    window.addEventListener('keydown', handleKey)
    return () => window.removeEventListener('keydown', handleKey)
  }, [])

  // Scroll to top on view change
  useEffect(() => {
    lenis.current?.scrollTo(0, { immediate: true })
  }, [view])

  const gradientTheme = VIEW_GRADIENT[view] || 'default'

  return (
    <div className={`app-shell ${presentationMode ? 'presentation-mode' : ''}`}>
      {/* Animated shader gradient background (disabled in Performance / Presentation mode) */}
      {!effectivePerformanceMode && (
        <ShaderBackground view={gradientTheme} />
      )}

      {/* Floating Exit Button in Presentation Mode */}
      {presentationMode && (
        <button
          className="floating-exit-pmode"
          onClick={() => setPresentationMode(false)}
        >
          ✕ Exit Presentation [P / Esc]
        </button>
      )}

      {/* Sidebar Navigation */}
      <Sidebar
        currentView={view}
        onNavigate={setView}
        connected={connected}
        presentationMode={presentationMode}
        onTogglePresentation={() => setPresentationMode(p => !p)}
        performanceMode={performanceMode}
        onTogglePerformance={() => setPerformanceMode(p => !p)}
        replayDataset={replayDataset}
        onChangeReplayDataset={setReplayDataset}
      />

      {/* Main Scrollable Content */}
      <main className="main-content" ref={mainRef}>
        <AnimatePresence mode="wait">
          <motion.div
            key={view}
            initial={{ opacity: 0, x: 15 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: -15 }}
            transition={{ duration: effectivePerformanceMode ? 0.05 : 0.25, ease: [0.4, 0, 0.2, 1] }}
            style={{ minHeight: '100%' }}
          >
            <Suspense fallback={<LoadingFallback />}>
              {view === 'compare' && (
                <CompareView
                  presentationMode={presentationMode}
                  performanceMode={effectivePerformanceMode}
                />
              )}
              {view === 'overview' && <OverviewView tick={tick} />}
              {view === 'timeline' && <TimelineView />}
              {view === 'distributions' && <DistributionsView />}
              {view === 'benefit' && <BenefitView />}
              {view === 'safety' && <SafetyView />}
              {view === 'experiments' && <ExperimentsView />}
              {view === 'decisions' && <DecisionsView />}
            </Suspense>
          </motion.div>
        </AnimatePresence>
      </main>
    </div>
  )
}
