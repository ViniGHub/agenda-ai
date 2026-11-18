import { useState } from 'react'
import { InkLink } from '@/components/ui/skiper-ui/ink-link'
import { cn } from '@/lib/utils'
import { GOOGLE_CONNECT_URL } from '../api'
import type { GoogleStatus } from '../types'

interface Props {
  status: GoogleStatus | null
  busy: string | null // what is running now ("sincronizando…"), or null
  onSync: () => void
  onDailyMail: (on: boolean) => void
  onSendNow: () => void
  onDisconnect: () => void
}

const CONTROL = 'font-mono text-[10.5px] tracking-wide uppercase'

/** "08:00" -> "8h", "08:30" -> "8h30" */
function hourLabel(hhmm: string): string {
  const [hours, minutes] = hhmm.split(':')
  return `${Number(hours)}h${minutes && minutes !== '00' ? minutes : ''}`
}

function syncedLabel(iso: string | null): string {
  if (!iso) return 'ainda não sincronizado'
  const when = new Date(iso)
  const today = new Date().toDateString() === when.toDateString()
  return today
    ? `sincronizado às ${when.toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' })}`
    : `sincronizado em ${when.toLocaleDateString('pt-BR', { day: '2-digit', month: '2-digit' })}`
}

/** The private agenda's link with Google Calendar and the morning e-mail, above the agenda. */
export function GoogleBar({ status, busy, onSync, onDailyMail, onSendNow, onDisconnect }: Props) {
  const [confirming, setConfirming] = useState(false)
  if (!status?.disponivel) return null

  if (!status.conectado) {
    return (
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1 border-b border-border px-6 py-2">
        <span className="font-hand text-[1.15rem] leading-tight text-pencil">
          conecte o google agenda: o que você anotar aqui vai para o seu calendário, e às 8h chega um e-mail com o dia.
        </span>
        {/* A plain link: the page goes to Google and comes back here. */}
        <a href={GOOGLE_CONNECT_URL} className={cn(CONTROL, 'text-primary underline underline-offset-2')}>
          conectar
        </a>
      </div>
    )
  }

  const missing = !status.agenda || !status.gmail
  return (
    <div
      className={cn(CONTROL, 'flex flex-wrap items-center gap-x-4 gap-y-1.5 border-b border-border px-6 py-2 text-pencil')}
      aria-busy={busy !== null}
    >
      <span className="text-primary" title={status.email ?? undefined}>
        google agenda
      </span>
      {status.agenda ? (
        <>
          <span>{busy ?? syncedLabel(status.sincronizado_em)}</span>
          <InkLink onClick={onSync} disabled={busy !== null} className="uppercase hover:text-primary">
            sincronizar
          </InkLink>
        </>
      ) : (
        busy && <span>{busy}</span>
      )}
      {status.gmail && (
        <>
          <button
            type="button"
            role="switch"
            aria-checked={status.resumo_diario}
            disabled={busy !== null}
            onClick={() => onDailyMail(!status.resumo_diario)}
            className="flex items-center gap-1.5 uppercase hover:text-primary disabled:opacity-50"
          >
            <span
              aria-hidden
              className={cn(
                'relative inline-block h-3 w-6 rounded-full border border-current transition-colors',
                status.resumo_diario && 'bg-primary text-primary',
              )}
            >
              <span
                className={cn(
                  'absolute top-[1px] left-[1px] size-2 rounded-full bg-current transition-transform',
                  status.resumo_diario && 'translate-x-3 bg-primary-foreground',
                )}
              />
            </span>
            e-mail às {hourLabel(status.resumo_hora)}
          </button>
          <InkLink onClick={onSendNow} disabled={busy !== null} className="uppercase hover:text-primary">
            enviar agora
          </InkLink>
        </>
      )}
      {missing && (
        <a href={GOOGLE_CONNECT_URL} className="text-primary underline underline-offset-2">
          {status.agenda ? 'liberar o e-mail' : 'liberar a agenda'}
        </a>
      )}
      <span className="ml-auto">
        {confirming ? (
          <span className="normal-case">
            <span className="text-primary">desconectar? o que veio do google sai daqui (continua no google). </span>
            <button type="button" onClick={() => { setConfirming(false); onDisconnect() }} className="text-primary underline">
              sim
            </button>
            {' · '}
            <button type="button" onClick={() => setConfirming(false)} className="underline">
              não
            </button>
          </span>
        ) : (
          <InkLink onClick={() => setConfirming(true)} disabled={busy !== null} className="uppercase hover:text-primary">
            desconectar
          </InkLink>
        )}
      </span>
    </div>
  )
}
