import { useEffect, useState, useSyncExternalStore } from 'react'
import { useRegisterSW } from 'virtual:pwa-register/react'
import { cn } from '@/lib/utils'

// Small notes the installed app needs: a new version is ready, there is no connection, and how to
// install it (Android/desktop Chrome offer a prompt; the iPhone only installs from Safari's share menu).

interface InstallPromptEvent extends Event {
  prompt: () => Promise<void>
}

const HINT_KEY = 'agenda-ia:dica-instalar'

const isStandalone = () =>
  window.matchMedia('(display-mode: standalone)').matches ||
  (navigator as Navigator & { standalone?: boolean }).standalone === true

const isIPhone = () =>
  /iphone|ipad|ipod/i.test(navigator.userAgent) || (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1)

function readHintDismissed(): boolean {
  try {
    return localStorage.getItem(HINT_KEY) === 'fechada'
  } catch {
    return false
  }
}

function useOnline(): boolean {
  return useSyncExternalStore(
    (notify) => {
      window.addEventListener('online', notify)
      window.addEventListener('offline', notify)
      return () => {
        window.removeEventListener('online', notify)
        window.removeEventListener('offline', notify)
      }
    },
    () => navigator.onLine,
  )
}

export function AppNotices() {
  const {
    needRefresh: [needRefresh, setNeedRefresh],
    updateServiceWorker,
  } = useRegisterSW({
    onRegisteredSW(_url, registration) {
      // An app left open on the phone for days still learns about new versions.
      if (registration) window.setInterval(() => void registration.update(), 60 * 60 * 1000)
    },
  })
  const online = useOnline()
  const [installEvent, setInstallEvent] = useState<InstallPromptEvent | null>(null)
  const [hintDismissed, setHintDismissed] = useState(readHintDismissed)

  useEffect(() => {
    const onPrompt = (event: Event) => {
      event.preventDefault() // keep the browser's own banner away; the note below offers it instead
      setInstallEvent(event as InstallPromptEvent)
    }
    const onInstalled = () => setInstallEvent(null)
    window.addEventListener('beforeinstallprompt', onPrompt)
    window.addEventListener('appinstalled', onInstalled)
    return () => {
      window.removeEventListener('beforeinstallprompt', onPrompt)
      window.removeEventListener('appinstalled', onInstalled)
    }
  }, [])

  function dismissHint() {
    setHintDismissed(true)
    setInstallEvent(null)
    try {
      localStorage.setItem(HINT_KEY, 'fechada')
    } catch {
      // private browsing: the note just comes back next time
    }
  }

  const iPhoneHint = !hintDismissed && !isStandalone() && isIPhone()
  const notes: { key: string; text: string; action?: { label: string; run: () => void }; close?: () => void }[] = []
  if (needRefresh)
    notes.push({
      key: 'versao',
      text: 'nova versão do app disponível.',
      action: { label: 'atualizar', run: () => void updateServiceWorker(true) },
      close: () => setNeedRefresh(false),
    })
  if (!online) notes.push({ key: 'offline', text: 'sem conexão: perguntas, agenda e login precisam de internet.' })
  if (installEvent && !hintDismissed)
    notes.push({
      key: 'instalar',
      text: 'dá para instalar a agenda como app.',
      action: {
        label: 'instalar',
        run: () => {
          void installEvent.prompt()
          setInstallEvent(null)
        },
      },
      close: dismissHint,
    })
  if (iPhoneHint)
    notes.push({
      key: 'iphone',
      text: 'para instalar no iPhone: no Safari, toque em Compartilhar e depois em "Adicionar à Tela de Início".',
      close: dismissHint,
    })

  if (notes.length === 0) return null
  return (
    <div
      role="status"
      className="pointer-events-none fixed inset-x-0 top-[calc(env(safe-area-inset-top)+0.5rem)] z-50 flex flex-col items-center gap-2 px-3"
    >
      {notes.map((note) => (
        <p
          key={note.key}
          className={cn(
            'pointer-events-auto flex max-w-md items-center gap-3 rounded-[3px] px-3 py-1.5 shadow-[0_8px_16px_-10px_rgb(0_0_0/0.45)]',
            'bg-card bg-[linear-gradient(color-mix(in_oklab,var(--marker)_60%,transparent),color-mix(in_oklab,var(--marker)_60%,transparent))]',
            'font-hand text-[1.2rem] leading-tight text-foreground',
          )}
        >
          <span>{note.text}</span>
          {note.action && (
            <button type="button" onClick={note.action.run} className="font-mono text-[11px] tracking-wide text-primary uppercase underline">
              {note.action.label}
            </button>
          )}
          {note.close && (
            <button type="button" onClick={note.close} aria-label="Fechar aviso" className="font-mono text-[12px] text-pencil">
              ×
            </button>
          )}
        </p>
      ))}
    </div>
  )
}
