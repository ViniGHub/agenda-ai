import { useEffect, useRef, useState } from 'react'
import { ApiError, getReady } from '@/api/client'
import type { Stage } from '@/api/types'

export interface Warmup {
  stage: Stage
  ready: boolean
  failed: boolean
  reached: boolean // the server has answered at least once
  seconds: number // since the page started waiting
}

/**
 * Follows the server's start-up until it is ready: one loop for the whole app.
 * /api/pronto answers when the step changes (a long poll), so each step shows as it happens.
 */
export function useWarmup(): Warmup {
  const [stage, setStage] = useState<Stage>('servidor')
  const [reached, setReached] = useState(false)
  const [seconds, setSeconds] = useState(0)
  const started = useRef(Date.now())
  const done = stage === 'pronto' || stage === 'falhou'

  useEffect(() => {
    let stopped = false
    async function follow() {
      let known: Stage | '' = ''
      while (!stopped) {
        try {
          const answer = await getReady(known)
          if (stopped) return
          setReached(true)
          setStage(answer.etapa)
          known = answer.etapa
          if (answer.pronto || answer.etapa === 'falhou') return
        } catch (error) {
          if (stopped) return
          if (error instanceof ApiError && error.status === 404) {
            setStage('pronto') // a server from before this route: it only answers once loaded
            return
          }
          await new Promise((resolve) => setTimeout(resolve, 2000)) // still starting, or offline: ask again
        }
      }
    }
    void follow()
    return () => {
      stopped = true
    }
  }, [])

  useEffect(() => {
    if (done) return
    const timer = window.setInterval(() => setSeconds(Math.round((Date.now() - started.current) / 1000)), 1000)
    return () => window.clearInterval(timer)
  }, [done])

  return { stage, ready: stage === 'pronto', failed: stage === 'falhou', reached, seconds }
}

/** True once `active` has stayed true for `ms`: a warm server answers before a loading note could flash. */
export function useShownAfter(active: boolean, ms = 600): boolean {
  const [shown, setShown] = useState(false)
  useEffect(() => {
    if (!active) {
      setShown(false)
      return
    }
    const timer = window.setTimeout(() => setShown(true), ms)
    return () => window.clearTimeout(timer)
  }, [active, ms])
  return shown
}
