import { ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight, Plus } from 'lucide-react'
import { Fragment, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { Dialog, DialogContent, DialogDescription, DialogTitle } from '@/components/ui/dialog'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { useMediaQuery } from '@/lib/media'
import { cn } from '@/lib/utils'
import { dayKey } from '../format'
import type { AgendaEvent } from '../types'
import { EventRow } from './EventRow'

interface Props {
  events: AgendaEvent[]
  loaded: boolean
  citedIds: Set<number>
  focusedEventId: number | null
  onEdit: (event: AgendaEvent) => void
  onDelete: (event: AgendaEvent) => void
  onNew: (date: string) => void // "YYYY-MM-DD": a day without a note opens "Novo compromisso" on it
}

interface Month {
  year: number
  month: number // 0 = January
}

const WEEKDAYS = ['dom', 'seg', 'ter', 'qua', 'qui', 'sex', 'sáb'] // the week starts on Sunday
const MONTHS = ['janeiro', 'fevereiro', 'março', 'abril', 'maio', 'junho', 'julho', 'agosto', 'setembro', 'outubro', 'novembro', 'dezembro']
const TILTS = ['-rotate-2', 'rotate-1', '-rotate-1', 'rotate-2'] // each note stuck on a little crooked
const MAX_SPAN_DAYS = 62

// Day keys ("2026-11-18") are São Paulo dates; the arithmetic runs on UTC midnights so no clock shift moves a day.
const addDays = (key: string, days: number) => {
  const date = new Date(`${key}T00:00:00Z`)
  date.setUTCDate(date.getUTCDate() + days)
  return date.toISOString().slice(0, 10)
}
const monthOf = (key: string): Month => ({ year: Number(key.slice(0, 4)), month: Number(key.slice(5, 7)) - 1 })
const shift = (m: Month, months: number): Month => {
  const date = new Date(Date.UTC(m.year, m.month + months, 1))
  return { year: date.getUTCFullYear(), month: date.getUTCMonth() }
}
const monthPrefix = (m: Month) => `${m.year}-${String(m.month + 1).padStart(2, '0')}`
const longDay = new Intl.DateTimeFormat('pt-BR', { timeZone: 'UTC', weekday: 'long', day: 'numeric', month: 'long' })
const dayTitle = (key: string) => {
  const label = longDay.format(new Date(`${key}T12:00:00Z`))
  return label.charAt(0).toUpperCase() + label.slice(1)
}
const plural = (n: number) => `${n} compromisso${n === 1 ? '' : 's'}`

/** Every event under each day it covers: a trip shows on all its days. All-day first, then by time. */
function eventsByDay(events: AgendaEvent[]): Map<string, AgendaEvent[]> {
  const days = new Map<string, AgendaEvent[]>()
  for (const event of events) {
    const first = dayKey(event.inicio)
    // The end is exclusive: an all-day event ending at 00:00 of the 21st covers up to the 20th.
    const last = dayKey(new Date(Math.max(Date.parse(event.fim) - 1, Date.parse(event.inicio))).toISOString())
    let key = first
    for (let i = 0; i < MAX_SPAN_DAYS && key <= last; i++, key = addDays(key, 1)) {
      days.set(key, [...(days.get(key) ?? []), event])
    }
  }
  for (const list of days.values()) {
    list.sort((a, b) => Number(b.dia_inteiro) - Number(a.dia_inteiro) || a.inicio.localeCompare(b.inicio))
  }
  return days
}

/** The month as a wall calendar: a square per day, a sticky note on each day with appointments. */
export function AgendaCalendar({ events, loaded, citedIds, focusedEventId, onEdit, onDelete, onNew }: Props) {
  const todayKey = dayKey(new Date().toISOString())
  const days = useMemo(() => eventsByDay(events), [events])
  const [shown, setShown] = useState<Month>(() => monthOf(todayKey))
  const [openDay, setOpenDay] = useState<string | null>(null)
  const desktop = useMediaQuery('(min-width: 1024px)')
  const eventsRef = useRef(events)
  eventsRef.current = events

  // An appointment clicked in an answer: turn to its month and open its day.
  useEffect(() => {
    if (focusedEventId === null) return
    const event = eventsRef.current.find((e) => e.id === focusedEventId)
    if (!event) return
    const key = dayKey(event.inicio)
    setShown(monthOf(key))
    setOpenDay(key)
  }, [focusedEventId])

  // The last appointment of an open day was deleted: close its note.
  const openEvents = openDay ? (days.get(openDay) ?? []) : []
  useEffect(() => {
    if (openDay && openEvents.length === 0) setOpenDay(null)
  }, [openDay, openEvents.length])

  const prefix = monthPrefix(shown)
  const first = `${prefix}-01`
  const lead = new Date(`${first}T00:00:00Z`).getUTCDay()
  const length = new Date(Date.UTC(shown.year, shown.month + 1, 0)).getUTCDate()
  const cells = Array.from({ length: Math.ceil((lead + length) / 7) * 7 }, (_, i) => addDays(first, i - lead))
  const inMonth = events.filter((e) => dayKey(e.inicio).startsWith(prefix)).length
  const isCurrent = todayKey.startsWith(prefix)

  // Closing the note before opening the form or deleting: the dialog then has the screen to itself.
  const note = (key: string) => (
    <DayNote
      day={key}
      events={days.get(key) ?? []}
      citedIds={citedIds}
      focusedEventId={focusedEventId}
      showTitle={desktop}
      onEdit={(event) => {
        setOpenDay(null)
        onEdit(event)
      }}
      onDelete={onDelete}
      onNew={() => {
        setOpenDay(null)
        onNew(key)
      }}
    />
  )

  if (!loaded) return <p className="px-6 py-[var(--line)] font-hand text-[1.4rem] text-pencil">abrindo a agenda…</p>

  return (
    <div className="paper-plain h-full overflow-y-auto px-3 pb-6 sm:px-5">
      <div className="flex items-center gap-0.5 pt-3">
        <NavButton label="Ano anterior" onClick={() => setShown(shift(shown, -12))}>
          <ChevronsLeft />
        </NavButton>
        <NavButton label="Mês anterior" onClick={() => setShown(shift(shown, -1))}>
          <ChevronLeft />
        </NavButton>
        <h3 aria-live="polite" className="flex-1 text-center font-heading text-[1.35rem] leading-tight font-medium tracking-tight">
          {MONTHS[shown.month].charAt(0).toUpperCase() + MONTHS[shown.month].slice(1)}{' '}
          <span className="text-pencil">{shown.year}</span>
        </h3>
        <NavButton label="Próximo mês" onClick={() => setShown(shift(shown, 1))}>
          <ChevronRight />
        </NavButton>
        <NavButton label="Próximo ano" onClick={() => setShown(shift(shown, 12))}>
          <ChevronsRight />
        </NavButton>
      </div>
      <div className="flex h-6 items-center justify-between px-1 font-mono text-[10.5px] tracking-wide text-pencil uppercase">
        <span>{inMonth === 0 ? 'nenhum compromisso no mês' : `${plural(inMonth)} no mês`}</span>
        {!isCurrent && (
          <button type="button" onClick={() => setShown(monthOf(todayKey))} className="text-primary uppercase underline">
            voltar para hoje
          </button>
        )}
      </div>

      <div aria-label={`${MONTHS[shown.month]} de ${shown.year}`} className="mt-1 grid grid-cols-7 gap-1">
        {WEEKDAYS.map((name) => (
          <div key={name} aria-hidden className="pb-1 text-center font-mono text-[10.5px] tracking-wide text-pencil uppercase">
            {name}
          </div>
        ))}
        {cells.map((key) => {
          const list = days.get(key) ?? []
          const day = Number(key.slice(8))
          const label = `${dayTitle(key)}: ${list.length ? plural(list.length) : 'nenhum compromisso, anotar um'}`
          const cell = (
            <button
              type="button"
              aria-label={label}
              onClick={list.length ? (desktop ? undefined : () => setOpenDay(key)) : () => onNew(key)}
              className={cn(
                'group relative flex aspect-square min-w-0 flex-col gap-0.5 rounded-[4px] border border-border/70 bg-card/60 p-1 text-left transition-colors hover:bg-muted/50 focus-visible:ring-2 focus-visible:ring-ring/60 focus-visible:outline-none',
                !key.startsWith(prefix) && 'opacity-40',
                openDay === key && 'bg-muted/60',
              )}
            >
              <span
                className={cn(
                  'self-start font-mono text-[11px] leading-4 tabular-nums',
                  key === todayKey ? 'rounded-full bg-primary px-1.5 text-primary-foreground' : 'text-pencil',
                )}
              >
                {day}
              </span>
              {list.length > 0 ? (
                <PostIt
                  events={list}
                  tilt={TILTS[day % TILTS.length]}
                  marked={list.some((e) => citedIds.has(e.id) || e.id === focusedEventId)}
                />
              ) : (
                <Plus aria-hidden className="m-auto size-4 text-pencil opacity-0 transition-opacity group-hover:opacity-60" />
              )}
            </button>
          )
          if (!desktop || list.length === 0) return <Fragment key={key}>{cell}</Fragment>
          return (
            <Popover key={key} open={openDay === key} onOpenChange={(open) => setOpenDay(open ? key : null)}>
              <PopoverTrigger asChild>{cell}</PopoverTrigger>
              <PopoverContent align="center" collisionPadding={12} className="max-h-(--radix-popover-content-available-height) w-[24rem] overflow-y-auto p-0">
                {note(key)}
              </PopoverContent>
            </Popover>
          )
        })}
      </div>

      {/* On a phone the day's note slides up from the bottom, wide enough to read and tap. */}
      {!desktop && (
        <Dialog open={openDay !== null} onOpenChange={(open) => !open && setOpenDay(null)}>
          <DialogContent className="top-auto bottom-0 left-0 max-h-[78dvh] w-full max-w-full translate-x-0 translate-y-0 gap-2 overflow-y-auto rounded-t-2xl rounded-b-none p-0 pb-[env(safe-area-inset-bottom)] sm:max-w-full">
            <DialogTitle className="px-5 pt-5 font-heading text-lg font-medium">{openDay ? dayTitle(openDay) : ''}</DialogTitle>
            <DialogDescription className="sr-only">Compromissos deste dia</DialogDescription>
            {openDay && note(openDay)}
          </DialogContent>
        </Dialog>
      )}
    </div>
  )
}

function NavButton({ label, onClick, children }: { label: string; onClick: () => void; children: ReactNode }) {
  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      onClick={onClick}
      className="flex size-9 shrink-0 items-center justify-center rounded-full text-pencil hover:bg-muted hover:text-primary [&_svg]:size-5"
    >
      {children}
    </button>
  )
}

/** The note on a day: on a phone how many; on a computer the first two titles and "+N" for the rest. */
function PostIt({ events, tilt, marked }: { events: AgendaEvent[]; tilt: string; marked: boolean }) {
  const more = events.length - 2
  return (
    <span
      className={cn(
        'postit relative mt-auto flex min-h-0 w-full flex-col justify-center rounded-[2px] px-1 py-0.5 transition-transform group-hover:-translate-y-0.5',
        tilt,
        marked && 'ring-[1.5px] ring-primary',
      )}
    >
      <span className="text-center font-mono text-[12px] leading-4 font-medium text-foreground/80 tabular-nums lg:hidden">
        {events.length}
      </span>
      {events.slice(0, 2).map((event) => (
        <span key={event.id} className="hidden truncate font-hand text-[0.95rem] leading-[1.05rem] text-foreground lg:block">
          {event.titulo}
        </span>
      ))}
      {more > 0 && (
        <span className="absolute -top-1.5 -right-1 hidden rounded-full bg-primary px-1 font-mono text-[9px] leading-[0.875rem] text-primary-foreground tabular-nums lg:block">
          +{more}
        </span>
      )}
    </span>
  )
}

interface DayNoteProps {
  day: string
  events: AgendaEvent[]
  citedIds: Set<number>
  focusedEventId: number | null
  showTitle: boolean // the phone's sheet has its own title
  onEdit: (event: AgendaEvent) => void
  onDelete: (event: AgendaEvent) => void
  onNew: () => void
}

/** What opens from a sticky note: the day's appointments, with edit and delete, on a bigger note. */
function DayNote({ day, events, citedIds, focusedEventId, showTitle, onEdit, onDelete, onNew }: DayNoteProps) {
  const focusedRef = useRef<HTMLLIElement>(null)
  useEffect(() => {
    // The appointment clicked in an answer, in view. After the note has been placed and sized.
    const timer = window.setTimeout(() => focusedRef.current?.scrollIntoView({ block: 'nearest' }), 150)
    return () => window.clearTimeout(timer)
  }, [focusedEventId])

  return (
    <div className="postit relative rounded-[4px] px-4 pt-4 pb-3">
      <span aria-hidden className="absolute -top-2 left-1/2 h-4 w-14 -translate-x-1/2 rotate-2 bg-card/70 shadow-sm" />
      {showTitle && <p className="font-heading text-base font-medium">{dayTitle(day)}</p>}
      <ul className="divide-y divide-foreground/10">
        {events.map((event) => (
          <li
            key={event.id}
            ref={event.id === focusedEventId ? focusedRef : undefined}
            className={cn('py-2 text-[14px]', event.id === focusedEventId && 'rounded-md ring-[1.5px] ring-primary ring-offset-2 ring-offset-transparent')}
          >
            <EventRow event={event} cited={citedIds.has(event.id)} onEdit={onEdit} onDelete={onDelete} />
          </li>
        ))}
      </ul>
      <button type="button" onClick={onNew} className="mt-1 font-mono text-[11px] tracking-wide text-primary uppercase hover:underline">
        + anotar neste dia
      </button>
    </div>
  )
}
