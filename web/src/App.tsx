import { useEffect, useRef, useState } from 'react'
import { askQuestion, getAgenda, getStatus } from './api'
import { AgendaPanel } from './components/AgendaPanel'
import { Chat, type ChatMessage } from './components/Chat'
import { DetailsPanel } from './components/DetailsPanel'
import type { AgendaEvent, Status } from './types'

type Tab = 'detalhes' | 'agenda'

export default function App() {
  const [status, setStatus] = useState<Status | null>(null)
  const [agenda, setAgenda] = useState<AgendaEvent[]>([])
  const [loadError, setLoadError] = useState<string | null>(null)
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [pending, setPending] = useState(false)
  const [selectedId, setSelectedId] = useState<number | null>(null) // answer shown in the side panel
  const [tab, setTab] = useState<Tab>('detalhes')
  const [focusedEventId, setFocusedEventId] = useState<number | null>(null)
  const nextId = useRef(1)

  function load() {
    setLoadError(null)
    Promise.all([getStatus(), getAgenda()])
      .then(([s, a]) => {
        setStatus(s)
        setAgenda(a)
      })
      .catch((error: Error) => setLoadError(error.message))
  }

  useEffect(load, [])

  async function send(question: string) {
    const text = question.trim()
    if (!text || pending) return
    setMessages((m) => [...m, { id: nextId.current++, role: 'user', text }])
    setPending(true)
    try {
      const answer = await askQuestion(text)
      const id = nextId.current++
      setMessages((m) => [...m, { id, role: 'assistant', answer }])
      setSelectedId(id)
      setTab('detalhes')
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      setMessages((m) => [...m, { id: nextId.current++, role: 'error', text: message }])
    } finally {
      setPending(false)
    }
  }

  function showEvent(eventId: number) {
    setFocusedEventId(eventId)
    setTab('agenda')
  }

  const selected = messages.find((m) => m.id === selectedId)
  const selectedAnswer = selected?.role === 'assistant' ? selected.answer : null
  const citedIds = new Set(selectedAnswer?.fontes.map((f) => f.id) ?? [])

  return (
    <div className="page">
      <header className="header">
        <div>
          <h1>📅 Agenda IA</h1>
          <p className="subtitle">
            Pergunte em português sobre a agenda e receba a resposta citando os compromissos de onde ela saiu.
          </p>
        </div>
        <StatusBadge status={status} />
      </header>

      {loadError && (
        <div className="banner banner-error" role="alert">
          {loadError} <button onClick={load}>Tentar de novo</button>
        </div>
      )}
      {status && status.origem === 'exemplo' && (
        <div className="banner">
          A agenda é <strong>fictícia</strong> e é gerada em volta da semana atual, para que perguntas como “o que tenho
          amanhã?” sempre tenham resposta. Não escreva dados pessoais: a pergunta é enviada a um modelo de linguagem.
        </div>
      )}
      {status && !status.llm && (
        <div className="banner banner-warning">
          O modelo de linguagem não está configurado: as respostas mostram só os compromissos mais próximos da pergunta.
        </div>
      )}

      <main className="layout">
        <Chat
          messages={messages}
          pending={pending}
          disabled={!status}
          selectedId={selectedId}
          onSend={send}
          onSelect={(id) => {
            setSelectedId(id)
            setTab('detalhes')
          }}
          onShowEvent={showEvent}
        />

        <aside className="side">
          <div className="tabs" role="tablist">
            <button role="tab" aria-selected={tab === 'detalhes'} onClick={() => setTab('detalhes')}>
              Como foi respondido
            </button>
            <button role="tab" aria-selected={tab === 'agenda'} onClick={() => setTab('agenda')}>
              Agenda completa{agenda.length > 0 && ` (${agenda.length})`}
            </button>
          </div>
          {tab === 'detalhes' ? (
            <DetailsPanel answer={selectedAnswer} onShowEvent={showEvent} />
          ) : (
            <AgendaPanel events={agenda} citedIds={citedIds} focusedEventId={focusedEventId} />
          )}
        </aside>
      </main>

      <footer className="footer">
        Busca híbrida (embeddings locais + BM25) com filtro de datas, resposta do <code>gpt-oss-120b</code> no Groq e
        citações conferidas em código.
      </footer>
    </div>
  )
}

function StatusBadge({ status }: { status: Status | null }) {
  if (!status) return <span className="badge">Conectando…</span>
  const source = status.origem === 'exemplo' ? 'Agenda fictícia' : 'Agenda do arquivo'
  return (
    <span className="badge" title={status.modelo ?? 'sem modelo de linguagem'}>
      {source} · {status.eventos} compromissos · {status.llm ? 'LLM ativo' : 'só busca'}
    </span>
  )
}
