import { CalendarIcon } from 'lucide-react'
import { useEffect, useState } from 'react'
import { ptBR } from 'react-day-picker/locale'
import { Calendar } from '@/components/ui/calendar'
import { Input } from '@/components/ui/input'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'

// The browser's own date and time inputs follow the browser's language (mm/dd/yyyy, 09:00 AM in an
// English Chrome), whatever the page says. These fields always read and write the Brazilian way:
// dd/mm/aaaa and 24-hour hh:mm. The form keeps the API's formats (YYYY-MM-DD and HH:MM).

/** "2026-11-19" -> "19/11/2026" */
const toDisplayDate = (iso: string) => (iso ? iso.split('-').reverse().join('/') : '')

/** "19/11/2026" -> "2026-11-19"; '' while incomplete or impossible (31/02). */
function toIsoDate(display: string): string {
  const m = /^(\d{2})\/(\d{2})\/(\d{4})$/.exec(display)
  if (!m) return ''
  const [, d, mo, y] = m
  const date = new Date(Date.UTC(Number(y), Number(mo) - 1, Number(d)))
  const real = date.getUTCFullYear() === Number(y) && date.getUTCMonth() === Number(mo) - 1 && date.getUTCDate() === Number(d)
  return real ? `${y}-${mo}-${d}` : ''
}

/** Digits typed in any way -> "dd/mm/aaaa" as far as they go. */
function maskDate(raw: string): string {
  const d = raw.replace(/\D/g, '').slice(0, 8)
  if (d.length <= 2) return d
  if (d.length <= 4) return `${d.slice(0, 2)}/${d.slice(2)}`
  return `${d.slice(0, 2)}/${d.slice(2, 4)}/${d.slice(4)}`
}

/** Digits -> "hh:mm"; a first digit above 2 is an hour of its own ("930" -> "09:30"). */
function maskTime(raw: string): string {
  let d = raw.replace(/\D/g, '')
  if (d && Number(d[0]) > 2) d = `0${d}`
  d = d.slice(0, 4)
  return d.length <= 2 ? d : `${d.slice(0, 2)}:${d.slice(2)}`
}

/** "15:30" if it is a real 24-hour time, else ''. */
function toClock(display: string): string {
  const m = /^(\d{2}):(\d{2})$/.exec(display)
  return m && Number(m[1]) < 24 && Number(m[2]) < 60 ? display : ''
}

const isoOf = (date: Date) =>
  `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`

interface FieldProps {
  id: string
  value: string // the API format, or '' when empty or not valid yet
  onChange: (value: string) => void
}

/** dd/mm/aaaa, typed with the numeric keyboard or picked from a calendar in Portuguese. */
export function DateField({ id, value, onChange }: FieldProps) {
  const [text, setText] = useState(toDisplayDate(value))
  const [open, setOpen] = useState(false)

  useEffect(() => {
    // Follow changes from outside (a sentence filled the form) without undoing what is being typed.
    if (toIsoDate(text) !== value) setText(toDisplayDate(value))
  }, [value]) // only when the value changes: `text` is what is being typed

  const selected = value ? new Date(Number(value.slice(0, 4)), Number(value.slice(5, 7)) - 1, Number(value.slice(8, 10))) : undefined

  return (
    <div className="relative">
      <Input
        id={id}
        inputMode="numeric"
        autoComplete="off"
        placeholder="dd/mm/aaaa"
        maxLength={10}
        value={text}
        aria-required
        onChange={(e) => {
          const next = maskDate(e.target.value)
          setText(next)
          onChange(toIsoDate(next))
        }}
        className="pr-9 tabular-nums"
      />
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <button type="button" aria-label="Escolher no calendário"
            className="absolute top-1/2 right-1.5 -translate-y-1/2 rounded p-1 text-pencil hover:text-primary">
            <CalendarIcon className="size-4" />
          </button>
        </PopoverTrigger>
        <PopoverContent align="end" className="w-auto p-0">
          <Calendar
            mode="single"
            locale={ptBR}
            selected={selected}
            defaultMonth={selected}
            onSelect={(date) => {
              if (!date) return
              onChange(isoOf(date))
              setOpen(false)
            }}
          />
        </PopoverContent>
      </Popover>
    </div>
  )
}

/** hh:mm in 24 hours. "15" becomes "15:00" when the field is left. */
export function TimeField({ id, value, onChange }: FieldProps) {
  const [text, setText] = useState(value)

  useEffect(() => {
    if (toClock(text) !== value) setText(value)
  }, [value]) // only when the value changes: `text` is what is being typed

  return (
    <Input
      id={id}
      inputMode="numeric"
      autoComplete="off"
      placeholder="hh:mm"
      maxLength={5}
      value={text}
      onChange={(e) => {
        const next = maskTime(e.target.value)
        setText(next)
        onChange(toClock(next))
      }}
      onBlur={() => {
        if (/^\d{1,2}$/.test(text) && Number(text) < 24) {
          const whole = `${text.padStart(2, '0')}:00`
          setText(whole)
          onChange(whole)
        }
      }}
      className="tabular-nums"
    />
  )
}
