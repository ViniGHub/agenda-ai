// The JSON contract with the Python API. Mirrors the Pydantic models in src/agenda_ia/api.py.

/** Which agenda a request talks to: the open demo or the signed-in person's own. */
export type Space = 'demo' | 'minha'

/** How a chat message is taken: the model decides, or the person said "that was a question" / "write it down". */
export type AskMode = 'auto' | 'pergunta' | 'anotar'

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

export type QueryType = 'listar' | 'buscar' | 'contar' | 'anotar' // 'anotar': a new appointment, not a question

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
  acao: NoteAction | null // set when the message asked to write down a new appointment
}

/**
 * What the chat did with "marca dentista quinta às 15h":
 * - anotado: an explicit order with day and time, already saved (the reply offers an undo);
 * - confirmar: the message only described an appointment, so it asks before saving;
 * - completar: the day or the time is missing, so the form has to be finished.
 */
export interface NoteAction {
  tipo: 'anotado' | 'confirmar' | 'completar'
  rascunho: Draft
  evento: AgendaEvent | null // the saved event, when "anotado"
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
  audio: boolean // voice messages can be transcribed
}

/** A voice message, transcribed. The page sends the text to the chat like a typed message. */
export interface Transcript {
  texto: string
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

/** The event form filled in from a message ("dentista quinta às 15h"), gaps allowed. */
export interface Draft {
  titulo: string
  data: string | null // YYYY-MM-DD
  dia_inteiro: boolean
  inicio: string | null // HH:MM
  fim: string | null
  local: string
  participantes: string[]
  notas: string
  faltando: Array<'data' | 'inicio'> // required fields the message did not give
}

/** What the form's sentence box gets back. Nothing is saved until the form is sent. */
export interface Suggestion extends Draft {
  tokens: number
}
