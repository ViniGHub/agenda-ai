import { useState } from 'react'
import { Button } from '@/components/ui/button'
import { InkLink } from '@/components/ui/skiper-ui/ink-link'
import { cn, STAMP_BUTTON } from '@/lib/utils'
import { draftWhen } from '../format'
import type { AgendaEvent, NoteAction } from '../types'

/** Where a chat note stands after the person acted on it. */
export type NoteState = 'aberto' | 'anotado' | 'desfeito' | 'cancelado' | 'pergunta'

interface Props {
  action: NoteAction
  state: NoteState
  saved: AgendaEvent | null
  onConfirm: () => Promise<void>
  onUndo: () => Promise<void>
  onEdit: () => void
  onEditSaved: () => void // the note was saved: change the saved event
  onDismiss: () => void
  onAskInstead: () => void
  onShowEvent: (eventId: number) => void
}

const MISSING: Record<string, string> = { data: 'o dia', inicio: 'o horário' }

/**
 * A sticky note under the chat reply: the appointment the model read from the message, and what
 * to do with it. Saved notes offer an undo; unclear ones ask first; incomplete ones open the form.
 */
export function NoteCard({ action, state, saved, onConfirm, onUndo, onEdit, onEditSaved, onDismiss, onAskInstead, onShowEvent }: Props) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const d = action.rascunho
  // Once saved, show the saved version (it may have been edited since); before that, what was read.
  const shown = state === 'anotado' && saved ? saved : { titulo: d.titulo, local: d.local, participantes: d.participantes, notas: d.notas }
  const gone = state === 'desfeito' || state === 'cancelado' || state === 'pergunta'

  async function run(task: () => Promise<void>) {
    setBusy(true)
    setError(null)
    try {
      await task()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const link = 'font-mono text-[11px] tracking-wide text-pencil uppercase'

  return (
    <div
      className={cn(
        'relative my-1 max-w-md -rotate-[0.6deg] rounded-[3px] px-4 py-1 shadow-[0_1px_0_rgb(0_0_0/0.08),0_8px_16px_-10px_rgb(0_0_0/0.35)]',
        'bg-card bg-[linear-gradient(color-mix(in_oklab,var(--marker)_65%,transparent),color-mix(in_oklab,var(--marker)_65%,transparent))]',
        gone && 'opacity-70',
      )}
    >
      {state === 'anotado' && (
        <span className="stamp absolute -top-2 right-3 rotate-[4deg] bg-card px-1.5 py-0.5 text-[10px] text-primary">anotado</span>
      )}
      <p className={cn('font-heading text-[1.1rem] leading-[var(--line)] font-medium', gone && 'line-through decoration-primary/60')}>
        {shown.titulo}
      </p>
      <p className="font-mono text-[12px] leading-[var(--line)] text-foreground/80">
        {state === 'anotado' && saved ? saved.quando : draftWhen(d)}
      </p>
      {(shown.local || shown.participantes.length > 0) && (
        <p className="text-[14px] leading-[var(--line)] text-foreground/80">
          {shown.local}
          {shown.local && shown.participantes.length > 0 && ' · '}
          {shown.participantes.length > 0 && `com ${shown.participantes.join(', ')}`}
        </p>
      )}
      {shown.notas && <p className="text-[14px] leading-[var(--line)] text-muted-foreground">{shown.notas}</p>}

      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 py-1">
        {state === 'aberto' && action.tipo === 'confirmar' && (
          <>
            <Button variant="ghost" disabled={busy} onClick={() => void run(onConfirm)}
              className={cn(STAMP_BUTTON, 'h-7 px-3 text-[11px]')}>
              {busy ? 'Anotando…' : 'Anotar'}
            </Button>
            <InkLink disabled={busy} onClick={onEdit} className={link}>editar</InkLink>
            <InkLink disabled={busy} onClick={onAskInstead} className={link}>era uma pergunta</InkLink>
          </>
        )}
        {state === 'aberto' && action.tipo === 'completar' && (
          <>
            <span className="font-hand text-[1.2rem] text-primary">
              falta {d.faltando.map((f) => MISSING[f]).join(' e ')}
            </span>
            <Button variant="ghost" onClick={onEdit} className={cn(STAMP_BUTTON, 'h-7 px-3 text-[11px]')}>
              Completar
            </Button>
            <InkLink onClick={onDismiss} className={link}>deixar para lá</InkLink>
          </>
        )}
        {state === 'anotado' && saved && (
          <>
            <InkLink onClick={() => onShowEvent(saved.id)} className={link}>ver na agenda</InkLink>
            <InkLink disabled={busy} onClick={onEditSaved} className={link}>editar</InkLink>
            <InkLink disabled={busy} onClick={() => void run(onUndo)} className={link}>
              {busy ? 'desfazendo…' : 'desfazer'}
            </InkLink>
          </>
        )}
        {state === 'desfeito' && <span className="font-hand text-[1.2rem] text-pencil">desfeito: saiu da agenda.</span>}
        {state === 'cancelado' && <span className="font-hand text-[1.2rem] text-pencil">deixei para lá.</span>}
        {state === 'pergunta' && <span className="font-hand text-[1.2rem] text-pencil">respondido como pergunta, logo abaixo.</span>}
      </div>
      {error && <p role="alert" className="pb-1 font-hand text-[1.2rem] leading-tight text-primary">{error}</p>}
    </div>
  )
}
