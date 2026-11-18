import { Check, LoaderCircle } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { cn } from '@/lib/utils'
import { useShownAfter, type Warmup } from '@/lib/warmup'

// The server's start-up, in order (src/agenda_ia/api.py, Startup). "banco" only runs in production.
const STEPS = [
  { key: 'servidor', label: 'ligar o servidor' },
  { key: 'modelo', label: 'carregar o modelo de busca' },
  { key: 'exemplo', label: 'montar a agenda de exemplo' },
  { key: 'banco', label: 'conectar ao banco das agendas' },
] as const

interface Props {
  warmup: Warmup
  title: string // "acordando o servidor", "abrindo a sua agenda"…
  className?: string
}

/** A to-do list on the page while the server wakes up: done steps crossed out, the current one turning. */
export function WarmupChecklist({ warmup, title, className }: Props) {
  const visible = useShownAfter(!warmup.ready)
  const wasShown = useRef(false)
  const [doneNote, setDoneNote] = useState(false) // "pronto" for a moment, so the list does not just vanish

  useEffect(() => {
    if (visible) wasShown.current = true
  }, [visible])

  useEffect(() => {
    if (!warmup.ready || !wasShown.current) return
    setDoneNote(true)
    const timer = window.setTimeout(() => setDoneNote(false), 2500)
    return () => window.clearTimeout(timer)
  }, [warmup.ready])

  if (warmup.ready) {
    return doneNote ? (
      <p role="status" className={cn('flex items-center gap-2 font-hand text-[1.25rem] text-primary', className)}>
        <Check className="size-4 shrink-0" aria-hidden />
        servidor pronto em {warmup.seconds} s
      </p>
    ) : null
  }
  if (!visible) return null

  if (warmup.failed) {
    return (
      <div role="alert" className={cn('font-hand text-[1.25rem] leading-tight text-primary', className)}>
        o servidor não conseguiu iniciar. tente de novo em alguns minutos.{' '}
        <ReloadButton />
      </div>
    )
  }

  // Before the first answer the server is still being switched on; once it answers, that step is done.
  const index = STEPS.findIndex((step) => step.key === warmup.stage)
  const current = !warmup.reached ? 0 : Math.max(1, index)

  return (
    <div role="status" aria-live="polite" className={className}>
      <p className="font-mono text-[11px] leading-6 tracking-wide text-primary uppercase">
        {title} · <span className="tabular-nums">{warmup.seconds} s</span>
      </p>
      <ul>
        {STEPS.map((step, i) => (
          <li
            key={step.key}
            className={cn(
              'flex items-center gap-2 font-hand text-[1.25rem] leading-[1.55]',
              i < current && 'text-pencil line-through decoration-1',
              i === current && 'text-foreground',
              i > current && 'text-pencil/60',
            )}
          >
            {i < current ? (
              <Check className="size-4 shrink-0 text-primary" aria-label="feito" />
            ) : i === current ? (
              <LoaderCircle className="size-4 shrink-0 text-primary motion-safe:animate-spin" aria-label="agora" />
            ) : (
              <span aria-hidden className="flex size-4 shrink-0 items-center justify-center">
                <span className="size-1.5 rounded-full bg-current" />
              </span>
            )}
            {step.label}
            {i === current && '…'}
          </li>
        ))}
      </ul>
      <p className="mt-1 font-hand text-[1.1rem] leading-tight text-pencil">
        {warmup.seconds > 90 ? (
          <>
            está demorando mais que o normal. <ReloadButton />
          </>
        ) : (
          'o servidor dorme quando ninguém usa: ao acordar, leva até um minuto. depois fica rápido.'
        )}
      </p>
    </div>
  )
}

function ReloadButton() {
  return (
    <button type="button" onClick={() => window.location.reload()} className="font-mono text-[11px] tracking-wide text-primary uppercase underline">
      recarregar
    </button>
  )
}
