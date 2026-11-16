import type { AgendaEvent, Details } from './types'

const TIME_ZONE = 'America/Sao_Paulo'

const isoDay = new Intl.DateTimeFormat('en-CA', { timeZone: TIME_ZONE, year: 'numeric', month: '2-digit', day: '2-digit' })
const dayTitle = new Intl.DateTimeFormat('pt-BR', { timeZone: TIME_ZONE, weekday: 'long', day: '2-digit', month: '2-digit' })
const clock = new Intl.DateTimeFormat('pt-BR', { timeZone: TIME_ZONE, hour: '2-digit', minute: '2-digit' })

/** "2026-11-17", in São Paulo time: used to group events by day. */
export const dayKey = (iso: string) => isoDay.format(new Date(iso))

/** "Terça-feira, 17/11" */
export const dayLabel = (iso: string) => {
  const label = dayTitle.format(new Date(iso))
  return label.charAt(0).toUpperCase() + label.slice(1)
}

/** "08:00–09:00" or "Dia inteiro" */
export const timeLabel = (event: AgendaEvent) =>
  event.dia_inteiro ? 'Dia inteiro' : `${clock.format(new Date(event.inicio))}–${clock.format(new Date(event.fim))}`

/** "2026-11-17" -> "17/11/2026" */
export const shortDate = (isoDate: string) => isoDate.split('-').reverse().join('/')

export function periodLabel(details: Pick<Details, 'de' | 'ate'>): string {
  const { de, ate } = details
  if (de && ate) return de === ate ? shortDate(de) : `${shortDate(de)} a ${shortDate(ate)}`
  if (de) return `a partir de ${shortDate(de)}`
  if (ate) return `até ${shortDate(ate)}`
  return 'toda a agenda'
}

export const QUERY_TYPE_LABEL: Record<Details['tipo'], string> = {
  listar: 'Listar um período',
  buscar: 'Buscar um compromisso',
  contar: 'Contar compromissos',
}
