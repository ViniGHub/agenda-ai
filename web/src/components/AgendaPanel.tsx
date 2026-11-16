import { useEffect, useMemo, useRef } from 'react'
import { dayKey, dayLabel, timeLabel } from '../format'
import type { AgendaEvent } from '../types'

interface Props {
  events: AgendaEvent[]
  citedIds: Set<number>
  focusedEventId: number | null
}

/** The whole agenda, day by day: the "answer key" to check what the assistant says. */
export function AgendaPanel({ events, citedIds, focusedEventId }: Props) {
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
    // Open on the event clicked in an answer, or on today. Scrolls only the panel, never the page.
    const panel = panelRef.current
    const target =
      (focusedEventId !== null && itemRefs.current.get(focusedEventId)) ||
      document.getElementById(`dia-${todayKey}`)
    if (panel && target) {
      panel.scrollTop = target.offsetTop - panel.clientHeight / 2 + target.clientHeight / 2
    }
  }, [focusedEventId, todayKey, days])

  if (events.length === 0) return <div className="panel panel-empty">Carregando a agenda…</div>

  return (
    <div className="panel agenda" ref={panelRef}>
      {days.map(([key, dayEvents]) => (
        <section key={key} id={`dia-${key}`} className={key === todayKey ? 'day is-today' : 'day'}>
          <h3>
            {dayLabel(dayEvents[0].inicio)}
            {key === todayKey && <span className="tag">hoje</span>}
          </h3>
          <ul>
            {dayEvents.map((event) => (
              <li
                key={event.id}
                ref={(node) => {
                  if (node) itemRefs.current.set(event.id, node)
                  else itemRefs.current.delete(event.id)
                }}
                className={[
                  'event',
                  citedIds.has(event.id) ? 'is-cited' : '',
                  event.id === focusedEventId ? 'is-focused' : '',
                ].join(' ')}
              >
                <span className="event-time">{timeLabel(event)}</span>
                <div>
                  <strong>{event.titulo}</strong>
                  {event.local && <div className="muted">{event.local}</div>}
                  {event.participantes.length > 0 && <div className="muted">Com: {event.participantes.join(', ')}</div>}
                  {event.notas && <div className="event-notes">{event.notas}</div>}
                </div>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  )
}
