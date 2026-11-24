import { useCallback, useEffect, useRef, useState } from 'react'
import { getConfig, getMe, logout } from '@/api/client'
import { Landing } from '@/pages/Landing'
import { useWarmup } from '@/features/startup/warmup'
import type { Config, Me } from '@/api/types'
import { Workspace } from '@/pages/Workspace'

type Screen = 'inicio' | 'demo' | 'minha'

// The screen lives in the URL hash, so "/#demo" can be shared and the back button works.
const screenFromHash = (): Screen => {
  const hash = window.location.hash.replace('#', '')
  return hash === 'demo' || hash === 'minha' ? hash : 'inicio'
}

// "This device was signed in": lets the app say "abrindo a sua agenda" instead of showing the cover
// with a login button while it asks the server. Only a hint: the session itself is the server's cookie.
const SESSION_HINT = 'agenda-ia:sessao'

function readSessionHint(): boolean {
  try {
    return localStorage.getItem(SESSION_HINT) === '1'
  } catch {
    return false
  }
}

function writeSessionHint(signedIn: boolean) {
  try {
    if (signedIn) localStorage.setItem(SESSION_HINT, '1')
    else localStorage.removeItem(SESSION_HINT)
  } catch {
    // private browsing: the cover shows until the server answers
  }
}

const SIGNED_OUT: Me = { logado: false, nome: null, email: null }

export default function App() {
  const [screen, setScreen] = useState<Screen>(screenFromHash)
  const [config, setConfig] = useState<Config | null>(null)
  const [configError, setConfigError] = useState<string | null>(null)
  const [me, setMe] = useState<Me | null>(null)
  const warmup = useWarmup()
  const hadSession = useRef(readSessionHint())
  const openedOnCover = useRef(screenFromHash() === 'inicio') // the installed app always opens on "/"

  const go = useCallback((next: Screen) => {
    window.location.hash = next === 'inicio' ? '' : next
    setScreen(next)
  }, [])

  useEffect(() => {
    let stopped = false
    getConfig()
      .then(setConfig)
      .catch((error: Error) => setConfigError(error.message))
    // A device that was signed in asks again while the server starts, instead of showing the cover.
    async function whoAmI(attempt: number) {
      try {
        const answer = await getMe()
        if (stopped) return
        setMe(answer)
        writeSessionHint(answer.logado)
        // Opened on the cover (the installed app starts at "/") with a session: straight to the agenda.
        if (answer.logado && openedOnCover.current) go('minha')
      } catch {
        if (stopped) return
        if (hadSession.current && attempt < 15) window.setTimeout(() => void whoAmI(attempt + 1), 2000)
        else setMe(SIGNED_OUT)
      }
    }
    void whoAmI(0)
    const onHashChange = () => setScreen(screenFromHash())
    window.addEventListener('hashchange', onHashChange)
    return () => {
      stopped = true
      window.removeEventListener('hashchange', onHashChange)
    }
  }, [go])

  const onSignedIn = useCallback(
    (signedIn: Me) => {
      setMe(signedIn)
      writeSessionHint(true)
      go('minha')
    },
    [go],
  )

  async function signOut() {
    await logout().catch(() => undefined)
    window.google?.accounts.id.disableAutoSelect()
    setMe(SIGNED_OUT)
    writeSessionHint(false)
    go('inicio')
  }

  function sessionLost() {
    setMe(SIGNED_OUT)
    writeSessionHint(false)
    go('inicio')
  }

  // The private notebook needs a session; until /api/me answers, or without one, show the cover.
  if (screen === 'minha' && me?.logado) {
    return <Workspace key="minha" space="minha" user={me} warmup={warmup} onLeave={signOut} onSessionLost={sessionLost} />
  }
  if (screen === 'demo') {
    return (
      <Workspace key="demo" space="demo" user={null} warmup={warmup} onLeave={() => go('inicio')} onSessionLost={() => undefined} />
    )
  }
  return (
    <Landing
      config={config}
      configError={configError}
      warmup={warmup}
      opening={me === null && hadSession.current}
      onDemo={() => go('demo')}
      onSignedIn={onSignedIn}
    />
  )
}
