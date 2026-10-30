import { useState, useEffect, useRef, useCallback } from 'react'

/**
 * useSSE — subscribes to the server-sent events stream at /api/stream
 * and provides live tick data. Falls back gracefully when unavailable.
 */
export function useSSE() {
  const [tick, setTick] = useState(null)
  const [connected, setConnected] = useState(false)
  const esRef = useRef(null)

  useEffect(() => {
    let retries = 0
    const MAX_RETRIES = 5

    function connect() {
      const es = new EventSource('/api/stream')
      esRef.current = es

      es.onopen = () => {
        setConnected(true)
        retries = 0
      }

      es.onmessage = (e) => {
        try {
          const data = JSON.parse(e.data)
          if (data.type !== 'ping') setTick(data)
        } catch { /* ignore malformed */ }
      }

      es.onerror = () => {
        setConnected(false)
        es.close()
        if (retries < MAX_RETRIES) {
          retries++
          setTimeout(connect, 2000 * retries)
        }
      }
    }

    connect()
    return () => {
      esRef.current?.close()
    }
  }, [])

  return { tick, connected }
}

/**
 * useAPI — fetches a JSON API endpoint on mount and when deps change.
 */
export function useAPI(endpoint, deps = []) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const fetch_ = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await fetch(endpoint)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const json = await res.json()
      setData(json)
    } catch (e) {
      setError(e.message)
    } finally {
      setLoading(false)
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [endpoint, ...deps])

  useEffect(() => { fetch_() }, [fetch_])

  return { data, loading, error, refetch: fetch_ }
}
