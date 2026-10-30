import { useEffect, useRef } from 'react'
import Lenis from 'lenis'

/**
 * useLenis — creates a Lenis smooth-scroll instance for a given container ref.
 * Returns the lenis instance for programmatic scrolling.
 */
export function useLenis(containerRef) {
  const lenisRef = useRef(null)

  useEffect(() => {
    if (!containerRef?.current) return

    const lenis = new Lenis({
      wrapper: containerRef.current,
      content: containerRef.current,
      duration: 1.2,
      easing: (t) => Math.min(1, 1.001 - Math.pow(2, -10 * t)),
      smoothWheel: true,
      wheelMultiplier: 0.8,
    })

    lenisRef.current = lenis

    function raf(time) {
      lenis.raf(time)
      requestAnimationFrame(raf)
    }
    const id = requestAnimationFrame(raf)

    return () => {
      cancelAnimationFrame(id)
      lenis.destroy()
    }
  }, [containerRef])

  return lenisRef
}
