import { useState } from 'react'
import { timeLabel } from '../format'
import type { AgendaEvent } from '../types'

const ORIGIN_NOTE: Record<AgendaEvent['origem'], string | null> = {
  base: null,
  cadastro: 'você anotou',
  importado: 'importado',
  google: 'do google',
}

interface Props {
  event: AgendaEvent
  cited: boolean // a source of the answer being looked at: highlighted
  onEdit: (event: AgendaEvent) => void
  onDelete: (event: AgendaEvent) => void
}

/** One appointment: time, title, where, who, notes, and "editar · apagar". The list and the calendar both use it. */
export function EventRow({ event, cited, onEdit, onDelete }: Props) {
  const [confirming, setConfirming] = useState(false) // "apagar mesmo?"

  return (
    <div className="grid grid-cols-[6rem_1fr] gap-x-3">
      <span className="font-mono text-[12px] leading-6 text-pencil tabular-nums">{timeLabel(event)}</span>
      <div className="min-w-0 leading-6">
        <div className="flex flex-wrap items-baseline gap-x-2">
          <span className="font-medium">
            {cited ? <mark className="marker bg-transparent text-inherit">{event.titulo}</mark> : event.titulo}
          </span>
          {ORIGIN_NOTE[event.origem] && (
            <span className="font-hand text-[1.05rem] leading-none text-primary">{ORIGIN_NOTE[event.origem]}</span>
          )}
          {event.origem !== 'base' &&
            (confirming ? (
              <span className="ml-auto font-mono text-[10px] tracking-wide uppercase">
                <span className="text-primary">apagar mesmo? </span>
                <button
                  type="button"
                  onClick={() => {
                    setConfirming(false)
                    onDelete(event)
                  }}
                  className="text-primary underline"
                >
                  sim
                </button>
                <span className="text-pencil"> · </span>
                <button type="button" onClick={() => setConfirming(false)} className="text-pencil underline">
                  não
                </button>
              </span>
            ) : (
              <span className="ml-auto flex gap-3 font-mono text-[10px] tracking-wide text-pencil uppercase">
                <button
                  type="button"
                  onClick={() => onEdit(event)}
                  className="uppercase hover:text-primary hover:underline"
                  aria-label={`Editar ${event.titulo}`}
                >
                  editar
                </button>
                <button
                  type="button"
                  onClick={() => setConfirming(true)}
                  className="uppercase hover:text-primary hover:line-through"
                  aria-label={`Apagar ${event.titulo}`}
                >
                  apagar
                </button>
              </span>
            ))}
        </div>
        {event.local && <div className="text-muted-foreground">{event.local}</div>}
        {event.participantes.length > 0 && <div className="text-muted-foreground">com {event.participantes.join(', ')}</div>}
        {event.notas && <div className="text-foreground/80">{event.notas}</div>}
      </div>
    </div>
  )
}
