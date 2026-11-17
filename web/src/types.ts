// The JSON contract with the Python API. Mirrors the Pydantic models in src/agenda_ia/api.py.

/** Which agenda a request talks to: the open demo or the signed-in person's own. */
export type Space = 'demo' | 'minha'

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
  origem: 'base' | 'cadastro' | 'importado' // only "cadastro" and "importado" can be deleted
}

/** What the form sends; the server builds the São Paulo date and times. */
export interface EventDraft {
  titulo: string
  data: string // YYYY-MM-DD
  dia_inteiro: boolean
  inicio: string | null // HH:MM
  fim: string | null
  local: string
  participantes: string[]
  notas: string
}

export interface ImportResult {
  encontrados: number
  importados: number
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
  origem: 'exemplo' | 'arquivo' | 'privada'
  eventos: number
  de: string
  ate: string
  llm: boolean
  modelo: string | null
  agora: string
  persistente: boolean // false: additions disappear on restart (private) or after 2 hours (demo)
}

export interface Config {
  login: boolean
  google_client_id: string | null
}

export interface Me {
  logado: boolean
  nome: string | null
  email: string | null
}
