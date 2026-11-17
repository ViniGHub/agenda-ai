import { useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { XIcon } from 'lucide-react'
import { Dialog, DialogClose, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { cn, STAMP_BUTTON } from '@/lib/utils'
import { addEvent, interpretSentence, updateEvent } from '../api'
import type { AgendaEvent, Draft, EventDraft, Space } from '../types'
import { DateField, TimeField } from './DateTimeFields'

interface Props {
  open: boolean
  space: Space
  llm: boolean // without a language model the sentence box is hidden and the form is filled by hand
  seed: Draft | null // opened from a chat note: start filled in with what the model read
  editing: AgendaEvent | null // an event being edited: the form starts with its fields and saves over it
  onOpenChange: (open: boolean) => void
  onSaved: (event: AgendaEvent, edited: boolean) => void
}

const today = () => new Intl.DateTimeFormat('en-CA', { timeZone: 'America/Sao_Paulo' }).format(new Date())

const EMPTY = { titulo: '', data: '', diaInteiro: false, inicio: '09:00', fim: '', local: '', participantes: '', notas: '' }
type FormState = typeof EMPTY
type FieldName = keyof FormState

const MISSING_NAMES: Record<string, string> = { data: 'o dia', inicio: 'o horário' }

/** The form's fields for an event as the API returns it (times already in São Paulo). */
function fromEvent(e: AgendaEvent): FormState {
  const sameDay = e.inicio.slice(0, 10) === e.fim.slice(0, 10)
  return {
    titulo: e.titulo,
    data: e.inicio.slice(0, 10),
    diaInteiro: e.dia_inteiro,
    inicio: e.dia_inteiro ? '09:00' : e.inicio.slice(11, 16),
    fim: e.dia_inteiro || !sameDay ? '' : e.fim.slice(11, 16),
    local: e.local,
    participantes: e.participantes.join(', '),
    notas: e.notas,
  }
}

/** Longer than one day (a trip, an event past midnight): the form only holds one day. */
function spansDays(e: AgendaEvent): boolean {
  const days = (Date.parse(e.fim) - Date.parse(e.inicio)) / 86_400_000
  return e.dia_inteiro ? days > 1 : e.inicio.slice(0, 10) !== e.fim.slice(0, 10)
}

const EXAMPLES = ['dentista quinta às 15h na clínica Sorriso', 'almoço com a Ana amanhã ao meio-dia', 'aniversário da Bia dia 28']

/**
 * "New appointment", as a page torn from the same notebook, with a sticky note beside it where one
 * sentence can fill the page in (the model reads it on the server). Either way nothing is saved
 * until "Anotar na agenda".
 * The server validates everything again.
 */
export function EventForm({ open, space, llm, seed, editing, onOpenChange, onSaved }: Props) {
  const sentenceBox = llm && !editing // editing starts from the event itself, not from a sentence
  const [form, setForm] = useState<FormState>({ ...EMPTY, data: today() })
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)
  const [sentence, setSentence] = useState('')
  const [reading, setReading] = useState(false)
  const [sentenceNote, setSentenceNote] = useState<{ text: string; failed: boolean } | null>(null)
  const [filled, setFilled] = useState<Set<FieldName>>(new Set()) // came from the sentence and not edited since
  const [missing, setMissing] = useState<Set<FieldName>>(new Set())
  const submitRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    if (open) {
      setForm(editing ? fromEvent(editing) : { ...EMPTY, data: today() })
      setError(null)
      setSentence('')
      setSentenceNote(null)
      setFilled(new Set())
      setMissing(new Set())
      if (seed) apply(seed, 'conversa')
    }
    // reset (and fill from the chat note) only when the dialog opens
  }, [open])

  const set = (field: FieldName) => (value: string | boolean) => {
    setForm((f) => ({ ...f, [field]: value }))
    setFilled((s) => without(s, field))
    setMissing((s) => without(s, field))
  }

  const stateOf = (field: FieldName) => (missing.has(field) ? 'missing' : filled.has(field) ? 'filled' : undefined)

  function apply(s: Draft, source: 'frase' | 'conversa') {
    const next: FormState = {
      titulo: s.titulo,
      data: s.data ?? '', // left empty on purpose: the form asks instead of guessing
      diaInteiro: s.dia_inteiro,
      inicio: s.inicio ?? '',
      fim: s.fim ?? '',
      local: s.local,
      participantes: s.participantes.join(', '),
      notas: s.notas,
    }
    setForm(next)
    setFilled(new Set((Object.keys(next) as FieldName[]).filter((k) => next[k] !== '' && next[k] !== false)))
    setMissing(new Set(s.faltando))
    const lead = source === 'conversa' ? 'veio da conversa.' : 'entendi assim.'
    setSentenceNote({
      failed: false,
      text: s.faltando.length
        ? `${lead} falta ${s.faltando.map((f) => MISSING_NAMES[f]).join(' e ')}; o resto, confira antes de anotar.`
        : `${lead} confira os campos marcados: nada foi anotado ainda.`,
    })
    // Next stop: the first gap to fill, or the button that saves.
    requestAnimationFrame(() => {
      const gap = s.faltando[0]
      if (gap) document.getElementById(gap)?.focus()
      else submitRef.current?.focus()
    })
  }

  async function interpret(event: FormEvent) {
    event.preventDefault()
    const text = sentence.trim()
    if (!text || reading) return
    setReading(true)
    setSentenceNote(null)
    setError(null)
    try {
      apply(await interpretSentence(space, text), 'frase')
    } catch (e) {
      setSentenceNote({ failed: true, text: e instanceof Error ? e.message : String(e) })
    } finally {
      setReading(false)
    }
  }

  /** Shows the message and puts the cursor on the field (the browser's own messages would be in its language). */
  function fail(message: string, field: string) {
    setError(message)
    document.getElementById(field)?.focus()
  }

  async function submit(event: FormEvent) {
    event.preventDefault()
    setError(null)
    const typed = (id: string) => (document.getElementById(id) as HTMLInputElement | null)?.value.trim() ?? ''
    if (!form.titulo.trim()) return fail('Escreva o que é o compromisso.', 'titulo')
    if (!form.data) return fail(typed('data') ? 'Confira o dia: use dd/mm/aaaa.' : 'Informe o dia (dd/mm/aaaa).', 'data')
    if (!form.diaInteiro) {
      if (!form.inicio)
        return fail(typed('inicio') ? 'Confira o início: use hh:mm, de 00:00 a 23:59.' : 'Informe o horário de início (hh:mm) ou marque "Dia inteiro".', 'inicio')
      if (!form.fim && typed('fim')) return fail('Confira o fim: use hh:mm, de 00:00 a 23:59.', 'fim')
      if (form.fim && form.fim <= form.inicio) return fail('O fim precisa ser depois do início.', 'fim')
    }
    setSaving(true)
    try {
      const save = editing ? (draft: EventDraft) => updateEvent(space, editing.id, draft) : (draft: EventDraft) => addEvent(space, draft)
      const saved = await save({
        titulo: form.titulo,
        data: form.data,
        dia_inteiro: form.diaInteiro,
        inicio: form.diaInteiro ? null : form.inicio,
        fim: form.diaInteiro || !form.fim ? null : form.fim,
        local: form.local,
        participantes: form.participantes.split(',').map((p) => p.trim()).filter(Boolean),
        notas: form.notas,
      })
      onSaved(saved, editing !== null)
      onOpenChange(false)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      {/* Two separate pieces of paper: the sentence on a sticky note beside the page, the form on the page. */}
      <DialogContent
        showCloseButton={false}
        className={cn('max-h-[94dvh] overflow-y-auto bg-transparent p-1 shadow-none ring-0', sentenceBox ? 'sm:max-w-[54rem]' : 'sm:max-w-lg')}
      >
        <div className={cn('grid items-start gap-6', sentenceBox && 'md:grid-cols-[17.5rem_1fr]')}>
          {sentenceBox && (
            <aside
              aria-label="Escrever em uma frase"
              className="relative mx-2 mt-5 -rotate-1 rounded-[3px] bg-card bg-[linear-gradient(color-mix(in_oklab,var(--marker)_70%,transparent),color-mix(in_oklab,var(--marker)_70%,transparent))] px-4 pt-5 pb-3 shadow-[0_1px_0_rgb(0_0_0/0.08),0_14px_24px_-14px_rgb(0_0_0/0.45)] md:mx-0 md:mt-10 md:-rotate-2"
            >
              <span aria-hidden className="absolute -top-2.5 left-1/2 h-5 w-16 -translate-x-1/2 rotate-2 bg-card/70 shadow-sm" />
              <form onSubmit={interpret} className="grid gap-2">
                <Label htmlFor="frase" className="font-hand text-[1.75rem] leading-none font-normal text-primary">
                  diga em uma frase
                </Label>
                <p className="text-[13px] leading-snug text-foreground/75">
                  o modelo preenche o formulário; nada é anotado sem você conferir.
                </p>
                <textarea
                  id="frase"
                  autoFocus
                  rows={4}
                  maxLength={300}
                  value={sentence}
                  onChange={(e) => setSentence(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && !e.shiftKey) {
                      e.preventDefault() // Enter fills the form; Shift+Enter breaks the line
                      e.currentTarget.form?.requestSubmit()
                    }
                  }}
                  placeholder="dentista quinta às 15h na clínica Sorriso, levar o raio-x"
                  className="w-full resize-none rounded-[3px] border border-foreground/15 bg-card/70 px-3 py-2 font-hand text-[1.35rem] leading-snug text-primary outline-none placeholder:text-pencil/70 focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/40"
                />
                <div className="flex items-center justify-between gap-2">
                  <span className="font-mono text-[11px] text-pencil tabular-nums">{sentence.length}/300</span>
                  <Button type="submit" variant="ghost" disabled={reading || !sentence.trim()}
                    className={cn(STAMP_BUTTON, 'h-8 shrink-0 bg-card/60 px-3 text-[11px]')}>
                    {reading ? 'Lendo…' : 'Preencher →'}
                  </Button>
                </div>
                <p role="status" className={cn('min-h-[1.2rem] font-hand text-[1.15rem] leading-tight', sentenceNote?.failed ? 'text-primary' : 'text-foreground/70')}>
                  {sentenceNote?.text ?? (space === 'demo' ? 'é uma demonstração: não use dados pessoais.' : '')}
                </p>
                <div className="border-t border-dashed border-foreground/20 pt-2">
                  <p className="font-mono text-[10px] tracking-wide text-pencil uppercase">experimente</p>
                  <ul>
                    {EXAMPLES.map((example) => (
                      <li key={example}>
                        <button type="button" onClick={() => setSentence(example)}
                          className="text-left font-hand text-[1.15rem] leading-snug text-foreground/80 underline-offset-2 hover:text-primary hover:underline">
                          → {example}
                        </button>
                      </li>
                    ))}
                  </ul>
                </div>
              </form>
            </aside>
          )}

          <div className="paper-plain relative grid gap-4 rounded-xl bg-card p-5 text-popover-foreground ring-1 ring-foreground/10 shadow-[0_24px_48px_-24px_rgb(0_0_0/0.45)]">
            <DialogClose asChild>
              <Button variant="ghost" size="icon-sm" className="absolute top-2 right-2">
                <XIcon />
                <span className="sr-only">Fechar</span>
              </Button>
            </DialogClose>
            <DialogHeader>
              <DialogTitle className="font-heading text-2xl font-semibold tracking-tight">
                {editing ? 'Editar compromisso' : 'Novo compromisso'}
              </DialogTitle>
              <DialogDescription className="font-hand text-[1.2rem] text-pencil">
                {editing
                  ? 'as próximas perguntas já usam o que você mudar.'
                  : space === 'demo'
                    ? 'fica só para você, nesta agenda fictícia, por duas horas.'
                    : 'entra na sua agenda e já pode ser perguntado.'}
              </DialogDescription>
              {editing && spansDays(editing) && (
                <p className="font-hand text-[1.15rem] leading-tight text-primary">
                  este compromisso ocupa mais de um dia; ao salvar, ele passa a ocupar só o dia escolhido.
                </p>
              )}
            </DialogHeader>

            <form onSubmit={submit} noValidate className="grid gap-4">
              <Field label="O quê" htmlFor="titulo" state={stateOf('titulo')}>
                <Input id="titulo" aria-required maxLength={120} autoFocus={!sentenceBox} value={form.titulo}
                  onChange={(e) => set('titulo')(e.target.value)} placeholder="Dentista, reunião com a padaria…" />
              </Field>

              <div className="grid grid-cols-2 gap-3">
                <Field label="Dia" htmlFor="data" state={stateOf('data')}>
                  <DateField id="data" value={form.data} onChange={set('data')} />
                </Field>
                <div className="flex items-end gap-2 pb-2">
                  <Checkbox id="dia-inteiro" checked={form.diaInteiro} onCheckedChange={(v) => set('diaInteiro')(v === true)} />
                  <Label htmlFor="dia-inteiro" className="font-normal">
                    <span className={cn(stateOf('diaInteiro') === 'filled' && 'marker')}>Dia inteiro</span>
                  </Label>
                </div>
              </div>

              {!form.diaInteiro && (
                <div className="grid grid-cols-2 gap-3">
                  <Field label="Começa" htmlFor="inicio" state={stateOf('inicio')}>
                    <TimeField id="inicio" value={form.inicio} onChange={set('inicio')} />
                  </Field>
                  <Field label="Termina (opcional)" htmlFor="fim" state={stateOf('fim')}>
                    <TimeField id="fim" value={form.fim} onChange={set('fim')} />
                  </Field>
                </div>
              )}

              <Field label="Onde (opcional)" htmlFor="local" state={stateOf('local')}>
                <Input id="local" maxLength={120} value={form.local} onChange={(e) => set('local')(e.target.value)} />
              </Field>
              <Field label="Com quem (separe por vírgula)" htmlFor="participantes" state={stateOf('participantes')}>
                <Input id="participantes" value={form.participantes} onChange={(e) => set('participantes')(e.target.value)}
                  placeholder="Ana, Bruno" />
              </Field>
              <Field label="Notas (opcional)" htmlFor="notas" state={stateOf('notas')}>
                <textarea id="notas" maxLength={1000} rows={3} value={form.notas} onChange={(e) => set('notas')(e.target.value)}
                  placeholder="o que levar, o que combinar…"
                  className="w-full resize-none rounded-md border border-input bg-transparent px-3 py-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50" />
              </Field>

              {error && <p role="alert" className="font-hand text-[1.25rem] leading-tight text-primary">{error}</p>}

              <div className="flex items-center justify-end gap-4">
                <button type="button" onClick={() => onOpenChange(false)} className="font-mono text-[11px] tracking-wide text-pencil uppercase">
                  cancelar
                </button>
                <Button ref={submitRef} type="submit" variant="ghost" disabled={saving}
                  className={cn(STAMP_BUTTON, '-rotate-1 px-4 py-2 text-[12px]')}>
                  {saving ? (editing ? 'Salvando…' : 'Anotando…') : editing ? 'Salvar alterações' : 'Anotar na agenda'}
                </Button>
              </div>
            </form>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}

function without<T>(set: Set<T>, item: T): Set<T> {
  if (!set.has(item)) return set
  const next = new Set(set)
  next.delete(item)
  return next
}

/** A labelled field. "filled": came from the sentence (highlighted); "missing": the sentence did not say it. */
function Field({ label, htmlFor, state, children }: {
  label: string
  htmlFor: string
  state?: 'filled' | 'missing'
  children: ReactNode
}) {
  return (
    <div className="grid gap-1.5">
      <Label htmlFor={htmlFor} className="font-mono text-[11px] font-normal tracking-wide text-pencil uppercase">
        <span className={cn(state === 'filled' && 'marker text-foreground')}>{label}</span>
        {state === 'missing' && (
          <span className="font-hand text-[1.1rem] leading-none tracking-normal text-primary normal-case">falta</span>
        )}
      </Label>
      {children}
    </div>
  )
}
