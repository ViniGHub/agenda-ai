import { useEffect, useState, type FormEvent, type ReactNode } from 'react'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { cn, STAMP_BUTTON } from '@/lib/utils'
import { addEvent } from '../api'
import type { AgendaEvent, Space } from '../types'

interface Props {
  open: boolean
  space: Space
  onOpenChange: (open: boolean) => void
  onSaved: (event: AgendaEvent) => void
}

const today = () => new Intl.DateTimeFormat('en-CA', { timeZone: 'America/Sao_Paulo' }).format(new Date())

const EMPTY = { titulo: '', data: '', diaInteiro: false, inicio: '09:00', fim: '', local: '', participantes: '', notas: '' }

/** "New appointment", as a page torn from the same notebook. The server validates everything again. */
export function EventForm({ open, space, onOpenChange, onSaved }: Props) {
  const [form, setForm] = useState({ ...EMPTY, data: today() })
  const [error, setError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (open) {
      setForm({ ...EMPTY, data: today() })
      setError(null)
    }
  }, [open])

  const set = (field: keyof typeof form) => (value: string | boolean) => setForm((f) => ({ ...f, [field]: value }))

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

        <form onSubmit={submit} className="grid gap-4">
          <Field label="O quê" htmlFor="titulo">
            <Input id="titulo" required maxLength={120} autoFocus value={form.titulo}
              onChange={(e) => set('titulo')(e.target.value)} placeholder="Dentista, reunião com a padaria…" />
          </Field>

          <div className="grid grid-cols-2 gap-3">
            <Field label="Dia" htmlFor="data">
              <Input id="data" type="date" required value={form.data} onChange={(e) => set('data')(e.target.value)} />
            </Field>
            <div className="flex items-end gap-2 pb-2">
              <Checkbox id="dia-inteiro" checked={form.diaInteiro} onCheckedChange={(v) => set('diaInteiro')(v === true)} />
              <Label htmlFor="dia-inteiro" className="font-normal">Dia inteiro</Label>
            </div>
          </div>

          {!form.diaInteiro && (
            <div className="grid grid-cols-2 gap-3">
              <Field label="Começa" htmlFor="inicio">
                <Input id="inicio" type="time" required value={form.inicio} onChange={(e) => set('inicio')(e.target.value)} />
              </Field>
              <Field label="Termina (opcional)" htmlFor="fim">
                <Input id="fim" type="time" value={form.fim} onChange={(e) => set('fim')(e.target.value)} />
              </Field>
            </div>
          )}

          <Field label="Onde (opcional)" htmlFor="local">
            <Input id="local" maxLength={120} value={form.local} onChange={(e) => set('local')(e.target.value)} />
          </Field>
          <Field label="Com quem (separe por vírgula)" htmlFor="participantes">
            <Input id="participantes" value={form.participantes} onChange={(e) => set('participantes')(e.target.value)}
              placeholder="Ana, Bruno" />
          </Field>
          <Field label="Notas (opcional)" htmlFor="notas">
            <textarea id="notas" maxLength={1000} rows={3} value={form.notas} onChange={(e) => set('notas')(e.target.value)}
              placeholder="o que levar, o que combinar…"
              className="w-full resize-none rounded-md border border-input bg-transparent px-3 py-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50" />
          </Field>

          {error && <p role="alert" className="font-hand text-[1.25rem] leading-tight text-primary">{error}</p>}

          <div className="flex items-center justify-end gap-4">
            <button type="button" onClick={() => onOpenChange(false)} className="font-mono text-[11px] tracking-wide text-pencil uppercase">
              cancelar
            </button>
            <Button type="submit" variant="ghost" disabled={saving} className={cn(STAMP_BUTTON, '-rotate-1 px-4 py-2 text-[12px]')}>
              {saving ? 'Anotando…' : 'Anotar na agenda'}
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  )
}

function Field({ label, htmlFor, children }: { label: string; htmlFor: string; children: ReactNode }) {
  return (
    <div className="grid gap-1.5">
      <Label htmlFor={htmlFor} className="font-mono text-[11px] font-normal tracking-wide text-pencil uppercase">
        {label}
      </Label>
      {children}
    </div>
  )
}
