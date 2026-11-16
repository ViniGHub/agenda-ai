import { useEffect, useMemo, useRef } from 'react'
import { cn } from '@/lib/utils'
import { dayKey, dayLabel, timeLabel } from '../format'
import type { AgendaEvent } from '../types'

interface Props {
  events: AgendaEvent[]
  loaded: boolean
  citedIds: Set<number>
  focusedEventId: number | null
}

/** The whole agenda, day by day, like a planner: the answer key to check what the assistant says. */
export function AgendaPanel({ events, loaded, citedIds, focusedEventId }: Props) {
  const panelRef = useRef<HTMLDivElement>(null)
  const itemRefs = useRef(new Map<number, HTMLLIElement>())
  const todayKey = dayKey(new Date().toISOString())

  const days = useMemo(() => {
    const groups = new Map<string, AgendaEvent[]>()
    for (const event of events) {
      const key = dayKey(event.inicio)
      groups.set(key, [...(groups.get(key) ?? []), event])
    }
    return [...groups.entries()]
  }, [events])

  useEffect(() => {
    // Open on the event clicked in an answer, or on today. Scrolls only the page, never the window.
    const panel = panelRef.current
    const target =
      (focusedEventId !== null && itemRefs.current.get(focusedEventId)) || document.getElementById(`dia-${todayKey}`)
    if (panel && target) panel.scrollTop = target.offsetTop - panel.clientHeight / 3
  }, [focusedEventId, todayKey, days])

  if (events.length === 0) {
    return (
      <p className="px-6 py-[var(--line)] font-hand text-[1.4rem] text-pencil">
        {loaded ? 'nenhum compromisso por aqui ainda.' : 'abrindo a agenda…'}
      </p>
    )
  }

  return (
    <div ref={panelRef} className="paper-plain relative h-full overflow-y-auto px-6 pb-[var(--line)]">
      {days.map(([key, dayEvents]) => (
        <section key={key} id={`dia-${key}`}>
          <h3 className="sticky top-0 z-10 flex items-center gap-3 bg-card pt-[calc(var(--line)/2)] font-heading text-lg leading-[calc(var(--line)*1.5)] font-medium tracking-tight">
            {dayLabel(dayEvents[0].inicio)}
            {key === todayKey && <span className="stamp -rotate-3 px-1.5 text-[10px] leading-5 text-primary">hoje</span>}
          </h3>
          <ul>
            {dayEvents.map((event) => (
              <li
                key={event.id}
                ref={(node) => {
                  if (node) itemRefs.current.set(event.id, node)
                  else itemRefs.current.delete(event.id)
                }}
                className={cn(
                  'grid grid-cols-[6rem_1fr] gap-x-3 py-[calc(var(--line)/4)] text-[14px]',
                  event.id === focusedEventId && 'rounded-[18px_8px_16px_10px] ring-[1.5px] ring-primary ring-offset-4 ring-offset-card',
                )}
              >
                <span className="font-mono text-[12px] leading-6 text-pencil tabular-nums">{timeLabel(event)}</span>
                <div className="min-w-0 leading-6">
                  <div className="font-medium">
                    {citedIds.has(event.id) ? <mark className="marker bg-transparent text-inherit">{event.titulo}</mark> : event.titulo}
                  </div>
                  {event.local && <div className="text-muted-foreground">{event.local}</div>}
                  {event.participantes.length > 0 && (
                    <div className="text-muted-foreground">com {event.participantes.join(', ')}</div>
                  )}
                  {event.notas && <div className="text-foreground/80">{event.notas}</div>}
                </div>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  )
}
