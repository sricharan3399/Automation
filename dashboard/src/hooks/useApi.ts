import { useCallback, useEffect, useRef, useState } from 'react'

interface AsyncState<T> {
  data: T | null
  error: unknown
  loading: boolean
  reload: () => void
}

/**
 * Fetch-on-mount with manual reload.
 *
 * The in-flight guard matters: pages poll and also reload on user action, and
 * a slow earlier response must not overwrite a newer one.
 */
export function useApi<T>(fetcher: () => Promise<T>, deps: unknown[] = []): AsyncState<T> {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [loading, setLoading] = useState(true)
  const [nonce, setNonce] = useState(0)
  const requestId = useRef(0)

  const fetcherRef = useRef(fetcher)
  fetcherRef.current = fetcher

  useEffect(() => {
    const id = ++requestId.current
    let cancelled = false
    setLoading(true)

    fetcherRef
      .current()
      .then((result) => {
        if (cancelled || id !== requestId.current) return
        setData(result)
        setError(null)
      })
      .catch((cause) => {
        if (cancelled || id !== requestId.current) return
        setError(cause)
      })
      .finally(() => {
        if (!cancelled && id === requestId.current) setLoading(false)
      })

    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, nonce])

  const reload = useCallback(() => setNonce((value) => value + 1), [])
  return { data, error, loading, reload }
}

/** Poll an endpoint on an interval, pausing while the tab is hidden. */
export function usePolling(callback: () => void, intervalMs: number, enabled = true): void {
  const callbackRef = useRef(callback)
  callbackRef.current = callback

  useEffect(() => {
    if (!enabled) return undefined
    const tick = () => {
      if (document.visibilityState === 'visible') callbackRef.current()
    }
    const timer = window.setInterval(tick, intervalMs)
    return () => window.clearInterval(timer)
  }, [intervalMs, enabled])
}
