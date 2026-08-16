/** WebSocket subscription for live run progress, with automatic reconnect. */

import type { ProgressFrame } from '@/types'

export type ProgressHandler = (frame: ProgressFrame) => void

const MAX_BACKOFF_MS = 15_000

export function subscribeToRun(runId: string | null, onFrame: ProgressHandler): () => void {
  let socket: WebSocket | null = null
  let closed = false
  let attempt = 0
  let timer: number | undefined

  const path = runId ? `/ws/runs/${runId}` : '/ws/runs'

  const connect = () => {
    if (closed) return
    const scheme = window.location.protocol === 'https:' ? 'wss' : 'ws'
    socket = new WebSocket(`${scheme}://${window.location.host}${path}`)

    socket.onopen = () => {
      attempt = 0
    }
    socket.onmessage = (event) => {
      try {
        const payload = JSON.parse(event.data) as { type: string; data?: ProgressFrame }
        if (payload.type === 'progress' && payload.data) onFrame(payload.data)
      } catch {
        // A malformed frame is dropped; progress frames are snapshots, so the
        // next one restores the correct state.
      }
    }
    socket.onclose = () => {
      if (closed) return
      // Exponential backoff: a backend restart should not produce a reconnect storm.
      attempt += 1
      const delay = Math.min(MAX_BACKOFF_MS, 500 * 2 ** Math.min(attempt, 5))
      timer = window.setTimeout(connect, delay)
    }
    socket.onerror = () => socket?.close()
  }

  connect()

  return () => {
    closed = true
    if (timer) window.clearTimeout(timer)
    socket?.close()
  }
}
