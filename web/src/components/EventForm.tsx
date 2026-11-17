import { useEffect, useRef, useState, type FormEvent, type ReactNode } from 'react'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { cn, STAMP_BUTTON } from '@/lib/utils'
import { addEvent, interpretSentence } from '../api'
import type { AgendaEvent, Space, Suggestion } from '../types'

interface Props {
  open: boolean
  space: Space
  llm: boolean // without a language model the sentence box is hidden and the form is filled by hand
  onOpenChange: (open: boolean) => void
  onSaved: (event: AgendaEvent) => void
}

const today = () => new Intl.DateTimeFormat('en-CA', { timeZone: 'America/Sao_Paulo' }).format(new Date())

const EMPTY = { titulo: '', data: '', diaInteiro: false, inicio: '09:00', fim: '', local: '', participantes: '', notas: '' }
type FormState = typeof EMPTY
type FieldName = keyof FormState

const MISSING_NAMES: Record<string, string> = { data: 'o dia', inicio: 'o horário' }

/**
 * "New appointment", as a page torn from the same notebook. A sentence can fill it in first
 * (the model reads it on the server); either way nothing is saved until "Anotar na agenda".
 * The server validates everything again.
 */
export function EventForm({ open, space, llm, onOpenChange, onSaved }: Props) {
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
      setForm({ ...EMPTY, data: today() })
      setError(null)
      setSentence('')
      setSentenceNote(null)
      setFilled(new Set())
      setMissing(new Set())
    }
  }, [open])

  const set = (field: FieldName) => (value: string | boolean) => {
    setForm((f) => ({ ...f, [field]: value }))
    setFilled((s) => without(s, field))
    setMissing((s) => without(s, field))
  }

  const stateOf = (field: FieldName) => (missing.has(field) ? 'missing' : filled.has(field) ? 'filled' : undefined)

  function apply(s: Suggestion) {
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
    setSentenceNote({
      failed: false,
      text: s.faltando.length
        ? `entendi assim. falta ${s.faltando.map((f) => MISSING_NAMES[f]).join(' e ')}; o resto, confira antes de anotar.`
        : 'entendi assim. confira os campos marcados: nada foi anotado ainda.',
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
      apply(await interpretSentence(space, text))
    } catch (e) {
      setSentenceNote({ failed: true, text: e instanceof Error ? e.message : String(e) })
    } finally {
      setReading(false)
    }
  }

  async function submit(event: FormEvent) {
    event.preventDefault()
    setError(null)
    if (!form.diaInteiro && form.fim && form.fim <= form.inicio) {
      setError('O fim precisa ser depois do início.')
      return
    }
    setSaving(true)
    try {
      const saved = await addEvent(space, {
        titulo: form.titulo,
        data: form.data,
        dia_inteiro: form.diaInteiro,
        inicio: form.diaInteiro ? null : form.inicio,
        fim: form.diaInteiro || !form.fim ? null : form.fim,
        local: form.local,
        participantes: form.participantes.split(',').map((p) => p.trim()).filter(Boolean),
        notas: form.notas,
      })
      onSaved(saved)
      onOpenChange(false)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="paper-plain max-h-[92dvh] overflow-y-auto border-border bg-card sm:max-w-lg">
        <DialogHeader>
          <DialogTitle className="font-heading text-2xl font-semibold tracking-tight">Novo compromisso</DialogTitle>
          <DialogDescription className="font-hand text-[1.2rem] text-pencil">
            {space === 'demo'
              ? 'fica só para você, nesta agenda fictícia, por duas horas.'
              : 'entra na sua agenda e já pode ser perguntado.'}
          </DialogDescription>
        </DialogHeader>

        {llm && (
          <form onSubmit={interpret} className="grid gap-1.5 border-b border-dashed border-border pb-4">
            <Label htmlFor="frase" className="font-mono text-[11px] font-normal tracking-wide text-pencil uppercase">
              Em uma frase
            </Label>
            <div className="flex gap-2">
              <Input id="frase" autoFocus autoComplete="off" maxLength={300} value={sentence}
                onChange={(e) => setSentence(e.target.value)} placeholder="dentista quinta às 15h na clínica Sorriso" />
              <Button type="submit" variant="ghost" disabled={reading || !sentence.trim()}
                className={cn(STAMP_BUTTON, 'h-8 shrink-0 px-3 text-[11px]')}>
                {reading ? 'Lendo…' : 'Preencher'}
              </Button>
            </div>
            <p role="status" className={cn('font-hand text-[1.15rem] leading-tight', sentenceNote?.failed ? 'text-primary' : 'text-pencil')}>
              {sentenceNote?.text ??
                (space === 'demo'
                  ? 'um modelo de linguagem preenche os campos abaixo e você confere. é uma demonstração: não use dados pessoais.'
                  : 'um modelo de linguagem preenche os campos abaixo e você confere antes de anotar.')}
            </p>
          </form>
        )}

        <form onSubmit={submit} className="grid gap-4">
          <Field label="O quê" htmlFor="titulo" state={stateOf('titulo')}>
            <Input id="titulo" required maxLength={120} autoFocus={!llm} value={form.titulo}
              onChange={(e) => set('titulo')(e.target.value)} placeholder="Dentista, reunião com a padaria…" />
          </Field>

          <div className="grid grid-cols-2 gap-3">
            <Field label="Dia" htmlFor="data" state={stateOf('data')}>
              <Input id="data" type="date" required value={form.data} onChange={(e) => set('data')(e.target.value)} />
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
                <Input id="inicio" type="time" required value={form.inicio} onChange={(e) => set('inicio')(e.target.value)} />
              </Field>
              <Field label="Termina (opcional)" htmlFor="fim" state={stateOf('fim')}>
                <Input id="fim" type="time" value={form.fim} onChange={(e) => set('fim')(e.target.value)} />
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
              {saving ? 'Anotando…' : 'Anotar na agenda'}
            </Button>
          </div>
        </form>
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
