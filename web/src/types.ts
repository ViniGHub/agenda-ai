// The JSON contract with the Python API. Mirrors the Pydantic models in src/agenda_ia/api.py.

export interface AgendaEvent {
  id: number
  titulo: string
  inicio: string // ISO datetime with the São Paulo offset
  fim: string
  dia_inteiro: boolean
  quando: string // already formatted by the server, e.g. "terça-feira, 17/11/2026, 08:00–09:00"
  local: string
  participantes: string[]
  notas: string
}

export type QueryType = 'listar' | 'buscar' | 'contar'

export interface Details {
  tipo: QueryType
  de: string | null // YYYY-MM-DD
  ate: string | null // YYYY-MM-DD, inclusive
  busca: string | null
  reserva: string | null // why the question understanding fell back to a plain search
  estrategia: string
  recuperados: AgendaEvent[]
  guarda: string | null // which guardrail acted on the answer, if any
  tokens: number
  chamadas: number
  segundos: number
}

export interface Answer {
  resposta: string
  encontrou: boolean
  fontes: AgendaEvent[]
  detalhes: Details
}

export interface Status {
  origem: 'exemplo' | 'arquivo'
  eventos: number
  de: string
  ate: string
  llm: boolean
  modelo: string | null
  agora: string
}
