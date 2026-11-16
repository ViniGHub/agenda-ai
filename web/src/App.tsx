import { useCallback, useEffect, useState } from 'react'
import { getConfig, getMe, logout } from './api'
import { Landing } from './Landing'
import type { Config, Me } from './types'
import { Workspace } from './Workspace'

type Screen = 'inicio' | 'demo' | 'minha'

// The screen lives in the URL hash, so "/#demo" can be shared and the back button works.
const screenFromHash = (): Screen => {
  const hash = window.location.hash.replace('#', '')
  return hash === 'demo' || hash === 'minha' ? hash : 'inicio'
}

export default function App() {
  const [screen, setScreen] = useState<Screen>(screenFromHash)
  const [config, setConfig] = useState<Config | null>(null)
  const [configError, setConfigError] = useState<string | null>(null)
  const [me, setMe] = useState<Me | null>(null)

  useEffect(() => {
    getConfig()
      .then(setConfig)
      .catch((error: Error) => setConfigError(error.message))
    getMe()
      .then(setMe)
      .catch(() => setMe({ logado: false, nome: null, email: null }))
    const onHashChange = () => setScreen(screenFromHash())
    window.addEventListener('hashchange', onHashChange)
    return () => window.removeEventListener('hashchange', onHashChange)
  }, [])

  const go = useCallback((next: Screen) => {
    window.location.hash = next === 'inicio' ? '' : next
    setScreen(next)
  }, [])

  const onSignedIn = useCallback(
    (signedIn: Me) => {
      setMe(signedIn)
      go('minha')
    },
    [go],
  )

  async function signOut() {
    await logout().catch(() => undefined)
    window.google?.accounts.id.disableAutoSelect()
    setMe({ logado: false, nome: null, email: null })
    go('inicio')
  }

  function sessionLost() {
    setMe({ logado: false, nome: null, email: null })
    go('inicio')
  }

  // The private notebook needs a session; until /api/me answers, or without one, show the cover.
  if (screen === 'minha' && me?.logado) {
    return <Workspace key="minha" space="minha" user={me} onLeave={signOut} onSessionLost={sessionLost} />
  }
  if (screen === 'demo') {
    return <Workspace key="demo" space="demo" user={null} onLeave={() => go('inicio')} onSessionLost={() => undefined} />
  }
  return <Landing config={config} configError={configError} onDemo={() => go('demo')} onSignedIn={onSignedIn} />
}
